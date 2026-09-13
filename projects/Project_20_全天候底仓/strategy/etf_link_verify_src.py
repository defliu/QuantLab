# -*- coding: utf-8 -*-
"""Project_20 全天候 - ETF 委托链路验证策略（模拟盘一次性验证单）。

设计：挂"不可能成交"的极端低价限价单（1 元/股）→ 验证委托进通道 → 反查订单 → 撤单。
全程零资金占用、零真实成交，验证的是：passorder 受理 / 代码格式 / 反查 / 撤单 market。

用法：粘贴进国金QMT客户端策略编辑器（账号 70180771），交易模式运行，看日志：
  [LINK][VERIFY] ... 各阶段标记

参数（可改）：
  VERIFY_CODES = ["510300.SH", "511010.SH", "518880.SH", "513100.SH"]  # 待验证标的
  VOLUME = 100   # 1手=100份（ETF按份）

注：ETF 单位是"份"，1手=100份；转债是"张"。本脚本仅验证 ETF。
"""
# QMT Python 3.6.8 兼容：无 f-string / 无类型注解 / 无 walrus

BUILD_TAG = "__BUILD_TAG__"   # 构建时自动替换
ACCOUNT_ID = "70180771"

VERIFY_CODES = ["510300.SH", "511010.SH", "518880.SH", "513100.SH"]
VOLUME = 100
EXTREME_PRICE = 1.0           # 不可能成交价（ETF 现价远高于 1 元）
RETRY_LOOKUP = 5
LOOKUP_INTERVAL = 1.0


def init(C):
    C.verify_index = 0          # 当前验证到第几个标的
    C.verify_state = 0          # 0=待挂单 1=已挂待反查 2=已反查待撤单
    C.verify_code = ""
    C.verify_oid = ""
    C.log("[LINK][VERIFY] init BUILD_TAG=%s account=%s" % (BUILD_TAG, ACCOUNT_ID))
    C.log("[LINK][VERIFY] 待验证标的: %s" % (" ".join(VERIFY_CODES)))


def handlebar(C):
    import time
    t = time.time()
    if C.verify_index >= len(VERIFY_CODES):
        C.log("[LINK][VERIFY] 全部验证完成，策略可停。")
        return

    code = VERIFY_CODES[C.verify_index]

    # ---- 状态0：挂极端低价单 ----
    if C.verify_state == 0:
        ret = passorder(23, 1101, ACCOUNT_ID, code, 11, EXTREME_PRICE, VOLUME, C)
        C.log("[LINK][VERIFY] 挂单 BUY %s 1.00 x%d ret=%s" % (code, VOLUME, ret))
        C.verify_code = code
        C.verify_state = 1
        C.verify_t0 = t
        return

    # ---- 状态1：反查是否进通道 ----
    if C.verify_state == 1:
        if t - C.verify_t0 < 2.0:
            return
        found = 0
        try:
            data = C.get_trade_detail_data(ACCOUNT_ID, "stock", "order")
            for o in data or []:
                oid = getattr(o, "m_nOrderID", "") or ""
                stk = getattr(o, "m_strInstrumentID", "") or getattr(o, "m_strCode", "") or ""
                if stk == code.split(".")[0]:
                    found += 1
                    C.verify_oid = oid
        except Exception as e:
            C.log("[LINK][VERIFY] 反查异常 %s" % e)
        if found > 0:
            C.log("[LINK][VERIFY] 反查成功：%s 委托已进通道（order 记录 %d 条）" % (code, found))
        else:
            C.log("[LINK][VERIFY] 反查无记录：%s 委托未进通道（废单？查 QMT 主日志）" % code)
        C.verify_state = 2
        C.verify_t0 = t
        return

    # ---- 状态2：撤单（生产惯用法：passorder(24,1101,acct,code,5,order_id,0,remark,2,"",C)）----
    if C.verify_state == 2:
        if t - C.verify_t0 < 2.0:
            return
        if C.verify_oid:
            try:
                ret = passorder(24, 1101, ACCOUNT_ID, code, 5, C.verify_oid, 0, "LINK撤单", 2, "", C)
                C.log("[LINK][VERIFY] 撤单 ret=%s oid=%s" % (ret, C.verify_oid))
            except Exception as e:
                C.log("[LINK][VERIFY] 撤单异常 %s" % e)
        else:
            C.log("[LINK][VERIFY] 无 oid 跳过撤单（可能未进通道）")
        C.verify_index += 1
        C.verify_state = 0
        C.log("[LINK][VERIFY] 下一个标的: %s" % (
            VERIFY_CODES[C.verify_index] if C.verify_index < len(VERIFY_CODES) else "无"))


def exit(C):
    C.log("[LINK][VERIFY] exit BUILD_TAG=%s" % BUILD_TAG)
