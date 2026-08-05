# coding=utf-8
"""多因子IC策略 · QMT单文件部署版（开发调试版）
文件：strategy_mfic_dev.py
本金：10万元 | 双月调仓 | TOP80 | 止损-12% | 单票2%上限
买入标记：passorder strRemark="mfic"
持仓隔离：D:/QMT_POOL/mfic_positions.json
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
FACTOR_WEIGHTS = {"BP": 0.27, "reversal_1m": 0.225, "volatility_60d": 0.225, "ROE": 0.18, "vwap_volume_corr": 0.10}
POSITIONS_FILE = "D:/QMT_POOL/mfic_positions.json"
TRADES_FILE = "D:/QMT_POOL/mfic_trades.txt"
ACCOUNT_ID = '67014907'   # QMT 资金账号

# ============================================================
# 工具函数
# ============================================================

def _normalize(series, reverse=False):
    """winsorize(1%,99%) -> z-score -> 方向控制"""
    lo = series.quantile(0.01)
    hi = series.quantile(0.99)
    s = series.clip(lo, hi)
    s = (s - s.mean()) / s.std(ddof=0)
    if reverse:
        s = -s
    return s


def _compute_scores(df_daily):
    """基于QMT实时数据计算4因子评分。
    df_daily: DataFrame, index=ts_code, columns=[pb, close, pct_chg, pe_ttm, circ_mv, amount, ...]
    需要至少120行历史数据用于计算60d波动率和动量。
    """
    pb = df_daily["pb"].copy()
    bp = 1.0 / pb.replace(0, np.nan)
    s_bp = _normalize(bp, reverse=False)

    # 反转: 过去1月涨幅越低(跌得多)得分越高 → reverse=True
    rev = df_daily["momentum_1m"] if "momentum_1m" in df_daily.columns else df_daily.get("ret_1m", pd.Series(np.nan, index=df_daily.index))
    s_rev = _normalize(rev, reverse=True)

    # 低波: 波动率越低得分越高 → reverse=True
    vol = df_daily["volatility_60d"] if "volatility_60d" in df_daily.columns else df_daily.get("vol_60d", pd.Series(np.nan, index=df_daily.index))
    s_vol = _normalize(vol, reverse=True)

    # ROE
    roe = df_daily["ROE"] if "ROE" in df_daily.columns else df_daily.get("roe", pd.Series(np.nan, index=df_daily.index))
    s_roe = _normalize(roe, reverse=False)

    # VWAP量价相关: IC为正，不反向
    vwap = df_daily["vwap_volume_corr"] if "vwap_volume_corr" in df_daily.columns else pd.Series(0.0, index=df_daily.index)
    s_vwap = _normalize(vwap, reverse=False)

    # 加权合成
    total = pd.Series(np.nan, index=df_daily.index)
    weight_sum = 0.0
    score_map = {"BP": s_bp, "reversal_1m": s_rev, "volatility_60d": s_vol, "ROE": s_roe, "vwap_volume_corr": s_vwap}
    for name, w in FACTOR_WEIGHTS.items():
        s = score_map.get(name)
        if s is not None and len(s.dropna()) > 0:
            total = total.add(s * w, fill_value=0)
            weight_sum += w
    if weight_sum > 0:
        total = total / weight_sum * 100.0
    return total


def _read_positions():
    """读取持仓文件"""
    if not os.path.exists(POSITIONS_FILE):
        return {}
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_positions(positions):
    """写入持仓文件"""
    try:
        os.makedirs(os.path.dirname(POSITIONS_FILE), exist_ok=True)
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[mfic] 写入持仓失败: %s" % e)


def _get_market_time(C):
    """获取当前市场时间"""
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


def _safe_price(val, default=0):
    """处理 QMT 返回的标量/数组价格，确保返回标量。"""
    if isinstance(val, (list, tuple, np.ndarray)):
        return float(val[0]) if len(val) > 0 else default
    return float(val or default)


def _is_rebalance_day(C, today):
    """判断今天是否为双月调仓日（偶数月的最后一个交易日）"""
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
# QMT 策略生命周期
# ============================================================

def init(C):
    """策略初始化"""
    # 设置账号
    try:
        C.set_account("STOCK")
    except Exception:
        pass
    C.mfic_state = {
        "capital": CAPITAL,
        "initialized": True,
        "debug": True,
    }
    print("[mfic] =============================================")
    print("[mfic] 多因子IC策略 初始化完成")
    print("[mfic] 本金: %d元 | 单票上限: %.0f%% | 止损: %.0f%%" % (CAPITAL, MAX_WEIGHT*100, STOP_LOSS*100))
    print("[mfic] 调仓: 双月 | TOP%d | 市值: 0-30亿 | 成交额>2000万" % TOP_N)
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

    # 仅在14:30-14:55执行（尾盘交易窗口）
    if hour < 14 or (hour == 14 and minute < 30) or hour >= 15:
        return

    # 读取当前持仓
    positions = _read_positions()
    held_codes = set(positions.keys())

    # ====== 1. 止损检查 ======
    to_sell = []
    for code, pos in positions.items():
        try:
            tick = C.get_full_tick([code])
            if tick and code in tick:
                last_price = _safe_price(tick[code].get("lastPrice"))
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
            # 止损卖出用限价卖一价（prType=0），避免市价单不处理
            tick_price = C.get_full_tick([code])
            ask1 = 0
            if tick_price and code in tick_price:
                ask1 = _safe_price(tick_price[code].get("askPrice1"))
            order_id = passorder(24, 1101, ACCOUNT_ID, code, 0, ask1, shares, C)
            if order_id:
                print("[mfic] 止损卖出 %s %d股" % (code, shares))
                if code in positions:
                    del positions[code]
            else:
                print("[mfic] 止损卖出失败 %s: passorder返回0" % code)
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
    all_codes = [c for c in all_codes if c and not c.startswith("3")]  # 排除创业板

    # 批量获取数据（先拿1日数据做过滤）
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

    # 转换为DataFrame
    df_list = []
    for code in md:
        arr = md[code]
        if arr is None or len(arr) == 0:
            continue
        close_series = arr["close"] if "close" in arr else None
        if close_series is None or len(close_series) == 0:
            continue
        latest = close_series.iloc[-1] if hasattr(close_series, "iloc") else close_series[-1]
        if latest is None or (isinstance(latest, float) and latest != latest):
            continue
        df_list.append(code)
    valid_codes = df_list
    print("[mfic] 有效股票: %d只" % len(valid_codes))

    # 获取最新行情数据做过滤
    try:
        snap = C.get_full_tick(valid_codes[:1000])  # 分批
    except Exception:
        snap = {}

    # 由于get_full_tick有代码数限制，简化实现：
    # 直接用get_market_data_ex取最新一天的pe/pb/circ_mv/amount
    try:
        fields = ["pe_ttm", "pb", "close", "circ_mv", "amount", "pct_chg"]
        latest_data = C.get_market_data_ex(
            fields, valid_codes, period="1d", count=1
        )
    except Exception:
        latest_data = {}

    # 过滤：0-30亿 + 成交额>2000万
    candidates = []
    for code in valid_codes:
        try:
            d = latest_data.get(code, {})
            circ_mv = d.get("circ_mv", [0])[-1] if isinstance(d.get("circ_mv"), (list, np.ndarray)) else d.get("circ_mv", 0)
            amount = d.get("amount", [0])[-1] if isinstance(d.get("amount"), (list, np.ndarray)) else d.get("amount", 0)
            if circ_mv and amount and MV_MIN < circ_mv < MV_MAX and amount > AMOUNT_MIN:
                candidates.append(code)
        except Exception:
            continue
    print("[mfic] 过滤后候选: %d只" % len(candidates))

    if len(candidates) == 0:
        print("[mfic] 无候选股，跳过调仓")
        return

    # 获取完整历史数据计算因子
    try:
        hist = C.get_market_data_ex(
            ["close", "pct_chg", "pb", "pe_ttm", "volume", "amount"],
            candidates, period="1d", count=120
        )
    except Exception:
        hist = {}

    # 计算因子
    factor_data = {}
    for code in candidates:
        try:
            h = hist.get(code, {})
            if not h or len(h.get("close", [])) < 60:
                continue
            close_arr = np.array(h["close"], dtype=float)
            pct_arr = np.array(h.get("pct_chg", []), dtype=float)
            pb_arr = np.array(h.get("pb", []), dtype=float)

            # BP = 1/PB
            pb_latest = pb_arr[-1] if len(pb_arr) > 0 else np.nan
            if pd.isna(pb_latest) or pb_latest <= 0:
                continue
            bp = 1.0 / pb_latest

            # 反转 = -1 * 近20日收益
            if len(close_arr) >= 21:
                ret_1m = close_arr[-2] / close_arr[-22] - 1.0  # 排除当日
            else:
                ret_1m = 0.0

            # 60日波动率
            if len(pct_arr) >= 61:
                vol_60d = np.nanstd(pct_arr[-61:-1])  # 排除当日
            else:
                vol_60d = np.nanstd(pct_arr)

            # ROE（取财报数据，简化为从pe/pb推算）
            pe = pb_arr[-1] if len(pb_arr) > 0 else np.nan
            roe = 0.0
            if not pd.isna(pb_latest) and not pd.isna(pe) and pb_latest > 0 and pe > 0:
                roe = pe / pb_latest * 0.01  # 粗略估算

            # VWAP量价相关: -5d Spearman corr(VWAP rank, volume rank)
            # VWAP = amount(元) / (volume(手) * 100) = 元/股
            vwap_corr = 0.0
            vol_arr = np.array(h.get("volume", []), dtype=float)
            amt_arr = np.array(h.get("amount", []), dtype=float)
            if len(vol_arr) >= 5 and len(amt_arr) >= 5:
                vol_5 = vol_arr[-5:]
                amt_5 = amt_arr[-5:]
                if np.all(vol_5 > 0) and np.all(amt_5 > 0):
                    vwap_5 = amt_5 / (vol_5 * 100.0)
                    vw_rank = np.argsort(np.argsort(vwap_5)).astype(float)
                    vl_rank = np.argsort(np.argsort(vol_5)).astype(float)
                    vw_m, vl_m = vw_rank.mean(), vl_rank.mean()
                    num = ((vw_rank - vw_m) * (vl_rank - vl_m)).sum()
                    d1 = ((vw_rank - vw_m) ** 2).sum()
                    d2 = ((vl_rank - vl_m) ** 2).sum()
                    if d1 > 0 and d2 > 0:
                        corr = num / (np.sqrt(d1) * np.sqrt(d2))
                        vwap_corr = -corr  # 负相关越强 → 值越高

            factor_data[code] = {
                "BP": bp, "reversal_1m": ret_1m,
                "volatility_60d": vol_60d, "ROE": roe,
                "vwap_volume_corr": vwap_corr
            }
        except Exception:
            continue

    if not factor_data:
        print("[mfic] 因子计算失败，跳过调仓")
        return

    # 评分
    df = pd.DataFrame(factor_data).T
    scores = _compute_scores(df)
    scores = scores.dropna().sort_values(ascending=False)

    if len(scores) < TOP_N:
        print("[mfic] 评分不足TOP%d，实际%d只" % (TOP_N, len(scores)))
        selected = scores.index.tolist()
    else:
        selected = scores.head(TOP_N).index.tolist()

    print("[mfic] 选中%d只, 最高分%.1f, 最低分%.1f" % (
        len(selected), scores[selected[0]] if len(selected) > 0 else 0,
        scores[selected[-1]] if len(selected) > 0 else 0))

    # ====== 4. 执行调仓 ======
    selected_set = set(selected)
    max_per_stock = CAPITAL * MAX_WEIGHT  # 2000元

    # 获取账户可用资金
    try:
        acct = C.get_account_info()
        available_cash = float(acct.get("cash", CAPITAL)) if acct else CAPITAL
    except Exception:
        available_cash = CAPITAL
    cash = available_cash * 0.98  # 预留2%现金

    # 卖出不在池中的持仓
    for code in list(held_codes):
        if code not in selected_set:
            pos = positions.get(code, {})
            shares = pos.get("shares", 0)
            if shares > 0:
                try:
                    # 调仓卖出用限价卖一价
                    tick_price = C.get_full_tick([code])
                    ask1 = 0
                    if tick_price and code in tick_price:
                        ask1 = _safe_price(tick_price[code].get("askPrice1"))
                    order_id = passorder(24, 1101, ACCOUNT_ID, code, 0, ask1, shares, C)
                    if order_id:
                        print("[mfic] 卖出 %s %d股" % (code, shares))
                    else:
                        print("[mfic] 卖出失败 %s: passorder返回0" % code)
                except Exception as e:
                    print("[mfic] 卖出失败 %s: %s" % (code, e))
            if code in positions:
                del positions[code]

    # 买入新选中的股票
    for code in selected:
        if code in positions:
            continue  # 已持有，跳过
        # 计算买入股数（单票上限2000元）
        try:
            tick = C.get_full_tick([code])
            if not tick or code not in tick:
                continue
            price = _safe_price(tick[code].get("lastPrice"))
            if price <= 0:
                continue
            volume = int(max_per_stock / price / 100) * 100
            if volume <= 0:
                continue
            order_id = passorder(23, 1101, ACCOUNT_ID, code, 11, price, volume, C)
            if order_id:
                print("[mfic] 买入 %s %.2f×%d股=%.0f元" % (code, price, volume, price*volume))
                positions[code] = {"shares": volume, "entry_price": price, "buy_date": today_str}
            else:
                print("[mfic] 买入失败 %s: passorder返回0" % code)
        except Exception as e:
            print("[mfic] 买入失败 %s: %s" % (code, e))

    # 保存持仓
    _write_positions(positions)
    print("[mfic] 调仓完成, 持仓%d只" % len(positions))
