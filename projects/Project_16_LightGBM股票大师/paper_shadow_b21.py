# -*- coding: utf-8 -*-
"""21f 纸面影子臂（B21 k1500）——零下单、零账本写入，只记录"会买什么"与前向收益。

背景（2026-10-27 立）：
  生产 G2 用外部 43 特征模型 + F1~F6 生产评分卡；内置沙箱引擎是 21 特征 B21 模型
  （vendor/g2_features_qmt.py + vendor/g2_tree_model_B21_k1500.py），当前 SIGNAL_ENABLED=False。
  两条信号线的策略级对比只存在于回测（且结论随 tier 口径 t0_close/t1_open 反转），
  缺少前向证据。本脚本用与内置引擎**同一份特征代码 + 同一个模型**，在外部逐日记录
  21f 臂的候选，回填 T+1 开盘起 5/10/15 日 open→open 收益，作为前向影子样本。

口径（与 backtest_b21_strategy.py 同源，差异处标注）：
  - 池：universe_all_a(enabled) ∩ is_st==0 ∩ 剔 688/4/8（filter_universe 默认链）
  - 特征：vendor/g2_features_qmt.assemble()，21 列 FEATURE_ORDER，训练口径（close*adj_factor）
  - 信号：prob >= REDLINE(0.60) 取前 SELECT_TOP(10)，再取前 TOP_N(3)（等比 live 内置参数）
  - 买入：T+1 开盘价；一字涨停跳过；低开守卫 gap<=-5% 跳过、(-5%,-3%] 需 volume_ratio>=3.3
  - 收益：T+1 开盘 -> T+1+N 开盘（N=5/10/15），毛收益（不含费税），另记 15 日 MFE/MAE
  - tier：双口径并列记录（t0=T 日自身涨跌，审计 B3 默认；open=T+1 开盘跳空，live 09:50 代理），
          本脚本**不按 tier 删候选**，只记 deploy 比例，避免在口径未拍板前改信号
  - 与内置实盘的已知差异（报告须注明）：①is_st 用逐行真值（内置用最近交易日静态 ST 名单）；
    ②pe/pb/dv 用逐日真值（内置用构建期快照）；③float_share 用逐日真值（内置用
    get_instrument_detail 静态股本，v4.7 实测 18/20 偏差 >1%）；④无 tick 级风控。

只读保证：仅读 merged_daily_full / incremental_daily / universe_all_a / stock_basic /
          index_daily / paper_forward_live.csv；不碰任何交易、账本、plan、state 文件。
产物：data/real/paper_shadow_b21_picks.csv   逐日候选 + 前向收益（全量重算，幂等）
      data/real/paper_shadow_b21_days.csv    逐日统计（过线数 / tier / 最高 prob）
      data/real/paper_shadow_b21_<date>.md   最新报告
      data/real/paper_shadow_b21.log

用法：
  python paper_shadow_b21.py --daily                 # 增量：只补最新交易日（定时任务用）
  python paper_shadow_b21.py --backfill [YYYY-MM-DD] # 回填历史（缺省取 live 纸面最早日）
  python paper_shadow_b21.py --asof 20261023 --daily # 指定数据截止日（重放/核对）
  python paper_shadow_b21.py --daily --no-report
"""
import argparse
import io
import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "vendor"))
import g2_features_qmt as GQ          # noqa: E402
import g2_tree_model_B21_k1500 as BM  # noqa: E402

MERGED = os.path.join(HERE, "data_live", "merged_daily_full.parquet")
INCR = os.path.join(HERE, "data_live", "incremental_daily.parquet")
UNIVERSE = r"D:\QuantLab\data\universe_all_a.csv"
BASIC = r"D:\astock\basic\stock_basic.parquet"
HS300 = r"D:\astock\index\index_daily\000300.SH.parquet"
LIVE_CSV = os.path.join(HERE, "data", "real", "paper_forward_live.csv")
REAL = os.path.join(HERE, "data", "real")
OUT_CSV = os.path.join(REAL, "paper_shadow_b21_picks.csv")
DAYS_CSV = os.path.join(REAL, "paper_shadow_b21_days.csv")
OUT_MD = os.path.join(REAL, "paper_shadow_b21_%s.md")
LOG = os.path.join(REAL, "paper_shadow_b21.log")

RAW_COLS = ["open", "high", "low", "close", "pre_close", "vol", "amount",
            "turnover_rate", "volume_ratio", "pe_ttm", "pb", "dv_ttm",
            "circ_mv", "is_st", "adj_factor", "float_share", "up_limit"]

REDLINE = 0.60
SELECT_TOP = 10
TOP_N = 3
HOLD = 15
TIER_STOP = -1.5
TIER_HALF = -1.0
DEPLOY = {0: 0.0, 1: 0.5, 2: 0.98}
LOOKBACK_TD = 420       # 特征窗口（交易日）：250 日滚动 + 20 日横截面 + 回填余量
LOOKBACK_CAL_DAYS = 760  # 自然日粗筛（≈520 交易日，含节假日余量）
GAP_HARD = -0.05
GAP_SOFT = -0.03
GAP_VOL_RATIO = 3.3

PICK_COLS = ["date", "source", "rank", "code", "prob", "tier_t0", "tier_open",
             "deploy_t0", "deploy_open", "entered", "skip_reason",
             "entry_date", "entry_open", "ret5", "ret10", "ret15",
             "mfe15", "mae15"]
DAY_COLS = ["date", "n_universe", "n_qualified", "top_prob", "tier_t0", "tier_open"]


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _load_merged(start_ts, end_ts):
    mg = pd.read_parquet(MERGED, columns=["ts_code"] + RAW_COLS,
                         filters=[("trade_date", ">=", start_ts),
                                  ("trade_date", "<=", end_ts)])
    mg = mg.reset_index()
    mg["ts_code"] = mg["ts_code"].astype(str)
    mg["trade_date"] = pd.to_datetime(mg["trade_date"]).dt.strftime("%Y%m%d")
    return mg


def _incr_min():
    if not os.path.exists(INCR):
        return "99999999"
    d = pd.read_parquet(INCR, columns=["trade_date"])
    if "trade_date" not in d.columns:
        d = d.reset_index()
    return pd.to_datetime(d["trade_date"]).min().strftime("%Y%m%d")


def _load_universe():
    u = pd.read_csv(UNIVERSE)
    return set(u[u["enabled"] == True]["code"].astype(str).tolist())  # noqa: E712


def _load_industry():
    sb = pd.read_parquet(BASIC, columns=["ts_code", "industry"])
    sb["ts_code"] = sb["ts_code"].astype(str)
    sb = sb.dropna(subset=["industry"])
    return dict(zip(sb["ts_code"], sb["industry"].astype(str)))


def _load_hs300():
    h = pd.read_parquet(HS300, columns=["close", "open", "pct_chg", "pre_close"])
    h = h.reset_index()
    h["trade_date"] = pd.to_datetime(h["trade_date"]).dt.strftime("%Y%m%d")
    h = h.sort_values("trade_date").reset_index(drop=True)
    # 2026-10-27 实测：index_daily 文件只到 20260930。pct_chg 缺失时用 close/pre_close 兜底
    bad = h["pct_chg"].isna() | (h["pct_chg"] == 0)
    if bad.any():
        calc = (h["close"] / h["pre_close"] - 1.0) * 100.0
        h.loc[bad, "pct_chg"] = calc[bad]
    return h


def _tier_t0(pct):
    if pct is None or pct != pct:
        return -1
    if pct <= TIER_STOP:
        return 0
    if pct < TIER_HALF:
        return 1
    return 2


def _tier_open(open_px, prev_close):
    if not open_px or not prev_close or prev_close <= 0:
        return -1
    pct = (float(open_px) / float(prev_close) - 1.0) * 100.0
    if pct <= TIER_STOP:
        return 0
    if pct < TIER_HALF:
        return 1
    return 2


def _nth_after(cal, d, n):
    try:
        i = cal.index(d)
    except ValueError:
        return None
    j = i + n
    return cal[j] if 0 <= j < len(cal) else None


def _build_features(raw, enabled, industry_map, st_ffill=False):
    """raw -> 21 特征长表（与内置引擎同一份 assemble()）。

    is_st 缺值口径：默认**照训练侧丢弃**（build_features_v2.py L101 `df[df["is_st"]==0]`，
    NaN 比较为 False 即被剔）。2026-09-14/18/24 全表缺、09-23 缺 1290 行，默认模式下
    这 3 天会没有候选（诚实留空）。--st-ffill 改为按个股前值前填（更接近实盘"未知即非 ST"），
    但偏离训练口径，报告须标注。
    """
    work = raw.copy().sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    incr_min = _incr_min()
    fs = work["float_share"].astype(float).replace(0.0, np.nan)
    inferred = work["vol"].astype(float) / fs
    is_incr = work["trade_date"] >= incr_min
    work["true_turnover"] = np.where(is_incr, inferred, work["turnover_rate"].astype(float))
    # compute_price_features 的 turn_ma5 取 df["turnover_rate"]；内置 build_raw_columns
    # 同样把它赋值成 true_turnover，这里保持一致
    work["turnover_rate"] = work["true_turnover"]
    if st_ffill:
        st = pd.to_numeric(work["is_st"], errors="coerce")
        n_missing = int(st.isna().sum())
        st = st.groupby(work["ts_code"]).apply(
            lambda s: s.ffill(limit=5)).reset_index(level=0, drop=True)
        work["is_st"] = st.reindex(work.index).fillna(0.0)
        log("--st-ffill：is_st 缺值 %d 行已前填/置 0（偏离训练口径）" % n_missing)
    work = GQ.filter_universe(work, enabled_codes=enabled)
    feat = GQ.assemble(work, industry_map)
    return feat, work


def _score(feat_day):
    if len(feat_day) == 0:
        return feat_day
    out = feat_day.copy()
    X = out[list(GQ.FEATURE_ORDER)].astype("float32").values
    out["prob"] = BM.predict_proba(X)
    return out


def _forward(px, cal, code, t_date, up_limit):
    """返回 T+1 开盘入场与 5/10/15 日 open→open 前向结果。"""
    res = {"entered": 0, "skip_reason": "", "entry_date": "", "entry_open": np.nan,
           "ret5": np.nan, "ret10": np.nan, "ret15": np.nan,
           "mfe15": np.nan, "mae15": np.nan}
    d1 = _nth_after(cal, t_date, 1)
    if d1 is None or (code, d1) not in px.index:
        res["skip_reason"] = "no_next_bar"
        return res
    r1 = px.loc[(code, d1)]
    o1 = float(r1["open"])
    pc = float(r1["pre_close"]) if r1["pre_close"] == r1["pre_close"] else np.nan
    res["entry_date"] = d1
    res["entry_open"] = o1
    if not (o1 > 0):
        res["skip_reason"] = "bad_open"
        return res
    ul = float(up_limit) if up_limit == up_limit else np.nan
    if ul == ul and ul > 0 and o1 >= ul * 0.999:
        res["skip_reason"] = "limit_up_open"
        return res
    if pc == pc and pc > 0:
        gap = o1 / pc - 1.0
        if gap <= GAP_HARD:
            res["skip_reason"] = "gap_le_-5pct"
        elif gap <= GAP_SOFT:
            vr = float(r1["volume_ratio"]) if r1["volume_ratio"] == r1["volume_ratio"] else np.nan
            if not (vr == vr) or vr < GAP_VOL_RATIO:
                res["skip_reason"] = "gap_-5_-3_volratio_lt_3.3"
    if res["skip_reason"]:
        return res
    res["entered"] = 1
    for n in (5, 10, 15):
        dn = _nth_after(cal, d1, n)
        if dn is not None and (code, dn) in px.index:
            on = float(px.loc[(code, dn)]["open"])
            if on > 0:
                res["ret%d" % n] = on / o1 - 1.0
    d15 = _nth_after(cal, d1, 15) or cal[-1]
    try:
        seg = px.loc[code].loc[d1:d15]
    except KeyError:
        seg = None
    if seg is not None and len(seg) > 0:
        hi = float(seg["high"].max())
        lo = float(seg["low"].min())
        if hi == hi and lo == lo:
            res["mfe15"] = hi / o1 - 1.0
            res["mae15"] = lo / o1 - 1.0
    return res


def _pick_date(feat, px, cal, hs, t_date, source):
    hs_row = hs[hs["trade_date"] == t_date]
    if len(hs_row):
        tier_t0 = _tier_t0(float(hs_row.iloc[0]["pct_chg"]))
    else:
        tier_t0 = -1          # -1 = 指数数据缺失（不静默当 T1）
    d1 = _nth_after(cal, t_date, 1)
    if d1 is not None and len(hs_row):
        prev_close = float(hs_row.iloc[0]["close"])
        r1 = hs[hs["trade_date"] == d1]
        tier_open = _tier_open(float(r1.iloc[0]["open"]), prev_close) if len(r1) else -1
    else:
        tier_open = -1
    def _meta(n_uni, n_q, top):
        return {"date": t_date, "n_universe": int(n_uni), "n_qualified": int(n_q),
                "top_prob": top, "tier_t0": tier_t0, "tier_open": tier_open}
    day = feat[feat["trade_date"] == t_date]
    if len(day) == 0:
        return [], _meta(0, 0, np.nan)
    day = _score(day)
    day = day[day["prob"] == day["prob"]]
    day = day.sort_values("prob", ascending=False)
    qualified = day[day["prob"] >= REDLINE]
    meta = _meta(len(day), len(qualified),
                 round(float(day.iloc[0]["prob"]), 6) if len(day) else np.nan)
    picks = []
    pool = qualified.head(SELECT_TOP)
    for i, (_, r) in enumerate(pool.head(TOP_N).iterrows()):
        fw = _forward(px, cal, r["ts_code"], t_date, r["up_limit"] if "up_limit" in r else np.nan)
        row = {"date": t_date, "source": source, "rank": i + 1,
               "code": r["ts_code"], "prob": round(float(r["prob"]), 6),
               "tier_t0": tier_t0, "tier_open": tier_open,
               "deploy_t0": DEPLOY.get(tier_t0, np.nan),
               "deploy_open": DEPLOY.get(tier_open, np.nan)}
        row.update(fw)
        picks.append(row)
    return picks, meta


def _read_csv(path, cols):
    if not os.path.exists(path):
        return pd.DataFrame(columns=cols)
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return pd.DataFrame(columns=cols)


def _fmt_code_date(s):
    out = []
    for v in s:
        if v is None or (isinstance(v, float) and v != v) or v == "":
            out.append("")
            continue
        sv = str(v)
        if sv.endswith(".0"):
            sv = sv[:-2]
        out.append(sv)
    return out


def _write_csv(df, path, cols):
    out = df.reindex(columns=cols).copy()
    for c in ("date", "entry_date"):
        if c in out.columns:
            out[c] = _fmt_code_date(out[c])
    if "skip_reason" in out.columns:
        out["skip_reason"] = out["skip_reason"].fillna("").astype(str).replace({"nan": ""})
    tmp = path + ".tmp"
    out.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, path)


def _recompute_returns(picks, px, cal):
    if len(picks) == 0:
        return picks
    out = []
    for _, r in picks.iterrows():
        fw = _forward(px, cal, r["code"], r["date"], px.loc[(r["code"], r["date"]), "up_limit"]
                      if (r["code"], r["date"]) in px.index else np.nan)
        row = r.to_dict()
        row.update(fw)
        out.append(row)
    return pd.DataFrame(out)


def _stats(df):
    if len(df) == 0:
        return "样本 0 笔"
    n = len(df)
    parts = ["n=%d" % n]
    for c in ("ret5", "ret10", "ret15"):
        s = pd.to_numeric(df[c], errors="coerce").dropna()
        if len(s):
            hit = float((s > 0).mean()) * 100.0
            parts.append("%s 均值 %+.2f%% / 中位 %+.2f%% / 胜率 %.0f%% (n=%d)"
                         % (c, s.mean() * 100, s.median() * 100, hit, len(s)))
        else:
            parts.append("%s 暂无到期样本" % c)
    return " | ".join(parts)


def _live_compare(picks, px, cal):
    """用同一套收益引擎重算 43f live 纸面臂，做同口径对照。"""
    live = _read_csv(LIVE_CSV, ["date", "code", "rank"])
    if len(live) == 0:
        return ["- live 纸面文件缺失，暂无对照"]
    live = live[pd.to_numeric(live["rank"], errors="coerce") <= TOP_N].copy()
    live["date"] = live["date"].astype(str).str.replace("-", "")
    live = live.drop_duplicates(subset=["date", "code"], keep="last")  # live CSV 有重复行
    rows = []
    for _, r in live.iterrows():
        if (r["code"], r["date"]) not in px.index:
            continue
        fw = _forward(px, cal, r["code"], r["date"],
                      px.loc[(r["code"], r["date"]), "up_limit"])
        fw["date"] = r["date"]
        fw["code"] = r["code"]
        rows.append(fw)
    if not rows:
        return ["- live 臂无可回填样本"]
    lv = pd.DataFrame(rows)
    lines = []
    both_dates = sorted(set(picks["date"]) & set(lv["date"]))
    if both_dates:
        sub_s = picks[picks["date"].isin(both_dates)]
        sub_l = lv[lv["date"].isin(both_dates)]
        lines.append("- 重叠信号日 %d 个（%s ~ %s）" % (
            len(both_dates), both_dates[0], both_dates[-1]))
        ov = 0
        for d in both_dates:
            s = set(sub_s[sub_s["date"] == d]["code"])
            l = set(sub_l[sub_l["date"] == d]["code"])
            ov += len(s & l)
        tot = max(len(sub_s), 1)
        if ov == 0:
            lines.append("- 名字重合 **0 / %d**（0%%）→ 两臂买的是**完全不同的票**，"
                         "差异即全部样本，不是少数分歧票" % tot)
        else:
            lines.append("- 名字重合 %d / %d（%.0f%%）→ 重合部分两臂同涨同跌，"
                         "有效差异只在分歧票" % (ov, tot, 100.0 * ov / tot))
        lines.append("")
        lines.append("| 臂 | 到期样本 | ret10 均值 | ret15 均值 | 胜率(ret15) |")
        lines.append("|---|---|---|---|---|")
        for name, df in (("21f 影子（本脚本）", sub_s), ("43f live 纸面（同引擎重算）", sub_l)):
            s10 = pd.to_numeric(df["ret10"], errors="coerce").dropna()
            s15 = pd.to_numeric(df["ret15"], errors="coerce").dropna()
            lines.append("| %s | %d | %s | %s | %s |" % (
                name, len(s15),
                "%+.2f%%" % (s10.mean() * 100) if len(s10) else "-",
                "%+.2f%%" % (s15.mean() * 100) if len(s15) else "-",
                "%.0f%%" % ((s15 > 0).mean() * 100) if len(s15) else "-"))
        lines.append("")
        lines.append("> 样本 < 100 笔、单一时间窗，且两臂模型与信号层都不同 → **仅作观察，不作裁决依据**。"
                     "累计到 60 个独立信号日再谈显著性。")
        # 分歧票子集（21f 有、43f 无）
        only_s = sub_s[~sub_s.set_index(["date", "code"]).index.isin(
            sub_l.set_index(["date", "code"]).index)]
        s15 = pd.to_numeric(only_s["ret15"], errors="coerce").dropna()
        if len(s15) and ov > 0:
            lines.append("")
            lines.append("- **分歧票（21f 独有）ret15 均值 %+.2f%%（n=%d，胜率 %.0f%%）**"
                         % (s15.mean() * 100, len(s15), (s15 > 0).mean() * 100))
    else:
        lines.append("- 暂无重叠信号日")
    return lines


def build_report(picks, days, data_last, asof, cmp_lines):
    lines = []
    A = lines.append
    A("# 21f 纸面影子臂（B21 k1500）· %s" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    A("")
    A("- 数据截止：`%s`（merged_daily_full 真实最后交易日；脚本不伪造成交日）" % data_last)
    if asof and asof != data_last:
        A("- 指定 asof：`%s`（实际取到 %s，数据源未更新）" % (asof, data_last))
    A("- 模型：`vendor/g2_tree_model_B21_k1500.py`（%d 树 / %d 特征）" % (
        BM.n_trees(), BM.feature_count()))
    A("- 特征：`vendor/g2_features_qmt.py` 的 `assemble()`（与 QMT 内置引擎同一份代码）")
    A("- 参数：红线 prob>=%.2f → 前 %d → 取 %d；持有 %d 日；tier 不删候选只记 deploy"
      % (REDLINE, SELECT_TOP, TOP_N, HOLD))
    A("- 成本：报告为**毛收益**（open→open，不含佣金/印花税/滑点），与 43f live 臂同引擎重算")
    A("")
    A("> **前向 vs 回填**：`source=forward` 是当日盘后真实入表（前向证据）；`source=backfill` "
      "是事后用同一代码补算（等同回测，**不得当 OOS 证据**）。")
    A("")
    n_fwd = int((picks["source"] == "forward").sum()) if len(picks) else 0
    n_bk = int((picks["source"] == "backfill").sum()) if len(picks) else 0
    A("**样本**：forward %d 笔 / backfill %d 笔" % (n_fwd, n_bk))
    if len(days):
        ok = int((pd.to_numeric(days["tier_t0"], errors="coerce") >= 0).sum())
        A("")
        A("**tier 可用性**：%d / %d 天（HS300 `index_daily/000300.SH` 止于 20260930；"
          "缺失日记 -1，**不静默当 T1**）。tier 只记录不删候选。" % (ok, len(days)))
        empty = days[pd.to_numeric(days["n_universe"], errors="coerce") == 0]
        if len(empty):
            A("")
            A("**空日**：%s —— 当日过滤帧为空（`is_st` 缺值按训练口径剔除或行情缺行），"
              "不是「无信号」而是「无数据」。" % ", ".join(empty["date"].astype(str)))
    A("")
    A("## 一、累计统计")
    A("")
    A("| 子集 | 统计 |")
    A("|---|---|")
    A("| 全部候选 | %s |" % _stats(picks))
    A("| 可执行（entered=1） | %s |" % _stats(picks[picks["entered"] == 1]))
    A("| 仅前向（forward） | %s |" % _stats(picks[picks["source"] == "forward"]))
    if len(picks):
        skip = picks[picks["entered"] == 0]["skip_reason"].value_counts()
        if len(skip):
            A("")
            A("**跳过原因**：" + "；".join("%s %d" % (k, v) for k, v in skip.items() if k))
    A("")
    A("## 二、最新一日候选")
    A("")
    if len(days):
        last = days.sort_values("date").iloc[-1]
        A("- 日期 `%s`：池内 %d 只，过红线 %d 只，最高 prob %.4f，tier_t0=%d / tier_open=%d"
          % (last["date"], last["n_universe"], last["n_qualified"],
             last["top_prob"] if last["top_prob"] == last["top_prob"] else -1,
             last["tier_t0"], last["tier_open"]))
        A("")
        lp = picks[picks["date"] == last["date"]]
        if len(lp):
            A("| rank | code | prob | tier_t0 | tier_open | entered | skip_reason |")
            A("|---|---|---|---|---|---|---|")
            for _, r in lp.iterrows():
                A("| %d | %s | %.4f | %d | %d | %d | %s |" % (
                    r["rank"], r["code"], r["prob"], r["tier_t0"], r["tier_open"],
                    r["entered"], r["skip_reason"] or "-"))
        else:
            A("当日无过线候选（空仓）。")
    else:
        A("暂无数据。")
    A("")
    A("## 三、与 43f live 纸面臂对照（同收益引擎）")
    A("")
    lines.extend(cmp_lines)
    A("")
    A("## 四、口径差异与限制")
    A("")
    A("1. 本影子是 **21f(B21) + prob 红线**；live 生产臂是 **43f + F1~F6 评分卡 SC60**。"
      "两者同时改变模型与信号层，**不能把差异全归因于特征数**。")
    A("2. 内置实盘与本地影子的已知差异：①is_st 逐行真值 vs 最近交易日静态 ST 名单；"
      "②pe/pb/dv 逐日真值 vs 构建期快照；③float_share 逐日真值 vs "
      "`get_instrument_detail` 静态股本（v4.7 实测 20 只中 18 只偏差 1.6%~100%）。"
      "影子更接近训练口径，内置更接近实盘可得口径。")
    A("3. 复权用当前 `adj_factor` 全序列（与项目既有面板同口径），存在常规的复权因子修订口径差。")
    A("4. tier 双口径并列但不删候选：口径未拍板前不改信号。")
    A("5. 本脚本零下单、零账本写入；不构成任何交易建议。")
    A("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true", help="只补最新交易日（定时任务）")
    ap.add_argument("--backfill", nargs="?", const="", default=None,
                    help="回填起始日 YYYY-MM-DD（缺省=live 纸面最早日）")
    ap.add_argument("--asof", default="", help="数据截止日 YYYYMMDD（默认取数据真实最新日）")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--st-ffill", action="store_true",
                    help="is_st 缺值按个股前值前填（偏离训练口径，仅研究对照用）")
    args = ap.parse_args()

    t0 = time.time()
    os.makedirs(REAL, exist_ok=True)
    log("=== paper_shadow_b21 start (daily=%s backfill=%s asof=%s) ==="
        % (args.daily, args.backfill, args.asof))

    # ---- 加载窗口（按自然日粗筛，避免全量读 1.1GB 索引）----
    end_ts = pd.Timestamp(args.asof) if args.asof else pd.Timestamp(datetime.now().date())
    start_ts = end_ts - pd.Timedelta(days=LOOKBACK_CAL_DAYS)
    raw = _load_merged(start_ts, end_ts)
    if len(raw) == 0:
        log("!! 窗口内无数据（%s ~ %s）" % (start_ts.date(), end_ts.date()))
        return 2
    cal = sorted(set(raw["trade_date"]))
    if len(cal) < 260:
        log("!! 交易日不足 260 天（实际 %d），无法计算 250 日特征" % len(cal))
        return 2
    data_last = cal[-1]
    keep = set(cal[-LOOKBACK_TD:])
    raw = raw[raw["trade_date"].isin(keep)]
    cal = cal[-LOOKBACK_TD:]
    log("window %s ~ %s (%d 交易日)" % (cal[0], cal[-1], len(cal)))

    # ---- 目标日 ----
    old = _read_csv(OUT_CSV, PICK_COLS)
    old_days = _read_csv(DAYS_CSV, DAY_COLS)
    targets = []
    forward_dates = set()
    if args.backfill is not None:
        if args.backfill:
            b0 = args.backfill.replace("-", "")
        elif len(old):
            b0 = min(old["date"].astype(str))
        elif os.path.exists(LIVE_CSV):
            lv = _read_csv(LIVE_CSV, ["date"])
            b0 = str(lv["date"].astype(str).min()).replace("-", "") if len(lv) else cal[-120]
        else:
            b0 = cal[-120]
        b0 = b0.replace("-", "")
        targets = [d for d in cal if d >= b0]
        log("backfill 目标 %d 个交易日（%s ~ %s）" % (len(targets), targets[0], targets[-1]))
    if args.daily or args.backfill is None:
        if data_last not in targets:
            targets.append(data_last)
        # 只有"信号日 = 运行日"才算 forward；数据陈旧补跑仍记 backfill，避免把补算冒充前向
        if data_last == datetime.now().strftime("%Y%m%d"):
            forward_dates.add(data_last)
        else:
            log("数据最后交易日 %s != 运行日 %s，本次记 backfill（不冒充前向）"
                % (data_last, datetime.now().strftime("%Y%m%d")))
    if not targets:
        log("无目标日，退出")
        return 0

    # ---- 加载 ----
    log("loaded merged rows=%d (%s ~ %s)" % (len(raw), raw["trade_date"].min(), raw["trade_date"].max()))
    enabled = _load_universe()
    industry_map = _load_industry()
    hs = _load_hs300()
    feat, work = _build_features(raw, enabled, industry_map, st_ffill=args.st_ffill)
    log("features rows=%d (%.1fs)" % (len(feat), time.time() - t0))

    px = work.set_index(["ts_code", "trade_date"]).sort_index()
    px = px[["open", "high", "low", "pre_close", "volume_ratio", "up_limit"]]

    # ---- 逐日打分 ----
    new_picks, new_days = [], []
    for d in sorted(set(targets)):
        picks, meta = _pick_date(feat, px, cal, hs, d,
                                 "forward" if d in forward_dates else "backfill")
        if meta is None:
            continue
        new_days.append(meta)
        new_picks.extend(picks)
        log("%s pool=%d qualified=%d top=%.4f tier=%d/%d picks=%d"
            % (d, meta["n_universe"], meta["n_qualified"],
               meta["top_prob"] if meta["top_prob"] == meta["top_prob"] else -1,
               meta["tier_t0"], meta["tier_open"], len(picks)))

    # ---- 合并（不重算历史信号，只重算前向收益）----
    picks_all = pd.concat([old, pd.DataFrame(new_picks)], ignore_index=True) if len(old) else pd.DataFrame(new_picks)
    if len(picks_all):
        picks_all["date"] = picks_all["date"].astype(str)
        picks_all = picks_all.drop_duplicates(subset=["date", "code"], keep="first")
        # forward 是"当日盘后真实入表"的不可追溯属性：一旦为 forward，永不降级为 backfill
        fwd_dates = set(picks_all.loc[picks_all["source"] == "forward", "date"]) | set(
            str(d) for d in forward_dates)
        picks_all.loc[picks_all["date"].isin(fwd_dates), "source"] = "forward"
        picks_all = _recompute_returns(picks_all, px, cal)
        picks_all = picks_all.sort_values(["date", "rank"])
    days_all = pd.concat([old_days, pd.DataFrame(new_days)], ignore_index=True) if len(old_days) else pd.DataFrame(new_days)
    if len(days_all):
        days_all["date"] = days_all["date"].astype(str)
        days_all = days_all.drop_duplicates(subset=["date"], keep="last").sort_values("date")

    _write_csv(picks_all, OUT_CSV, PICK_COLS)
    _write_csv(days_all, DAYS_CSV, DAY_COLS)
    log("written %s rows=%d | %s rows=%d" % (OUT_CSV, len(picks_all), DAYS_CSV, len(days_all)))

    # ---- 报告 ----
    if not args.no_report:
        cmp_lines = _live_compare(picks_all, px, cal) if len(picks_all) else ["- 暂无候选"]
        body = build_report(picks_all, days_all, data_last, args.asof, cmp_lines)
        out_md = OUT_MD % datetime.now().strftime("%Y%m%d")
        with io.open(out_md, "w", encoding="utf-8") as f:
            f.write(body + "\n")
        log("report -> %s" % out_md)

    log("done %.1fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
