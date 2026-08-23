# coding=utf-8
"""多因子IC策略 · QMT单文件部署版 v2 全天测试版
改动：去掉 is_last_bar() 和时间限制，每根K线都执行，用于模拟盘验证。
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
MV_MAX = 300000
AMOUNT_MIN = 20000
FACTOR_WEIGHTS = {"BP": 0.30, "reversal_1m": 0.25, "volatility_60d": 0.25, "ROE": 0.20}
POSITIONS_FILE = "D:/QMT_POOL/mfic_positions.json"
FIN_DATA_CSV = "D:/QMT_POOL/mfic_fin_data.csv"
DEBUG_FORCE_REBAL = True  # 调试：强制每天都调仓，生产设为 False
ACCOUNT_ID = '70180771'   # QMT 资金账号

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

def _load_financial_data():
    """从本地 CSV 加载财务数据（QMT Python 3.6 无 pyarrow，不能读 parquet）。"""
    print("[mfic] 加载本地财务数据...")
    try:
        if not os.path.exists(FIN_DATA_CSV):
            print("[mfic] 财务CSV不存在: %s" % FIN_DATA_CSV)
            return {}
        df = pd.read_csv(FIN_DATA_CSV, encoding="gbk")
        print("[mfic] CSV加载: %d只" % len(df))
        fin_data = {}
        for _, row in df.iterrows():
            code = row["ts_code"]
            pb = row.get("pb", None)
            pe_ttm = row.get("pe_ttm", None)
            circ_mv = row.get("circ_mv", None)
            amount = row.get("amount", None)
            roe = row.get("roe", None)
            fin_data[code] = {
                "pb": float(pb) if pd.notna(pb) else None,
                "pe_ttm": float(pe_ttm) if pd.notna(pe_ttm) else None,
                "circ_mv": float(circ_mv) if pd.notna(circ_mv) else None,
                "roe": float(roe) if pd.notna(roe) else None,
                "amount": float(amount) if pd.notna(amount) else None,
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
    try:
        C.set_account("STOCK")
    except Exception:
        pass
    fin_data = _load_financial_data()
    C.mfic_state = {
        "capital": CAPITAL,
        "initialized": True,
        "debug": True,
        "fin_data": fin_data,
        "last_rebal_date": None,
        "bar_count": 0,
    }
    print("[mfic] =============================================")
    print("[mfic] 多因子IC策略 v2 全天测试版 初始化完成")
    print("[mfic] 本金: %d元 | 止损: %.0f%%" % (CAPITAL, STOP_LOSS*100))
    print("[mfic] 本地财务数据: %d只" % len(fin_data))
    print("[mfic] =============================================")


def handlebar(C):
    """每根K线执行 — 全天模式，去掉 is_last_bar 和时间限制"""
    now = _get_market_time(C)
    today_str = now.strftime("%Y-%m-%d")
    hour = now.hour
    minute = now.minute

    # 计数器
    C.mfic_state["bar_count"] = C.mfic_state.get("bar_count", 0) + 1
    bar_count = C.mfic_state["bar_count"]

    # 每50根K线打印一次心跳
    if bar_count % 50 == 0:
        print("[mfic] 心跳 #%d %s:%02d 持仓=%d只" % (
            bar_count, today_str, hour,
            len(_read_positions())))

    # 读取持仓
    positions = _read_positions()
    held_codes = set(positions.keys())
    fin_data = C.mfic_state.get("fin_data", {})

    # ====== 1. 止损检查（每次都检查） ======
    to_sell = []
    for code, pos in list(positions.items()):
        try:
            tick = C.get_full_tick([code])
            if tick and code in tick:
                last_price = tick[code].get("lastPrice", 0)
                entry_price = pos.get("entry_price", 0)
                if last_price > 0 and entry_price > 0:
                    ret = last_price / entry_price - 1.0
                    if ret <= STOP_LOSS:
                        to_sell.append((code, pos["shares"]))
                        print("[mfic] [止损] %s 入场%.2f 现价%.2f %.1f%%" % (
                            code, entry_price, last_price, ret*100))
        except Exception:
            continue

    for code, shares in to_sell:
        try:
            passorder(24, 1101, ACCOUNT_ID, code, 5, -1, shares, C)
            print("[mfic] [止损卖出] %s %d股" % (code, shares))
            if code in positions:
                del positions[code]
            _write_positions(positions)
        except Exception as e:
            print("[mfic] [止损失败] %s: %s" % (code, e))

    # ====== 2. 检查调仓日（仅在调仓日执行选股买入） ======
    is_rebal = DEBUG_FORCE_REBAL or _is_rebalance_day(C, now)
    last_rebal = C.mfic_state.get("last_rebal_date")

    if not is_rebal:
        return

    # 防止同一天重复调仓
    if last_rebal == today_str:
        return

    # ====== 3. 调仓流程 ======
    print("[mfic] ============================================")
    print("[mfic] [调仓日] %s %s:%02d" % (today_str, hour, minute))
    print("[mfic] ============================================")

    # 获取全市场股票
    try:
        all_codes = C.get_stock_list_in_sector("沪深A股", False)
    except Exception:
        all_codes = C.get_stock_list_in_sector("上证A股", False) + C.get_stock_list_in_sector("深证A股", False)
    all_codes = [c for c in all_codes if c and not c.startswith("3")]
    print("[mfic] 全市场: %d只" % len(all_codes))

    # QMT API 获取价格历史
    print("[mfic] 获取价格数据...")
    try:
        md = C.get_market_data_ex(
            stock_code=all_codes,
            period="1d",
            count=120,
            dividend_type="front"
        )
    except Exception as e:
        print("[mfic] [ERROR] 获取数据失败: %s" % e)
        return

    if not md:
        print("[mfic] [ERROR] 无数据")
        return

    # 过滤有效股票
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

    # 市值+成交额过滤
    candidates = []
    for code in valid_codes:
        fd = fin_data.get(code, {})
        circ_mv = fd.get("circ_mv")
        amount = fd.get("amount")
        if circ_mv is not None and amount is not None:
            if MV_MIN < circ_mv < MV_MAX and amount > AMOUNT_MIN:
                candidates.append(code)
    print("[mfic] 候选(0-30亿+额>2000万): %d只" % len(candidates))

    if len(candidates) < TOP_N:
        print("[mfic] [SKIP] 候选不足%d只" % TOP_N)
        return

    # 计算因子
    print("[mfic] 计算因子...")
    factor_data = {}
    for code in candidates:
        try:
            h = md.get(code, {})
            close_arr = np.array(h.get("close", []), dtype=float)
            if len(close_arr) < 60:
                continue
            fd = fin_data.get(code, {})
            pb = fd.get("pb")
            if pb is None or pb <= 0:
                continue
            bp = 1.0 / pb
            if len(close_arr) >= 21:
                ret_1m = close_arr[-2] / close_arr[-22] - 1.0
            else:
                ret_1m = 0.0
            if len(close_arr) >= 61:
                pct_returns = np.diff(close_arr[-62:]) / close_arr[-62:-1]
                vol_60d = np.nanstd(pct_returns)
            else:
                pct_returns = np.diff(close_arr) / close_arr[:-1]
                vol_60d = np.nanstd(pct_returns)
            roe = fd.get("roe", 0.0) or 0.0
            factor_data[code] = {
                "BP": bp, "reversal_1m": ret_1m,
                "volatility_60d": vol_60d, "ROE": roe
            }
        except Exception:
            continue

    print("[mfic] 有效因子: %d只" % len(factor_data))

    if len(factor_data) < TOP_N:
        print("[mfic] [SKIP] 因子不足%d只" % TOP_N)
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
    print("[mfic] 选股完成 TOP%d" % len(top_stocks))
    print("[mfic] TOP3: %s" % str(top_stocks[:3]))
    print("[mfic] BOTTOM3: %s" % str(top_stocks[-3:]))

    # 卖出
    held_to_sell = [c for c in held_codes if c not in top_stocks]
    print("[mfic] 待卖出: %d只" % len(held_to_sell))
    for code in held_to_sell:
        if code in positions:
            pos = positions[code]
            try:
                passorder(24, 1101, ACCOUNT_ID, code, 5, -1, pos["shares"], C)
                print("[mfic] [卖出] %s %d股" % (code, pos["shares"]))
                del positions[code]
            except Exception as e:
                print("[mfic] [卖出失败] %s: %s" % (code, e))

    # 买入
    new_buys = [c for c in top_stocks if c not in positions]
    print("[mfic] 待买入: %d只" % len(new_buys))
    n_buy = min(len(new_buys), 80)
    new_buys = new_buys[:n_buy]

    if new_buys:
        try:
            account_info = C.get_account_info()
            available = account_info.get("cash", CAPITAL) if account_info else CAPITAL
        except Exception:
            available = CAPITAL
        cash_per = available * 0.95 / max(len(new_buys), 1)
        print("[mfic] 可用资金: %.0f, 每只分配: %.0f" % (available, cash_per))

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
                            print("[mfic] [买入] %s %d股 @ %.2f" % (code, shares, price))
            except Exception as e:
                print("[mfic] [买入失败] %s: %s" % (code, e))

    _write_positions(positions)
    C.mfic_state["last_rebal_date"] = today_str
    print("[mfic] [调仓完成] 持仓 %d 只" % len(positions))
    print("[mfic] ============================================")
