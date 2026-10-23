# coding=gbk
"""
V13 纯风控自闭环版（Python 3.6.8 单文件源码）· QMT 沙箱合规改造 2026-10-15

职责：仅做止损/止盈/追盈的风控卖出（tick 级），不管理换仓（换仓由外部 rebalance 负责）。
本版为零文件读写自闭环：QMT 沙箱（m_nPythonOpFile=1）下策略 open() 已存在文件一律被拒，
positions_cfg / peak_v13.json / heart / fills 文件桥架构自 2026-10-15 起永久废止
（AGENTS.md「QMT 策略模式长期架构红线」+ specs/QMT沙箱自闭环改造方案_20261015.md §4.1）。
状态三层模型（方案 §3.1）：
    L1 会话内存   全局变量：pending 委托、追盈峰值、当日风控计数（重启丢失，由 L2+L3 重建）
    L2 账户即账本 get_trade_detail_data：position（成本锚=m_dOpenPrice，持仓唯一真相）+
                  当日 order（remark 反查重建 risk_sold 去重 + RSK 序号水位）
    L3 期初快照   源码硬编码字面量：V13_CODES（持仓归属白名单）+ PEAK_SNAPSHOT（追盈峰值），
                  人工粘贴部署时更新；白名单外持仓绝不触碰（资金红线）
日志一律 print（C++ 侧落 XtClient_FormulaOutput 日志，不受沙箱限制）；
监控 = 沙箱外 schtasks 旁路 grep 日志关键行（策略进程零外部交互，方案 §3.4）。
追盈语义为 2026-09-07 修复版（T-20260907-002）：激活阈值+8% + 保本底线 + T+1 不评估不并峰。
风控逻辑与文件桥版零改动：三规则阈值、pending 管理（300s/3次重试）、状态码口径、
passorder 调用全部保留；仅状态数据源从「文件」替换为「账户即账本 + 源码快照」。
"""
import time
import traceback

# ============================================================
# 常量
# ============================================================
BUILD_TAG = "20261023-110053"

ACCOUNT_ID = "67014907"

# ---- L3 期初快照（源码硬编码字面量，人工粘贴部署时更新；op 部署前必填） ----
# 持仓归属白名单：V13 实际持仓代码，支持 '600522.SH' / 'SH.600522' / '600522' 格式。
# 账号 67014907 上 V13 与 V2 共存，白名单用于圈定 V13 纳管范围：
#   白名单 ∩ 账户 position = 纳管集；白名单外的持仓绝不触碰（资金分配红线）。
# 白名单为空 = 部署未填 = 完全被动模式（不评估不下单）；
# ★ 2026-10-23：账号 67014907 已全清（V2 清仓，账户 N=0），V13 白名单置空，
#   首个交易日由外部 rebalance_daily.py 买入（remark=planA_new_top）后，
#   init 扫当日 order / 被动模式下每 30s 延迟绑定自动纳管（见 _late_bind_whitelist）。
#   注意：QMT 当日 order 隔日即丢：若 V1.3 已有持仓且策略隔日重启，必须人工把持仓代码填回本常量。
V13_CODES = ()

# 追盈历史峰值快照（跨日延续，T-20260910 只升不降语义）：
#   {代码: 峰值}，代码格式同 V13_CODES；快照缺失的票运行中以当日 high 重锚（偏保守，方案 §5.6）。
# 2026-10-15 peak_v13.json 实测 peaks={}（追盈未激活）→ 留空，运行中走成本底+当日重锚即可。
PEAK_SNAPSHOT = {}

# passorder 常量（对齐 ATR 战备模板：prType=5最新价，第8参=remark对账标识，第9参=2，第11参=C）
OP_BUY = 23
OP_SELL = 24
ORDER_TYPE_VOL = 1101
PRTYPE_LATEST = 5
ORDER_REMARK = "P16V13风控"
ORDER_REMARK_ALL = (ORDER_REMARK,)

# ★ 2026-10-23 外部下单 remark（T-20261023-002，与 G2/ENS 同款）
# 拍板 F 后换仓买入由外部 rebalance_daily.py（xtquant 直连 67014907）完成，
# remark 与内置风控 remark 完全不同 → 若不自查，内置风控会：
#   ① 白名单（V13_CODES）永远漏掉外部新买入的票 → 新仓零止损覆盖（T-20261020-005 同型 P0）；
#   ② risk_sold 重建漏掉外部卖出 → 重启后可能重复卖出。
# 解法（对齐官方《QMT内置Python外部读写权限调整说明》§06「订单备注建立幂等控制」）：
#   init 时扫「当日 order」，凡 remark 命中 EXTERNAL_REMARK_BUY 的票自动并入白名单。
EXTERNAL_REMARK_BUY = "planA_new_top"
EXTERNAL_REMARK_SELL = ("planA_st_exit", "planA_mature", "planA_pk_out", "planA_trim")
EXTERNAL_REMARK_ALL = (EXTERNAL_REMARK_BUY,) + tuple(EXTERNAL_REMARK_SELL)
# 全量 remark（内置自身 + 外部）→ 用于 risk_sold 重建
ALL_REMARKS = ORDER_REMARK_ALL + EXTERNAL_REMARK_ALL

# pending 超时与重试（买卖统一：300秒/3次，防「30秒即放弃永不补单」）
PENDING_TIMEOUT = 300
MAX_RETRY = 3

# 订单状态码（对齐 QMT撤单重委托方案调研.md 标准表）：
# 活跃={48未报,49待报,50已报,51已报待撤,52部成待撤,55部成}；终态={53部撤,54已撤,56全成,57废单}
CANCEL_STATUS = (53, 54)
CONFIRM_DEAD_STATUS = (53, 54, 57)   # 撤单死透=部撤/已撤/废单（55部成是活跃态不能算死）
ACTIVE_SKIP_STATUS = (53, 54, 57)    # active_only 时跳过终态（55部成是活跃态不能排除；调研 4.2 原写 54,55,57 有 bug）

# 内置实时风控参数（自 qmt_monitor.py 迁移，纯行情驱动；追盈为 2026-09-07 修复版语义）
STOP_LOSS_PCT = 0.07
TAKE_PROFIT_PCT = 0.15
TRAILING_PCT = 0.08
TRAIL_ACTIVATE_PCT = 0.08   # 追盈激活阈值（T-20260907-002）：峰值≥成本×(1+阈值)才追踪，防"追盈=追跌"

# 止损跳空与实际滑点保护（T-20261002）
# 背景：首次止损已是 PRTYPE_LATEST 最新价市价委托（不是成本价×0.93 固定限价，改成限价反而可能卖不掉），
# 真正的缺口是：①成交价来源不可靠（订单对象 m_dPrice 语义未实测，可能只是委托价）；
# ②没有独立滑点阈值，跳空卖出多亏无法量化告警；③超时/废单重试沿用陈旧触发价。
# 因此这里加"不利滑点"阈值与"跳空容忍"阈值：超限只告警留痕，绝不阻止卖出（卖出风控优先级最高）。
MAX_ADVERSE_SLIPPAGE_PCT = 0.03   # 不利滑点告警阈值：卖出成交价低于参考价 3% / 买入成交价高于参考价 3%
MAX_GAP_TOLERANCE_PCT = 0.02      # 跳空容忍：触发时现价已低于止损线 2% 以上 = 跳空破位，单独告警留痕
DEAL_QUERY_RETRIES = 2            # deal 明细查询重试次数（成交明细有写入延迟）
DEAL_QUERY_INTERVAL = 0.2         # deal 明细查询重试间隔（秒）
DEAL_QUERY_MAX_ERR = 3            # deal 接口连续异常达此次数才判定永久不可用（单次异常可能只是瞬时抖动，不该永久降级）

# 反查参数
LOOKUP_RETRIES = 6
LOOKUP_INTERVAL = 0.25

# ============================================================
# 全局状态（L1 会话内存；quickTrade=2 官方要求委托状态存全局变量，不存 ContextInfo）
# ============================================================
_g_pending = {}
_g_today = ""
_g_today_order_count = 0
_g_today_fill_count = 0
_g_today_abandon_count = 0
_g_today_risk_count = 0
_g_positions = {}
_g_risk_sold = set()
_g_passive = False          # 被动模式：True 时 handlebar 顶部直接 return（绝不「活跃+状态缺失」盲跑）
_g_whitelist = set()        # V13_CODES 归一化缓存（QMT 代码格式，init 重建）
_g_peak_snap = {}           # PEAK_SNAPSHOT 归一化缓存（QMT 代码格式，init 重建）
_g_last_heart_ts = 0.0      # print 心跳节流（60秒，tick 级 handlebar 全量打印会刷爆日志）
_g_last_passive_ts = 0.0    # 被动模式心跳节流（300秒）
_g_last_bind_try = 0.0      # 被动模式下延迟绑定白名单的上次尝试时刻（T-20261023-002，30s 节流）
_g_diag_printed = False
_g_deal_ok = None   # deal 明细接口可用性探测缓存：None=未探测 True=可用 False=不可用（降级只用订单对象价）
_g_deal_err_count = 0   # deal 接口连续异常计数（达 DEAL_QUERY_MAX_ERR 才永久降级，单次异常可能只是瞬时抖动）
_g_deal_no_oid = False   # deal 明细无委托号字段（只告警一次；无法归属本次委托 → 一律降级，不用猜测归属的均价记账）
_g_slippage_alert_count = 0   # 当日不利滑点/跳空超限次数（日终日志留痕）


# ============================================================
# 工具函数
# ============================================================
def _today_str():
    return time.strftime("%Y%m%d")


def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _to_qmt_code(code):
    """QMT passorder 下单代码：600522.SH（数字在前）原样返回。
    勿翻转为 SH.600522 —— QMT 解析 SH.600522 为 600522SH 判「代码不合法」废单（T-20260831-002，
    QMT 主日志实锤: orderCode:600522SH 不合法!；ATR/Project_10 均以 600522.SH 实盘成交）。"""
    parts = code.split(".")
    if len(parts) != 2:
        return None
    num, ex = parts[0], parts[1]
    if ex == "SH" and (num.startswith("6") or num.startswith("688")):
        return "%s.SH" % num
    if ex == "SZ" and (num.startswith("0") or num.startswith("3")):
        return "%s.SZ" % num
    return None


def _to_qmt_code_any(code):
    """L3 快照代码归一化：'600522.SH' / 'SH.600522' / '600522' 均转 QMT 代码 '600522.SH'。
    裸数字按 6 开头=SH、0/3 开头=SZ 补后缀（与 _check_risk_signals 持仓代码补后缀口径一致）；
    无法归一化返回 None（调用方打印 WARN，不猜）。"""
    c = str(code or "").strip()
    if not c:
        return None
    num = ""
    ex = ""
    parts = c.split(".")
    if len(parts) == 2:
        a, b = parts[0], parts[1]
        if b in ("SH", "SZ"):
            num, ex = a, b
        elif a in ("SH", "SZ"):
            num, ex = b, a
    elif len(parts) == 1:
        num, ex = c, ""
    if num and not ex:
        if num.startswith("6"):
            ex = "SH"
        elif num.startswith("0") or num.startswith("3"):
            ex = "SZ"
    if not num or not ex:
        return None
    if ex == "SH" and (num.startswith("6") or num.startswith("688")):
        return "%s.SH" % num
    if ex == "SZ" and (num.startswith("0") or num.startswith("3")):
        return "%s.SZ" % num
    return None


def _to_bridge_code(code):
    """QMT代码(SH.600522) 转回桥协议代码(600522.SH)，供日志/记录对账展示。"""
    if "." in code:
        ex, num = code.split(".", 1)
        if ex == "SH" and num.startswith("6"):
            return "%s.SH" % num
        if ex == "SZ" and (num.startswith("0") or num.startswith("3")):
            return "%s.SZ" % num
    return code


def _norm_code(code):
    """代码归一化：统一返回裸数字段用于跨格式比较。
    'SH.600522' / '600522.SH' / '600522' 均 → '600522'。QMT m_strInstrumentID 返回裸码，
    而指令为 SH.600522 / 桥协议 600522.SH，直接 o_code != code 比较必失配（T-20260831-001）。"""
    c = str(code or "").strip()
    if "." in c:
        return c.split(".")[0]
    return c


# ============================================================
# 下单
# ============================================================
def _do_passorder(C, action, code, vol, price, prtype=PRTYPE_LATEST):
    """调用passorder全局函数。11参形式（对齐ATR战备模板）：
    (op, 1101, account, code, prType, 第6参=价格, 第7参=股数, remark, quickTrade=2, '', C)。
    prtype: 5=最新价(默认,忽略price按市价) / 11=指定价(用price做限价,可造不可成交单测撤单)。"""
    if action == "BUY":
        op = OP_BUY
    else:
        op = OP_SELL
    try:
        ret = passorder(op, ORDER_TYPE_VOL, ACCOUNT_ID, code, prtype, price, vol, ORDER_REMARK, 2, "", C)
        return ret
    except Exception as e:
        print("[P16V13][PASSORDER-ERR] %s %s: %s" % (action, code, e))
        return None


# ============================================================
# 反查订单
# ============================================================
def _extract_order_id(matched):
    """从QMT订单对象提取撤单用委托标识。字段名随QMT版本变化，多字段兜底。
    参考 qmt_wrapper 用 m_nOrderID（真实 QMT 平台委托号，撤单已验证可用）→ 优先取；
    D:\\QMT交易端模拟 模拟端无 m_nOrderID（DIAG 实锤），只有 m_strOrderRef（passorder ref）
    与 m_strOrderSysID（sys号）——但实测**模拟端 passorder(24) 撤单两个都不认**（被当价格解析、
    报「下单数量为0」），模拟端撤单只能走 QMT 界面手动；取到仅用于 pending 记录/诊断。"""
    if matched is None:
        return ""
    for attr in ("m_nOrderID", "m_strOrderID", "m_strOrderRef", "m_strOrderSysID", "m_strSysid", "m_nOrderRef", "m_strUserOrderId"):
        try:
            v = getattr(matched, attr, "")
            if v:
                return str(v)
        except Exception:
            continue
    return ""


def _extract_deal_volume(matched, fallback_vol=0):
    """从QMT订单对象提取已成交量。多字段兜底；全部取不到但 status=56(已成) 时按目标量视为全成。"""
    if matched is None:
        return 0
    for attr in ("m_nDealVolume", "m_nTradedVolume", "m_nVolumeTraded", "m_nCumDealVolume", "m_nDealVol", "m_nTradeVolume", "m_nVolTraded"):
        try:
            v = getattr(matched, attr, 0) or 0
            if int(v) > 0:
                return int(v)
        except Exception:
            continue
    try:
        st = int(getattr(matched, "m_nOrderStatus", 0) or 0)
        if st == 56:
            return int(fallback_vol)
    except Exception:
        pass
    return 0


def _deal_oid(d):
    """从成交明细对象提取委托号；返回 (值, 是否该对象具备委托号字段)。"""
    for attr in ("m_nOrderID", "m_strOrderSysID", "m_strOrderID", "m_strOrderRef", "m_nOrderRef", "m_strSysid"):
        try:
            v = getattr(d, attr, "")
        except Exception:
            continue
        if v not in (None, "", 0):
            return str(v), True
    return "", False


def _query_deals(C, code, oids):
    """查询本次守护委托的真实成交明细，返回 [(委托号, 量, 价)]（查不到返回空列表）。
    大 QMT get_trade_detail_data(acct,'stock','deal') 返回成交对象；字段名随版本变化，多字段兜底。
    过滤硬条件：标的代码一致 AND 委托号 ∈ 本次 oids（remark 只作候选优先信号，绝不硬过滤）。
    可用性探测语义（三态，2026-10-02 修正）：
      · 接口不存在            → _g_deal_ok=False（永久降级）
      · 调用抛异常连续 DEAL_QUERY_MAX_ERR 次 → _g_deal_ok=False（单次异常视为抖动，不永久降级）
      · 返回空列表            → 仅「尚未落库」，保留重试资格
      · 当日 deal 全无委托号   → 无法把成交归属到本次委托（可能混入同标的其它单的历史成交），
        必须返回 [] 拒绝使用，而不是放行同标的全部记录 —— 宁可降级到 order_obj，也不用错误均价记账。
    """
    global _g_deal_ok, _g_deal_err_count, _g_deal_no_oid
    if _g_deal_ok is False:
        return []
    f_get = globals().get("get_trade_detail_data")
    if f_get is None:
        f_get = getattr(C, "get_trade_detail_data", None)
    if f_get is None:
        _g_deal_ok = False
        print("[P16V13][DEAL-API-UNAVAIL] 无 get_trade_detail_data，成交价降级为订单对象价")
        return []
    try:
        deals = f_get(ACCOUNT_ID, "stock", "deal")
    except Exception as e:
        _g_deal_err_count += 1
        if _g_deal_err_count >= DEAL_QUERY_MAX_ERR:
            _g_deal_ok = False
        print("[P16V13][DEAL-API-ERR] get_trade_detail_data 抛异常(%d/%d): %s%s" % (
            _g_deal_err_count, DEAL_QUERY_MAX_ERR, e,
            "，成交价降级为订单对象价" if _g_deal_ok is False else "，本次按无明细处理"))
        return []
    _g_deal_err_count = 0
    if not deals:
        return []
    _g_deal_ok = True
    target = _norm_code(code)
    rows = []
    any_oid = False
    for d in deals:
        try:
            d_code = _norm_code(getattr(d, "m_strInstrumentID", "") or getattr(d, "m_strStockCode", ""))
            # 无标的字段 = 无法确认是本标的成交 → 拒绝（不能因为"代码缺失"就放行，那会混入其它股票）
            if not d_code or d_code != target:
                continue
            d_oid, has_oid = _deal_oid(d)
            if has_oid:
                any_oid = True
            d_vol = 0
            for attr in ("m_nVolume", "m_nDealVolume", "m_nTradedVolume", "m_nVolumeTraded"):
                v = getattr(d, attr, 0) or 0
                if int(v) > 0:
                    d_vol = int(v)
                    break
            d_px = 0.0
            for attr in ("m_dAvgPrice", "m_dPrice"):
                v = float(getattr(d, attr, 0) or 0)
                if v > 0:
                    d_px = v
                    break
            if d_vol <= 0 or d_px <= 0:
                continue
            rows.append((d_oid, has_oid, d_vol, d_px))
        except Exception:
            continue
    if not rows:
        return []
    # 两遍决策：先扫全量确认「本次明细里是否存在带委托号的记录」，再决定是否启用委托号过滤，
    # 避免遍历顺序决定结果（否则前面几条无委托号的脏成交会先被放行）。
    if not any_oid:
        # 全无委托号 → 无法归属本次委托，拒绝使用（fail-safe：宁可降级，也不用可能混入历史成交的均价记账）
        if not _g_deal_no_oid:
            _g_deal_no_oid = True
            print("[P16V13][DEAL-NO-OID] 成交明细无任何委托号字段，无法归属本次委托，"
                  "成交均价降级为订单对象价（不猜测归属）")
        return []
    if not oids:
        return []
    return [(r[0], r[2], r[3]) for r in rows if r[1] and r[0] in oids]


def _resolve_fill_avg(C, code, oids, expect_vol):
    """真实成交加权均价：Σ(量×价)/Σ量。命中成交量必须覆盖 expect_vol，否则返回 0（降级）。
    oids 必须累计本次守护期间**全部**成功委托号（多轮重挂/撤单重报），否则部分成交会漏算。
    deal 明细有写入延迟，最多重试 DEAL_QUERY_RETRIES 次。"""
    if expect_vol <= 0 or not oids:
        return 0.0
    for attempt in range(DEAL_QUERY_RETRIES):
        deals = _query_deals(C, code, oids)
        tot_v = 0
        tot_amt = 0.0
        for _oid, dv, dp in deals:
            tot_v += dv
            tot_amt += dv * dp
        if tot_v >= expect_vol and tot_amt > 0:
            return tot_amt / tot_v
        if attempt + 1 < DEAL_QUERY_RETRIES:
            time.sleep(DEAL_QUERY_INTERVAL)
    return 0.0


def _order_obj_price(matched):
    """从订单对象取价格（多字段兜底）。注意：m_dPrice 语义未实测（可能是委托价而非成交均价），
    因此这里的取值只作为 deal 明细不可用时的次级降级，且会在留痕里标注 px_source。"""
    if matched is None:
        return 0.0
    for attr in ("m_dAvgPrice", "m_dPrice"):
        try:
            v = float(getattr(matched, attr, 0) or 0)
            if v > 0:
                return v
        except Exception:
            continue
    return 0.0


def _adverse_slippage(action, ref_price, fill_price):
    """不利滑点：卖出看"卖低了"、买入看"买高了"，统一返回正数百分比。
    参考价缺失或非正时返回 0（无基准不臆造滑点）。"""
    if ref_price <= 0 or fill_price <= 0:
        return 0.0
    if action == "SELL":
        return (ref_price - fill_price) / ref_price
    return (fill_price - ref_price) / ref_price


def _resolve_px(C, code, action, oids, expect_vol, matched, ref_price):
    """统一成交价协议：fill_avg(真实成交均价) → order_obj(订单对象价) → ref_price(触发/最新价)。
    返回 (price, px_source, adverse_slip)。price 恒 > 0 时才可被留痕消费方记账。"""
    avg = _resolve_fill_avg(C, code, oids, expect_vol)
    if avg > 0:
        return avg, "fill_avg", _adverse_slippage(action, ref_price, avg)
    opx = _order_obj_price(matched)
    if opx > 0:
        return opx, "order_obj", _adverse_slippage(action, ref_price, opx)
    if ref_price > 0:
        return ref_price, "ref_price", 0.0
    return 0.0, "unresolved", 0.0


def _gap_pct(cost_price, trigger_price):
    """跳空幅度：触发时现价已跌破成本止损线的幅度（正数 = 跳空破位）。
    跳空说明「按 -7% 应该成交但实际开盘就在线下」，此时市价止损的实际亏损会超过 7%，
    需单独留痕以便事后区分「止损失效」与「跳空超额亏损」。"""
    if cost_price <= 0 or trigger_price <= 0:
        return 0.0
    line = cost_price * (1.0 - STOP_LOSS_PCT)
    if trigger_price >= line:
        return 0.0
    return (line - trigger_price) / cost_price


def _slippage_alert(sid, code, action, px_source, ref_price, fill_price, adverse, gap=0.0):
    """不利滑点 / 跳空破位告警：**只告警留痕，绝不拦单**（卖出风控优先级高于一切优化）。
    返回可追加到留痕尾部的告警文本（空串=正常）。
    注意 %-格式化与 * 的优先级：必须写成 "%s" % (adverse * 100)，写成 "%s" % adverse * 100
    会先格式化再重复 100 次（2026-10-02 修复）。"""
    global _g_slippage_alert_count
    notes = []
    if adverse > MAX_ADVERSE_SLIPPAGE_PCT:
        notes.append("不利滑点%.2f%%>阈值%.0f%%" % (
            adverse * 100, MAX_ADVERSE_SLIPPAGE_PCT * 100))
    if gap > MAX_GAP_TOLERANCE_PCT:
        notes.append("跳空破位%.2f%%>容忍%.0f%%" % (
            gap * 100, MAX_GAP_TOLERANCE_PCT * 100))
    if not notes:
        return ""
    _g_slippage_alert_count += 1
    print("[P16V13][SLIPPAGE-ALERT] %s %s %s px_source=%s ref=%.3f fill=%.3f adverse=%.2f%% gap=%.2f%% | %s" % (
        sid, code, action, px_source, ref_price, fill_price, adverse * 100, gap * 100, "; ".join(notes)))
    tail = " adverse_slippage=%.2f%%" % (adverse * 100) if adverse > MAX_ADVERSE_SLIPPAGE_PCT else ""
    if gap > MAX_GAP_TOLERANCE_PCT:
        tail += " gap_exceeded=%.2f%%" % (gap * 100)
    return tail


def _refresh_last_price(C, code, fallback=0.0):
    """重试前刷新最新价：避免用陈旧触发价重挂。取不到行情才退回 fallback（fail-safe 不阻断重试）。"""
    try:
        tick = C.get_full_tick([code])
        if tick:
            t = tick.get(code)
            if t:
                p = float(t.get("lastPrice", 0) or 0)
                if p > 0:
                    return p
    except Exception:
        pass
    return fallback


def _acc_order_id(info, oid):
    """把委托号累计进 pending.order_ids（多轮重挂/撤单重报都要留痕）。
    真实成交均价查询必须拿全部委托号，否则中途重挂前的部分成交会被漏算，
    导致「明细量<已确认量」误判降级、或者用残缺明细冒充真实均价。"""
    if not oid:
        return
    oid = str(oid)
    ids = info.setdefault("order_ids", [])
    if oid not in ids:
        ids.append(oid)


def _write_fill(C, date, sid, info, code, action, vol, status, sysid, reason,
                matched=None, extra=None):
    """统一成交收尾留痕（成交价协议 + 跳空/滑点留痕的唯一出口，print 通道）。
    价源优先级：deal明细真实均价(fill_avg) → 订单对象价(order_obj) → 触发/参考价(ref_price)。
    参考价=触发时现价（_resolve_px 传入 info['trigger_price']），滑点基准必须用它而非委托价，
    否则市价单的「委托价=触发价」会把真实跳空亏损算成 0 滑点（掩盖问题）。
    返回记录 dict（调用方可继续打日志）。"""
    ref_price = float(info.get("trigger_price", 0) or info.get("price", 0) or 0)
    oids = list(info.get("order_ids", []))
    oid_now = info.get("order_id", "")
    if oid_now and str(oid_now) not in oids:
        oids.append(str(oid_now))
    price, px_source, adverse = _resolve_px(
        C, code, action, oids, vol, matched, ref_price)
    gap = _gap_pct(float(info.get("cost_price", 0) or 0), float(info.get("trigger_price", 0) or 0))
    alert = _slippage_alert(sid, code, action, px_source, ref_price, price, adverse, gap)
    rec = {
        "strategy_order_id": sid,
        "code": info.get("bridge_code", code),
        "action": action,
        "vol": vol,
        "price": price,
        "status": status,
        "sysid": sysid,
        "reason": (reason or "") + alert,
        "ts": _now_str(),
        "px_source": px_source,
        "trigger_price": ref_price,
        "adverse_slippage": round(adverse, 6),
        "gap_pct": round(gap, 6),
        "order_ids": oids,
        "retry": info.get("retry", 0),
    }
    if extra:
        rec.update(extra)
    # 自闭环版：fills 文件回写已废止（沙箱禁 open），统一 print 留痕（C++ 侧落 FormulaOutput 日志）
    print("[P16V13][FILL] %s %s %s %s vol=%d px=%.4f src=%s status=%s ref=%.3f slip=%.2f%% gap=%.2f%% retry=%d oids=%s | %s" % (
        date, sid, info.get("bridge_code", code), action, vol, price, px_source, status,
        ref_price, adverse * 100, gap * 100, rec.get("retry", 0), ",".join(oids), reason))
    return rec


def _lookup_order(C, code, vol, action, sid="", active_only=True):
    """短轮询反查订单（对齐ATR战备模板）。
    全局函数优先、小写'stock'、m_strOrderID优先、量容差相对化(0.10)。
    active_only=True: 跳过终态(54,55,57)，用于下单反查/重报反查。
    active_only=False: 不过滤终态，用于跟踪/撤单确认/超时确认。
    候选排序: sid(userOrderId)精确匹配 > remark含ORDER_REMARK > 时间最新（软优先不硬过滤）。
    """
    f_get = globals().get("get_trade_detail_data")
    if f_get is None:
        f_get = getattr(C, "get_trade_detail_data", None)
    if f_get is None:
        return None, None
    dir_cn = "买入" if action == "BUY" else "卖出"
    for _ in range(LOOKUP_RETRIES):
        time.sleep(LOOKUP_INTERVAL)
        try:
            orders = f_get(ACCOUNT_ID, "stock", "order")
            if not orders:
                continue
            candidates = []
            for o in orders:
                try:
                    o_code = str(getattr(o, "m_strInstrumentID", "") or "")
                    o_vol = int(getattr(o, "m_nOrderVolume", 0) or 0)
                    o_opt = str(getattr(o, "m_strOptName", "") or "")
                    o_status = int(getattr(o, "m_nOrderStatus", 0) or 0)
                except Exception:
                    continue
                if _norm_code(o_code) != _norm_code(code):
                    continue
                if dir_cn not in o_opt:
                    continue
                if active_only and o_status in ACTIVE_SKIP_STATUS:
                    continue
                if vol > 0 and o_vol > 0 and abs(o_vol - vol) > float(max(o_vol, vol)) * 0.10:
                    continue
                candidates.append(o)
            if candidates:
                def _key(o):
                    uid = str(getattr(o, "m_strUserOrderId", "") or "")
                    remark = str(getattr(o, "m_strRemark", "") or "")
                    return (
                        1 if sid and uid == sid else 0,
                        1 if ORDER_REMARK in remark else 0,
                        str(getattr(o, "m_strInsertTime", "") or ""),
                    )
                candidates.sort(key=_key, reverse=True)
                matched = candidates[0]
                order_id = _extract_order_id(matched)
                # 一次性诊断：打印匹配订单的 ID/状态类字段，确认真实委托号字段名
                global _g_diag_printed
                if not _g_diag_printed:
                    _g_diag_printed = True
                    try:
                        attrs = [a for a in dir(matched)
                                 if ("order" in a.lower()) or ("sys" in a.lower()) or ("id" in a.lower())
                                 or ("deal" in a.lower()) or ("vol" in a.lower()) or ("trade" in a.lower())
                                 or ("price" in a.lower()) or ("amount" in a.lower())]
                        diag = {a: str(getattr(matched, a, ""))[:40] for a in attrs}
                        print("[P16V13][DIAG-ORDER] 匹配订单字段=%s" % diag)
                    except Exception:
                        pass
                return order_id, matched
        except Exception:
            continue
    return None, None


def _cancel_order_by_id(C, code, order_id, sysid=""):
    """官方撤单 cancel()（对齐 QMT撤单重委托方案调研.md：passorder opType 无撤单值（24=卖出），
    cancel(orderId, accountId, accountType, C) 是官方撤单唯一入口；先 can_cancel_order 预检）。
    顺序：can_cancel_order 预检 → cancel() 发指令（返回成功仅=指令送达柜台）→ 确认态轮询终态。
    order_id 优先用 sysid（m_strOrderSysID 柜台合同号，调研实战文多用），否则用 order_id。"""
    oid = sysid or order_id
    if not oid:
        return None
    try:
        if not can_cancel_order(oid, ACCOUNT_ID, "stock"):
            print("[P16V13][CANCEL-NOT-CANCELABLE] %s %s 当前不可撤" % (code, oid))
            return None
    except Exception:
        pass
    try:
        r = cancel(oid, ACCOUNT_ID, "stock", C)
        print("[P16V13][CANCEL] code=%s order_id=%s 返回:%s" % (code, oid, r))
        return r
    except Exception as e:
        print("[P16V13][CANCEL-ERR] code=%s order_id=%s: %s" % (code, oid, e))
        return None


# ============================================================
# pending 状态机
# ============================================================
def _handle_rejected_retry(C, date, sid, info, now, deal_vol):
    """status=57 真废单：先确认原单死透才重报剩余量；原单仍活跃则延后复查绝不重报。
    2026-09-02 P0 修复：55=部成(活跃态，原单仍在成交)曾被误当废单直接重报 → 原单+重报单双成交
    = 双倍建仓（实锤 600262 3000→6400）。对齐 timeout_retry 纪律：死透(53/54/57或查不到)才重报。
    注意调用方已限定 status==57；此处二次确认是双保险，防模拟端状态码语义漂移。"""
    global _g_today_fill_count, _g_today_abandon_count
    code = info["code"]
    action = info["action"]
    vol = info.get("vol", 0)
    cur_vol = info.get("cur_vol", vol)
    retry = info.get("retry", 0)

    # 死透确认（短轮询4×0.5s）：原单仍活跃/已成交满，绝不重报
    dead = False
    saw_order = False
    filled_now = 0
    for _ in range(4):
        time.sleep(0.5)
        _oid2, m2 = _lookup_order(C, code, cur_vol, action, sid=sid, active_only=False)
        if m2 is None:
            continue
        saw_order = True
        st2 = int(getattr(m2, "m_nOrderStatus", 0) or 0)
        dv2 = _extract_deal_volume(m2, fallback_vol=cur_vol)
        if dv2 >= cur_vol:
            filled_now = dv2
            break
        if st2 in CONFIRM_DEAD_STATUS:
            dead = True
            break
    if not saw_order:
        dead = True
    # 原单实际已全成交 → FILLED 收尾，绝不重报
    if filled_now > 0:
        total_deal = info.get("filled_so_far", 0) + filled_now
        print("[P16V13][FILLED] %s %s 原单已全成交 deal=%d" % (sid, code, total_deal))
        _write_fill(C, date, sid, info, code, action, total_deal, "FILLED",
                    info.get("sysid", ""),
                    info.get("risk_reason", "filled, original still active"),
                    matched=m2)
        _g_pending.pop(sid, None)
        _g_today_fill_count += 1
        return
    if not dead:
        # 原单未确认死透（仍活跃）→ 延后60s复查，不重报不计数（防双倍持仓）
        print("[P16V13][REJECTED-UNCONFIRMED] %s %s 原单未死透，延后60s复查" % (sid, code))
        info["time"] = now - PENDING_TIMEOUT + 60
        return

    filled_so_far = info.get("filled_so_far", 0) + deal_vol
    info["filled_so_far"] = filled_so_far
    if retry >= MAX_RETRY:
        print("[P16V13][ABANDON] %s %s 废单retry=%d filled=%d" % (sid, code, retry, filled_so_far))
        _write_fill(C, date, sid, info, code, action, filled_so_far, "ABANDONED",
                    info.get("sysid", ""),
                    info.get("risk_reason", "rejected status=57, retry exhausted"))
        _g_pending.pop(sid, None)
        _g_today_abandon_count += 1
        return
    remaining = vol - filled_so_far
    if remaining <= 0:
        _g_pending.pop(sid, None)
        return
    # 重试前刷新最新价：市价单用陈旧触发价虽不影响成交价，但会让滑点基准失真、且限价兜底场景会挂错价
    retry_px = _refresh_last_price(C, code, float(info.get("price", 0) or 0))
    info["last_price"] = retry_px
    ret = _do_passorder(C, action, code, remaining, retry_px)
    print("[P16V13][REJECTED-RETRY-%d] %s %s %d股 px=%.3f ret=%s" % (
        retry + 1, sid, code, remaining, retry_px, ret))
    oid3, m3 = _lookup_order(C, code, remaining, action, sid=sid)
    sysid3 = ""
    if m3:
        sysid3 = str(getattr(m3, "m_strSysid", "") or "")
    _acc_order_id(info, oid3 or _extract_order_id(m3))
    info["time"] = now
    info["retry"] = retry + 1
    info["cur_vol"] = remaining
    info["order_id"] = oid3 or ""
    info["sysid"] = sysid3


def _handle_timeout_retry(C, date, sid, info, now):
    """超时处理：撤旧单 → 确认死透才重报剩余量；撤单未确认则延后60s复查（绝不重复报）。
    关键：撤前若已全部成交(dv2>=cur_vol) → 按FILLED收尾绝不重报；死透才重报 remaining=vol-filled_so_far。"""
    global _g_today_fill_count, _g_today_abandon_count
    code = info["code"]
    action = info["action"]
    vol = info.get("vol", 0)
    cur_vol = info.get("cur_vol", vol)
    filled_so_far = info.get("filled_so_far", 0)
    retry = info.get("retry", 0)
    oid = info.get("order_id", "")
    if not oid:
        # order_id 未知：先反查补齐真实委托号再撤（防空ID撤不掉→旧单未死透→误重报双倍持仓）
        _oid0, _m0 = _lookup_order(C, code, cur_vol, action, sid=sid, active_only=False)
        if _m0 is not None:
            oid = _extract_order_id(_m0) or _oid0 or ""
            # 首反查才拿到委托号时必须立即累计，否则后续 _write_fill 的 deal 查询会遗漏该笔明细
            _acc_order_id(info, oid)
            if not info.get("order_id"):
                info["order_id"] = oid
    print("[P16V13][TIMEOUT] %s %s elapsed>%ds retry=%d cur_vol=%d filled=%d" % (
        sid, code, PENDING_TIMEOUT, retry, cur_vol, filled_so_far))
    if oid:
        _cancel_order_by_id(C, code, oid, info.get("sysid", ""))
    # 短轮询确认撤单死透（4次×0.5s）：dv2>=cur_vol → 撤前已全成交；状态码进入终态 → 死透
    dead = False
    filled_before_cancel = 0
    saw_order = False
    for _ in range(4):
        time.sleep(0.5)
        oid2, m2 = _lookup_order(C, code, cur_vol, action, sid=sid, active_only=False)
        if m2 is None:
            continue
        saw_order = True
        st2 = int(getattr(m2, "m_nOrderStatus", 0) or 0)
        dv2 = _extract_deal_volume(m2, fallback_vol=cur_vol)
        if dv2 >= cur_vol:
            filled_before_cancel = dv2
            break
        if st2 in CONFIRM_DEAD_STATUS:
            dead = True
            break
    if not saw_order:
        dead = True
    if filled_before_cancel > 0:
        total_deal = filled_so_far + filled_before_cancel
        print("[P16V13][FILLED] %s %s 撤前已全成交 deal=%d" % (sid, code, total_deal))
        _write_fill(C, date, sid, info, code, action, total_deal, "FILLED",
                    info.get("sysid", ""),
                    info.get("risk_reason", "filled before cancel"),
                    matched=m2)
        _g_pending.pop(sid, None)
        _g_today_fill_count += 1
        return
    if not dead:
        # 撤单未确认死透 → 延后60s复查，不重报不计数（防双倍持仓）
        print("[P16V13][TIMEOUT-UNCONFIRMED] %s 撤单未确认，延后60s复查" % sid)
        info["time"] = now - PENDING_TIMEOUT + 60
        return
    if retry >= MAX_RETRY:
        print("[P16V13][ABANDON] %s %s 超时retry=%d filled=%d" % (sid, code, retry, filled_so_far))
        _write_fill(C, date, sid, info, code, action, filled_so_far, "ABANDONED",
                    info.get("sysid", ""),
                    info.get("risk_reason", "timeout, retry exhausted"))
        _g_pending.pop(sid, None)
        _g_today_abandon_count += 1
        return
    remaining = vol - filled_so_far
    if remaining <= 0:
        _g_pending.pop(sid, None)
        return
    retry_px = _refresh_last_price(C, code, float(info.get("price", 0) or 0))
    info["last_price"] = retry_px
    ret = _do_passorder(C, action, code, remaining, retry_px)
    print("[P16V13][RETRY-%d] %s %s %d股 px=%.3f ret=%s" % (
        retry + 1, sid, code, remaining, retry_px, ret))
    oid3, m3 = _lookup_order(C, code, remaining, action, sid=sid)
    sysid3 = ""
    if m3:
        sysid3 = str(getattr(m3, "m_strSysid", "") or "")
    _acc_order_id(info, oid3 or _extract_order_id(m3))
    info["time"] = now
    info["retry"] = retry + 1
    info["cur_vol"] = remaining
    info["order_id"] = oid3 or ""
    info["sysid"] = sysid3


def _handle_cancel_confirm(C, date, sid, info, now):
    """撤单已发后确认终态（2026-09-01 实锤定稿）。
    已成(status56/deal>=cur_vol)→FILLED；已撤(53/54)→CANCELED；废单(55/57)→REJECTED；
    仍活跃→撤单未生效，保留 pending + 90s 重发（绝不假写 CANCELED，fail-loud）；
    查不到订单→状态未知，写 UNCONFIRMED（绝不写假 CANCELED）。"""
    global _g_today_fill_count
    code = info["code"]
    action = info["action"]
    cur_vol = info.get("cur_vol", info.get("vol", 0))
    filled_so_far = info.get("filled_so_far", 0)
    _oid, m = _lookup_order(C, code, cur_vol, action, sid=sid, active_only=False)
    if m is not None:
        # 首反查才拿到委托号时必须立即累计，否则 _write_fill 的 deal 查询会遗漏该笔明细
        _acc_order_id(info, _extract_order_id(m) or _oid or "")
        st = int(getattr(m, "m_nOrderStatus", 0) or 0)
        dv = _extract_deal_volume(m, fallback_vol=cur_vol)
        # 补录 sysid（m_strOrderSysID 柜台合同号，官方 cancel() 撤单用）
        _sid_now = str(getattr(m, "m_strSysid", "") or getattr(m, "m_strOrderSysID", "") or info.get("sysid", ""))
        if _sid_now:
            info["sysid"] = _sid_now
        if dv >= cur_vol or st == 56:
            total_deal = filled_so_far + dv
            _write_fill(C, date, sid, info, code, action, total_deal, "FILLED",
                        info.get("sysid", ""),
                        info.get("risk_reason", "filled while canceling"),
                        matched=m)
            _g_pending.pop(sid, None)
            _g_today_fill_count += 1
            print("[P16V13][CANCEL-CONFIRM-FILLED] %s %s deal=%d" % (sid, code, total_deal))
            return
        if st in (53, 54):
            _write_fill(C, date, sid, info, code, action, filled_so_far + dv, "CANCELED",
                        info.get("sysid", ""),
                        info.get("risk_reason", "cancel confirmed"),
                        matched=m)
            _g_pending.pop(sid, None)
            print("[P16V13][CANCEL-CONFIRMED] %s %s status=%d" % (sid, code, st))
            return
        if st == 57:
            _write_fill(C, date, sid, info, code, action, filled_so_far + dv, "REJECTED",
                        info.get("sysid", ""), "cancel rejected status=%d" % st,
                        matched=m)
            _g_pending.pop(sid, None)
            print("[P16V13][CANCEL-REJECTED] %s %s status=%d" % (sid, code, st))
            return
        # 55=部成是活跃态（原单仍在成交），不能当 REJECTED 弹出 pending（假终态=真单孤儿）
        # 仍活跃（2/48/49/50 等非终态）：撤单未生效！保留 pending（fail-loud），90s 后重发最多2次
        print("[P16V13][CANCEL-NOT-EFFECTIVE] %s %s status=%d 撤单未生效，订单仍活跃，保留pending复查" % (sid, code, st))
        if now - info.get("time", now) > 90 and info.get("cancel_retry", 0) < 2:
            info["cancel_retry"] = info.get("cancel_retry", 0) + 1
            info["time"] = now
            _cancel_order_by_id(C, code, _extract_order_id(m) or info.get("order_id", ""), info.get("sysid", ""))
            print("[P16V13][CANCEL-RETRY] %s %s 重发撤单 %d/2" % (sid, code, info["cancel_retry"]))
        return
    # m is None：查不到订单（可能已撤/已成交/列表过期）→ UNCONFIRMED（绝不写假终态）
    print("[P16V13][CANCEL-UNKNOWN] %s %s 撤单后订单查不到，状态未知，写UNCONFIRMED" % (sid, code))
    _write_fill(C, date, sid, info, code, action, filled_so_far, "UNCONFIRMED",
                info.get("sysid", ""),
                "cancel state unknown (may be canceled or filled)")
    _g_pending.pop(sid, None)


def _check_pending_orders(C, date):
    """检查所有pending订单状态。主判据=成交量(m_nDealVolume)，状态码仅辅助收尾。
    1a) filled_so_far+deal_vol >= vol → FILLED（成交量硬事实优先于一切状态码）
    1b) status=57 废单 → 计retry重报剩余量（55=部成是活跃态，绝不当废单）
    1c) status in (53,54) → CANCELED 收尾(vol=total_deal)
    1d) deal>0 → PARTIAL_FILLED 进度回写
    然后超时判断 → 撤单→确认死透→重报剩余量；未确认延后复查。"""
    global _g_today_fill_count, _g_today_abandon_count
    now = time.time()
    for sid in list(_g_pending.keys()):
        info = _g_pending[sid]
        action = info["action"]
        code = info["code"]
        vol = info.get("vol", 0)
        cur_vol = info.get("cur_vol", vol)
        filled_so_far = info.get("filled_so_far", 0)

        # 撤单确认态：只确认终态（已成→FILLED / 已撤→CANCELED / 废单→REJECTED / 超时强制），不走重试/超时路径
        if info.get("cancel_requested"):
            _handle_cancel_confirm(C, date, sid, info, now)
            continue

        oid, matched = _lookup_order(C, code, cur_vol, action, sid=sid, active_only=False)
        if matched is None:
            elapsed = now - info.get("time", now)
            if elapsed > PENDING_TIMEOUT:
                _handle_timeout_retry(C, date, sid, info, now)
            else:
                print("[P16V13][PENDING-NO-ORDER] %s %s elapsed=%.0fs" % (sid, code, elapsed))
            continue

        deal_vol = _extract_deal_volume(matched, fallback_vol=cur_vol)
        status = int(getattr(matched, "m_nOrderStatus", 0) or 0)
        sysid = str(getattr(matched, "m_strSysid", "") or getattr(matched, "m_strOrderSysID", "") or info.get("sysid", ""))
        if sysid:
            info["sysid"] = sysid
        # 回填 order_id：首次反查（风控下单时）可能因缓存延迟未抓到，状态机内持续补齐，
        # 否则撤单/超时重报拿不到真实委托号（2026-09-01 P0：撤单空ID→真单变孤儿）
        # 同时累计进 order_ids（多轮委托全部留痕，供真实成交均价查询覆盖所有轮次成交）
        oid_bk = _extract_order_id(matched)
        if oid_bk:
            _acc_order_id(info, oid_bk)
            if not info.get("order_id"):
                info["order_id"] = oid_bk
        total_deal = filled_so_far + deal_vol

        # 1a) 成交量硬事实优先：已成交 >= 目标量 → FILLED
        if total_deal >= vol:
            print("[P16V13][FILLED] %s %s deal=%d" % (sid, code, total_deal))
            _write_fill(C, date, sid, info, code, action, total_deal, "FILLED",
                        sysid, info.get("risk_reason", "deal confirmed"), matched=matched)
            _g_pending.pop(sid, None)
            _g_today_fill_count += 1
            continue
        # 1b) 废单(57终态) → 计retry重报剩余量
        # 2026-09-02 P0 修复：55=部成(活跃态，原单仍在成交)，不是废单！误当废单直接重报剩余量
        # 且不撤原单 → 原单继续成交 + 重报单也成交 = 双倍建仓（9/2 实锤 600262 3000→6400）。
        # 55 走 1d/1a 等原单自然成交满；只有 status=57(真废单终态) 才重报。
        if status == 57:
            print("[P16V13][REJECTED] %s %s status=57废单 deal=%d" % (sid, code, deal_vol))
            _handle_rejected_retry(C, date, sid, info, now, deal_vol)
            continue
        # 1c) 撤类终态(53,54) → CANCELED 收尾（vol=total_deal，防漏计最后成交）
        if status in CANCEL_STATUS:
            print("[P16V13][CANCELED-FROM-QMT] %s %s status=%d deal=%d" % (sid, code, status, total_deal))
            _write_fill(C, date, sid, info, code, action, total_deal, "CANCELED",
                        sysid,
                        info.get("risk_reason", "canceled by QMT, status=%d" % status),
                        matched=matched)
            _g_pending.pop(sid, None)
            continue
        # 1d) 有成交未满 → PARTIAL_FILLED 进度留痕（filled_so_far不变，deal_vol是累计值）
        # 注意：部成也必须走超时判断（否则停滞单永不撤/永不重报，挂到日终被 NEW-DAY 清掉）
        if deal_vol > 0:
            print("[P16V13][PARTIAL] %s %s deal=%d/%d" % (sid, code, total_deal, vol))
            _write_fill(C, date, sid, info, code, action, total_deal, "PARTIAL_FILLED",
                        sysid, info.get("risk_reason", "partial deal, waiting more"),
                        matched=matched)
            # 部成后若停滞超过 PENDING_TIMEOUT，同样走超时撤剩余/重报
            elapsed = now - info.get("time", now)
            if elapsed > PENDING_TIMEOUT:
                _handle_timeout_retry(C, date, sid, info, now)
            continue

        # 2) 超时判断（有活跃单但无成交）
        elapsed = now - info.get("time", now)
        if elapsed > PENDING_TIMEOUT:
            _handle_timeout_retry(C, date, sid, info, now)
            continue
        print("[P16V13][PENDING-STATUS] %s %s status=%d deal=%d" % (sid, code, status, deal_vol))


# ============================================================
# 内置实时风控（止损/止盈/追盈，纯行情驱动；逻辑与文件桥版零改动，仅数据源替换）
# ============================================================
def _check_risk_signals(C, date):
    """内置实时风控（自闭环版）：白名单∩账户position纳管（成本锚=position.m_dOpenPrice，逐tick自愈）
    → get_full_tick → 三规则判断 → 触发即卖出走pending。
    白名单外持仓绝不纳管绝不触碰（资金红线）；持仓消失自动出管。"""
    global _g_today_risk_count
    # 1) 账户持仓纳管（白名单过滤：白名单外持仓绝不触碰——资金分配红线）
    f_get = globals().get("get_trade_detail_data")
    if f_get is None:
        f_get = getattr(C, "get_trade_detail_data", None)
    if f_get is None:
        return
    try:
        positions = f_get(ACCOUNT_ID, "stock", "position")
        acct_pos = {}
        if positions:
            for p in positions:
                code = str(getattr(p, "m_strInstrumentID", "") or getattr(p, "m_strSecurityCode", "") or "")
                if not code:
                    continue
                if "." not in code:
                    if code.startswith("6") or code.startswith("688"):
                        code = "%s.SH" % code
                    else:
                        code = "%s.SZ" % code
                if code not in _g_whitelist:
                    continue
                vol = int(getattr(p, "m_nVolume", 0) or 0)
                can_use = int(getattr(p, "m_nCanUseVolume", 0) or 0)
                if vol <= 0:
                    continue
                cost = 0.0
                for attr in ("m_dOpenPrice", "m_dCostPrice", "m_dPrice"):
                    v = float(getattr(p, attr, 0) or 0)
                    if v > 0:
                        cost = v
                        break
                acct_pos[code] = {"vol": vol, "can_use": can_use, "cost": cost}
        for code, info in acct_pos.items():
            if code in _g_positions:
                _g_positions[code]["vol"] = info["vol"]
                _g_positions[code]["can_use"] = info["can_use"]
                # 成本锚自愈：position 成本价逐tick刷新（外部换仓/成本变化自动跟进；取不到不覆盖）
                if info["cost"] > 0:
                    _g_positions[code]["cost"] = info["cost"]
            else:
                # 白名单内新持仓（外部换仓买入等）：入账；峰值=快照或成本底，运行中 tick high 重锚
                snap_peak = float(_g_peak_snap.get(code, 0) or 0)
                peak0 = info["cost"] if info["cost"] > 0 else 0.0
                if snap_peak > peak0:
                    peak0 = snap_peak
                _g_positions[code] = {"cost": info["cost"], "vol": info["vol"],
                                      "can_use": info["can_use"], "peak": peak0}
                print("[P16V13][POS-NEW] %s 入账 cost=%.4f vol=%d can_use=%d peak=%.4f（白名单内新持仓）" % (
                    code, info["cost"], info["vol"], info["can_use"], peak0))
        for code in list(_g_positions.keys()):
            if code not in acct_pos:
                _g_positions.pop(code, None)
                _g_risk_sold.discard(code)
    except Exception as e:
        print("[P16V13][RISK-POS-ERR] %s" % e)
        return
    if not _g_positions:
        return
    # 2) 逐持仓评估（三规则照搬 qmt_monitor.evaluate）
    codes = list(_g_positions.keys())
    try:
        ticks = C.get_full_tick(codes)
    except Exception:
        ticks = {}
    if not ticks:
        return
    now = time.time()
    for code, info in _g_positions.items():
        if code in _g_risk_sold:
            continue
        cost = info.get("cost", 0.0)
        if cost <= 0:
            continue
        tick = ticks.get(code)
        if not tick:
            continue
        last = float(tick.get("lastPrice", 0) or 0)
        if last <= 0:
            continue
        can_use = info.get("can_use", info.get("vol", 0))
        if can_use <= 0:
            # T+1 卫生（T-20260907-002）：当日买入(T+1锁)不评估卖出信号、不并入峰值（避免买入日高点污染追盈峰值）
            continue
        high = max(float(tick.get("high", last) or last), last, info.get("peak", cost))
        info["peak"] = high
        action = "HOLD"
        note = ""
        stop_line = cost * (1 - STOP_LOSS_PCT)
        tp_line = cost * (1 + TAKE_PROFIT_PCT)
        if last <= stop_line:
            action, note = "SELL_STOP", "现价%.2f 跌破止损位%.2f" % (last, stop_line)
        elif last >= tp_line:
            action, note = "SELL_TAKE_PROFIT", "现价%.2f 达止盈位%.2f" % (last, tp_line)
        elif info["peak"] >= cost * (1 + TRAIL_ACTIVATE_PCT):
            # 追盈激活阈值 + 保本底线（T-20260907-002）：峰值≥成本×(1+8%)才追踪；线=max(成本,peak×0.92)
            trail_line = max(cost, info["peak"] * (1 - TRAILING_PCT))
            if last <= trail_line:
                action, note = "SELL_TRAILING", "从高点%.2f 回撤%.0f%% 触发追盈(线%.2f)" % (
                    info["peak"], TRAILING_PCT * 100, trail_line)
        if action == "HOLD":
            continue
        # 触发 → passorder 卖出 → 成功才记防重复标记（下单失败当日可重试，防裸奔）
        sid_pre = "P16_%s_RSK%04d" % (date, _g_today_risk_count + 1)
        ret = _do_passorder(C, "SELL", code, can_use, last)
        if ret is None or ret != 0:
            print("[P16V13][RISK-FAIL] %s %s %s 卖%d股 ret=%s 未记 risk_sold（下轮重试） | %s" % (
                action, sid_pre, code, can_use, ret, note))
            continue
        _g_risk_sold.add(code)
        _g_today_risk_count += 1
        sid = "P16_%s_RSK%04d" % (date, _g_today_risk_count)
        print("[P16V13][RISK] %s %s %s 卖%d股 ret=%s | %s" % (action, sid, code, can_use, ret, note))
        _g_pending[sid] = {
            "code": code,
            "bridge_code": _to_bridge_code(code),
            "action": "SELL",
            "vol": can_use,
            "cur_vol": can_use,
            "filled_so_far": 0,
            "price": last,
            # 滑点/跳空基线（T-20261002）：trigger_price=触发时现价（滑点基准，市价单委托价=触发价，
            # 用委托价会把真实跳空亏损算成 0 滑点从而掩盖问题）；cost_price=持仓成本（跳空基准）。
            # 注意：止损/止盈/追盈三种触发都要留 cost_price，否则跳空幅度无法计算。
            "trigger_price": last,
            "cost_price": cost,
            "last_price": last,
            "time": now,
            "retry": 0,
            "order_id": "",
            "order_ids": [],
            "sysid": "",
            "status": "PENDING",
            "risk_reason": action,
        }


# ============================================================
# 状态重建（L2 账户即账本 + L3 源码快照，方案 §4.1）
# ============================================================
def _rebuild_state(C):
    """init 状态重建四步（任一步异常由 init 捕获转被动，绝不「活跃+状态缺失」盲跑）：
    1) 预检：position 接口可调用且返回 list（探针从文件改为接口，沙箱合规）；
    2) 白名单归一化（V13_CODES；空 = 完全被动）；「白名单 ∩ 账户position」= 纳管集
       （成本锚=m_dOpenPrice 多字段兜底；白名单外持仓绝不触碰——资金红线）；
    3) L3 PEAK_SNAPSHOT 初始化各票追盈峰值（只升不降，T-20260910 语义）；
    4) 当日 order 表反查 remark=ORDER_REMARK → 重建 risk_sold 去重 + RSK 序号水位（防重启重复卖出）。"""
    global _g_passive, _g_risk_sold, _g_today_risk_count, _g_whitelist, _g_peak_snap
    f_get = globals().get("get_trade_detail_data")
    if f_get is None:
        f_get = getattr(C, "get_trade_detail_data", None)
    if f_get is None:
        raise RuntimeError("无 get_trade_detail_data 接口")
    # 1) 预检：position 接口可调用且返回 list
    positions = f_get(ACCOUNT_ID, "stock", "position")
    if positions is None or not isinstance(positions, list):
        raise RuntimeError("position 探针失败（接口不可用或返回非 list）: %r" % type(positions))
    # 2) 白名单归一化；白名单为空 = 部署未填 = 完全被动（不评估不下单）
    _g_whitelist = set()
    for c in V13_CODES:
        qc = _to_qmt_code_any(c)
        if qc:
            _g_whitelist.add(qc)
        else:
            print("[P16V13][WARN] 白名单代码无法归一化，已忽略: %r" % (c,))
    # ★ 2026-10-23（T-20261023-002）：外部下单票自动并入白名单（与 G2/ENS 同款）。
    # 换仓买入由外部 rebalance_daily.py 完成（remark=planA_new_top），
    # 内置白名单恒量 V13_CODES 不可能预知新票 → 必须扫当日 order 反推，否则新仓零风控覆盖。
    # 官方口径（§06）：用订单备注 + 交易日建立归属与幂等，而非硬编码。
    _ext_added = []
    try:
        _day_orders = f_get(ACCOUNT_ID, "stock", "order")
        for o in (_day_orders or []):
            try:
                _rk = str(getattr(o, "m_strRemark", "") or "")
                if EXTERNAL_REMARK_BUY not in _rk:
                    continue
                _oc = _to_qmt_code_any(str(getattr(o, "m_strInstrumentID", "") or ""))
                if _oc and _oc not in _g_whitelist:
                    _g_whitelist.add(_oc)
                    _ext_added.append(_oc)
            except Exception:
                continue
    except Exception as _e:
        print("[P16V13][WARN] 外部委托白名单反推失败（可能零风控覆盖，需人工核对）: %r" % (_e,))
    if _ext_added:
        print("[P16V13][INIT] 外部委托(remark=%s)反推并入白名单 %d 只: %s" % (
            EXTERNAL_REMARK_BUY, len(_ext_added), ",".join(sorted(_ext_added))))
    if not _g_whitelist:
        _g_passive = True
        print("[P16V13][WARN] V13_CODES 白名单为空且当日无外部买入委托，完全被动模式"
              "（不评估不下单；被动下每30s自动重扫外部买入，见 _late_bind_whitelist）")
        return
    # 纳管集 = 白名单 ∩ 账户 position（白名单外绝不纳管）
    managed = 0
    for p in positions:
        code = str(getattr(p, "m_strInstrumentID", "") or getattr(p, "m_strSecurityCode", "") or "")
        if not code:
            continue
        if "." not in code:
            if code.startswith("6") or code.startswith("688"):
                code = "%s.SH" % code
            else:
                code = "%s.SZ" % code
        if code not in _g_whitelist:
            continue
        vol = int(getattr(p, "m_nVolume", 0) or 0)
        can_use = int(getattr(p, "m_nCanUseVolume", 0) or 0)
        if vol <= 0:
            continue
        cost = 0.0
        for attr in ("m_dOpenPrice", "m_dCostPrice", "m_dPrice"):
            v = float(getattr(p, attr, 0) or 0)
            if v > 0:
                cost = v
                break
        _g_positions[code] = {"cost": cost, "vol": vol, "can_use": can_use, "peak": cost}
        managed += 1
        if cost <= 0:
            print("[P16V13][WARN] %s 成本字段缺失(m_dOpenPrice<=0)，该票风控暂跳过待逐tick自愈" % code)
    if managed <= 0:
        # 交集为空：非故障（可能空仓/外部尚未买入），保持只监控，白名单内持仓出现即自动纳管
        print("[P16V13][WARN] 白名单(%d只) ∩ 账户position 交集为空，进入只监控模式"
              "（白名单内持仓出现即自动纳管）" % len(_g_whitelist))
    else:
        print("[P16V13][INIT] 白名单∩position 纳管 %d/%d 只" % (managed, len(_g_whitelist)))
    # 3) L3 追盈峰值快照（只升不降：快照仅更高时采纳，防 0 污染把成本底覆盖回 0，T-20260910）
    for sc, sp in PEAK_SNAPSHOT.items():
        qc = _to_qmt_code_any(sc)
        if not qc:
            continue
        try:
            pv = float(sp or 0.0)
        except Exception:
            continue
        _g_peak_snap[qc] = pv
        if qc in _g_positions and pv > float(_g_positions[qc].get("peak", 0.0) or 0.0):
            _g_positions[qc]["peak"] = pv
    # 4) 当日 order 反查 remark∈ALL_REMARKS → 重建 risk_sold + RSK 序号水位
    #    （2026-10-23：并入外部 remark，防内置重启后对外部已卖票重复卖出）
    orders = f_get(ACCOUNT_ID, "stock", "order")
    risk_orders = 0
    if orders:
        for o in orders:
            try:
                remark = str(getattr(o, "m_strRemark", "") or "")
                if not any(rk in remark for rk in ALL_REMARKS):
                    continue
                ocode = str(getattr(o, "m_strInstrumentID", "") or "")
                if not ocode:
                    continue
                if "." not in ocode:
                    if ocode.startswith("6") or ocode.startswith("688"):
                        ocode = "%s.SH" % ocode
                    else:
                        ocode = "%s.SZ" % ocode
                _g_risk_sold.add(ocode)
                risk_orders += 1
            except Exception:
                continue
    if risk_orders > 0:
        _g_today_risk_count = risk_orders
        print("[P16V13][INIT] 当日order反查重建 risk_sold=%d只 RSK水位=%d" % (
            len(_g_risk_sold), _g_today_risk_count))


# ============================================================
# 心跳（print 通道，方案 §3.4：沙箱外旁路 grep [HEART] 检测超时告警）
# ============================================================
def _print_heartbeat(date):
    """print 心跳（节流60秒，tick 级 handlebar 全量打印会刷爆日志）。
    替代原 heart_v13_*.json 文件心跳；pending 快照不再持久化（重启由 risk_sold 水位防重复）。"""
    global _g_last_heart_ts
    now = time.time()
    if now - _g_last_heart_ts < 60:
        return
    _g_last_heart_ts = now
    print("[P16V13][HEART] %s date=%s pending=%d positions=%d risk_sold=%d risk_count=%d fills=%d slippage_alerts=%d passive=%s" % (
        _now_str(), date, len(_g_pending), len(_g_positions), len(_g_risk_sold),
        _g_today_risk_count, _g_today_fill_count, _g_slippage_alert_count, _g_passive))
    _print_holdings()


def _print_holdings():
    """[自闭环] 持仓代码明细（旁路 grep [HOLD] 即得完整持仓，无需读盘）。
    格式：code=vol@cost，按 code 排序保证稳定输出；空仓打印 n=0 -。"""
    parts = []
    for code in sorted(_g_positions.keys()):
        info = _g_positions.get(code) or {}
        try:
            parts.append("%s=%d@%.3f" % (code, int(info.get("vol", 0) or 0),
                                         float(info.get("cost", 0) or 0)))
        except Exception:
            parts.append("%s=?" % code)
    print("[P16V13][HOLD] %s n=%d %s" % (
        _now_str(), len(_g_positions), " ".join(parts) if parts else "-"))


# ============================================================
# 被动模式延迟绑定（T-20261023-002，与 G2/ENS 同款）
# ============================================================
def _late_bind_whitelist(C):
    """被动模式下延迟绑定白名单。

    场景：策略 09:00 init 时账户空仓、当日无委托 → 白名单空 → 进被动；
          09:50 外部 rebalance_daily.py 用 remark=planA_new_top 买入 →
          若不做延迟绑定，内置风控永远 passive，新仓零止损覆盖（T-20261020-005 同型 P0）。

    做法（官方 §06「订单备注 + 交易日建立归属」）：每 30s 扫一次当日 order，
    凡 remark 命中 EXTERNAL_REMARK_BUY 的票并入白名单；一旦非空则重建持仓并解除被动。

    返回 True = 已成功绑定并解除被动（调用方应继续走正常 handlebar 流程）。
    """
    global _g_passive, _g_last_bind_try, _g_whitelist
    now = time.time()
    if now - _g_last_bind_try < 30.0:
        return False
    _g_last_bind_try = now
    f_get = globals().get("get_trade_detail_data")
    if f_get is None:
        f_get = getattr(C, "get_trade_detail_data", None)
    if f_get is None:
        return False
    added = []
    try:
        for o in (f_get(ACCOUNT_ID, "stock", "order") or []):
            try:
                if EXTERNAL_REMARK_BUY not in str(getattr(o, "m_strRemark", "") or ""):
                    continue
                c = _to_qmt_code_any(str(getattr(o, "m_strInstrumentID", "") or ""))
                if c and c not in _g_whitelist:
                    _g_whitelist.add(c)
                    added.append(c)
            except Exception:
                continue
    except Exception:
        return False
    if not added:
        return False
    print("[P16V13][LATE-BIND] 被动模式下捕获外部买入 %d 只并入白名单: %s"
          % (len(added), ",".join(sorted(added))))
    # 重建持仓纳管集（复用 init 口径：白名单 ∩ 账户 position）
    managed = 0
    try:
        for p in (f_get(ACCOUNT_ID, "stock", "position") or []):
            code = _to_qmt_code_any(str(getattr(p, "m_strInstrumentID", "")
                                        or getattr(p, "m_strSecurityCode", "") or ""))
            if not code or code not in _g_whitelist:
                continue
            vol = int(getattr(p, "m_nVolume", 0) or 0)
            if vol <= 0:
                continue
            cost = 0.0
            for attr in ("m_dOpenPrice", "m_dCostPrice", "m_dPrice"):
                v = float(getattr(p, attr, 0) or 0)
                if v > 0:
                    cost = v
                    break
            _g_positions[code] = {"cost": cost, "vol": vol,
                                  "can_use": int(getattr(p, "m_nCanUseVolume", 0) or 0),
                                  "peak": cost}
            managed += 1
    except Exception as e:
        print("[P16V13][LATE-BIND] 持仓重建异常，保持被动: %r" % (e,))
        return False
    if managed <= 0:
        print("[P16V13][LATE-BIND] 白名单已扩充但账户暂无对应持仓（T+1 未成交），继续等待")
        return False
    _g_passive = False
    print("[P16V13][LATE-BIND] 解除被动，纳管 %d 只: %s"
          % (managed, ",".join(sorted(_g_positions.keys()))))
    return True


# ============================================================
# QMT 生命周期
# ============================================================
def init(C):
    """策略初始化（自闭环版）：预检 + 四步重建（position→白名单→峰值快照→当日order反查）。
    任一步异常 → 被动模式（不评估不下单，绝不「活跃+状态缺失」盲跑，2026-10-14 形态禁令）。"""
    global _g_today, _g_passive
    try:
        C.set_account(ACCOUNT_ID)
    except Exception:
        pass
    _g_today = _today_str()
    try:
        _rebuild_state(C)
    except Exception:
        _g_passive = True
        print("[P16V13][INIT-REBUILD-ERR] 状态重建异常，转被动模式（不评估不下单），"
              "疑似沙箱/接口异常，请重启客户端并确认交易账号登录成功后重载策略: %s" % traceback.format_exc())
    print("[P16V13][BUILD] BUILD_TAG=%s account=%s mode=自闭环纯风控(零文件)" % (BUILD_TAG, ACCOUNT_ID))
    print("[P16V13][INIT] date=%s passive=%s pending=%d positions=%d risk_sold=%d whitelist=%d" % (
        _g_today, _g_passive, len(_g_pending), len(_g_positions), len(_g_risk_sold), len(_g_whitelist)))


def handlebar(C):
    """每次触发（部署时按 tick 级/高频设置）：被动守卫 → 内置实时风控(止损/止盈/追盈)
    → pending状态机 → print心跳。"""
    global _g_today, _g_slippage_alert_count, _g_last_passive_ts

    # 被动守卫（红线：绝不活跃盲跑）
    # ★ 2026-10-23（T-20261023-002）：被动下每 30s 尝试延迟绑定（外部买入票 planA_new_top 自动纳管）。
    #   绑定成功则解除被动、继续走正常风控流程；仍失败则只打被动心跳退出。
    if _g_passive:
        if _late_bind_whitelist(C):
            pass   # 已解除被动，继续往下执行
        else:
            now = time.time()
            if now - _g_last_passive_ts >= 300:
                _g_last_passive_ts = now
                print("[P16V13][PASSIVE] %s 被动模式运行中（不评估不下单），"
                      "查上方 WARN/ERR 行定位真实原因" % _now_str())
            return

    # 日期切换重置（pending 清空：隔日委托已失效；risk_sold 清空：新一天允许重新触发）
    today = _today_str()
    if today != _g_today:
        if _g_slippage_alert_count:
            print("[P16V13][SLIPPAGE-DAY-SUMMARY] 前一交易日不利滑点/跳空超限 %d 次" % _g_slippage_alert_count)
        _g_today = today
        _g_pending.clear()
        _g_risk_sold.clear()
        _g_slippage_alert_count = 0   # 告警计数按交易日重置（否则跨日累加，日终无法归因到具体某天）
        print("[P16V13][NEW-DAY] date=%s pending清空 risk_sold清空 slippage_alert清零" % today)

    # 交易时段判断：09:30-11:30 或 13:00-15:00
    try:
        qtime = C.get_current_time()
    except Exception:
        qtime = time.strftime("%H:%M:%S")
    hour_min = qtime[:5]
    in_session = ("09:30" <= hour_min <= "11:30") or ("13:00" <= hour_min <= "15:00")

    if not in_session:
        try:
            _print_heartbeat(_g_today)
        except Exception:
            print("[P16V13][HANDLEBAR-ERR-HEART] %s" % traceback.format_exc())
        return

    # 1) 内置实时风控（止损/止盈/追盈，纯行情驱动，tick 级判断）
    try:
        _check_risk_signals(C, _g_today)
    except Exception:
        print("[P16V13][HANDLEBAR-ERR-RISK] %s" % traceback.format_exc())

    # 2) pending 状态机（风控卖出委托的成交跟踪/超时重试）
    try:
        _check_pending_orders(C, _g_today)
    except Exception:
        print("[P16V13][HANDLEBAR-ERR-PENDING] %s" % traceback.format_exc())

    # 3) print 心跳（旁路监控主通道）
    try:
        _print_heartbeat(_g_today)
    except Exception:
        print("[P16V13][HANDLEBAR-ERR-HEART] %s" % traceback.format_exc())


def exit(C):
    """日终摘要（print 通道）。"""
    pending_count = len(_g_pending)
    print("[P16V13][EXIT] date=%s 委托=%d 成交=%d 放弃=%d 最终pending=%d 滑点/跳空超限=%d passive=%s" % (
        _g_today, _g_today_order_count, _g_today_fill_count,
        _g_today_abandon_count, pending_count, _g_slippage_alert_count, _g_passive))
