# coding=utf-8
"""
g2 大QMT 文件桥 · 内置执行器（Python 3.6.8 单文件源码）

外部信号层把买卖指令写到文件桥，本策略在大QMT内轮询读取指令 → 委托 → 反查 → 超时撤单重试 → 状态回写。

数据流：
    外部(选股/风控, Python3.10)                内置执行器(大QMT, Python3.6)
            | 写 cmd/orders_<date>.json  -->  读指令(seq幂等)
            | 写 cmd/cancel_<date>.json  -->  撤单处理
            | <-- 写 state/fills_<date>.json    委托/成交/失败回写
            | <-- 写 state/positions_<date>.json 持仓快照
            | <-- 写 state/asset_<date>.json    资产快照
            | <-- 写 state/heart_<date>.json    心跳+已处理seq
"""
import json
import os
import time
import traceback
from datetime import datetime

# ============================================================
# 常量
# ============================================================
BUILD_TAG = "20260831-000000"

BRIDGE_DIR = "D:/QMT_POOL/g2_bridge"
CMD_DIR = os.path.join(BRIDGE_DIR, "cmd")
STATE_DIR = os.path.join(BRIDGE_DIR, "state")

ACCOUNT_ID = "70180771"

# passorder 常量（对齐ATR战备模板：prType=5最新价，第8参=remark对账标识，第9参=2，第11参=C）
OP_BUY = 23
OP_SELL = 24
ORDER_TYPE_VOL = 1101
PRTYPE_LATEST = 5
ORDER_REMARK = "P16G2桥"

# pending 超时与重试（买卖统一：300秒/3次，防「30秒即放弃永不补单」）
PENDING_TIMEOUT = 300
MAX_RETRY = 3

# 订单状态码（对齐 QMT撤单重委托方案调研.md 标准表）：
# 活跃={48未报,49待报,50已报,51已报待撤,52部成待撤,55部成}；终态={53部撤,54已撤,56全成,57废单}
CANCEL_STATUS = (53, 54)
CONFIRM_DEAD_STATUS = (53, 54, 57)   # 撤单死透=部撤/已撤/废单（55部成是活跃态不能算死）
ACTIVE_SKIP_STATUS = (53, 54, 57)    # active_only 时跳过终态（55部成是活跃态不能排除；调研 4.2 原写 54,55,57 有 bug）

# 内置实时风控参数（自 qmt_monitor.py 迁移，纯行情驱动）
STOP_LOSS_PCT = 0.07
TAKE_PROFIT_PCT = 0.15
TRAILING_PCT = 0.08
TRAIL_ACTIVATE_PCT = 0.08   # 追盈激活阈值（2026-09-07 修复，T-20260907-002）：峰值≥成本×(1+阈值)才追踪，防"追盈=追跌"
PEAK_STALE_HOURS = 24.0     # peak.json 距上次写盘超过此小时数视为 stale，init 跳过恢复（T-20260928-002：防桥宕机多日后拿陈旧 peak 判追盈→追认止损）

# ===== 自闭环纯风控版（2026-10-16，QMT 沙箱 m_nPythonOpFile=1 下零文件读写，方案 §4.2 Step 1）=====
# 持仓白名单：白名单 ∩ 账户 position = 纳管集；白名单外持仓绝不触碰（资金分配红线）。
# 空 = 部署未填 = 完全被动模式（不评估不下单）。op 部署前按当日实际持仓填入。
G2_CODES = ("605098.SH", "000607.SZ")

# 追盈历史峰值快照（跨日延续，T-20260910 只升不降语义）：{代码: 峰值}。
# PEAK_SNAPSHOT_DATE = 快照取数日（YYYYMMDD）；距今 > PEAK_STALE_HOURS 不恢复（T-20260928-002 防追认止损）。
# 快照缺失/过期的票运行中以当日 high 重锚（偏保守，方案 §5.6）。
PEAK_SNAPSHOT = {}
PEAK_SNAPSHOT_DATE = "20261015"

# 反查参数
LOOKUP_RETRIES = 6
LOOKUP_INTERVAL = 0.25

# 成交均价/跳空/滑点保护（2026-10-02 第3项，与 V1.3 同口径）
# deal 明细查询：QMT 约 100ms 分配 order_id 延迟 → 短轮询等成交落库
DEAL_QUERY_RETRIES = 2
DEAL_QUERY_INTERVAL = 0.2
DEAL_QUERY_MAX_ERR = 3      # 连续异常达此数 → 判定 deal 接口永久不可用，不再重试
MAX_ADVERSE_SLIPPAGE_PCT = 0.03   # 不利滑点告警阈值（卖出低于参考价 3%）
MAX_GAP_TOLERANCE_PCT = 0.02      # 跳空破位容忍阈值（现价低于止损线 2% 以上）

# 持仓/资产刷新间隔(秒)
POSITION_REFRESH_INTERVAL = 10

# ============================================================
# 全局状态
# ============================================================
_g_pending = {}
_g_last_cmd_seq = 0
_g_last_position_refresh = 0.0
_g_last_asset_refresh = 0.0
_g_today = ""
_g_today_order_count = 0
_g_today_fill_count = 0
_g_today_abandon_count = 0
_g_today_risk_count = 0
_g_positions = {}
_g_risk_sold = set()
_g_last_cfg_load = 0.0   # 孤儿自校准节流（cost=0 持仓重读当日成本表的时间戳，T-20260908）
_g_cfg_fallback_logged = False   # 成本表回退提示只打一次（T-20260910）
_g_diag_printed = False
_g_passive = False         # IO 预检失败后转被动（T-20261015-004，防沙箱下活跃实例盲跑）
_g_whitelist = set()       # 自闭环白名单（G2_CODES 归一化，init 填充；空=被动）
_g_peak_snap = {}          # L3 追盈峰值快照（PEAK_SNAPSHOT 归一化）
_g_last_heart_ts = 0.0      # print 心跳节流（60秒，tick 级 handlebar 全量打印会刷爆日志）

# deal 明细接口三态可用性 + 当日滑点/跳空告警计数（2026-10-02 第3项）
_g_deal_ok = None          # None=尚未探测 / True=可用 / False=永久不可用
_g_deal_err_count = 0      # 连续异常次数，达 DEAL_QUERY_MAX_ERR 熔断
_g_deal_no_oid = False     # 「deal 无委托号」告警只打一次
_g_slippage_alert_count = 0


# ============================================================
# [信号内产版 s4-5d] 静态常量与全局状态
#   方案 §4.2 Step2：信号引擎内移到 handlebar 时间闸门（09:50 对齐原换仓时点），
#   取数 -> 21 特征 -> 模型打分 -> 目标持仓 diff -> 既有订单执行链。
#   全部输入来自 QMT 内部 API + 源码内嵌快照（_g2_signal_snapshots.py 构建期生成）。
# ============================================================
# 信号时点（HH:MM，对齐原 09:50 G2 换仓任务）与执行窗口
SIGNAL_HM = "09:50"
SIGNAL_DEADLINE_HM = "09:58"      # 过点未跑则当日放弃（防盘中重复触发）
# 简化红线（拍板：内移版只带 prob + 简化红线；F2/F5/F6 沙箱内不可得）
# 依据：门禁 _top2_sim(train_g2.py:239) 用 score=prob*100 >= 60 即 prob>=0.60，
#       B21 k=1500 的 top2_ret=+0.04095 即该口径；与现役 total_new>=60 的外部红线
#       相比漏掉评分卡 F2/F5/F6 加成，属拍板接受的口径差（s4-5e 信号对齐 diff 验证）。
SIGNAL_REDLINE_PROB = 0.60
SIGNAL_TOP_N = 3                  # g2_config.TOP_N（等权目标只数）
SIGNAL_SELECT_TOP = 10             # 候选池（prob TopN，对齐 g2_config.SELECT_TOP）
SIGNAL_HOLD_DAYS = 15              # g2_config.HOLD_DAYS 持有期
SIGNAL_MIN_VOL = 100               # 整手
SIGNAL_DEPLOY_PCT = 0.98           # 满仓部署（保留 2% 现金）
SIGNAL_HALF_DEPLOY_PCT = 0.50      # 半仓（T=1）
SIGNAL_TIER_STOP_PCT = -1.5        # T=0 停买线
SIGNAL_TIER_HALF_PCT = -1.0        # T=1 半仓线
SIGNAL_TRADE_HOURS = 5             # 换仓交易日历按 5 小时/日折算（原 is_trade_day 由行情日历替代）
SIGNAL_MIN_BARS = 250              # dist_250_low 需 250 根
SIGNAL_BATCH = 200                 # get_market_data_ex 分批（对齐 ATR 实现）
SIGNAL_HS300_SYMS = ("000300.SH", "399300.SZ")   # 沪深300（QMT 内部行情，交替兜底）

# 信号版全局状态
_g_snap = None             # 解压后的快照字典（init 构建一次）
_g_signal_done = False     # 当日信号是否已执行（新交易日重置）
_g_signal_stage = ""       # 当日信号阶段标记（用于日志/诊断）
_g_signal_prob = {}        # 当日打分 {code: prob}（TopN 缓存，供日志）
_g_hold_dates = {}         # L3 期初持仓建仓日 {code: YYYYMMDD}（快照 init 载入）
_g_oppo_blocked = set()    # 对方策略持仓（买入互斥，替代原读对方账本文件；快照 OPPONENT_HOLDS）
_g_signal_capital = 0.0    # 资金池（L3 快照；运行中以 position 推算更新）
_g_fs_map = None           # float_share 快照 {code: 万股}（每日信号前构建一次）
_g_st_codes = set()        # ST 名单（SNAP_BLOB 尾块，_snap_load 填充）
_g_signal_bars = None      # 当日 bars 缓存（_compute_scores 填充，供低开量比校验/取价）
_g_signal_bought = set()   # 当日信号已买 code（同日重启防重复，order 反查重建）
_g_signal_seq = 0          # 当日信号订单序号水位（sid = P16_<date>_<seq>）
_g_trade_dates = None      # 近期交易日集合（持有期计数，init 取行情日历）

# ★ QMT 嵌入安全（AGENTS 红线：import 必须 try/except）：信号引擎依赖 numpy/pandas
#   （白名单内置库），缺失时置 None -> 信号段调用期抛 AttributeError -> 转被动 fail-safe。
try:
    import numpy as np
except Exception:
    np = None
try:
    import pandas as pd
except Exception:
    pd = None

# ---- vendor 注入占位（build_p16_g2.py 构建期替换为文件全文）----
# __VENDOR__:snapshot
# __VENDOR__:features
# __VENDOR__:model


# ============================================================
# 工具函数
# ============================================================
def _atomic_write_json(path, data):
    """[自闭环] 文件写已废止（QMT 沙箱 m_nPythonOpFile=1：open 已存在文件被拒，
    新建可写但读回被拒、不可依赖，方案 §1.1）。保留空壳供旧调用点安全通过；
    真实监控走 print → XtClient_FormulaOutput 日志（沙箱外旁路 grep）。"""
    return


def _write_cmd_alert(date, kind, path):
    """[自闭环] 告警落盘已废止 → 改 print（桥无推送能力，唯一通道是 QMT 日志）。"""
    print("[P16G2][ALERT] kind=%s path=%s (文件落盘已废止，走print通道)" % (kind, path))


def _read_json(path):
    """[自闭环] 文件读已废止（沙箱下 open 已存在文件必拒）。恒返回 None；
    调用方（cmd 消费/heart/peak/cfg）在纯风控版中已全部停用或改为账户接口。"""
    return None


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


def _to_bridge_code(code):
    """QMT代码(SH.600522) 转回桥协议代码(600522.SH)，供 fills 回写外部对账。"""
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


def _valid_bridge_code(code):
    """桥协议代码是否可转为QMT代码。"""
    parts = code.split(".")
    if len(parts) != 2:
        return False
    num, ex = parts[0], parts[1]
    if ex == "SH" and (num.startswith("6") or num.startswith("688")):
        return True
    if ex == "SZ" and (num.startswith("0") or num.startswith("3")):
        return True
    return False


def _fills_path(date):
    return os.path.join(STATE_DIR, "fills_%s.json" % date)


def _positions_path(date):
    return os.path.join(STATE_DIR, "positions_%s.json" % date)


def _asset_path(date):
    return os.path.join(STATE_DIR, "asset_%s.json" % date)


def _heart_path(date):
    return os.path.join(STATE_DIR, "heart_%s.json" % date)


def _orders_cmd_path(date):
    return os.path.join(CMD_DIR, "orders_%s.json" % date)


def _cancel_cmd_path(date):
    return os.path.join(CMD_DIR, "cancel_%s.json" % date)


def _orders_archive_path(date, seq):
    return os.path.join(CMD_DIR, "orders_%s_done_%s.json" % (date, seq))


def _cancel_archive_path(date, seq):
    return os.path.join(CMD_DIR, "cancel_%s_done_%s.json" % (date, seq))


def _positions_cfg_path(date):
    return os.path.join(CMD_DIR, "positions_cfg_%s.json" % date)


def _find_latest_cfg():
    """扫描 cmd 目录找最近一份 positions_cfg_*.json（按文件名日期倒序，T-20260910）。"""
    try:
        names = [n for n in os.listdir(CMD_DIR)
                 if n.startswith("positions_cfg_") and n.endswith(".json")]
        if not names:
            return None
        names.sort(reverse=True)
        return _read_json(os.path.join(CMD_DIR, names[0]))
    except Exception:
        return None


def _load_positions_cfg(date):
    """优先读当日成本表；缺失/账号不符时回退最近一份历史表（T-20260910 兜底）。
    背景：成本表由外部任务收盘后（约15:47）生成，盘中当日表不存在 → 孤儿校准打空 →
    cost=0 风控全天静默（peak.json=0.0）。持仓未变时历史成本仍有效，回退可恢复风控。
    回退提示每个进程只打一次，避免刷屏。"""
    global _g_cfg_fallback_logged
    cfg = _read_json(_positions_cfg_path(date))
    if cfg and str(cfg.get("account_id", "") or "") == ACCOUNT_ID:
        return cfg
    fb = _find_latest_cfg()
    if fb and str(fb.get("account_id", "") or "") == ACCOUNT_ID:
        if not _g_cfg_fallback_logged:
            _g_cfg_fallback_logged = True
            print("[P16G2][CFG-FALLBACK] 当日成本表缺失，回退最近表 date=%s（持仓未变时成本仍有效）"
                  % str(fb.get("date", "")))
        return fb
    return None


def _peak_path():
    return os.path.join(STATE_DIR, "peak.json")


def _archive_cmd(path, date, seq, prefix):
    """归档已处理的cmd文件。目标已存在则先删除。"""
    if prefix == "orders":
        dest = _orders_archive_path(date, seq)
    else:
        dest = _cancel_archive_path(date, seq)
    if os.path.exists(dest):
        try:
            os.remove(dest)
        except Exception:
            pass
    try:
        os.rename(path, dest)
    except Exception:
        pass


def _scan_stale_orders():
    """启动时扫描未归档的 orders/cancel 命令文件（只告警不补执行：
    价格可能已 stale，宁可丢单不可重复。供人工/外部监控处置）。"""
    try:
        if not os.path.isdir(CMD_DIR):
            return
        for name in sorted(os.listdir(CMD_DIR)):
            if not name.endswith(".json"):
                continue
            if "_done_" in name:
                continue
            if not (name.startswith("orders_") or name.startswith("cancel_")):
                continue
            full = os.path.join(CMD_DIR, name)
            print("[P16G2][STALE-CMD] 未归档命令文件: %s" % full)
            try:
                _atomic_write_json(
                    os.path.join(STATE_DIR, "stale_cmd_alert.json"),
                    {"file": full, "time": _now_str(), "account_id": ACCOUNT_ID},
                )
            except Exception:
                pass
    except Exception:
        pass


# ============================================================
# fills 回写
# ============================================================
def _read_fills(date):
    return _read_json(_fills_path(date)) or {"account_id": ACCOUNT_ID, "date": date, "updated_at": "", "fills": []}


def _write_fills(date, fills_data):
    fills_data["account_id"] = ACCOUNT_ID
    fills_data["date"] = date
    fills_data["updated_at"] = _now_str()
    _atomic_write_json(_fills_path(date), fills_data)


def _add_or_update_fill(date, fill_info):
    """向fills中追加或更新一条记录。同一strategy_order_id只保留最新状态。"""
    data = _read_fills(date)
    existing = None
    for i, f in enumerate(data.get("fills", [])):
        if f.get("strategy_order_id") == fill_info.get("strategy_order_id"):
            existing = i
            break
    if existing is not None:
        data["fills"][existing] = fill_info
    else:
        if "fills" not in data:
            data["fills"] = []
        data["fills"].append(fill_info)
    _write_fills(date, data)


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
        print("[P16G2][PASSORDER-ERR] %s %s: %s" % (action, code, e))
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
    可用性探测语义（三态，T-20261002）：
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
        print("[P16G2][DEAL-API-UNAVAIL] 无 get_trade_detail_data，成交价降级为订单对象价")
        return []
    try:
        deals = f_get(ACCOUNT_ID, "stock", "deal")
    except Exception as e:
        _g_deal_err_count += 1
        if _g_deal_err_count >= DEAL_QUERY_MAX_ERR:
            _g_deal_ok = False
        print("[P16G2][DEAL-API-ERR] get_trade_detail_data 抛异常(%d/%d): %s%s" % (
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
            print("[P16G2][DEAL-NO-OID] 成交明细无任何委托号字段，无法归属本次委托，"
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
    因此这里的取值只作为 deal 明细不可用时的次级降级，且会在 fills 里标注 px_source。"""
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
    返回 (price, px_source, adverse_slip)。price 恒 > 0 时才可被 fills 消费方记账。"""
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
    返回可写入 fills 的告警文本（空串=正常）。
    注意 %-格式化与 * 的优先级：必须写成 "%s" % (adverse * 100)，写成 "%s" % adverse * 100
    会先格式化再重复 100 次（T-20261002 修复）。"""
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
    print("[P16G2][SLIPPAGE-ALERT] %s %s %s px_source=%s ref=%.3f fill=%.3f adverse=%.2f%% gap=%.2f%% | %s" % (
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
    """统一写 fills 收尾记录（成交价协议 + 跳空/滑点留痕的唯一出口）。
    价源优先级：deal明细真实均价(fill_avg) → 订单对象价(order_obj) → 触发/参考价(ref_price)。
    参考价=触发时现价（_resolve_px 传入 info['trigger_price']），滑点基准必须用它而非委托价，
    否则市价单的「委托价=触发价」会把真实跳空亏损算成 0 滑点（掩盖问题）。
    返回写入记录的 dict（调用方可继续打日志）。"""
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
    # 自闭环版（2026-10-20，T-20261020-001）：fills 文件回写已废止（沙箱禁 open，
    # _atomic_write_json 是空壳），若不在此 print，成交记录会**彻底消失**——
    # 外部 sync_*/对账/飞书成交卡全部断供（V13 早已有 [P16V13][FILL]，G2/ENS 漏了）。
    # 格式与 V13 逐字段对齐，外部解析器可复用同一正则。
    print("[P16G2][FILL] %s %s %s %s vol=%d px=%.4f src=%s status=%s ref=%.3f slip=%.2f%% gap=%.2f%% retry=%d oids=%s | %s" % (
        date, sid, rec.get("code", code), action, vol, price, px_source, status,
        ref_price, adverse * 100, gap * 100, rec.get("retry", 0), ",".join(oids), reason))
    _add_or_update_fill(date, rec)
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
                        print("[P16G2][DIAG-ORDER] 匹配订单字段=%s" % diag)
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
            print("[P16G2][CANCEL-NOT-CANCELABLE] %s %s 当前不可撤" % (code, oid))
            return None
    except Exception:
        pass
    try:
        r = cancel(oid, ACCOUNT_ID, "stock", C)
        print("[P16G2][CANCEL] code=%s order_id=%s 返回:%s" % (code, oid, r))
        return r
    except Exception as e:
        print("[P16G2][CANCEL-ERR] code=%s order_id=%s: %s" % (code, oid, e))
        return None


# ============================================================
# 执行单笔指令
# ============================================================
def _execute_order(C, order, date):
    """处理单条买卖指令，记入pending。"""
    global _g_today_order_count
    action = order.get("action", "")
    code = order.get("code", "")
    vol = int(order.get("vol", 0) or 0)
    price = float(order.get("price", 0) or 0)
    strategy_order_id = order.get("strategy_order_id", "")

    # 无效指令检查
    if vol <= 0 or price <= 0:
        print("[P16G2][REJECTED] %s %s vol=%d price=%.3f reason=无效参数" % (action, code, vol, price))
        _add_or_update_fill(date, {
            "strategy_order_id": strategy_order_id,
            "code": code,
            "action": action,
            "vol": vol,
            "price": price,
            "status": "REJECTED",
            "sysid": "",
            "reason": "vol<=0 or price<=0",
            "ts": _now_str(),
        })
        return

    # 代码格式转换
    qmt_code = _to_qmt_code(code)
    if qmt_code is None:
        print("[P16G2][REJECTED] %s %s reason=代码格式无法转换" % (action, code))
        _add_or_update_fill(date, {
            "strategy_order_id": strategy_order_id,
            "code": code,
            "action": action,
            "vol": vol,
            "price": price,
            "status": "REJECTED",
            "sysid": "",
            "reason": "code format invalid: %s" % code,
            "ts": _now_str(),
        })
        return

    # 幂等：已在pending中则跳过
    if strategy_order_id in _g_pending:
        print("[P16G2][SKIP] %s %s 已在pending中" % (action, strategy_order_id))
        return

    # 下单（prtype 可覆盖为 11 指定价，用于测试不可成交单）
    prtype = int(order.get("prtype", PRTYPE_LATEST) or PRTYPE_LATEST)
    ret = _do_passorder(C, action, qmt_code, vol, price, prtype)
    print("[P16G2][ORDER] %s %s %s %d股 @%.3f prtype=%d ret=%s" % (action, strategy_order_id, qmt_code, vol, price, prtype, ret))
    _g_today_order_count += 1
    # 信号内产版：新买入票立即纳入白名单（否则成交后 _check_risk_signals 按
    # 白名单过滤不纳管 -> 止损/止盈永不触发 = 孤儿持仓，实盘红线）。
    if action == "BUY" and qmt_code:
        _g_whitelist.add(qmt_code)

    # 反查确认（同tick内撤单需要 sysid=柜台合同号；m_strOrderSysID 优先，与 _check_pending_orders 一致）
    oid, matched = _lookup_order(C, qmt_code, vol, action, sid=strategy_order_id)
    sysid = ""
    if matched:
        sysid = str(getattr(matched, "m_strSysid", "") or getattr(matched, "m_strOrderSysID", "") or "")

    # 写入pending（vol=目标总量, cur_vol=当前活跃单量, filled_so_far=历史已成交累计）
    _g_pending[strategy_order_id] = {
        "code": qmt_code,
        "bridge_code": code,
        "action": action,
        "vol": vol,
        "cur_vol": vol,
        "filled_so_far": 0,
        "price": price,
        # 滑点基准=下单时参考价（T-20261002）；指令单无持仓成本锚，cost_price=0 表示不参与跳空判定
        "trigger_price": price,
        "cost_price": 0.0,
        "last_price": price,
        "time": time.time(),
        "retry": 0,
        "order_id": oid or "",
        "order_ids": [oid] if oid else [],
        "sysid": sysid,
        "status": "PENDING",
    }


# ============================================================
# 撤单处理
# ============================================================
def _process_cancels(C, cancel_data, date):
    """处理撤单指令：先查deal——已全部成交则撤太迟按FILLED收尾；否则撤单后CANCELED。"""
    if not cancel_data:
        return
    cancels = cancel_data.get("cancels", [])
    for c in cancels:
        sid = c.get("strategy_order_id", "")
        info = _g_pending.get(sid)
        if info is None:
            print("[P16G2][CANCEL-SKIP] %s 不在pending中" % sid)
            continue
        code = info["code"]
        action = info["action"]
        vol = info.get("vol", 0)
        cur_vol = info.get("cur_vol", vol)
        filled_so_far = info.get("filled_so_far", 0)
        oid = info.get("order_id", "")
        deal_vol = 0
        # 无论 order_id 是否已知，先反查订单当前状态（2026-09-01 竞态：模拟盘下单 0.1s 即成交，
        # 撤单指令到达时订单可能已成交 → 必须按 FILLED 收尾，绝不写假 CANCELED）
        _oid, m = _lookup_order(C, code, cur_vol, action, sid=sid, active_only=False)
        if m is not None:
            oid = _extract_order_id(m) or oid
            # 首反查才拿到委托号时必须立即累计，否则 _write_fill 的 deal 查询会遗漏该笔明细
            _acc_order_id(info, oid)
            if not info.get("order_id"):
                info["order_id"] = oid
            deal_vol = _extract_deal_volume(m, fallback_vol=cur_vol)
            st_now = int(getattr(m, "m_nOrderStatus", 0) or 0)
            # 补录 sysid（柜台合同号，cancel() 需要；_execute_order 若未捕获这里兜底）
            _sid_now = str(getattr(m, "m_strSysid", "") or getattr(m, "m_strOrderSysID", "") or info.get("sysid", ""))
            if _sid_now:
                info["sysid"] = _sid_now
            if deal_vol >= cur_vol or st_now == 56:
                total_deal = filled_so_far + deal_vol
                _write_fill(C, date, sid, info, code, action, total_deal, "FILLED",
                            info.get("sysid", ""),
                            info.get("risk_reason", "filled before cancel"),
                            matched=m)
                _g_pending.pop(sid, None)
                print("[P16G2][CANCEL-TOO-LATE] %s 已全部成交 deal=%d" % (sid, total_deal))
                continue
        if oid:
            # 官方撤单 cancel()（调研 QMT撤单重委托方案.md），进入 cancel_requested 确认态
            _cancel_order_by_id(C, code, oid, info.get("sysid", ""))
            info["order_id"] = oid
            info["cancel_requested"] = True
            info["time"] = time.time()
            print("[P16G2][CANCEL-SENT] %s %s 撤单已发 order_id=%s，等待确认" % (sid, code, oid))
            continue
        # 拿不到真实 order_id：绝不假写 CANCELED（否则 QMT 真单变孤儿），保留 pending 延后复查
        print("[P16G2][CANCEL-NOID] %s %s 无法定位order_id，保留pending复查" % (sid, code))
        continue


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
        print("[P16G2][FILLED] %s %s 原单已全成交 deal=%d" % (sid, code, total_deal))
        _write_fill(C, date, sid, info, code, action, total_deal, "FILLED",
                    info.get("sysid", ""),
                    info.get("risk_reason", "filled, original still active"),
                    matched=m2)
        _g_pending.pop(sid, None)
        _g_today_fill_count += 1
        return
    if not dead:
        # 原单未确认死透（仍活跃）→ 延后60s复查，不重报不计数（防双倍持仓）
        print("[P16G2][REJECTED-UNCONFIRMED] %s %s 原单未死透，延后60s复查" % (sid, code))
        info["time"] = now - PENDING_TIMEOUT + 60
        return

    filled_so_far = info.get("filled_so_far", 0) + deal_vol
    info["filled_so_far"] = filled_so_far
    if retry >= MAX_RETRY:
        print("[P16G2][ABANDON] %s %s 废单retry=%d filled=%d" % (sid, code, retry, filled_so_far))
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
    print("[P16G2][REJECTED-RETRY-%d] %s %s %d股 px=%.3f ret=%s" % (
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
    print("[P16G2][TIMEOUT] %s %s elapsed>%ds retry=%d cur_vol=%d filled=%d" % (
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
        print("[P16G2][FILLED] %s %s 撤前已全成交 deal=%d" % (sid, code, total_deal))
        _write_fill(C, date, sid, info, code, action, total_deal, "FILLED",
                    info.get("sysid", ""),
                    info.get("risk_reason", "filled before cancel"),
                    matched=m2)
        _g_pending.pop(sid, None)
        _g_today_fill_count += 1
        return
    if not dead:
        # 撤单未确认死透 → 延后60s复查，不重报不计数（防双倍持仓）
        print("[P16G2][TIMEOUT-UNCONFIRMED] %s 撤单未确认，延后60s复查" % sid)
        info["time"] = now - PENDING_TIMEOUT + 60
        return
    if retry >= MAX_RETRY:
        print("[P16G2][ABANDON] %s %s 超时retry=%d filled=%d" % (sid, code, retry, filled_so_far))
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
    print("[P16G2][RETRY-%d] %s %s %d股 px=%.3f ret=%s" % (
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
    查不到订单→状态未知，写 UNCONFIRMED（外部以 positions 兜底，绝不写假 CANCELED）。"""
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
            print("[P16G2][CANCEL-CONFIRM-FILLED] %s %s deal=%d" % (sid, code, total_deal))
            return
        if st in (53, 54):
            _write_fill(C, date, sid, info, code, action, filled_so_far + dv, "CANCELED",
                        info.get("sysid", ""),
                        info.get("risk_reason", "cancel confirmed"),
                        matched=m)
            _g_pending.pop(sid, None)
            print("[P16G2][CANCEL-CONFIRMED] %s %s status=%d" % (sid, code, st))
            return
        if st == 57:
            _write_fill(C, date, sid, info, code, action, filled_so_far + dv, "REJECTED",
                        info.get("sysid", ""), "cancel rejected status=%d" % st,
                        matched=m)
            _g_pending.pop(sid, None)
            print("[P16G2][CANCEL-REJECTED] %s %s status=%d" % (sid, code, st))
            return
        # 55=部成是活跃态（原单仍在成交），不能当 REJECTED 弹出 pending（假终态=真单孤儿）
        # 仍活跃（2/48/49/50 等非终态）：撤单未生效！保留 pending（fail-loud），90s 后重发最多2次
        print("[P16G2][CANCEL-NOT-EFFECTIVE] %s %s status=%d 撤单未生效，订单仍活跃，保留pending复查" % (sid, code, st))
        if now - info.get("time", now) > 90 and info.get("cancel_retry", 0) < 2:
            info["cancel_retry"] = info.get("cancel_retry", 0) + 1
            info["time"] = now
            _cancel_order_by_id(C, code, _extract_order_id(m) or info.get("order_id", ""), info.get("sysid", ""))
            print("[P16G2][CANCEL-RETRY] %s %s 重发撤单 %d/2" % (sid, code, info["cancel_retry"]))
        return
    # m is None：查不到订单（可能已撤/已成交/列表过期）→ UNCONFIRMED，外部以 positions 兜底
    print("[P16G2][CANCEL-UNKNOWN] %s %s 撤单后订单查不到，状态未知，写UNCONFIRMED（外部以positions兜底）" % (sid, code))
    _write_fill(C, date, sid, info, code, action, filled_so_far, "UNCONFIRMED",
                info.get("sysid", ""),
                "cancel state unknown (may be canceled or filled), rely on positions")
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
                print("[P16G2][PENDING-NO-ORDER] %s %s elapsed=%.0fs" % (sid, code, elapsed))
            continue

        deal_vol = _extract_deal_volume(matched, fallback_vol=cur_vol)
        status = int(getattr(matched, "m_nOrderStatus", 0) or 0)
        sysid = str(getattr(matched, "m_strSysid", "") or getattr(matched, "m_strOrderSysID", "") or info.get("sysid", ""))
        if sysid:
            info["sysid"] = sysid
        # 回填 order_id：首次反查（_execute_order 内）可能因缓存延迟未抓到，状态机内持续补齐，
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
            print("[P16G2][FILLED] %s %s deal=%d" % (sid, code, total_deal))
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
            print("[P16G2][REJECTED] %s %s status=57废单 deal=%d" % (sid, code, deal_vol))
            _handle_rejected_retry(C, date, sid, info, now, deal_vol)
            continue
        # 1c) 撤类终态(53,54) → CANCELED 收尾（vol=total_deal，防漏计最后成交）
        if status in CANCEL_STATUS:
            print("[P16G2][CANCELED-FROM-QMT] %s %s status=%d deal=%d" % (sid, code, status, total_deal))
            _write_fill(C, date, sid, info, code, action, total_deal, "CANCELED",
                        sysid,
                        info.get("risk_reason", "canceled by QMT, status=%d" % status),
                        matched=matched)
            _g_pending.pop(sid, None)
            continue
        # 1d) 有成交未满 → PARTIAL_FILLED 进度回写（filled_so_far不变，deal_vol是累计值）
        # 注意：部成也必须走超时判断（否则停滞单永不撤/永不重报，挂到日终被 NEW-DAY 清掉）
        if deal_vol > 0:
            print("[P16G2][PARTIAL] %s %s deal=%d/%d" % (sid, code, total_deal, vol))
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
        print("[P16G2][PENDING-STATUS] %s %s status=%d deal=%d" % (sid, code, status, deal_vol))


# ============================================================
# 内置实时风控（自 qmt_monitor.py 迁移：止损/止盈/追盈，纯行情驱动）
# ============================================================
def _write_peak():
    """持久化追盈历史最高价（跨日延续，重启不丢）。"""
    try:
        data = {"account_id": ACCOUNT_ID, "updated_at": _now_str(),
                "peaks": {c: round(float(v.get("peak", 0.0) or 0.0), 4) for c, v in _g_positions.items()}}
        _atomic_write_json(_peak_path(), data)
    except Exception:
        pass


def _reload_positions_cfg(date):
    """孤儿持仓自校准（T-20260908，防部署时序缺陷）：
    成本表 positions_cfg_<date>.json 若晚于策略 init 生成，孤儿持仓 cost=0 会被 _check_risk_signals 跳过、
    风控静默失效（peak.json=0.0 即症状）。存在孤儿时重读当日成本表填充 cost/peak。
    节流：距上次读取 <30 秒不重复读文件。"""
    global _g_last_cfg_load
    has_orphan = False
    for info in _g_positions.values():
        if info.get("cost", 0) <= 0:
            has_orphan = True
            break
    if not has_orphan:
        return
    now = time.time()
    if now - _g_last_cfg_load < 30:
        return
    _g_last_cfg_load = now
    cfg = _load_positions_cfg(date)
    if not cfg:
        return
    for p in cfg.get("positions", []):
        try:
            bcode = str(p.get("code", "") or "")
            cost = float(p.get("cost", 0) or 0)
            if not bcode or cost <= 0:
                continue
            qcode = _to_qmt_code(bcode) or bcode
            info = _g_positions.get(qcode)
            if info is not None and info.get("cost", 0) <= 0:
                info["cost"] = cost
                if not info.get("vol"):
                    info["vol"] = int(p.get("vol", 0) or 0)
                if not info.get("peak", 0) or info.get("peak", 0) <= 0:
                    info["peak"] = cost
                print("[P16G2][CFG-RELOAD] %s 孤儿校准 cost=%.4f vol=%d" % (qcode, cost, info.get("vol", 0)))
        except Exception:
            pass


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
                print("[P16G2][POS-NEW] %s 入账 cost=%.4f vol=%d can_use=%d peak=%.4f（白名单内新持仓）" % (
                    code, info["cost"], info["vol"], info["can_use"], peak0))
        for code in list(_g_positions.keys()):
            if code not in acct_pos:
                _g_positions.pop(code, None)
                _g_risk_sold.discard(code)
    except Exception as e:
        print("[P16G2][RISK-POS-ERR] %s" % e)
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
            print("[P16G2][RISK-FAIL] %s %s %s 卖%d股 ret=%s 未记 risk_sold（下轮重试） | %s" % (
                action, sid_pre, code, can_use, ret, note))
            continue
        _g_risk_sold.add(code)
        _g_today_risk_count += 1
        sid = "P16_%s_RSK%04d" % (date, _g_today_risk_count)
        print("[P16G2][RISK] %s %s %s 卖%d股 ret=%s | %s" % (action, sid, code, can_use, ret, note))
        _g_pending[sid] = {
            "code": code,
            "bridge_code": _to_bridge_code(code),
            "action": "SELL",
            "vol": can_use,
            "cur_vol": can_use,
            "filled_so_far": 0,
            "price": last,
            # 滑点/跳空基线（T-20261002）：trigger_price=触发时现价（滑点基准，市价单委托价=触发价，
            # 用委托价会把真实跳空亏损算成 0 滑点从而掩盖问题）；cost_price=含费成本（跳空基准）。
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
        # 立即回写一条 RISK 触发记录，外部可实时感知（后续 FILLED/CANCELED 覆盖同 sid）
        _add_or_update_fill(date, {
            "strategy_order_id": sid,
            "code": _to_bridge_code(code),
            "action": "SELL",
            "vol": can_use,
            "price": last,
            "status": "RISK_%s" % action,
            "sysid": "",
            "reason": note,
            "ts": _now_str(),
        })


# ============================================================
# 持仓/资产快照
# ============================================================
def _refresh_positions(C, date):
    """导出持仓快照（全局函数优先、小写'stock'；m_strInstrumentID纯数字按首位补交易所后缀）。"""
    f_get = globals().get("get_trade_detail_data")
    if f_get is None:
        f_get = getattr(C, "get_trade_detail_data", None)
    if f_get is None:
        return
    try:
        positions = f_get(ACCOUNT_ID, "stock", "position")
        if positions is None:
            # 查询异常（None）：保留旧快照
            return
        result = []
        if not positions:
            # 查询成功但空持仓：写入空快照（防清仓后外部读到旧持仓）
            data = {
                "account_id": ACCOUNT_ID,
                "date": date,
                "positions": [],
                "updated_at": _now_str(),
            }
            _atomic_write_json(os.path.join(STATE_DIR, "positions_%s.json" % date), data)
            return
        for p in positions:
            code = str(getattr(p, "m_strInstrumentID", "") or getattr(p, "m_strSecurityCode", "") or "")
            if not code:
                continue
            if "." not in code:
                if code.startswith("6") or code.startswith("688"):
                    code = "%s.SH" % code
                else:
                    code = "%s.SZ" % code
            vol = int(getattr(p, "m_nVolume", 0) or 0)
            can_use = int(getattr(p, "m_nCanUseVolume", 0) or 0)
            avg_price = float(getattr(p, "m_dOpenPrice", 0) or 0)
            market_value = float(getattr(p, "m_dInstrumentValue", 0) or 0)
            result.append({
                "code": code,
                "volume": vol,
                "can_use_volume": can_use,
                "avg_price": avg_price,
                "market_value": market_value,
            })
        data = {
            "account_id": ACCOUNT_ID,
            "date": date,
            "updated_at": _now_str(),
            "positions": result,
        }
        _atomic_write_json(_positions_path(date), data)
    except Exception as e:
        print("[P16G2][POSITIONS-ERR] %s" % e)


def _refresh_asset(C, date):
    """导出资产快照（全局函数优先、小写'stock'）。"""
    f_get = globals().get("get_trade_detail_data")
    if f_get is None:
        f_get = getattr(C, "get_trade_detail_data", None)
    if f_get is None:
        return
    try:
        accts = f_get(ACCOUNT_ID, "stock", "account")
        if not accts:
            return
        a = accts[0] if isinstance(accts, list) else accts
        total_asset = float(getattr(a, "m_dBalance", 0) or 0)
        cash_available = float(getattr(a, "m_dAvailable", 0) or 0)
        market_value = float(getattr(a, "m_dInstrumentValue", 0) or 0)
        data = {
            "account_id": ACCOUNT_ID,
            "date": date,
            "updated_at": _now_str(),
            "total_asset": total_asset,
            "cash_available": cash_available,
            "market_value": market_value,
        }
        _atomic_write_json(_asset_path(date), data)
    except Exception as e:
        print("[P16G2][ASSET-ERR] %s" % e)


# ============================================================
# 心跳
# ============================================================
def _write_heartbeat(date, cmd_seq):
    """[自闭环] print 心跳（节流60秒，方案 §3.4：沙箱外旁路 grep [HEART] 检测超时告警）。
    替代原 heart json + peak json 文件写；pending 快照不再持久化（重启由当日 order 反查重建）。"""
    global _g_last_heart_ts
    now = time.time()
    if now - _g_last_heart_ts < 60:
        return
    _g_last_heart_ts = now
    print("[P16G2][HEART] %s date=%s pending=%d positions=%d risk_sold=%d risk_count=%d fills=%d slippage=%d passive=%s" % (
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
    print("[P16G2][HOLD] %s n=%d %s" % (
        _now_str(), len(_g_positions), " ".join(parts) if parts else "-"))


# ============================================================
# 读取指令文件
# ============================================================
def _archive_cmd_if_same(path, date, seq, prefix):
    """重读cmd文件，seq仍一致才归档。防归档掉外部新写入的更高seq。"""
    try:
        data = _read_json(path)
        if data is None:
            return
        cur_seq = int(data.get("seq", 0) or 0)
        if cur_seq != seq:
            print("[P16G2][ARCHIVE-SKIP] %s seq变化 %d->%d，不归档" % (prefix, seq, cur_seq))
            return
        _archive_cmd(path, date, seq, prefix)
    except Exception:
        pass


def _process_orders_cmd(C, date):
    """读取并处理orders指令文件。返回已处理的seq(用于归档)。
    先持久化seq到heart再执行（宁可丢单不可重复：防下单后写heart前崩溃→重启重复下单）。"""
    global _g_last_cmd_seq
    path = _orders_cmd_path(date)
    if not os.path.exists(path):
        return 0
    data = _read_json(path)
    if data is None:
        # 文件存在但读取失败：10-14 丢单即此路径（静默吞异常无法定位）
        print("[P16G2][CMD-READ-FAIL] %s exists but read failed" % path)
        _write_cmd_alert(date, "orders", path)
        return 0

    # 账号防呆
    file_acct = str(data.get("account_id", "") or "")
    if file_acct != ACCOUNT_ID:
        print("[P16G2][ACCT-MISMATCH] orders文件account=%s != 期望%s，跳过" % (file_acct, ACCOUNT_ID))
        return 0

    seq = int(data.get("seq", 0) or 0)
    if seq <= _g_last_cmd_seq:
        return 0

    print("[P16G2][CMD] 读取orders seq=%d (上次=%d)" % (seq, _g_last_cmd_seq))
    _g_last_cmd_seq = seq
    _write_heartbeat(date, seq)
    orders = data.get("orders", [])
    for o in orders:
        try:
            _execute_order(C, o, date)
        except Exception:
            print("[P16G2][ORDER-ERR] %s" % traceback.format_exc())

    _archive_cmd_if_same(path, date, seq, "orders")
    return seq


def _process_cancel_cmd(C, date):
    """读取并处理cancel指令文件。返回已处理的seq(用于归档)。"""
    global _g_last_cmd_seq
    path = _cancel_cmd_path(date)
    if not os.path.exists(path):
        return 0
    data = _read_json(path)
    if data is None:
        # 文件存在但读取失败：10-14 丢单即此路径（静默吞异常无法定位）
        print("[P16G2][CMD-READ-FAIL] %s exists but read failed" % path)
        _write_cmd_alert(date, "cancel", path)
        return 0

    file_acct = str(data.get("account_id", "") or "")
    if file_acct != ACCOUNT_ID:
        print("[P16G2][ACCT-MISMATCH] cancel文件account=%s != 期望%s，跳过" % (file_acct, ACCOUNT_ID))
        return 0

    seq = int(data.get("seq", 0) or 0)
    if seq <= _g_last_cmd_seq:
        return 0

    print("[P16G2][CMD] 读取cancel seq=%d (上次=%d)" % (seq, _g_last_cmd_seq))
    _g_last_cmd_seq = seq
    _write_heartbeat(date, seq)
    cancels = data.get("cancels", [])
    cancel_data = {"cancels": cancels}
    try:
        _process_cancels(C, cancel_data, date)
    except Exception:
        print("[P16G2][CANCEL-ERR] %s" % traceback.format_exc())

    _archive_cmd_if_same(path, date, seq, "cancel")
    return seq


# ============================================================
# QMT 生命周期
# ============================================================
def _io_preflight():
    """预检（自闭环接口探针版，2026-10-16）：原「peak/orders 文件可读」探针随文件链路废止。
    实际探针在 _rebuild_state 内（需 C 实例）：position 接口返回 list + G2_CODES 白名单非空。
    任一失败 → 转被动防盲跑（恢复=重启QMT客户端确认登录后重载）。保留本函数供诊断调用。"""
    return True


def _to_qmt_code_any(c):
    """代码归一化：6开头→.SH，否则→.SZ（已有后缀原样）。"""
    if not c:
        return ""
    c = str(c)
    if "." in c:
        return c
    if c.startswith("6") or c.startswith("688"):
        return "%s.SH" % c
    return "%s.SZ" % c


def _rebuild_state(C):
    """init 状态重建四步（任一步异常由 init 捕获转被动，绝不「活跃+状态缺失」盲跑）：
    1) 预检：position 接口可调用且返回 list；
    2) 白名单归一化（G2_CODES；空 = 完全被动）；「白名单 ∩ 账户position」= 纳管集
       （成本锚=m_dOpenPrice 多字段兜底；白名单外持仓绝不触碰——资金红线）；
    3) L3 PEAK_SNAPSHOT 初始化各票追盈峰值（只升不降 + stale 检测 T-20260928-002）；
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
    for c in G2_CODES:
        qc = _to_qmt_code_any(c)
        if qc:
            _g_whitelist.add(qc)
        else:
            print("[P16G2][WARN] 白名单代码无法归一化，已忽略: %r" % (c,))
    # 信号内产版：L3 hold_dates 持票并入白名单（重启后风控必须纳管信号持仓）
    for c in list(_g_hold_dates.keys()):
        qc = _to_qmt_code_any(c)
        if qc:
            _g_whitelist.add(qc)
    if not _g_whitelist:
        _g_passive = True
        print("[P16G2][WARN] G2_CODES 白名单为空且 L3 账本无持票，完全被动模式（不评估不下单）")
        return
    print("[P16G2][INIT] 白名单合并 G2_CODES+L3 hold_dates = %d 只" % len(_g_whitelist))
    # 纳管集 = 白名单 ∩ 账户 position（白名单外绝不纳管）
    managed = 0
    for p in positions:
        code = str(getattr(p, "m_strInstrumentID", "") or getattr(p, "m_strSecurityCode", "") or "")
        if not code:
            continue
        code = _to_qmt_code_any(code)
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
            print("[P16G2][WARN] %s 成本字段缺失(m_dOpenPrice<=0)，该票风控暂跳过待逐tick自愈" % code)
    if managed <= 0:
        print("[P16G2][WARN] 白名单(%d只) ∩ 账户position 交集为空，进入只监控模式"
              "（白名单内持仓出现即自动纳管）" % len(_g_whitelist))
    else:
        print("[P16G2][INIT] 白名单∩position 纳管 %d/%d 只" % (managed, len(_g_whitelist)))
    # 3) L3 追盈峰值快照（只升不降 + stale 检测 T-20260928-002：快照超 PEAK_STALE_HOURS 不恢复）
    _pk_stale = False
    try:
        if PEAK_SNAPSHOT_DATE:
            _snap_dt = datetime.strptime(str(PEAK_SNAPSHOT_DATE), "%Y%m%d")
            _age_h = (datetime.now() - _snap_dt).total_seconds() / 3600.0
            if _age_h > PEAK_STALE_HOURS:
                _pk_stale = True
                print("[P16G2][INIT] PEAK_SNAPSHOT STALE %.1fh ago (>%gh), skip restore "
                      "to avoid retroactive trailing (T-20260928-002)" % (_age_h, PEAK_STALE_HOURS))
    except Exception:
        _pk_stale = True
    if not _pk_stale:
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
    # 4) 当日 order 反查 remark=ORDER_REMARK → 重建 risk_sold + RSK 序号水位
    orders = f_get(ACCOUNT_ID, "stock", "order")
    risk_orders = 0
    if orders:
        for o in orders:
            try:
                remark = str(getattr(o, "m_strRemark", "") or "")
                if ORDER_REMARK not in remark:
                    continue
                ocode = str(getattr(o, "m_strInstrumentID", "") or "")
                if not ocode:
                    continue
                ocode = _to_qmt_code_any(ocode)
                _g_risk_sold.add(ocode)
                risk_orders += 1
            except Exception:
                continue
    if risk_orders > 0:
        _g_today_risk_count = risk_orders
        print("[P16G2][INIT] 当日order反查重建 risk_sold=%d只 RSK水位=%d" % (
            len(_g_risk_sold), _g_today_risk_count))


def init(C):
    """策略初始化（自闭环信号内产版）：预检 + 四步重建（position→白名单→峰值快照→当日order反查）
    + L3 信号账本载入（hold_dates/ENS互斥/资金池/快照新鲜度）。
    任一步异常 → 被动模式（不评估不下单，绝不「活跃+状态缺失」盲跑，2026-10-14 形态禁令）。"""
    global _g_last_cmd_seq, _g_today, _g_risk_sold, _g_today_risk_count, _g_slippage_alert_count, _g_passive
    global _g_hold_dates, _g_oppo_blocked, _g_signal_capital, _g_trade_dates
    try:
        C.set_account(ACCOUNT_ID)
    except Exception:
        pass
    _g_today = _today_str()
    # L3 信号账本（快照内嵌；account_id 戳校验在构建期生成时已完成 T-20260823-004）
    try:
        if str(ACCOUNT_STAMP) != ACCOUNT_ID:
            raise RuntimeError("快照账号戳 %s != 策略 %s" % (ACCOUNT_STAMP, ACCOUNT_ID))
        _g_hold_dates = dict(HOLD_DATES)
        _g_oppo_blocked = set(str(x) for x in OPPONENT_HOLDS)
        _g_signal_capital = float(CAPITAL_SNAP)
        # 快照新鲜度（对齐 T-20260928-002 语义：>14 自然日告警，但不阻断信号——
        # 估值特征陈旧化由误差量化结论接受，仓位保护不受影响）
        _snap_age_d = -1
        try:
            from datetime import datetime as _dtm
            _sd = _dtm.strptime(str(SNAPSHOT_DATE), "%Y%m%d")
            _snap_age_d = (_dtm.now() - _sd).days
        except Exception:
            _snap_age_d = -1
        print("[P16G2][INIT] L3快照 date=%s age=%dd hold_dates=%d ens=%d capital=%.0f" % (
            SNAPSHOT_DATE, _snap_age_d, len(_g_hold_dates), len(_g_oppo_blocked), _g_signal_capital))
        if _snap_age_d >= 0 and _snap_age_d > 14:
            print("[P16G2][INIT][WARN] 静态快照已 %d 天未刷新(>14)，估值特征陈旧——"
                  "请重新构建（gen_g2_signal_snapshots.py + build_p16_g2.py）后粘贴" % _snap_age_d)
    except Exception:
        _g_passive = True
        print("[P16G2][INIT-SNAP-ERR] L3 信号账本载入失败，转被动: %s" % traceback.format_exc())
    # 近期交易日历（持有期计数）：取快照宇宙任一票近 400 根日线索引（一次）
    if _g_trade_dates is None:
        try:
            _any = next(iter(_snap_load().keys()))
            _d = C.get_market_data_ex(stock_code=[_any], period="1d", count=400,
                                       dividend_type="none")
            _df = (_d or {}).get(_any)
            if _df is not None and len(_df) > 10:
                _g_trade_dates = [str(x).replace("-", "")[:8] for x in _df.index]
        except Exception as e:
            print("[P16G2][INIT][WARN] 交易日历取数失败(%s)，持有期回退自然日/4" % e)
    try:
        _rebuild_state(C)
    except Exception:
        _g_passive = True
        print("[P16G2][INIT-REBUILD-ERR] 状态重建异常，转被动模式（不评估不下单），"
              "疑似沙箱/接口异常，请重启客户端并确认交易账号登录成功后重载策略: %s" % traceback.format_exc())
    print("[P16G2][BUILD] BUILD_TAG=%s account=%s mode=自闭环信号内产(零文件) bridge=%s" % (
        BUILD_TAG, ACCOUNT_ID, BRIDGE_DIR))
    print("[P16G2][INIT] date=%s passive=%s last_seq=%d pending=%d positions=%d risk_sold=%d whitelist=%d" % (
        _g_today, _g_passive, _g_last_cmd_seq, len(_g_pending), len(_g_positions), len(_g_risk_sold), len(_g_whitelist)))


# ============================================================
# [信号内产版 s4-5d] 信号引擎（QMT 内部产生，零文件零网络）
#   取数(get_market_data_ex front_ratio) -> 21 特征 -> B21 模型打分 ->
#   简化红线(prob>=0.60) -> TOP_N 动态等权 diff -> 既有订单执行链。
#   对齐外部 rebalance_g2.build_plan 语义（先卖后买 / 到期卖出 / 等权平衡）。
# ============================================================
def _snap_load():
    """构建快照字典（一次）：解 SNAP_BLOB -> 填 _g_snap / _g_st_codes。
    布局见 _g2_signal_snapshots.SNAP_BLOB（构建期生成）：
      keys(9B/票) + pe/pb/dv/adj(int32 定点) + ind(int16) + ST尾块(int32 n + n*9B)"""
    global _g_snap, _g_st_codes
    if _g_snap is not None:
        return _g_snap
    if np is None or pd is None:
        raise RuntimeError("numpy/pandas 不可用")
    import base64 as _b64
    import lzma as _lzma
    n = int(SNAP_N)
    raw = _lzma.decompress(_b64.b64decode("".join(SNAP_BLOB)))
    keys = [raw[i * CODE_LEN:(i + 1) * CODE_LEN].decode("ascii") for i in range(n)]
    off = n * CODE_LEN
    pe = np.frombuffer(raw[off:off + 4 * n], dtype=np.int32); off += 4 * n
    pb = np.frombuffer(raw[off:off + 4 * n], dtype=np.int32); off += 4 * n
    dv = np.frombuffer(raw[off:off + 4 * n], dtype=np.int32); off += 4 * n
    adj = np.frombuffer(raw[off:off + 4 * n], dtype=np.int32); off += 4 * n
    ind = np.frombuffer(raw[off:off + 2 * n], dtype=np.int16); off += 2 * n
    n_st = int(np.frombuffer(raw[off:off + 4], dtype=np.int32)[0]); off += 4
    _st = set()
    for i in range(n_st):
        _st.add(raw[off:off + CODE_LEN].decode("ascii")); off += CODE_LEN
    _g_st_codes = _st
    snap = {}
    for i in range(n):
        snap[keys[i]] = {
            "pe": (np.nan if int(pe[i]) == PE_NAN_Q else float(pe[i]) / PE_SCALE),
            "pb": (np.nan if int(pb[i]) == PB_NAN_Q else float(pb[i]) / PB_SCALE),
            "dv": (np.nan if int(dv[i]) == DV_NAN_Q else float(dv[i]) / DV_SCALE),
            "adj": float(adj[i]) / ADJ_SCALE,
            "ind": int(ind[i]),
        }
    _g_snap = snap
    return snap


def _snap_universe():
    """快照宇宙 code 集合（= 训练过滤帧，横截面特征参与行集）。"""
    return set(_snap_load().keys())


def _build_float_share_map(C):
    """float_share 快照 {code: 万股}（必修③：FloatVolume 单位=股 -> ÷1e4）。
    每次信号前构建（每日一次），逐票取最新流通股本；取不到的票记 0（NaN 处理）。"""
    global _g_fs_map
    fs = {}
    bad = 0
    for code in _snap_load().keys():
        try:
            d = C.get_instrument_detail(code)
            v = float((d or {}).get("FloatVolume", 0) or 0)
        except Exception:
            v = 0.0
        if v <= 0:
            bad += 1
            fs[code] = float("nan")   # 缺股本 -> NaN（circ_mv/换手全 NaN，模型按缺失走，非 0）
        else:
            fs[code] = v / 10000.0
    _g_fs_map = fs
    print("[P16G2][SIG-FS] float_share 快照构建 %d 只（缺 %d）" % (len(fs), bad))
    return fs


def _fetch_bars(C, codes):
    """QMT 日线取数 -> bars dict {code: DataFrame(index=YYYYMMDD,
    columns=[close(adj_close), volume, amount, adj_factor, unadj_close])}。
    ★ 复权口径（探针 2026-10-17 实证）：front_ratio close × 快照 adj_latest
      与训练面板 hfq(close*adj_factor) 相对误差 max=3.6e-16（60 日全对齐）。
    ★ 单位：xtdata amount=元、volume=手（增量库实测与 astock vol 同）。
      astock amount 口径=千元 -> 传 amount_yuan=True 在 build_raw_columns ÷1000。"""
    bars = {}
    codes = list(codes)
    ok = 0
    for i in range(0, len(codes), SIGNAL_BATCH):
        batch = codes[i:i + SIGNAL_BATCH]
        # ★ 签名（QMT xtdata get_market_data_ex）: (field_list, stock_list, period, ...)
        #   field_list 在前！位置传参会错位 -> 必须用 stock_code= 关键字（2026-10-17 核对
        #   xtdata.py:392 签名 + qmt_builder.py:229 现役调用范式）。
        try:
            d = C.get_market_data_ex(stock_code=batch, period="1d",
                                      count=SIGNAL_MIN_BARS,
                                      dividend_type="front_ratio")
        except Exception:
            # 旧签名兜底（fields 第一参变体，ATR/黄氏529 部署为该形态）
            try:
                d = C.get_market_data_ex([], batch, period="1d",
                                         count=SIGNAL_MIN_BARS,
                                         dividend_type="front_ratio")
            except Exception as e:
                print("[P16G2][SIG-MD-ERR] batch=%d %s" % (i, e))
                continue
        if not d:
            continue
        for code, df in d.items():
            try:
                if df is None or len(df) < SIGNAL_MIN_BARS:
                    continue
                idx = df.index
                dates = [str(x).replace("-", "")[:8] for x in idx]
                snap = _snap_load().get(code)
                if not snap:
                    continue
                adj_latest = snap["adj"]
                px = df["close"].astype(float)
                bars[code] = pd.DataFrame({
                    "close": px.values,                      # qfq*adj_latest = 训练 hfq
                    "adj_close": px.values,                  # 同上（模块 docstring 路径1）
                    "unadj": px.values / adj_latest if adj_latest else px.values,
                    "volume": df["volume"].astype(float).values,
                    "amount": df["amount"].astype(float).values,
                    "adj_factor": [adj_latest] * len(px),
                }, index=pd.Index(dates, name="trade_date"))
                ok += 1
            except Exception:
                continue
        if (i // SIGNAL_BATCH) % 5 == 0:
            print("[P16G2][SIG-MD] 进度 %d/%d ok=%d" % (i + len(batch), len(codes), ok))
    print("[P16G2][SIG-MD] 取数完成 ok=%d/%d" % (len(bars), len(codes)))
    return bars


def _compute_scores(C, trade_date):
    """21 特征 + B21 打分，返回按 prob 降序的 [(code, prob), ...]（过滤帧行集）。
    bars 缓存到 _g_signal_bars（plan 阶段低开量比校验用）。"""
    global _g_signal_prob, _g_signal_bars
    snap = _snap_load()
    codes = sorted(snap.keys())
    fs = _g_fs_map if _g_fs_map is not None else _build_float_share_map(C)
    bars = _fetch_bars(C, codes)
    _g_signal_bars = bars
    # 行业 map：IND_POOL + IND_ID 序号 -> {code: 行业名}
    ind_map = {}
    for i, code in enumerate(codes):
        idx = snap[code]["ind"]
        if idx >= 0 and idx < len(IND_POOL):
            ind_map[code] = IND_POOL[idx]
    # snap_map（估值，模块内 assemble 覆盖 pe/pb/dv）
    snap_map = dict((c, {"pe_ttm": v["pe"], "pb": v["pb"], "dv_ttm": v["dv"]})
                    for c, v in snap.items())
    # 白名单/ST（filter_universe）：enabled=过滤帧宇宙（H1 同口径）；ST 快照剔当日新 ST
    # ★ target_date 传 None（取全序列算滚动窗口）后自行取最新日：
    #   09:50 盘中当日 bar 可能尚未生成，写死 trade_date==today 会筛空 -> 信号失败；
    #   取 max(trade_date) 自适应（若当日 bar 已生成即为今日，否则 T-1）。
    st_codes = _g_st_codes or (_snap_load(), _g_st_codes)[1]
    out = build_from_qmt(
        bars, fs, ind_map, snap_map,
        target_codes=None, target_date=None,
        amount_yuan=True, volume_is_lot=True,
        enabled_codes=codes, st_codes=st_codes, align_universe=True)
    if out is None or len(out) == 0:
        raise RuntimeError("特征构建为空（行集过滤/取数失败）")
    out = out[out["trade_date"] == out["trade_date"].max()]
    if len(out) == 0:
        raise RuntimeError("最新交易日无行（取数日对齐失败）")
    X = out[[c for c in FEATURE_ORDER]].astype("float32").values
    prob = predict_proba(X)
    prob = [float(x) for x in prob]
    pairs = sorted(zip(out["ts_code"].values.tolist(), prob),
                   key=lambda t: t[1], reverse=True)
    _g_signal_prob = dict(pairs[:SIGNAL_SELECT_TOP * 4])
    top = pairs[:SIGNAL_SELECT_TOP]
    n_pass = len([p for p in top if p[1] >= SIGNAL_REDLINE_PROB])
    print("[P16G2][SIG-SCORE] rows=%d 红线(>=%.2f)过 %d/%d 首 %.4f 末 %.4f" % (
        len(out), SIGNAL_REDLINE_PROB, n_pass, len(top),
        top[0][1] if top else 0.0, top[-1][1] if top else 0.0))
    return pairs


def _count_hold_days(since, today):
    """since(YYYYMMDD) 到 today 的交易日数（行情日历近似；无日历回退自然日/4）。
    对齐 rebalance_g2._count_trade_days（start 含、end 不含）。"""
    try:
        dates = _g_trade_dates or []
        if len(dates) > 10:
            a = b = None
            for d in dates:
                if a is None and d >= since:
                    a = d
                if d < today:
                    b = d
                elif d >= today:
                    break
            if a is not None:
                i0 = dates.index(a)
                i1 = len(dates)
                for j in range(i0, len(dates)):
                    if dates[j] >= today:
                        i1 = j
                        break
                return max(0, i1 - i0)
        import datetime as _dt
        d0 = _dt.datetime.strptime(str(since), "%Y%m%d").date()
        d1 = _dt.datetime.strptime(str(today), "%Y%m%d").date()
        return max(0, int((d1 - d0).days / 4))
    except Exception:
        return 0


def _g2_tier(C):
    """大盘门控 T 档（复刻 rebalance_g2._calc_tier；数据源换 QMT 内部行情）。
    T=0 停买 / T=1 半仓 / T=2 满仓；取不到 fail-safe T=1 半仓。"""
    tier_err = "行情不足"
    for _sym in SIGNAL_HS300_SYMS:
        try:
            d = C.get_market_data_ex(stock_code=[_sym], period="1d",
                                     count=2, dividend_type="none")
            df = (d or {}).get(_sym)
            if df is None or len(df) < 2:
                raise RuntimeError("%s 行情不足" % _sym)
            last = float(df["close"].iloc[-1])
            prev = float(df["close"].iloc[-2])
            if prev <= 0:
                raise RuntimeError("%s 昨收非正" % _sym)
            pct = (last / prev - 1.0) * 100.0
            break
        except Exception as e:
            tier_err = str(e)
            pct = None
    if pct is None:
        print("[P16G2][SIG-TIER] 沪深300 取数失败(%s)，fail-safe T=1 半仓" % tier_err)
        return 1, None
    if pct <= SIGNAL_TIER_STOP_PCT:
        return 0, pct
    if pct < SIGNAL_TIER_HALF_PCT:
        return 1, pct
    return 2, pct


def _plan_signal(C, date, pairs):
    """复刻 rebalance_g2.build_plan 核心：到期卖出 + 等权平衡（先卖后买）。
    返回 orders（action/code/vol/price/reason/strategy_order_id）。"""
    global _g_signal_capital
    snap = _snap_load()
    # 资金池 = L3 快照 + 当日 position 推算更新（持仓市值 + 期初快照基数口径见 §5.4）
    capital = _g_signal_capital or CAPITAL_SNAP
    # 账户 position（资金池/持仓真相）
    f_get = globals().get("get_trade_detail_data")
    if f_get is None:
        f_get = getattr(C, "get_trade_detail_data", None)
    if f_get is None:
        raise RuntimeError("无 get_trade_detail_data")
    positions = f_get(ACCOUNT_ID, "stock", "position") or []
    acct = {}
    for p in positions:
        code = str(getattr(p, "m_strInstrumentID", "") or "")
        if not code:
            continue
        if "." not in code:
            code = "%s.SH" % code if code.startswith("6") else "%s.SZ" % code
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
        acct[code] = {"vol": vol, "can_use": can_use, "cost": cost}
    # 价格源：tick 现价（兜底快照估值用 bars 近似价）
    tick_codes = list(acct.keys())
    ticks = {}
    try:
        ticks = C.get_full_tick(tick_codes) or {}
    except Exception:
        ticks = {}

    def _price(code):
        t = ticks.get(code) or {}
        try:
            v = float(t.get("lastPrice", 0) or 0)
        except Exception:
            v = 0.0
        return v

    # G2 纳管集 = 持仓 ∩ (G2 白名单 ∪ 既有 hold_dates)——信号版持有账本 = hold_dates
    hd = dict(_g_hold_dates)
    # ---- 卖出：ST 强制 + 持有满 HOLD_DAYS 到期 ----
    orders = []
    sold = set()
    for code in list(acct.keys()):
        if code not in hd:
            continue          # 非 G2 持票（资金红线：绝不碰他票）
        skip_st = False
        try:
            if code in (_g_st_codes or (_snap_load(), _g_st_codes)[1]):
                skip_st = True
            else:
                nm = C.get_stock_name(code) if hasattr(C, "get_stock_name") else ""
                if nm and "ST" in str(nm).upper():
                    skip_st = True
        except Exception:
            pass
        held = _count_hold_days(hd[code], date)
        if skip_st or held >= SIGNAL_HOLD_DAYS:
            vol = min(acct[code]["can_use"] or acct[code]["vol"], acct[code]["vol"])
            if vol <= 0:
                continue
            px = _price(code) or acct[code]["cost"]
            if px <= 0:
                continue
            reason = ("ST强制卖出" if skip_st else "g2持有到期(满%d日)" % SIGNAL_HOLD_DAYS)
            orders.append({"action": "SELL", "code": code, "vol": vol,
                           "price": round(px, 3), "reason": reason,
                           "strategy_order_id": "P16_%s_%04d" % (date, _g_signal_seq + len(orders) + 1)})
            sold.add(code)
    kept = set(hd.keys()) - sold
    # ---- 门控 ----
    tier, pct = _g2_tier(C)
    deploy = 0.0 if tier == 0 else (SIGNAL_HALF_DEPLOY_PCT if tier == 1 else SIGNAL_DEPLOY_PCT)
    print("[P16G2][SIG-TIER] hs300=%s tier=%d deploy=%.2f" % (
        ("%.2f%%" % pct) if pct is not None else "N/A", tier, deploy))
    if tier == 0 or deploy <= 0:
        return orders, {"tier": tier, "buys": 0, "sells": len(orders)}
    # ---- 买入候选：过简化红线 + 排除持仓/ENS ----
    cand = [p for p in pairs
            if p[1] >= SIGNAL_REDLINE_PROB
            and p[0] not in kept and p[0] not in sold
            and p[0] not in _g_oppo_blocked
            and p[0] in snap]
    cand = cand[:SIGNAL_SELECT_TOP]
    n_slots = SIGNAL_TOP_N - len(kept)
    if n_slots <= 0:
        n_slots = 0
    # 动态等权（对齐 rebalance_g2 L545-586）：target = capital*deploy/TOP_N
    target_value = capital * deploy / float(SIGNAL_TOP_N)
    # 超配减仓（kept > target）—— 先于买入
    for code in list(kept):
        if code not in acct:
            continue
        px = _price(code) or acct[code]["cost"]
        if px <= 0:
            continue
        tgt = int(target_value / px / SIGNAL_MIN_VOL) * SIGNAL_MIN_VOL
        diff = acct[code]["vol"] - tgt
        if diff < SIGNAL_MIN_VOL:
            continue
        vol = min(diff, acct[code]["can_use"] or 0)
        if vol < SIGNAL_MIN_VOL:
            continue
        orders.append({"action": "SELL", "code": code, "vol": vol,
                       "price": round(px, 3),
                       "reason": "动态等权超配减仓(目标%.0f)" % target_value,
                       "strategy_order_id": "P16_%s_%04d" % (date, _g_signal_seq + len(orders) + 1)})
    # 新晋建仓
    buys = 0
    for code, prob in cand:
        if buys >= n_slots:
            break
        try:
            d = C.get_market_data_ex(stock_code=[code], period="1d", count=1,
                                      dividend_type="front_ratio")
            df = (d or {}).get(code)
            px = float(df["close"].iloc[-1]) if df is not None and len(df) else 0.0
        except Exception:
            px = 0.0
        if px <= 0:
            px = _price(code)
        if px <= 0:
            print("[P16G2][SIG-BUY-SKIP] %s 取价失败" % code)
            continue
        # 低开校验（复刻 position_filter.gap_guard，数据源=QMT tick）：
        #   open_pct<=-5% 跳过；-5%~-3% 需量比>=2.0 放行；缺数据 fail-open 放行
        try:
            t = ticks.get(code) or {}
            _op = float(t.get("open", 0) or 0)
            _pre = float(t.get("lastClose", 0) or 0)
            if _op > 0 and _pre > 0:
                _opct = (_op / _pre - 1.0) * 100.0
                if _opct <= -5.0:
                    print("[P16G2][SIG-BUY-SKIP] %s 极端低开%.1f%%" % (code, _opct))
                    continue
                if _opct <= -3.0:
                    _v0 = float(t.get("volume", 0) or 0)
                    _b = (_g_signal_bars or {}).get(code)
                    _avg5 = float(_b["volume"].tail(5).mean()) if _b is not None else 0.0
                    _vr = (_v0 / _avg5) if (_avg5 > 0 and _v0 > 0) else None
                    if not (_vr is not None and _vr >= 2.0):
                        print("[P16G2][SIG-BUY-SKIP] %s 低开%.1f%%且量比不足跳过" % (code, _opct))
                        continue
        except Exception:
            pass
        tgt = int(target_value / px / SIGNAL_MIN_VOL) * SIGNAL_MIN_VOL
        if tgt < SIGNAL_MIN_VOL:
            print("[P16G2][SIG-BUY-SKIP] %s 资金不足一手 @%.2f" % (code, px))
            continue
        orders.append({"action": "BUY", "code": code, "vol": tgt,
                       "price": round(px, 3),
                       "reason": "g2内产信号 prob=%.4f" % prob,
                       "strategy_order_id": "P16_%s_%04d" % (date, _g_signal_seq + len(orders) + 1)})
        buys += 1
    return orders, {"tier": tier, "buys": buys, "sells": len(orders),
                    "target_value": round(target_value, 2),
                    "cand": len(cand), "kept": len(kept)}


def _rebuild_signal_watermark(C, date):
    """同日重启防重复：当日 order 反查 remark=ORDER_REMARK，
    按 sid(P16_<date>_<seq>) 重建 _g_signal_seq 水位与 _g_signal_bought。"""
    global _g_signal_seq, _g_signal_bought
    f_get = globals().get("get_trade_detail_data")
    if f_get is None:
        f_get = getattr(C, "get_trade_detail_data", None)
    if f_get is None:
        return
    try:
        orders = f_get(ACCOUNT_ID, "stock", "order") or []
    except Exception:
        return
    max_seq = 0
    for o in orders:
        try:
            uid = str(getattr(o, "m_strUserOrderId", "") or "")
            if not uid.startswith("P16_%s_" % date):
                continue
            tail = uid.split("_")[-1]
            max_seq = max(max_seq, int(tail) if tail.isdigit() else 0)
            ocode = str(getattr(o, "m_strInstrumentID", "") or "")
            if "." not in ocode:
                ocode = "%s.SH" % ocode if ocode.startswith("6") else "%s.SZ" % ocode
            opt = str(getattr(o, "m_strOptName", "") or "")
            if "买入" in opt:
                _g_signal_bought.add(ocode)
        except Exception:
            continue
    _g_signal_seq = max(_g_signal_seq, max_seq)
    if max_seq:
        print("[P16G2][SIG-WATERMARK] 同日重启重建 signal_seq=%d bought=%d" % (
            max_seq, len(_g_signal_bought)))


def _run_signal(C, date):
    """信号入口（时间闸门内调用一次/日）。失败转被动并大声打印。"""
    global _g_signal_done, _g_signal_stage, _g_signal_seq, _g_hold_dates, _g_signal_capital
    if _g_signal_done:
        return
    if _g_passive:
        print("[P16G2][SIG-SKIP] 被动模式，信号不执行")
        _g_signal_done = True
        return
    t0 = time.time()
    _g_signal_stage = "start"
    print("[P16G2][SIG-START] date=%s snap=%s n=%d" % (
        date, SNAPSHOT_DATE, SNAP_N))
    try:
        _rebuild_signal_watermark(C, date)
        _g_signal_stage = "scores"
        pairs = _compute_scores(C, date)
        _g_signal_stage = "plan"
        orders, meta = _plan_signal(C, date, pairs)
        _g_signal_stage = "execute"
        print("[P16G2][SIG-PLAN] %s orders=%d" % (meta, len(orders)))
        for o in orders:
            if o["action"] == "BUY" and o["code"] in _g_signal_bought:
                print("[P16G2][SIG-SKIP-DUP] %s 当日已买，跳过" % o["code"])
                continue
            sid = o.get("strategy_order_id", "")
            _execute_order(C, o, date)
            ok = sid in _g_pending or sid == ""
            if o["action"] == "BUY":
                if ok:
                    # 仅下单入 pending 才记账（防失败单污染已买集合/持有日）
                    _g_signal_bought.add(o["code"])
                    _g_hold_dates.setdefault(o["code"], date)
            else:
                # 到期/ST 全额卖出清持有日；等权超配减仓为部分卖（仍持有），不清
                if "超配减仓" not in o.get("reason", ""):
                    _g_hold_dates.pop(o["code"], None)
            _g_signal_seq += 1
        _g_signal_stage = "done"
        _g_signal_done = True
        print("[P16G2][SIG-DONE] %d 单 耗时 %.1fs" % (len(orders), time.time() - t0))
    except Exception:
        _g_signal_done = True   # 失败也标完成（防同日反复重试放大风险）
        print("[P16G2][SIG-ERR] 信号失败(阶段=%s)，当日信号终止（持仓风控不受影响）: %s" % (
            _g_signal_stage, traceback.format_exc()))


def handlebar(C):
    """每分钟触发：信号闸门→指令→下单→反查→pending→快照→心跳。"""
    global _g_last_cmd_seq, _g_today, _g_last_position_refresh, _g_last_asset_refresh, _g_slippage_alert_count
    # ★ 信号闸门段对 _g_signal_* 有赋值 -> 必须显式声明 global，
    #   否则 Python 判其为局部变量，读取处抛 UnboundLocalError（冒烟实测）。
    global _g_signal_done, _g_signal_stage, _g_signal_prob, _g_signal_bought, _g_signal_seq

    # 被动模式守卫（T-20261015-004）：IO 预检失败后不读指令/不下单/只写心跳，防沙箱下盲跑
    if _g_passive:
        try:
            _write_heartbeat(_g_today, _g_last_cmd_seq)
        except Exception:
            pass
        return

    # 日期切换重置
    today = _today_str()
    if today != _g_today:
        if _g_slippage_alert_count:
            print("[P16G2][SLIPPAGE-DAY-SUMMARY] 前一交易日不利滑点/跳空超限 %d 次" % _g_slippage_alert_count)
        _g_today = today
        _g_last_cmd_seq = 0
        _g_last_position_refresh = 0.0
        _g_last_asset_refresh = 0.0
        _g_pending.clear()
        _g_risk_sold.clear()
        _g_slippage_alert_count = 0   # 告警计数按交易日重置（否则跨日累加，日终无法归因到具体某天）
        _g_signal_done = False        # 信号每日一次（新交易日重置）
        _g_signal_stage = ""
        _g_signal_prob.clear()
        _g_signal_bought.clear()      # 当日已买集合按日清零（防陈旧误跳过）
        print("[P16G2][NEW-DAY] date=%s pending清空 risk_sold清空 slippage_alert清零 signal复位" % today)

    # 交易时段判断：09:30-11:30 或 13:00-15:00
    try:
        qtime = C.get_current_time()
    except Exception:
        qtime = time.strftime("%H:%M:%S")
    hour_min = qtime[:5]
    in_session = ("09:30" <= hour_min <= "11:30") or ("13:00" <= hour_min <= "15:00")

    if not in_session:
        # 2026-09-01 教训：此分支必须 try/except 兜底——心跳写失败若不捕获会崩溃整个策略
        try:
            _write_heartbeat(_g_today, _g_last_cmd_seq)
        except Exception:
            print("[P16G2][HANDLEBAR-ERR-HEART] %s" % traceback.format_exc())
        return

    # 0) 信号闸门（s4-5d 信号内产版）：09:50 触发一次（对齐原 G2 换仓时点），
    #    09:58 后未跑则当日放弃（防盘中误触发）。信号段自身 try/except 全隔离。
    if (not _g_signal_done) and _g_signal_stage == "":
        if hour_min >= SIGNAL_HM and hour_min <= SIGNAL_DEADLINE_HM:
            try:
                _run_signal(C, _g_today)
            except Exception:
                print("[P16G2][HANDLEBAR-ERR-SIG] %s" % traceback.format_exc())
                _g_signal_done = True
        elif hour_min > SIGNAL_DEADLINE_HM:
            _g_signal_done = True   # 过点放弃（当日不追补）
            print("[P16G2][SIG-EXPIRED] 过信号时点(%s)且未执行，当日放弃" % SIGNAL_DEADLINE_HM)

    # 1) pending 状态机（风控卖出委托的成交跟踪/超时重试/撤单重下）
    try:
        _check_pending_orders(C, _g_today)
    except Exception:
        print("[P16G2][HANDLEBAR-ERR-PENDING] %s" % traceback.format_exc())

    # 2) 内置实时风控（止损/止盈/追盈，纯行情驱动）
    try:
        _check_risk_signals(C, _g_today)
    except Exception:
        print("[P16G2][HANDLEBAR-ERR-RISK] %s" % traceback.format_exc())

    # 3) print 心跳（含持仓/资产快照的 print 留痕；原文件快照链路已废止）
    try:
        _write_heartbeat(_g_today, _g_last_cmd_seq)
    except Exception:
        print("[P16G2][HANDLEBAR-ERR-HEART] %s" % traceback.format_exc())


def exit(C):
    """日终摘要。"""
    pending_count = len(_g_pending)
    print("[P16G2][EXIT] date=%s 委托=%d 成交=%d 放弃=%d 最终pending=%d" % (
        _g_today, _g_today_order_count, _g_today_fill_count,
        _g_today_abandon_count, pending_count))
    # 写心跳
    try:
        _write_heartbeat(_g_today, _g_last_cmd_seq)
    except Exception:
        pass
