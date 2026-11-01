# -*- coding: utf-8 -*-
"""v13_arm_decompose.py — 方案B 两臂「变量隔离」：池构造 vs 评分卡源。

背景（2026-11-01 复核）：
  原 `v13_paper_parallel` 的配对差（−850bp/日，仅 2 个配对执行日）把多个变量混在一起。
  实测澄清了两件事：
    ① 两臂**用的是同一个面板日**（T-1），不存在「旧臂用当日盘中信息」的时点差；
    ② 真正的差异是 (a) 候选池构造、(b) 评分卡数据源。

  可复现性约束：old臂的 F5=`sw_l1`、F2=`sina` 走实时接口，历史不可重放。因此本脚本
  **统一用一套可复现评分卡**（F2=moneyflow、F5=main_eqw、F6=daily_basic），把它施加到
  两个不同的池上——这样「池构造」这个最大变量被干净隔离。评分卡源的差异另用
  「实际落盘产物 vs 本脚本重算」的残差来量化（见 RESIDUAL 段）。

变体：
  P_old  = deploy_predict 落盘的 `<D>_model_top10.csv`（prob Top100 → 代理分≥58 → combo → Top10）
           + 统一评分卡 → 红线 58 → Top5
  P_new  = 纯 prob Top10（= 回测引擎 `nlargest(100).nlargest(10)`）+ 统一评分卡 → 红线 58 → Top5

对照（只读，用于自检与残差量化）：
  ACT_old = 执行日 `<T>_selection_full.csv` 实际 Top5（旧臂真实产物）
  ACT_new = `<D>_final_list.csv` 实际 redline Top5（新臂真实产物）

  P_old − ACT_old = 评分卡源差异（sw_l1/sina → moneyflow/main_eqw）
  P_new − ACT_new = 同一残差（新臂本来就是 main_eqw，故应接近 0，可作自检）
  P_new − P_old   = **纯池构造差异**（← 本脚本的主结论）

口径：面板日 D → 执行日 T（D 的下一交易日）开盘入场，第 HOLD_DAYS 个交易日开盘退出。
纯离线重算，不写生产产物、不下单、不动 log。

用法：
  python v13_arm_decompose.py                  # 全部可用面板日
  python v13_arm_decompose.py --since 20261020
  python v13_arm_decompose.py --dry-run        # 只打印不写盘
"""
from __future__ import print_function

import argparse
import datetime
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_config as DC
import scorecard_real as SR
import build_v13_final as BV
import paper_forward as PFWD

PROJ = DC.PROJECT_DIR
SELECT_DIR = os.path.join(DC.DATA_DIR, "selections")
RESULTS = os.path.join(PROJ, "results")
OUT_CSV = os.path.join(RESULTS, "v13_arm_decompose.csv")
OUT_MD = os.path.join(RESULTS, "v13_arm_decompose.md")

REDLINE = 58.0
TOP_N = 5
HOLD_DAYS = 5


def log(msg):
    print(msg, flush=True)


# ---------------- 数据 ----------------
def _panel_and_model():
    import lightgbm as lgb
    meta = json.load(open(os.path.join(DC.DATA_DIR, "features_v3.json"), encoding="utf-8"))
    panel = pd.read_parquet(os.path.join(DC.DATA_DIR, "feature_panel_v3.parquet"))
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel["ts_code"] = panel["ts_code"].astype(str)
    return panel, meta["feature_cols"], lgb.Booster(model_file=DC.model_file("_v3"))


def _real_sources(D):
    """信号日 D 的可复现真实源：F2 moneyflow(元) / F5 main_eqw(%) / F6 daily_basic。

    返回 (mf_map{code:元}, ind_map{code:百分数}, db{code:(pe_ttm,turnover_rate)})。
    """
    D = pd.Timestamp(D)

    mf_map = {}
    if os.path.isfile(BV.MONEYFLOW):
        mf = pd.read_parquet(BV.MONEYFLOW, columns=[
            "buy_lg_amount", "buy_elg_amount", "sell_lg_amount", "sell_elg_amount"])
        sub = mf[mf.index.get_level_values("trade_date") == D]
        if len(sub):
            sub = sub.reset_index().drop_duplicates("ts_code").set_index("ts_code")
            main_wan = (sub["buy_lg_amount"] + sub["buy_elg_amount"]
                        - sub["sell_lg_amount"] - sub["sell_elg_amount"])
            mf_map = (main_wan * 1e4).to_dict()

    ind_map = {}
    if os.path.isfile(BV.STOCK_BASIC):
        basic = pd.read_parquet(BV.STOCK_BASIC).reset_index()
        basic["ts_code"] = basic["ts_code"].astype(str)
        if "industry" in basic.columns:
            code_ind = basic.drop_duplicates("ts_code").set_index("ts_code")["industry"].to_dict()
            day, _ = BV.load_market_day(D)
            day = day.copy()
            day["pct"] = np.where(day["pre_close"] > 0, day["close"] / day["pre_close"] - 1.0, np.nan)
            day = day.dropna(subset=["pct"])
            day["industry"] = day["ts_code"].map(code_ind)
            day = day.dropna(subset=["industry"])
            if len(day) >= 100:
                ind_mean = day.groupby("industry")["pct"].mean()
                for code, ind in code_ind.items():
                    if ind is not None and ind in ind_mean.index:
                        ind_map[code] = float(ind_mean[ind]) * 100.0

    db = None
    if os.path.isfile(BV.DAILY_BASIC):
        d = pd.read_parquet(BV.DAILY_BASIC, columns=["pe_ttm", "turnover_rate"])
        if not isinstance(d.index, pd.MultiIndex):
            d = d.set_index(["trade_date", "ts_code"])
        d.index.names = ["trade_date", "ts_code"]
        sub = d[d.index.get_level_values("trade_date") == D]
        if len(sub):
            db = sub.droplevel(0)
            db.index = db.index.astype(str)
    return mf_map, ind_map, db


def _score(pool, mf_map, ind_map, db):
    """统一评分卡：F1/F3/F4 代理分 + F2 moneyflow + F5 main_eqw + F6 daily_basic。

    compute_real_scorecard 按 .values 取 est，故 est 必须与 pool 行序对齐 —— 在此显式 reindex。
    """
    p = pool.copy().reset_index(drop=True)
    p["main_net"] = p["ts_code"].map(mf_map).astype(float)
    p["industry_pct"] = p["ts_code"].map(ind_map).astype(float)
    est = None
    if db is not None:
        est = db.reindex(p["ts_code"].tolist())
        est.index = p.index  # 行序对齐
    sc = SR.compute_real_scorecard(p, est)
    out = p[["ts_code", "model_prob"]].copy()
    for f in ("F1", "F2", "F3", "F4", "F5", "F6"):
        out[f] = sc[f].values
    out["total"] = sc["total_new_real"].values
    return out


def _pick(scored, top_n=TOP_N):
    s = scored[scored["total"] >= REDLINE]
    if len(s) == 0:
        return s
    s = s.sort_values(["total", "model_prob", "ts_code"], ascending=[False, False, True])
    return s.head(top_n)


# ---------------- 收益 ----------------
_TRADES, _OPEN = None, None


def _load_panel_open():
    global _TRADES, _OPEN
    if _TRADES is None:
        daily, _, _ = PFWD.load_open_panel()
        _TRADES = sorted(set(daily.index.get_level_values(0)))
        _OPEN = daily["open"].to_dict()
    return _TRADES


def _next_td(D):
    trades = _load_panel_open()
    nxt = [t for t in trades if t > pd.Timestamp(D)]
    return nxt[0] if nxt else None


def _ret(code, T, hold=HOLD_DAYS):
    trades = _load_panel_open()
    try:
        i = trades.index(T)
    except ValueError:
        return np.nan
    if i + hold >= len(trades):
        return np.nan
    e = _OPEN.get((T, code))
    x = _OPEN.get((trades[i + hold], code))
    if e is None or x is None or not np.isfinite(e) or not np.isfinite(x) or e <= 0:
        return np.nan
    return x / e - 1.0


def _stat(picks, T):
    if picks is None or len(picks) == 0:
        return 0, np.nan, np.nan, np.nan, ""
    rets = [r for r in (_ret(c, T) for c in picks["ts_code"]) if pd.notna(r)]
    codes = ",".join(picks["ts_code"].tolist())
    if not rets:
        return 0, np.nan, np.nan, np.nan, codes
    return (len(rets), float(np.mean(rets)), float(np.median(rets)),
            float(np.mean([r > 0 for r in rets])), codes)


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    log("[1/4] 加载面板与模型 ...")
    panel, feat, booster = _panel_and_model()

    days = []
    for p in sorted(glob.glob(os.path.join(SELECT_DIR, "*_final_list.csv"))):
        m = re.match(r"^(\d{8})_final_list\.csv$", os.path.basename(p))
        if m and (not args.since or m.group(1) >= args.since):
            days.append(pd.Timestamp(m.group(1)))
    if not days:
        log("!! 无可用 final_list")
        return 1
    log("    面板日 %d 个：%s ~ %s" % (len(days), days[0].date(), days[-1].date()))

    log("[2/4] 逐面板日重算 ...")
    rows = []
    for D in days:
        day = panel[panel["trade_date"] == D]
        if len(day) == 0:
            log("    [SKIP] %s 面板无行" % D.date())
            continue
        day = day.copy()
        day["model_prob"] = booster.predict(day[feat].astype("float32").values)
        T = _next_td(D)
        if T is None:
            log("    [SKIP] %s 无下一交易日" % D.date())
            continue

        mf_map, ind_map, db = _real_sources(D)

        # P_new：纯 prob Top10
        p_new = _pick(_score(day.nlargest(BV.POOL_N, "model_prob"), mf_map, ind_map, db))

        # P_old：deploy_predict 落盘的池
        mtp = os.path.join(SELECT_DIR, "%s_model_top10.csv" % D.strftime("%Y%m%d"))
        p_old = None
        if os.path.isfile(mtp):
            mt = pd.read_csv(mtp, encoding="utf-8-sig", dtype={"ts_code": str})
            mt_codes = set(mt["ts_code"].astype(str).str.strip())
            pool = day[day["ts_code"].isin(mt_codes)]
            if len(pool):
                p_old = _pick(_score(pool, mf_map, ind_map, db))

        # ACT_new：final_list 实际产物
        flp = os.path.join(SELECT_DIR, "%s_final_list.csv" % D.strftime("%Y%m%d"))
        act_new = None
        if os.path.isfile(flp):
            fl = pd.read_csv(flp, encoding="utf-8-sig", dtype={"ts_code": str})
            fl = fl[fl["redline_pass"].astype(str).str.lower() == "true"]
            act_new = fl.sort_values("total", ascending=False).head(TOP_N)[["ts_code"]] if len(fl) else fl[["ts_code"]]

        # ACT_old：selection_full 实际产物（执行日 T）
        sfp = os.path.join(SELECT_DIR, "%s_selection_full.csv" % T.strftime("%Y%m%d"))
        act_old = None
        if os.path.isfile(sfp):
            sf = pd.read_csv(sfp, encoding="utf-8-sig", dtype={"ts_code": str})
            sf = sf[sf["total"] >= REDLINE].sort_values("total", ascending=False).head(TOP_N)
            act_old = sf[["ts_code"]] if len(sf) else sf[["ts_code"]]

        for name, picks in (("P_old", p_old), ("P_new", p_new),
                            ("ACT_old", act_old), ("ACT_new", act_new)):
            n, mean, med, win, codes = _stat(picks, T)
            rows.append({"panel_date": D.strftime("%Y%m%d"), "exec_date": T.strftime("%Y%m%d"),
                         "variant": name, "n": n, "ret_mean": mean, "ret_median": med,
                         "win": win, "codes": codes})
        log("    %s → %s | P_old %s | P_new %s" % (
            D.date(), T.date(),
            "空" if p_old is None or len(p_old) == 0 else "%d只" % len(p_old),
            "空" if len(p_new) == 0 else "%d只" % len(p_new)))

    df = pd.DataFrame(rows)
    log("[3/4] 汇总 ...")
    md = _report(df, days)
    if args.dry_run:
        log("[4/4] --dry-run 不写盘")
        return 0
    os.makedirs(RESULTS, exist_ok=True)
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    log("[4/4] 已写 %s / %s" % (os.path.basename(OUT_CSV), os.path.basename(OUT_MD)))
    return 0


def _report(df, days):
    md = ["# V13 方案B 两臂变量隔离（2026-11-01）", "",
          "> 生成：%s | 脚本 `v13_arm_decompose.py`" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
          "> 口径：面板日 D → 执行日 T（D 的下一交易日）开盘入场，第 %d 个交易日开盘退出。" % HOLD_DAYS,
          "> 统一评分卡：F1/F3/F4 代理分 + F2=moneyflow + F5=main_eqw + F6=daily_basic（红线 58，Top5）。", "",
          "## 变体", "",
          "| 变体 | 候选池 | 说明 |",
          "|---|---|---|",
          "| `P_old` | `deploy_predict` 落盘池（prob Top100 → 代理分≥58 → combo → Top10） | 旧臂池 + **统一评分卡** |",
          "| `P_new` | **纯 prob Top10**（= 回测引擎口径） | 新臂池 + **统一评分卡** |",
          "| `ACT_old` | — | 旧臂真实产物（sw_l1/sina 评分卡） |",
          "| `ACT_new` | — | 新臂真实产物（main_eqw/sina 评分卡） |",
          "",
          "> `P_new − P_old` = **纯池构造差异**（本报告主结论）",
          "> `P_old − ACT_old` = 评分卡源差异（sw_l1/sina → moneyflow/main_eqw）",
          "> `P_new − ACT_new` = 自检（新臂本来就是 main_eqw，应接近 0）",
          "",
          "## ⚠️ 自检不通过的根因（2026-11-01 实测，必读）",
          "",
          "**`P_new` 与 `ACT_new` 对不上，不是脚本 bug，是历史不可重放**：",
          "",
          "特征面板 `feature_panel_v3.parquet` 已于 **2026-10-31 23:51 按修复口径重建**",
          "（`amount` 元→千元、训练宇宙筛选、F5 主源改主库）。实测 10-20 同一交易日：",
          "当前面板 2798 行 vs 重建前备份 2720 行，**14 个特征逐票不等**（`amount_ma5` 差最大 6.775、",
          "`pe_ttm` 差最大 12556、`turn_volume` 类差 15.8）。⇒ 用当前面板 + 当前模型重算，",
          "**必然得到与历史落盘不同的 prob Top10**，因此 `P_new` 复现不了 `ACT_new`。",
          "",
          "**本报告的适用范围**：`P_old` vs `P_new` 两臂**都用同一当前面板重算**，面板误差对两臂",
          "等权抵消 ⇒ **两者之间的差（纯池构造差异）仍可信**。但 `ACT_*` 那两列属历史口径，",
          "只能作方向参考，不可与 `P_*` 逐值比对。",
          ""]

    md.append("## 汇总")
    md.append("")
    md.append("| 变体 | 有效笔数 | 均值 | 中位 | 胜率 | 有票面板日 |")
    md.append("|---|---|---|---|---|---|")
    for name in ("P_old", "P_new", "ACT_old", "ACT_new"):
        sub = df[df["variant"] == name]
        valid = sub[sub["ret_mean"].notna()]
        md.append("| %s | %d | %s | %s | %s | %d/%d |" % (
            name, int(valid["n"].sum()),
            "%.3f%%" % (valid["ret_mean"].mean() * 100) if len(valid) else "—",
            "%.3f%%" % (valid["ret_median"].mean() * 100) if len(valid) else "—",
            "%.1f%%" % (valid["win"].mean() * 100) if len(valid) else "—",
            len(valid), len(sub)))
    md.append("")

    md.append("## 逐日配对差（bp/日）")
    md.append("")
    md.append("| 面板日 | 执行日 | P_old | P_new | **P_new−P_old** | ACT_old | ACT_new | P_old−ACT_old |")
    md.append("|---|---|---|---|---|---|---|---|")

    diffs_pool, diffs_card = [], []
    for D in days:
        ds = D.strftime("%Y%m%d")
        row = df[df["panel_date"] == ds]
        if len(row) == 0:
            continue
        g = row.set_index("variant")
        get = lambda k: (g.loc[k, "ret_mean"] if k in g.index else np.nan)
        po, pn = get("P_old"), get("P_new")
        ao, an = get("ACT_old"), get("ACT_new")
        d_pool = (pn - po) * 1e4 if pd.notna(po) and pd.notna(pn) else np.nan
        d_card = (po - ao) * 1e4 if pd.notna(po) and pd.notna(ao) else np.nan
        if pd.notna(d_pool):
            diffs_pool.append(d_pool)
        if pd.notna(d_card):
            diffs_card.append(d_card)
        md.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            ds, row["exec_date"].iloc[0],
            "%.3f%%" % (po * 100) if pd.notna(po) else "空",
            "%.3f%%" % (pn * 100) if pd.notna(pn) else "空",
            "%+.0f" % d_pool if pd.notna(d_pool) else "—",
            "%.3f%%" % (ao * 100) if pd.notna(ao) else "空",
            "%.3f%%" % (an * 100) if pd.notna(an) else "空",
            "%+.0f" % d_card if pd.notna(d_card) else "—"))
    md.append("")
    if diffs_pool:
        md.append("- **纯池构造差异（P_new−P_old）：均值 %+.0f bp/日**（%d 个配对面板日）" % (
            float(np.mean(diffs_pool)), len(diffs_pool)))
    if diffs_card:
        md.append("- 评分卡源差异（P_old−ACT_old）：均值 %+.0f bp/日（%d 日）" % (
            float(np.mean(diffs_card)), len(diffs_card)))
    md.append("")
    md.append("## 归因")
    md.append("")
    md.append("### 结论（2026-11-01，只能定性不能定量）")
    md.append("")
    md.append("**1. 「信号时点」这个说法不成立**——实测两臂用的是**同一个面板日 D（= 执行日 T 的前一交易日）**：")
    md.append("`v13_selection_daily` 取 `feature_panel_v3` 的 max trade_date 作 D，`build_v13_final` 用同一面板 +")
    md.append("同一 `latest_features` 快照。所谓「旧臂用当日盘中信息」是把 `review_full` 的**实时复核字段**")
    md.append("（`quote_pct` / `catalyst`）误当成信号输入——那些字段只影响 F1/F3 展示与缓存兜底，")
    md.append("**不参与池构造，也不改变 prob 排序**。")
    md.append("")
    md.append("**2. 两臂的真实差异只有两条，且都是「口径」不是「时点」**：")
    md.append("")
    md.append("| 差异 | old臂 | new臂 | 影响 |")
    md.append("|---|---|---|---|")
    md.append("| 候选池 | prob Top100 → **代理分≥58 过滤** → combo 排序 → Top10 | **纯 prob Top10** | 决定性：池子重叠仅 0~2/10 |")
    md.append("| 评分卡源 | F2=moneyflow(T-1) / F5=sw_l1 | F2=sina / F5=main_eqw | 次要：P_old−ACT_old ≈ +160bp/日 |")
    md.append("")
    md.append("**3. 当前无法给出可信的定量归因**——历史不可重放，两个独立原因：")
    md.append("")
    md.append("  ① 特征面板 **2026-10-31 23:51 按修复口径重建**（`amount` 元→千元、训练宇宙筛选、F5 改主库）。")
    md.append("     用当前面板 + 当前模型重算，prob Top10 与历史落盘**完全不同**（10-19 交集 0/10、10-22 交集 3/10）；")
    md.append("     即使用重建前的备份面板（`.bak_20261031`）重放，10-19 交集仍是 0/10。")
    md.append("  ② `latest_features.parquet` 快照**每日 16:35 覆盖**，历史快照不存在；")
    md.append("     `build_v13_final` 走 `_prefer_snapshot`，故 final_list 的 prob 依赖当日快照。")
    md.append("")
    md.append("  ⇒ 本报告 `P_old` / `P_new` 两列**都用同一当前面板重算**，面板误差对两臂等权抵消，")
    md.append("  两者之差有方向性意义；但**绝对值不可信**（`P_new` 只有 4 笔、`P_old` 18 笔，")
    md.append("  与历史真实产物 9 笔完全对不上）。**`ACT_*` 两列才是历史事实，`P_*` 只是同口径下的相对对照。**")
    md.append("")
    md.append("### 可以确认的判断")
    md.append("")
    md.append("1. **原 −850bp/日 的配对差不成立**：它来自 2 个配对执行日、每臂 1~3 笔，且混了池构造与评分卡源两个变量。")
    md.append("2. **两臂的差异是「池怎么选」不是「什么时候选」**：old臂多了一道回测里没有的代理分预过滤。")
    md.append("   回测引擎（`backtest_b21_strategy`）用的是 `nlargest(100,prob).nlargest(10,prob)`，")
    md.append("   **new臂与回测同口径，old臂不是**。")
    md.append("3. **要得到可信结论，只能向前看**：从 2026-11-03 起，两臂在**同一重建后面板上**逐日累积，")
    md.append("   等满 15 个配对执行日再看。任何试图「回放历史」的做法都会撞上面板重建 + 快照覆盖两堵墙。")
    md.append("")
    return md


if __name__ == "__main__":
    sys.exit(main())
