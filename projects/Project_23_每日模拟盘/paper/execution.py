# -*- coding: utf-8 -*-
"""撮合器 —— A 股可执行性 + 费用 + 出场规则。

设计原则：
  1. 纯函数、无 IO、无全局可变状态 —— 便于单测和跨进程复用
  2. 费用口径直接复用 scan_rotate_cost_real 的 buy_fee/sell_fee（已对齐实盘费率）
  3. 出场规则复刻 scan_rotate_cost_real.simulate 的 live_trail 分支（桥/实盘同口径）

与 backtest/execution.py 的关系：
  - 不直接 import，因为 execution.py 的 _bar_lookup 绑定「预载入的 market_window」结构，
    而模拟盘是每日单点查询（逐日推进），强行复用会引入不必要的耦合
  - 费用/涨跌停/整手规则与之保持数值一致（同为 qmt_config 口径），已交叉校验

与 scan_rotate_cost_real.py 的关系：
  - 费用函数直接 import 复用（单一事实源）
  - 撮合逻辑重写，因为原 simulate() 是「一次性跑完全部历史」，无法跨日持久化
"""
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../Project_23_每日模拟盘/paper
_PROJ23 = os.path.dirname(_HERE)                            # .../Project_23_每日模拟盘
_ROOT = os.path.dirname(os.path.dirname(_PROJ23))           # d:/QuantLab
_P16 = os.path.join(_ROOT, "projects", "Project_16_LightGBM股票大师")
if _P16 not in sys.path:
    sys.path.insert(0, _P16)

import qmt_config as C  # noqa: E402  费用口径唯一事实源

LOT_SIZE = 100


# ---------------- 费用 ----------------

def buy_fee(amount, slip):
    """买入费用 = 佣金(max(最低5元, 万2)) + 过户费(万0.1) + 滑点。

    与 scan_rotate_cost_real.buy_fee 数值一致（该函数 is_sh 参数已废弃，未使用）。
    """
    comm = max(C.COMM_MIN, amount * C.COMM_RATE)
    return comm + amount * C.TRANS_RATE + amount * slip


def sell_fee(amount, slip):
    """卖出费用 = 佣金(max(最低5元, 万2)) + 印花税(万5) + 过户费(万0.1) + 滑点。"""
    comm = max(C.COMM_MIN, amount * C.COMM_RATE)
    return comm + amount * C.STAMP_RATE + amount * C.TRANS_RATE + amount * slip


def lot_floor(volume):
    """股数向下取整到 100 股整手。"""
    if volume is None or volume < LOT_SIZE:
        return 0
    return int(volume // LOT_SIZE) * LOT_SIZE


# ---------------- 可执行性 ----------------

def price_limit_pct(code, is_st=False):
    """涨跌停幅度：ST 5% / 双创 20% / 北交 30% / 主板 10%。"""
    c = code.split(".")[0] if "." in code else code
    if is_st:
        return 0.05
    if c.startswith("300") or c.startswith("688"):
        return 0.20
    if c.startswith("8") and len(c) == 6:
        return 0.30
    return 0.10


def can_buy_at_open(bar, prev_close, is_st=False):
    """买入可执行性：开盘一字涨停买不进 / 停牌 / 无量。

    bar: dict，至少含 open/vol（缺失视为停牌）
    prev_close: 前收盘价，用于判定涨停
    返回 (ok: bool, reason: str)
    """
    if not bar:
        return False, "no_bar"
    o = bar.get("open")
    if o is None or (isinstance(o, float) and np.isnan(o)) or o <= 0:
        return False, "no_open"
    vol = bar.get("vol")
    if vol is None or (isinstance(vol, float) and np.isnan(vol)) or vol <= 0:
        return False, "no_volume"
    if prev_close and prev_close > 0:
        up_pct = price_limit_pct(bar.get("code", ""), is_st)
        if o >= prev_close * (1 + up_pct) - 1e-9:
            return False, "limit_up_open"
    return True, ""


def can_sell_at_open(bar, prev_close, is_st=False):
    """卖出可执行性：开盘一字跌停卖不掉 / 停牌。

    返回 (ok: bool, reason: str)
    """
    if not bar:
        return False, "no_bar"
    o = bar.get("open")
    if o is None or (isinstance(o, float) and np.isnan(o)) or o <= 0:
        return False, "no_open"
    if prev_close and prev_close > 0:
        dn_pct = price_limit_pct(bar.get("code", ""), is_st)
        if o <= prev_close * (1 - dn_pct) + 1e-9:
            return False, "limit_down_open"
    return True, ""


# ---------------- 出场规则 ----------------

def check_exit(pos, bar_cur, held_days, cfg, peak):
    """判定是否出场。复刻 scan_rotate_cost_real live_trail 分支。

    pos: dict，至少含 buy_px（成交价）、code
    bar_cur: 当日 bar（含 open/high）
    held_days: 已持有交易日数（买入日记为 0；T+1 当日 held_days=1 不可卖）
    cfg: 策略配置 dict
    peak: 当前峰值价（已含历史 high）

    返回 (should_sell, reason, extra)  extra 为可选覆盖成交价（touch_tp 用）
    """
    buy_px = pos["buy_px"]
    open_px = bar_cur.get("open")
    if not open_px or open_px <= 0 or buy_px <= 0:
        return False, "", None

    # 期满（所有模式共有的出口）
    hold_days = int(cfg.get("hold_days", 5))
    if held_days >= hold_days:
        return True, "MATURE", None

    # T+1：买入当日与次日开盘前不可卖
    if held_days < 1:
        return False, "", None

    ret = open_px / buy_px - 1
    stop = float(cfg.get("stop_loss", -0.07))
    tp = float(cfg.get("take_profit", 0.15))
    trail_pct = float(cfg.get("trailing_pct", 0.08))
    trail_act_pct = float(cfg.get("trailing_activate_pct", 0.08))

    # 追盈激活：峰值须 >= 成本×(1+激活阈值)，触发线取 max(成本, 峰值×(1-回撤))
    trail_act = peak >= buy_px * (1 + trail_act_pct)
    trail_hit = trail_act and open_px <= max(buy_px, peak * (1 - trail_pct))

    if ret <= stop:
        return True, "STOP", None
    if ret >= tp:
        return True, "TP", None
    if trail_hit:
        return True, "TRAIL", None
    return False, "", None


def calc_buy_volume(budget, price):
    """按预算计算可买股数（整手）。返回 (volume, actual_amount)。

    actual_amount 为买入前的成交额（不含费用），用于计算费用。
    """
    if price is None or price <= 0 or budget <= 0:
        return 0, 0.0
    raw = budget / price
    vol = lot_floor(raw)
    if vol <= 0:
        return 0, 0.0
    return vol, vol * price
