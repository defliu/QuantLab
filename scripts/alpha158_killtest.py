# coding=utf-8
"""Alpha158 日内 K 线结构族 × 大盘域 整族 Kill Test

任务编号: T-20260903-001
缘起: 外部资料审计「头条·150因子跑上证180」(`外部策略资料审计_150因子上证180_20260903.md`)
     裁定「直接归档不复现」后，发现该族在大盘域属真空白，故做整族 kill test。
     **本任务是整族检验，不是复现该文 5 因子。**

设计要点（预注册，先定后跑）:
  1. 无前视: 因子用 t 日及以前 OHLCV 计算; 收益从 t+1 开盘开始计
  2. 域(PIT): 按当日 circ_mv 排序, top180(模拟上证180) / top500(大盘域, 对齐 A5) / bot30(小盘对照)
  3. 频率: D / W / 2W / M 全扫（价格类因子衰减快, 只跑月频等于人为证伪, 不公平）
  4. 成本: 双边 0.30%（= 2×(滑点0.001+佣金0.00025) + 印花0.0005，与 A5 同口径）
  5. 多重检验: 报告全族 15 因子完整分布, 并给 Bonferroni 校正后 t 阈值, 不搞 Top-K 选择
  6. 方向: 只报原始因子 rankIC（IC>0 表示买高好, IC<0 表示买低好）, 再对照文章声称方向

判定线（沿用本仓既有口径）:
  IC均值 ≥ 0.03  AND  ICIR ≥ 0.3  AND  五分位单调(Q5>Q3>Q1)  AND  Bonferroni t 显著
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\QuantLab")

DAILY_PATH = r"D:/astock/daily/stock_daily.parquet"
OUT_DIR = r"D:\QuantLab\reports"
RESULT_JSON = os.path.join(OUT_DIR, "alpha158_killtest_results.json")
REPORT_MD = os.path.join(OUT_DIR, "Alpha158_大盘域KillTest_20260903.md")

# ---- 预注册参数 ----
START = "2015-01-01"       # 回测起点
WARM_START = "2014-07-01"  # 数据加载起点(预留 60 日滚动窗口预热)
END = "2026-08-21"         # 数据末端(实测)
MIN_N = 30                 # 每期最少有效样本
COST_ONEWAY = 0.00125      # 单边成本 = 滑点0.001 + 佣金0.00025
COST_TAX = 0.0005          # 印花税(卖出单边)
COST_DOUBLE = 2 * COST_ONEWAY + COST_TAX  # = 0.0030

DOMAINS = {
    "top180": "大盘域(PIT circ_mv前180, 模拟上证180)",
    "top500": "大盘域(PIT circ_mv前500, 对齐A5)",
    "bot30": "小盘域(后30%, 对照/锚检查)",
}
FREQS = {"D": "日频", "W": "周频", "2W": "双周", "M": "月频"}

_log = []


def log(*args):
    s = " ".join(str(a) for a in args)
    print(s, flush=True)
    _log.append(s)


# ---------------------------------------------------------------- 数据加载
def load_data():
    """加载 OHLCV + circ_mv + adj_factor, 做后复权, 返回 wide DataFrame (float32)"""
    t0 = time.time()
    need = ["trade_date", "ts_code", "open", "high", "low", "close",
            "vol", "circ_mv", "adj_factor"]
    df = pd.read_parquet(DAILY_PATH, columns=need).reset_index()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df[(df["trade_date"] >= WARM_START) & (df["trade_date"] <= END)]
    df = df.sort_values(["trade_date", "ts_code"])
    log("loaded rows=%d  codes=%d  %.1fs" % (
        len(df), df["ts_code"].nunique(), time.time() - t0))

    t1 = time.time()
    codes = sorted(df["ts_code"].unique())
    dates = pd.DatetimeIndex(sorted(df["trade_date"].unique()))

    def to_wide(col):
        w = df.pivot(index="trade_date", columns="ts_code", values=col)
        return w.reindex(index=dates, columns=codes)

    adj = to_wide("adj_factor").ffill().fillna(1.0)
    log("adj_factor: 非1占比=%.3f  min=%.4f max=%.2f" % (
        float((adj != 1.0).mean().mean()), float(adj.min().min()), float(adj.max().max())))

    # 后复权: hfq_price = price * adj_factor
    o = (to_wide("open") * adj).astype(np.float32)
    h = (to_wide("high") * adj).astype(np.float32)
    l = (to_wide("low") * adj).astype(np.float32)
    c = (to_wide("close") * adj).astype(np.float32)
    v = to_wide("vol").astype(np.float32)
    mv = to_wide("circ_mv").astype(np.float32)
    log("wide panels built %.1fs  shape=%s" % (time.time() - t1, str(c.shape)))
    return dates, codes, o, h, l, c, v, mv


# ---------------------------------------------------------------- PIT 宇宙
def build_universe_mask(dates, codes, mv, rebal_dates):
    """PIT 宇宙: 月度快照按当日 circ_mv 排序, 逐调仓日 forward-fill。

    返回 {domain_key: np.ndarray bool (T_rebal, N)}
    """
    code_pos = {cd: i for i, cd in enumerate(codes)}
    # 月度快照日
    s = pd.Series(range(len(dates)), index=dates)
    snap_pos = list(s.groupby(s.index.to_period("M")).first().values)
    snap_dates = [dates[i] for i in snap_pos]

    snaps = {k: [] for k in DOMAINS}
    for i in snap_pos:
        row = mv.values[i]
        ok = ~np.isnan(row) & (row > 0)
        idx = np.where(ok)[0]
        order = idx[np.argsort(-row[idx])]
        n = len(order)
        snaps["top180"].append(order[:180].copy())
        snaps["top500"].append(order[:500].copy())
        snaps["bot30"].append(order[int(n * 0.7):].copy())
    log("universe snapshots: %d months (top180/top500/bot30)" % len(snap_dates))

    # 每个调仓日映射到最近的历史快照（PIT, 不用未来快照）
    snap_arr = np.array([np.datetime64(d) for d in snap_dates])
    reb_arr = np.array([np.datetime64(d) for d in rebal_dates])
    si = np.searchsorted(snap_arr, reb_arr, side="right") - 1
    si[si < 0] = 0
    # 安全网: 若快照日晚于调仓日(不应发生), 前移一个
    for t in range(len(si)):
        while si[t] > 0 and snap_arr[si[t]] > reb_arr[t]:
            si[t] -= 1

    masks = {}
    T = len(rebal_dates)
    N = len(codes)
    for k in DOMAINS:
        m = np.zeros((T, N), dtype=bool)
        for t in range(T):
            m[t, snaps[k][si[t]]] = True
        masks[k] = m
    return masks


# ---------------------------------------------------------------- 因子族
def build_factors(o, h, l, c, v):
    """Alpha158 日内 K 线结构族 + 文章点名因子, 全 wide (只用 t 日及以前数据)"""
    F = {}
    t0 = time.time()
    prev_close = c.shift(1)
    min_oc = np.minimum(o, c)
    max_oc = np.maximum(o, c)
    rng = (h - l).replace(0, np.nan)

    F["KMID"] = (c - o) / o                       # K线实体/开盘
    F["KLEN"] = (h - l) / o                       # K线全长/开盘
    F["KLOW"] = (min_oc - l) / o                  # 下影线(未归一化)
    F["KLOW2"] = (min_oc - l) / rng               # 下影线占比
    F["KHIGH"] = (h - max_oc) / o                 # 上影线
    F["KSFT"] = (2 * c - h - l) / o               # 收盘在K线中的偏移
    F["KSFT2"] = (2 * c - h - l) / rng
    F["OPEN0"] = o / prev_close - 1.0             # 隔夜跳空
    F["HIGH0"] = h / c - 1.0                      # 最高价相对收盘
    F["LOW0"] = l / c - 1.0                       # 最低价相对收盘
    F["STD5"] = c.pct_change().rolling(5).std()   # 5日波动(低波=取负)
    F["ROC5"] = c / c.shift(5) - 1.0              # 5日动量
    log("  fast factors done %.1fs" % (time.time() - t0))

    t1 = time.time()
    lv = np.log(v.replace(0, np.nan))
    F["CORR5"] = c.rolling(5).corr(lv)            # 价量相关性(Alpha158)
    log("  CORR5 done %.1fs" % (time.time() - t1))

    t2 = time.time()
    F["VSTD60"] = v.rolling(60).std() / v.rolling(60).mean()   # 量波动率(CV)
    F["VMA20"] = v / v.rolling(20).mean()                      # 量能相对强弱
    log("  volume factors done %.1fs" % (time.time() - t2))
    return F


# ---------------------------------------------------------------- 调仓日历
def make_rebal_dates(dates, freq):
    if freq == "D":
        return list(dates)
    s = pd.Series(range(len(dates)), index=dates)
    if freq == "W":
        pos = list(s.groupby(s.index.to_period("W")).last().values)
    elif freq == "2W":
        wk = list(s.groupby(s.index.to_period("W")).last().values)
        pos = wk[1::2]
    elif freq == "M":
        pos = list(s.groupby(s.index.to_period("M")).last().values)
    else:
        raise ValueError(freq)
    return [dates[i] for i in pos]


# ---------------------------------------------------------------- 核心评估
def eval_factor(fac, o, h, l, c, pos_buy, pos_sell, dates_idx, mask,
                rebal_dates, cost_double):
    """单域单频率评估一个因子。返回 dict。

    fac  : wide DataFrame (全交易日 × codes)
    收益 : open[pos_buy] -> open[pos_sell]  (t+1 开盘买, 下期 t+1 开盘卖)
    """
    cols = fac.columns
    t_idx = np.array(dates_idx)                      # 调仓日在 dates 中的位置
    f_rows = fac.values[t_idx]                       # (T, N)
    o_buy = o.values[pos_buy]
    o_sell = o.values[pos_sell]
    h_buy = h.values[pos_buy]
    l_buy = l.values[pos_buy]
    c_sig = c.values[t_idx]                          # 信号日收盘(算涨停基准)

    # 一字涨停过滤: 开盘=最高=最低 且 涨幅>9.8% -> 买不进
    with np.errstate(divide="ignore", invalid="ignore"):
        gap = o_buy / c_sig - 1.0
    limit_up = (o_buy == h_buy) & (h_buy == l_buy) & (gap > 0.098)
    limit_dn = (o_buy == h_buy) & (h_buy == l_buy) & (gap < -0.098)

    with np.errstate(divide="ignore", invalid="ignore"):
        ret = o_sell / o_buy - 1.0
    ret = np.where(np.isfinite(ret), ret, np.nan)
    ret[limit_up | limit_dn] = np.nan                # 一字板不可交易
    ret = np.where(mask, ret, np.nan)
    fv = np.where(mask, f_rows, np.nan)

    T, N = fv.shape
    idx = pd.DatetimeIndex(rebal_dates[:T])

    # --- rank IC (逐行 Spearman) ---
    fr = pd.DataFrame(fv, index=idx, columns=cols).rank(axis=1)
    rr = pd.DataFrame(ret, index=idx, columns=cols).rank(axis=1)
    ic = fr.corrwith(rr, axis=1)
    ic = ic.dropna()

    # --- 五分位 ---
    qrank = fr.rank(axis=1, pct=True)
    qret = {}
    qmask = {}
    for qi in range(1, 6):
        lo, hi = (qi - 1) / 5.0, qi / 5.0
        m = ((qrank > lo) & (qrank <= hi)).values
        sub = np.where(m, ret, np.nan)
        with np.errstate(invalid="ignore"):
            qret[qi] = pd.Series(np.nanmean(sub, axis=1), index=idx)
        qmask[qi] = m
    ls_gross = (qret[5] - qret[1]).dropna()

    # --- 换手率(两侧平均) ---
    def _turn(m):
        a = m[1:].astype(np.int8)
        b = m[:-1].astype(np.int8)
        inter = (a & b).sum(axis=1)
        cnt = a.sum(axis=1)
        return 1.0 - np.where(cnt > 0, inter / np.maximum(cnt, 1), np.nan)

    tq5 = _turn(qmask[5])
    tq1 = _turn(qmask[1])
    turn = pd.Series((np.nan_to_num(tq5) + np.nan_to_num(tq1)) / 2.0,
                     index=idx[1:])
    turn_mean = float(np.nanmean(turn)) if len(turn) else np.nan

    # 净多空 = 毛多空 - 换手×双边成本
    cost_ser = (turn.reindex(ls_gross.index).fillna(0.0)) * cost_double
    ls_net = (ls_gross - cost_ser).dropna()

    # 单调性
    qavg = {qi: float(np.nanmean(qret[qi])) for qi in range(1, 6)}

    # 多空净值(净)
    nav = float(np.prod(1.0 + ls_net.values)) if len(ls_net) else np.nan

    return {
        "ic": ic,
        "ic_mean": float(ic.mean()) if len(ic) else np.nan,
        "ic_std": float(ic.std()) if len(ic) else np.nan,
        "icir": float(ic.mean() / ic.std()) if len(ic) and ic.std() > 0 else np.nan,
        "ic_pos_pct": float((ic > 0).mean()) if len(ic) else np.nan,
        "n_periods": int(len(ic)),
        "qavg": qavg,
        "monotone": bool(qavg[5] > qavg[3] > qavg[1]),
        "ls_gross": float(ls_gross.mean()) if len(ls_gross) else np.nan,
        "ls_net": float(ls_net.mean()) if len(ls_net) else np.nan,
        "turnover": turn_mean,
        "nav_net": nav,
        "ls_series": ls_net,
        "ic_series": ic,
    }


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="冒烟测试: 单因子单域单频率")
    ap.add_argument("--freqs", default="D,W,2W,M")
    ap.add_argument("--domains", default="top180,top500,bot30")
    args = ap.parse_args()

    t_start = time.time()
    log("======== Alpha158 日内K线结构族 × 大盘域 整族 Kill Test (T-20260903-001) ========")
    log("start: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    log("成本口径: 双边 %.4f (= 2×(滑点0.001+佣金0.00025) + 印花0.0005), 与 A5 同口径" % COST_DOUBLE)

    dates, codes, o, h, l, c, v, mv = load_data()
    log("区间 %s ~ %s  交易日 %d  股票 %d" % (
        dates[0].date(), dates[-1].date(), len(dates), len(codes)))

    F = build_factors(o, h, l, c, v)
    log("因子数: %d -> %s" % (len(F), ",".join(F.keys())))

    freqs = args.freqs.split(",")
    domains = args.domains.split(",")
    if args.quick:
        freqs = ["W"]
        domains = ["top500"]
        F = {k: F[k] for k in ["KMID", "STD5"]}

    results = {}
    for freq in freqs:
        rebal_dates = make_rebal_dates(dates, freq)
        # 只保留 START 之后的调仓日
        rebal_dates = [d for d in rebal_dates if d >= pd.Timestamp(START)]
        if len(rebal_dates) < 20:
            log("freq=%s 调仓日不足20, 跳过" % freq)
            continue
        pos = np.array([dates.get_loc(d) for d in rebal_dates])
        # 买入/卖出: T+1 开盘
        pos_buy = pos + 1
        pos_sell = pos + 1
        pos_sell = np.roll(pos_sell, -1)[:-1]   # 下期调仓日的 T+1
        # 对齐: 丢掉最后一期
        pos_buy = pos_buy[:-1]
        rebal_valid = rebal_dates[:-1]
        pos_sig = pos[:-1]
        if pos_sell[-1] >= len(dates):
            pos_buy = pos_buy[:-1]
            pos_sell = pos_sell[:-1]
            rebal_valid = rebal_valid[:-1]
            pos_sig = pos_sig[:-1]

        masks = build_universe_mask(dates, codes, mv, rebal_valid)
        log("freq=%s 调仓 %d 期, 域掩码构建完成" % (freq, len(rebal_valid)))

        for dom in domains:
            mask = masks[dom]
            for fname, fac in F.items():
                t1 = time.time()
                r = eval_factor(fac, o, h, l, c, pos_buy, pos_sell,
                                pos_sig, mask, rebal_valid, COST_DOUBLE)
                key = "%s|%s|%s" % (fname, dom, freq)
                results[key] = {
                    "factor": fname, "domain": dom, "freq": freq,
                    "ic_mean": r["ic_mean"], "ic_std": r["ic_std"],
                    "icir": r["icir"], "ic_pos_pct": r["ic_pos_pct"],
                    "n": r["n_periods"], "qavg": r["qavg"],
                    "monotone": r["monotone"], "ls_gross": r["ls_gross"],
                    "ls_net": r["ls_net"], "turnover": r["turnover"],
                    "nav_net": r["nav_net"],
                }
                results[key]["_ic_series"] = r["ic_series"]
                log("  %-22s IC=%+.4f ICIR=%+.3f 正%.0f%% n=%d 多空净=%+.4f%% 换手=%.2f 单调=%s (%.0fs)" % (
                    key, r["ic_mean"], r["icir"], r["ic_pos_pct"] * 100,
                    r["n_periods"], (r["ls_net"] or 0) * 100,
                    r["turnover"] if r["turnover"] == r["turnover"] else -1,
                    "Y" if r["monotone"] else "N", time.time() - t1))

    # ---------------- 多重检验 ----------------
    n_factors = len(F)
    alpha = 0.05 / max(n_factors, 1)
    # 双尾正态临界值(近似)
    from statistics import NormalDist
    z_bonf = NormalDist().inv_cdf(1 - alpha / 2)
    log("\n多重检验: 因子数=%d, Bonferroni α=%.5f, 双尾 z 临界=%.3f" % (
        n_factors, alpha, z_bonf))

    for k, r in results.items():
        icir = r["icir"]
        n = r["n"]
        tstat = icir * np.sqrt(n) if (icir == icir and n) else np.nan
        r["t_stat"] = float(tstat) if tstat == tstat else None
        r["t_bonf_pass"] = bool(tstat == tstat and abs(tstat) >= z_bonf)
        r["pass_all"] = bool(
            r["ic_mean"] >= 0.03 and abs(r["icir"]) >= 0.3
            and r["monotone"] and r["t_bonf_pass"])

    # ---------------- 落盘 JSON ----------------
    os.makedirs(OUT_DIR, exist_ok=True)
    jr = {}
    for k, r in results.items():
        jr[k] = {kk: (None if (isinstance(vv, float) and vv != vv) else vv)
                 for kk, vv in r.items() if kk != "_ic_series"}
    meta = {
        "task": "T-20260903-001",
        "run_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "start": START, "end": str(dates[-1].date()),
        "cost_double": COST_DOUBLE,
        "n_factors": n_factors, "factors": list(F.keys()),
        "bonferroni_alpha": alpha, "z_bonf": z_bonf,
        "freqs": freqs, "domains": domains,
    }
    with open(RESULT_JSON, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "results": jr}, f, ensure_ascii=False, indent=2)
    log("JSON: %s" % RESULT_JSON)

    # ---------------- 报告 ----------------
    lines = gen_report(results, meta, F, z_bonf, alpha, dates)
    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log("报告: %s" % REPORT_MD)
    log("\n总用时 %.0fs" % (time.time() - t_start))


def gen_report(results, meta, F, z_bonf, alpha, dates):
    L = []
    A = L.append
    A("# Alpha158 日内 K 线结构族 × 大盘域 整族 Kill Test\n")
    A("> 任务 **T-20260903-001** ｜ 运行 %s ｜ 脚本 `scripts/alpha158_killtest.py`" % meta["run_time"])
    A(">")
    A("> **缘起**：外部资料审计「头条·150因子跑上证180」裁定「直接归档」后，发现该族在大盘域属真空白。")
    A("> **本任务是整族检验，不是复现该文 5 因子。**\n")
    A("## 〇、预注册规格（先定后跑）\n")
    A("| 项 | 设定 |")
    A("|---|---|")
    A("| 区间 | %s ~ %s（含 2015 牛熊 / 2018 熊 / 2021 核心资产牛 / 2022-24 熊） |" % (meta["start"], meta["end"]))
    A("| 域(PIT) | top180(模拟上证180) / top500(大盘域,对齐A5) / bot30(小盘对照)，按月 circ_mv 逐期截断 |")
    A("| 频率 | 日/周/双周/月 全扫 —— 价格类因子衰减快，只跑月频等于人为证伪 |")
    A("| 前视控制 | 因子用 t 日及以前 OHLCV；收益从 **t+1 开盘** 起算 |")
    A("| 不可交易过滤 | 买入日一字涨停/跌停剔除 |")
    A("| 成本 | 双边 **%.4f** = 2×(滑点0.001+佣金0.00025) + 印花0.0005（与 A5 同口径） |" % meta["cost_double"])
    A("| 复权 | 后复权（price × adj_factor） |")
    A("| 判定线 | IC均值≥0.03 且 \\|ICIR\\|≥0.3 且 五分位单调(Q5>Q3>Q1) 且 Bonferroni t 显著 |")
    A("| 多重检验 | 因子数 %d，Bonferroni α=%.5f，双尾 z 临界 **%.3f** |" % (
        meta["n_factors"], alpha, z_bonf))
    A("")
    A("## 一、全族 rankIC 汇总（主口径：周频 top500）\n")
    A("> IC>0 表示「买因子值高的」更好；IC<0 表示「买低的」更好。\n")
    A("| 因子 | 含义 | IC均值 | ICIR | t值 | 正值% | 单调 | 多空(毛) | 多空(净) | 换手 | 判定 |")
    A("|---|---|---|---|---|---|---|---|---|---|---|")
    rows = [(k, v) for k, v in results.items() if v["freq"] == "W" and v["domain"] == "top500"]
    rows.sort(key=lambda x: -(x[1]["ic_mean"] if x[1]["ic_mean"] == x[1]["ic_mean"] else -9))
    DESC = {
        "KMID": "K线实体/开盘", "KLEN": "K线全长/开盘", "KLOW": "下影线(未归一)",
        "KLOW2": "下影线占比", "KHIGH": "上影线", "KSFT": "收盘在K线中偏移",
        "KSFT2": "偏移(归一化)", "OPEN0": "隔夜跳空", "HIGH0": "最高价/收盘-1",
        "LOW0": "最低价/收盘-1", "STD5": "5日波动率", "ROC5": "5日动量",
        "CORR5": "5日价量相关", "VSTD60": "60日量波动CV", "VMA20": "量能/20日均量",
    }
    for k, v in rows:
        f = v["factor"]
        t = v.get("t_stat")
        A("| **%s** | %s | %+.4f | %+.3f | %s | %.0f%% | %s | %+.3f%% | %+.3f%% | %.2f | %s |" % (
            f, DESC.get(f, ""), v["ic_mean"], v["icir"],
            ("%+.2f" % t) if t is not None else "—",
            (v["ic_pos_pct"] or 0) * 100, "Y" if v["monotone"] else "N",
            (v["ls_gross"] or 0) * 100, (v["ls_net"] or 0) * 100,
            v["turnover"] if v["turnover"] == v["turnover"] else -1,
            "**PASS**" if v["pass_all"] else "FAIL"))
    A("")
    A("## 二、频率敏感性（top500，各因子 ICIR）\n")
    A("| 因子 | 日频IC | 周频IC | 双周IC | 月频IC | 日频ICIR | 周频ICIR | 月频ICIR |")
    A("|---|---|---|---|---|---|---|---|")
    for f in F.keys():
        cells = []
        for fr in ["D", "W", "2W", "M"]:
            v = results.get("%s|top500|%s" % (f, fr))
            cells.append(("%+.4f" % v["ic_mean"]) if v else "—")
        for fr in ["D", "W", "M"]:
            v = results.get("%s|top500|%s" % (f, fr))
            cells.append(("%+.3f" % v["icir"]) if v else "—")
        A("| %s | %s |" % (f, " | ".join(cells)))
    A("")
    A("## 三、域对比（周频，各因子 IC）\n")
    A("| 因子 | top180(上证180模拟) | top500(大盘域) | bot30(小盘对照) |")
    A("|---|---|---|---|")
    for f in F.keys():
        cells = []
        for dom in ["top180", "top500", "bot30"]:
            v = results.get("%s|%s|W" % (f, dom))
            cells.append(("%+.4f" % v["ic_mean"]) if v else "—")
        A("| %s | %s |" % (f, " | ".join(cells)))
    A("")
    A("## 四、五分位分组明细（周频 top500）\n")
    A("| 因子 | Q1 | Q2 | Q3 | Q4 | Q5 | 单调 |")
    A("|---|---|---|---|---|---|---|")
    for f in F.keys():
        v = results.get("%s|top500|W" % f)
        if not v:
            continue
        q = v["qavg"]
        A("| %s | %+.3f%% | %+.3f%% | %+.3f%% | %+.3f%% | %+.3f%% | %s |" % (
            f, q.get("1", 0) * 100, q.get("2", 0) * 100, q.get("3", 0) * 100,
            q.get("4", 0) * 100, q.get("5", 0) * 100, "Y" if v["monotone"] else "N"))
    A("")
    A("## 五、多重检验与全族分布\n")
    allw = [v for v in results.values() if v["freq"] == "W" and v["domain"] == "top500"]
    ics = np.array([v["ic_mean"] for v in allw if v["ic_mean"] == v["ic_mean"]])
    ab = np.abs(ics)
    A("- 全族 IC 绝对值：中位数 **%.4f**，最大 **%.4f**（%s），最小 %.4f" % (
        np.median(ab), ab.max(),
        sorted(allw, key=lambda v: -abs(v["ic_mean"]))[0]["factor"], ab.min()))
    A("- 过线计数（主口径周频 top500，共 %d 因子）：" % len(allw))
    A("  - IC均值 ≥ 0.03：**%d** 个" % int((ab >= 0.03).sum()))
    A("  - \\|ICIR\\| ≥ 0.3：**%d** 个" % int(sum(1 for v in allw if abs(v["icir"]) >= 0.3)))
    A("  - Bonferroni t 显著：**%d** 个" % int(sum(1 for v in allw if v["t_bonf_pass"])))
    A("  - 五分位单调：**%d** 个" % int(sum(1 for v in allw if v["monotone"])))
    A("  - **四条全过：%d 个**" % int(sum(1 for v in allw if v["pass_all"])))
    A("")
    A("> 说明：若「全过」数 ≈ 0，则该族在大盘域无可用 alpha；")
    A("> 若「全过」数 ≥ 3 且集中在某一频率/方向，才值得进组合级验证。\n")
    A("## 六、结论\n")
    n_pass = int(sum(1 for v in allw if v["pass_all"]))
    if n_pass == 0:
        A("**整族证伪**：%d 个日内 K 线结构因子在大盘域无一个通过预注册判定线，" % len(allw))
        A("与 P12（-60.8%）/ P18（命中率<50%）/ P01 审计（仅 BP 有真实超额）的同族先验一致。")
        A("该族不进组合级验证，不立项。")
    else:
        A("**%d 个因子过线**，需逐个复核方向稳定性与容量后，再决定是否进组合级验证。" % n_pass)
    A("")
    A("---")
    A("")
    A("### 附：与外部文章《150因子跑上证180》的对照\n")
    CLAIM = {"HIGH0": ("买高", +1), "OPEN0": ("买高", +1), "KLEN": ("买高", +1),
             "CORR5": ("买高", +1), "STD5": ("买低(低波)", -1),
             "KSFT": ("买低(接飞刀)", -1), "VSTD60": ("买高", +1),
             "KLOW2": ("买高(下影线长)", +1), "KMID": ("买高", +1),
             "VMA20": ("买低(量能弱)", -1)}
    A("| 因子 | 文章声称 | 文章声称IC方向 | 本仓实测IC | 一致? |")
    A("|---|---|---|---|---|")
    for f, (desc, sign) in CLAIM.items():
        v = results.get("%s|top180|W" % f) or results.get("%s|top500|W" % f)
        if not v:
            continue
        ic = v["ic_mean"]
        ok = (ic * sign > 0)
        A("| %s | %s | %s | %+.4f | %s |" % (
            f, desc, "正" if sign > 0 else "负", ic, "✅" if ok else "❌"))
    A("")
    return L


if __name__ == "__main__":
    main()
