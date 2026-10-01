# -*- coding: utf-8 -*-
"""T-20260928-G2DRY-2：G2 候选池枯竭根因与红线校准复核（只读计算，不碰生产代码/数据）。

任务A：2024-07-01~2026-08-14 候选通过率基线（既有审计只报 102 笔成交、从未报通过率）
任务B：红线阈值 60/58/55/52/50 walk-forward（IS 2024-07~2025-06 定参 → OOS 2025-07~2026-08 验证）
任务C：F5 绝对阈值 → 当日横截面分位打分的对照评估（红线 60，可选任务）

引擎复用 scan_rotate_cost_real.py（与 audit_stats_walkforward_20260901.md 同口径：
open→open 可执行、0.1% 滑点、一字板/停牌过滤、N=10、TOP2）。
锚点复现要求：红线 60 / N=10 / TOP2 / 滑点 0.1% → 成交 102 笔、组合日均超额 +0.150%，
不复现则中止（说明引擎口径有偏差，先修口径再跑）。

红线作用池口径说明：
  - 生产 deploy_predict_g2：模型分 Top100 预选池 → 红线 60（作用于 Top100）→ 按 total_new 取 Top10 落盘
  - 回测引擎 per_day：Top10 by prob（买入池）；本脚本将 BT.TOP10 临时置 100 保留 Top100 池，
    Top10 买入池由其派生（nlargest(10,'prob')，与原引擎逐行等价，锚点运行验证）
  - 「候选通过数」双口径：Top10 买入池（回测可成交口径）/ Top100 预选池（生产红线作用口径）

用法：
  C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe research/audit_redline_sweep_20260928.py

输出：results/G2候选池枯竭根因与红线校准复核_20260928.md（本脚本只写这一个文件）
"""
import datetime
import os
import re
import sys
import time

import numpy as np
import pandas as pd

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
sys.path.insert(0, PROJ)
DATA = os.path.join(PROJ, "data")
OUT = os.path.join(PROJ, "results", "G2候选池枯竭根因与红线校准复核_20260928.md")

# ---- 与 audit_stats_walkforward.py 完全一致的环境（模型/面板/区间）----
os.environ["BT_PANEL"] = os.path.join(DATA, "feature_panel_v3_enh2_n3_bt.parquet")
os.environ["BT_MODEL"] = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260825_1964t.txt"
os.environ["BT_META"] = os.path.join(DATA, "features_v3_g2_strong_real_20260825.json")
os.environ["BT_THRESHOLD"] = "60.0"
os.environ["BT_TOP"] = "2"
os.environ["BT_START"] = "2024-07-01"
os.environ["BT_END"] = "2026-08-14"

import scan_rotate_cost_real as BT  # noqa: E402
import review_full as RF  # noqa: E402

N = 10
SLIP = 0.001
IS_CUT = pd.Timestamp("2025-07-01")
THRS = [60, 58, 55, 52, 50]
ANCHOR_TRADES = 102
ANCHOR_EXCESS = 0.00150  # audit_stats_walkforward_20260901.md：组合日均超额 +0.150%
BOOT_SEED = 20260928
IND_OUTLIER = 20.0  # 任务书第八节：|industry_pct|>20% 视为脏数据，分位统计先剔除
W = {"F1": 0.25, "F2": 0.20, "F3": 0.20, "F4": 0.15, "F5": 0.10, "F6": 0.10}
F5_TOTAL_W = W["F5"] * 10.0  # F5 每变动 1 分 → total_new 变动 1.0 分


def block_bootstrap(series, block_len=11, n_boot=10000, seed=BOOT_SEED):
    """块长 11 日（=1 轮 N+1）的 block bootstrap，返回超额均值分布。"""
    rng = np.random.default_rng(seed)
    s = np.asarray(series, dtype=float)
    n = len(s)
    n_blocks = int(np.ceil(n / block_len))
    means = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, n, size=n_blocks)
        sample = np.concatenate([s[st:st + block_len] for st in starts])[:n]
        means[b] = np.nanmean(sample)
    return means


def ann(x):
    return (1.0 + x) ** 244 - 1.0


def main():
    t0 = time.time()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# G2 候选池枯竭根因与红线校准复核（T-20260928-G2DRY-2）",
        "",
        f"> 生成：{now} | 执行：DE（audit_redline_sweep_20260928.py，只读计算）",
        "> 引擎：`scan_rotate_cost_real.py`（open→open 可执行、0.1% 滑点、一字板/停牌过滤）| g2_strong_real（43特征/1964树）",
        "> 口径：N=10 / TOP2 | 测试期 2024-07-01 ~ 2026-08-14（= 模型 test split，516 交易日）",
        "> 前置：任务1（前视排查）已定案——F5 数据源健康、无前视偏差，本文不重复。",
        "",
    ]

    # ================= build =================
    print("[1/6] build_per_day（TOP10→100 保留生产口径 Top100 预选池）...", flush=True)
    BT.TOP10 = 100  # 临时保留 Top100 预选池（生产红线作用池）；Top10 买入池由其派生
    dates_all, per_day100, mkt_full, open_map = BT.build_per_day()
    keep = [d for d in dates_all if d in per_day100]
    if len(keep) != len(dates_all):
        print(f"    !! 有 {len(dates_all) - len(keep)} 个交易日被引擎跳过（<20 只），已剔除")
    dates = keep
    mkt_by_date = {d: float(mkt_full.iloc[i]) for i, d in enumerate(dates_all)}
    per_day10 = {d: per_day100[d].nlargest(10, "prob") for d in dates}
    is_dates = [d for d in dates if d < IS_CUT]
    oos_dates = [d for d in dates if d >= IS_CUT]
    print(f"    全期 {len(dates)} 日 | IS {len(is_dates)} 日 | OOS {len(oos_dates)} 日 | 耗时 {time.time()-t0:.0f}s", flush=True)

    # ================= 锚点复现 =================
    print("[2/6] 锚点复现（红线60 / N=10 / TOP2 / 滑点0.1% → 应为 102 笔 / +0.150%）...", flush=True)
    BT.THRESHOLD = 60.0
    a_tr, a_dr, _ = BT.simulate(dates, per_day10, N, SLIP, open_map, exec_ok=True)
    a_mkt = np.array([mkt_by_date[d] for d in dates])
    a_ex = np.array(a_dr) - a_mkt
    anchor_ex = float(np.nanmean(a_ex))
    print(f"    实测：{len(a_tr)} 笔 | 日均超额 {anchor_ex:+.4%} | 耗时 {time.time()-t0:.0f}s", flush=True)
    anchor_ok = (len(a_tr) == ANCHOR_TRADES) and abs(anchor_ex - ANCHOR_EXCESS) < 2e-4
    if not anchor_ok:
        print("!! 锚点未复现（102 笔 / +0.150%）——引擎口径有偏差，按任务书要求中止，先修口径再跑")
        sys.exit(2)
    lines += [
        "## 〇、锚点复现",
        "",
        f"- 红线 60 / N=10 / TOP2 / 滑点 0.1%：成交 **{len(a_tr)} 笔**（= 102 ✓）、组合日均超额 **{anchor_ex:+.3%}**"
        f"（audit_stats_walkforward_20260901 报 +0.150% ✓）——引擎口径与既有审计一致，后续统计可信。",
        "",
    ]

    # ================= 面板 industry_pct（市场状态 + F5 改造） =================
    print("[3/6] 面板 industry_pct（市场状态分层 + F5 改造基准）...", flush=True)
    ind = pd.read_parquet(BT.PANEL, columns=["trade_date", "ts_code", "industry_pct"])
    ind["trade_date"] = pd.to_datetime(ind["trade_date"])
    ind = ind[(ind["trade_date"] >= pd.Timestamp(BT.START)) & (ind["trade_date"] <= pd.Timestamp(BT.END))]
    ind_v = ind[ind["industry_pct"].notna() & (ind["industry_pct"].abs() <= IND_OUTLIER)].copy()
    # 当日横截面 pct rank（任务C：在有效值内排名；脏值(|v|>20%)/缺失 → 中性 5.0）
    ind_v["f5_rank"] = ind_v.groupby("trade_date")["industry_pct"].rank(pct=True) * 10.0
    ind_by_date = {d: g.set_index("ts_code")["industry_pct"] for d, g in ind.groupby("trade_date")}
    rank_by_date = {d: g.set_index("ts_code")["f5_rank"] for d, g in ind_v.groupby("trade_date")}
    board_med = ind_v.groupby("trade_date")["industry_pct"].median()  # 市场状态锚（已剔 |v|>20%）
    f5_fullmean = ind.groupby("trade_date")["industry_pct"].apply(lambda s: float(s.map(RF.score_f5).mean()))
    print(f"    面板 {len(ind)} 行 | 有效占比 {(len(ind_v)/max(1,len(ind))):.1%} | 耗时 {time.time()-t0:.0f}s", flush=True)

    # ================= 任务A：候选通过率基线 =================
    print("[4/6] 任务A：候选通过率基线 ...", flush=True)
    recs = []
    for d in dates:
        p10 = per_day10[d]
        p100 = per_day100[d]
        t10 = p10["total_new"].values
        t100 = p100["total_new"].values
        exec10 = p10.apply(BT._executable, axis=1).values
        rec = {
            "date": d,
            "max10": float(np.nanmax(t10)),
            "p90_10": float(np.nanpercentile(t10, 90)),
            "p75_10": float(np.nanpercentile(t10, 75)),
            "p50_10": float(np.nanpercentile(t10, 50)),
            "pass60e_10": int(((t10 >= 60) & exec10).sum()),
            "board_med": float(board_med.get(d, np.nan)),
            "f5_full": float(f5_fullmean.get(d, np.nan)),
        }
        for thr in THRS:
            rec[f"pass{thr}_10"] = int((t10 >= thr).sum())
            rec[f"pass{thr}_100"] = int((t100 >= thr).sum())
        recs.append(rec)
    dfA = pd.DataFrame(recs).set_index("date")

    def dist_row(name, s):
        s = s.dropna()
        return (f"| {name} | {s.mean():.2f} | {s.median():.2f} | {s.quantile(0.10):.2f} "
                f"| {s.quantile(0.90):.2f} | {s.max():.0f} |")

    lines += [
        "## 一、任务A：候选通过率基线（既有审计缺失的关键数字）",
        "",
        "> 候选池双口径：**Top10 买入池**（回测引擎 per_day，top10 by prob，买入只从这里发生）；"
        "**Top100 预选池**（生产 `deploy_predict_g2` 红线作用池，可比实盘「预选池内无股票通过红线」日志）。",
        "",
        "### 1.1 每日 total_new 分布（Top10 买入池内逐日统计量，再跨日聚合）",
        "",
        "| 逐日统计量 | 跨日均值 | 跨日中位 | 跨日p10 | 跨日p90 | 跨日最大 |",
        "|---|---|---|---|---|---|",
        dist_row("当日 max", dfA["max10"]),
        dist_row("当日 p90", dfA["p90_10"]),
        dist_row("当日 p75", dfA["p75_10"]),
        dist_row("当日 p50", dfA["p50_10"]),
        "",
        f"- Top10 池内**单日最高分都到不了 60** 的天数：{(dfA['max10'] < 60).mean():.1%}"
        f"（{(dfA['max10'] < 60).sum()}/{len(dfA)} 日）。",
        "",
        "### 1.2 每日过 60 候选数分布",
        "",
        "| 口径 | 日均 | 中位 | p75 | p90 | 最大 | 零候选天 | ≤1 只天 | ≥2 只天 | ≥5 只天 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, col in [("Top10 买入池", "pass60_10"), ("Top10 且可执行", "pass60e_10"), ("Top100 预选池", "pass60_100")]:
        s = dfA[col]
        lines.append(f"| {name} | {s.mean():.2f} | {s.median():.0f} | {s.quantile(0.75):.0f} | {s.quantile(0.90):.0f} "
                     f"| {s.max():.0f} | {(s == 0).mean():.1%} | {(s <= 1).mean():.1%} "
                     f"| {(s >= 2).mean():.1%} | {(s >= 5).mean():.1%} |")
    lines += [""]

    # 市场状态分层
    state = pd.qcut(dfA["board_med"], 4, labels=["最弱25%", "偏弱", "偏强", "最强25%"])
    dfA["_state"] = state
    lines += [
        "### 1.3 按市场状态分层（当日板块涨幅中位数四分位；ind 口径统计已剔 |值|>20% 异常值）",
        "",
        "| 市场状态 | 天数 | 板块中位涨幅均值 | 全市场F5均值 | Top10池日均过60 | Top100池日均过60 | Top100通过率 |",
        "|---|---|---|---|---|---|---|",
    ]
    for st in ["最弱25%", "偏弱", "偏强", "最强25%"]:
        g = dfA[dfA["_state"] == st]
        if not len(g):
            continue
        lines.append(f"| {st} | {len(g)} | {g['board_med'].mean():+.2f}% | {g['f5_full'].mean():.2f} "
                     f"| {g['pass60_10'].mean():.2f} | {g['pass60_100'].mean():.2f} | {g['pass60_100'].mean()/100:.1%} |")
    lines += [""]

    # 分时段
    lines += [
        "### 1.4 分时段通过数（候选枯竭的时间定位）",
        "",
        "| 时段 | 交易日 | Top10池日均过60 | Top100池日均过60 | Top100零候选天 |",
        "|---|---|---|---|---|",
    ]
    periods = [
        ("2024-07 ~ 2024-12", pd.Timestamp("2024-07-01"), pd.Timestamp("2024-12-31")),
        ("2025-01 ~ 2025-12", pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31")),
        ("2026-01 ~ 2026-05", pd.Timestamp("2026-01-01"), pd.Timestamp("2026-05-31")),
        ("2026-06 ~ 2026-08-14", pd.Timestamp("2026-06-01"), pd.Timestamp("2026-08-14")),
    ]
    for name, d0, d1 in periods:
        g = dfA[(dfA.index >= d0) & (dfA.index <= d1)]
        if not len(g):
            continue
        lines.append(f"| {name} | {len(g)} | {g['pass60_10'].mean():.2f} | {g['pass60_100'].mean():.2f} "
                     f"| {(g['pass60_100'] == 0).mean():.1%} |")
    lines += [""]

    # 实盘对照（只读解析 selections md；deploy 红线无候选时提前 return 不落盘 → 无文件≈0 候选）
    sel_dir = os.path.join(DATA, "selections", "g2")
    live = {}
    if os.path.isdir(sel_dir):
        for fn in os.listdir(sel_dir):
            m = re.match(r"(\d{8})_g2_selection\.md$", fn)
            if not m:
                continue
            try:
                txt = open(os.path.join(sel_dir, fn), encoding="utf-8").read()
            except OSError:
                continue
            live[m.group(1)] = len(re.findall(r"^\| \d+ \|", txt, flags=re.M))
    rec_dir = os.path.join(DATA, "reconcile_g2")
    trade_days_live = []
    if os.path.isdir(rec_dir):
        trade_days_live = sorted(
            re.match(r"reconcile_g2_(\d{8})\.md$", f).group(1)
            for f in os.listdir(rec_dir) if re.match(r"reconcile_g2_(\d{8})\.md$", f))
    live_rows = []
    if trade_days_live:
        d_lo, d_hi = trade_days_live[0], [x for x in trade_days_live if x <= "20260928"][-1]
        span = [x for x in trade_days_live if d_lo <= x <= d_hi]
        for x in span:
            live_rows.append((x, live.get(x, 0)))
        n_zero = sum(1 for _, v in live_rows if v == 0)
        mean_live = float(np.mean([v for _, v in live_rows]))
        lines += [
            "### 1.5 实盘对照（G2 上线以来，selections/g2 落盘行数 = 过 60 落盘数，≤10）",
            "",
            f"- 对照期 {d_lo} ~ {d_hi}（以 reconcile_g2 落盘日为交易日基准，共 {len(live_rows)} 个交易日）："
            f"实盘**日均过 60 落盘 {mean_live:.2f} 只**，其中 **零候选 {n_zero} 天（{n_zero/len(live_rows):.0%}）**。",
            "- 逐日明细（0 = 当日无 selection 文件，即红线无候选/任务未产文件）：",
            "",
            "```",
            " ".join(f"{x[2:]}:{v}" for x, v in live_rows),
            "```",
            "",
        ]
    print(f"    任务A统计完成 | 耗时 {time.time()-t0:.0f}s", flush=True)

    # ================= 任务B：阈值扫描 walk-forward =================
    print("[5/6] 任务B：红线 60/58/55/52/50 walk-forward ...", flush=True)

    def run_window(dd, pool, thr):
        BT.THRESHOLD = float(thr)
        trades, dr, n_skip = BT.simulate(dd, pool, N, SLIP, open_map, exec_ok=True)
        mkt = np.array([mkt_by_date[x] for x in dd])
        ex = np.array(dr) - mkt
        rets = np.array([t[2] for t in trades]) if trades else np.array([])
        nav = np.cumprod(1.0 + np.array(dr))
        mdd = float((nav / np.maximum.accumulate(nav) - 1.0).min())
        boot = block_bootstrap(ex, 11)
        lo, hi = np.percentile(boot, [2.5, 97.5])
        drop5 = float(np.sort(rets)[: len(rets) - 5].mean()) if len(rets) > 5 else np.nan
        return {
            "thr": thr, "n_trades": len(trades), "daily_ex": float(np.nanmean(ex)),
            "ann_ex": ann(float(np.nanmean(ex))), "mdd": mdd,
            "win": float((rets > 0).mean()) if len(rets) else np.nan,
            "drop5": drop5, "ci_lo": float(lo), "ci_hi": float(hi), "dr": np.array(dr),
        }

    def cand_mean(sub_dates, thr, pool_tag):
        return float(dfA.loc[sub_dates, f"pass{thr}_{pool_tag}"].mean())

    windows = [("全期 2024-07~2026-08", dates), ("IS 2024-07~2025-06", is_dates), ("OOS 2025-07~2026-08", oos_dates)]
    results = {}
    for wname, dd in windows:
        for thr in THRS:
            results[(wname, thr)] = run_window(dd, per_day10, thr)
            r = results[(wname, thr)]
            print(f"    [{wname}] 红线{thr}: {r['n_trades']}笔 超额{r['daily_ex']:+.3%} "
                  f"回撤{r['mdd']:.1%} CI[{r['ci_lo']:+.3%},{r['ci_hi']:+.3%}] | {time.time()-t0:.0f}s", flush=True)

    # 旧审计 OOS 对齐偏差量化（audit_stats_walkforward align_market 按位置对齐，OOS 段用了 IS 段基准）
    oos60 = results[("OOS 2025-07~2026-08", 60)]
    mkt_prefix = float(np.mean([mkt_by_date[d] for d in dates[: len(oos_dates)]]))
    old_style_oos_ex = float(np.nanmean(oos60["dr"])) - mkt_prefix

    lines += [
        "## 二、任务B：红线阈值 walk-forward（60/58/55/52/50）",
        "",
        "> 每格口径：N=10 / TOP2 / 滑点 0.1% / open→open 可执行。超额 = 组合日收益 − 同日全市场等权日收益"
        "（按日期对齐）。CI = block bootstrap 95%（块长 11 日，10000 次）。「日均候选」= Top10 买入池 / Top100 预选池当日过线数均值。",
        "",
    ]
    for wname, dd in windows:
        lines += [f"### {'2.1' if wname.startswith('全期') else ('2.2' if wname.startswith('IS') else '2.3')} {wname}",
                  "",
                  "| 红线 | 日均候选(T10/T100) | 交易笔数 | 日均超额 | 年化超额 | 最大回撤 | 胜率 | 剔最优5笔后均值 | bootstrap 95% CI |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for thr in THRS:
            r = results[(wname, thr)]
            c10 = cand_mean(dd, thr, "10")
            c100 = cand_mean(dd, thr, "100")
            lines.append(f"| {thr} | {c10:.1f} / {c100:.1f} | {r['n_trades']} | {r['daily_ex']:+.3%} | {r['ann_ex']:+.1%} "
                         f"| {r['mdd']:.1%} | {r['win']:.1%} | {r['drop5']:+.3%} "
                         f"| [{r['ci_lo']:+.3%}, {r['ci_hi']:+.3%}] |")
        lines += [""]

    is_best = max(THRS, key=lambda t: results[("IS 2024-07~2025-06", t)]["daily_ex"])
    oos_best_res = results[("OOS 2025-07~2026-08", is_best)]
    lines += [
        "### 2.4 walk-forward 结论（IS 定参 → OOS 验证）",
        "",
        f"- **IS 段最优红线 = {is_best}**（IS 日均超额 {results[('IS 2024-07~2025-06', is_best)]['daily_ex']:+.3%}，选参时 OOS 完全未知）。",
        f"- IS 前定红线 {is_best} 在 **OOS 日均超额 {oos_best_res['daily_ex']:+.3%}**"
        f"（{'正 → 有样本外支撑' if oos_best_res['daily_ex'] > 0 else '负/平 → 前定参数在 OOS 失效'}），"
        f"OOS bootstrap CI = [{oos_best_res['ci_lo']:+.3%}, {oos_best_res['ci_hi']:+.3%}]。",
        f"- 全期寻优对照（红线 60，OOS）：{results[('OOS 2025-07~2026-08', 60)]['daily_ex']:+.3%}。",
        "",
        "### 2.5 对既有审计的一处口径修正（重要）",
        "",
        f"`audit_stats_walkforward.py` 的 `align_market` 按**位置**对齐市场基准，OOS 段（非前缀子集）"
        f"错配到了 IS 段的市场收益——其报告的 OOS 红线 60 日均超额 +0.078% 实为"
        f"「OOS 组合收益 − IS 段市场均值」。本脚本按日期对齐后，OOS 红线 60 日均超额为 "
        f"**{results[('OOS 2025-07~2026-08', 60)]['daily_ex']:+.3%}**（按旧口径复算为 {old_style_oos_ex:+.3%}，"
        f"两者差异来自基准错配）。IS 段为前缀子集、不受影响，IS 结论（IS 最优=60）不变。",
        "",
    ]
    print(f"    任务B完成 | 耗时 {time.time()-t0:.0f}s", flush=True)

    # ================= 任务C：F5 横截面分位改造 =================
    print("[6/6] 任务C：F5 绝对阈值 → 当日横截面分位打分（红线 60）...", flush=True)
    empty_s = pd.Series(dtype=float)
    per_day10_rank, per_day100_rank = {}, {}
    dirty_pool_rows = 0
    f5_abs_pool_sum, f5_rank_pool_sum, pool_rows = 0.0, 0.0, 0
    for d in dates:
        raw_all = ind_by_date.get(d, empty_s)
        rk_all = rank_by_date.get(d, empty_s)
        for src, dst in ((per_day10[d], per_day10_rank), (per_day100[d], per_day100_rank)):
            raw = raw_all.reindex(src.index)
            f5_abs = raw.map(RF.score_f5)  # 与引擎同式：NaN → 5.0；脏值>20% 照旧打分（引擎口径）
            f5_rank = rk_all.reindex(src.index).fillna(5.0)  # 脏值/缺失 → 中性 5.0
            q = src.copy()
            q["total_new"] = src["total_new"] + F5_TOTAL_W * (f5_rank - f5_abs)
            dst[d] = q
            if dst is per_day100_rank:
                continue
        # 统计（只按 Top10 池计）
        raw10 = raw_all.reindex(per_day10[d].index)
        dirty_pool_rows += int((raw10.notna() & (raw10.abs() > IND_OUTLIER)).sum())
        f5_abs_pool_sum += float(raw10.map(RF.score_f5).sum())
        f5_rank_pool_sum += float(rk_all.reindex(per_day10[d].index).fillna(5.0).sum())
        pool_rows += len(per_day10[d])

    # rank 版逐日通过数（Top10/Top100，红线 60）
    rank_pass = []
    for d in dates:
        rank_pass.append({
            "date": d,
            "pass60_10": int((per_day10_rank[d]["total_new"] >= 60).sum()),
            "pass60_100": int((per_day100_rank[d]["total_new"] >= 60).sum()),
        })
    dfR = pd.DataFrame(rank_pass).set_index("date")
    dfR_is = dfR[dfR.index < IS_CUT]
    dfR_oos = dfR[dfR.index >= IS_CUT]

    resC = {}
    for wname, dd in [("全期", dates), ("IS", is_dates), ("OOS", oos_dates)]:
        resC[wname] = run_window(dd, per_day10_rank, 60)
        r = resC[wname]
        print(f"    [rank-F5 {wname}] 60: {r['n_trades']}笔 超额{r['daily_ex']:+.3%} | {time.time()-t0:.0f}s", flush=True)

    c60 = results[("全期 2024-07~2026-08", 60)]
    c60_is = results[("IS 2024-07~2025-06", 60)]
    c60_oos = results[("OOS 2025-07~2026-08", 60)]
    lines += [
        "## 三、任务C：F5 绝对阈值 → 当日横截面分位打分（对照评估）",
        "",
        "> 改造定义：F5_rank = 10 × 当日全市场（面板日）`industry_pct` 横截面分位（有效值内排名，"
        "缺失/|值|>20% 脏值 → 中性 5.0），全市场均值恒 ≈5；total_new 其余分量不动，"
        "total_new_rank = total_new + 1.0 × (F5_rank − F5_abs)。红线 60、其余口径与任务B完全一致。",
        "",
        f"- Top10 买入池 F5 均值：改造前 {f5_abs_pool_sum / pool_rows:.2f} → 改造后 {f5_rank_pool_sum / pool_rows:.2f}；"
        f"池内携带脏值（|industry_pct|>20%）行数 {dirty_pool_rows}/{pool_rows}（{(dirty_pool_rows/pool_rows):.2%}）。",
        "",
        "| 窗口 | F5 版本 | 日均过60（T10/T100） | 交易笔数 | 日均超额 | 年化超额 | 最大回撤 | 胜率 | 剔最优5笔后均值 | bootstrap 95% CI |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for wname, dfR_sub, base in [("全期", dfR, c60), ("IS", dfR_is, c60_is), ("OOS", dfR_oos, c60_oos)]:
        rk = resC[wname]
        p10r, p100r = float(dfR_sub["pass60_10"].mean()), float(dfR_sub["pass60_100"].mean())
        p10b = float(dfA.loc[[d for d in dfR_sub.index], "pass60_10"].mean())
        p100b = float(dfA.loc[[d for d in dfR_sub.index], "pass60_100"].mean())
        lines.append(f"| {wname} | 绝对阈值（现状） | {p10b:.1f} / {p100b:.1f} | {base['n_trades']} | {base['daily_ex']:+.3%} "
                     f"| {base['ann_ex']:+.1%} | {base['mdd']:.1%} | {base['win']:.1%} | {base['drop5']:+.3%} "
                     f"| [{base['ci_lo']:+.3%}, {base['ci_hi']:+.3%}] |")
        lines.append(f"| {wname} | 横截面分位（改造） | {p10r:.1f} / {p100r:.1f} | {rk['n_trades']} | {rk['daily_ex']:+.3%} "
                     f"| {rk['ann_ex']:+.1%} | {rk['mdd']:.1%} | {rk['win']:.1%} | {rk['drop5']:+.3%} "
                     f"| [{rk['ci_lo']:+.3%}, {rk['ci_hi']:+.3%}] |")
    lines += [""]

    # ================= 结论区（数字回填后手工定性，先落关键判定） =================
    zero_share_100 = float((dfA["pass60_100"] == 0).mean())
    lines += [
        "## 四、综合结论",
        "",
        f"（数字结论见上表；本节为脚本自动回填的硬事实，最终定性以主代理复核为准。）",
        "",
        f"1. **任务A**：回测全期 Top100 预选池日均过 60 = {dfA['pass60_100'].mean():.2f} 只，"
        f"Top10 买入池日均 = {dfA['pass60_10'].mean():.2f} 只，零候选天占比 {zero_share_100:.1%}；"
        f"最弱 25% 市场状态日均过 60（T100）= "
        f"{dfA[dfA['_state'] == '最弱25%']['pass60_100'].mean() if (dfA['_state'] == '最弱25%').any() else float('nan'):.2f} 只 vs "
        f"最强 25% = {dfA[dfA['_state'] == '最强25%']['pass60_100'].mean() if (dfA['_state'] == '最强25%').any() else float('nan'):.2f} 只。",
        f"2. **任务B**：IS 前定最优红线 = {is_best}，OOS 日均超额 {oos_best_res['daily_ex']:+.3%}"
        f"（CI [{oos_best_res['ci_lo']:+.3%}, {oos_best_res['ci_hi']:+.3%}]）。",
        f"3. **任务C**：改造后红线 60 全期日均过 60（T10）= {dfR['pass60_10'].mean():.2f} 只"
        f"（现状 {dfA['pass60_10'].mean():.2f}）；OOS 日均超额 {resC['OOS']['daily_ex']:+.3%}"
        f" vs 现状 {c60_oos['daily_ex']:+.3%}。",
        "",
        "## 五、口径与限制",
        "",
        "- 本脚本只读计算：未修改任何生产代码/配置/数据，未写 `data_live/`、`data/selections/`、`D:/QMT_POOL/`，未下单。",
        f"- 候选池：回测引擎 per_day = top10 by prob（本脚本同时保留 top100 预选池做生产口径对照）；"
        "生产买入 = top100 过红线后按 total_new 取 top10/补足，与回测存在已知的池内选票差异（既有口径，非本次引入）。",
        "- F5 数据源：引擎/生产评分卡均用面板/增量库 `industry_pct`（所属行业当日涨幅，%单位）；"
        "任务1定案无前视。市场状态分层用其当日中位数（已剔 |值|>20% 异常值，任务书第八节）。",
        "- OOS 区间与模型 test split 重叠（模型训练见过 2025-07~2026-08 标签），OOS 非真正样本外，"
        "仅缓解「参数事后选择」偏误；block bootstrap CI 宽，结论为弱证据。",
        "- 实盘对照的「无文件日」≈ 红线无候选（deploy 提前 return 不落盘），个别缺失也可能为任务未跑；"
        "live 行数上限 10（生产落盘 top10）。",
        "",
        "*仅供个人量化研究使用，不构成投资建议。市场有风险。*",
        "",
    ]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[完成] 报告: {OUT}")
    print(f"    总耗时 {time.time()-t0:.0f}s")
    print(f"    A: T100日均过60={dfA['pass60_100'].mean():.2f} T10={dfA['pass60_10'].mean():.2f} "
          f"零候选天={zero_share_100:.1%}")
    print(f"    B: IS最优={is_best} OOS={oos_best_res['daily_ex']:+.3%} | 旧口径OOS60={old_style_oos_ex:+.3%}")
    print(f"    C: rank-F5 OOS={resC['OOS']['daily_ex']:+.3%} vs abs {c60_oos['daily_ex']:+.3%}")


if __name__ == "__main__":
    main()
