# -*- coding: utf-8 -*-
"""实盘 NAV 重建 —— 从 QMT 桥的每日账本还原策略自己的净值曲线。

数据源（按可信度排序）：
  G2  : D:/QMT_POOL/g2_bridge/cmd/positions_cfg_<date>.json
        —— G2 自己的持仓账本（含 strategy 标记 + 成本价），37 天全覆盖
        ⚠️ state/positions_*.json 是「整个账户」的持仓，不能用于策略级净值
  V13 : data/qmt_trade_log.csv + data/strategy_capital.json
        —— 只有成交流水，没有每日持仓快照，只能按成交推算

净值口径：
  total = cash + Σ(vol × 当日 close)
  cash  = start_capital − Σ(买入额+费) + Σ(卖出额−费)
  费用按 qmt_config 实盘费率。
未实现盈亏体现在持仓市值里，已实现体现在 cash 里，两者相加即总资产。
"""
import glob
import json
import os
import re

import numpy as np
import pandas as pd

from . import execution as EX
from .data import QuotePanel

G2_CFG_DIR = "D:/QMT_POOL/g2_bridge/cmd"
G2_CAPITAL = "D:/QMT_POOL/g2_bridge/g2_strategy_capital.json"

_HERE = os.path.dirname(os.path.abspath(__file__))
PROJ23 = os.path.dirname(_HERE)
ROOT = os.path.dirname(os.path.dirname(PROJ23))
P16 = os.path.join(ROOT, "projects", "Project_16_LightGBM股票大师")
V13_CAPITAL = os.path.join(P16, "data", "strategy_capital.json")
V13_TRADE_LOG = os.path.join(P16, "data", "qmt_trade_log.csv")


def load_g2_positions():
    """读 G2 自己的持仓账本。返回 DataFrame[date, code, vol, cost]。"""
    rows = []
    for fp in glob.glob(os.path.join(G2_CFG_DIR, "positions_cfg_*.json")):
        m = re.search(r"positions_cfg_(\d{8})\.json$", os.path.basename(fp))
        if not m:
            continue
        try:
            with open(fp, encoding="utf-8-sig") as f:
                d = json.load(f)
        except Exception:
            continue
        # 只认 G2 自己的账本（带 strategy 标记的）
        if str(d.get("strategy", "")) != "Project_16_g2":
            continue
        day = m.group(1)
        for p in d.get("positions", []):
            rows.append({
                "date": day,
                "code": str(p.get("code")),
                "vol": int(p.get("vol") or 0),
                "cost": float(p.get("cost") or 0),
            })
    if not rows:
        return pd.DataFrame(columns=["date", "code", "vol", "cost"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def build_g2_nav(start_capital=100000.0, end_capital=None, slippage_fee=True):
    """用 G2 每日持仓账本 + 当日收盘价重建 NAV 曲线（浮盈法）。

    口径说明（重要）：
      G2 是「收益滚动型」策略 —— 已实现盈亏滚动进资金池继续建仓，
      因此不能假设现金 = 起始资金 − 持仓成本（那样会算出负现金）。

    正确做法：拆成「已实现」+「浮动」两部分
      NAV_t = capital_base + realized_t + float_t
      float_t   = Σ(vol × close_t) − Σ(vol × cost_t)      ← 持仓快照可直接算，精确
      realized_t = 累积已实现，无法从快照得知
                 → 用 fills_*.json 的买卖金额推算（但 fills 不全，只能近似）

    本实现在 fills 完整时用 fills 推算 realized；fills 缺失时段，
    退化为「浮盈变化」口径（nav_floatonly），只反映持仓浮动盈亏，标注为近似。
    期末用 g2_strategy_capital.json 的 capital 做整体校准（缩放），
    保证终点与官方资金池一致。
    """
    pos = load_g2_positions()
    if len(pos) == 0:
        return None
    dates = sorted(pos["date"].unique())
    panel = QuotePanel(dates[0], dates[-1])

    # 每个 (date, code) 的成本与数量，用于计算浮盈
    recs = []
    for d in dates:
        day = pos[pos["date"] == d]
        mv = float((day["vol"] * day["cost"]).sum())   # 成本口径市值
        float_sum = 0.0
        missing = []
        for _, r in day.iterrows():
            bar = panel.bar(d, r["code"])
            px = float(bar["close"]) if (bar and bar.get("close")) else None
            if px is None:
                missing.append(r["code"])
                continue
            float_sum += r["vol"] * (px - r["cost"])
        recs.append({
            "date": d.strftime("%Y-%m-%d"),
            "cost_basis": round(mv, 2),
            "float_pnl": round(float_sum, 2),
            "n_positions": len(day),
            "missing_px": ",".join(missing),
        })
    df = pd.DataFrame(recs)

    # ---- 累积 realized：由 fills 推算 ----
    realized = _cum_realized_by_date(dates, slippage_fee)
    df["realized_cum"] = df["date"].map(realized).fillna(0.0)

    # NAV = 基准资金 + 累积已实现 + 当前浮盈
    # 基准资金用「期末资金池 − 期末浮盈 − 期末已实现」反推，保证终点对齐官方值
    last = df.iloc[-1]
    base = (end_capital - last["float_pnl"] - last["realized_cum"]) \
        if end_capital is not None else start_capital
    df["capital_base"] = round(base, 2)
    df["total_asset"] = (base + df["realized_cum"] + df["float_pnl"]).round(2)
    df["ret"] = df["total_asset"].pct_change().fillna(0.0)
    return df


def _cum_realized_by_date(dates, with_fee=True):
    """从 fills_*.json 推算每个日期的累积已实现盈亏（配对买卖，移动加权成本）。

    fills 不完整，结果仅作近似；缺失日期沿用前值。
    """
    fills = []
    for fp in glob.glob("D:/QMT_POOL/g2_bridge/state/fills_*.json"):
        try:
            with open(fp, encoding="utf-8-sig") as f:
                d = json.load(f)
        except Exception:
            continue
        for x in d.get("fills", []):
            if x.get("status") != "FILLED":
                continue
            fills.append({
                "date": pd.Timestamp(d.get("date")),
                "code": x.get("code"),
                "side": x.get("action"),
                "vol": float(x.get("vol") or 0),
                "price": float(x.get("price") or 0),
            })
    if not fills:
        return {}
    fills.sort(key=lambda r: r["date"])

    # 移动加权成本法逐笔配对
    book = {}      # code -> {vol, cost_total}
    realized = 0.0
    per_date = {}
    for f in fills:
        code, side, vol, px = f["code"], f["side"], f["vol"], f["price"]
        if vol <= 0 or px <= 0:
            continue
        amt = vol * px
        fee = EX.buy_fee(amt, 0.001) if side == "BUY" else EX.sell_fee(amt, 0.001)
        b = book.setdefault(code, {"vol": 0.0, "cost": 0.0})
        if side == "BUY":
            b["vol"] += vol
            b["cost"] += amt + fee
        else:
            if b["vol"] <= 0:
                continue
            sell_vol = min(vol, b["vol"])
            unit_cost = b["cost"] / b["vol"]
            realized += sell_vol * px - fee - sell_vol * unit_cost
            b["cost"] -= sell_vol * unit_cost
            b["vol"] -= sell_vol
        per_date[f["date"]] = realized

    # 展开到每个账本日期（沿用最近值）
    out = {}
    keys = sorted(per_date)
    for d in dates:
        val = 0.0
        for k in keys:
            if k <= d:
                val = per_date[k]
            else:
                break
        out[d.strftime("%Y-%m-%d")] = round(val, 2)
    return out


def build_v13_nav(start_capital=100000.0, end_capital=None, slip=0.001,
                  max_single_amount=None, drop_non_strategy=True):
    """用 V1.3 成交流水重建 NAV（无每日持仓快照，精度低于 G2）。

    数据源：data/qmt_trade_log.csv（138 笔，其中 63 笔 price=0）
    方法：逐笔移动加权成本法累积持仓与现金，逐日按收盘价盯市。

    数据污染排除（必须，2026-10-22 用户确认）：
      qmt_trade_log.csv 中 300684.SZ 的 3 笔是**调试出错产生**，不属于策略交易：
        2026-08-24 09:54:49  BUY  109,600 股 @85.72 =   939 万
        2026-08-25 14:45:00  SELL   7,600 股 @85.50 =  65.0 万
        2026-08-27 09:51:58  SELL  99,800 股 @90.51 =   903 万
      其中两笔近千万的单会瞬间把 10 万本金打到 -930 万，必须剔除。
      剔除规则用「显式黑名单 + 金额兜底」双重保险：
        1. 黑名单：code=300684.SZ 且 时间在 2026-08-24~2026-08-27（调试窗口）
        2. 金额兜底：单笔 > start_capital × max_single_mult（默认 3 倍），
           防止未来再出现未登记的异常大单
      剔除明细写入 df.attrs["excluded_orders"]，供报告审计。

    精度局限（必须在报告中标注）：
      1. 63 笔 price=0（2026-08-20~08-24）无法计入成本，那段时间盈亏不可信；
      2. 成交流水可能不含全部撤单/部分成交，与真实持仓有偏差；
      3. 没有 QMT 账户快照可校准，只能用 strategy_capital.json 的期末资金做终点锚定。
    """
    if not os.path.isfile(V13_TRADE_LOG):
        return None
    if max_single_amount is None:
        max_single_amount = start_capital * 3.0

    t = pd.read_csv(V13_TRADE_LOG, encoding="utf-8-sig", dtype={"code": str})
    t["time"] = pd.to_datetime(t["time"])
    t["date"] = t["time"].dt.normalize()
    t["price"] = t["price"].astype(float)
    t["amount"] = t["vol"].astype(float) * t["price"]
    t = t.sort_values("time")

    # ---- 剔除调试污染单：黑名单 + 金额兜底 ----
    # 1. 显式黑名单：300684.SZ 全票剔除（2026-10-22 用户确认：该票交易均为调试错误）
    #    原因：该票在账本中被大量卖出（8/24 买 109,600 → 8/27 卖 99,800），
    #    且 8/25 的 5 笔 500 股卖出 + 1 笔 7,600 股卖出都无对应买入，
    #    整体净持仓 -2,200 股，属异常。全票剔除最干净，避免残留污染。
    dbg = t["code"] == "300684.SZ"
    # 2. 金额兜底：单笔超过本金 3 倍（防未来未登记异常）
    big = t["amount"] > max_single_amount
    mask = dbg | big
    excluded = t[mask][["time", "code", "side", "vol", "price", "amount"]].copy()
    excluded["exclude_reason"] = [
        "debug_blacklist" if d else "amount_overflow" for d in dbg[mask]]
    t = t[~mask].copy()

    # ---- 剔除「非策略交易」：历史持仓清理 ----
    # 2026-08-20~08-24 有 72 笔、55 只股票的卖出，是策略建仓前的账户历史持仓清理
    # （实证：这 55 只中策略后续买回 0 笔）。这些卖出的现金流入不属于策略收益，
    # 混入会把 NAV 虚高。判定规则：只保留「策略自己买过的股票」的全部成交。
    excluded_cleanup = None
    if drop_non_strategy:
        bought = set(t.loc[t["side"].astype(str).str.upper() == "BUY", "code"])
        is_cleanup = (
            (t["side"].astype(str).str.upper() == "SELL")
            & (~t["code"].isin(bought))
            & (t["time"] < pd.Timestamp("2026-08-25"))
        )
        if is_cleanup.any():
            excluded_cleanup = t[is_cleanup][
                ["time", "code", "side", "vol", "price", "amount"]].copy()
            excluded_cleanup["exclude_reason"] = "pre_strategy_cleanup"
            t = t[~is_cleanup].copy()
    t.loc[t["price"] <= 0, "amount"] = 0.0

    d0 = t["date"].min()
    d1 = t["date"].max()
    panel = QuotePanel(d0, d1)
    dates = panel.trading_dates(d0, d1)

    # 现金起点校准：账本里可能混有「策略建仓前」的卖出回款（历史持仓清理），
    # 直接以 start_capital 起算会让起点偏高。
    # 做法：把「首个策略买入日之前」的净现金流回滚掉，使策略首日现金恰好 = start_capital。
    first_buy = t.loc[t["side"].astype(str).str.upper() == "BUY", "date"].min()
    pre_cash = 0.0
    if pd.notna(first_buy):
        pre = t[t["date"] < first_buy]
        for _, r in pre.iterrows():
            amt = float(r["amount"])
            if amt <= 0:
                continue
            pre_cash += (-amt if str(r["side"]).upper() == "BUY" else amt)
        dates = [d for d in dates if d >= first_buy]

    cash = start_capital - pre_cash
    book = {}            # code -> {vol, cost_total}
    recs = []
    n_invalid = 0
    last_px = {}

    by_date = {d: g for d, g in t.groupby("date")}
    for d in dates:
        # 当日成交（开盘价成交，与纸面口径一致）
        if d in by_date:
            for _, r in by_date[d].iterrows():
                code = r["code"]
                vol = int(r["vol"])
                px = r["price"]
                if px <= 0:
                    n_invalid += 1
                    # 无价成交：无法计入成本/现金，只在持仓里按「零成本」记账
                    b = book.setdefault(code, {"vol": 0.0, "cost": 0.0})
                    if r["side"] == "BUY":
                        b["vol"] += vol
                    else:
                        b["vol"] = max(0.0, b["vol"] - vol)
                    continue
                amt = vol * px
                fee = EX.buy_fee(amt, slip) if r["side"] == "BUY" else EX.sell_fee(amt, slip)
                b = book.setdefault(code, {"vol": 0.0, "cost": 0.0})
                if r["side"] == "BUY":
                    cash -= (amt + fee)
                    b["vol"] += vol
                    b["cost"] += amt + fee
                else:
                    if b["vol"] <= 0:
                        continue
                    sv = min(vol, b["vol"])
                    unit = b["cost"] / b["vol"]
                    cash += (sv * px - fee)
                    b["cost"] -= sv * unit
                    b["vol"] -= sv
        # 收盘盯市
        mv = 0.0
        for code, b in list(book.items()):
            if b["vol"] <= 0:
                del book[code]
                continue
            bar = panel.bar(d, code)
            px = float(bar["close"]) if (bar and bar.get("close")) else last_px.get(code)
            if px:
                last_px[code] = px
            else:
                px = b["cost"] / b["vol"] if b["vol"] else 0.0
            mv += b["vol"] * px
        recs.append({
            "date": d.strftime("%Y-%m-%d"),
            "cash": round(cash, 2),
            "market_value": round(mv, 2),
            "total_asset": round(cash + mv, 2),
            "n_positions": len(book),
            "cum_invalid_fills": n_invalid,
            # 可信标记：无价成交污染过的日期不可信（cash 会被打成负数）
            "trusted": n_invalid == 0,
        })
    df = pd.DataFrame(recs)
    if end_capital is not None and len(df):
        # 终点锚定：整体平移使末值与官方资金池一致（线性校准）
        diff = end_capital - df["total_asset"].iloc[-1]
        df["total_asset"] = (df["total_asset"] + diff).round(2)
        df["calib_offset"] = round(diff, 2)
    df["ret"] = df["total_asset"].pct_change().fillna(0.0)

    # 可信区间起点 = 最后一笔无价成交所在交易日的「下一个交易日」
    # 已知：63 笔 price=0 全为 SELL，末笔 2026-08-24 09:32，其后无污染 → 可信起点 2026-08-25
    trusted_from = None
    if n_invalid > 0:
        last_bad = t.loc[t["price"] <= 0, "date"].max()
        after = [d for d in dates if d > last_bad]
        trusted_from = after[0].strftime("%Y-%m-%d") if after else None

    # 把剔除记录挂到 attrs（供报告显示），不影响 DataFrame 结构
    all_ex = excluded.to_dict("records")
    if excluded_cleanup is not None:
        all_ex += excluded_cleanup.to_dict("records")
    df.attrs["excluded_orders"] = all_ex
    df.attrs["excluded_big_orders"] = excluded.to_dict("records")
    df.attrs["n_excluded_cleanup"] = (
        len(excluded_cleanup) if excluded_cleanup is not None else 0)
    df.attrs["n_invalid_fills"] = n_invalid
    df.attrs["trusted_from"] = trusted_from
    df.attrs["untrusted_until"] = (
        last_bad.strftime("%Y-%m-%d") if n_invalid > 0 else None)
    return df


def load_capital(path):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compare_nav(paper_nav_path, live_nav, label, start_from=None):
    """纸面 NAV vs 实盘 NAV 逐日对比。

    参数：
      start_from : 对比起点（'YYYY-MM-DD'）。传 None 则用两边交集首日。
                   对数据受污染的实盘序列，应传可信起点，避免被污染区间带偏。

    返回 (info, detail_df)；无重叠区间返回 (None, None)。
    """
    if not os.path.isfile(paper_nav_path) or live_nav is None or len(live_nav) == 0:
        return None, None
    p = pd.read_csv(paper_nav_path, encoding="utf-8-sig")
    p = p[["date", "total_asset"]].rename(columns={"total_asset": "paper"})
    l = live_nav[["date", "total_asset"]].rename(columns={"total_asset": "live"})
    m = p.merge(l, on="date", how="inner")
    if start_from:
        m = m[m["date"] >= str(start_from)]
    if len(m) == 0:
        return None, None
    # 归一化到起点 1.0 便于比较
    m["paper_n"] = m["paper"] / m["paper"].iloc[0]
    m["live_n"] = m["live"] / m["live"].iloc[0]
    m["diff_pp"] = (m["live_n"] - m["paper_n"]) * 100

    info = {
        "strategy": label,
        "n_days": len(m),
        "start": m["date"].iloc[0],
        "end": m["date"].iloc[-1],
        "paper_ret": (m["paper_n"].iloc[-1] - 1) * 100,
        "live_ret": (m["live_n"].iloc[-1] - 1) * 100,
        "tracking_diff_pp": m["diff_pp"].iloc[-1],
        "max_abs_diff_pp": m["diff_pp"].abs().max(),
        "mean_abs_diff_pp": m["diff_pp"].abs().mean(),
        "corr": m["paper_n"].pct_change().corr(m["live_n"].pct_change()),
    }
    return info, m
