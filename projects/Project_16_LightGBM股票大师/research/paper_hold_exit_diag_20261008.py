# -*- coding: utf-8 -*-
"""P16 纸面优化诊断（2026-10-08 夜，opencode，只读，不碰生产文件）。

三块诊断，全部为 §八 优化方案的数据基础：
  P1  G2 持有期敏感性 + N15 vs N10/N8/N5 直接配对（B1 前置条件，TRAE §1.2 只做过 vs N5）
  P2  v3_enh / 融合 持有期敏感性刷新（数据源至 10-08，窗口较 §1.2 的 ≤09-14 扩大）
  P3  止损触发口径诊断 open vs touch vs close（B2-①，rank<=3, N10，对齐 paper_forward_exit 口径）

方法：
  P1/P2 与 paper_forward_ab_stats.py 同口径：信号日 T 收盘出候选，T+1 开盘入场，
         持有 N 个交易日后开盘卖出，复权 open→open；同臂「所有 N 均到期」的公共信号窗口，
         逐笔配对 t（与 §1.2 方法一致，窗口口径保守）。
  P3 与 paper_forward_exit.py simulate_entry 逐行对齐（T+1、一字跌停顺延、峰值用截至昨日），
      仅改变 STOP 触发价：
        open   = 现行纸面口径（开盘价破线即卖，卖在开盘）
        touch  = 盘中低点触线即卖（近似实盘轮询触发；跳空低开卖在开盘，否则卖在触发线）
        close  = 收盘确认（14:50 价仍破线才卖，卖在收盘；对应 §7.3 异议1 的合规替代路径）
      TP/TRAIL 三变体均保持原 open 口径不变（TP/TRAIL 出场占比小，隔离 STOP 单一变量）。

用法：python research/paper_hold_exit_diag_20261008.py
输出：控制台 + results/P16纸面持有期与止损诊断_20261008.md
"""
import math
import os
import sys

import numpy as np
import pandas as pd

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
sys.path.insert(0, PROJ)
REAL = os.path.join(PROJ, "data", "real")
RESULTS = os.path.join(PROJ, "results")
MERGED = os.path.join(PROJ, "data_live", "merged_daily_full.parquet")

NS = [3, 5, 8, 10, 15]
STOP, TP, TRAIL_PCT, ACT = -0.07, 0.15, 0.08, 0.08
DELIST_DAYS, DELIST_LOSS = 60, 0.5

ARMS = {  # 臂 -> (候选csv, 该臂需考察的持有期集合)：G2 实盘 N15 需纳入；V1.x/ENS 现用 N10，不强制 N15
    "G2(live)": (os.path.join(REAL, "paper_forward_live.csv"), [3, 5, 8, 10, 15]),
    "v3_enh": (os.path.join(REAL, "paper_forward_ab_v3enh.csv"), [3, 5, 8, 10]),
    "融合(ENS)": (os.path.join(REAL, "paper_forward_ens.csv"), [3, 5, 8, 10]),
}

OUT_LINES = []


def log(s=""):
    print(s)
    OUT_LINES.append(s)


def t_of(d):
    d = pd.Series(d).dropna()
    if len(d) < 5 or d.std(ddof=1) == 0:
        return float("nan")
    return float(d.mean() / (d.std(ddof=1) / math.sqrt(len(d))))


# ---------------- P1/P2 ----------------
def build_rets():
    sd = pd.read_parquet(MERGED, columns=["open", "adj_factor"]).reset_index()
    sd["trade_date"] = pd.to_datetime(sd["trade_date"])
    sd["ds"] = sd["trade_date"].dt.strftime("%Y-%m-%d")
    sd["adj_open"] = sd["open"] * sd["adj_factor"]
    sd = sd.sort_values(["ts_code", "trade_date"])
    g = sd.groupby("ts_code")["adj_open"]
    sd["op1"] = g.shift(-1)
    rets = {}
    for N in NS:
        sd["opN"] = g.shift(-1 - N)
        m = sd.dropna(subset=["op1", "opN"])
        rets[N] = {c: dict(zip(sub["ds"], sub["opN"] / sub["op1"] - 1))
                   for c, sub in m.groupby("ts_code")}
    cal = sorted(sd["ds"].unique().tolist())
    return rets, cal


def hold_diag(tag, path, ns, rets, cal):
    df = pd.read_csv(path, encoding="utf-8-sig")
    df = df.drop_duplicates(subset=["date", "code"], keep="last")
    rows = []
    for _, r in df.iterrows():
        d0 = str(r["date"])
        if d0 not in cal:
            continue
        i = cal.index(d0)
        if i + max(ns) + 1 >= len(cal):
            continue  # 本臂最大 N 未到期 → 公共窗口剔除
        rec = {"rank": r.get("rank", np.nan)}
        ok = True
        for N in ns:
            v = rets[N].get(str(r["code"]), {}).get(d0)
            if pd.isna(v):
                ok = False
                break
            rec["r%d" % N] = float(v)
        if ok:
            rows.append(rec)
    m = pd.DataFrame(rows)
    if len(m) == 0:
        log("%s：公共窗口无样本" % tag)
        return None
    d0 = df["date"].min()
    sig_max = max(str(x) for x in df["date"]
                  if str(x) in cal and cal.index(str(x)) + max(ns) + 1 < len(cal))
    log("\n### %s（公共窗口：信号日 %s ~ %s，考察 N=%s，n=%d）"
        % (tag, d0, sig_max, "/".join(str(x) for x in ns), len(m)))
    log("| 口径 | n | %s |" % " | ".join("N%d" % N for N in ns))
    log("|" + "---|" * (len(ns) + 2))
    sub = m[m["rank"].fillna(99) <= 15] if "rank" in m.columns else m
    log("| rank<=15 | %d | %s |" % (len(sub), " | ".join(
        "%+.2f%%" % (sub["r%d" % N].mean() * 100) for N in ns)))
    top = m[m["rank"].fillna(99) <= 3]
    if len(top) > 5:
        log("| rank<=3 | %d | %s |" % (len(top), " | ".join(
            "%+.2f%%" % (top["r%d" % N].mean() * 100) for N in ns)))
    pairs = [(ns[i + 1], ns[i]) for i in range(len(ns) - 1)]
    if 15 in ns and 5 in ns:
        pairs += [(15, 5), (15, 3)]
    if 10 in ns and 5 in ns:
        pairs += [(10, 5)]
    log("")
    log("| 配对（公共窗口） | n | 差(pp/笔) | t |")
    log("|---|---|---|---|")
    for a, b in pairs:
        for name, sub2 in (("rank<=15", sub), ("rank<=3", top)):
            if name == "rank<=3" and len(top) <= 5:
                continue
            d = sub2["r%d" % a] - sub2["r%d" % b]
            log("| N%d-N%d %s | %d | %+.2f | %+.2f%s |" % (
                a, b, name, len(d), d.mean() * 100, t_of(d),
                " *T2*" if abs(t_of(d)) > 2 else ""))
    return m


# ---------------- P3 ----------------
def simulate_variant(sub, en, N, variant):
    o = sub["open"].values.astype(float)
    h = sub["high"].values.astype(float)
    l = sub["low"].values.astype(float)
    c = sub["close"].values.astype(float)
    down = sub["down_limit"].values.astype(float)
    if len(o) == 0 or not (np.isfinite(o[en]) and o[en] > 0):
        return None
    entry = float(o[en])
    line = entry * (1 + STOP)
    buy_i = en - 1
    peak = None
    missing = 0
    for j in range(en, len(o)):
        if not (np.isfinite(o[j]) and o[j] > 0):
            missing += 1
            if missing >= DELIST_DAYS:
                return {"ret": -DELIST_LOSS, "reason": "DELIST", "j": j}
            continue
        missing = 0
        local_peak = peak if (peak and peak > 0) else entry
        if j > en and np.isfinite(h[j]) and h[j] > 0:
            peak = max(local_peak, float(h[j]))
        if np.isfinite(down[j]) and o[j] <= down[j] + 1e-9:
            continue
        if j - buy_i < 2:
            continue
        if j - buy_i >= N + 1:
            return {"ret": o[j] / entry - 1, "reason": "MATURE", "j": j}
        sell = fill = None
        if variant == "open":
            if o[j] / entry - 1 <= STOP:
                sell, fill = "STOP", o[j]
        elif variant == "touch":
            if np.isfinite(l[j]) and l[j] <= line:
                sell, fill = "STOP", (o[j] if o[j] <= line else line)
        elif variant == "close":
            if np.isfinite(c[j]) and c[j] <= line:
                sell, fill = "STOP", c[j]
        if sell is None and variant != "off":
            ret_o = o[j] / entry - 1
            if ret_o >= TP:
                sell, fill = "TP", o[j]
            elif local_peak >= entry * (1 + ACT):
                tl = max(entry, local_peak * (1 - TRAIL_PCT))
                if o[j] <= tl:
                    sell, fill = "TRAIL", o[j]
        if sell:
            return {"ret": fill / entry - 1, "reason": sell, "j": j}
    return None


def exit_diag():
    import paper_forward_exit as pfe
    df, trades, main_last, inc_last = pfe.load_ohlc()
    tset = pd.Index(trades)
    live = pd.read_csv(pfe.LIVE_LOG, encoding="utf-8-sig")
    live = live.drop_duplicates(subset=["date", "code"], keep="last")
    sub = live[live["rank"] <= 3].sort_values(["date", "rank"]).reset_index(drop=True)
    log("\n## P3 止损触发口径诊断（rank≤3，N=10，数据合并最新 %s，候选 %d 笔）"
        % (str(trades[-1])[:10], len(sub)))
    recs = []
    for _, r in sub.iterrows():
        d = pd.Timestamp(r["date"])
        pos = tset.searchsorted(d, side="right")
        if pos >= len(trades):
            continue
        en = pos
        code = r["code"]
        try:
            sdf = df.xs(code, level="ts_code").reindex(trades)
        except KeyError:
            continue
        if not (np.isfinite(sdf["open"].values.astype(float)[en])
                and sdf["open"].values.astype(float)[en] > 0):
            continue
        rec = {"date": str(d)[:10], "code": code}
        for var in ("none", "open", "touch", "close"):
            if var == "none":
                res = simulate_variant(sdf, en, 10, "off")
            else:
                res = simulate_variant(sdf, en, 10, var)
            rec[var + "_ret"] = res["ret"] if res else np.nan
            rec[var + "_reason"] = res["reason"] if res else ""
            rec[var + "_j"] = res["j"] if res else -1
        recs.append(rec)
    m = pd.DataFrame(recs)
    # 校验：open 变体必须复现 paper_forward_exit_live.csv 的 live_trail（同源同口径）
    ref = pd.read_csv(os.path.join(REAL, "paper_forward_exit_live.csv"),
                      encoding="utf-8-sig")[["date", "code", "live_trail_ret"]]
    chk = m.merge(ref, on=["date", "code"], how="left")
    both = chk.dropna(subset=["open_ret", "live_trail_ret"])
    maxdiff = float((both["open_ret"] - both["live_trail_ret"]).abs().max()) if len(both) else float("nan")
    log("> 自检：open 变体 vs paper_forward_exit_live.csv 的 live_trail，同笔最大差 %.2e（n=%d，应≈0）"
        % (maxdiff, len(both)))
    mature = m.dropna(subset=["none_ret", "open_ret", "touch_ret", "close_ret"])
    log("> 可判定样本 %d 笔 / 候选 %d 笔" % (len(mature), len(m)))
    log("")
    log("| 变体 | n | 均值 | 胜率 | STOP笔数 | STOP占比 |")
    log("|---|---|---|---|---|---|")
    for var in ("none", "open", "touch", "close"):
        s = mature[var + "_ret"]
        ns = int((mature[var + "_reason"] == "STOP").sum())
        label = {"none": "无止损(上界)", "open": "open(现行纸面)", "touch": "touch(盘中触线≈实盘)",
                 "close": "close(收盘确认)"}[var]
        log("| %s | %d | %+.3f%% | %.1f%% | %d | %.0f%% |" % (
            label, len(s), s.mean() * 100, (s > 0).mean() * 100, ns, 100.0 * ns / len(s)))
    log("")
    log("| 配对 | 差(pp/笔) | t | n |")
    log("|---|---|---|---|")
    for a, b in (("open", "none"), ("touch", "none"), ("close", "none"),
                 ("close", "open"), ("touch", "open")):
        d = mature[a + "_ret"] - mature[b + "_ret"]
        log("| %s - %s | %+.3f | %+.2f | %d |" % (a, b, d.mean() * 100, t_of(d), len(d)))
    # 噪音扫损画像：open 口径被 STOP 的笔，收盘确认口径下是否仍会止损
    stops = mature[mature["open_reason"] == "STOP"].copy()
    log("")
    log("### STOP 噪音画像（open 口径 %d 笔止损）" % len(stops))
    kept = stops[stops["close_reason"] != "STOP"]
    n_saved = len(kept)
    log("- 收盘确认可救回（当日收盘未破线 / 后续未触发）：**%d/%d 笔（%.0f%%）**" % (
        n_saved, len(stops), 100.0 * n_saved / max(len(stops), 1)))
    if len(kept):
        log("- 这些被误扫的笔若按收盘确认持有到期（none 口径）：均值 **%+.2f%%**（vs 被止损实际 %+.2f%%）" % (
            kept["none_ret"].mean() * 100, kept["open_ret"].mean() * 100))
    log("- 收盘确认相对 open 的逐笔差已见上表（close - open）。")

    # P3b：止损笔的「拿住不动」价值 + 止损后复入前向（B2-② 依据）
    if len(stops):
        d_none = stops["none_ret"] - stops["open_ret"]
        log("")
        log("### 止损笔复入诊断（B2-② 依据，n=%d）" % len(stops))
        log("- 若不止损拿到期（none - open）：均值 **%+.2fpp/笔**（t=%+.2f）—— 止损损耗集中在这些笔上" % (
            d_none.mean() * 100, t_of(d_none)))
        fwd = {"1": [], "3": [], "5": []}
        for _, r in stops.iterrows():
            j = int(r["open_j"])
            try:
                sdf = df.xs(r["code"], level="ts_code").reindex(trades)
            except KeyError:
                continue
            o = sdf["open"].values.astype(float)
            c = sdf["close"].values.astype(float)
            if j + 1 >= len(o) or not np.isfinite(o[j + 1]) or o[j + 1] <= 0:
                continue
            base = o[j + 1]  # 止损次日开盘 = 最早可复入价
            for k in (1, 3, 5):
                jj = j + 1 + k
                if jj < len(c) and np.isfinite(c[jj]) and c[jj] > 0:
                    fwd[str(k)].append(c[jj] / base - 1)
        for k, vals in fwd.items():
            if vals:
                log("- 复入后 T+%s 收盘（vs 止损次日开盘，n=%d）：均值 **%+.2f%%**，胜率 %.0f%%" % (
                    k, len(vals), np.mean(vals) * 100, 100.0 * np.mean([v > 0 for v in vals])))
    return m


def main():
    log("# P16 纸面持有期与止损触发诊断 · 2026-10-08 夜（opencode，只读）")
    log("")
    rets, cal = build_rets()
    log("> 数据源 merged_daily_full.parquet，最新交易日 %s，日历 %d 日" % (cal[-1], len(cal)))
    for tag, (path, ns) in ARMS.items():
        hold_diag(tag, path, ns, rets, cal)
    exit_diag()
    os.makedirs(RESULTS, exist_ok=True)
    out = os.path.join(RESULTS, "P16纸面持有期与止损诊断_20261008.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(OUT_LINES) + "\n")
    print("\n报告:", out)


if __name__ == "__main__":
    main()
