# coding: utf-8
"""委托守护：下单后轮询成交状态，未全部成交则撤单重试（更新价格），直到成交或涨跌停跳过。

核心逻辑（rebalance_daily / qmt_clear 等真实委托复用）：
  1. 下单 order_stock → order_id
  2. 轮询 query_stock_orders（间隔 3s，单次最长等 WAIT_SECONDS=60s，即"委托后一分钟查一下"）
  3. 全部成交（traded_volume >= order_volume）→ 返回成功
  4. 废单/已撤 → 若已触涨跌停则跳过(limit)；否则更新价格重新委托
  5. 单次等待超时仍未全成 → cancel_order_stock 撤单 → 更新价格重新委托
  6. 最多 MAX_RETRY 次；期间检测到涨跌停（买入涨停封板/卖出跌停封板）立即跳过
  7. 部分成交：撤单后仅重试剩余未成交部分

返回：
  {"ok": bool, "action": "FILLED"|"LIMIT_SKIP"|"CANCELED_TIMEOUT"|"REJECTED"|"ERROR",
   "traded_vol": int, "order_id": int, "attempts": int, "note": str,
   "avg_price": float|None, "order_ids": list[int]}

ok 语义（T-20260930-001 厘清）：ok=True 仅见于「有已确认成交」的返回（FILLED / 有成交后放弃重挂）；
ok=False **不代表零成交**——LIMIT_SKIP/CANCELED_TIMEOUT/ERROR 可能带 traded_vol>0（前几轮重试
的真实成交）。调用方记账必须以 traded_vol>0 为唯一门槛，禁止 `ok and traded_vol>0` 双重门槛
（2026-09-30 603237.SH 事故：撤单窗口内全部成交但 ok=False → 记账被跳过 → 账实脱节）。

avg_price（T-20261002-001 新增）：真实成交均价 = Σ(成交明细 traded_volume×traded_price)/Σ成交量，
按本次守护期间**全部**委托 order_id 汇总（多轮重挂 + 撤单窗口部分成交都要计入），故为加权均价；
成交明细同时按 (order_id, stock_code) 双重过滤（query_stock_trades 返回全账户当日明细，
只按单号过滤会被同号异标的脏明细污染均价）。
成交明细不可用（接口异常 / 非交易时段阻塞 / 明细未同步 / 明细量不足）时返回 None，
**调用方必须用 resolve_fill_price() 决定记账价**：
  - 真实均价 → "fill_avg"（权威口径，正常路径不再告警）
  - 无均价但有参考价 → "ref_price"（近似值，须落待补价队列核对）
  - 两者皆无 → "unresolved"，price=0.0 仅作**显式待补标记**，此时必须
    record_pending_fill() 落 JSONL 队列并告警，下游不得把该 0 当真实成交价结算盈亏。
即：除已落待补队列的 unresolved 标记外，**禁止静默把 0 写进成交记录**（0 价 SELL 会让 FIFO
已实现盈亏永久缺口，并污染持仓成本推导）。
"""
import json
import os
import sys
import time

import qmt_config as C

sys.path.append(C.XTPACK)

# 「已成交但成交价未确认」的待补价队列（T-20261002-001）。
# 成交是既成事实、成交量必须入账，但成交价缺失不能静默写 0（0 价 SELL 会让 FIFO 已实现
# 盈亏永久缺口并污染持仓成本推导）。故：照常写成交行（price=0 作为待补标记）+ 落本队列，
# 供后续用 query_stock_trades 回补真实成交均价。
PENDING_FILL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "data", "qmt_pending_fills.jsonl")


# 订单状态（xtconstant）
ORDER_REPORTED = 50       # 已报
ORDER_REPORTED_CANCEL = 51  # 已报待撤
ORDER_PARTSUCC_CANCEL = 52  # 部成待撤
ORDER_PART_CANCEL = 53    # 部撤
ORDER_CANCELED = 54       # 已撤
ORDER_PART_SUCC = 55      # 部成
ORDER_SUCCEEDED = 56      # 已成
ORDER_JUNK = 57           # 废单

WAIT_SECONDS = 60          # 单次委托最长等待（秒）
POLL_INTERVAL = 3          # 轮询间隔（秒）
# ---- 总守护窗口权衡（2026-09-14 P1 修复）----
# AGENTS「实盘执行红线」：买入 pending 超时必须重试，语义口径为 300s×3 次 ≈ 15 分钟仍不成交才放弃。
# 此前 60s×5=5 分钟（601890.SH 案例实证追不上高开低走行情）。现取 60s×10=10 分钟折中：
#   ① 单轮 60s 已覆盖限价单短期撮合窗口，轮次越多 → 追价机会越多（第 2 轮起每轮刷新现价），
#      比单轮拉长到 300s 更容易跟上移动行情，滑点也更可控（逐轮小步追，而非一次挂高）；
#   ② rebalance_daily 换仓为多笔串行，单笔上限 10 分钟可防整轮换仓窗口过多溢出交易时段；
#   ③ 总时长 10 分钟 < 红线 15 分钟，属保守侧：红线语义是"不得早于约 15 分钟放弃"，并非要求等满。
# 不取 MAX_RETRY=15（=15 分钟满格）：多笔串行时整轮换仓最坏 10 只 × 15 分钟会溢出当日交易时段。
MAX_RETRY = 10             # 最大撤单重试次数（总窗口 ≈ 60s×10 = 10 分钟）

# 成交均价回填的短轮询（T-20261002-001）：成交明细常晚于委托状态同步，
# 少量重试可显著提高拿到真实均价的成功率；总耗时 ≈ (3-1)×1s，远小于下单守护窗口。
_AVGPRICE_RETRIES = 3
_AVGPRICE_RETRY_INTERVAL = 1


def get_limit_prices(code):
    """取涨跌停价 (up, down)。失败返回 None。"""
    try:
        from xtquant import xtdata
        d = xtdata.get_instrument_detail(code)
        if d:
            up = float(d.get("UpStopPrice", 0) or 0)
            down = float(d.get("DownStopPrice", 0) or 0)
            if up > 0 and down > 0:
                return up, down
    except Exception as e:
        print(f"    !! {code} 涨跌停价查询异常: {e!r}")
    return None


def check_limit_skip(code, side, last_price):
    """判断是否应跳过（触板无法成交）。side: 'BUY'/'SELL'。返回 (skip, note)。"""
    lim = get_limit_prices(code)
    if not lim:
        return False, "无涨跌停数据，不跳过"
    up, down = lim
    if side == "BUY" and last_price >= up:
        return True, f"现价{last_price:.2f}≥涨停{up:.2f}，买单无法成交"
    if side == "SELL" and last_price <= down:
        return True, f"现价{last_price:.2f}≤跌停{down:.2f}，卖单无法成交"
    return False, ""


def _order(trader, account, code, side, vol, price, remark):
    """下单，返回 order_id（>0 成功，-1 失败）。"""
    from xtquant import xtconstant
    if side == "BUY":
        order_type = xtconstant.STOCK_BUY
        price_type = xtconstant.FIX_PRICE if C.BUY_PRICE_TYPE == "FIX" else xtconstant.LATEST_PRICE
        px = price if price_type == xtconstant.FIX_PRICE else 0.0
    else:
        order_type = xtconstant.STOCK_SELL
        price_type = xtconstant.LATEST_PRICE if C.AUTO_SELL_PRICE_TYPE == "LATEST" else xtconstant.FIX_PRICE
        px = 0.0 if price_type == xtconstant.LATEST_PRICE else round(price * 0.995, 2)
    return trader.order_stock(account, code, order_type, vol, price_type, px,
                              "traework_rebalance", remark)


def _fetch_price(code):
    """取最新价。失败返回 None。"""
    try:
        from xtquant import xtdata
        xtdata.subscribe_quote(code, period="tick", count=-1)
        time.sleep(0.3)
        tick = xtdata.get_full_tick([code]).get(code)
        if tick:
            p = float(tick.get("lastPrice", 0))
            return p if p > 0 else None
    except Exception:
        pass
    return None


def _query_order(trader, account, order_id):
    """查指定委托的最新状态。返回 (status, traded_vol, order_vol) 或 None。"""
    try:
        orders = trader.query_stock_orders(account)
        if not orders:
            return None
        for o in orders:
            if int(getattr(o, "order_id", -1)) == order_id:
                return (int(getattr(o, "order_status", 255)),
                        int(getattr(o, "traded_volume", 0)),
                        int(getattr(o, "order_volume", 0)))
    except Exception as e:
        print(f"    !! 查询委托状态异常: {e!r}")
    return None


def _norm_code(v):
    """把股票代码规范成 '600000.SH' 形态，用于成交明细按标的过滤。

    miniQMT 的 stock_code 常见 '600000.SH'，但不同版本/接口可能给 'SH600000'
    或纯 '600000'。统一成「6 位数字 + .SH/.SZ/.BJ」再比较，避免纯格式差异把
    本人成交明细误判成「其它股票」而丢弃（那会让均价恒不可用 → 全量退化到参考价）。
    """
    s = str(v or "").strip().upper()
    if not s:
        return ""
    if "." in s:
        head, tail = s.split(".", 1)
        head = head.strip()
        tail = tail.strip()
        if tail in ("SH", "SZ", "BJ") and head:
            return "%s.%s" % (head, tail)
        return s
    if len(s) > 6 and s[:2] in ("SH", "SZ", "BJ"):
        return "%s.%s" % (s[2:], s[:2])
    return s


def _query_trade_stats(trader, account, code, oids):
    """按 (order_id, stock_code) 双重条件汇总成交明细。返回 (成交量, 成交金额) 或 None。

    成交明细对象字段（MINIQMT_GUIDE §6.3 / scripts/readonly_check_20260930.py 实证）：
      order_id, stock_code, traded_volume, traded_price, traded_time

    **必须同时校验 stock_code（T-20261002-001 A5 修复）**：query_stock_trades 返回的是
    **全账户当日全部成交明细**，不是本次下单的成交。只按 order_id 过滤，一旦委托号被复用、
    或明细里混入同号不同标的的脏数据，就会把他票的成交价计入加权均价 → 账本盈亏直接写错，
    且事后从 CSV 完全无法区分（真实缺陷实测：2800股×15.30 混入 1000股×88.88 → 均价 34.663，
    是正确值 15.30 的 2.3 倍）。

    stock_code 缺失/无法规范化时**一律剔除**（不「缺失即信任」）：宁可返回 None 让调用方走
    「参考价兜底 + 显式告警 + 待补队列」这条已审计的受控降级路径，也不写入可能错误的均价。
    漏记均价可事后回补；错记均价无法事后甄别。

    注意（QMT避坑指南）：query_stock_trades 非交易时段会长时间阻塞，故只在 order_with_guard
    内部（即必然处于下单等待窗口）调用；任何异常都降级为 None，绝不抛出打断守护流程。
    """
    if not oids:
        return None
    target = _norm_code(code)
    try:
        trades = trader.query_stock_trades(account)
    except Exception as e:
        print(f"    !! {code} 查询成交明细异常（真实成交均价不可用）: {e!r}")
        return None
    if not trades:
        return None
    want = set()
    for o in oids:
        try:
            want.add(int(o))
        except (TypeError, ValueError):
            continue
    if not want or not target:
        return None
    total_vol = 0
    total_amt = 0.0
    dropped = 0
    for t in trades:
        try:
            oid = int(getattr(t, "order_id", -1))
        except (TypeError, ValueError):
            continue
        if oid not in want:
            continue
        # 标的二次校验：同号不同票的脏明细必须剔除，否则加权均价被污染
        if _norm_code(getattr(t, "stock_code", "")) != target:
            dropped += 1
            continue
        try:
            tvol = int(getattr(t, "traded_volume", 0) or 0)
            tpx = float(getattr(t, "traded_price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if tvol <= 0 or tpx <= 0:
            continue
        total_vol += tvol
        total_amt += tvol * tpx
    if dropped:
        print(f"    !  {code} 剔除 {dropped} 条同号但非本标的的成交明细"
              f"（防脏明细污染成交均价）")
    if total_vol <= 0:
        return None
    return total_vol, total_amt


def _resolve_avg_price(trader, account, code, oids, traded_vol):
    """真实成交加权均价。不可用/不完整一律返回 None（由调用方走 resolve_fill_price 兜底）。

    「不完整」= 成交明细汇总量 < 已确认成交量。**严禁**把部分明细的均价当成全部成交的真实均价
    写进成交记录——那会把「部分成交的真实价」冒充「全部成交的真实价」，账面价与实际不符且
    无法事后从 CSV 区分。此时降级为 None，让调用方按参考价入账并落待补价队列。
    成交明细常晚于委托状态同步，故短轮询重试几次再放弃（重试总耗时 ≤ 约 3s，不拖交易窗口）。
    """
    if traded_vol <= 0:
        return None

    for attempt in range(_AVGPRICE_RETRIES):
        stats = _query_trade_stats(trader, account, code, oids)
        if stats:
            vol, amt = stats
            if vol >= traded_vol:
                return round(amt / vol, 3)
            print(f"    !  {code} 成交明细 {vol}股 < 已确认成交量 {traded_vol}股"
                  f"（明细未同步，第 {attempt + 1}/{_AVGPRICE_RETRIES} 次）")
        else:
            print(f"    !  {code} 无可用成交明细（第 {attempt + 1}/{_AVGPRICE_RETRIES} 次）")
        if attempt + 1 < _AVGPRICE_RETRIES:
            time.sleep(_AVGPRICE_RETRY_INTERVAL)

    print(f"    !! {code} 真实成交均价不可用（明细缺失或不完整），"
          f"调用方须用参考价兜底并落待补价队列")
    return None


def record_pending_fill(code, side, vol, order_id, ref_price, note="", order_ids=None):
    """登记「已成交但成交价未确认」的待补价记录（JSONL 追加，T-20261002-001）。

    成交是既成事实、成交量必须入账（否则账实脱节）；但成交价未知不能编。故：
      - 成交行照写（price 用调用方参考价，或 0 作待补标记），
      - 同时把代码/方向/数量落盘，后续可用 query_stock_trades 回补真实均价。

    order_ids（T-20261002-001 补强）：多轮重挂时成交量分散在**多个** order_id 上，
    只留最后一个（last_order_id）会让回补按单号查询时漏掉前几轮成交 → 补出的均价偏小。
    故额外落 order_ids 全量列表（去重、升序），order_id 字段保留为最后一笔（兼容旧读取）。

    返回落盘条数；失败返回 0（不抛异常，绝不因记账辅助动作打断交易流程）。
    """
    ids = []
    for o in (order_ids or []):
        try:
            o = int(o)
        except (TypeError, ValueError):
            continue
        if o > 0 and o not in ids:
            ids.append(o)
    ids.sort()
    rec = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "account_id": getattr(C, "ACCOUNT_ID", ""),
        "code": code,
        "side": side,
        "vol": int(vol or 0),
        "order_id": int(order_id or 0),
        "order_ids": ids,
        "ref_price": round(float(ref_price or 0.0), 3),
        "note": note,
    }
    try:
        os.makedirs(os.path.dirname(PENDING_FILL_FILE), exist_ok=True)
        with open(PENDING_FILL_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return 1
    except Exception as e:
        print(f"    !! {code} 待补价记录落盘失败（人工补价时需一并核对）: {e!r}")
        return 0


def resolve_fill_price(result, ref_price=0.0, code=""):
    """从 order_guard 结果取记账成交价：真实成交均价优先，参考价兜底。

    返回 (price, source)，source ∈ {"fill_avg", "ref_price", "unresolved"}：
      - "fill_avg"   : 用真实成交均价（权威记账口径）
      - "ref_price"  : 无真实均价，按调用方参考价记（近似值，需人工核对成交回报）
      - "unresolved" : 两者皆无 → price=0.0，**调用方必须**调 record_pending_fill() 落待补价
                       队列并显著告警；0 价只是待补标记，不得被下游当作真实成交价参与盈亏结算

    存在的意义：让「委托守护已成交但拿不到成交明细」这条路径有唯一、显式、可告警的口径，
    杜绝各调用方各写一套（历史根因：qmt_clear.py 用自己的局部 price 写日志，取价失败即写 0，
    产生 63 条 0 价 SELL 记录）。
    """
    ap = (result or {}).get("avg_price")
    try:
        ap = float(ap) if ap is not None else 0.0
    except (TypeError, ValueError):
        ap = 0.0
    if ap > 0:
        return round(ap, 3), "fill_avg"
    try:
        rp = float(ref_price or 0.0)
    except (TypeError, ValueError):
        rp = 0.0
    if rp > 0:
        if code:
            print(f"    !! {code} 无真实成交均价，按参考价 {rp:.3f} 记账"
                  f"（明细缺失，实际成交价可能偏离）")
        return round(rp, 3), "ref_price"
    if code:
        print(f"    !! {code} 成交价完全无法确认（无真实均价且无参考价）"
              f"→ 记 0 作待补标记，严禁据此结算盈亏")
    return 0.0, "unresolved"


def wait_order_fill(trader, account, order_id, timeout=WAIT_SECONDS):
    """等待委托成交，单次最多 timeout 秒。返回 (final_status, traded_vol, order_vol)。

    终止条件：全部成交 / 废单 / 已撤 / 部撤 / 超时。
    超时前必须回查一次真实状态：丢弃部成量会导致按全量重报（双倍建仓）。
    """
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = _query_order(trader, account, order_id)
        if r:
            last = r
            status, traded, vol = r
            if vol > 0 and traded >= vol:
                return ORDER_SUCCEEDED, traded, vol   # 全部成交
            if status in (ORDER_JUNK, ORDER_CANCELED, ORDER_PART_CANCEL):
                return status, traded, vol
            # 已报/部成/待撤 等 → 继续等
        time.sleep(POLL_INTERVAL)
    # 超时：回查一次，返回最后一次已知真实状态（保留部成量）
    r = _query_order(trader, account, order_id)
    if r:
        status, traded, vol = r
        if vol > 0 and traded >= vol:
            return ORDER_SUCCEEDED, traded, vol
        return status, traded, vol
    if last:
        return last
    return 255, 0, 0  # 超时且始终查不到


def _order_with_guard_core(trader, account, code, side, vol, price=None,
                           remark="order_guard", oid_out=None):
    """委托守护核心：下单→轮询→未全成撤单重试→直到成交或涨跌停跳过。

    oid_out：可选 list，收集本轮全部成功委托的 order_id（供外层回填真实成交均价）。
    核心层只管成交量语义，不查成交明细（保持 12 处 return 分支零改动）。
    """
    attempts = 0
    total_traded = 0
    remaining = vol
    last_order_id = -1

    while attempts < MAX_RETRY:
        attempts += 1
        # 价格（2026-09-14 P1 追价修复）：首轮用传入价（有效时）；
        # 第 2 轮起追价——刷新最新现价重挂（此前 5 次重试全复用同一旧价，601890.SH 案例实证追不上）；
        # 追价取价失败则回退传入价。
        if attempts > 1:
            px = _fetch_price(code)
            if not px or px <= 0:
                px = price if price and price > 0 else None
        else:
            px = price if price and price > 0 else _fetch_price(code)
        if not px or px <= 0:
            return {"ok": False, "action": "ERROR", "traded_vol": total_traded,
                    "order_id": last_order_id, "attempts": attempts,
                    "note": f"{code} 取价失败，无法委托"}
        # 涨跌停跳过检查
        skip, note = check_limit_skip(code, side, px)
        if skip:
            return {"ok": False, "action": "LIMIT_SKIP", "traded_vol": total_traded,
                    "order_id": last_order_id, "attempts": attempts, "note": note}

        oid = _order(trader, account, code, side, remaining, px, remark)
        last_order_id = oid
        if oid is None or oid <= 0:
            print(f"    !! {code} 委托失败(order_stock 返回 {oid})，{note or ''}")
            continue  # 重试
        if oid_out is not None:
            oid_out.append(oid)

        print(f"    委托#{attempts} {code} {side} {remaining}股 @{px:.2f} -> order={oid}（等待成交最多{WAIT_SECONDS}s）")
        status, traded, order_vol = wait_order_fill(trader, account, oid)
        total_traded += traded

        if status == ORDER_SUCCEEDED:
            return {"ok": True, "action": "FILLED", "traded_vol": total_traded,
                    "order_id": oid, "attempts": attempts,
                    "note": f"{code} 委托#{attempts} 全部成交 {total_traded}股 @{px:.2f}"}
        # 部成(55)也必须扣减 remaining，否则按全量重报=双倍建仓
        # 活跃态(50/51/52)超时同样必须走撤单分支（T-20260930-001 修复）：原实现落入底部 continue
        # 直接重挂，而原单仍活跃 → 双重委托风险（卖单重挂因无券被拒耗尽重试；买单重挂=双倍建仓）。
        if status in (ORDER_JUNK, ORDER_CANCELED, ORDER_PART_CANCEL, ORDER_PART_SUCC, 255,
                      ORDER_REPORTED, ORDER_REPORTED_CANCEL, ORDER_PARTSUCC_CANCEL):
            if status == ORDER_JUNK:
                print(f"    !! {code} 委托废单(成交{traded})，重试剩余")
            elif status == 255:
                print(f"    !  {code} 等待超时(成交{traded}/{order_vol})，撤单后重试剩余")
            elif status in (ORDER_REPORTED, ORDER_REPORTED_CANCEL, ORDER_PARTSUCC_CANCEL):
                print(f"    !  {code} 等待超时仍活跃(状态{status}，成交{traded}/{order_vol})，撤单后重试剩余")
            else:
                print(f"    !  {code} 委托已撤/部撤/部成(成交{traded})，重试剩余")
            if traded > 0:
                remaining = max(0, remaining - traded)
            if remaining <= 0:
                return {"ok": True, "action": "FILLED", "traded_vol": total_traded,
                        "order_id": oid, "attempts": attempts,
                        "note": f"{code} 已全部成交(含部成) {total_traded}股"}

            # 超时/仍活跃 → 先撤单；撤单失败绝不直接重挂（防活单并存双倍成交）
            if status in (ORDER_JUNK, ORDER_CANCELED, ORDER_PART_CANCEL):
                time.sleep(1)
                continue
            cancel = trader.cancel_order_stock(account, oid)
            if cancel == 0:
                # 撤单已受理（T-20260930-001 补强）：回查原单终态，补记撤单处理窗口内的成交，
                # 防止「撤单受理→重挂」间隙的成交漏记（remaining 虚大 → 重挂过量 = 双倍卖出）。
                time.sleep(1)
                r3 = _query_order(trader, account, oid)
                if r3:
                    st3, tr3, _v3 = r3
                    if tr3 > traded:
                        _d = tr3 - traded
                        total_traded += _d
                        remaining = max(0, remaining - _d)
                        print(f"    !  {code} 撤单处理窗口内补记成交 {_d} 股（累计 {total_traded}）")
                    if st3 == ORDER_SUCCEEDED or remaining <= 0:
                        return {"ok": True, "action": "FILLED", "traded_vol": total_traded,
                                "order_id": oid, "attempts": attempts,
                                "note": f"{code} 撤单竞争窗口内已成交 {total_traded}股"}
            if cancel != 0:
                # 撤单失败：确认原单死透(53/54/57)或查不到才允许重挂
                print(f"    !! {code} 撤单失败(cancel={cancel})，确认原单状态后再重试")
                confirmed_dead = False
                for _ in range(8):
                    time.sleep(1)
                    r2 = _query_order(trader, account, oid)
                    if r2 is None:
                        confirmed_dead = True
                        break
                    st2, tr2, _v2 = r2
                    if st2 in (ORDER_JUNK, ORDER_CANCELED, ORDER_PART_CANCEL):
                        if tr2 > traded:
                            # 补记撤单窗口内部成量（T-20260930-001 修复：原为 pass，
                            # 09-30 603237.SH 事故形态——撤单竞争窗口内已成交但
                            # traded_vol 漏记 → 返回 traded_vol=0 → 记账门槛漏记账本）
                            _d = tr2 - traded
                            total_traded += _d
                            remaining = max(0, remaining - _d)
                            print(f"    !  {code} 撤单确认窗口内补记成交 {_d} 股（累计 {total_traded}）")
                            if remaining <= 0:
                                return {"ok": True, "action": "FILLED", "traded_vol": total_traded,
                                        "order_id": oid, "attempts": attempts,
                                        "note": f"{code} 撤单窗口内已全部成交 {total_traded}股"}
                        confirmed_dead = True
                        break
                    if st2 == ORDER_SUCCEEDED:
                        # 撤前已全成
                        total_traded += max(0, tr2 - traded)
                        return {"ok": True, "action": "FILLED", "traded_vol": total_traded,
                                "order_id": oid, "attempts": attempts,
                                "note": f"{code} 撤前已全部成交 {total_traded}股"}
                if not confirmed_dead:
                    # 原单仍活跃：本轮不重挂，避免双倍
                    print(f"    !! {code} 原单仍活跃，本轮放弃重挂（已成交 {total_traded}）")
                    # ok 语义=「有已确认成交」（T-20260930-001：原误用本轮 traded，漏掉前几轮累计）
                    return {"ok": total_traded > 0,
                            "action": "CANCELED_TIMEOUT" if total_traded > 0 else "ERROR",
                            "traded_vol": total_traded, "order_id": oid, "attempts": attempts,
                            "note": f"{code} 撤单失败且原单仍活跃，未重挂（防双倍，已成交 {total_traded}）"}
                # 死透确认后允许重挂
            print(f"    !  {code} 已撤单，更新价格重试剩余 {remaining} 股")
            time.sleep(2)
            continue

        # 未知状态兜底（T-20260930-001 加固）：wait_order_fill 的返回状态已全枚举，正常不可达；
        # 若真到达（未来新增状态码），绝不盲重挂（原单可能仍活跃 → 双重委托，且 remaining 未扣减
        # 会超量重挂），直接按实际成交量放弃——记账走 traded_vol 门槛不丢。
        print(f"    !! {code} 委托状态异常({status})，放弃重挂（已成交 {total_traded}，防双倍）")
        return {"ok": total_traded > 0, "action": "CANCELED_TIMEOUT" if total_traded > 0 else "ERROR",
                "traded_vol": total_traded, "order_id": oid, "attempts": attempts,
                "note": f"{code} 委托状态异常({status})，放弃（已成交 {total_traded}股）"}

    # 重试耗尽：ok=False 但 traded_vol 可能>0（窗口内部分/全部成交）——
    # 调用方记账必须只看 traded_vol（T-20260930-001），note 已带实际成交量。
    return {"ok": False, "action": "CANCELED_TIMEOUT", "traded_vol": total_traded,
            "order_id": last_order_id, "attempts": attempts,
            "note": f"{code} 重试 {MAX_RETRY} 次仍未全部成交，放弃（已成交 {total_traded}股）"}


def order_with_guard(trader, account, code, side, vol, price=None, remark="order_guard"):
    """委托守护公开入口 = 核心委托逻辑 + 真实成交均价回填（T-20261002-001）。

    返回 dict 在核心字段（ok/action/traded_vol/order_id/attempts/note）之外**恒含**：
      - avg_price : 真实成交均价（加权，来自 query_stock_trades 成交明细）
                    零成交时为 None（符合语义：无成交谈价格）；
                    有成交但明细不可用时为 None，由调用方 resolve_fill_price() 兜底并告警
      - order_ids : 本次守护期间**全部**成功委托号（多轮重挂 + 撤单窗口补记都可能在多个单号上
                    成交）。供 record_pending_fill() 落盘，回补时按全量单号查成交明细，
                    只留最后一个单号会漏掉前几轮成交导致补出的均价偏小。
    核心逻辑与既有语义（撤单补记 / 防双倍 / 涨跌停跳过 / ok 语义）完全不变。
    """
    oids = []
    res = _order_with_guard_core(trader, account, code, side, vol, price, remark, oids)
    traded = int(res.get("traded_vol", 0) or 0)
    res["avg_price"] = _resolve_avg_price(trader, account, code, oids, traded) if traded > 0 else None
    res["order_ids"] = list(oids)
    return res
