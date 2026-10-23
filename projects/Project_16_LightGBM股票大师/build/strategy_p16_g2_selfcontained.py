# coding=gbk
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
BUILD_TAG = "20261023-105529"

BRIDGE_DIR = "D:/QMT_POOL/g2_bridge"
CMD_DIR = os.path.join(BRIDGE_DIR, "cmd")
STATE_DIR = os.path.join(BRIDGE_DIR, "state")

ACCOUNT_ID = "67014907"

# passorder 常量（对齐ATR战备模板：prType=5最新价，第8参=remark对账标识，第9参=2，第11参=C）
OP_BUY = 23
OP_SELL = 24
ORDER_TYPE_VOL = 1101
PRTYPE_LATEST = 5
ORDER_REMARK = "P16G2I桥"
# 旧 remark 兼容（2026-10-20 改名，T-20261020-004）：改名前（文件桥版，10-16 前）下的
# 委托 remark 是 "P16G2桥"。同日重启/当日 order 反查若只认新值，会把**改名当天之前**
# 挂出的未成交委托当作「非本策略」→ risk_sold 重建漏项 → 可能重复卖出。
# 故反查/重建一律用 ORDER_REMARK_ALL 做「任一命中」判定；下单只写新值。
ORDER_REMARK_LEGACY = ("P16G2桥",)
ORDER_REMARK_ALL = (ORDER_REMARK,) + ORDER_REMARK_LEGACY

# ★ 2026-10-23 外部下单 remark（T-20261023-002，拍板 F 配套）
# 拍板 F 后信号与下单全部移到外部 Python（rebalance_g2.py），内置侧只做风控。
# 外部用 g2_config.REMARK_BUY / REMARK_SELL 下单，与内置原 remark 完全不同 →
# 若不自查这两个值，内置风控会：
#   ① 白名单永远漏掉外部新买入的票 → 零止损覆盖（T-20261020-005 同型 P0）；
#   ② risk_sold 重建漏项 → 重启后可能重复卖出。
# 解法（对齐官方《QMT内置Python外部读写权限调整说明》§06「通过订单备注建立幂等控制」）：
#   init 时扫「当日 order」，凡 remark 命中 EXTERNAL_REMARK_BUY 的票自动并入白名单。
EXTERNAL_REMARK_BUY = "p16_g2_new"
EXTERNAL_REMARK_SELL = "p16_g2_risk"
EXTERNAL_REMARK_ALL = (EXTERNAL_REMARK_BUY, EXTERNAL_REMARK_SELL)
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

# 内置实时风控参数（自 qmt_monitor.py 迁移，纯行情驱动）
STOP_LOSS_PCT = 0.07
TAKE_PROFIT_PCT = 0.15
TRAILING_PCT = 0.08
TRAIL_ACTIVATE_PCT = 0.08   # 追盈激活阈值（2026-09-07 修复，T-20260907-002）：峰值≥成本×(1+阈值)才追踪，防"追盈=追跌"
PEAK_STALE_HOURS = 24.0     # peak.json 距上次写盘超过此小时数视为 stale，init 跳过恢复（T-20260928-002：防桥宕机多日后拿陈旧 peak 判追盈→追认止损）

# ===== 自闭环纯风控版（2026-10-16，QMT 沙箱 m_nPythonOpFile=1 下零文件读写，方案 §4.2 Step 1）=====
# 持仓白名单：白名单 ∩ 账户 position = 纳管集；白名单外持仓绝不触碰（资金分配红线）。
# 空 = 部署未填 = 完全被动模式（不评估不下单）。
# ★ 2026-10-23（T-20261023-002）：拍板 F 后改为**外部下单 + 内置风控**，
#   白名单不再靠人工硬编码，而是 init 时从「当日 order 的 remark=p16_g2_new」自动反推
#   （见 _rebuild_state 步骤 2.5）。本常量仅保留"历史存量票"用途。
#   2026-10-23 现状：67014907 已清空（V2 清仓后 0 只），故置空。
#   → 若当日有外部买入，会自动并入；若无持仓且无买入，进入"只监控模式"（安全）。
G2_CODES = ()


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
_g_last_bind_try = 0.0   # 被动模式延迟绑定白名单的上次尝试时刻（T-20261023-002，30s 节流）
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
# ★ 2026-10-22 拍板 F：信号改为**外部 Python 算完整评分卡 F1~F6**，本内置策略只保留风控段。
#   SIGNAL_ENABLED=False → handlebar 的信号闸门整段跳过（见 handlebar 0) 段），
#   内置侧仅做 tick 级 STOP/TP/TRAIL + pending 状态机，不选股不下新单。
#   原因：《QMT内置Python外部读写权限调整说明》2026-10-23 全量启用限制后，
#   内置侧拿不到 F2/F5/F6（需外部网络），内产信号只能退化为 prob 单维 → 口径降级。
SIGNAL_ENABLED = False
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

# ---- vendor 注入占位（build_p16_g2_selfcontained.py 构建期替换为文件全文）----
# ======== VENDOR BEGIN: snapshot ========
"""G2 信号内产版静态快照（gen_g2_signal_snapshots.py 自动生成，禁止手改）。
    生成日 20261021  过滤帧 4863 只  账号 70180771
"""
SNAPSHOT_DATE = "20261021"
ACCOUNT_STAMP = "70180771"
SNAP_N = 4863
CODE_LEN = 9
PE_SCALE = 1000.0
PB_SCALE = 1000.0
DV_SCALE = 10000.0
ADJ_SCALE = 10000.0
PE_NAN_Q = -1000
PB_NAN_Q = -1000
DV_NAN_Q = -10000
SNAP_BLOB = (
    "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"
)
IND_POOL = (
    "IT设备",
    "专用机械",
    "中成药",
    "乳制品",
    "互联网",
    "仓储物流",
    "供气供热",
    "保险",
    "元器件",
    "全国地产",
    "公共交通",
    "公路",
    "其他商业",
    "其他建材",
    "农业综合",
    "农用机械",
    "农药化肥",
    "出版业",
    "化学制药",
    "化工原料",
    "化工机械",
    "化纤",
    "区域地产",
    "医疗保健",
    "医药商业",
    "半导体",
    "商品城",
    "商贸代理",
    "啤酒",
    "园区开发",
    "塑料",
    "多元金融",
    "家居用品",
    "家用电器",
    "小金属",
    "工程机械",
    "广告包装",
    "建筑工程",
    "影视音像",
    "房产服务",
    "批发业",
    "摩托车",
    "文教休闲",
    "新型电力",
    "旅游景点",
    "旅游服务",
    "日用化工",
    "普钢",
    "服饰",
    "机场",
    "机床制造",
    "机械基件",
    "林业",
    "染料涂料",
    "橡胶",
    "水力发电",
    "水务",
    "水泥",
    "水运",
    "汽车整车",
    "汽车服务",
    "汽车配件",
    "渔业",
    "港口",
    "火力发电",
    "焦炭加工",
    "煤炭开采",
    "特种钢",
    "环境保护",
    "玻璃",
    "生物制药",
    "电信运营",
    "电器仪表",
    "电器连锁",
    "电气设备",
    "白酒",
    "百货",
    "石油加工",
    "石油开采",
    "石油贸易",
    "矿物制品",
    "种植业",
    "空运",
    "红黄酒",
    "纺织",
    "纺织机械",
    "综合类",
    "航空",
    "船舶",
    "装修装饰",
    "证券",
    "超市连锁",
    "路桥",
    "软件服务",
    "软饮料",
    "轻工机械",
    "运输设备",
    "通信设备",
    "造纸",
    "酒店餐饮",
    "钢加工",
    "铁路",
    "铅锌",
    "铜",
    "铝",
    "银行",
    "陶瓷",
    "食品",
    "饲料",
    "黄金",
)
HOLD_DATES = {
    "301202.SZ": "20261019",
}
OPPONENT_HOLDS = (
    "002762.SZ",
    "002841.SZ",
    "600691.SH",
    "688758.SH",
)
CAPITAL_SNAP = 95245.82
# ======== VENDOR END: snapshot ========
# ======== VENDOR BEGIN: features ========
# =============================================================================
# g2_features_qmt.py  —— G2/ENS 信号内产版 21 特征 QMT 内置计算模块 (s4-5b)
#
# 运行环境: QMT 内置 Python 3.6.8 + numpy + pandas
#          禁止 f-string / 海象运算符 / 泛型下标标注 / match-case
#
# 口径权威（逐字复刻，禁止擅自改口径）:
#   1) build_g2_daily.py  compute_price_features  L39-91   -> 17 个价量/估值特征
#   2) refresh_panel_enh.py  turnover_rank        L107-111/L125
#   3) refresh_panel_enh.py  industry_mom20       L120-121/L129-135
#   4) train_g2_sandbox.py   SANDBOX_B            L45-51    -> 特征装配顺序
#
# 设计分层:
#   [L1] 原始列构造  build_raw_columns()  : QMT 行情 + 静态快照 -> astock 口径原始列
#   [L2] 价量特征    compute_price_features() : 逐字复刻 build_g2_daily L39-91
#   [L3] 增强特征    compute_turnover_rank() / compute_industry_mom20()
#   [L4] 装配        assemble()  : 严格按 FEATURE_ORDER(=SANDBOX_B) 输出 21 列
#   [L5] QMT 入口    build_from_qmt() : 面向沙箱的对外接口
#
# 单位契约（astock / Tushare 口径，务必对齐）:
#   close      : 元（未复权，get_market_data_ex dividend_type='none'）
#   vol        : 手
#   amount     : 千元        <-- xtdata 的成交额是「元」，调用方须先 ÷1000
#   float_share: 万股
#   circ_mv    : 万元        = float_share(万股) * close(元)
#   turnover_rate: %         = vol(手) / float_share(万股)   << 单位恰好相等
#   pe_ttm/pb/dv_ttm : as-is（静态快照，astock 口径原值）
#   adj_factor : 后复权因子（每股全局唯一常数，静态快照 1 个 float 即可永久纠正）
#                训练面板量价特征口径 = 后复权价 hfq = close_未复权 * adj_factor
#                QMT 侧取得方式见 compute_price_features docstring『复权口径』
#   推导: 1 手 / 1 万股 = 100 股 / 10000 股 = 1e-2 -> 数值上恰等于换手率(%)
#
# 行集契约（train-serving skew 头号来源，务必遵守）:
#   模型 B21 的训练行集 = 「过滤帧」= build_features_v2.py L95-103:
#     universe(enabled) -> is_st==0 -> 剔 688*/4*/8* 开头 -> float32 量化
#   横截面特征（turnover_rank / rel_mom_20 的 median / industry_mom20）依赖行集，
#   调用方必须先用 filter_universe() 对齐，否则该三项系统性偏移。
#   残留差异: 训练侧另有 universe_all_a.csv(enabled) 白名单，沙箱内无此清单，
#   由调用方在传入前自行剔除（见 filter_universe docstring）。
# =============================================================================

# ★ QMT 嵌入安全（s4-5d）：顶层 import 必须 try/except（AGENTS「QMT import 必须
#   try/except fallback」红线）。内联进单文件后，顶层裸 import 失败会导致整个策略
#   加载失败（引擎无法隔离捕获）。失败置 None，调用期 AttributeError 由信号引擎
#   的 try/except 捕获 -> 转被动 fail-safe。
try:
    import numpy as np
except Exception:
    np = None
try:
    import pandas as pd
except Exception:
    pd = None

# ---------------------------------------------------------------------------
# 特征顺序（= train_g2_sandbox.SANDBOX_B，字典序即模型输入序，禁止改动）
# ---------------------------------------------------------------------------
FEATURE_ORDER = [
    "vol_ratio_5_20", "amount_ma5", "volume_ratio", "above_ma60", "rsi6",
    "above_ma20", "turn_ma5", "vol20", "mom_20", "rel_mom_20",
    "mom_10", "mom_5", "dist_250_low", "mom_60", "macd_hist",
    "turnover_rank", "industry_mom20",
    "pb", "dv_ttm", "pe_ttm", "log_mv",
]
# ★ 本文件会被 build_p16_g2_selfcontained.py 构入门禁（含禁用词扫描），不得出现断言语句；
#   长度一致性由构建期脚本与离线对齐 harness 校验。
N_FEATURES = len(FEATURE_ORDER)

# 单位换算（QMT 侧调用方按实测 xtdata 单位覆盖）
AMOUNT_YUAN_TO_KYUAN = 0.001   # 成交额 元 -> 千元
VOL_LOT_TO_LOT = 1.0           # 成交量 手 -> 手（xtdata 股票 volume 即为手，待 QMT 实测确认）

# 求一部分特征所需的最少历史 bar 数（dist_250_low 需 250 根）
MIN_HISTORY_BARS = 250


# ---------------------------------------------------------------------------
# [L2] 逐字复刻 build_g2_daily.py L39-91
#   前置条件: df 按 (ts_code, trade_date) 升序排列（同 code 连续）
# ---------------------------------------------------------------------------
def _gshift(s, gkey, n):
    return s.groupby(gkey).shift(n)


def _groll(s, gkey, w, agg, base_index):
    # ★ 必修⑤ QMT pandas 1.x 兼容性：
    #   groupby().rolling().agg() 返回 MultiIndex(ts_code, 原索引)，而原实现里
    #   .rolling() 保留原索引（astock live 用 pandas 2.x）。此处显式
    #   reset_index(level=0, drop=True) 去掉 group key、再 index = base_index 强制对齐，
    #   两版行为一致（唯一依赖：原索引在 groupby 内单调递增，前置条件已保证）。
    #   若 QMT 旧版 pandas 的 rolling 在 groupby 内因缺值/重复索引改变顺序，对齐校验
    #   校验（s4-5e）会暴露差异；此时退化为显式循环实现 _groll_loop（保序保基）。
    r = s.groupby(gkey).rolling(w, min_periods=w).agg(agg)
    r = r.reset_index(level=0, drop=True)
    r.index = base_index
    return r


def compute_price_features(df):
    """输入 df 需含: ts_code/trade_date/close/vol/amount/turnover_rate/
    volume_ratio/circ_mv/pe_ttm/pb/dv_ttm；价格类特征另需 adj_factor 或 adj_close。
    返回 dict of Series（与 df 同行索引）。

    逐字复刻 build_g2_daily.compute_price_features，不含 turnover_rank/industry_mom20。

    ★ 复权口径（2026-08-22 审计修复 R2，2026-10-16 s4-5c 补齐，2026-10-17 s4-5d 定稿 front_ratio）:
      训练面板 (build_features_v2.py L110-114) 的量价特征一律用后复权价
          hfq = close(未复权) * adj_factor        <- adj_factor 为「每股全局唯一常数」
      QMT 侧供给优先级（三源等价，只走 1 或 2）:
        1) df 含 adj_close 列 + adj_factor 列（推荐）
           adj_close 必须来自 C.get_market_data_ex(dividend_type='front_ratio')['close']
             该列 = close * (adj_factor / adj_factor_最新) = 前复权价 qfq
           -> hfq = adj_close * adj_factor(最新静态快照)   ★ 不可再乘未复权 close
        2) df 仅含 adj_factor 列  -> hfq = close(未复权) * adj_factor
        3) 都无 -> 退回未复权 close（仅与 build_g2_daily 旧口径一致，不推荐）
        ★ 禁用 dividend_type='back_ratio': 实测与训练面板 hfq 不同基
        （macd_hist rel_to_std 5.847 >> 对齐判据 0.05），故一律用 front_ratio + 静态 adj_factor。
      唯一对复权基数敏感的特征是 macd_hist（差分量，随基数线性缩放）；
      mom_* / above_ma* / dist_250_low / rsi6 / vol20 / rel_mom_20 均为比值或比率，对逐股常数缩放不变。

    ★ 未复权口径（禁止替换）: circ_mv / log_mv 用未复权 close；industry_mom20 亦用未复权 close。
    """
    gkey = df["ts_code"]
    close_unadj = df["close"].astype(float)          # 未复权（估值口径）
    if "adj_close" in df.columns and "adj_factor" in df.columns:
        # QMT front_ratio 的 close = qfq = close * (C / C_latest);
        # 乘最新静态 adj_factor 还原为训练面板口径 hfq（见 docstring『复权口径』）
        close = df["adj_close"].astype(float) * df["adj_factor"].astype(float)
    elif "adj_close" in df.columns:
        close = df["adj_close"].astype(float)
    elif "adj_factor" in df.columns:
        close = close_unadj * df["adj_factor"].astype(float)
    else:
        close = close_unadj
    idx = df.index
    f = {}

    def groll(s, w, agg="mean"):
        return _groll(s, gkey, w, agg, idx)

    f["mom_5"] = close / _gshift(close, gkey, 5) - 1.0
    f["mom_10"] = close / _gshift(close, gkey, 10) - 1.0
    f["mom_20"] = close / _gshift(close, gkey, 20) - 1.0
    f["mom_60"] = close / _gshift(close, gkey, 60) - 1.0

    high250 = groll(close, 250, "max")
    low250 = groll(close, 250, "min")
    f["pos_250"] = close / high250                    # 不输出，保持与参考实现一致
    f["dist_250_low"] = close / low250 - 1.0

    f["volume_ratio"] = df["volume_ratio"].astype(float)

    vol_ma5 = groll(df["vol"].astype(float), 5)
    vol_ma20 = groll(df["vol"].astype(float), 20)
    f["vol_ratio_5_20"] = vol_ma5 / vol_ma20

    f["amount_ma5"] = np.log1p(groll(df["amount"].astype(float), 5))
    f["turn_ma5"] = groll(df["turnover_rate"].astype(float), 5)

    ma20 = groll(close, 20)
    ma60 = groll(close, 60)
    f["above_ma20"] = close / ma20 - 1.0
    f["above_ma60"] = close / ma60 - 1.0

    delta = close.groupby(gkey).diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.groupby(gkey).transform(lambda s: s.ewm(alpha=1.0 / 6, min_periods=6).mean())
    avg_loss = loss.groupby(gkey).transform(lambda s: s.ewm(alpha=1.0 / 6, min_periods=6).mean())
    f["rsi6"] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss.replace(0.0, np.nan))

    ema12 = close.groupby(gkey).transform(lambda s: s.ewm(span=12, adjust=False).mean())
    ema26 = close.groupby(gkey).transform(lambda s: s.ewm(span=26, adjust=False).mean())
    dif = ema12 - ema26
    dea = dif.groupby(gkey).transform(lambda s: s.ewm(span=9, adjust=False).mean())
    f["macd_hist"] = (dif - dea) * 2.0

    ret1 = close / _gshift(close, gkey, 1) - 1.0
    f["vol20"] = groll(ret1, 20, "std")

    f["log_mv"] = np.log(df["circ_mv"].astype(float))
    f["pe_ttm"] = df["pe_ttm"].astype(float)
    f["pb"] = df["pb"].astype(float)
    f["dv_ttm"] = df["dv_ttm"].astype(float)

    mkt_mom20 = f["mom_20"].groupby(df["trade_date"]).transform("median")
    f["rel_mom_20"] = f["mom_20"] - mkt_mom20
    return f


# ---------------------------------------------------------------------------
# [L3] 增强特征（refresh_panel_enh.py 口径）
# ---------------------------------------------------------------------------
def compute_turnover_rank(df, turnover_col="true_turnover"):
    """全市场换手横截面排名（pct rank, method='average'）。

    参考: refresh_panel_enh.py L125  mg.groupby("trade_date")["true_turnover"].rank(pct=True)
    注: rank 对全局单调缩放不变 -> 只要 vol 与 float_share 单位一致即可。
    """
    return df.groupby("trade_date")[turnover_col].rank(pct=True)


def compute_industry_mom20(df, industry_map):
    """行业 20 日动量（stock_basic.industry 映射 + 行业等权均值）。

    参考: refresh_panel_enh.py L120-121/L129-135
      mom20 = close / close.groupby(ts_code).shift(20) - 1.0   （未复权 close）
      ind_mom = mg.dropna(subset=[industry, mom20])
                  .groupby([trade_date, industry])["mom20"].mean()
    注意: 行业分类来源 = stock_basic.industry（Tushare 行业），非 881 / 非同花顺。
    """
    gkey = df["ts_code"]
    close = df["close"].astype(float)
    mom20 = close / _gshift(close, gkey, 20) - 1.0
    ind = df["ts_code"].map(industry_map)
    tmp = pd.DataFrame({
        "trade_date": df["trade_date"].values,
        "industry": ind.values,
        "mom20": mom20.values,
    })
    tmp = tmp.dropna(subset=["industry", "mom20"])
    ind_mom = tmp.groupby(["trade_date", "industry"])["mom20"].mean().rename("industry_mom20")
    ind_mom = ind_mom.reset_index()
    merged = pd.DataFrame({"trade_date": df["trade_date"].values, "industry": ind.values})
    merged["_row"] = np.arange(len(df))
    out = merged.merge(ind_mom, on=["trade_date", "industry"], how="left")
    out = out.sort_values("_row")
    return pd.Series(out["industry_mom20"].values, index=df.index)


# ---------------------------------------------------------------------------
# [L4] 装配：严格按 FEATURE_ORDER 输出 21 列
# ---------------------------------------------------------------------------
def assemble(df, industry_map, snap_map=None):
    """df 需已含 astock 口径原始列 + true_turnover 列；返回仅含 21 特征的 DataFrame。

    snap_map: {ts_code: {'pb':.., 'dv_ttm':.., 'pe_ttm':..}}，None 时用 df 内列。
    """
    work = df.copy()
    work = work.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    f = compute_price_features(work)
    f["turnover_rank"] = compute_turnover_rank(work)
    f["industry_mom20"] = compute_industry_mom20(work, industry_map)

    # 静态快照优先覆盖 pb/dv_ttm/pe_ttm（QMT 内无原生估值字段）
    if snap_map is not None:
        for col in ("pb", "dv_ttm", "pe_ttm"):
            vals = work["ts_code"].map(lambda c: (snap_map.get(c) or {}).get(col, np.nan))
            f[col] = vals.astype(float)

    out = pd.DataFrame(index=work.index)
    out["ts_code"] = work["ts_code"].values
    out["trade_date"] = work["trade_date"].values
    for name in FEATURE_ORDER:
        out[name] = np.asarray(f[name], dtype=np.float64)
    return out


# ---------------------------------------------------------------------------
# [L1] 原始列构造：QMT 行情 + 静态快照 -> astock 口径原始列
# ---------------------------------------------------------------------------
def build_raw_columns(bars, float_share_map, snap_map,
                      amount_yuan=True, volume_is_lot=True):
    """把 QMT 行情拼成长表并推导 astock 口径原始列。

    bars: {ts_code: DataFrame(index=trade_date 'YYYYMMDD',
                              columns=['close','volume','amount', (可选)'adj_factor'])}
          未复权 close；若提供 adj_factor 则价量特征走复权口径
    float_share_map: {ts_code: 万股}
      ★ 必修③ 来源契约（沙箱内唯一可取路径，2026-10-17 实测）:
        float_share_map = {code: C.get_instrument_detail(code)['FloatVolume'] / 1e4}
        —— get_instrument_detail 的 FloatVolume 单位是「股」
           （实测 600000.SH = 33305838300.0 股 -> 3330583.83 万股），
           而本模块单位契约要求「万股」，故须 ÷1e4。
        ★ 该映射是「静态快照」：QMT 无逐日股数接口（get_market_data_ex 的 fields
           参数被忽略、恒回 ['open','high','low','close','volume','amount',
           'settelementPrice','openInterest','preClose','suspendFlag']，
           total_share/float_share/circ_mv 均不可得）。
        ★ 已知系统误差（沙箱固有口径，需在验收中量化）: 股本并非常量，送转/增发/
           回购发生后 float_share 会跳变，静态快照会使 true_turnover(turnover_rate)
           与 circ_mv 在该变动日之后整体偏移（乘性常数）。影响面: turn_ma5 /
           turnover_rank / log_mv / （经由排名）三类；mom_* / rsi6 / vol20 /
           above_ma* / dist_250_low / macd_hist / volume_ratio / vol_ratio_5_20 /
           amount_ma5 不受影响（纯价量比值）。
           缓解: 每次换仓重建快照即可（策略换仓频率 2 月级，与快照刷新同频）。
    snap_map: {ts_code: {'pb','dv_ttm','pe_ttm'}}
      ★ 同上为静态快照，来源为外部管道落盘的最近一期估值（astock 口径原值，
        不做 PIT 还原）；覆盖率不足的 code 记为 NaN，由下层模型按缺失处理。
    返回: 长表 df（含 ts_code/trade_date/close/vol/amount/turnover_rate/
          volume_ratio/circ_mv/pe_ttm/pb/dv_ttm/true_turnover[, adj_factor]）
    ★ 复权: bars 含 adj_factor 时原样透传，供 compute_price_features 复用
      （训练面板量价特征口径 = close * adj_factor，见 build_features_v2 L110-114）
    """
    frames = []
    for code, d in bars.items():
        if d is None or len(d) == 0:
            continue
        t = d.reset_index()
        t.columns = ["trade_date"] + list(d.columns)
        t["ts_code"] = code
        frames.append(t)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["ts_code"] = df["ts_code"].astype(str)
    df["trade_date"] = df["trade_date"].astype(str).str.slice(0, 8)
    df["close"] = df["close"].astype(float)
    df["vol"] = df["volume"].astype(float)
    df["amount"] = df["amount"].astype(float)
    # 复权透传：训练面板量价特征口径 = close * adj_factor（build_features_v2 L110-114）
    for _adjc in ("adj_factor", "adj_close"):
        if _adjc in df.columns:
            df[_adjc] = df[_adjc].astype(float)
    if amount_yuan:
        df["amount"] = df["amount"] * AMOUNT_YUAN_TO_KYUAN
    if not volume_is_lot:
        df["vol"] = df["vol"] * VOL_LOT_TO_LOT

    fs = df["ts_code"].map(lambda c: float_share_map.get(c, np.nan)).astype(float)
    df["true_turnover"] = df["vol"] / fs.replace(0.0, np.nan)
    df["turnover_rate"] = df["true_turnover"]          # 主库区即原生换手率，量纲一致
    df["circ_mv"] = fs * df["close"]                   # 万股 * 元 = 万元

    # volume_ratio（量比）: astock 原生口径为「多日均量加权」，无闭式精确复刻。
    #   2026-10-17 探针实证（2024-07-01~2026-10-16，n≈3.0e6，对照列=merged_daily_full.volume_ratio）:
    #     v1 = vol_t / mean(vol_{t-1..t-5})  ->  pear=0.9891 spear=0.9857
    #                                           round(…,2) 与原生值相等命中率 98.14%
    #     其他候选(prev6/incl5/incl6/ewm5)均更差（pear≤0.92）；
    #     残差尾部全为停牌/ST 极端行（vol=0 时原生写 25.67 等哨兵值，long 期间无成交）。
    #   故保留本近似式；round 到 2 位可进一步贴合原生发布精度（下游 Z-score 排序等价）。
    #   若后续 QMT 内能取到原生 volume_ratio 列，则直接透传（见 compute_price_features L135 优先取 df 列）。
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    g = df.groupby("ts_code")["vol"]
    prev5 = g.transform(lambda s: s.shift(1).rolling(5, min_periods=5).mean())
    df["volume_ratio"] = df["vol"] / prev5.replace(0.0, np.nan)

    for col in ("pe_ttm", "pb", "dv_ttm"):
        df[col] = df["ts_code"].map(lambda c: (snap_map.get(c) or {}).get(col, np.nan)).astype(float)
    return df


# ---------------------------------------------------------------------------
# [L1b] 行集对齐门（必修⑥）：逐字复刻 build_features_v2.py L95-103
# ---------------------------------------------------------------------------
def filter_universe(df, enabled_codes=None, st_codes=None, quantize=True):
    """把长表裁到与模型 B21 训练行集一致的行集合（train-serving skew 头号防线）。

    逐字复刻 build_features_v2.py L95-103 的过滤链，顺序固定不可调换：
        1) universe(enabled) 白名单 isin   （universe_all_a.csv 中 enabled==True）
        2) is_st == 0
        3) 剔除 688* / 4* / 8* 前缀（科创板 + 北交所/新三板）
        4) float64 列 -> float32 量化（训练侧 L101-103 同款，保证数值位一致）
        5) 按 (ts_code, trade_date) 升序稳定排序

    参数:
      df           : 长表（需含 ts_code / trade_date 列）
      enabled_codes: 白名单 code 集合；None = 不过滤
                     ★ 沙箱内无 universe_all_a.csv，须由构建期生成静态快照
                       （build_p16_g2_selfcontained.py 内联字面量）后经 init 传入
      st_codes     : 当日 ST/*ST code 集合；None = 若 df 无 is_st 列则跳过该步
                     ★ 沙箱内无逐行 is_st（QMT get_market_data_ex 无该字段），
                       同样须构建期静态快照（astock 最近交易日 is_st==1）
      quantize     : 是否做 float32 量化；离线对齐 harness 置 False 可分离纯口径差

    返回: 过滤后的新 DataFrame（reset_index）

    ★ 调用时机: 必须在「原始列就绪 + 特征计算之前」；
      横截面特征（turnover_rank / rel_mom_20 的 median / industry_mom20）依赖参与行集合，
      若在全量上算再裁行，训练侧会系统性偏移。
    """
    if len(df) == 0:
        return df
    out = df
    if enabled_codes is not None:
        want = set([str(c) for c in enabled_codes])
        out = out[out["ts_code"].isin(want)]
    if "is_st" in out.columns:
        out = out[out["is_st"].astype(float) == 0]
    elif st_codes is not None:
        st = set([str(c) for c in st_codes])
        out = out[~out["ts_code"].isin(st)]
    codes = out["ts_code"]
    out = out[~codes.str.startswith("688") & ~codes.str.startswith(("4", "8"))]
    out = out.copy()
    if quantize:
        for c in out.columns:
            if out[c].dtype == "float64":
                out[c] = out[c].astype("float32")
    out = out.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# [L5] QMT 沙箱对外入口
# ---------------------------------------------------------------------------
def build_from_qmt(bars, float_share_map, industry_map, snap_map,
                   target_codes=None, target_date=None,
                   amount_yuan=True, volume_is_lot=True,
                   enabled_codes=None, st_codes=None, align_universe=True):
    """沙箱内一步到位：QMT 行情 -> 21 特征长表。

    target_codes: 候选池；None = 全市场（横截面特征需全市场参与计算）
    target_date : 'YYYYMMDD'；None = 全市场最新交易日
    align_universe: True  = 先过 filter_universe（必修⑥，对齐 B21 训练行集，生产默认）
                    False = 保留未过滤全量（仅用于 train-serving skew 对照实验）
    enabled_codes/st_codes: 见 filter_universe docstring（构建期静态快照）
    返回: DataFrame[ts_code, trade_date] + FEATURE_ORDER(21)
    """
    raw = build_raw_columns(bars, float_share_map, snap_map,
                            amount_yuan=amount_yuan, volume_is_lot=volume_is_lot)
    if len(raw) == 0:
        return raw
    if align_universe:
        raw = filter_universe(raw, enabled_codes=enabled_codes, st_codes=st_codes)
        if len(raw) == 0:
            return raw
    full = assemble(raw, industry_map, snap_map=snap_map)
    if target_date is not None:
        full = full[full["trade_date"] == str(target_date)]
    if target_codes is not None:
        want = set([str(c) for c in target_codes])
        full = full[full["ts_code"].isin(want)]
    return full.reset_index(drop=True)


def cross_section_ready(history_len, need=None):
    """历史 bar 是否足够（dist_250_low 需 250 根）。"""
    n = MIN_HISTORY_BARS if need is None else int(need)
    return int(history_len) >= n
# ======== VENDOR END: features ========
# ======== VENDOR BEGIN: model ========
# AUTO-GENERATED by dump_lgb_to_py.py -- DO NOT EDIT.
# source_model : D:/Temp/opencode/g2_sandbox_B21.txt
# generated_at : 2026-10-17 06:16:28
# trees=1500 nodes=74610 features=21 enc=lzma
# NOTE: pure-python (numpy only) LightGBM binary-objective predictor.
# NOTE: Python 3.6 compatible.
import base64
import struct

# ★ QMT 嵌入安全（s4-5d）：顶层 import 必须 try/except（AGENTS 红线），
#   失败置 None，predict 调用期由信号引擎 try/except 捕获 -> 转被动。
try:
    import numpy as np
except Exception:
    np = None

try:
    import zlib as _zlib
except Exception:
    _zlib = None
try:
    import lzma as _lzma
except Exception:
    _lzma = None

_ENC = "lzma"
_INIT_SCORE = -3.6313144713417955e-09
_SIGMOID = 1.0
_N_FEATURES = 21
_BLOB = (
    "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"
)


_MISS_ZERO = 1
_MISS_NAN = 2


def _decompress():
    raw = base64.b64decode(_BLOB)
    if _ENC == "lzma":
        if _lzma is None:
            raise RuntimeError("lzma module unavailable in this interpreter")
        return _lzma.decompress(raw)
    if _zlib is None:
        raise RuntimeError("zlib module unavailable in this interpreter")
    return _zlib.decompress(raw)


def _load():
    blob = _decompress()
    nodelen = struct.unpack("<8i", blob[:32])
    off = 32
    dts = [np.int32, np.int8, np.float32, np.uint8, np.int16, np.int16,
           np.float32, np.uint8]
    out = []
    for cnt, dt in zip(nodelen, dts):
        sz = np.dtype(dt).itemsize
        out.append(np.frombuffer(blob[off:off + cnt * sz], dtype=dt).copy())
        off += cnt * sz
    return out


# ★ QMT 嵌入安全（s4-5d）：原模块级 _load() 在 import 期解压 lzma，
#   若 QMT 内置 Python 缺 lzma 模块会 import 即崩（策略加载失败）。
#   改为惰性加载：首次 predict 时才解压，缺模块则抛可捕获异常（上层转被动）。
_OFFS = None
_FEAT = None
_THR = None
_DLEFT = None
_LEFT = None
_RIGHT = None
_LEAFVAL = None
_MISS = None
_N_TREES = 0
_MODEL_ERR = None


def _ensure_loaded():
    global _OFFS, _FEAT, _THR, _DLEFT, _LEFT, _RIGHT, _LEAFVAL, _MISS, _N_TREES, _MODEL_ERR
    if _OFFS is not None:
        return
    try:
        (_OFFS, _FEAT, _THR, _DLEFT, _LEFT,
         _RIGHT, _LEAFVAL, _MISS) = _load()
        _N_TREES = int(_OFFS.shape[0]) - 1
    except Exception as e:
        _MODEL_ERR = e
        raise


def predict_raw(X):
    _ensure_loaded()
    X = np.asarray(X, dtype=np.float64)
    if X.ndim == 1:
        X = X.reshape(1, -1)
    if X.shape[1] != _N_FEATURES:
        raise ValueError("expect %d features, got %d" % (_N_FEATURES, X.shape[1]))
    n = X.shape[0]
    rows = np.arange(n)
    raw = np.zeros(n, dtype=np.float64)
    for t in range(_N_TREES):
        base = int(_OFFS[t])
        node = np.full(n, base, dtype=np.int32)
        for _ in range(128):
            f = _FEAT[node]
            is_leaf = f < 0
            if bool(is_leaf.all()):
                break
            x = X[rows, f]
            nan = np.isnan(x)
            m = _MISS[node]
            # LightGBM NumericalDecision: NaN -> 0 when missing_type != NaN
            x0 = np.where(nan & (m != _MISS_NAN), np.float32(0.0), x)
            ul = x0 <= _THR[node]
            # Zero type & value==0 (incl. converted NaN) OR NaN type & NaN -> default
            go_default = ((m == _MISS_ZERO) & (x0 == np.float32(0.0))) |                 (nan & (m == _MISS_NAN))
            ul = np.where(go_default, _DLEFT[node] == 1, ul)
            nxt = np.where(ul, _LEFT[node].astype(np.int32) + base,
                           _RIGHT[node].astype(np.int32) + base)
            node = np.where(is_leaf, node, nxt)
        raw += _LEAFVAL[node].astype(np.float64)
    return raw + _INIT_SCORE


def predict_proba(X):
    raw = predict_raw(X) * _SIGMOID
    return 0.5 * (1.0 + np.tanh(0.5 * raw))


def n_trees():
    return _N_TREES


def feature_count():
    return _N_FEATURES
# ======== VENDOR END: model ========


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
    print("[P16G2I][ALERT] kind=%s path=%s (文件落盘已废止，走print通道)" % (kind, path))


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
            print("[P16G2I][CFG-FALLBACK] 当日成本表缺失，回退最近表 date=%s（持仓未变时成本仍有效）"
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
            print("[P16G2I][STALE-CMD] 未归档命令文件: %s" % full)
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
        print("[P16G2I][PASSORDER-ERR] %s %s: %s" % (action, code, e))
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
        print("[P16G2I][DEAL-API-UNAVAIL] 无 get_trade_detail_data，成交价降级为订单对象价")
        return []
    try:
        deals = f_get(ACCOUNT_ID, "stock", "deal")
    except Exception as e:
        _g_deal_err_count += 1
        if _g_deal_err_count >= DEAL_QUERY_MAX_ERR:
            _g_deal_ok = False
        print("[P16G2I][DEAL-API-ERR] get_trade_detail_data 抛异常(%d/%d): %s%s" % (
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
            print("[P16G2I][DEAL-NO-OID] 成交明细无任何委托号字段，无法归属本次委托，"
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
    print("[P16G2I][SLIPPAGE-ALERT] %s %s %s px_source=%s ref=%.3f fill=%.3f adverse=%.2f%% gap=%.2f%% | %s" % (
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
    print("[P16G2I][FILL] %s %s %s %s vol=%d px=%.4f src=%s status=%s ref=%.3f slip=%.2f%% gap=%.2f%% retry=%d oids=%s | %s" % (
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
                        1 if any(rk in remark for rk in ORDER_REMARK_ALL) else 0,
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
                        print("[P16G2I][DIAG-ORDER] 匹配订单字段=%s" % diag)
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
            print("[P16G2I][CANCEL-NOT-CANCELABLE] %s %s 当前不可撤" % (code, oid))
            return None
    except Exception:
        pass
    try:
        r = cancel(oid, ACCOUNT_ID, "stock", C)
        print("[P16G2I][CANCEL] code=%s order_id=%s 返回:%s" % (code, oid, r))
        return r
    except Exception as e:
        print("[P16G2I][CANCEL-ERR] code=%s order_id=%s: %s" % (code, oid, e))
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
        print("[P16G2I][REJECTED] %s %s vol=%d price=%.3f reason=无效参数" % (action, code, vol, price))
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
        print("[P16G2I][REJECTED] %s %s reason=代码格式无法转换" % (action, code))
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
        print("[P16G2I][SKIP] %s %s 已在pending中" % (action, strategy_order_id))
        return

    # 下单（prtype 可覆盖为 11 指定价，用于测试不可成交单）
    prtype = int(order.get("prtype", PRTYPE_LATEST) or PRTYPE_LATEST)
    ret = _do_passorder(C, action, qmt_code, vol, price, prtype)
    print("[P16G2I][ORDER] %s %s %s %d股 @%.3f prtype=%d ret=%s" % (action, strategy_order_id, qmt_code, vol, price, prtype, ret))
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
            print("[P16G2I][CANCEL-SKIP] %s 不在pending中" % sid)
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
                print("[P16G2I][CANCEL-TOO-LATE] %s 已全部成交 deal=%d" % (sid, total_deal))
                continue
        if oid:
            # 官方撤单 cancel()（调研 QMT撤单重委托方案.md），进入 cancel_requested 确认态
            _cancel_order_by_id(C, code, oid, info.get("sysid", ""))
            info["order_id"] = oid
            info["cancel_requested"] = True
            info["time"] = time.time()
            print("[P16G2I][CANCEL-SENT] %s %s 撤单已发 order_id=%s，等待确认" % (sid, code, oid))
            continue
        # 拿不到真实 order_id：绝不假写 CANCELED（否则 QMT 真单变孤儿），保留 pending 延后复查
        print("[P16G2I][CANCEL-NOID] %s %s 无法定位order_id，保留pending复查" % (sid, code))
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
        print("[P16G2I][FILLED] %s %s 原单已全成交 deal=%d" % (sid, code, total_deal))
        _write_fill(C, date, sid, info, code, action, total_deal, "FILLED",
                    info.get("sysid", ""),
                    info.get("risk_reason", "filled, original still active"),
                    matched=m2)
        _g_pending.pop(sid, None)
        _g_today_fill_count += 1
        return
    if not dead:
        # 原单未确认死透（仍活跃）→ 延后60s复查，不重报不计数（防双倍持仓）
        print("[P16G2I][REJECTED-UNCONFIRMED] %s %s 原单未死透，延后60s复查" % (sid, code))
        info["time"] = now - PENDING_TIMEOUT + 60
        return

    filled_so_far = info.get("filled_so_far", 0) + deal_vol
    info["filled_so_far"] = filled_so_far
    if retry >= MAX_RETRY:
        print("[P16G2I][ABANDON] %s %s 废单retry=%d filled=%d" % (sid, code, retry, filled_so_far))
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
    print("[P16G2I][REJECTED-RETRY-%d] %s %s %d股 px=%.3f ret=%s" % (
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
    print("[P16G2I][TIMEOUT] %s %s elapsed>%ds retry=%d cur_vol=%d filled=%d" % (
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
        print("[P16G2I][FILLED] %s %s 撤前已全成交 deal=%d" % (sid, code, total_deal))
        _write_fill(C, date, sid, info, code, action, total_deal, "FILLED",
                    info.get("sysid", ""),
                    info.get("risk_reason", "filled before cancel"),
                    matched=m2)
        _g_pending.pop(sid, None)
        _g_today_fill_count += 1
        return
    if not dead:
        # 撤单未确认死透 → 延后60s复查，不重报不计数（防双倍持仓）
        print("[P16G2I][TIMEOUT-UNCONFIRMED] %s 撤单未确认，延后60s复查" % sid)
        info["time"] = now - PENDING_TIMEOUT + 60
        return
    if retry >= MAX_RETRY:
        print("[P16G2I][ABANDON] %s %s 超时retry=%d filled=%d" % (sid, code, retry, filled_so_far))
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
    print("[P16G2I][RETRY-%d] %s %s %d股 px=%.3f ret=%s" % (
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
            print("[P16G2I][CANCEL-CONFIRM-FILLED] %s %s deal=%d" % (sid, code, total_deal))
            return
        if st in (53, 54):
            _write_fill(C, date, sid, info, code, action, filled_so_far + dv, "CANCELED",
                        info.get("sysid", ""),
                        info.get("risk_reason", "cancel confirmed"),
                        matched=m)
            _g_pending.pop(sid, None)
            print("[P16G2I][CANCEL-CONFIRMED] %s %s status=%d" % (sid, code, st))
            return
        if st == 57:
            _write_fill(C, date, sid, info, code, action, filled_so_far + dv, "REJECTED",
                        info.get("sysid", ""), "cancel rejected status=%d" % st,
                        matched=m)
            _g_pending.pop(sid, None)
            print("[P16G2I][CANCEL-REJECTED] %s %s status=%d" % (sid, code, st))
            return
        # 55=部成是活跃态（原单仍在成交），不能当 REJECTED 弹出 pending（假终态=真单孤儿）
        # 仍活跃（2/48/49/50 等非终态）：撤单未生效！保留 pending（fail-loud），90s 后重发最多2次
        print("[P16G2I][CANCEL-NOT-EFFECTIVE] %s %s status=%d 撤单未生效，订单仍活跃，保留pending复查" % (sid, code, st))
        if now - info.get("time", now) > 90 and info.get("cancel_retry", 0) < 2:
            info["cancel_retry"] = info.get("cancel_retry", 0) + 1
            info["time"] = now
            _cancel_order_by_id(C, code, _extract_order_id(m) or info.get("order_id", ""), info.get("sysid", ""))
            print("[P16G2I][CANCEL-RETRY] %s %s 重发撤单 %d/2" % (sid, code, info["cancel_retry"]))
        return
    # m is None：查不到订单（可能已撤/已成交/列表过期）→ UNCONFIRMED，外部以 positions 兜底
    print("[P16G2I][CANCEL-UNKNOWN] %s %s 撤单后订单查不到，状态未知，写UNCONFIRMED（外部以positions兜底）" % (sid, code))
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
                print("[P16G2I][PENDING-NO-ORDER] %s %s elapsed=%.0fs" % (sid, code, elapsed))
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
            print("[P16G2I][FILLED] %s %s deal=%d" % (sid, code, total_deal))
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
            print("[P16G2I][REJECTED] %s %s status=57废单 deal=%d" % (sid, code, deal_vol))
            _handle_rejected_retry(C, date, sid, info, now, deal_vol)
            continue
        # 1c) 撤类终态(53,54) → CANCELED 收尾（vol=total_deal，防漏计最后成交）
        if status in CANCEL_STATUS:
            print("[P16G2I][CANCELED-FROM-QMT] %s %s status=%d deal=%d" % (sid, code, status, total_deal))
            _write_fill(C, date, sid, info, code, action, total_deal, "CANCELED",
                        sysid,
                        info.get("risk_reason", "canceled by QMT, status=%d" % status),
                        matched=matched)
            _g_pending.pop(sid, None)
            continue
        # 1d) 有成交未满 → PARTIAL_FILLED 进度回写（filled_so_far不变，deal_vol是累计值）
        # 注意：部成也必须走超时判断（否则停滞单永不撤/永不重报，挂到日终被 NEW-DAY 清掉）
        if deal_vol > 0:
            print("[P16G2I][PARTIAL] %s %s deal=%d/%d" % (sid, code, total_deal, vol))
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
        print("[P16G2I][PENDING-STATUS] %s %s status=%d deal=%d" % (sid, code, status, deal_vol))


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
                print("[P16G2I][CFG-RELOAD] %s 孤儿校准 cost=%.4f vol=%d" % (qcode, cost, info.get("vol", 0)))
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
                print("[P16G2I][POS-NEW] %s 入账 cost=%.4f vol=%d can_use=%d peak=%.4f（白名单内新持仓）" % (
                    code, info["cost"], info["vol"], info["can_use"], peak0))
        for code in list(_g_positions.keys()):
            if code not in acct_pos:
                _g_positions.pop(code, None)
                _g_risk_sold.discard(code)
    except Exception as e:
        print("[P16G2I][RISK-POS-ERR] %s" % e)
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
            print("[P16G2I][RISK-FAIL] %s %s %s 卖%d股 ret=%s 未记 risk_sold（下轮重试） | %s" % (
                action, sid_pre, code, can_use, ret, note))
            continue
        _g_risk_sold.add(code)
        _g_today_risk_count += 1
        sid = "P16_%s_RSK%04d" % (date, _g_today_risk_count)
        print("[P16G2I][RISK] %s %s %s 卖%d股 ret=%s | %s" % (action, sid, code, can_use, ret, note))
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
        print("[P16G2I][POSITIONS-ERR] %s" % e)


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
        print("[P16G2I][ASSET-ERR] %s" % e)


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
    print("[P16G2I][HEART] %s date=%s pending=%d positions=%d risk_sold=%d risk_count=%d fills=%d slippage=%d passive=%s" % (
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
    print("[P16G2I][HOLD] %s n=%d %s" % (
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
            print("[P16G2I][ARCHIVE-SKIP] %s seq变化 %d->%d，不归档" % (prefix, seq, cur_seq))
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
        print("[P16G2I][CMD-READ-FAIL] %s exists but read failed" % path)
        _write_cmd_alert(date, "orders", path)
        return 0

    # 账号防呆
    file_acct = str(data.get("account_id", "") or "")
    if file_acct != ACCOUNT_ID:
        print("[P16G2I][ACCT-MISMATCH] orders文件account=%s != 期望%s，跳过" % (file_acct, ACCOUNT_ID))
        return 0

    seq = int(data.get("seq", 0) or 0)
    if seq <= _g_last_cmd_seq:
        return 0

    print("[P16G2I][CMD] 读取orders seq=%d (上次=%d)" % (seq, _g_last_cmd_seq))
    _g_last_cmd_seq = seq
    _write_heartbeat(date, seq)
    orders = data.get("orders", [])
    for o in orders:
        try:
            _execute_order(C, o, date)
        except Exception:
            print("[P16G2I][ORDER-ERR] %s" % traceback.format_exc())

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
        print("[P16G2I][CMD-READ-FAIL] %s exists but read failed" % path)
        _write_cmd_alert(date, "cancel", path)
        return 0

    file_acct = str(data.get("account_id", "") or "")
    if file_acct != ACCOUNT_ID:
        print("[P16G2I][ACCT-MISMATCH] cancel文件account=%s != 期望%s，跳过" % (file_acct, ACCOUNT_ID))
        return 0

    seq = int(data.get("seq", 0) or 0)
    if seq <= _g_last_cmd_seq:
        return 0

    print("[P16G2I][CMD] 读取cancel seq=%d (上次=%d)" % (seq, _g_last_cmd_seq))
    _g_last_cmd_seq = seq
    _write_heartbeat(date, seq)
    cancels = data.get("cancels", [])
    cancel_data = {"cancels": cancels}
    try:
        _process_cancels(C, cancel_data, date)
    except Exception:
        print("[P16G2I][CANCEL-ERR] %s" % traceback.format_exc())

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
            print("[P16G2I][WARN] 白名单代码无法归一化，已忽略: %r" % (c,))
    # 信号内产版：L3 hold_dates 持票并入白名单（重启后风控必须纳管信号持仓）
    for c in list(_g_hold_dates.keys()):
        qc = _to_qmt_code_any(c)
        if qc:
            _g_whitelist.add(qc)
    # ★ 2026-10-23（T-20261023-002）：外部下单票自动并入白名单。
    # 拍板 F 后买入由外部 rebalance_g2.py 完成（remark=p16_g2_new），内置白名单
    # 恒量 G2_CODES 不可能预知新票 → 必须扫当日 order 反推，否则新仓零风控覆盖。
    # 官方口径（§06）：用订单备注 + 交易日期建立归属与幂等，而非硬编码。
    _ext_added = []
    try:
        _day_orders = f_get(ACCOUNT_ID, "stock", "order")
        for o in (_day_orders or []):
            try:
                _rk = str(getattr(o, "m_strRemark", "") or "")
                if EXTERNAL_REMARK_BUY not in _rk:
                    continue
                _oc = str(getattr(o, "m_strInstrumentID", "") or "")
                if not _oc:
                    continue
                _oc = _to_qmt_code_any(_oc)
                if _oc and _oc not in _g_whitelist:
                    _g_whitelist.add(_oc)
                    _ext_added.append(_oc)
            except Exception:
                continue
    except Exception as _e:
        print("[P16G2I][WARN] 外部委托白名单反推失败（可能零风控覆盖，需人工核对）: %r" % (_e,))
    if _ext_added:
        print("[P16G2I][INIT] 外部委托(remark=%s)反推并入白名单 %d 只: %s" % (
            EXTERNAL_REMARK_BUY, len(_ext_added), ",".join(sorted(_ext_added))))
    if not _g_whitelist:
        _g_passive = True
        print("[P16G2I][WARN] G2_CODES 白名单为空且 L3 账本无持票，完全被动模式（不评估不下单）")
        return
    print("[P16G2I][INIT] 白名单合并 G2_CODES+L3 hold_dates = %d 只" % len(_g_whitelist))
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
            print("[P16G2I][WARN] %s 成本字段缺失(m_dOpenPrice<=0)，该票风控暂跳过待逐tick自愈" % code)
    if managed <= 0:
        print("[P16G2I][WARN] 白名单(%d只) ∩ 账户position 交集为空，进入只监控模式"
              "（白名单内持仓出现即自动纳管）" % len(_g_whitelist))
    else:
        print("[P16G2I][INIT] 白名单∩position 纳管 %d/%d 只" % (managed, len(_g_whitelist)))
    # 3) L3 追盈峰值快照（只升不降 + stale 检测 T-20260928-002：快照超 PEAK_STALE_HOURS 不恢复）
    _pk_stale = False
    try:
        if PEAK_SNAPSHOT_DATE:
            _snap_dt = datetime.strptime(str(PEAK_SNAPSHOT_DATE), "%Y%m%d")
            _age_h = (datetime.now() - _snap_dt).total_seconds() / 3600.0
            if _age_h > PEAK_STALE_HOURS:
                _pk_stale = True
                print("[P16G2I][INIT] PEAK_SNAPSHOT STALE %.1fh ago (>%gh), skip restore "
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
                if not any(rk in remark for rk in ALL_REMARKS):
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
        print("[P16G2I][INIT] 当日order反查重建 risk_sold=%d只 RSK水位=%d" % (
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
        print("[P16G2I][INIT] L3快照 date=%s age=%dd hold_dates=%d ens=%d capital=%.0f" % (
            SNAPSHOT_DATE, _snap_age_d, len(_g_hold_dates), len(_g_oppo_blocked), _g_signal_capital))
        if _snap_age_d >= 0 and _snap_age_d > 14:
            print("[P16G2I][INIT][WARN] 静态快照已 %d 天未刷新(>14)，估值特征陈旧——"
                  "请重新构建（gen_g2_signal_snapshots.py + build_p16_g2_selfcontained.py）后粘贴" % _snap_age_d)
    except Exception:
        _g_passive = True
        print("[P16G2I][INIT-SNAP-ERR] L3 信号账本载入失败，转被动: %s" % traceback.format_exc())
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
            print("[P16G2I][INIT][WARN] 交易日历取数失败(%s)，持有期回退自然日/4" % e)
    try:
        _rebuild_state(C)
    except Exception:
        _g_passive = True
        print("[P16G2I][INIT-REBUILD-ERR] 状态重建异常，转被动模式（不评估不下单），"
              "疑似沙箱/接口异常，请重启客户端并确认交易账号登录成功后重载策略: %s" % traceback.format_exc())
    print("[P16G2I][BUILD] BUILD_TAG=%s account=%s mode=自闭环信号内产(零文件) bridge=%s" % (
        BUILD_TAG, ACCOUNT_ID, BRIDGE_DIR))
    print("[P16G2I][INIT] date=%s passive=%s last_seq=%d pending=%d positions=%d risk_sold=%d whitelist=%d" % (
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
    print("[P16G2I][SIG-FS] float_share 快照构建 %d 只（缺 %d）" % (len(fs), bad))
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
                print("[P16G2I][SIG-MD-ERR] batch=%d %s" % (i, e))
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
            print("[P16G2I][SIG-MD] 进度 %d/%d ok=%d" % (i + len(batch), len(codes), ok))
    print("[P16G2I][SIG-MD] 取数完成 ok=%d/%d" % (len(bars), len(codes)))
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
    print("[P16G2I][SIG-SCORE] rows=%d 红线(>=%.2f)过 %d/%d 首 %.4f 末 %.4f" % (
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
        print("[P16G2I][SIG-TIER] 沪深300 取数失败(%s)，fail-safe T=1 半仓" % tier_err)
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
    print("[P16G2I][SIG-TIER] hs300=%s tier=%d deploy=%.2f" % (
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
            print("[P16G2I][SIG-BUY-SKIP] %s 取价失败" % code)
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
                    print("[P16G2I][SIG-BUY-SKIP] %s 极端低开%.1f%%" % (code, _opct))
                    continue
                if _opct <= -3.0:
                    _v0 = float(t.get("volume", 0) or 0)
                    _b = (_g_signal_bars or {}).get(code)
                    _avg5 = float(_b["volume"].tail(5).mean()) if _b is not None else 0.0
                    _vr = (_v0 / _avg5) if (_avg5 > 0 and _v0 > 0) else None
                    if not (_vr is not None and _vr >= 2.0):
                        print("[P16G2I][SIG-BUY-SKIP] %s 低开%.1f%%且量比不足跳过" % (code, _opct))
                        continue
        except Exception:
            pass
        tgt = int(target_value / px / SIGNAL_MIN_VOL) * SIGNAL_MIN_VOL
        if tgt < SIGNAL_MIN_VOL:
            print("[P16G2I][SIG-BUY-SKIP] %s 资金不足一手 @%.2f" % (code, px))
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
        print("[P16G2I][SIG-WATERMARK] 同日重启重建 signal_seq=%d bought=%d" % (
            max_seq, len(_g_signal_bought)))


def _run_signal(C, date):
    """信号入口（时间闸门内调用一次/日）。失败转被动并大声打印。"""
    global _g_signal_done, _g_signal_stage, _g_signal_seq, _g_hold_dates, _g_signal_capital
    if _g_signal_done:
        return
    if _g_passive:
        print("[P16G2I][SIG-SKIP] 被动模式，信号不执行")
        _g_signal_done = True
        return
    t0 = time.time()
    _g_signal_stage = "start"
    print("[P16G2I][SIG-START] date=%s snap=%s n=%d" % (
        date, SNAPSHOT_DATE, SNAP_N))
    try:
        _rebuild_signal_watermark(C, date)
        _g_signal_stage = "scores"
        pairs = _compute_scores(C, date)
        _g_signal_stage = "plan"
        orders, meta = _plan_signal(C, date, pairs)
        _g_signal_stage = "execute"
        print("[P16G2I][SIG-PLAN] %s orders=%d" % (meta, len(orders)))
        for o in orders:
            if o["action"] == "BUY" and o["code"] in _g_signal_bought:
                print("[P16G2I][SIG-SKIP-DUP] %s 当日已买，跳过" % o["code"])
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
        print("[P16G2I][SIG-DONE] %d 单 耗时 %.1fs" % (len(orders), time.time() - t0))
    except Exception:
        _g_signal_done = True   # 失败也标完成（防同日反复重试放大风险）
        print("[P16G2I][SIG-ERR] 信号失败(阶段=%s)，当日信号终止（持仓风控不受影响）: %s" % (
            _g_signal_stage, traceback.format_exc()))


def _late_bind_whitelist(C):
    """被动模式下延迟绑定白名单（T-20261023-002）。

    场景：策略 09:00 init 时账户空仓、当日无委托 → 白名单空 → 进被动；
          09:50 外部 rebalance_g2.py 用 remark=p16_g2_new 买入 →
          若不做延迟绑定，内置风控永远 passive，新仓零止损覆盖（P0 同型）。

    做法（官方 §06「订单备注 + 交易日建立归属」）：每 30s 扫一次当日 order，
    凡 remark 命中 EXTERNAL_REMARK_BUY 的票并入白名单；一旦非空则重建持仓并解除被动。

    返回 True = 已成功绑定并解除被动（调用方应继续走正常 handlebar 流程）。
    """
    global _g_passive, _g_last_bind_try, _g_whitelist
    import time as _t
    now = _t.time()
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
    print("[P16G2I][LATE-BIND] 被动模式下捕获外部买入 %d 只并入白名单: %s"
          % (len(added), ",".join(sorted(added))))
    # 重建持仓纳管集（复用 init 口径：白名单 ∩ 账户 position）
    managed = 0
    try:
        for p in (f_get(ACCOUNT_ID, "stock", "position") or []):
            code = _to_qmt_code_any(str(getattr(p, "m_strInstrumentID", "") or ""))
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
        print("[P16G2I][LATE-BIND] 持仓重建异常，保持被动: %r" % (e,))
        return False
    if managed <= 0:
        print("[P16G2I][LATE-BIND] 白名单已扩充但账户暂无对应持仓（T+1 未成交），继续等待")
        return False
    _g_passive = False
    print("[P16G2I][LATE-BIND] 解除被动，纳管 %d 只: %s"
          % (managed, ",".join(sorted(_g_positions.keys()))))
    return True


def handlebar(C):
    """每分钟触发：信号闸门→指令→下单→反查→pending→快照→心跳。"""
    global _g_last_cmd_seq, _g_today, _g_last_position_refresh, _g_last_asset_refresh, _g_slippage_alert_count
    # ★ 信号闸门段对 _g_signal_* 有赋值 -> 必须显式声明 global，
    #   否则 Python 判其为局部变量，读取处抛 UnboundLocalError（冒烟实测）。
    global _g_signal_done, _g_signal_stage, _g_signal_prob, _g_signal_bought, _g_signal_seq

    # 被动模式守卫（T-20261015-004）：IO 预检失败后不读指令/不下单/只写心跳，防沙箱下盲跑
    # ★ 2026-10-23（T-20261023-002）：被动模式下每 30s 尝试延迟绑定（外部买入票自动纳管）。
    #   绑定成功则解除被动、继续走正常风控流程；仍失败则只写心跳退出。
    if _g_passive:
        if _late_bind_whitelist(C):
            pass   # 已解除被动，继续往下执行
        else:
            try:
                _write_heartbeat(_g_today, _g_last_cmd_seq)
            except Exception:
                pass
            return

    # 日期切换重置
    today = _today_str()
    if today != _g_today:
        if _g_slippage_alert_count:
            print("[P16G2I][SLIPPAGE-DAY-SUMMARY] 前一交易日不利滑点/跳空超限 %d 次" % _g_slippage_alert_count)
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
        print("[P16G2I][NEW-DAY] date=%s pending清空 risk_sold清空 slippage_alert清零 signal复位" % today)

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
            print("[P16G2I][HANDLEBAR-ERR-HEART] %s" % traceback.format_exc())
        return

    # 0) 信号闸门（s4-5d 信号内产版）：09:50 触发一次（对齐原 G2 换仓时点），
    #    09:58 后未跑则当日放弃（防盘中误触发）。信号段自身 try/except 全隔离。
    # ★ 2026-10-22 拍板 F：SIGNAL_ENABLED=False → 整段跳过，信号由外部 Python 算 F1~F6
    if SIGNAL_ENABLED and (not _g_signal_done) and _g_signal_stage == "":
        if hour_min >= SIGNAL_HM and hour_min <= SIGNAL_DEADLINE_HM:
            try:
                _run_signal(C, _g_today)
            except Exception:
                print("[P16G2I][HANDLEBAR-ERR-SIG] %s" % traceback.format_exc())
                _g_signal_done = True
        elif hour_min > SIGNAL_DEADLINE_HM:
            _g_signal_done = True   # 过点放弃（当日不追补）
            print("[P16G2I][SIG-EXPIRED] 过信号时点(%s)且未执行，当日放弃" % SIGNAL_DEADLINE_HM)

    # 1) pending 状态机（风控卖出委托的成交跟踪/超时重试/撤单重下）
    try:
        _check_pending_orders(C, _g_today)
    except Exception:
        print("[P16G2I][HANDLEBAR-ERR-PENDING] %s" % traceback.format_exc())

    # 2) 内置实时风控（止损/止盈/追盈，纯行情驱动）
    try:
        _check_risk_signals(C, _g_today)
    except Exception:
        print("[P16G2I][HANDLEBAR-ERR-RISK] %s" % traceback.format_exc())

    # 3) print 心跳（含持仓/资产快照的 print 留痕；原文件快照链路已废止）
    try:
        _write_heartbeat(_g_today, _g_last_cmd_seq)
    except Exception:
        print("[P16G2I][HANDLEBAR-ERR-HEART] %s" % traceback.format_exc())


def exit(C):
    """日终摘要。"""
    pending_count = len(_g_pending)
    print("[P16G2I][EXIT] date=%s 委托=%d 成交=%d 放弃=%d 最终pending=%d" % (
        _g_today, _g_today_order_count, _g_today_fill_count,
        _g_today_abandon_count, pending_count))
    # 写心跳
    try:
        _write_heartbeat(_g_today, _g_last_cmd_seq)
    except Exception:
        pass
