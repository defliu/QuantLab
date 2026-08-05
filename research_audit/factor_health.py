# coding=utf-8
"""因子健康监控体系（Factor Health Monitoring System）

痛点背景（本项目实证）：
  - 既有工具（scripts/batch_ic_test.py、factors/engine.py::compute_ic）只输出
    **全期聚合 IC/ICIR 一个数** → Project_01 的 BP 因子 2024+ 衰减、
    Project_10 的 hp 因子 2026 反转，都没能被提前预警，全靠事后人工审计（audit14-17）。
  - 本模块补缺：按**调仓期**计算 Spearman IC 序列 → 分段统计
    （全期 / 2024+ / 2026 / 近 N 期）→ 启发式**衰减告警**（HEALTHY/WATCH/DEGRADED/FAILED）。

设计目标：
  - 可复用、可扩展：新增因子只需实现 `factor_defs` 里的一个
    `(date, rebal_dates, panel, fin_snapshot, ind_map, dd) -> Series|None`。
  - 口径与实盘策略一致：调仓日 = get_rebalance_dates(freq="2M")；
    IC 前向收益 = 下一调仓日 T+1 开盘 → 下下调仓日 T+1 开盘（与 audit14 结算口径一致）。
  - PIT 安全：财务用 ann_date 快照；禁止未来数据。
  - 自包含：数据从 E:/astock 读，不改任何其他文件。

用法：
  python research_audit/factor_health.py
输出：
  research_audit/factor_health_报告.md   （人读报告）
  research_audit/factor_health_ic.csv    （逐期 IC 时序，机器读）
"""
import sys, os, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, r"D:\QuantLab")
sys.path.insert(0, r"D:\QuantLab\projects\Project_01_多因子IC小盘Alpha")

from research.multi_factor_ic.config import DAILY_PATH, BASIC_PATH, FINANCE_PATH
from research.multi_factor_ic.data_loader import get_rebalance_dates

OUT_MD = os.path.join(HERE, "factor_health_报告.md")
OUT_CSV = os.path.join(HERE, "factor_health_ic.csv")

START_DATE = "2018-01-01"
END_DATE = "2026-06-30"
TOP_N = 80                # 与策略持有数一致（IC 在候选池内、前向收益按等权 TOP N 口径）
FWD = 1                   # 前向跨 1 个调仓期（2M）
_MIN_IC_N = 30            # 每期 IC 最少样本
REBAL_FREQ = "M"          # 健康监控采样频率："M"=月频(细粒度，业界标准) / "2M"=双月(与策略对齐)

_log = []
def log(*args):
    s = " ".join(str(a) for a in args)
    print(s)
    _log.append(s)


# ============================================================
# 1. 数据加载（PIT 安全，复用 audit14 口径）
# ============================================================
def load_panel():
    _start_ts = pd.Timestamp(START_DATE)
    _end_ts = pd.Timestamp(END_DATE)

    daily = pd.read_parquet(DAILY_PATH)
    idx = daily.index
    codes_all = set(idx.get_level_values("ts_code")[
        idx.get_level_values("trade_date") >= _start_ts].unique())
    daily = daily.loc[idx.get_level_values("ts_code").isin(codes_all)].copy()
    idx = daily.index
    daily = daily.loc[
        (idx.get_level_values("trade_date") >= _start_ts) &
        (idx.get_level_values("trade_date") <= _end_ts)].copy()
    idx = daily.index
    prev_close = daily["close"].groupby(level=1).shift(1)

    codes_all = set(idx.get_level_values("ts_code"))
    panel = pd.DataFrame({
        "close": daily["close"].values,
        "open": daily["open"].values,
        "high": daily["high"].values,
        "low": daily["low"].values,
        "pe_ttm": daily["pe_ttm"].values,
        "pb": daily["pb"].values,
        "circ_mv": daily["circ_mv"].values,
        "amount": daily["amount"].values,
        "vol": daily["vol"].values,
        "prev_close": prev_close.values,
    }, index=idx)
    is_st = daily["is_st"].astype(bool)
    suspend = daily["suspend_type"].fillna("N")
    panel = panel.loc[~is_st & ~suspend.isin(["S", "R", "R&S"])]
    return panel, codes_all


def load_industry_map():
    basic = pd.read_parquet(BASIC_PATH)
    return dict(zip(basic["ts_code"], basic["industry"].fillna("其他")))


def load_finance(codes_all):
    fin = pd.read_parquet(FINANCE_PATH)
    fin = fin[["ts_code", "end_date", "ann_date", "bps", "roe",
               "profit_dedt", "debt_to_assets"]].copy()
    fin["ann_date"] = pd.to_datetime(fin["ann_date"], errors="coerce")
    fin = fin.dropna(subset=["ann_date"])
    fin = fin[fin["ts_code"].isin(codes_all)]
    fin = fin.sort_values(["ts_code", "ann_date"])
    fin_by_code = {c: g for c, g in fin.groupby("ts_code")}

    _cache = {}
    def fin_snapshot(date):
        d = pd.Timestamp(date)
        if d in _cache:
            return _cache[d]
        rows = {}
        for c, g in fin_by_code.items():
            gg = g[g["ann_date"] <= d]
            if len(gg) == 0:
                continue
            last = gg.iloc[-1]
            rows[c] = (last["bps"], last["roe"], last["profit_dedt"],
                       last["debt_to_assets"])
        _cache[d] = rows
        return rows
    return fin_snapshot


# ============================================================
# 2. 候选池 + 因子计算
# ============================================================
def get_candidates(dd, fin_snapshot, date):
    """Candidate universe = strategy screen: small-cap + PIT quality gate."""
    m = (dd["circ_mv"] > 0) & (dd["circ_mv"] < 300000) & \
        (dd["pe_ttm"] > 0) & (dd["pb"] > 0)
    fs = fin_snapshot(pd.Timestamp(date))
    mq = m.copy()
    for c in mq.index:
        r = fs.get(c)
        if r is None or not (r[0] > 0 and r[2] > 0 and r[1] > 0):
            mq[c] = False
    return mq[mq].index


# 因子实现：均返回 Series(index=code) 或 None
def _bp_ind_z(d, dd, cand, ind_map, fin_snapshot):
    """行业中性 BP z-score（Project_10 V2a 核心）"""
    bp = 1.0 / dd.loc[cand]["pb"].replace(0, np.nan)
    inds = pd.Series(cand, index=cand).map(ind_map)
    t = pd.DataFrame({"bp": bp, "ind": inds}).dropna()
    if len(t) < _MIN_IC_N:
        return None
    z = t.groupby("ind")["bp"].transform(
        lambda x: (x - x.mean()) / (x.std() + 1e-9))
    return z


def _bp_hist_pct(d, dd, cand, ind_map, fin_snapshot, bp_monthly=None):
    """BP 历史分位（Project_10 旧 hp，已知 2026 反向）"""
    if bp_monthly is None:
        return None
    dts = pd.Timestamp(d)
    w = [m for m in bp_monthly.index if m <= dts][-36:]
    if len(w) < 12:
        return None
    sub = bp_monthly.loc[w]
    if dts not in sub.index and len(sub) == 0:
        return None
    ref = sub.loc[w[-1]]
    r = (sub <= ref).mean(axis=0)
    return r.reindex(cand)


def _roll_factor(panel, code, col, n, agg):
    """按 code 滚动计算，返回 code-> 最新期 Series 用的全序列缓存（外部统一算）"""
    raise NotImplementedError


# 直接基于 window 的截面因子（调用方逐期传入 window 字典）
def build_factor_defs(bp_monthly=None, close_by_code=None):
    """返回 {factor_name: callable(d, dd, cand, ctx)}，”光标“因子在外部预计算。"""
    defs = {}

    def f_bp_ind_z(d, dd, cand, ctx):
        return _bp_ind_z(d, dd, cand, ctx["ind_map"], ctx["fin_snapshot"])

    def f_bp_hist_pct(d, dd, cand, ctx):
        return _bp_hist_pct(d, dd, cand, ctx["ind_map"],
                            ctx["fin_snapshot"], ctx["bp_monthly"])

    def f_roe(d, dd, cand, ctx):
        fs = ctx["fin_snapshot"](pd.Timestamp(d))
        out = {}
        for c in cand:
            r = fs.get(c)
            if r is not None:
                out[c] = r[1]
        s = pd.Series(out)
        return s if len(s) >= _MIN_IC_N else None

    def f_momentum_1m(d, dd, cand, ctx):
        cbuf = ctx["close_by_code"]
        out = {}
        for c in cand:
            arr = cbuf.get(c)
            if arr is None or len(arr) < 21:
                continue
            t = pd.Timestamp(d)
            dates = arr.index
            pos = dates.searchsorted(t)
            if pos < 21:
                continue
            cur = arr.iloc[pos - 1]
            prev = arr.iloc[pos - 1 - 21]
            if prev and prev > 0 and cur and cur > 0:
                out[c] = cur / prev - 1.0
        s = pd.Series(out)
        return s if len(s) >= _MIN_IC_N else None

    def f_volatility_60d(d, dd, cand, ctx):
        cbuf = ctx["close_by_code"]
        out = {}
        for c in cand:
            arr = cbuf.get(c)
            if arr is None or len(arr) < 60:
                continue
            t = pd.Timestamp(d)
            dates = arr.index
            pos = dates.searchsorted(t)
            if pos < 60:
                continue
            seg = arr.iloc[pos - 60:pos]
            ret = seg.pct_change().dropna()
            if len(ret) < 20:
                continue
            sd = float(ret.std())
            if sd and sd > 0:
                out[c] = sd * (252.0 ** 0.5)
        s = pd.Series(out)
        return s if len(s) >= _MIN_IC_N else None

    def f_vwap_corr(d, dd, cand, ctx):
        """GTJA191: -1 * rank(corr(rank(VWAP), rank(volume), 5)) 截面近似。
        用逐日 VWAP=amount/vol 的 5 日秩相关（在最近 5 个 bar 上）。
        """
        vbuf = ctx["vwap_by_code"]
        out = {}
        for c in cand:
            v = vbuf.get(c)
            if v is None or len(v) < 5:
                continue
            t = pd.Timestamp(d)
            dates = v.index
            pos = dates.searchsorted(t)
            if pos < 5:
                continue
            seg = v.iloc[pos - 5:pos]
            rv = seg.rank()
            rt = pd.Series(range(len(seg)), index=seg.index).rank()  # 时间秩
            if rv.nunique() < 2:
                continue
            corr = rv.corr(rt)
            if corr is None or np.isnan(corr):
                continue
            out[c] = -corr
        s = pd.Series(out)
        return s if len(s) >= _MIN_IC_N else None

    defs["BP行业中性z"] = f_bp_ind_z
    defs["BP历史分位"] = f_bp_hist_pct
    defs["ROE"] = f_roe
    defs["动量1m"] = f_momentum_1m
    defs["波动率60d"] = f_volatility_60d
    defs["VWAP量价相关"] = f_vwap_corr
    return defs


# ============================================================
# 3. 前向收益 + 逐期 IC（与 audit14 结算一致：期 i 结算 E_{i} 开盘开仓 -> E_{i+1} 开盘平仓）
# ============================================================
def per_period_forward_returns(panel, trade_dates, rebal):
    """返回 {rebal_i_date: Series(code->RET)}：
    在 rebal[i] 选股，E_{i}（rebal[i] 次一交易）开盘买入，持有到 E_{i+1} 开盘卖出。
    """
    ti = {pd.Timestamp(x): k for k, x in enumerate(trade_dates)}
    out = {}
    for i in range(len(rebal) - 1):
        d = rebal[i]
        e_idx = ti[pd.Timestamp(d)] + 1
        nxt_e_idx = ti[pd.Timestamp(rebal[i + 1])] + 1
        if e_idx >= len(trade_dates) or nxt_e_idx >= len(trade_dates):
            continue
        e_date = trade_dates[e_idx]
        x_date = trade_dates[nxt_e_idx]
        e_open = panel.loc[e_date, "open"]
        x_open = panel.loc[x_date, "open"]
        common = e_open.dropna().index.intersection(
            x_open.dropna().index)
        ret = {}
        for c in common:
            eo, xo = e_open[c], x_open[c]
            if eo > 0 and xo > 0:
                ret[c] = xo / eo - 1.0
        if ret:
            out[pd.Timestamp(d)] = pd.Series(ret)
    return out


def spearman_ic(factor_vals, fwd_ret):
    common = factor_vals.dropna().index.intersection(fwd_ret.dropna().index)
    if len(common) < _MIN_IC_N:
        return None
    ic = factor_vals.loc[common].rank().corr(fwd_ret.loc[common].rank())
    if np.isnan(ic):
        return None
    return ic


# ============================================================
# 4. 健康度评估 + 衰减告警
# ============================================================
def _ic_stats(series):
    if series is None or len(series) < 3:
        return None
    mean = series.mean()
    std = series.std()
    return {
        "n": len(series),
        "ic_mean": float(mean),
        "ic_std": float(std),
        "icir": float(mean / std) if std > 0 else 0.0,
        "positive_pct": float((series > 0).mean()),
        "max_dd_ic": float(series.min()),
    }


def assess_decay(ic, recent_n=8, alert_n=4):
    """启发式衰减评估。返回 (等级, 原因列表)。
    ic: 全期 IC Series(按调仓期)
    等级: HEALTHY / WATCH / DEGRADED / FAILED
    """
    if ic is None or len(ic) < 3:
        return "INSUFFICIENT", ["样本不足"]
    full = ic.mean()
    recent = ic.iloc[-recent_n:]
    recent_mean = recent.mean()
    pos_full = (ic > 0).mean()
    pos_recent = (recent > 0).mean()
    half = max(3, len(ic) // 2)
    early = ic.iloc[:half]
    late = ic.iloc[half:]
    early_mean = early.mean()
    late_mean = late.mean()

    reasons = []
    grade = "HEALTHY"
    if recent_mean <= 0:
        grade = "FAILED"
        reasons.append("近%d期IC均值为%+.3f<=0，已失效" % (recent_n, recent_mean))
    elif pos_recent < 0.5:
        grade = "DEGRADED"
        reasons.append("近%d期IC正值占比%.0f%%<50%%" % (recent_n, pos_recent * 100))
    elif full > 0 and recent_mean < 0.5 * full:
        grade = "DEGRADED"
        reasons.append("近%d期IC(%+.3f)<全期(%+.3f)一半" % (recent_n, recent_mean, full))
    elif late_mean < early_mean - 0.02:
        grade = "WATCH"
        reasons.append("后半段IC(%+.3f)低于前半段(%+.3f)" % (late_mean, early_mean))
    elif recent_mean < 0.03:
        grade = "WATCH"
        reasons.append("近%d期IC仅%+.3f，偏弱" % (recent_n, recent_mean))
    else:
        reasons.append("全期IC%+.3f / 近%d期%+.3f / 正值占比%.0f%%"
                       % (full, recent_n, recent_mean, pos_recent * 100))
    return grade, reasons


# ============================================================
# 5. 主流程
# ============================================================
def main():
    t0 = time.time()
    log("======== 因子健康监控 ========")

    panel, codes_all = load_panel()
    ind_map = load_industry_map()
    fin_snapshot = load_finance(codes_all)
    log("panel:", panel.shape)

    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    ti = {pd.Timestamp(x): k for k, x in enumerate(trade_dates)}
    rebal = get_rebalance_dates(panel, freq=REBAL_FREQ)
    log("调仓期数(%s):" % REBAL_FREQ, len(rebal))

    # BP 历史分位依赖月度 BP（PIT warmup）
    pb_wide = panel["pb"].unstack("ts_code")
    pb_wide.index = pd.DatetimeIndex(pb_wide.index)
    bp_wide = 1.0 / pb_wide.replace(0, np.nan)
    bp_monthly = bp_wide.resample("ME").last()
    log("bp_monthly:", bp_monthly.shape)

    # 逐 code 的 close / vwap 序列缓存（供动量/波动/VWAP）
    close_by_code = {}
    vwap_by_code = {}
    for code, g in panel.groupby(level=1):
        g = g.droplevel(1)  # MultiIndex -> 仅 trade_date
        g = g.sort_index() if not g.index.is_monotonic_increasing else g
        close_by_code[code] = g["close"]
        vwap_by_code[code] = g["amount"] / g["vol"].replace(0, np.nan)
    log("code 序列缓存:", len(close_by_code))

    defs = build_factor_defs()
    ctx = {"ind_map": ind_map, "fin_snapshot": fin_snapshot,
           "bp_monthly": bp_monthly, "close_by_code": close_by_code,
           "vwap_by_code": vwap_by_code}

    fwd = per_period_forward_returns(panel, trade_dates, rebal)
    log("前向收益期数:", len(fwd))

    # 逐期逐因子 IC
    ic_records = []
    factor_ic = {name: [] for name in defs}
    valid_i = []
    _rebal_used = [d for d in rebal if pd.Timestamp(d) in fwd]
    for d in _rebal_used:
        dd = panel.loc[pd.Timestamp(d)]
        cand = get_candidates(dd, fin_snapshot, pd.Timestamp(d))
        if len(cand) < _MIN_IC_N:
            continue
        fwd_ret = fwd[pd.Timestamp(d)]
        for fname, fn in defs.items():
            try:
                vals = fn(pd.Timestamp(d), dd, cand, ctx)
            except Exception as e:
                vals = None
            if vals is None:
                continue
            ic = spearman_ic(vals, fwd_ret)
            if ic is None:
                continue
            factor_ic[fname].append(ic)
            ic_records.append({"date": pd.Timestamp(d).strftime("%Y-%m-%d"),
                               "factor": fname, "ic": round(ic, 5)})
        valid_i.append(pd.Timestamp(d))

    ic_series = {}
    for fname, lst in factor_ic.items():
        if lst:
            ic_series[fname] = pd.Series(lst, index=pd.DatetimeIndex(
                [pd.Timestamp(_rebal_used[k]) for k in range(len(lst))]))

    # 报告组装
    lines = []
    lines.append("# 因子健康监控报告\n")
    lines.append("- 生成时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("- 口径: 调仓期 Spearman IC，前向收益=下一调仓期 T+1 开 → 下下期 T+1 开")
    lines.append("- 调仓频率: %s，区间 %s ~ %s，期数 %d\n"
                 % (REBAL_FREQ, START_DATE, END_DATE, len(valid_i)))

    grade_order = {"FAILED": 0, "DEGRADED": 1, "WATCH": 2, "HEALTHY": 3,
                   "INSUFFICIENT": 4}

    def regime_stats(ic_ser):
        if ic_ser is None or len(ic_ser) < 3:
            return {}
        seg = {}
        idx = ic_ser.index
        seg["全期"] = _ic_stats(ic_ser)
        m2024 = idx >= "2024-01-01"
        m2026 = idx >= "2026-01-01"
        if m2024.any():
            seg["2024+"] = _ic_stats(ic_ser[m2024])
        if m2026.any():
            seg["2026"] = _ic_stats(ic_ser[m2026])
        seg["近8期"] = _ic_stats(ic_ser.iloc[-8:])
        return seg

    sorted_names = sorted(ic_series.keys(),
                          key=lambda n: grade_order[assess_decay(ic_series[n])[0]])
    lines.append("## 因子健康度总览\n")
    lines.append("| 因子 | 等级 | 全期IC | 2024+IC | 2026IC | 近8期IC | 判断 |")
    lines.append("|---|---|---|---|---|---|---|")
    for n in sorted_names:
        s = ic_series[n]
        grade, reasons = assess_decay(s)
        reg = regime_stats(s)
        def g(key):
            v = reg.get(key)
            return "%.3f" % v["ic_mean"] if v and "ic_mean" in v else "—"
        lines.append("| %s | %s | %s | %s | %s | %s | %s |"
                     % (n, grade, g("全期"), g("2024+"), g("2026"),
                        g("近8期"), "; ".join(reasons[:2])))

    lines.append("\n## 分阶段明细\n")
    for n in sorted_names:
        reg = regime_stats(ic_series[n])
        lines.append("### %s\n" % n)
        lines.append("| 分段 | n | IC均值 | ICIR | 正值占比 |")
        lines.append("|---|---|---|---|---|")
        for k, v in reg.items():
            if not v:
                lines.append("| %s | — | — | — | — |" % k)
                continue
            lines.append("| %s | %d | %.4f | %.3f | %.0f%% |"
                         % (k, v["n"], v["ic_mean"], v["icir"],
                            v["positive_pct"] * 100))
        lines.append("")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    df = pd.DataFrame(ic_records)
    if not df.empty:
        df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    log("\n总用时 %.0fs" % (time.time() - t0))
    log("报告: %s" % OUT_MD)
    log("时序: %s" % OUT_CSV)


if __name__ == "__main__":
    main()