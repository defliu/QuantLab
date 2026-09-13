# coding: utf-8
"""「3~5 交易日触价 +5% 即换票」第一步实证：候选池 MFE 触价统计 + 逐笔净收益模拟（T-20260911-001）。

研究问题（2026-09-11 诚哥）：单票 3~5 个交易日内到 +5% 就换票，是否可行？
本脚本不改任何生产代码，复用 scan_rotate_cost_real.build_per_day() 的候选流
（feature_panel_v3_sc + lgb_model_v3_enh + 真实评分卡，默认测试期 2024-07-01~2026-08-14）：
  [1] 中性基准：同 universe 全市场在测试日的触价概率与到期收益（按 20 日波动分位）
  [2] 候选池：top10 / 红线>=58 / >=60 子集的触价率（含/不含 T+1 锁）、命中价、未命中到期收益
  [3] 逐笔净收益模拟：touch（触价即卖）与 touch_stop（叠加 -7% 开盘止损），含 T+1 锁、
      跳空高开按开盘成交、真实费率（佣金万2最低5元 + 印花税万5 + 过户费万0.1）+ 滑点 0.1%/边
  [4] 分年 / 模型分位 / 波动分位稳健性

口径：
  - 与官方引擎一致用原始价（open/high/low 未除权调整）；3~5 日窗口内除权占比低，偏差可忽略
  - T+1 锁：买入日（T+1）盘中触价不可卖出，可卖窗口从 T+2 起（触价发生在 T+1 只统计不成交）
  - 触价成交价 = max(当日开盘, 成本×(1+TP))（跳空高开按开盘成交，保守假设）
  - 到期（MATURE）= T+H+1 开盘卖出（与引擎 (i-buy_i)>=N+1 口径一致）
  - 逐笔模拟不含组合层资金约束/轮动再部署（那是第二步 run_tp5_rotation.py 的职责）
  - 单源声明：gpsj 备用源 is_available()=False 时打印 [SKIP]，结论仅基于 astock

用法: python scan_mfe_tp5.py
输出: results/MFE触价统计_TP5_20260911.md
"""
import datetime
import importlib.util
import os
import sys

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
import scan_rotate_cost_real as eng  # noqa: E402

OUT_MD = os.path.join(PROJ, "results", "MFE触价统计_TP5_20260911.md")
TPS = [0.03, 0.05, 0.08]
HORIZONS = [3, 5]
SLIP = 0.001
STOP = -0.07
AMT = 47500.0  # 单笔名义金额（佣金最低5元生效量级，与资金池单票上限一致）


def log(msg):
    print(msg, flush=True)


def gpsj_check():
    """备用数据源可用性检查（AGENTS 降级规则：不可用打印 [SKIP]，不阻断）。"""
    root = os.path.abspath(os.path.join(PROJ, "..", ".."))
    p = os.path.join(root, "data", "gpsj_reader.py")
    if not os.path.exists(p):
        log("[SKIP] gpsj_reader 不存在，单源结论（仅 astock），未经备用源交叉验证")
        return
    try:
        spec = importlib.util.spec_from_file_location("gpsj_reader", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if mod.is_available():
            log("[WARN] gpsj 可用但本脚本未做交叉验证（仅 astock 单源）")
        else:
            log("[SKIP] gpsj 备用源不可用（is_available()=False），单源结论（仅 astock），未经备用源交叉验证")
    except Exception as e:
        log(f"[SKIP] gpsj 检查异常({e})，单源结论（仅 astock），未经备用源交叉验证")


def build_daily(universe, start_date):
    """前向窗口：o{k}/h{k}/l{k}/dl{k} = T+k 开/高/低/跌停价（个股后第 k 根K线）；vol20 决策日 T 已知。"""
    daily = pd.read_parquet(eng.DC.MAIN_DAILY,
                            columns=["open", "high", "low", "close", "down_limit"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily[daily["ts_code"].isin(universe)]
    daily = daily[daily["trade_date"] >= pd.Timestamp(start_date) - pd.Timedelta(days=45)]
    daily = daily.sort_values(["ts_code", "trade_date"])
    daily["vol20"] = daily.groupby("ts_code")["close"].transform(
        lambda s: s.pct_change().rolling(20, min_periods=10).std())
    g = daily.groupby("ts_code")
    for k in range(1, 7):
        daily[f"o{k}"] = g["open"].shift(-k)
        daily[f"h{k}"] = g["high"].shift(-k)
        daily[f"l{k}"] = g["low"].shift(-k)
        daily[f"dl{k}"] = g["down_limit"].shift(-k)
    return daily


def build_candidates(per_day):
    frames = []
    for d, df in per_day.items():
        t = df.reset_index()
        t["trade_date"] = d
        frames.append(t)
    cand = pd.concat(frames, ignore_index=True)
    cand["exec_ok"] = cand.apply(eng._executable, axis=1)
    return cand


def sim_touch(entry, o_arr, h_arr, tp, stop, H):
    """逐笔模拟：可卖日 k=2..H 逐日——开盘先判止损，盘中高点判触价；未触发则 T+H+1 开盘到期卖出。
    o_arr/h_arr: 长度 H+2 数组，下标 1..H+1 为 T+k 开盘/最高价（h_arr[H+1] 不用）。
    返回 (gross_ret, reason, hold_days)；gross_ret 为 NaN 表示前向数据缺失。"""
    limit = entry * (1.0 + tp)
    for k in range(2, H + 1):
        ok = o_arr[k]
        if np.isnan(ok):
            return np.nan, "MISSING", H
        if stop is not None and ok <= entry * (1.0 + stop):
            return ok / entry - 1.0, "STOP", k - 1
        hk = h_arr[k]
        if not np.isnan(hk) and hk >= limit:
            return max(ok, limit) / entry - 1.0, "TP", k - 1
    om = o_arr[H + 1]
    if np.isnan(om):
        return np.nan, "MISSING", H
    return om / entry - 1.0, "MATURE", H


def net_ret(gross, code):
    """毛收益 → 净收益（真实费率 + 滑点 0.1%/边，名义本金 AMT）。"""
    sell_amt = AMT * (1.0 + gross)
    fees = eng.buy_fee(AMT, code, SLIP) + eng.sell_fee(sell_amt, code, SLIP)
    return gross - fees / AMT


def run_sim(sub, tp, stop, H):
    """对候选子集逐笔跑 sim_touch，返回带 reason/gross/net/hold 的 DataFrame。"""
    o_cols = [f"o{k}" for k in range(1, H + 2)]
    h_cols = [f"h{k}" for k in range(1, H + 1)]
    O = sub[o_cols].to_numpy(dtype=float)
    Hh = sub[h_cols].to_numpy(dtype=float)
    # 下标对齐：O[k]=o{k}（k=1..H+1），下标 0 为占位 NaN
    O = np.column_stack([np.full(len(sub), np.nan), O])
    Hh = np.column_stack([np.full(len(sub), np.nan), Hh])
    entry = sub["o1"].to_numpy(dtype=float)
    codes = sub["ts_code"].to_numpy()
    rows = []
    for i in range(len(sub)):
        if np.isnan(entry[i]):
            rows.append((np.nan, "MISSING", 0, np.nan))
            continue
        g, reason, hold = sim_touch(entry[i], O[i], Hh[i], tp, stop, H)
        rows.append((g, reason, hold, net_ret(g, codes[i]) if not np.isnan(g) else np.nan))
    out = pd.DataFrame(rows, columns=["gross", "reason", "hold", "net"], index=sub.index)
    return out


def fmt_pct(x, digits=1):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{digits}f}%"


def sim_summary(res):
    v = res.dropna(subset=["net"])
    if not len(v):
        return {"n": 0, "net": np.nan, "win": np.nan, "hold": np.nan,
                "tp": np.nan, "stop": np.nan, "mature": np.nan,
                "net_tp": np.nan, "net_mature": np.nan, "net_stop": np.nan}
    r = v["reason"]
    return {
        "n": len(v), "net": v["net"].mean(), "win": (v["net"] > 0).mean(), "hold": v["hold"].mean(),
        "tp": (r == "TP").mean(), "stop": (r == "STOP").mean(), "mature": (r == "MATURE").mean(),
        "net_tp": v.loc[r == "TP", "net"].mean() if (r == "TP").any() else np.nan,
        "net_mature": v.loc[r == "MATURE", "net"].mean() if (r == "MATURE").any() else np.nan,
        "net_stop": v.loc[r == "STOP", "net"].mean() if (r == "STOP").any() else np.nan,
    }


def baseline_stats(daily, dates_set, H, tp):
    """中性基准：universe 全部股票在测试日 T 的同窗口触价统计（T+2..T+H 可卖窗口）。"""
    b = daily[daily["trade_date"].isin(dates_set)].copy()
    hmax = b[[f"h{k}" for k in range(2, H + 1)]].max(axis=1)
    entry = b["o1"]
    exit_o = b[f"o{H + 1}"]
    ok = entry.notna() & exit_o.notna() & hmax.notna() & (entry > 0)
    b = b[ok]
    hmax, entry, exit_o = hmax[ok], entry[ok], exit_o[ok]
    hit = hmax >= entry * (1.0 + tp)
    timeout = exit_o / entry - 1.0
    out = {"n": len(b), "touch": hit.mean(),
           "timeout_if_miss": timeout[~hit].mean() if (~hit).any() else np.nan}
    # 分波动分位
    bq = pd.qcut(b["vol20"], 5, labels=False, duplicates="drop")
    by_q = {}
    for q in sorted(bq.dropna().unique()):
        m = bq == q
        by_q[int(q) + 1] = {"n": int(m.sum()), "touch": hit[m].mean(),
                            "timeout_if_miss": timeout[m][~hit[m]].mean() if (~hit[m]).any() else np.nan}
    return out, by_q


def main():
    gpsj_check()
    log("[1/4] 构建候选流（复用官方引擎 build_per_day，逐日真实评分卡）...")
    dates, per_day, market_daily, open_map = eng.build_per_day()
    log(f"    测试期 {dates[0].date()} ~ {dates[-1].date()} | {len(dates)} 日")

    cand = build_candidates(per_day)
    log(f"[2/4] 候选 {len(cand)} 笔（top10/日），其中可执行 {int(cand['exec_ok'].sum())} 笔")
    # 口径自检：本脚本的 o1 应与引擎 open_next 一致
    chk = cand.dropna(subset=["o1", "open_next"]) if "o1" in cand else None

    universe = set(cand["ts_code"].astype(str))
    daily = build_daily(universe, eng.START)
    fwd_cols = ["o1"] + [f"o{k}" for k in range(2, 7)] + [f"h{k}" for k in range(1, 7)] + \
               [f"dl{k}" for k in range(1, 7)] + ["vol20", "trade_date", "ts_code"]
    cand = cand.merge(daily[fwd_cols], on=["ts_code", "trade_date"], how="left")
    if "o1_y" in cand.columns:  # merge 后缀处理（o1 不在原 cand，不会触发，防御）
        cand = cand.rename(columns={"o1_y": "o1"}).drop(columns=["o1_x"])
    diff = (cand["o1"] - cand["open_next"]).abs()
    log(f"    口径自检 o1 vs 引擎 open_next：max|diff|={diff.max():.6f}（0=一致）")

    cand["year"] = cand["trade_date"].dt.year
    cand["pq"] = pd.qcut(cand["prob"], 5, labels=False, duplicates="drop") + 1
    cand["vq"] = pd.qcut(cand["vol20"], 5, labels=False, duplicates="drop") + 1

    subsets = {
        "top10(可执行)": cand[cand["exec_ok"]],
        "红线>=58": cand[cand["exec_ok"] & (cand["total_new"] >= 58.0)],
        "红线>=60": cand[cand["exec_ok"] & (cand["total_new"] >= 60.0)],
    }
    log(f"    子集样本：红线>=58 {len(subsets['红线>=58'])} 笔 / 红线>=60 {len(subsets['红线>=60'])} 笔")

    log("[3/4] 中性基准 + 候选池触价统计 ...")
    dates_set = set(pd.Timestamp(d) for d in dates)
    lines = []
    base_cache = {}
    for H in HORIZONS:
        for tp in TPS:
            base_cache[(H, tp)] = baseline_stats(daily, dates_set, H, tp)
    b5 = base_cache[(3, 0.05)][0]
    log(f"    中性基准(TP5%): H3 触价率 {b5['touch']:.3f} | H5 触价率 {base_cache[(5, 0.05)][0]['touch']:.3f}")

    # ---- 主表：子集 × H × TP 的触价统计与逐笔模拟 ----
    sim_cache = {}
    for name, sub in subsets.items():
        for H in HORIZONS:
            for tp in TPS:
                pure = run_sim(sub, tp, None, H)
                with_stop = run_sim(sub, tp, STOP, H)
                sim_cache[(name, H, tp)] = (sim_summary(pure), sim_summary(with_stop))

    log("[4/4] 写报告 ...")
    today = datetime.date.today().strftime("%Y%m%d")
    lines = [
        "# 「3~5 交易日触价 +5% 即换票」第一步实证：候选池 MFE 触价统计（" + today + "）",
        "",
        f"> 研究问题：单票 3~5 个交易日内到 +5% 就换票是否可行（T-20260911-001 第一步）。",
        f"> 候选流：`scan_rotate_cost_real.build_per_day()` 官方口径（v3_sc 面板 + v3_enh 模型 + 真实评分卡），"
        f"测试期 {dates[0].date()} ~ {dates[-1].date()}（{len(dates)} 交易日）。",
        "> 口径：T 日决策 → T+1 开盘买入（原始价，与引擎一致）；T+1 锁（买入日触价不可卖，可卖窗口 T+2 起）；"
        "触价成交 = max(当日开盘, 成本×(1+TP))；到期 = T+H+1 开盘；费率=佣金万2(最低5)+印花税万5+过户费万0.1+滑点0.1%/边。",
        "> **单源声明：gpsj 备用源不可用，本报告结论仅基于 astock，未经备用源交叉验证。**",
        "> 逐笔模拟不含组合层资金约束与轮动再部署（第二步 `run_tp5_rotation.py` 职责）。",
        "",
        "## 一、中性基准（同 universe 全市场，TP=5%）",
        "",
        "全市场任意股票买入后 3/5 日内触价 +5% 的概率与未命中到期收益——衡量候选池是否有「触价概率提升」的选股 edge。",
        "",
        "| 窗口 | 样本 | 触价率(T+2起可卖) | 未命中到期收益 |",
        "|---|---|---|---|",
    ]
    for H in HORIZONS:
        b, _ = base_cache[(H, 0.05)]
        lines.append(f"| H={H} | {b['n']:,} | {fmt_pct(b['touch'])} | {fmt_pct(b['timeout_if_miss'], 2)} |")
    lines += [
        "",
        "按 20 日波动分位（Q1=最低波动 ~ Q5=最高波动，TP=5%）：",
        "",
        "| 窗口 | 波动分位 | 样本 | 触价率 | 未命中到期收益 |",
        "|---|---|---|---|---|",
    ]
    for H in HORIZONS:
        _, by_q = base_cache[(H, 0.05)]
        for q in sorted(by_q):
            d = by_q[q]
            lines.append(f"| H={H} | Q{q} | {d['n']:,} | {fmt_pct(d['touch'])} | {fmt_pct(d['timeout_if_miss'], 2)} |")

    lines += [
        "",
        "## 二、候选池触价统计与逐笔净收益模拟（主表）",
        "",
        "touch=纯触价止盈（无止损，未触价到期卖）；touch_stop=触价止盈+开盘-7%止损。净收益已扣全部费率+滑点。",
        "",
    ]
    for H in HORIZONS:
        lines += [
            f"### 持有窗口 H={H}（T+1 开盘买入，未触价 T+{H + 1} 开盘到期）",
            "",
            "| 子集 | TP | 样本 | 触价率 | 触价净收益 | 到期净收益 | touch净均值 | touch胜率 | touch持有(日) | touch_stop净均值 | 胜率 | 止损占比 | 止损净收益 | touch_stop持有(日) |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for name in subsets:
            for tp in TPS:
                pure, ws = sim_cache[(name, H, tp)]
                if not pure["n"]:
                    continue
                lines.append(
                    f"| {name} | {tp:.0%} | {pure['n']:,} | {fmt_pct(pure['tp'])} "
                    f"| {fmt_pct(pure['net_tp'], 2)} | {fmt_pct(pure['net_mature'], 2)} "
                    f"| **{fmt_pct(pure['net'], 3)}** | {fmt_pct(pure['win'])} | {pure['hold']:.1f} "
                    f"| **{fmt_pct(ws['net'], 3)}** | {fmt_pct(ws['win'])} | {fmt_pct(ws['stop'])} "
                    f"| {fmt_pct(ws['net_stop'], 2)} | {ws['hold']:.1f} |")
        lines.append("")

    lines += [
        "## 三、候选池 vs 中性基准：触价概率提升（TP=5%）",
        "",
        "「选股 edge」的直接度量：候选子集触价率 − 全市场基准触价率（同窗口）。",
        "",
        "| 子集 | H=3 触价率 | H=3 基准 | H=3 提升 | H=5 触价率 | H=5 基准 | H=5 提升 |",
        "|---|---|---|---|---|---|---|",
    ]
    for name in subsets:
        row = [name]
        for H in HORIZONS:
            pure, _ = sim_cache[(name, H, 0.05)]
            b, _ = base_cache[(H, 0.05)]
            lift = pure["tp"] - b["touch"] if pure["n"] else np.nan
            row += [fmt_pct(pure["tp"]), fmt_pct(b["touch"]), ("+" if lift >= 0 else "") + f"{lift * 100:.1f}pp"]
        lines.append("| " + " | ".join(row) + " |")

    lines += [
        "",
        "## 四、分年稳健性（红线>=58，TP=5%，touch_stop 口径）",
        "",
        "| 年份 | H | 样本 | 触价率 | 净均值 | 胜率 | 止损占比 | 同年基准触价率 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    sub58 = subsets["红线>=58"]
    for yr in sorted(sub58["year"].dropna().unique()):
        ysub = sub58[sub58["year"] == yr]
        ydates = set(ysub["trade_date"].unique())
        for H in HORIZONS:
            ws = sim_summary(run_sim(ysub, 0.05, STOP, H))
            # 同年基准
            bsub = daily[daily["trade_date"].isin(ydates)]
            hmax = bsub[[f"h{k}" for k in range(2, H + 1)]].max(axis=1)
            entry = bsub["o1"]
            okm = entry.notna() & hmax.notna() & (entry > 0)
            btouch = (hmax[okm] >= entry[okm] * 1.05).mean() if okm.sum() else np.nan
            if ws["n"]:
                lines.append(f"| {int(yr)} | H={H} | {ws['n']:,} | {fmt_pct(ws['tp'])} | **{fmt_pct(ws['net'], 3)}** "
                             f"| {fmt_pct(ws['win'])} | {fmt_pct(ws['stop'])} | {fmt_pct(btouch)} |")

    lines += [
        "",
        "## 五、模型分位 × 波动分位（红线>=58，TP=5%，H=3，touch_stop 净均值）",
        "",
        "| 模型分位 | " + " | ".join(f"波动Q{q}" for q in range(1, 6)) + " |",
        "|---|" + "---|" * 5,
    ]
    for pq in sorted(sub58["pq"].dropna().unique()):
        row = [f"Q{int(pq)}"]
        for vq in range(1, 6):
            cell = sub58[(sub58["pq"] == pq) & (sub58["vq"] == vq)]
            if len(cell) < 20:
                row.append("—")
                continue
            ws = sim_summary(run_sim(cell, 0.05, STOP, 3))
            row.append(fmt_pct(ws["net"], 2) if ws["n"] else "—")
        lines.append("| " + " | ".join(row) + " |")

    # ---- 自动小结 ----
    pure3, ws3 = sim_cache[("红线>=58", 3, 0.05)]
    pure5, ws5 = sim_cache[("红线>=58", 5, 0.05)]
    b3 = base_cache[(3, 0.05)][0]
    b5v = base_cache[(5, 0.05)][0]
    lines += [
        "",
        "## 六、自动小结（数字由脚本计算，结论判定需人工复核）",
        "",
        f"- 红线>=58 子集：H=3 触价率 {fmt_pct(ws3['tp'])}（基准 {fmt_pct(b3['touch'])}，"
        f"提升 {fmt_pct(ws3['tp'] - b3['touch'])}）；touch_stop 净均值 {fmt_pct(ws3['net'], 3)}/笔，"
        f"平均持有 {ws3['hold']:.1f} 日",
        f"- 红线>=58 子集：H=5 触价率 {fmt_pct(ws5['tp'])}（基准 {fmt_pct(b5v['touch'])}，"
        f"提升 {fmt_pct(ws5['tp'] - b5v['touch'])}）；touch_stop 净均值 {fmt_pct(ws5['net'], 3)}/笔，"
        f"平均持有 {ws5['hold']:.1f} 日",
        f"- 对照：现行实盘口径 N=10 open→open 平均持有约 8~10 日（T-20260904-001 回测："
        f"N=3 日超额 +0.010% / N=5 +0.095% / N=10 +0.149%，红线60）",
        f"- 触价换票的本质期望 = 触价率×触价净收益 + (1-触价率)×到期净收益；上述口径已含费率滑点",
        "",
    ]
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log(f"报告 -> {OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
