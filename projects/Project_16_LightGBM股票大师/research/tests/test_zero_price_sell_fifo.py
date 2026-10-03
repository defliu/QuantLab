# -*- coding: utf-8 -*-
"""零价 SELL 的 FIFO 口径统一回归测试（T-20261002-001 / 项2）。

背景：结算价未回填时调用方会写 price=0 的 SELL（项1 的 unresolved 语义）。
这类记录有两个必须分开的语义：

  1) **持仓语义**：成交是真实的 → 必须消耗 FIFO 成本层。否则后续有价 SELL 会
     错误地配对更早那批买入成本，把 A 批成本错记到 B 批的卖出上。
  2) **盈亏语义**：成交价未知 → 该笔**不能**计算已实现盈亏。否则会把整批成本
     一次性写成巨额亏损。

历史缺陷：reconcile_trades.derive_positions() 已按「消耗 FIFO 但不计盈亏」实现，
而 strategy_capital.calc_realized() 直接 `if price <= 0: continue` —— 整条跳过，
既不消耗 FIFO 也不计盈亏。两个模块自相矛盾，且同文件内也不一致：
  - 资金池已实现盈亏（calc_realized）      → 错口径
  - 资金池浮盈成本（_fifo_positions_from_log）→ 对口径
  → 同一笔持仓的「已实现」与「浮盈」成本基准不同，资金池失真。

测试策略：monkeypatch qmt_config.load_trade_log_rows 注入构造序列，
真实驱动两个生产函数（不连 QMT、不写生产文件）。
关键序列刻意让**有价 SELL 跨越批次边界**，否则新旧口径会巧合相等而测不出差异。

用例：
  Z1 口径一致  ：零价 SELL 后两模块的持仓成本 / 已实现必须完全一致
  Z2 已实现口径：有价 SELL 必须配对被零价扣减后的下一批成本（不配对更早那批）
  Z3 持仓口径  ：零价 SELL 扣减持仓量，剩余 20股@第二批
  Z4 全零价    ：整段零价 SELL 不产生任何已实现盈亏，也不留持仓
  Z5 卖出超配  ：SELL 量 > FIFO 可配对量时不崩溃、不虚增盈亏
  Z6 顺序无关  ：输入行序打乱后结果稳定（内部按 time 排序生效）
  Z7 异常明细  ：detect_anomalies 需逐笔给出 时间/委托号/量/被消耗成本/待补状态
  Z8 源码断言  ：calc_realized 内不得再出现 `if price <= 0` 后的裸 continue

运行（miniqmt venv）：
  C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe research/tests/test_zero_price_sell_fifo.py
"""
import os
import sys
import types
from collections import defaultdict, deque

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

# ---- 屏蔽真 xtquant：strategy_capital / reconcile_trades 顶层只 import qmt_config，
# 本用例全离线，不需要行情/交易通道 ----
sys.modules.setdefault("xtquant", types.ModuleType("xtquant"))

import qmt_config as C  # noqa: E402
import strategy_capital as SC  # noqa: E402
import reconcile_trades as RT  # noqa: E402

PASS = 0
FAIL = 0

CODE = "603237.SH"


def _check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  -> %s" % (name, detail))


def _row(time_, code, side, vol, price, order_id=0):
    return {
        "time": time_,
        "code": code,
        "side": side,
        "vol": vol,
        "price": price,
        "score": "",
        "order_id": order_id,
        "account_id": C.ACCOUNT_ID,
    }


# 核心序列（刻意让有价 SELL 跨越批次边界）：
#   BUY  100 @10  -> 成本层1
#   SELL  40 @0   -> 成交价未回填：消耗 成本层1 的 40 股，不计盈亏
#   BUY  100 @12  -> 成本层2
#   SELL 140 @15  -> 必须配对 60@层1 + 80@层2，剩余 20股@层2
#
#   错口径（整条跳过零价）：配对 100@层1 + 40@层2，剩余 60股@层2
SEQ = [
    _row("2026-10-02 09:35:00", CODE, "BUY", 100, 10.0, 1001),
    _row("2026-10-02 10:05:00", CODE, "SELL", 40, 0.0, 1002),
    _row("2026-10-02 13:10:00", CODE, "BUY", 100, 12.0, 1003),
    _row("2026-10-02 14:20:00", CODE, "SELL", 140, 15.0, 1004),
]


class _Patch(object):
    """临时替换 qmt_config.load_trade_log_rows。"""

    def __init__(self, rows):
        self.rows = rows
        self.orig = None

    def __enter__(self):
        self.orig = C.load_trade_log_rows
        C.load_trade_log_rows = lambda: list(self.rows)
        return self

    def __exit__(self, *a):
        C.load_trade_log_rows = self.orig
        return False


def _cost_per(buy_price, vol, code=CODE):
    amt = buy_price * vol
    return (amt + SC._buy_fee(amt, code)) / vol


def _sell_net(price, vol, code=CODE):
    amt = price * vol
    return amt - SC._sell_fee(amt, code)


def _reference_fifo(rows):
    """独立参考实现：正确口径（零价消耗 FIFO 但不计盈亏）。

    注意 sell_fee 按 FIFO 分段各自计算（最低佣金逐段收取），
    参考实现与生产必须同口径，故此处同样分段。
    """
    q = defaultdict(deque)
    realized = 0.0
    for r in sorted(rows, key=lambda x: x.get("time", "")):
        code, side = r.get("code", ""), r.get("side", "")
        if not code or code in SC.EXCLUDE_CODES:
            continue
        try:
            vol = int(float(r["vol"]))
            price = float(r["price"])
        except (TypeError, ValueError, KeyError):
            continue
        if side == "BUY":
            amt = price * vol
            q[code].append((vol, (amt + SC._buy_fee(amt, code)) / vol))
        elif side == "SELL":
            sv = vol
            while sv > 0 and q[code]:
                v, cp = q[code][0]
                take = min(v, sv)
                if price > 0:
                    realized += _sell_net(price, take, code) - cp * take
                sv -= take
                q[code][0] = (v - take, cp)
                if q[code][0][0] <= 0:
                    q[code].popleft()
    pos = {}
    for code, dq in q.items():
        tv = sum(v for v, _ in dq)
        if tv > 0:
            pos[code] = (tv, sum(v * c for v, c in dq) / tv)
    return pos, realized


def test_z1_fifo_cost_consistency():
    """Z1 口径一致：两模块对零价 SELL 后的持仓成本 / 已实现必须完全一致。"""
    print("\n[Z1] 零价 SELL 后两模块 FIFO 成本一致性")
    with _Patch(SEQ):
        cap_real = SC.calc_realized()
        cap_pos = SC._fifo_positions_from_log()
    rt_pos, rt_real = RT.derive_positions(list(SEQ))

    _check(
        "Z1-1 strategy_capital 持仓 == reconcile_trades 持仓（量）",
        abs(cap_pos.get(CODE, (0, 0))[0] - rt_pos.get(CODE, (0, 0))[0]) < 1e-6,
        "cap=%s rt=%s" % (cap_pos.get(CODE), rt_pos.get(CODE)),
    )
    _check(
        "Z1-2 strategy_capital 持仓 == reconcile_trades 持仓（含费成本）",
        abs(cap_pos.get(CODE, (0, 0))[1] - rt_pos.get(CODE, (0, 0))[1]) < 1e-6,
        "cap=%s rt=%s" % (cap_pos.get(CODE), rt_pos.get(CODE)),
    )
    _check(
        "Z1-3 已实现盈亏两模块一致",
        abs(cap_real - rt_real) < 0.01,
        "cap=%.2f rt=%.2f" % (cap_real, rt_real),
    )
    ref_pos, ref_real = _reference_fifo(SEQ)
    _check(
        "Z1-4 两模块均等于独立参考实现（%.2f / %d股）" % (ref_real, ref_pos[CODE][0]),
        abs(cap_real - ref_real) < 0.01
        and abs(cap_pos.get(CODE, (0, 0))[0] - ref_pos[CODE][0]) < 1e-6,
        "got=%.2f/%s" % (cap_real, cap_pos.get(CODE)),
    )


def test_z2_realized_pairs_correct_lot():
    """Z2 已实现口径：有价 SELL 配对被零价扣减后的成本层，而非更早那批。"""
    print("\n[Z2] 零价 SELL 不计盈亏 / 有价 SELL 配对第二批成本")
    c1 = _cost_per(10.0, 100)
    c2 = _cost_per(12.0, 100)

    # 正确口径：零价先扣 40 股 → 层1 剩 60；有价 140 = 60@层1 + 80@层2
    want = _sell_net(15.0, 60) - c1 * 60 + _sell_net(15.0, 80) - c2 * 80
    # 错口径：零价被整条跳过 → 有价 140 = 100@层1 + 40@层2
    wrong = _sell_net(15.0, 100) - c1 * 100 + _sell_net(15.0, 40) - c2 * 40

    with _Patch(SEQ):
        cap_real = SC.calc_realized()
    _, rt_real = RT.derive_positions(list(SEQ))

    _check(
        "Z2-1 strategy_capital 已实现 == 正确口径（60@层1 + 80@层2 = %.2f）" % want,
        abs(cap_real - want) < 0.01,
        "got=%.2f want=%.2f" % (cap_real, want),
    )
    _check(
        "Z2-2 reconcile_trades 已实现同口径",
        abs(rt_real - want) < 0.01,
        "got=%.2f want=%.2f" % (rt_real, want),
    )
    _check(
        "Z2-3 旧错口径（100@层1 + 40@层2 = %.2f）已不生效" % wrong,
        abs(cap_real - wrong) > 0.01,
        "旧错值=%.2f 当前=%.2f 相等说明未修复" % (wrong, cap_real),
    )
    _check(
        "Z2-4 零价那笔本身不贡献盈亏（正确口径 != 错口径，差额=%.2f）" % abs(want - wrong),
        abs(abs(want - wrong) - abs(c1 * 40 - c2 * 40)) < 0.01,
        "差额=%.4f" % abs(want - wrong),
    )


def test_z3_position_remainder():
    """Z3 持仓口径：零价 SELL 扣减后，剩余 20股@第二批。"""
    print("\n[Z3] 零价 SELL 扣减持仓量")
    c2 = _cost_per(12.0, 100)
    with _Patch(SEQ):
        cap_pos = SC._fifo_positions_from_log()
    rt_pos, _ = RT.derive_positions(list(SEQ))

    _check("Z3-1 剩余持仓 20 股（错口径会得 60 股）",
           cap_pos.get(CODE, (0, 0))[0] == 20,
           "got=%s" % (cap_pos.get(CODE),))
    _check(
        "Z3-2 含费成本 == 剩余 20 股的第二批成本（%.6f）" % c2,
        abs(cap_pos.get(CODE, (0, 0))[1] - c2) < 1e-6,
        "got=%.6f want=%.6f" % (cap_pos.get(CODE, (0, 0))[1], c2),
    )
    _check(
        "Z3-3 reconcile_trades 持仓同口径",
        abs(rt_pos.get(CODE, (0, 0))[0] - 20) < 1e-6
        and abs(rt_pos.get(CODE, (0, 0))[1] - c2) < 1e-6,
        "got=%s" % (rt_pos.get(CODE),),
    )


def test_z4_all_zero_price():
    """Z4 整段零价：无盈亏、无残留持仓。"""
    print("\n[Z4] 整段零价 SELL")
    rows = [
        _row("2026-10-02 09:35:00", CODE, "BUY", 200, 20.0, 2001),
        _row("2026-10-02 10:05:00", CODE, "SELL", 120, 0.0, 2002),
        _row("2026-10-02 14:20:00", CODE, "SELL", 80, 0.0, 2003),
    ]
    with _Patch(rows):
        cap_real = SC.calc_realized()
        cap_pos = SC._fifo_positions_from_log()
    rt_pos, rt_real = RT.derive_positions(list(rows))

    _check("Z4-1 已实现 = 0", abs(cap_real) < 0.005, "got=%.4f" % cap_real)
    _check("Z4-2 reconcile 已实现 = 0", abs(rt_real) < 0.005, "got=%.4f" % rt_real)
    _check("Z4-3 无残留持仓（两模块）",
           cap_pos.get(CODE) is None and rt_pos.get(CODE) is None,
           "cap=%s rt=%s" % (cap_pos.get(CODE), rt_pos.get(CODE)))


def test_z5_oversell():
    """Z5 卖出超配：不崩溃、不虚增盈亏。"""
    print("\n[Z5] 卖出量 > FIFO 可配对量")
    rows = [
        _row("2026-10-02 09:35:00", CODE, "BUY", 100, 10.0, 3001),
        _row("2026-10-02 14:20:00", CODE, "SELL", 500, 15.0, 3002),
    ]
    with _Patch(rows):
        cap_real = SC.calc_realized()
    rt_pos, rt_real = RT.derive_positions(list(rows))

    want = _sell_net(15.0, 100) - _cost_per(10.0, 100) * 100
    _check("Z5-1 仅按可配对 100 股计盈亏（%.2f）" % want,
           abs(cap_real - want) < 0.01, "got=%.2f" % cap_real)
    _check("Z5-2 两模块一致", abs(cap_real - rt_real) < 0.01,
           "cap=%.2f rt=%.2f" % (cap_real, rt_real))
    _check("Z5-3 持仓清空", rt_pos.get(CODE) is None, "got=%s" % rt_pos.get(CODE))


def test_z6_time_order_stable():
    """Z6 输入行序打乱后结果稳定（内部按 time 排序生效）。"""
    print("\n[Z6] 行序无关性")
    shuffled = [SEQ[3], SEQ[0], SEQ[2], SEQ[1]]
    with _Patch(shuffled):
        a, ap = SC.calc_realized(), SC._fifo_positions_from_log()
    with _Patch(SEQ):
        b, bp = SC.calc_realized(), SC._fifo_positions_from_log()
    _check("Z6-1 已实现稳定", abs(a - b) < 0.01,
           "shuffled=%.4f sorted=%.4f" % (a, b))
    _check("Z6-2 持仓稳定",
           ap.get(CODE) == bp.get(CODE),
           "shuffled=%s sorted=%s" % (ap.get(CODE), bp.get(CODE)))


def test_z7_anomaly_detail():
    """Z7 异常报告必须逐笔可定位，而非只给代码汇总。"""
    print("\n[Z7] 零价成交异常明细")
    issues = RT.detect_anomalies(list(SEQ))
    text = "\n".join("%s|%s" % (c, d) for c, d in issues)
    zero_rows = [r for r in SEQ if r["side"] == "SELL" and float(r["price"]) <= 0]

    _check("Z7-1 检出零价卖出异常", len(issues) > 0, "issues=%s" % issues)
    _check("Z7-2 逐笔条数 == 零价笔数（%d）" % len(zero_rows),
           ("%d 笔" % len(zero_rows)) in text, text)
    for i, r in enumerate(zero_rows, 1):
        _check("Z7-3.%d 含成交时间 %s" % (i, r["time"]),
               r["time"] in text, text)
        _check("Z7-4.%d 含委托号 %d" % (i, r["order_id"]),
               str(r["order_id"]) in text, text)
        _check("Z7-5.%d 含成交量 %d 股" % (i, r["vol"]),
               ("%d 股" % r["vol"]) in text, text)
    _check("Z7-6 含被消耗的 FIFO 成本", ("成本" in text), text)
    _check("Z7-7 含待补状态标记", ("待补" in text) or ("未回填" in text), text)
    _check("Z7-8 含零价 SELL 消耗的股数说明", ("扣减" in text) or ("消耗" in text), text)

    # 接管前遗留旧仓的零价卖出不应污染策略口径
    legacy = [_row("2026-09-30 10:00:00", "000001.SZ", "SELL", 500, 0.0, 4001)]
    legacy_text = "\n".join("%s|%s" % (c, d) for c, d in RT.detect_anomalies(legacy))
    _check("Z7-9 未买入过的标的零价卖出不算策略异常",
           "000001.SZ" not in legacy_text, legacy_text)


def test_z8_source_assertions():
    """Z8 源码断言：禁止把整条零价 SELL 裸 continue 回来。"""
    print("\n[Z8] 源码约束")
    src_cap = open(os.path.join(PROJECT_ROOT, "strategy_capital.py"),
                   encoding="utf-8").read()
    start = src_cap.index("def calc_realized(")
    end = src_cap.index("def _query_positions_with_retry(")
    body = src_cap[start:end]

    _check("Z8-1 calc_realized 内无 `if price <= 0` 裸跳过",
           "if price <= 0" not in body,
           "仍存在整条跳过分支，会导致 FIFO 不消耗")
    _check("Z8-2 calc_realized 内零价仅作为不计盈亏的条件",
           "price > 0" in body, "缺少 price>0 条件包裹")
    _check("Z8-3 calc_realized docstring 说明零价语义",
           ("price=0" in body) or ("零价" in body) or ("未回填" in body),
           "缺少口径说明")


def main():
    print("=" * 74)
    print("零价 SELL 的 FIFO 口径统一回归测试（T-20261002-001 / 项2）")
    print("=" * 74)
    for fn in (test_z1_fifo_cost_consistency,
               test_z2_realized_pairs_correct_lot,
               test_z3_position_remainder,
               test_z4_all_zero_price,
               test_z5_oversell,
               test_z6_time_order_stable,
               test_z7_anomaly_detail,
               test_z8_source_assertions):
        try:
            fn()
        except Exception as e:
            global FAIL
            FAIL += 1
            print("  [FAIL] %s 抛出异常 -> %r" % (fn.__name__, e))
    print("-" * 74)
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())