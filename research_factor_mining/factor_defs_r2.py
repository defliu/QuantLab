# coding=utf-8
"""因子挖掘正式批次 R2 - 候选因子实现（40 条）

设计来源：DE 第二轮（data/schedules/de_factor_mining_r2_20260916.md）：
  A-北向自身历史分位族(8) / A-龙虎榜席位结构族(7, 含 top_inst 已落盘) /
  A-研报修正分位族(8) / B-财务科目背离族(8) / 价格行为族(9)

run_screen.py 会挂载本模块的所有非私有函数到 FDS 命名空间。
ctx 扩展见 run_screen.py（lhb_amt_rate/buy_ratio/inst_net/inst_flag/res_tp/res_eps/
pctchg_by_code/turnover_by_code/limit_flag_by_code/open_by_code/amount_by_code/list_date）。
"""
import numpy as np
import pandas as pd

from factor_defs_pilot import _cand_only, _fill0, _lhb_win, _res_win


def _north_hist_pct(date, ctx, field, win):
    """自身历史分位：当前值在近 win 交易日序列中的分位（0~1）。"""
    df = ctx[field]
    t = pd.Timestamp(date)
    sub = df.loc[:t]
    if len(sub) < win:
        return None
    last = sub.iloc[-1]
    hist = sub.iloc[-win:]
    return (hist <= last).mean(axis=0)


def _north_hist_z(date, ctx, field, win):
    df = ctx[field]
    t = pd.Timestamp(date)
    sub = df.loc[:t]
    if len(sub) < win:
        return None
    last = sub.iloc[-1]
    hist = sub.iloc[-win:]
    mu = hist.mean(axis=0)
    sd = hist.std(axis=0)
    return (last - mu) / sd.replace(0, np.nan)


def _north_series(date, ctx, field):
    df = ctx[field]
    t = pd.Timestamp(date)
    sub = df.loc[:t]
    if len(sub) == 0:
        return None
    return sub


# ================= A-北向：自身历史分位族 =================
def hk_ratio_pct250(date, dd, cand, ctx):
    return _cand_only(_north_hist_pct(date, ctx, "north_ratio", 250), cand)


def hk_ratio_z250(date, dd, cand, ctx):
    return _cand_only(_north_hist_z(date, ctx, "north_ratio", 250), cand)


def hk_ratio_chg20_pct(date, dd, cand, ctx):
    """20 日增持幅度相对自身历史(252日窗口)增持序列的分位"""
    df = ctx["north_ratio"]
    t = pd.Timestamp(date)
    sub = df.loc[:t]
    if len(sub) < 253:
        return None
    chg_now = sub.iloc[-1] - sub.iloc[-21]
    chg_hist = sub.iloc[-252:-20].diff(20)
    pct = (chg_hist <= chg_now).mean(axis=0)
    return _cand_only(pct, cand)


def hk_ratio_chg60_pct(date, dd, cand, ctx):
    df = ctx["north_ratio"]
    t = pd.Timestamp(date)
    sub = df.loc[:t]
    if len(sub) < 253:
        return None
    chg_now = sub.iloc[-1] - sub.iloc[-61]
    chg_hist = sub.iloc[-252:-60].diff(60)
    pct = (chg_hist <= chg_now).mean(axis=0)
    return _cand_only(pct, cand)


def hk_vol_pct250(date, dd, cand, ctx):
    return _cand_only(_north_hist_pct(date, ctx, "north_vol", 250), cand)


def hk_ratio_from_peak(date, dd, cand, ctx):
    """距自身历史持股峰值回撤：last / running_max - 1（负=仍低于峰值）"""
    df = ctx["north_ratio"]
    t = pd.Timestamp(date)
    sub = df.loc[:t]
    if len(sub) < 60:
        return None
    last = sub.iloc[-1]
    peak = sub.iloc[-252:].max(axis=0) if len(sub) >= 252 else sub.max(axis=0)
    return _cand_only((last / peak.replace(0, np.nan) - 1.0), cand)


def hk_hold_days(date, dd, cand, ctx):
    """连续在册天数（从最近日往前数连续非 NaN 交易日数）"""
    df = ctx["north_ratio"]
    t = pd.Timestamp(date)
    sub = df.loc[:t]
    if len(sub) == 0:
        return None
    notna = sub.notna()
    out = {}
    for c in cand:
        if c not in notna.columns:
            continue
        s = notna[c].iloc[::-1]
        cnt = int(s.idxmax()) if s.any() else 0  # 首次出现 False 的位置
        n = int((~s).cumsum().min() if (~s).any() else len(s))
        out[c] = float(n)
    return _cand_only(pd.Series(out), cand)


def hk_ratio_stability(date, dd, cand, ctx):
    """近 60 日持股比例标准差（高=交易盘，负向）"""
    df = ctx["north_ratio"]
    t = pd.Timestamp(date)
    sub = df.loc[:t]
    if len(sub) < 60:
        return None
    seg = sub.iloc[-60:]
    return _cand_only(seg.std(axis=0), cand)


# ================= A-龙虎榜：席位结构族 =================
def lhb_amount_rate_20d(date, dd, cand, ctx):
    s = _lhb_win(date, ctx, "lhb_amt_rate", 20, "mean")
    return _fill0(s, cand)


def lhb_buy_ratio_20d(date, dd, cand, ctx):
    s = _lhb_win(date, ctx, "lhb_buy_ratio", 20, "mean")
    return _fill0(s, cand)


def lhb_netrate_60d(date, dd, cand, ctx):
    s = _lhb_win(date, ctx, "lhb_rate", 60, "mean")
    return _fill0(s, cand)


def lhb_times_60d(date, dd, cand, ctx):
    s = _lhb_win(date, ctx, "lhb_flag", 60, "count")
    return _fill0(s, cand)


def lhb_net_mv_60d(date, dd, cand, ctx):
    s = _lhb_win(date, ctx, "lhb_net", 60, "sum")
    if s is None:
        return None
    mv = dd.loc[cand]["circ_mv"] * 10000.0
    ratio = (s / mv.replace(0, np.nan)).reindex(cand).fillna(0.0)
    return ratio.dropna() if ratio.notna().any() else None


def lhb_inst_net_20d(date, dd, cand, ctx):
    s = _lhb_win(date, ctx, "inst_net", 20, "sum")
    return _fill0(s, cand)


def lhb_inst_flag_20d(date, dd, cand, ctx):
    s = _lhb_win(date, ctx, "inst_flag", 20, "count")
    return _fill0((s > 0).astype(float), cand)


# ================= A-研报：修正分位族 =================
def _res_series_hist(date, ctx, field, win):
    df = ctx[field]
    t = pd.Timestamp(date)
    sub = df[(df.index > t - pd.Timedelta(days=win)) & (df.index <= t)]
    return sub


def res_rating_pct(date, dd, cand, ctx):
    """当前评级在自身两年(720日)评级序列中的分位（评级数 1=买..5=卖，值小=看多）"""
    df = ctx["res_rate"]
    t = pd.Timestamp(date)
    sub = df[(df.index > t - pd.Timedelta(days=720)) & (df.index <= t)]
    if len(sub) < 5:
        return None
    last = sub.iloc[-1]
    pct = (sub <= last).mean(axis=0)
    return _cand_only(pct, cand)


def res_updown_120d(date, dd, cand, ctx):
    """评级变动净上调动量：-(末值-初值)（评级数下降=上调=看多）"""
    df = ctx["res_rate"]
    t = pd.Timestamp(date)
    sub = df[(df.index > t - pd.Timedelta(days=120)) & (df.index <= t)]
    if len(sub) < 2:
        return None
    chg = -(sub.iloc[-1] - sub.iloc[0])
    return _cand_only(chg, cand)


def res_tp_ret_60d(date, dd, cand, ctx):
    """目标价隐含收益：近60日 tp 均值 / 现价 - 1"""
    s = _res_win(date, ctx, "res_tp", 60, "mean")
    if s is None:
        return None
    close = dd.loc[cand]["close"]
    ret = (s / close.replace(0, np.nan) - 1.0).reindex(cand)
    return _cand_only(ret, cand)


def res_tp_chg_120d(date, dd, cand, ctx):
    """目标价 120 日修正：末值/初值 - 1"""
    df = ctx["res_tp"]
    if df is None:
        return None
    t = pd.Timestamp(date)
    sub = df[(df.index > t - pd.Timedelta(days=120)) & (df.index <= t)]
    if len(sub) < 2:
        return None
    chg = sub.iloc[-1] / sub.iloc[0].replace(0, np.nan) - 1.0
    return _cand_only(chg, cand)


def res_eps_chg_120d(date, dd, cand, ctx):
    """EPS 预测 120 日修正：末值/初值 - 1"""
    df = ctx["res_eps"]
    if df is None:
        return None
    t = pd.Timestamp(date)
    sub = df[(df.index > t - pd.Timedelta(days=120)) & (df.index <= t)]
    if len(sub) < 2:
        return None
    chg = sub.iloc[-1] / sub.iloc[0].replace(0, np.nan) - 1.0
    return _cand_only(chg, cand)


def res_count_season(date, dd, cand, ctx):
    """覆盖数同比变化：近60日 - 去年同期近60日"""
    df = ctx["res_cnt"]
    t = pd.Timestamp(date)
    cur = df[(df.index > t - pd.Timedelta(days=60)) & (df.index <= t)].sum()
    prev = df[(df.index > t - pd.Timedelta(days=425)) & (df.index <= t - pd.Timedelta(days=365))].sum()
    return _fill0(cur - prev, cand)


def res_rating_disp_60d(date, dd, cand, ctx):
    """60 日评级分歧度：窗口内评级序列的标准差（高=分歧大，负向）"""
    df = ctx["res_rate"]
    t = pd.Timestamp(date)
    sub = df[(df.index > t - pd.Timedelta(days=60)) & (df.index <= t)]
    if len(sub) < 3:
        return None
    return _cand_only(sub.std(axis=0), cand)


def res_init_cover_120d(date, dd, cand, ctx):
    """120 日内首次覆盖哑变量：该股历史上首篇研报落在近 120 日内"""
    df = ctx["res_cnt"]
    t = pd.Timestamp(date)
    out = {}
    for c in cand:
        if c not in df.columns:
            out[c] = 0.0
            continue
        ser = df[c].dropna()
        if len(ser) == 0:
            out[c] = 0.0
            continue
        first = ser.index[0]
        out[c] = 1.0 if first > t - pd.Timedelta(days=120) else 0.0
    return _cand_only(pd.Series(out), cand)


# ================= B-财务：科目背离族 =================
def _fin(date, ctx, field):
    w = ctx["fin_wide"].get(field)
    if w is None:
        return None
    t = pd.Timestamp(date)
    if t not in w.index:
        return None
    return w.loc[t]


def fin_rev_np_div(date, dd, cand, ctx):
    """营收增速 - 净利增速（增收不增利，负向）"""
    a = _fin(date, ctx, "or_yoy")
    b = _fin(date, ctx, "netprofit_yoy")
    if a is None or b is None:
        return None
    return _cand_only(a - b, cand)


def fin_rev_np_gapsign(date, dd, cand, ctx):
    """营收增 & 净利降 背离哑变量"""
    a = _fin(date, ctx, "or_yoy")
    b = _fin(date, ctx, "netprofit_yoy")
    if a is None or b is None:
        return None
    gap = ((a > 0) & (b < 0)).astype(float)
    return _cand_only(gap, cand)


def fin_np_ocf_div(date, dd, cand, ctx):
    """净利增速 - 现金流增速（Sloan 应计，负向）"""
    a = _fin(date, ctx, "netprofit_yoy")
    b = _fin(date, ctx, "ocf_yoy")
    if a is None or b is None:
        return None
    return _cand_only(a - b, cand)


def fin_np_ocf_div_q(date, dd, cand, ctx):
    """单季净利同比 - 现金流同比（单季版应计近似）"""
    a = _fin(date, ctx, "q_netprofit_yoy")
    b = _fin(date, ctx, "ocf_yoy")
    if a is None or b is None:
        return None
    return _cand_only(a - b, cand)


def fin_rev_asset_div(date, dd, cand, ctx):
    """总资产增速 - 营收增速（低效扩产/商誉，负向）"""
    a = _fin(date, ctx, "assets_yoy")
    b = _fin(date, ctx, "or_yoy")
    if a is None or b is None:
        return None
    return _cand_only(a - b, cand)


def fin_margin_trend(date, dd, cand, ctx):
    """毛利率同比变动：当前毛利率 - 12 个月前（12 调仓期≈1年）"""
    w = ctx["fin_wide"].get("gross_margin")
    if w is None:
        return None
    t = pd.Timestamp(date)
    idx = w.index
    pos = idx.get_loc(t)
    if pos < 12:
        return None
    cur = w.iloc[pos]
    prev = w.iloc[pos - 12]
    return _cand_only(cur - prev, cand)


def fin_roe_stability(date, dd, cand, ctx):
    """近 8 调仓期 ROE 标准差（低=盈利稳定，正向）"""
    w = ctx["fin_wide"].get("roe")
    if w is None:
        return None
    t = pd.Timestamp(date)
    pos = w.index.get_loc(t)
    if pos < 8:
        return None
    seg = w.iloc[pos - 8:pos + 1]
    return _cand_only(-seg.std(axis=0), cand)   # 取负：值大=稳定


def fin_q_np_annual(date, dd, cand, ctx):
    """单季净利同比 - 累计净利同比（单季快于累计=近期加速；异常大=季节性扭曲）"""
    a = _fin(date, ctx, "q_netprofit_yoy")
    b = _fin(date, ctx, "netprofit_yoy")
    if a is None or b is None:
        return None
    return _cand_only(a - b, cand)


# ================= 价格行为族（daily 主库衍生，零新数据） =================
def _win_last(date, ser_by_code, n):
    """取 code 序列在 date 前的近 n 个值，返回 {code: Series} 或 None"""
    t = pd.Timestamp(date)
    out = {}
    for c, s in ser_by_code.items():
        if s is None or len(s) == 0:
            continue
        idx = s.index
        pos = idx.searchsorted(t)
        if pos < 2:
            continue
        seg = s.iloc[max(0, pos - n):pos]
        if len(seg) == 0:
            continue
        out[c] = seg
    return out


def px_max20(date, dd, cand, ctx):
    out = {}
    w = _win_last(date, ctx["pctchg_by_code"], 20)
    for c, seg in w.items():
        if c in cand and len(seg) > 0:
            out[c] = float(seg.max())
    return _cand_only(pd.Series(out), cand)


def px_limitup_cnt20(date, dd, cand, ctx):
    out = {}
    w = _win_last(date, ctx["limit_flag_by_code"], 20)
    for c, seg in w.items():
        if c in cand:
            out[c] = float(seg.sum())
    return _cand_only(pd.Series(out), cand)


def px_overnight_20d(date, dd, cand, ctx):
    """近20日隔夜收益均值 = open/prev_close - 1（用 close.shift(1) 近似 prev_close）"""
    out = {}
    t = pd.Timestamp(date)
    for c in cand:
        o = ctx["open_by_code"].get(c)
        cl = ctx["close_by_code"].get(c)
        if o is None or cl is None or len(o) < 2:
            continue
        pos = o.index.searchsorted(t)
        if pos < 2:
            continue
        oo = o.iloc[max(0, pos - 20):pos]
        pc = cl.iloc[max(0, pos - 21):pos - 1]
        n = min(len(oo), len(pc))
        if n < 5:
            continue
        oo, pc = oo.iloc[-n:], pc.iloc[-n:]
        ret = oo.values / pc.values - 1.0
        out[c] = float(np.nanmean(ret))
    return _cand_only(pd.Series(out), cand)


def px_intraday_20d(date, dd, cand, ctx):
    """近20日日内收益均值 = close/open - 1"""
    out = {}
    t = pd.Timestamp(date)
    for c in cand:
        o = ctx["open_by_code"].get(c)
        cl = ctx["close_by_code"].get(c)
        if o is None or cl is None or len(o) < 2:
            continue
        pos = o.index.searchsorted(t)
        if pos < 2:
            continue
        oo = o.iloc[max(0, pos - 20):pos]
        cc = cl.iloc[max(0, pos - 20):pos]
        n = min(len(oo), len(cc))
        if n < 5:
            continue
        ret = cc.values[-n:] / oo.values[-n:] - 1.0
        out[c] = float(np.nanmean(ret))
    return _cand_only(pd.Series(out), cand)


def px_turn_pct252(date, dd, cand, ctx):
    """20 日均换手在自身 252 日换手序列中的分位"""
    out = {}
    t = pd.Timestamp(date)
    for c in cand:
        s = ctx["turnover_by_code"].get(c)
        if s is None or len(s) < 252:
            continue
        pos = s.index.searchsorted(t)
        if pos < 252:
            continue
        hist = s.iloc[pos - 252:pos]
        cur20 = s.iloc[max(0, pos - 20):pos].mean()
        if np.isnan(cur20):
            continue
        out[c] = float((hist <= cur20).mean())
    return _cand_only(pd.Series(out), cand)


def px_amt_ratio_20d(date, dd, cand, ctx):
    """20 日均成交额 / 流通市值"""
    out = {}
    t = pd.Timestamp(date)
    mv = dd.loc[cand]["circ_mv"] * 10000.0
    for c in cand:
        s = ctx["amount_by_code"].get(c)
        if s is None or len(s) < 2:
            continue
        pos = s.index.searchsorted(t)
        if pos < 2:
            continue
        seg = s.iloc[max(0, pos - 20):pos]
        if len(seg) < 5:
            continue
        m = mv.get(c)
        if m is None or m <= 0:
            continue
        out[c] = float(seg.mean() / m)
    return _cand_only(pd.Series(out), cand)


def px_vol_std20(date, dd, cand, ctx):
    out = {}
    w = _win_last(date, ctx["pctchg_by_code"], 20)
    for c, seg in w.items():
        if c in cand and len(seg) >= 5:
            out[c] = float(seg.std())
    return _cand_only(pd.Series(out), cand)


def px_price_level(date, dd, cand, ctx):
    """绝对股价（真实价，panel close 为不复权价）"""
    s = dd.loc[cand]["close"]
    return _cand_only(s, cand)


def px_listed_new(date, dd, cand, ctx):
    """上市未满 250 交易日哑变量（list_date map）"""
    t = pd.Timestamp(date)
    ld = ctx.get("list_date", {})
    out = {}
    for c in cand:
        d = ld.get(c)
        if d is None or pd.isna(d):
            out[c] = 0.0
            continue
        days = (t - pd.Timestamp(d)).days
        out[c] = 1.0 if days <= 365 else 0.0   # 近似 250 交易日 ≈ 365 自然日
    return _cand_only(pd.Series(out), cand)
