# -*- coding: utf-8 -*-
"""V1.3 风控桥「真实成交均价 + 跳空/滑点留痕」专项测试。

覆盖项 3 的核心链路：
  A. _query_deals 可用性三态降级（接口缺失/单次异常/连续异常/空列表）
  B. _query_deals 归属过滤（代码缺失/代码不符/无委托号/同标的历史成交/多轮委托号）
  C. _resolve_fill_avg 成交量覆盖判定与加权均价
  D. _resolve_px 降级协议 fill_avg -> order_obj -> ref_price -> unresolved
  E. _adverse_slippage 方向（卖出卖低为正/买入买高为正）
  F. _gap_pct 跳空幅度与止损线
  G. _slippage_alert 只告警不拦单 + 格式化正确性（2026-10-02 优先级 bug）
  H. _write_fill 统一出口字段完整性与消费方可记账性
  I. _acc_order_id / _refresh_last_price

不依赖 miniQMT，全部用桩对象驱动。
"""
import importlib.util
import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(PROJ, "strategy", "strategy_p16_v13_risk_src.py")

PASS = [0]
FAIL = []


def check(name, cond, extra=""):
    if cond:
        PASS[0] += 1
    else:
        FAIL.append("%s %s" % (name, extra))
        print("[FAIL] %s %s" % (name, extra))


def approx(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


def load_module():
    """加载风控桥源码为独立模块（QMT 运行时才有 init/handlebar，这里只测纯函数）。

    exec 进真实 ModuleType 的 __dict__，这样：
      · 函数里的 `global` 声明改的就是模块全局（状态可测）
      · `globals().get("get_trade_detail_data")` 能取到测试注入的桩
    """
    with io.open(SRC, "r", encoding="utf-8") as f:
        src = f.read()
    mod = types.ModuleType("v13_risk_under_test")
    mod.__file__ = SRC
    exec(compile(src, SRC, "exec"), mod.__dict__)
    # m.g 指向模块全局字典，便于按全局变量读写
    mod.g = mod.__dict__
    return mod


class _FakeTime(object):
    """替换模块内 time 引用，避免真实 sleep 拖慢测试 / 污染全局 time 模块。"""

    def __init__(self):
        self.slept = []

    def sleep(self, s):
        self.slept.append(s)

    def time(self):
        return 1000000.0

    def strftime(self, fmt):
        return "2026-10-02 09:30:00"


def stub_time(m):
    ft = _FakeTime()
    m.g["time"] = ft
    return ft


class Deal(object):
    def __init__(self, code=None, oid=None, vol=0, px=0.0,
                 oid_attr="m_nOrderID", code_attr="m_strInstrumentID",
                 vol_attr="m_nVolume", px_attr="m_dAvgPrice"):
        if code is not None:
            setattr(self, code_attr, code)
        if oid is not None:
            setattr(self, oid_attr, oid)
        if vol:
            setattr(self, vol_attr, vol)
        if px:
            setattr(self, px_attr, px)


class Order(object):
    def __init__(self, avg=0.0, px=0.0):
        if avg:
            self.m_dAvgPrice = avg
        if px:
            self.m_dPrice = px


def reset_deal_probe(m):
    m._g_deal_ok = None
    m._g_deal_err_count = 0
    m._g_deal_no_oid = False
    m._g_slippage_alert_count = 0


# ---------------------------------------------------------------- A. 可用性三态
def test_a_probe(m):
    reset_deal_probe(m)

    # A1 接口不存在 -> 永久降级
    calls = []

    def no_api(*a, **k):
        return []

    saved = m.g.get("get_trade_detail_data")
    m.g.pop("get_trade_detail_data", None)
    r = m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001"])
    check("A1 接口缺失返回空", r == [], r)
    check("A1 接口缺失永久降级", m._g_deal_ok is False, m._g_deal_ok)
    if saved is not None:
        m.g["get_trade_detail_data"] = saved

    # A2 单次异常 -> 不永久降级
    reset_deal_probe(m)
    state = {"n": 0}

    def once_err(acct, mkt, tp):
        state["n"] += 1
        raise RuntimeError("net jitter")

    m.g["get_trade_detail_data"] = once_err
    m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001"])
    check("A2 单次异常不永久降级", m._g_deal_ok is None, m._g_deal_ok)
    check("A2 单次异常计数=1", m._g_deal_err_count == 1, m._g_deal_err_count)

    # A3 连续异常达阈值 -> 永久降级
    reset_deal_probe(m)

    def always_err(acct, mkt, tp):
        raise RuntimeError("down")

    m.g["get_trade_detail_data"] = always_err
    for _ in range(m.DEAL_QUERY_MAX_ERR):
        m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001"])
    check("A3 连续异常永久降级", m._g_deal_ok is False, m._g_deal_ok)
    check("A3 异常计数达阈值",
          m._g_deal_err_count == m.DEAL_QUERY_MAX_ERR, m._g_deal_err_count)

    # A4 降级后短路，不再调用接口
    reset_deal_probe(m)
    m.g["get_trade_detail_data"] = lambda a, b, c: (_ for _ in ()).throw(
        AssertionError("降级后不应再调接口"))
    m._g_deal_ok = False
    r = m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001"])
    check("A4 降级后短路返回空", r == [], r)

    # A5 空列表 = 尚未落库，保留重试资格（不永久降级）
    reset_deal_probe(m)
    m.g["get_trade_detail_data"] = lambda a, b, c: []
    r = m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001"])
    check("A5 空列表返回空", r == [], r)
    check("A5 空列表不永久降级", m._g_deal_ok is not False, m._g_deal_ok)
    check("A5 空列表不清零异常计数", m._g_deal_err_count == 0, m._g_deal_err_count)
    # 空列表时保持 None（未探测），既不当可用也不当不可用——下轮仍可再查
    check("A5 空列表保持未探测态", m._g_deal_ok is None, m._g_deal_ok)

    # A6 异常后恢复正常 -> 计数清零
    reset_deal_probe(m)
    seq = {"n": 0}

    def recover(acct, mkt, tp):
        seq["n"] += 1
        if seq["n"] == 1:
            raise RuntimeError("jitter")
        return [Deal("300413.SZ", "1001", 1000, 10.0)]

    m.g["get_trade_detail_data"] = recover
    m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001"])
    check("A6 首次异常计数=1", m._g_deal_err_count == 1, m._g_deal_err_count)
    r = m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001"])
    check("A6 恢复后有明细", len(r) == 1, r)
    check("A6 恢复后异常计数清零", m._g_deal_err_count == 0, m._g_deal_err_count)
    check("A6 恢复后接口可用", m._g_deal_ok is True, m._g_deal_ok)


# ---------------------------------------------------------------- B. 归属过滤
def test_b_attribution(m):
    # B1 代码不一致 -> 拒绝
    reset_deal_probe(m)
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", "1001", 1000, 10.0),
        Deal("600000.SH", "1001", 5000, 88.0),
    ]
    r = m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001"])
    check("B1 代码不符被拒", len(r) == 1 and r[0][1] == 1000, r)

    # B2 代码字段缺失 -> 拒绝（宁缺勿滥）
    reset_deal_probe(m)
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal(None, "1001", 1000, 10.0),
    ]
    r = m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001"])
    check("B2 代码缺失被拒", r == [], r)

    # B3 同标的历史成交混入（委托号不同）-> 拒绝
    reset_deal_probe(m)
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", "1001", 1000, 10.0),
        Deal("300413.SZ", "9999", 3000, 9.0),
    ]
    r = m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001"])
    check("B3 非本次委托号被拒", len(r) == 1 and r[0][1] == 1000, r)

    # B4 全无委托号 -> 拒绝，且只告警一次
    reset_deal_probe(m)
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", None, 1000, 10.0),
        Deal("300413.SZ", None, 2000, 9.5),
    ]
    r1 = m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001"])
    check("B4 全无委托号被拒", r1 == [], r1)
    check("B4 无委托号告警标志置位", m._g_deal_no_oid is True, m._g_deal_no_oid)

    # B5 多轮委托号全部纳入
    reset_deal_probe(m)
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", "1001", 600, 10.0),
        Deal("300413.SZ", "1002", 400, 10.5),
        Deal("300413.SZ", "1003", 999, 99.0),
    ]
    r = m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001", "1002"])
    check("B5 多轮委托号纳入", len(r) == 2, r)
    check("B5 总量=1000", sum(x[1] for x in r) == 1000, sum(x[1] for x in r))

    # B6 遍历顺序不影响结果（无委托号记录在前）
    reset_deal_probe(m)
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", None, 7000, 1.0),
        Deal("300413.SZ", "1001", 1000, 10.0),
    ]
    r = m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001"])
    check("B6 顺序无关，无委托号不放行", len(r) == 1 and r[0][1] == 1000, r)

    # B7 无待匹配委托号 -> 拒绝
    reset_deal_probe(m)
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", "1001", 1000, 10.0),
    ]
    r = m._query_deals(types.SimpleNamespace(), "300413.SZ", [])
    check("B7 无oids拒绝", r == [], r)

    # B8 代码归一化：SZ 后缀写法混用仍可匹配
    reset_deal_probe(m)
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", "1001", 1000, 10.0),
    ]
    r = m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001"])
    check("B8 标准代码匹配", len(r) == 1, r)
    r = m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001"])
    check("B8 标准代码幂等", len(r) == 1, r)

    # B9 字段名兜底：不同版本字段名
    reset_deal_probe(m)
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", "1001", 1000, 10.0, oid_attr="m_strOrderSysID"),
    ]
    r = m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001"])
    check("B9 备用委托号字段", len(r) == 1, r)

    # B10 无效量/价记录被跳过
    reset_deal_probe(m)
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", "1001", 0, 10.0),
        Deal("300413.SZ", "1001", 1000, 0.0),
        Deal("300413.SZ", "1001", 1000, 10.0),
    ]
    r = m._query_deals(types.SimpleNamespace(), "300413.SZ", ["1001"])
    check("B10 无效量价跳过", len(r) == 1 and r[0][1] == 1000, r)


# ---------------------------------------------------------------- C. 加权均价
def test_c_resolve_avg(m):
    reset_deal_probe(m)
    # C1 单笔加权均价
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", "1001", 1000, 10.00),
    ]
    v = m._resolve_fill_avg(types.SimpleNamespace(), "300413.SZ", ["1001"], 1000)
    check("C1 单笔均价", approx(v, 10.0), v)

    # C2 多笔加权（不是算术平均）
    reset_deal_probe(m)
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", "1001", 1000, 10.00),
        Deal("300413.SZ", "1002", 3000, 11.00),
    ]
    v = m._resolve_fill_avg(types.SimpleNamespace(), "300413.SZ", ["1001", "1002"], 4000)
    check("C2 加权均价=(1000*10+3000*11)/4000", approx(v, 10.75), v)

    # C3 明细量不足 -> 降级返回 0（不用残缺明细冒充真实均价）
    reset_deal_probe(m)
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", "1001", 1000, 10.00),
    ]
    stub_time(m)
    v = m._resolve_fill_avg(types.SimpleNamespace(), "300413.SZ", ["1001"], 4000)
    check("C3 量不足降级", approx(v, 0.0), v)

    # C4 deal 写入延迟 -> 重试后拿到
    reset_deal_probe(m)
    state = {"n": 0}

    def delayed(acct, mkt, tp):
        state["n"] += 1
        if state["n"] < 2:
            return []
        return [Deal("300413.SZ", "1001", 2000, 9.80)]

    m.g["get_trade_detail_data"] = delayed
    ft = stub_time(m)
    v = m._resolve_fill_avg(types.SimpleNamespace(), "300413.SZ", ["1001"], 2000)
    check("C4 延迟后重试成功", approx(v, 9.80), v)
    check("C4 至少重试2次", state["n"] >= 2, state["n"])
    check("C4 重试间隔已sleep", len(ft.slept) >= 1, ft.slept)

    # C5 无 oids / expect_vol<=0 直接降级
    reset_deal_probe(m)
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", "1001", 2000, 9.8),
    ]
    check("C5 无oids降级",
          approx(m._resolve_fill_avg(types.SimpleNamespace(), "300413.SZ", [], 2000), 0.0))
    check("C5 零量降级",
          approx(m._resolve_fill_avg(types.SimpleNamespace(), "300413.SZ", ["1001"], 0), 0.0))


# ---------------------------------------------------------------- D. 降级协议
def test_d_resolve_px(m):
    reset_deal_probe(m)
    ref = 10.00

    # D1 deal 可用 -> fill_avg 优先
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", "1001", 1000, 9.80),
    ]
    p, src, adv = m._resolve_px(
        types.SimpleNamespace(), "300413.SZ", "SELL", ["1001"], 1000, Order(avg=9.99), ref)
    check("D1 优先真实均价", approx(p, 9.80) and src == "fill_avg", (p, src))
    check("D1 卖出卖低为不利滑点", approx(adv, 0.02), adv)

    # D2 deal 不可用 -> order_obj 降级
    reset_deal_probe(m)
    m.g.pop("get_trade_detail_data", None)
    p, src, adv = m._resolve_px(
        types.SimpleNamespace(), "300413.SZ", "SELL", ["1001"], 1000, Order(avg=9.99), ref)
    check("D2 降级订单对象价", approx(p, 9.99) and src == "order_obj", (p, src))

    # D3 无 matched -> ref_price
    p, src, adv = m._resolve_px(
        types.SimpleNamespace(), "300413.SZ", "SELL", ["1001"], 1000, None, ref)
    check("D3 降级参考价", approx(p, 10.00) and src == "ref_price", (p, src))
    check("D3 参考价滑点记0（不臆造）", approx(adv, 0.0), adv)

    # D4 全无 -> unresolved，价格 0（消费方不得记账）
    p, src, adv = m._resolve_px(
        types.SimpleNamespace(), "300413.SZ", "SELL", ["1001"], 1000, None, 0.0)
    check("D4 unresolved价=0", approx(p, 0.0) and src == "unresolved", (p, src))

    # D5 买入方向：买高为不利
    reset_deal_probe(m)
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", "1001", 1000, 10.50),
    ]
    p, src, adv = m._resolve_px(
        types.SimpleNamespace(), "300413.SZ", "BUY", ["1001"], 1000, None, ref)
    check("D5 买入买高为不利", approx(adv, 0.05), adv)


# ---------------------------------------------------------------- E. 滑点方向
def test_e_slippage(m):
    check("E1 卖出卖低为正", approx(m._adverse_slippage("SELL", 10.0, 9.0), 0.10))
    check("E2 卖出卖高为负", approx(m._adverse_slippage("SELL", 10.0, 11.0), -0.10))
    check("E3 买入买高为正", approx(m._adverse_slippage("BUY", 10.0, 11.0), 0.10))
    check("E4 买入买低为负", approx(m._adverse_slippage("BUY", 10.0, 9.0), -0.10))
    check("E5 无基准返回0", approx(m._adverse_slippage("SELL", 0.0, 9.0), 0.0))
    check("E6 成交价0返回0", approx(m._adverse_slippage("SELL", 10.0, 0.0), 0.0))


# ---------------------------------------------------------------- F. 跳空
def test_f_gap(m):
    cost = 10.00
    line = cost * (1.0 - m.STOP_LOSS_PCT)
    # F1 未破止损线 -> 无跳空
    check("F1 线内无跳空", approx(m._gap_pct(cost, line + 0.01), 0.0))
    # F2 恰好在线上 -> 0
    check("F2 线上无跳空", approx(m._gap_pct(cost, line), 0.0))
    # F3 跳空跌破
    trig = cost * (1.0 - m.STOP_LOSS_PCT - 0.03)
    exp = (line - trig) / cost
    check("F3 跳空幅度正确", approx(m._gap_pct(cost, trig), exp), m._gap_pct(cost, trig))
    # F4 缺基准不臆造
    check("F4 无成本无跳空", approx(m._gap_pct(0.0, 8.0), 0.0))
    check("F5 无触发价无跳空", approx(m._gap_pct(10.0, 0.0), 0.0))
    # F6 止损线与配置一致（-7%）
    check("F6 止损线=成本*0.93", approx(line, 9.30), line)


# ---------------------------------------------------------------- G. 告警
def test_g_alert(m):
    reset_deal_probe(m)
    # G1 正常区间不告警
    txt = m._slippage_alert("s1", "300413.SZ", "SELL", "fill_avg", 10.0, 9.9, 0.01, 0.0)
    check("G1 正常无告警", txt == "", txt)
    check("G1 计数未增", m._g_slippage_alert_count == 0, m._g_slippage_alert_count)

    # G2 滑点超限 -> 告警文本 + 计数
    reset_deal_probe(m)
    txt = m._slippage_alert("s1", "300413.SZ", "SELL", "fill_avg", 10.0, 9.5, 0.05, 0.0)
    check("G2 滑点超限告警", "adverse_slippage=5.00%" in txt, txt)
    check("G2 计数=1", m._g_slippage_alert_count == 1, m._g_slippage_alert_count)
    # G3 格式化正确（不得出现 %100 重复）——2026-10-02 优先级 bug 回归
    check("G3 无重复百分号", "%%" not in txt.replace("5.00%", ""), txt)
    check("G3 长度合理", len(txt) < 60, len(txt))

    # G4 跳空超限 -> 告警
    reset_deal_probe(m)
    txt = m._slippage_alert("s1", "300413.SZ", "SELL", "fill_avg", 10.0, 10.0, 0.0, 0.05)
    check("G4 跳空告警", "gap_exceeded=5.00%" in txt, txt)

    # G5 双超限同时输出
    reset_deal_probe(m)
    txt = m._slippage_alert("s1", "300413.SZ", "SELL", "fill_avg", 10.0, 9.0, 0.10, 0.05)
    check("G5 双告警", "adverse_slippage" in txt and "gap_exceeded" in txt, txt)

    # G6 容忍区间内不告警（2%跳空 < 3%滑点阈值）
    reset_deal_probe(m)
    txt = m._slippage_alert("s1", "300413.SZ", "SELL", "fill_avg", 10.0, 9.8, 0.02, 0.02)
    check("G6 容忍内不告警", txt == "", txt)

    # G7 边界：恰好等于阈值不算超限
    reset_deal_probe(m)
    txt = m._slippage_alert("s1", "300413.SZ", "SELL", "fill_avg",
                            10.0, 10.0 * (1 - m.MAX_ADVERSE_SLIPPAGE_PCT),
                            m.MAX_ADVERSE_SLIPPAGE_PCT, 0.0)
    check("G7 等于阈值不告警", txt == "", txt)


# ---------------------------------------------------------------- H. 统一出口
def test_h_write_fill(m):
    reset_deal_probe(m)
    written = []
    m._add_or_update_fill = lambda date, rec: written.append((date, rec))

    info = {
        "code": "300413.SZ",
        "bridge_code": "300413.SZ",
        "action": "SELL",
        "price": 9.50,
        "trigger_price": 9.50,
        "cost_price": 10.00,
        "last_price": 9.50,
        "order_id": "1001",
        "order_ids": ["1001", "1002"],
        "retry": 2,
        "risk_reason": "stop_loss",
    }
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", "1001", 600, 9.40),
        Deal("300413.SZ", "1002", 400, 9.30),
    ]
    rec = m._write_fill(types.SimpleNamespace(), "20261002", "s1", info,
                        "300413.SZ", "SELL", 1000, "FILLED", "sys1",
                        "deal confirmed", matched=Order(avg=9.0))
    check("H1 已写入一条", len(written) == 1, len(written))
    d, r = written[0]
    check("H1 日期一致", d == "20261002", d)
    check("H1 用真实均价", approx(r["price"], (600 * 9.40 + 400 * 9.30) / 1000.0), r["price"])
    check("H1 价源标注", r["px_source"] == "fill_avg", r["px_source"])
    check("H1 状态语义保留", r["status"] == "FILLED", r["status"])
    check("H1 保留原因", "deal confirmed" in r["reason"], r["reason"])
    check("H1 保留风险原因字段", r["retry"] == 2, r.get("retry"))
    check("H1 全委托号留痕", r["order_ids"] == ["1001", "1002"], r["order_ids"])
    check("H1 触发价留痕", approx(r["trigger_price"], 9.50), r["trigger_price"])
    check("H1 滑点留痕", "adverse_slippage" in r, list(r.keys()))
    check("H1 跳空留痕", "gap_pct" in r, list(r.keys()))
    check("H1 消费方可记账", float(r["price"]) > 0 and int(r["vol"]) > 0)

    # H2 消费方只认 FILLED/PARTIAL_FILLED
    check("H2 FILLED 可记账", r["status"] in ("FILLED", "PARTIAL_FILLED"))
    rec2 = m._write_fill(types.SimpleNamespace(), "20261002", "s2", info,
                         "300413.SZ", "SELL", 500, "PARTIAL_FILLED", "sys2",
                         "partial", matched=None)
    check("H2 PARTIAL 可记账", rec2["status"] == "PARTIAL_FILLED")
    rec3 = m._write_fill(types.SimpleNamespace(), "20261002", "s3", info,
                         "300413.SZ", "SELL", 0, "CANCELED", "sys3", "canceled")
    check("H2 CANCELED 不可记账", rec3["status"] not in ("FILLED", "PARTIAL_FILLED"))

    # H3 order_id 未进 order_ids 时自动补
    reset_deal_probe(m)
    written[:] = []
    info2 = dict(info)
    info2["order_ids"] = []
    info2["order_id"] = "7777"
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", "7777", 1000, 9.5),
    ]
    r4 = m._write_fill(types.SimpleNamespace(), "20261002", "s4", info2,
                       "300413.SZ", "SELL", 1000, "FILLED", "sys4", "x")
    check("H3 自动补订单号", "7777" in r4["order_ids"], r4["order_ids"])

    # H4 跳空破位时 reason 带告警尾
    reset_deal_probe(m)
    written[:] = []
    info3 = {
        "code": "300413.SZ", "bridge_code": "300413.SZ", "action": "SELL",
        "trigger_price": 8.50, "cost_price": 10.00, "order_id": "1001",
        "order_ids": ["1001"], "retry": 0,
    }
    m.g["get_trade_detail_data"] = lambda a, b, c: [
        Deal("300413.SZ", "1001", 1000, 8.45),
    ]
    r5 = m._write_fill(types.SimpleNamespace(), "20261002", "s5", info3,
                       "300413.SZ", "SELL", 1000, "FILLED", "sys5", "stop")
    check("H4 跳空写入reason", "gap_exceeded" in r5["reason"], r5["reason"])
    check("H4 跳空幅度记录", r5["gap_pct"] > m.MAX_GAP_TOLERANCE_PCT, r5["gap_pct"])


# ---------------------------------------------------------------- I. 辅助
def test_i_helpers(m):
    # I1 _acc_order_id 去重且保序
    info = {}
    m._acc_order_id(info, "1001")
    m._acc_order_id(info, "1002")
    m._acc_order_id(info, "1001")
    m._acc_order_id(info, "")
    m._acc_order_id(info, None)
    check("I1 委托号去重保序", info["order_ids"] == ["1001", "1002"], info.get("order_ids"))

    # I2 _refresh_last_price 成功
    class Ctx(object):
        def get_full_tick(self, codes):
            return {"300413.SZ": {"lastPrice": 9.87}}

    v = m._refresh_last_price(Ctx(), "300413.SZ", 8.0)
    check("I2 刷新最新价", approx(v, 9.87), v)

    # I3 取不到行情退回 fallback（不阻断重试）
    class BadCtx(object):
        def get_full_tick(self, codes):
            raise RuntimeError("no tick")

    v = m._refresh_last_price(BadCtx(), "300413.SZ", 8.0)
    check("I3 行情失败退fallback", approx(v, 8.0), v)

    # I4 无效最新价退回 fallback
    class ZeroCtx(object):
        def get_full_tick(self, codes):
            return {"300413.SZ": {"lastPrice": 0}}

    v = m._refresh_last_price(ZeroCtx(), "300413.SZ", 8.0)
    check("I4 零价退fallback", approx(v, 8.0), v)


def main():
    m = load_module()
    for fn in (test_a_probe, test_b_attribution, test_c_resolve_avg,
               test_d_resolve_px, test_e_slippage, test_f_gap,
               test_g_alert, test_h_write_fill, test_i_helpers):
        try:
            fn(m)
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAIL.append("%s 抛异常: %s" % (fn.__name__, e))
    print("=" * 60)
    print("PASS=%d FAIL=%d" % (PASS[0], len(FAIL)))
    for f in FAIL:
        print("  [FAIL] %s" % f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())