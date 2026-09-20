# coding=utf-8
"""因子挖掘管线 - 批量 IC 初筛 + 训练集验证（S1 + S2）

用法：
  D:\\Python311\\python.exe research_factor_mining/run_screen.py \
      --batch candidates/batch_pilot_20260916.yaml
  （可选 --start/--end 覆盖训练窗起点等）

口径（与 factor_health.py 对齐，防过拟合纪律内置）：
  - Universe: 小盘域 circ_mv 0~30亿 + pe_ttm>0 + pb>0 + 非ST + 非停牌
  - 调仓频率: 月频（月末最后交易日）
  - 前向收益: 期 i T+1 开盘买入 -> 期 i+1 T+1 开盘卖出（Spearman IC）
  - S1 只淘汰不入选: |全期IC|<0.02 或 训练段与全期反号 -> 弃
  - S2 训练集(2021-01~2023-12)验证: 训练段 ICIR>=0.5 且正值占比>=60% 且逐年无反号

输出：
  ic_screen/batch_<id>_ic.csv   逐期 IC 时序（机器读）
  ic_screen/batch_<id>_report.md 汇总报告（S1/S2 判定 + 漏斗）
"""
import sys, os, time, argparse
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, r"D:\QuantLab")
sys.path.insert(0, r"D:\QuantLab\projects\Project_01_多因子IC小盘Alpha")

from research.multi_factor_ic.config import DAILY_PATH, BASIC_PATH, FINANCE_PATH
from research.multi_factor_ic.data_loader import get_rebalance_dates
import factor_defs_pilot as FDS
try:
    import factor_defs_r2 as FDS2
    for _n in dir(FDS2):
        if not _n.startswith("_"):
            setattr(FDS, _n, getattr(FDS2, _n))
except ImportError:
    pass

START_DATE = "2018-01-01"
END_DATE = "2026-09-11"
TRAIN_START, TRAIN_END = "2021-01-01", "2023-12-31"
OOS_START, OOS_END = "2024-01-01", "2026-09-11"
REBAL_FREQ = "M"
FWD = 1
_MIN_IC_N = 30
OUTDIR = os.path.join(HERE, "ic_screen")

_log = []
def log(*args):
    s = " ".join(str(a) for a in args)
    print(s)
    _log.append(s)


def load_panel(start=START_DATE, end=END_DATE):
    daily = pd.read_parquet(DAILY_PATH)
    idx = daily.index
    codes_all = set(idx.get_level_values("ts_code")[
        idx.get_level_values("trade_date") >= pd.Timestamp(start)].unique())
    daily = daily.loc[idx.get_level_values("ts_code").isin(codes_all)].copy()
    idx = daily.index
    daily = daily.loc[
        (idx.get_level_values("trade_date") >= pd.Timestamp(start)) &
        (idx.get_level_values("trade_date") <= pd.Timestamp(end))].copy()
    idx = daily.index
    prev_close = daily["close"].groupby(level=1).shift(1)
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
    for col in ["turnover_rate", "up_limit"]:
        if col in daily.columns:
            panel[col] = daily[col].values
    is_st = daily["is_st"].astype(bool)
    suspend = daily["suspend_type"].fillna("N")
    panel = panel.loc[~is_st & ~suspend.isin(["S", "R", "R&S"])]
    return panel


def load_northbound():
    """北向持股面板 -> (date x code) ratio 宽表 + vol 宽表"""
    hk = pd.read_parquet(r"D:\astock\northbound\hk_hold_full.parquet")
    hk["trade_date"] = pd.to_datetime(hk["trade_date"])
    ts = hk["ts_code"].str.upper()
    ratio = hk.pivot_table(index="trade_date", columns="ts_code",
                           values="ratio", aggfunc="last")
    vol = hk.pivot_table(index="trade_date", columns="ts_code",
                         values="vol", aggfunc="last")
    return ratio.sort_index(), vol.sort_index()


FIN_FIELDS = ["bps", "roe", "gross_margin", "netprofit_margin", "ocfps",
              "eps", "current_ratio", "quick_ratio", "debt_to_assets",
              "profit_dedt", "netprofit_yoy", "dt_netprofit_yoy", "or_yoy",
              "ocf_yoy", "roe_yoy", "assets_yoy", "tr_yoy",
              "q_roe", "q_gsprofit_margin", "q_netprofit_margin",
              "q_sales_qoq", "q_netprofit_qoq", "q_dtprofit_to_profit",
              "q_ocf_to_sales", "dtprofit_to_profit", "ocf_to_profit",
              "ocf_to_or"]


def load_finance_wide(rebal_dates, codes_all):
    """财务 PIT 宽表：{field: DataFrame(index=调仓期, columns=code)}。

    用 ann_date 做 PIT 锚：调仓日取 该日及之前最新已公告值，forward-fill。
    """
    fin = pd.read_parquet(FINANCE_PATH)
    keep = ["ts_code", "ann_date"] + [c for c in FIN_FIELDS if c in fin.columns]
    fin = fin[keep].copy()
    fin["ann_date"] = pd.to_datetime(fin["ann_date"], errors="coerce")
    fin = fin.dropna(subset=["ann_date"])
    fin = fin[fin["ts_code"].isin(codes_all)]
    rd = pd.DatetimeIndex(sorted(rebal_dates))
    out = {}
    for f in [c for c in FIN_FIELDS if c in fin.columns]:
        wide = fin.pivot_table(index="ann_date", columns="ts_code",
                               values=f, aggfunc="last")
        wide = wide.reindex(rd).ffill()
        out[f] = wide
    return out


def _evt_frame(path):
    """统一加载事件型 parquet -> (date_series, code_series) 视图，兼容 MultiIndex/平铺。"""
    df = pd.read_parquet(path)
    idx = df.index
    if isinstance(idx, pd.MultiIndex):
        date_vals = idx.get_level_values(0)
        code_vals = idx.get_level_values(1)
    elif "trade_date" in df.columns and "ts_code" in df.columns:
        date_vals = df["trade_date"]
        code_vals = df["ts_code"]
    elif "report_date" in df.columns and "ts_code" in df.columns:
        date_vals = df["report_date"]
        code_vals = df["ts_code"]
    else:
        raise ValueError("无法解析事件表日期/代码列: %s" % path)
    return df, date_vals, code_vals


def load_lhb():
    """龙虎榜事件面板（date x code）：net 净买入额 / net_rate 净买入率 / flag 上榜 / amt_rate 成交占流通盘比 / buy_ratio 买方占比"""
    df, dv, cv = _evt_frame(r"D:\astock\lhb\top_list.parquet")
    df = df.reset_index(drop=True).copy()
    df["trade_date"] = pd.to_datetime(dv, errors="coerce")
    df["ts_code"] = cv.astype(str).str.upper()
    df = df.dropna(subset=["trade_date"])
    net = df.pivot_table(index="trade_date", columns="ts_code",
                         values="net_amount", aggfunc="sum").sort_index()
    rate = df.pivot_table(index="trade_date", columns="ts_code",
                          values="net_rate", aggfunc="mean").sort_index()
    flag = net.notna().astype(int)
    amt_rate = None
    if "amount_rate" in df.columns:
        amt_rate = df.pivot_table(index="trade_date", columns="ts_code",
                                  values="amount_rate", aggfunc="mean").sort_index()
    buy_ratio = None
    if {"l_buy", "l_amount"}.issubset(df.columns):
        bb = df[df["l_amount"].fillna(0) > 0].copy()
        if len(bb):
            buy_ratio = (bb["l_buy"] / bb["l_amount"].replace(0, np.nan))
            buy_ratio = pd.DataFrame({"br": buy_ratio, "d": bb["trade_date"], "c": bb["ts_code"]})
            buy_ratio = buy_ratio.pivot_table(index="d", columns="c",
                                              values="br", aggfunc="mean").sort_index()
    return net, rate, flag, amt_rate, buy_ratio


def load_inst():
    """龙虎榜机构专用席位面板（date x code）：net_buy 机构净买入 / flag 机构参与哑变量（top_inst.parquet 已落盘 2012-2026）"""
    path = r"D:\astock\lhb\top_inst.parquet"
    df = pd.read_parquet(path)
    idx = df.index
    if isinstance(idx, pd.MultiIndex):
        date_vals = idx.get_level_values(0)
        code_vals = idx.get_level_values(1)
    elif "trade_date" in df.columns and "ts_code" in df.columns:
        date_vals = df["trade_date"]
        code_vals = df["ts_code"]
    else:
        raise ValueError("无法解析 top_inst 日期/代码列: %s" % path)
    df = df.reset_index(drop=True).copy()
    df["trade_date"] = pd.to_datetime(date_vals, errors="coerce")
    df["ts_code"] = code_vals.astype(str).str.upper()
    df = df.dropna(subset=["trade_date"])
    net = df.pivot_table(index="trade_date", columns="ts_code",
                         values="net_buy", aggfunc="sum").sort_index()
    flag = net.notna().astype(int)
    return net, flag


def load_res():
    """研报事件面板（date x code）：count 研报数 / rating 评级均值 / tp 目标价均值 / eps 预测均值

    report_rc_daily MultiIndex 顺序 = (ts_code, report_date, name, analyst, title, period)。
    """
    path = r"D:\astock\research\report_rc_daily.parquet"
    df = pd.read_parquet(path)
    idx = df.index
    if isinstance(idx, pd.MultiIndex):
        code_vals = idx.get_level_values(0)
        date_vals = idx.get_level_values(1)
    elif "report_date" in df.columns and "ts_code" in df.columns:
        date_vals = df["report_date"]
        code_vals = df["ts_code"]
    else:
        raise ValueError("无法解析研报表日期/代码列: %s" % path)
    df = df.reset_index(drop=True).copy()
    df["report_date"] = pd.to_datetime(date_vals, errors="coerce")
    df["ts_code"] = code_vals.astype(str).str.upper()
    df = df.dropna(subset=["report_date"])
    rmap = {"买入": 1, "增持": 2, "中性": 3, "减持": 4, "卖出": 5}
    df["rating_num"] = df["rating"].map(rmap)
    cnt = df.pivot_table(index="report_date", columns="ts_code",
                         values="name", aggfunc="count").sort_index()
    rate = df.pivot_table(index="report_date", columns="ts_code",
                          values="rating_num",
                          aggfunc="mean").sort_index()
    tp = None
    if "tp" in df.columns:
        tp = df.pivot_table(index="report_date", columns="ts_code",
                            values="tp", aggfunc="mean").sort_index()
    eps = None
    if "eps" in df.columns:
        eps = df.pivot_table(index="report_date", columns="ts_code",
                             values="eps", aggfunc="mean").sort_index()
    return cnt, rate, tp, eps


def get_candidates(dd):
    m = (dd["circ_mv"] > 0) & (dd["circ_mv"] < 300000) & \
        (dd["pe_ttm"] > 0) & (dd["pb"] > 0)
    return m[m].index


def per_period_forward_returns(panel, trade_dates, rebal):
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
        common = e_open.dropna().index.intersection(x_open.dropna().index)
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


def ic_stats(series):
    if series is None or len(series) < 3:
        return None
    return {"n": len(series),
            "ic_mean": float(series.mean()),
            "icir": float(series.mean() / series.std()) if series.std() > 0 else 0.0,
            "positive_pct": float((series > 0).mean()),
            "min": float(series.min()),
            "max": float(series.max())}


def seg_stats(ic_ser, name):
    out = {}
    if ic_ser is None or len(ic_ser) < 3:
        return {"全期": None, "训练段": None, "样本外": None, "近8期": None}
    out["全期"] = ic_stats(ic_ser)
    tr = ic_ser[(ic_ser.index >= TRAIN_START) & (ic_ser.index <= TRAIN_END)]
    oo = ic_ser[(ic_ser.index >= OOS_START) & (ic_ser.index <= OOS_END)]
    out["训练段"] = ic_stats(tr) if len(tr) >= 3 else None
    out["样本外"] = ic_stats(oo) if len(oo) >= 3 else None
    out["近8期"] = ic_stats(ic_ser.iloc[-8:])
    return out


def yearly_sign(ic_ser):
    """训练段逐年 IC 均值符号 -> dict{year: sign}"""
    if ic_ser is None:
        return {}
    tr = ic_ser[(ic_ser.index >= TRAIN_START) & (ic_ser.index <= TRAIN_END)]
    out = {}
    for y, g in tr.groupby(tr.index.year):
        if len(g) >= 3:
            out[y] = 1 if g.mean() > 0 else -1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    ap.add_argument("--start", default=START_DATE)
    ap.add_argument("--end", default=END_DATE)
    args = ap.parse_args()

    batch_path = os.path.join(HERE, args.batch)
    with open(batch_path, encoding="utf-8") as f:
        import yaml
        batch = yaml.safe_load(f)
    batch_id = batch["batch_id"]
    cands = batch["candidates"]
    log("批次 %s | 候选 %d | universe=%s" % (batch_id, len(cands), batch.get("universe")))
    t0 = time.time()

    os.makedirs(OUTDIR, exist_ok=True)
    panel = load_panel(args.start, args.end)
    log("panel:", panel.shape)
    north_ratio, north_vol = load_northbound()
    log("north:", north_ratio.shape)
    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    rebal = get_rebalance_dates(panel, freq=REBAL_FREQ)
    log("调仓期数(%s): %d" % (REBAL_FREQ, len(rebal)))

    codes_all = set(panel.index.get_level_values("ts_code"))
    fin_wide = load_finance_wide(rebal, codes_all)
    lhb_net, lhb_rate, lhb_flag, lhb_amt_rate, lhb_buy_ratio = load_lhb()
    inst_net, inst_flag = load_inst()
    res_cnt, res_rate, res_tp, res_eps = load_res()
    log("fin_wide fields:", list(fin_wide.keys()))
    log("lhb/res/inst 面板已加载")

    close_by_code = {}
    pctchg_by_code = {}
    turnover_by_code = {}
    limit_flag_by_code = {}
    open_by_code = {}
    amount_by_code = {}
    for code, g in panel.groupby(level=1):
        g = g.droplevel(1).sort_index()
        close_by_code[code] = g["close"]
        pct = g["close"].pct_change()
        pctchg_by_code[code] = pct
        if "turnover_rate" in g.columns:
            turnover_by_code[code] = g["turnover_rate"]
        else:
            turnover_by_code[code] = None
        open_by_code[code] = g["open"]
        amount_by_code[code] = g["amount"]
        # 涨停：close >= up_limit - 0.01（精确口径）；无 up_limit 时退化为 pct>=9.5% 近似
        if "up_limit" in g.columns:
            ul = g["up_limit"]
            limit_flag_by_code[code] = ((g["close"] >= ul - 0.01) & ul.notna() & (ul > 0)).astype(int)
        else:
            limit_flag_by_code[code] = (pct >= 0.095).astype(int)
    log("code 序列缓存:", len(close_by_code))

    # 上市日期 map（价格行为族次新因子）
    list_date = {}
    try:
        basic = pd.read_parquet(BASIC_PATH)
        if "list_date" in basic.columns:
            ld = pd.to_datetime(basic["list_date"], errors="coerce")
            list_date = {str(c).upper(): d for c, d in zip(basic["ts_code"], ld)
                         if d is not None and pd.notna(d)}
    except Exception as e:
        log("list_date 加载失败(跳过):", e)

    ctx = {
        "fin_wide": fin_wide,
        "close_by_code": close_by_code,
        "pctchg_by_code": pctchg_by_code,
        "turnover_by_code": turnover_by_code,
        "limit_flag_by_code": limit_flag_by_code,
        "open_by_code": open_by_code,
        "amount_by_code": amount_by_code,
        "list_date": list_date,
        "north_ratio": north_ratio,
        "north_vol": north_vol,
        "lhb_net": lhb_net,
        "lhb_rate": lhb_rate,
        "lhb_flag": lhb_flag,
        "lhb_amt_rate": lhb_amt_rate,
        "lhb_buy_ratio": lhb_buy_ratio,
        "inst_net": inst_net,
        "inst_flag": inst_flag,
        "res_cnt": res_cnt,
        "res_rate": res_rate,
        "res_tp": res_tp,
        "res_eps": res_eps,
    }

    fwd = per_period_forward_returns(panel, trade_dates, rebal)
    log("前向收益期数:", len(fwd))

    # 逐期逐因子 IC
    ic_records = []
    factor_ic = {c["id"]: [] for c in cands}
    _rebal_used = [d for d in rebal if pd.Timestamp(d) in fwd]
    for d in _rebal_used:
        dd = panel.loc[pd.Timestamp(d)]
        cand = get_candidates(dd)
        if len(cand) < _MIN_IC_N:
            continue
        fwd_ret = fwd[pd.Timestamp(d)]
        for cand_def in cands:
            fname = cand_def["id"]
            fn = getattr(FDS, cand_def["fn"], None)
            if fn is None:
                continue
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

    ic_series = {}
    for fname in [c["id"] for c in cands]:
        lst = factor_ic.get(fname, [])
        if lst:
            ic_series[fname] = pd.Series(lst, index=pd.DatetimeIndex(
                [pd.Timestamp(_rebal_used[k]) for k in range(len(lst))]))

    # 判定
    lines = []
    lines.append("# 因子初筛报告 批次 %s\n" % batch_id)
    lines.append("- 生成: %s | 调仓 %s | 前向 %d 期"
                 % (time.strftime("%Y-%m-%d %H:%M:%S"), REBAL_FREQ, len(fwd)))
    lines.append("- 口径: 月频 Spearman IC；前向=期i T+1开->期i+1 T+1开；universe=小盘域(0~30亿+pe/pb>0)\n")
    lines.append("| ID | 方向 | S1(全期IC) | S1判定 | 训练段IC/ICIR/正值 | 逐年符号 | S2判定 |")
    lines.append("|---|---|---|---|---|---|---|")
    survive_s1, survive_s2 = [], []
    for c in cands:
        fname = c["id"]
        if fname not in ic_series:
            lines.append("| %s | %s | 样本不足 | — | — | — | 弃(样本不足) |" % (fname, c.get("direction", "")))
            continue
        hint = c.get("direction_hint", 1)
        s = ic_series[fname] * hint          # 翻转后正值=方向正确
        seg = seg_stats(s, fname)
        full = seg["全期"]
        tr = seg["训练段"]
        # S1: 只淘汰不入选
        full_ic = full["ic_mean"] if full else 0.0
        tr_ic = tr["ic_mean"] if tr else 0.0
        if abs(full_ic) < 0.02 or (tr_ic != 0 and (tr_ic * full_ic < 0)):
            s1 = "淘汰"
        else:
            s1 = "晋级"
            survive_s1.append(fname)
        # S2: 训练集验证（翻转后）
        ysign = yearly_sign(s)
        signs = {str(k): ("+" if v > 0 else "-") for k, v in sorted(ysign.items())}
        s2 = "—"
        if s1 == "晋级" and tr and tr["icir"] >= 0.5 and tr["positive_pct"] >= 0.60 \
                and len(set(signs.values())) == 1 and signs:
            s2 = "✅通过"
            survive_s2.append(fname)
        elif s1 == "晋级":
            s2 = "未过(S2)"
        def fmt(x):
            return "%.3f/%.3f/%.0f%%" % (x["ic_mean"], x["icir"], x["positive_pct"] * 100) if x else "—"
        lines.append("| %s | %s | %.3f | %s | %s | %s | %s |"
                     % (fname, c.get("direction", ""), full_ic, s1,
                        fmt(tr), ",".join("%s:%s" % (k, v) for k, v in signs.items()), s2))

    lines.append("\n## 漏斗\n")
    lines.append("- 登记: %d" % len(cands))
    lines.append("- S1 晋级: %d" % len(survive_s1))
    lines.append("- S2 通过: %d -> %s" % (len(survive_s2), survive_s2))
    lines.append("- 全期IC/训练段反号或|全期IC|<0.02 视为 S1 淘汰（只淘汰不入选）")

    lines.append("\n## 分阶段明细（全期/训练段/样本外/近8期）\n")
    for c in cands:
        fname = c["id"]
        if fname not in ic_series:
            continue
        seg = seg_stats(ic_series[fname], fname)
        lines.append("### %s\n" % fname)
        lines.append("| 分段 | n | IC均值 | ICIR | 正值占比 | min | max |")
        lines.append("|---|---|---|---|---|---|---|")
        for k, v in seg.items():
            if not v:
                lines.append("| %s | — | — | — | — | — | — |" % k)
                continue
            lines.append("| %s | %d | %.4f | %.3f | %.0f%% | %.3f | %.3f |"
                         % (k, v["n"], v["ic_mean"], v["icir"],
                            v["positive_pct"] * 100, v["min"], v["max"]))
        lines.append("")

    report_path = os.path.join(OUTDIR, "batch_%s_report.md" % batch_id)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    df = pd.DataFrame(ic_records)
    if not df.empty:
        df.to_csv(os.path.join(OUTDIR, "batch_%s_ic.csv" % batch_id),
                  index=False, encoding="utf-8-sig")
    log("\n总用时 %.1fs" % (time.time() - t0))
    log("报告: %s" % report_path)


if __name__ == "__main__":
    main()
