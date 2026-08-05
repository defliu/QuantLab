# coding=utf-8
"""多因子IC策略 · QMT单文件部署版 v2（本地财务数据 + QMT实时价格）
核心改动：init 阶段从本地 astock parquet 加载 pb/pe_ttm/circ_mv/roe，
         handlebar 用 QMT API 取实时价格，两路合并计算因子。
文件：strategy_mfic_v2.py
"""
import math
import numpy as np
import pandas as pd
import json
import os
from datetime import datetime, timedelta

# ============================================================
# 配置常量
# ============================================================
CAPITAL = 100000
MAX_WEIGHT = 0.02
STOP_LOSS = -0.12
TOP_N = 80
FREQ_MONTHS = 2
MV_MIN = 0
MV_MAX = 300000        # 万元 → 0-30亿
AMOUNT_MIN = 20000     # 千元 → 2000万
FACTOR_WEIGHTS = {"BP": 0.30, "reversal_1m": 0.25, "volatility_60d": 0.25, "ROE": 0.20}
POSITIONS_FILE = "D:/QMT_POOL/mfic_positions.json"
TRADES_FILE = "D:/QMT_POOL/mfic_trades.txt"
ACCOUNT_ID = '67014907'   # QMT 资金账号

# 本地 astock parquet 路径
ASTOCK_DAILY = "E:/astock/daily/stock_daily.parquet"

# ============================================================
# 工具函数
# ============================================================

def _normalize(series, reverse=False):
    lo = series.quantile(0.01)
    hi = series.quantile(0.99)
    s = series.clip(lo, hi)
    s = (s - s.mean()) / s.std(ddof=0)
    if reverse:
        s = -s
    return s


def _read_positions():
    if not os.path.exists(POSITIONS_FILE):
        return {}
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_positions(positions):
    try:
        os.makedirs(os.path.dirname(POSITIONS_FILE), exist_ok=True)
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[mfic] 写入持仓失败: %s" % e)


def _get_market_time(C):
    try:
        tick_time = C.get_tick_timetag()
        if tick_time and tick_time > 0:
            return datetime.fromtimestamp(tick_time)
    except Exception:
        pass
    try:
        bar_time = C.get_bar_timetag(C.barpos)
        if bar_time and bar_time > 0:
            return datetime.fromtimestamp(bar_time)
    except Exception:
        pass
    return datetime.now()


def _is_rebalance_day(C, today):
    try:
        month = today.month
        if month % 2 != 0:
            return False
        if month == 12:
            next_first = today.replace(year=today.year+1, month=1, day=1)
        else:
            next_first = today.replace(month=month+1, day=1)
        last_day = next_first - timedelta(days=1)
        tds = C.get_trading_dates(
            today.replace(day=1).strftime("%Y%m%d"),
            last_day.strftime("%Y%m%d"),
            "1d"
        )
        if tds and len(tds) > 0 and tds[-1] == today.strftime("%Y%m%d"):
            return True
    except Exception:
        pass
    return False


# ============================================================
# 本地财务数据加载（核心改动）
# ============================================================

def _load_financial_data():
    """从本地 astock parquet 加载财务数据。

    Returns: dict {ts_code: {pb, pe_ttm, circ_mv, roe, amount, pct_chg}}
    """
    print("[mfic] 加载本地财务数据...")
    try:
        if not os.path.exists(ASTOCK_DAILY):
            print("[mfic] astock parquet 不存在: %s" % ASTOCK_DAILY)
            return {}

        # 只读需要的列
        import pyarrow.parquet as _pq
        present = set(_pq.ParquetFile(ASTOCK_DAILY).schema_arrow.names)
        need_cols = ["trade_date", "ts_code", "close", "open", "pb", "pe_ttm",
                     "circ_mv", "amount", "pct_chg", "turnover_rate"]
        read_cols = [c for c in need_cols if c in present]

        df = pd.read_parquet(ASTOCK_DAILY, columns=read_cols)

        # 取最近一个交易日的数据
        trade_dates = sorted(df["trade_date"].unique())
        latest_date = trade_dates[-1]
        latest = df[df["trade_date"] == latest_date].copy()
        print("[mfic] 最新数据日期: %s, 股票数: %d" % (latest_date, len(latest)))

        # 构建查找表 {ts_code: {pb, pe_ttm, circ_mv, roe}}
        fin_data = {}
        for _, row in latest.iterrows():
            code = row["ts_code"]
            pb = row.get("pb", np.nan)
            pe_ttm = row.get("pe_ttm", np.nan)
            circ_mv = row.get("circ_mv", np.nan)  # 万元
            amount = row.get("amount", np.nan)     # 千元
            pct_chg = row.get("pct_chg", np.nan)

            # ROE 从 pe/pb 推算：ROE ≈ PB / PE × 100
            roe = np.nan
            if not pd.isna(pb) and not pd.isna(pe_ttm) and pb > 0 and pe_ttm > 0:
                roe = pb / pe_ttm * 100.0

            fin_data[code] = {
                "pb": float(pb) if not pd.isna(pb) else None,
                "pe_ttm": float(pe_ttm) if not pd.isna(pe_ttm) else None,
                "circ_mv": float(circ_mv) if not pd.isna(circ_mv) else None,
                "roe": float(roe) if not pd.isna(roe) else None,
                "amount": float(amount) if not pd.isna(amount) else None,
                "pct_chg": float(pct_chg) if not pd.isna(pct_chg) else None,
            }

        print("[mfic] 财务数据加载完成: %d只" % len(fin_data))
        return fin_data

    except Exception as e:
        print("[mfic] 财务数据加载失败: %s" % e)
        return {}


# ============================================================
# QMT 策略生命周期
# ============================================================

def init(C):
    """策略初始化 — 加载本地财务数据"""
    try:
        C.set_account("STOCK")
    except Exception:
        pass

    # 加载本地财务数据
    fin_data = _load_financial_data()

    C.mfic_state = {
        "capital": CAPITAL,
        "initialized": True,
        "debug": True,
        "fin_data": fin_data,  # 本地财务数据缓存
    }

    print("[mfic] =============================================")
    print("[mfic] 多因子IC策略 v2 初始化完成")
    print("[mfic] 本金: %d元 | 单票上限: %.0f%% | 止损: %.0f%%" % (CAPITAL, MAX_WEIGHT*100, STOP_LOSS*100))
    print("[mfic] 调仓: 双月 | TOP%d | 市值: 0-30亿 | 成交额>2000万" % TOP_N)
    print("[mfic] 本地财务数据: %d只" % len(fin_data))
    print("[mfic] 持仓文件: %s" % POSITIONS_FILE)
    print("[mfic] =============================================")


def handlebar(C):
    """每根K线执行一次"""
    if not C.is_last_bar():
        return

    now = _get_market_time(C)
    today_str = now.strftime("%Y-%m-%d")
    hour = now.hour
    minute = now.minute

    # 仅在14:30-14:55执行
    if hour < 14 or (hour == 14 and minute < 30) or hour >= 15:
        return

    # 读取当前持仓
    positions = _read_positions()
    held_codes = set(positions.keys())
    fin_data = C.mfic_state.get("fin_data", {})

    # ====== 1. 止损检查 ======
    to_sell = []
    for code, pos in positions.items():
        try:
            tick = C.get_full_tick([code])
            if tick and code in tick:
                last_price = tick[code].get("lastPrice", 0)
                entry_price = pos.get("entry_price", 0)
                if last_price > 0 and entry_price > 0:
                    ret = last_price / entry_price - 1.0
                    if ret <= STOP_LOSS:
                        to_sell.append((code, pos["shares"]))
                        print("[mfic] 止损触发 %s: 入场%.2f 现价%.2f 跌幅%.1f%%" % (
                            code, entry_price, last_price, ret*100))
        except Exception:
            continue

    for code, shares in to_sell:
        try:
            passorder(24, 1101, ACCOUNT_ID, code, 5, -1, shares, C)
            print("[mfic] 止损卖出 %s %d股" % (code, shares))
            if code in positions:
                del positions[code]
        except Exception as e:
            print("[mfic] 止损卖出失败 %s: %s" % (code, e))

    # ====== 2. 检查调仓日 ======
    if not _is_rebalance_day(C, now):
        if to_sell:
            _write_positions(positions)
        return

    # ====== 3. 调仓：获取数据 ======
    print("[mfic] 调仓日 %s 开始" % today_str)

    # 获取全市场股票列表
    try:
        all_codes = C.get_stock_list_in_sector("沪深A股", False)
    except Exception:
        all_codes = C.get_stock_list_in_sector("上证A股", False) + C.get_stock_list_in_sector("深证A股", False)
    all_codes = [c for c in all_codes if c and not c.startswith("3")]

    # QMT API 获取价格历史（120日）
    try:
        md = C.get_market_data_ex(
            stock_code=all_codes,
            period="1d",
            count=120,
            dividend_type="front"
        )
    except Exception as e:
        print("[mfic] 获取数据失败: %s" % e)
        return

    if not md:
        print("[mfic] 无数据")
        return

    # 过滤有效股票（有120日价格数据）
    valid_codes = []
    for code in md:
        arr = md[code]
        if arr is None:
            continue
        close_series = arr.get("close")
        if close_series is None or len(close_series) < 60:
            continue
        latest = close_series.iloc[-1] if hasattr(close_series, "iloc") else close_series[-1]
        if latest is None or (isinstance(latest, float) and latest != latest):
            continue
        valid_codes.append(code)
    print("[mfic] 有效股票: %d只" % len(valid_codes))

    # 过滤：0-30亿 + 成交额>2000万（用本地财务数据）
    candidates = []
    for code in valid_codes:
        try:
            fd = fin_data.get(code, {})
            circ_mv = fd.get("circ_mv")  # 万元
            amount = fd.get("amount")    # 千元
            if circ_mv is not None and amount is not None:
                if MV_MIN < circ_mv < MV_MAX and amount > AMOUNT_MIN:
                    candidates.append(code)
        except Exception:
            continue
    print("[mfic] 过滤后候选: %d只" % len(candidates))

    if len(candidates) == 0:
        print("[mfic] 无候选股，跳过调仓")
        return

    # 计算因子
    factor_data = {}
    for code in candidates:
        try:
            h = md.get(code, {})
            close_arr = np.array(h.get("close", []), dtype=float)
            if len(close_arr) < 60:
                continue

            # 从本地财务数据获取 PB
            fd = fin_data.get(code, {})
            pb = fd.get("pb")
            if pb is None or pb <= 0:
                continue

            # BP = 1/PB
            bp = 1.0 / pb

            # 反转 = -1 * 近20日收益（从价格计算）
            if len(close_arr) >= 21:
                ret_1m = close_arr[-2] / close_arr[-22] - 1.0
            else:
                ret_1m = 0.0

            # 60日波动率（从价格计算）
            if len(close_arr) >= 61:
                pct_returns = np.diff(close_arr[-62:]) / close_arr[-62:-1]
                vol_60d = np.nanstd(pct_returns)
            else:
                pct_returns = np.diff(close_arr) / close_arr[:-1]
                vol_60d = np.nanstd(pct_returns)

            # ROE（从本地财务数据）
            roe = fd.get("roe", 0.0) or 0.0

            factor_data[code] = {
                "BP": bp, "reversal_1m": ret_1m,
                "volatility_60d": vol_60d, "ROE": roe
            }
        except Exception:
            continue

    if len(factor_data) < TOP_N:
        print("[mfic] 因子数据不足 %d只 (实际%d只)" % (TOP_N, len(factor_data)))
        return

    # 评分
    df_factors = pd.DataFrame(factor_data).T
    s_bp = _normalize(df_factors["BP"], reverse=False)
    s_rev = _normalize(df_factors["reversal_1m"], reverse=True)
    s_vol = _normalize(df_factors["volatility_60d"], reverse=True)
    s_roe = _normalize(df_factors["ROE"], reverse=False)

    total = (s_bp * FACTOR_WEIGHTS["BP"] +
             s_rev * FACTOR_WEIGHTS["reversal_1m"] +
             s_vol * FACTOR_WEIGHTS["volatility_60d"] +
             s_roe * FACTOR_WEIGHTS["ROE"]) * 100.0

    top_stocks = total.sort_values(ascending=False).head(TOP_N).index.tolist()
    print("[mfic] 评分完成，选中 %d 只" % len(top_stocks))
    print("[mfic] TOP3: %s (分数: %s)" % (
        str(top_stocks[:3]),
        str([round(total.loc[c], 1) for c in top_stocks[:3]])))

    # ====== 4. 卖出不在新池中的持仓 ======
    held_to_sell = [c for c in held_codes if c not in top_stocks]
    for code in held_to_sell:
        if code in positions:
            pos = positions[code]
            try:
                passorder(24, 1101, ACCOUNT_ID, code, 5, -1, pos["shares"], C)
                print("[mfic] 调仓卖出 %s %d股" % (code, pos["shares"]))
                del positions[code]
            except Exception as e:
                print("[mfic] 调仓卖出失败 %s: %s" % (code, e))

    # ====== 5. 买入新股票 ======
    new_buys = [c for c in top_stocks if c not in positions]
    n_buy = min(len(new_buys), MAX_POSITIONS if 'MAX_POSITIONS' in dir() else 80)
    new_buys = new_buys[:n_buy]

    if new_buys:
        # 均分资金
        try:
            account_info = C.get_account_info()
            available = account_info.get("cash", CAPITAL) if account_info else CAPITAL
        except Exception:
            available = CAPITAL
        cash_per = available * 0.95 / max(len(new_buys), 1)

        for code in new_buys:
            try:
                tick = C.get_full_tick([code])
                if tick and code in tick:
                    price = tick[code].get("lastPrice", 0)
                    if price > 0:
                        shares = int(cash_per / price / 100) * 100
                        if shares >= 100:
                            passorder(23, 1101, ACCOUNT_ID, code, 11, price, shares, C)
                            positions[code] = {
                                "shares": shares,
                                "entry_price": price,
                                "buy_date": today_str,
                            }
                            print("[mfic] 买入 %s %d股 @ %.2f" % (code, shares, price))
            except Exception as e:
                print("[mfic] 买入失败 %s: %s" % (code, e))

    _write_positions(positions)
    print("[mfic] 调仓完成，当前持仓 %d 只" % len(positions))
