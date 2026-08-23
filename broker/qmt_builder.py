# coding=utf-8
"""QMT 策略生成器 — 将策略逻辑打包为单文件 GBK 策略。

用法:
  python broker/qmt_builder.py
  输出: E:/QuantLab/projects/Project_01_多因子IC小盘Alpha/build/strategy_mfic.py (生产版)

流程:
  1. 读取配置和策略参数
  2. 组装 QMT 生命周期 + 策略逻辑 + 因子计算
  3. 转 GBK 编码输出
"""

import os, sys, json
import numpy as np
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config():
    path = os.path.join(PROJECT_ROOT, 'config', 'settings.yaml')
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def _safe_price(val, default=0):
    if isinstance(val, (list, tuple, np.ndarray)):
        return float(val[0]) if len(val) > 0 else default
    return float(val or default)


def build_qmt_strategy(config):
    """组装 QMT 策略源码字符串。"""
    p01 = config.get("project_01", {})
    live = p01.get("live", {})
    mc = p01.get("market_cap", {})

    capital = live.get("initial_capital", 100000)
    account_id = config.get("qmt", {}).get("account_id", "70180771")
    max_pos_pct = live.get("max_position_pct", 0.02)
    stop_loss_pct = live.get("stop_loss_pct", -0.12)
    top_n = p01.get("backtest", {}).get("top_n", 80)
    mv_min = mc.get("min", 0)
    mv_max = mc.get("max", 300000)
    amount_min = mc.get("amount_min", 20000)

    weights = p01.get("factors", {}).get("weights", {
        "BP": 0.27, "reversal_1m": 0.225,
        "volatility_60d": 0.225, "ROE": 0.18,
        "vwap_volume_corr": 0.10,
    })

    return f'''# coding=gbk
"""多因子IC策略 — 含VWAP量价因子 | 自动生成"""
import math
import numpy as np
import pandas as pd
import json
import os
from datetime import datetime, timedelta

# ====== 配置 ======
CAPITAL = {capital}
MAX_WEIGHT = {max_pos_pct}
STOP_LOSS = {stop_loss_pct}
TOP_N = {top_n}
MV_MIN = {mv_min}
MV_MAX = {mv_max}
AMOUNT_MIN = {amount_min}
FACTOR_WEIGHTS = {json.dumps(weights)}
POSITIONS_FILE = "D:/QMT_POOL/mfic_positions.json"
ACCOUNT_ID = '{account_id}'

# ====== 工具函数 ======
def _normalize(series, reverse=False):
    lo = series.quantile(0.01)
    hi = series.quantile(0.99)
    s = series.clip(lo, hi)
    s = (s - s.mean()) / s.std(ddof=0)
    if reverse:
        s = -s
    return s

def _safe_price(val, default=0):
    if isinstance(val, (list, tuple, np.ndarray)):
        return float(val[0]) if len(val) > 0 else default
    return float(val or default)

def _read_positions():
    if not os.path.exists(POSITIONS_FILE):
        return {{}}
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {{}}

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
            today.replace(day=1).strftime("%%Y%%m%%d"),
            last_day.strftime("%%Y%%m%%d"),
            "1d"
        )
        if tds and len(tds) > 0 and tds[-1] == today.strftime("%%Y%%m%%d"):
            return True
    except Exception:
        pass
    return False

def _compute_vwap_corr(close_arr, vol_arr, amt_arr):
    """计算单只股票的 VWAP 量价相关因子。"""
    if len(vol_arr) < 5 or len(amt_arr) < 5:
        return 0.0
    vol_5 = vol_arr[-5:]
    amt_5 = amt_arr[-5:]
    if not (np.all(vol_5 > 0) and np.all(amt_5 > 0)):
        return 0.0
    vwap_5 = amt_5 / (vol_5 * 100.0)
    vw_rank = np.argsort(np.argsort(vwap_5)).astype(float)
    vl_rank = np.argsort(np.argsort(vol_5)).astype(float)
    vw_m, vl_m = vw_rank.mean(), vl_rank.mean()
    num = ((vw_rank - vw_m) * (vl_rank - vl_m)).sum()
    d1 = ((vw_rank - vw_m) ** 2).sum()
    d2 = ((vl_rank - vl_m) ** 2).sum()
    if d1 > 0 and d2 > 0:
        corr = num / (np.sqrt(d1) * np.sqrt(d2))
        return -corr
    return 0.0

# ====== 策略生命周期 ======
def init(C):
    try:
        C.set_account("STOCK")
    except Exception:
        pass
    C.mfic_state = {{"capital": CAPITAL, "initialized": True}}

def handlebar(C):
    if not C.is_last_bar():
        return
    now = _get_market_time(C)
    today_str = now.strftime("%%Y-%%m-%%d")
    hour, minute = now.hour, now.minute
    if hour < 14 or (hour == 14 and minute < 30) or hour >= 15:
        return

    positions = _read_positions()
    held_codes = set(positions.keys())

    # 止损检查
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
        except Exception:
            continue
    for code, shares in to_sell:
        try:
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
    if to_sell:
        _write_positions(positions)

    # 调仓检查
    if not _is_rebalance_day(C, now):
        return

    print("[mfic] 调仓日 %s 开始" % today_str)

    # 获取全市场
    try:
        all_codes = C.get_stock_list_in_sector("沪深A股", False)
    except Exception:
        all_codes = C.get_stock_list_in_sector("上证A股", False) + C.get_stock_list_in_sector("深证A股", False)
    all_codes = [c for c in all_codes if c and not c.startswith("3")]

    try:
        md = C.get_market_data_ex(
            stock_code=all_codes, period="1d", count=120, dividend_type="front"
        )
    except Exception as e:
        print("[mfic] 获取数据失败: %s" % e)
        return
    if not md:
        return

    valid_codes = []
    for code in md:
        arr = md[code]
        if arr is None or len(arr) == 0:
            continue
        close_series = arr.get("close")
        if close_series is None or len(close_series) == 0:
            continue
        latest = close_series.iloc[-1] if hasattr(close_series, "iloc") else close_series[-1]
        if latest is None or (isinstance(latest, float) and latest != latest):
            continue
        valid_codes.append(code)
    print("[mfic] 有效股票: %d只" % len(valid_codes))

    # 过滤 + 计算因子
    factor_data = {{}}
    for code in valid_codes:
        try:
            h = md.get(code, {{}})
            close_arr = np.array(h.get("close", []), dtype=float)
            vol_arr = np.array(h.get("volume", []), dtype=float)
            amt_arr = np.array(h.get("amount", []), dtype=float)
            pb_arr = np.array(h.get("pb", []), dtype=float)
            if len(close_arr) < 60:
                continue
            pb_latest = pb_arr[-1] if len(pb_arr) > 0 else np.nan
            if pd.isna(pb_latest) or pb_latest <= 0:
                continue
            bp = 1.0 / pb_latest
            ret_1m = close_arr[-2] / close_arr[-22] - 1.0 if len(close_arr) >= 21 else 0.0
            pct_arr = np.array(h.get("pct_chg", []), dtype=float)
            if len(pct_arr) >= 61:
                vol_60d = np.nanstd(pct_arr[-61:-1])
            else:
                vol_60d = np.nanstd(pct_arr)
            pe_arr = np.array(h.get("pe_ttm", []), dtype=float)
            pe = pe_arr[-1] if len(pe_arr) > 0 else np.nan
            roe = 0.0
            if not pd.isna(pb_latest) and not pd.isna(pe) and pb_latest > 0 and pe > 0:
                roe = pe / pb_latest * 0.01
            vwap_corr = _compute_vwap_corr(close_arr, vol_arr, amt_arr)
            factor_data[code] = {{
                "BP": bp, "reversal_1m": ret_1m,
                "volatility_60d": vol_60d, "ROE": roe,
                "vwap_volume_corr": vwap_corr
            }}
        except Exception:
            continue

    if not factor_data:
        print("[mfic] 因子计算失败")
        return

    # 评分
    df = pd.DataFrame(factor_data).T
    s_bp = _normalize(df["BP"], reverse=False)
    s_rev = _normalize(df["reversal_1m"], reverse=True)
    s_vol = _normalize(df["volatility_60d"], reverse=True)
    s_roe = _normalize(df["ROE"], reverse=False)
    s_vwap = _normalize(df["vwap_volume_corr"], reverse=False)
    total = pd.Series(np.nan, index=df.index)
    w_sum = 0.0
    score_map = {{"BP": s_bp, "reversal_1m": s_rev, "volatility_60d": s_vol, "ROE": s_roe, "vwap_volume_corr": s_vwap}}
    for name, w in FACTOR_WEIGHTS.items():
        s = score_map.get(name)
        if s is not None and len(s.dropna()) > 0:
            total = total.add(s * w, fill_value=0)
            w_sum += w
    if w_sum > 0:
        total = total / w_sum * 100.0
    scores = total.dropna().sort_values(ascending=False)
    selected = scores.head(TOP_N).index.tolist()
    selected_set = set(selected)

    # 获取可用资金
    try:
        acct = C.get_account_info()
        available_cash = float(acct.get("cash", CAPITAL)) if acct else CAPITAL
    except Exception:
        available_cash = CAPITAL
    cash = available_cash * 0.98

    # 卖出
    for code in list(held_codes):
        if code not in selected_set:
            pos = positions.get(code, {{}})
            shares = pos.get("shares", 0)
            if shares > 0:
                try:
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

    # 买入
    max_per_stock = CAPITAL * MAX_WEIGHT
    for code in selected:
        if code in positions:
            continue
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
                print("[mfic] 买入 %s %.2f×%d股" % (code, price, volume))
                positions[code] = {{"shares": volume, "entry_price": price, "buy_date": today_str}}
            else:
                print("[mfic] 买入失败 %s: passorder返回0" % code)
        except Exception as e:
            print("[mfic] 买入失败 %s: %s" % (code, e))

    _write_positions(positions)
    print("[mfic] 调仓完成, 持仓%d只" % len(positions))

def exit(C):
    pass
'''

def save_strategy(source_code, output_path):
    """保存策略文件 (GBK 编码)。"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='gbk') as f:
        f.write(source_code)
    size = os.path.getsize(output_path)
    print("生成: %s (%d bytes)" % (output_path, size))
    return size


if __name__ == "__main__":
    config = load_config()
    code = build_qmt_strategy(config)
    save_strategy(code, 'E:/QuantLab/projects/Project_01_多因子IC小盘Alpha/build/strategy_mfic.py')
