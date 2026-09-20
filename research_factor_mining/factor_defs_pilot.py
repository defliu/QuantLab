# coding=utf-8
"""因子挖掘试点批 - 候选因子实现（方向 A 另类数据 + 方向 B 财务组合）

每个函数签名：fn(date, dd, cand, ctx) -> Series(index=code) 或 None
ctx 预计算面板见 run_screen.py：
  - fin_wide: {field: DataFrame(index=调仓期, columns=code)} PIT
  - north_ratio / north_vol: DataFrame(index=交易日, columns=code)
  - lhb_net / lhb_rate / lhb_flag: 龙虎榜事件面板（date x code，稀疏）
  - res_cnt / res_rate: 研报事件面板（date x code，稀疏）
  - close_by_code: {code: Series(close)}
"""
import numpy as np
import pandas as pd


def _cand_only(s, cand):
    if s is None:
        return None
    s = s.reindex(cand)
    return s.dropna() if s.notna().any() else None


# ============================================================
# 方向 A-1：北向持仓（横截面日频面板）
# ============================================================
def _north(date, ctx, field, n=None):
    df = ctx[field]
    sub = df.loc[:pd.Timestamp(date)]
    if len(sub) == 0:
        return None
    if n is None:
        return sub.iloc[-1]
    if len(sub) < n + 1:
        return None
    return sub.iloc[-1] - sub.iloc[-1 - n]


def hk_ratio_level(date, dd, cand, ctx):
    return _cand_only(_north(date, ctx, "north_ratio"), cand)


def hk_ratio_chg_5d(date, dd, cand, ctx):
    return _cand_only(_north(date, ctx, "north_ratio", 5), cand)


def hk_ratio_chg_20d(date, dd, cand, ctx):
    return _cand_only(_north(date, ctx, "north_ratio", 20), cand)


def hk_ratio_chg_60d(date, dd, cand, ctx):
    return _cand_only(_north(date, ctx, "north_ratio", 60), cand)


def hk_ratio_20d_avg(date, dd, cand, ctx):
    df = ctx["north_ratio"]
    sub = df.loc[:pd.Timestamp(date)]
    if len(sub) < 20:
        return None
    return _cand_only(sub.iloc[-20:].mean(), cand)


def hk_vol_chg_20d(date, dd, cand, ctx):
    df = ctx["north_vol"]
    sub = df.loc[:pd.Timestamp(date)]
    if len(sub) < 21:
        return None
    cur = sub.iloc[-1]
    prev = sub.iloc[-21]
    chg = cur / prev.replace(0, np.nan) - 1.0
    return _cand_only(chg, cand)


def hk_ratio_accel(date, dd, cand, ctx):
    """20 日变动 - 5 日变动：增持加速（近期加速优于远期）"""
    df = ctx["north_ratio"]
    sub = df.loc[:pd.Timestamp(date)]
    if len(sub) < 61:
        return None
    a = sub.iloc[-1] - sub.iloc[-21]      # 近20日变动
    b = sub.iloc[-1] - sub.iloc[-6]       # 近5日变动
    return _cand_only(a - b, cand)


# ============================================================
# 方向 A-2：龙虎榜（事件型稀疏）
# ============================================================
def _lhb_win(date, ctx, col, days, agg):
    df = ctx[col]
    t = pd.Timestamp(date)
    sub = df[(df.index > t - pd.Timedelta(days=days)) & (df.index <= t)]
    if len(sub) == 0:
        return None
    if agg == "sum":
        return sub.sum()
    if agg == "mean":
        return sub.mean()
    if agg == "count":
        return sub.sum()          # flag 0/1，求和=上榜次数（非 notna 计数）
    return None


def _fill0(s, cand):
    """事件型因子：窗口内无事件的股票填 0，保持横截面完整。"""
    if s is None:
        return None
    s = s.reindex(cand).fillna(0.0)
    return s.dropna() if s.notna().any() else None


def lhb_net_20d(date, dd, cand, ctx):
    s = _lhb_win(date, ctx, "lhb_net", 20, "sum")
    return _fill0(s, cand)


def lhb_net_60d(date, dd, cand, ctx):
    s = _lhb_win(date, ctx, "lhb_net", 60, "sum")
    return _fill0(s, cand)


def lhb_times_20d(date, dd, cand, ctx):
    s = _lhb_win(date, ctx, "lhb_flag", 20, "count")
    return _fill0(s, cand)


def lhb_netrate_20d(date, dd, cand, ctx):
    s = _lhb_win(date, ctx, "lhb_rate", 20, "mean")
    return _fill0(s, cand)


def lhb_recent_days(date, dd, cand, ctx):
    """距最近一次上榜的交易日数（值小=曝光新近）；返回负值使『值大=更新近』，未上榜填 -365"""
    df = ctx["lhb_flag"]
    t = pd.Timestamp(date)
    sub = df[df.index <= t]
    if len(sub) == 0:
        return None
    last = sub.iloc[-1]
    out = {}
    for c in cand:
        v = last.get(c, 0)
        if v and v > 0:
            out[c] = 1.0
        else:
            hist = sub[c].replace(0, np.nan).dropna()
            if len(hist) == 0:
                out[c] = -365.0
                continue
            days = (t - hist.index[-1]).days
            out[c] = -float(days)
    return _fill0(pd.Series(out), cand)


def lhb_net_mv_20d(date, dd, cand, ctx):
    """近20日净买入额 / 流通市值（相对规模，过滤大盘股天然净额大）"""
    s = _lhb_win(date, ctx, "lhb_net", 20, "sum")
    if s is None:
        return None
    mv = dd.loc[cand]["circ_mv"] * 10000.0   # 万元 -> 元
    ratio = (s / mv.replace(0, np.nan)).reindex(cand).fillna(0.0)
    return ratio.dropna() if ratio.notna().any() else None


# ============================================================
# 方向 A-3：研报（事件型稀疏）
# ============================================================
def _res_win(date, ctx, col, days, agg):
    df = ctx[col]
    t = pd.Timestamp(date)
    sub = df[(df.index > t - pd.Timedelta(days=days)) & (df.index <= t)]
    if len(sub) == 0:
        return None
    if agg == "sum":
        return sub.sum()
    if agg == "mean":
        return sub.mean()
    return None


def res_count_60d(date, dd, cand, ctx):
    return _fill0(_res_win(date, ctx, "res_cnt", 60, "sum"), cand)


def res_count_120d(date, dd, cand, ctx):
    return _fill0(_res_win(date, ctx, "res_cnt", 120, "sum"), cand)


def res_count_chg_60d(date, dd, cand, ctx):
    """近60日研报数 - 前60日研报数（覆盖增加）"""
    t = pd.Timestamp(date)
    df = ctx["res_cnt"]
    cur = df[(df.index > t - pd.Timedelta(days=60)) & (df.index <= t)].sum()
    prev = df[(df.index > t - pd.Timedelta(days=120)) & (df.index <= t - pd.Timedelta(days=60))].sum()
    return _fill0(cur - prev, cand)


def res_rating_60d(date, dd, cand, ctx):
    s = _res_win(date, ctx, "res_rate", 60, "mean")
    return _cand_only(s, cand)


def res_rating_chg(date, dd, cand, ctx):
    """最近60日评级均值 - 前60日评级均值（评级上调=负值更看多 -> 取负使值大=上调）"""
    t = pd.Timestamp(date)
    df = ctx["res_rate"]
    cur = df[(df.index > t - pd.Timedelta(days=60)) & (df.index <= t)].mean()
    prev = df[(df.index > t - pd.Timedelta(days=120)) & (df.index <= t - pd.Timedelta(days=60))].mean()
    chg = cur - prev
    return _cand_only(-chg, cand)


def res_new_cover_60d(date, dd, cand, ctx):
    """近60日是否有研报（覆盖事件，哑变量）"""
    s = _res_win(date, ctx, "res_cnt", 60, "sum")
    if s is None:
        return None
    return _fill0((s > 0).astype(float), cand)


def res_rating_level(date, dd, cand, ctx):
    """近30日评级均值（rating_num 1=买入..5=卖出；取负使值大=更看多）"""
    s = _res_win(date, ctx, "res_rate", 30, "mean")
    if s is None:
        return None
    return _cand_only(-s, cand)


# ============================================================
# 方向 B：财务（PIT 宽表，高=质量好/成长好）
# ============================================================
def _fin(date, ctx, field):
    w = ctx["fin_wide"].get(field)
    if w is None:
        return None
    t = pd.Timestamp(date)
    if t not in w.index:
        return None
    return w.loc[t]


def fin_acc_quality(date, dd, cand, ctx):
    """扣非净利占比（dtprofit_to_profit，越大盈余质量越高）"""
    return _cand_only(_fin(date, ctx, "dtprofit_to_profit"), cand)


def fin_ocf_to_profit(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "ocf_to_profit"), cand)


def fin_ocf_to_sales(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "ocf_to_or"), cand)


def fin_debt_to_asset(date, dd, cand, ctx):
    """资产负债率（负向，取负使值大=更稳健）"""
    s = _fin(date, ctx, "debt_to_assets")
    if s is None:
        return None
    return _cand_only(-s, cand)


def fin_current_ratio(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "current_ratio"), cand)


def fin_quick_ratio(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "quick_ratio"), cand)


def fin_netprofit_yoy(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "netprofit_yoy"), cand)


def fin_dt_netprofit_yoy(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "dt_netprofit_yoy"), cand)


def fin_rev_yoy(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "or_yoy"), cand)


def fin_ocf_yoy(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "ocf_yoy"), cand)


def fin_roe_yoy(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "roe_yoy"), cand)


def fin_assets_yoy(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "assets_yoy"), cand)


def fin_q_gr_qoq(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "q_gr_qoq"), cand)


def fin_q_roe(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "q_roe"), cand)


def fin_q_gs_margin(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "q_gsprofit_margin"), cand)


def fin_q_np_margin(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "q_netprofit_margin"), cand)


def fin_q_sales_qoq(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "q_sales_qoq"), cand)


def fin_q_np_qoq(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "q_netprofit_qoq"), cand)


def fin_q_dtprofit_ratio(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "q_dtprofit_to_profit"), cand)


def fin_q_ocf_to_sales(date, dd, cand, ctx):
    return _cand_only(_fin(date, ctx, "q_ocf_to_sales"), cand)
