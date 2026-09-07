# -*- coding: utf-8 -*-
"""出场规则前向验证 paper_forward_exit.py —— 只读，不碰生产代码。

背景（T-20260904-002 追盈止损 ablation 的延续）：
exit_ablation 显示 ATR2.0（红线60/N=10）方向稳定为正但逐笔 t=1.18 不显著，
按统计纪律不能据此改实盘出场参数。本脚本把各出场规则挂进 G2 前向验证管道：
对 paper_forward_live.csv 中实盘口径候选（rank<=2，即 g2_config.TOP_N=2 真实交易口径）
逐笔模拟各出场规则，累积真实前向样本，按判定门槛 N>=30 + 逐笔配对 t>2 再拍板。

规则逻辑与 scan_rotate_cost_real.simulate(exec_ok=True) 分支逐行对齐：
  none       : 只靠持有期满（纯 alpha 上界）
  fixed      : -7% 止损 / +15% 止盈 / 期满（回测原口径）
  live_trail : -7% 硬止损 + 峰值回撤 8% 移动止盈 + 15% 止盈（实盘现跑口径）
  atr        : 成本 - k1*ATR%(建仓日快照) 硬止损 + 峰值 - k2*ATR% 移动止盈
  time       : 持有过半仍不盈利即换股
  ma         : 跌破 MA5 或 -7% 止损
对齐要点：峰值用「截至昨日」判定再并入今日 high；一字跌停顺延；T+1 次日可卖；
ATR 用建仓日快照不再重算；MATURE 优先于止损/止盈。

用法：
  python paper_forward_exit.py --top 2 --hold 10     # 默认：实盘口径 rank<=2，N=10
  python paper_forward_exit.py --selfcheck           # 用已回填行核对 none 口径 == backfill ret（精度校验）
  python paper_forward_exit.py --top 10              # 用 Top10 全集更快累积（统计口径不同，勿与 rank<=2 混判）
  python paper_forward_exit.py --rules atr20,atr25   # 指定规则子集
"""
import argparse
import datetime
import math
import os
import sys

import numpy as np
import pandas as pd

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
DATA = os.path.join(PROJ, "data")
REAL = os.path.join(DATA, "real")
LIVE = os.path.join(PROJ, "data_live")
MAIN_DAILY = r"D:\astock\daily\stock_daily.parquet"
LIVE_LOG = os.path.join(REAL, "paper_forward_live.csv")

STOP = -0.07
TP = 0.15
TRAIL_PCT = 0.08
TRAIL_ACTIVATE_PCT = 0.08  # 追盈激活阈值（T-20260907-002）：峰值≥成本×(1+阈值) 才追踪
DELIST_DAYS = 60
DELIST_LOSS = 0.5

RULES = {
    "none": "无止损(纯alpha上界)",
    "fixed": "固定-7%/+15%(回测原口径)",
    "live_trail": "实盘口径:-7%+8%追盈+15%",
    "atr20": "ATR自适应 2.0/2.0",
    "atr25": "ATR自适应 2.0/2.5",
    "atr30": "ATR自适应 3.0/3.0",
    "time": "时间止损(过半不盈换股)",
    "ma5": "跌破MA5 + -7%止损",
}
ATR_K = {"atr20": (2.0, 2.0), "atr25": (2.0, 2.5), "atr30": (3.0, 3.0)}


def _limit_pct(code):
    """A 股涨跌幅限制近似：创业板/科创板 20%，北交所 30%，主板 10%（ST 5% 忽略）。"""
    if code.startswith(("300", "301", "302", "688", "689")):
        return 0.20
    if code.startswith(("4", "8")):
        return 0.30
    return 0.10


def load_ohlc():
    """主库 + data_live 增量合并（增量优先）的 OHLCV + 跌停价面板。

    返回 (df, trades)。df 为 MultiIndex (trade_date, ts_code) 的 open/high/low/close/
    pre_close/down_limit；trades 为合并后的全局交易日历（升序 Timestamp 列表）。
    增量库无 down_limit，用 preClose × (1-limit%) 推算。
    """
    need = ["trade_date", "ts_code", "open", "high", "low", "close", "pre_close", "down_limit"]
    main = pd.read_parquet(MAIN_DAILY, columns=need).reset_index()
    main["trade_date"] = pd.to_datetime(main["trade_date"])
    main["ts_code"] = main["ts_code"].astype(str)
    main = main.set_index(["trade_date", "ts_code"])
    main = main[~main.index.duplicated(keep="last")]

    inc_path = os.path.join(LIVE, "incremental_daily.parquet")
    inc_last = None
    if os.path.isfile(inc_path):
        inc = pd.read_parquet(inc_path)
        need_i = {"trade_date", "ts_code", "open", "high", "low", "close", "preClose"}
        if need_i.issubset(set(inc.columns)):
            inc = inc[["trade_date", "ts_code", "open", "high", "low", "close", "preClose"]].copy()
            inc["trade_date"] = pd.to_datetime(inc["trade_date"])
            inc["ts_code"] = inc["ts_code"].astype(str)
            inc = inc.dropna(subset=["open"])
            inc = inc[inc["open"] > 0]
            inc = inc.set_index(["trade_date", "ts_code"])
            inc = inc[~inc.index.duplicated(keep="last")]
            inc_code = inc.index.get_level_values("ts_code")
            inc["down_limit"] = (inc["preClose"] * (1 - inc_code.map(_limit_pct))).round(2)
            inc = inc.drop(columns=["preClose"])
            inc_last = str(sorted(set(inc.index.get_level_values(0)))[-1])[:10]
            df = pd.concat([main, inc])
            df = df[~df.index.duplicated(keep="last")]  # 增量覆盖主库
            df = df.sort_index()
        else:
            print("  [WARN] 增量库缺列，仅用主库: 缺 %s" % (need_i - set(inc.columns)))
            df = main
    else:
        print("  [WARN] 未找到增量库 %s，仅用主库" % inc_path)
        df = main
    trades = sorted(set(df.index.get_level_values(0)))
    main_last = str(trades[0])[:10] if trades else "NA"
    return df, trades, main_last, inc_last


def _atr_ma_arrays(sub):
    """sub: 按日期升序的 (open/high/low/close) DataFrame（全局日历对齐后）。
    返回 (atr_pct 数组, ma5 数组)，长度与 sub 相同。
    """
    close = sub["close"].values.astype(float)
    high = sub["high"].values.astype(float)
    low = sub["low"].values.astype(float)
    pc = np.full(len(close), np.nan)
    pc[1:] = close[:-1]
    tr = np.maximum.reduce([high - low, np.abs(high - pc), np.abs(low - pc)])
    atr = np.full(len(close), np.nan)
    for i in range(len(close)):
        lo = max(0, i - 13)
        seg = tr[lo:i + 1]
        seg = seg[np.isfinite(seg)]
        if len(seg) >= 7:
            atr[i] = seg.mean()
    atr_pct = atr / close
    ma5 = np.full(len(close), np.nan)
    for i in range(len(close)):
        lo = max(0, i - 4)
        seg = close[lo:i + 1]
        seg = seg[np.isfinite(seg)]
        if len(seg) >= 3:
            ma5[i] = seg.mean()
    return atr_pct, ma5


def simulate_entry(sub, en, N, mode, k1, k2):
    """对单笔入场（信号日 en-1，入场日 en 开盘买入）模拟出场规则。

    sub: 该 code 按全局日历对齐后的 DataFrame（含 open/high/low/close/down_limit，
         无行=该日停牌/无数据，用 None 标记）。
    返回 dict(ret, reason, exit_j, entry) 或 None（入场价缺失 / 数据未到持有期末）。
    """
    o = sub["open"].values.astype(float)
    h = sub["high"].values.astype(float)
    down = sub["down_limit"].values.astype(float)
    if len(o) == 0 or not (np.isfinite(o[en]) and o[en] > 0):
        return None
    entry = float(o[en])
    buy_i = en - 1
    atr_pct = ma5 = None
    if mode in ATR_K:
        atr_pct, _ = _atr_ma_arrays(sub)
        apct_snap = float(atr_pct[en]) if (np.isfinite(atr_pct[en]) and atr_pct[en] > 0) else 0.035
    elif mode == "ma5":
        _, ma5 = _atr_ma_arrays(sub)

    peak = None
    missing = 0
    for j in range(en, len(o)):
        if not (np.isfinite(o[j]) and o[j] > 0):
            missing += 1
            if missing >= DELIST_DAYS:
                return {"ret": -DELIST_LOSS, "reason": "DELIST", "exit_j": j, "entry": entry}
            continue
        missing = 0
        local_peak = peak if (peak and peak > 0) else entry
        # T+1 卫生（T-20260907-002）：入场日(j==en) T+1 锁不可卖，不并入当日 high，避免峰值污染
        if j > en and np.isfinite(h[j]) and h[j] > 0:
            peak = max(local_peak, float(h[j]))
        ret = o[j] / entry - 1
        # 一字跌停卖不掉：顺延（与回测一致）
        if np.isfinite(down[j]) and o[j] <= down[j] + 1e-9:
            continue
        if j - buy_i < 2:          # T+1：入场日与次日不可卖
            continue
        if j - buy_i >= N + 1:     # 持有期满优先
            return {"ret": ret, "reason": "MATURE", "exit_j": j, "entry": entry}
        sell = None
        if mode == "none":
            continue
        elif mode == "time":
            if (j - buy_i) >= max(2, int(round(N / 2.0))) and ret <= 0:
                sell = "TIME"
        elif mode in ATR_K:
            if ret <= -k1 * apct_snap:
                sell = "ATR_STOP"
            elif local_peak > entry and o[j] <= local_peak * (1 - k2 * apct_snap):
                sell = "ATR_TRAIL"
        elif mode == "ma5":
            if np.isfinite(ma5[j - 1]) and o[j] < ma5[j - 1]:
                sell = "MA5_BREAK"
            elif ret <= STOP:
                sell = "STOP"
        elif mode == "live_trail":
            # 追盈激活阈值 + 保本底线（T-20260907-002）：峰值≥成本×(1+8%)才追踪；
            # 触发线=max(成本, peak×0.92)，激活后永不亏损出局（防"追盈=追跌"）
            if ret <= STOP:
                sell = "STOP"
            elif ret >= TP:
                sell = "TP"
            elif local_peak >= entry * (1 + TRAIL_ACTIVATE_PCT):
                trail_line = max(entry, local_peak * (1 - TRAIL_PCT))
                if o[j] <= trail_line:
                    sell = "TRAIL"
        else:  # fixed
            if ret <= STOP or ret >= TP:
                sell = "STOP" if ret <= STOP else "TP"
        if sell:
            return {"ret": ret, "reason": sell, "exit_j": j, "entry": entry}
    return None  # 数据未到持有期末


def run(rules, top, hold):
    df, trades, main_last, inc_last = load_ohlc()
    last_trade = str(trades[-1])[:10]
    gap = (pd.Timestamp.today().normalize() - pd.Timestamp(last_trade)).days
    stale = gap > 5

    live = pd.read_csv(LIVE_LOG, encoding="utf-8-sig")
    live = live.drop_duplicates(subset=["date", "code"], keep="last").reset_index(drop=True)
    if "rank" not in live.columns:
        live["rank"] = np.nan
    if top == 2:
        sub = live[live["rank"] <= 2]
    else:
        sub = live.copy()
    sub = sub.sort_values(["date", "rank"]).reset_index(drop=True)
    print("[前向出场规则验证] 候选 %d 笔（rank<=%d 实盘口径）| 数据 主库%s 增量%s 合并最新 %s"
          % (len(sub), top, main_last, inc_last or "NA", last_trade))

    tset = pd.Index(trades)
    rows = []
    n_nodata = 0
    for _, r in sub.iterrows():
        d = pd.Timestamp(r["date"])
        pos = tset.searchsorted(d, side="right")
        if pos >= len(trades):
            continue
        en = pos  # 入场日 = 信号日之后第一个交易日
        code = r["code"]
        sdf = df.xs(code, level="ts_code").reindex(trades)  # 对齐全局日历；缺失行=停牌/无数据
        if not (np.isfinite(sdf["open"].values.astype(float)[en]) and sdf["open"].values.astype(float)[en] > 0):
            n_nodata += 1
            continue
        rec = {"date": str(d)[:10], "code": code, "rank": int(r["rank"]) if not pd.isna(r["rank"]) else np.nan,
               "entry_day": str(trades[en])[:10], "entry": float(sdf["open"].values.astype(float)[en])}
        for mode in rules:
            res = simulate_entry(sdf, en, hold, mode, *ATR_K.get(mode, (0.0, 0.0)))
            if res is None:
                rec[mode + "_ret"] = np.nan
                rec[mode + "_exit"] = np.nan
                rec[mode + "_reason"] = ""
                rec[mode + "_hold"] = np.nan
            else:
                rec[mode + "_ret"] = res["ret"]
                rec[mode + "_exit"] = str(trades[res["exit_j"]])[:10] if res["exit_j"] < len(trades) else "NA"
                rec[mode + "_reason"] = res["reason"]
                rec[mode + "_hold"] = res["exit_j"] - en
        rows.append(rec)
    res = pd.DataFrame(rows)
    if len(res) == 0:
        print("  无任何可模拟候选（无入场价）")
        return 0 if not stale else 2

    # ---------- 统计 ----------
    out_csv = os.path.join(REAL, "paper_forward_exit_live.csv")
    res.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print("  明细已写:", out_csv)

    mature = res.dropna(subset=[mode + "_ret" for mode in rules])
    print("\n已到期（可判定）%d 笔 / 未到期 %d 笔 / 无入场价 %d 笔 / 候选累计 %d 笔"
          % (len(mature), len(res) - len(mature), n_nodata, len(sub)))
    if len(mature) == 0:
        print("  [提示] 尚无到期样本，先跑 paper_forward.py --backfill 或等数据累积")
        return 0 if not stale else 2

    print("\n=== 逐规则汇总（%d 笔到期，同入场逐笔对比）===" % len(mature))
    for mode in rules:
        col, hcol = mode + "_ret", mode + "_hold"
        print("  %-24s n=%d 均值%+.3f%% 胜率%.1f%% 均持有%.1f日" % (
            RULES[mode], int(mature[col].notna().sum()), mature[col].mean() * 100,
            (mature[col] > 0).mean() * 100, mature[hcol].mean()))
    # 逐笔配对
    print("\n=== 逐笔配对差（vs fixed）===")
    for mode in rules:
        if mode == "fixed":
            continue
        d = mature[mode + "_ret"] - mature["fixed_ret"]
        d = d.dropna()
        if len(d) < 10 or d.std() == 0:
            t = float("nan")
        else:
            t = d.mean() / (d.std(ddof=1) / math.sqrt(len(d)))
        print("  %-24s 差%+.3fpp/笔 t=%s (n=%d)" % (RULES[mode], d.mean() * 100,
                                                   ("%+.2f" % t) if not np.isnan(t) else "NA", len(d)))
    # 决策相关：atr20 vs live_trail
    if "atr20" in rules and "live_trail" in rules:
        d = mature["atr20_ret"] - mature["live_trail_ret"]
        d = d.dropna()
        if len(d) >= 10 and d.std() > 0:
            t = d.mean() / (d.std(ddof=1) / math.sqrt(len(d)))
            print("  [决策] ATR2.0 vs 实盘现跑 live_trail：差%+.3fpp/笔 t=%+.2f (n=%d)"
                  % (d.mean() * 100, t, len(d)))

    # ---------- Markdown 报告 ----------
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    md = [f"# P16 出场规则前向验证（paper_forward_exit）· {now}", "",
          f"> 候选源：paper_forward_live.csv（G2 红线60，rank<=2 实盘口径，持仓2）| 持有 N={hold} | open→open",
          f"> 数据源：主库最新 {main_last} | 增量最新 {inc_last or 'NA'} | 合并最新 {last_trade}"
          + (" | **行情停更告警**" if stale else ""),
          f"> 规则逻辑与 scan_rotate_cost_real.simulate(exec_ok) 分支对齐：峰值用昨日/持有日高点、一字跌停顺延、T+1、ATR 建仓日快照", "",
          f"**已到期 {len(mature)} 笔 / 未到期 {len(res)-len(mature)} 笔 / 无入场价 {n_nodata} 笔 / 候选累计 {len(sub)} 笔**", "",
          "| 规则 | n | 均值 | 胜率 |",
          "|---|---|---|---|"]
    for mode in rules:
        col = mode + "_ret"
        md.append("| %s | %d | %+.3f%% | %.1f%% |" % (
            RULES[mode], int(mature[col].notna().sum()), mature[col].mean() * 100,
            (mature[col] > 0).mean() * 100))
    md += ["", "## 逐笔配对差（vs 回测基线 fixed）", "",
           "| 规则 | 差(pp/笔) | t | n |", "|---|---|---|---|"]
    for mode in rules:
        if mode == "fixed":
            continue
        d = mature[mode + "_ret"] - mature["fixed_ret"]
        d = d.dropna()
        t = d.mean() / (d.std(ddof=1) / math.sqrt(len(d))) if (len(d) >= 10 and d.std() > 0) else np.nan
        md.append("| %s | %+.3f | %s | %d |" % (RULES[mode], d.mean() * 100,
                                                ("%.2f" % t) if not np.isnan(t) else "NA", len(d)))
    if "atr20" in rules and "live_trail" in rules:
        d = mature["atr20_ret"] - mature["live_trail_ret"]
        d = d.dropna()
        t = d.mean() / (d.std(ddof=1) / math.sqrt(len(d))) if (len(d) >= 10 and d.std() > 0) else np.nan
        md += ["", "## 决策对照：ATR2.0 vs 实盘现跑 live_trail", "",
               "- 差 %+.3fpp/笔 | 逐笔配对 t=%s（n=%d）" % (d.mean() * 100,
                                                          ("%.2f" % t) if not np.isnan(t) else "NA", len(d)), ""]
    N_MIN = 30
    md += ["", "## 判定", ""]
    if len(mature) < N_MIN:
        md.append(f"⚠️ 有效样本 {len(mature)} 笔 < 判定门槛 {N_MIN} 笔，**不作任何结论**。"
                  f"进度 {len(mature)}/{N_MIN}（约 2 笔/交易日，按 rank<=2 需再约 {math.ceil((N_MIN-len(mature))/2)} 个交易日）。")
    else:
        if "atr20" in rules and "live_trail" in rules:
            d = mature["atr20_ret"] - mature["live_trail_ret"]
            d = d.dropna()
            t = d.mean() / (d.std(ddof=1) / math.sqrt(len(d))) if (len(d) >= 10 and d.std() > 0) else np.nan
            if not np.isnan(t) and d.mean() > 0 and t > 2:
                md.append(f"✅ 样本 {len(mature)} 笔达标：ATR2.0 相对 live_trail 差 {d.mean()*100:+.3f}pp/笔、"
                          f"t={t:.2f}>2 → **可进入实盘试跑/落盘拍板**。")
            else:
                md.append(f"样本 {len(mature)} 笔达标但 ATR2.0 相对 live_trail 未过线（差 "
                          f"{d.mean()*100:+.3f}pp/笔、t={t if not np.isnan(t) else 0:.2f} ≤ 2）→ **维持现规则，继续观察**。")
        else:
            md.append(f"样本 {len(mature)} 笔达标，请人工查看逐笔配对差判定。")
    out = os.path.join(REAL, "paper_forward_exit_%s.md" % now[:10])
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("\n报告:", out)
    if stale:
        print("  [ALERT] 行情数据源停更：最新交易日 %s 距今 %d 天" % (last_trade, gap))
        return 2
    return 0


def selfcheck():
    """用已回填行核对 none 规则（纯期满 N=10 open→open）必须精确复现 backfill 的 ret。"""
    df, trades, main_last, inc_last = load_ohlc()
    live = pd.read_csv(LIVE_LOG, encoding="utf-8-sig")
    done = live.dropna(subset=["ret"]).reset_index(drop=True)
    if len(done) == 0:
        print("[selfcheck] live 无已回填行")
        return 0
    tset = pd.Index(trades)
    n_ok = n_bad = 0
    for _, r in done.iterrows():
        d = pd.Timestamp(r["date"])
        pos = tset.searchsorted(d, side="right")
        if pos >= len(trades):
            continue
        en = pos
        code = r["code"]
        sdf = df.xs(code, level="ts_code").reindex(trades)
        res = simulate_entry(sdf, en, int(r.get("hold") or 10), "none", 0.0, 0.0)
        if res is None:
            print("  [MISS] %s %s 无法模拟" % (r["date"], code))
            continue
        diff = abs(res["ret"] - r["ret"])
        ok = diff < 1e-9
        n_ok += ok
        n_bad += (not ok)
        print("  %s %-10s 模拟%+.4f%% backfill%+.4f%% 差%.2e %s"
              % (r["date"], code, res["ret"] * 100, r["ret"] * 100, diff, "OK" if ok else "!! 不一致"))
    print("[selfcheck] %d/%d 一致" % (n_ok, n_ok + n_bad))
    return 0 if n_bad == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=2, help="rank<=N 的候选口径（2=实盘口径）")
    ap.add_argument("--hold", type=int, default=10)
    ap.add_argument("--rules", default="none,fixed,live_trail,atr20",
                    help="逗号分隔，可用 atr25/atr30/time/ma5")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    if args.selfcheck:
        sys.exit(selfcheck())
    rules = [r for r in args.rules.split(",") if r in RULES]
    if not rules:
        print("无有效规则，可选:", ",".join(RULES))
        sys.exit(1)
    sys.exit(run(rules, args.top, args.hold))


if __name__ == "__main__":
    main()
