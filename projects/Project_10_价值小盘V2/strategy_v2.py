# coding=gbk
"""价值小盘V2微调版 — QMT单文件策略
行业中性BP(z-score)*1.0 (V2a 纯BP, hp已去除)
风控: 8%止损 / 60天持有上限 / 15%组合回撤
账号: 67014907
自动生成于 2026-08-04
2b (2026-10-16): 自闭环状态侧——state/文件锁/日志/遥测写盘链路全部废止，
改 print 化 + init 期初账本(PERIOD_LEDGER∩账户position)重建 + 接口探针预检；
运行期零 open()/json 读写（QMT沙箱自闭环，方案B，specs/QMT沙箱自闭环改造方案_20261015.md）。"""
import math
import time as _time
from datetime import datetime, timedelta

# ============ 参数 ============
ACCOUNT_ID = "67014907"
N_STOCKS = 30
REBALANCE_MONTHS = 2
STOP_LOSS = 0.08
MAX_HOLDING_DAYS = 60
MAX_DRAWDOWN = 0.15
Z_WEIGHT = 1.0
HP_WEIGHT = 0.0
HP_WINDOW = 36
HP_MIN = 12
CAPITAL_INIT = 100000.0   # 专属资金池初始资金（小资金测试，收益滚动）
MIN_POSITION_PCT = 0.6    # 持仓低于60%触发补仓

# v2.3 (2026-08-06 讨论室批准): buffer 降换手 + 退市排雷
# buffer: 换仓时卖出排名 > BUFFER_KEEP_MAX 的持仓 (排名=当期候选评分降序)。
#   0 = 关闭(保持旧行为: 换仓不卖出, 仅靠止损/持有期/回撤卖)。
#   160 = 已验证档位 (研究回测: 年化+1.7pp / 超额全期+43pp / 换手0.91->0.80, 2024+不劣化)。
BUFFER_KEEP_MAX = 160
# 退市排雷: 市值红线缓冲区 + 退市临近剔除 (数据源 delist_info.csv / financial_total_mv.csv)
DELIST_MV_MAIN = 75000.0     # 主板总市值红线缓冲: 5亿 x 1.5 (万元)
DELIST_MV_GEMSTAR = 45000.0  # 创业板/科创板: 3亿 x 1.5 (万元)
DELIST_NEAR_DAYS = 30        # 距退市日 <= 30 天剔除 (北交所不适用市值红线)

# E1 宇宙筛选 (2026-10-13 对齐回测 runner.get_candidates / config/strategy.yaml):
#   回测候选 = circ_mv∈(min,max) 且 pe_ttm>0 且 pb>0 且 质量排雷(bps>0 & roe>0 & profit_dedt>0)
#             且 非ST 且 非停牌。生产旧版仅 pb>0 → 实为"全市场行业中性BP top80"，
#   与回测口径漂移(P0)。补齐后生产候选池=回测口径。停牌沿用买入时 _is_suspended 实时剔除。
MARKET_CAP_MAX = 300000.0    # 流通市值上限(万元) = 30亿
MARKET_CAP_MIN = 0.0         # 流通市值下限(万元) >0
PE_TTM_MIN = 0.0             # PE_TTM > 0
PB_MIN = 0.0                 # PB > 0
QUALITY_BPS_MIN = 0.0        # 每股净资产 > 0 (质量排雷)
QUALITY_ROE_MIN = 0.0        # ROE > 0 (质量排雷)
QUALITY_PROFIT_DEDT_MIN = 0.0  # 扣非净利润 > 0 (质量排雷)

# 2b (2026-10-16)：state/日志/遥测/锁 的文件路径常量随文件链路一并废止（运行期零文件IO）

# 挂单巡检：委托超时未成交则撤单重下，避免错过交易黄金期
PENDING_TIMEOUT_MIN = 3      # 委托超过3分钟仍未成交，触发撤单重下
PENDING_MAX_RETRIES = 3      # 单只最多重下次数（含首次），超过则放弃（等收盘对账校准）
RETRY_COOLDOWN_MIN = 1       # 撤单重下的最小冷却间隔（分钟），防止同一分钟反复重下

# 下单/撤单机制常量（2026-10-13 修复，依据 QMT撤单重委托方案调研.md + P16 生产范式）
PRTYPE_LATEST = 5            # prType=5「最新价」限价单（会忽略 price 参数，仅作取价失败兜底）
PRTYPE_PRICE = 11            # prType=11「指定价」限价单（用 price 做限价）
AGGRESSIVE_BUFFER = 0.02     # 激进价缓冲：买入上浮 / 卖出下探，促成交
CONFIRM_DEAD_STATUS = (53, 54, 57)  # 撤单确认死透终态：部撤/已撤/废单
CANCEL_CONFIRM_RETRIES = 4          # 撤单后确认终态的轮询次数
CANCEL_CONFIRM_INTERVAL = 0.5       # 撤单后确认终态的轮询间隔（秒）

# 心跳节流（2b 打印化）：[HEART] print 最小间隔（秒），替代原遥测/heart json 落盘
HEARTBEAT_INTERVAL_SEC = 60

# 构建版本标记：build.py 每次构建时自动替换为时间戳（YYYYmmdd-HHMMSS）
BUILD_TAG = "dev"

# ============ V2 自闭环快照（2a, 2026-10-16）============
# 快照由 op 脚本按 BEGIN/END 标记注入替换（标记行逐字保留，勿改）。
# 字段约定（供快照生成脚本对齐；加载侧见 _load_* 系列）：
#   circ_mv/pe_ttm/pb/bps/roe/profit_dedt : float（万元/倍数，与原 financial_*.csv 同口径）
#   industry   : str 行业名（同原 industry_map.csv）
#   bp_hist    : [(date 'YYYY-MM-DD', hp_pct float), ...] 升序（同原 bp_hist_pct.csv）
#   delist     : [list_status, delist_date 'YYYY-MM-DD']（同原 delist_info.csv 两列）
#   可选 total_mv : float 总市值万元（退市排雷市值红线用；缺失则该股跳过红线）
#   可选 is_st    : 真值时进入 ST 兜底集合（主判为 C.get_stock_name 实时含 "ST"）
# --- V2SNAPSHOT-BEGIN ---
SNAPSHOT = {
    '000019.SZ': {'pe_ttm': 45.1192, 'pb': 1.6282, 'circ_mv': 291762.44, 'total_mv': 807927.25, 'bps': 4.3054, 'roe': 2.2483, 'profit_dedt': 85814589.18, 'industry': '农业综合', 'is_st': False, 'delist': ['', '']},
    '000020.SZ': {'pe_ttm': 137.0374, 'pb': 9.0357, 'circ_mv': 258704.19, 'total_mv': 404354.19, 'bps': 1.5804, 'roe': 1.8962, 'profit_dedt': 7962959.89, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '000153.SZ': {'pe_ttm': 30.0689, 'pb': 1.3127, 'circ_mv': 281582.95, 'total_mv': 283922.84, 'bps': 4.6547, 'roe': 3.3413, 'profit_dedt': 63862068.93, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '000159.SZ': {'pe_ttm': 91.8974, 'pb': 1.273, 'circ_mv': 261964.33, 'total_mv': 261973.87, 'bps': 4.2812, 'roe': 0.742, 'profit_dedt': 16844121.72, 'industry': '石油贸易', 'is_st': False, 'delist': ['', '']},
    '000419.SZ': {'pe_ttm': 24.4758, 'pb': 0.8208, 'circ_mv': 277227.18, 'total_mv': 277227.18, 'bps': 6.2135, 'roe': 2.1743, 'profit_dedt': 53584186.07, 'industry': '百货', 'is_st': False, 'delist': ['', '']},
    '000548.SZ': {'pe_ttm': 80.588, 'pb': 1.1237, 'circ_mv': 231636.13, 'total_mv': 231636.13, 'bps': 4.1291, 'roe': 1.1006, 'profit_dedt': 22718271.96, 'industry': '路桥', 'is_st': False, 'delist': ['', '']},
    '000554.SZ': {'pe_ttm': 17.3389, 'pb': 2.4394, 'circ_mv': 222409.67, 'total_mv': 295538.39, 'bps': 2.5375, 'roe': 9.2205, 'profit_dedt': 108428410.51, 'industry': '石油贸易', 'is_st': False, 'delist': ['', '']},
    '000565.SZ': {'pe_ttm': 202.8651, 'pb': 2.142, 'circ_mv': 274897.45, 'total_mv': 274897.45, 'bps': 2.9598, 'roe': 1.9365, 'profit_dedt': 4831241.46, 'industry': '染料涂料', 'is_st': False, 'delist': ['', '']},
    '000570.SZ': {'pe_ttm': 45.1874, 'pb': 1.0522, 'circ_mv': 293405.64, 'total_mv': 372605.64, 'bps': 5.018, 'roe': 3.0084, 'profit_dedt': 85247927.47, 'industry': '农用机械', 'is_st': False, 'delist': ['', '']},
    '000573.SZ': {'pe_ttm': 197.4432, 'pb': 1.5499, 'circ_mv': 246698.32, 'total_mv': 248929.43, 'bps': 2.5164, 'roe': 2.8239, 'profit_dedt': 28032410.2, 'industry': '区域地产', 'is_st': False, 'delist': ['', '']},
    '000605.SZ': {'pe_ttm': 242.809, 'pb': 1.0036, 'circ_mv': 213005.79, 'total_mv': 213005.79, 'bps': 6.0182, 'roe': 0.6752, 'profit_dedt': 3497627.63, 'industry': '水务', 'is_st': False, 'delist': ['', '']},
    '000633.SZ': {'pe_ttm': 379.622, 'pb': 10.1716, 'circ_mv': 203721.29, 'total_mv': 203721.29, 'bps': 0.5201, 'roe': 1.1917, 'profit_dedt': 1242979.65, 'industry': '小金属', 'is_st': False, 'delist': ['', '']},
    '000715.SZ': {'pe_ttm': 25.8681, 'pb': 1.4225, 'circ_mv': 259161.18, 'total_mv': 288051.7, 'bps': 3.747, 'roe': 2.2758, 'profit_dedt': 37961378.75, 'industry': '百货', 'is_st': False, 'delist': ['', '']},
    '000757.SZ': {'pe_ttm': 63.6023, 'pb': 1.4215, 'circ_mv': 229067.92, 'total_mv': 229067.92, 'bps': 3.025, 'roe': 1.1655, 'profit_dedt': 14745690.96, 'industry': '汽车服务', 'is_st': False, 'delist': ['', '']},
    '000856.SZ': {'pe_ttm': 94.03, 'pb': 4.7826, 'circ_mv': 201576.0, 'total_mv': 201576.0, 'bps': 1.8567, 'roe': 1.4964, 'profit_dedt': 2150358.31, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '000953.SZ': {'pe_ttm': 629.309, 'pb': 14.1622, 'circ_mv': 218941.08, 'total_mv': 218941.08, 'bps': 0.4223, 'roe': 0.5149, 'profit_dedt': 761858.33, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '001202.SZ': {'pe_ttm': 40.63, 'pb': 2.8212, 'circ_mv': 173834.11, 'total_mv': 248036.51, 'bps': 5.2744, 'roe': 3.3983, 'profit_dedt': 28893815.4, 'industry': '仓储物流', 'is_st': False, 'delist': ['', '']},
    '001205.SZ': {'pe_ttm': 21.3748, 'pb': 1.2509, 'circ_mv': 240403.33, 'total_mv': 271477.05, 'bps': 11.5433, 'roe': 3.3202, 'profit_dedt': 71327991.41, 'industry': '水运', 'is_st': False, 'delist': ['', '']},
    '001206.SZ': {'pe_ttm': 25.3242, 'pb': 1.8751, 'circ_mv': 198981.7, 'total_mv': 344272.44, 'bps': 9.9303, 'roe': 2.9545, 'profit_dedt': 47798033.67, 'industry': '造纸', 'is_st': False, 'delist': ['', '']},
    '001209.SZ': {'pe_ttm': 97.8589, 'pb': 1.9254, 'circ_mv': 181390.89, 'total_mv': 248183.33, 'bps': 9.8008, 'roe': 1.2251, 'profit_dedt': 11350965.06, 'industry': '服饰', 'is_st': False, 'delist': ['', '']},
    '001218.SZ': {'pe_ttm': 12.0547, 'pb': 1.2963, 'circ_mv': 280906.76, 'total_mv': 314969.97, 'bps': 18.6647, 'roe': 6.4931, 'profit_dedt': 148103733.82, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '001219.SZ': {'pe_ttm': 22.577, 'pb': 2.3419, 'circ_mv': 246115.57, 'total_mv': 252309.04, 'bps': 5.5255, 'roe': 6.138, 'profit_dedt': 62257467.42, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '001220.SZ': {'pe_ttm': 22.2497, 'pb': 1.8688, 'circ_mv': 72170.78, 'total_mv': 288683.12, 'bps': 16.7384, 'roe': 4.5053, 'profit_dedt': 53074765.35, 'industry': '仓储物流', 'is_st': False, 'delist': ['', '']},
    '001221.SZ': {'pe_ttm': 17.876, 'pb': 4.2941, 'circ_mv': 250902.37, 'total_mv': 1305632.64, 'bps': 7.6011, 'roe': 9.9821, 'profit_dedt': 278900259.63, 'industry': '家居用品', 'is_st': False, 'delist': ['', '']},
    '001222.SZ': {'pe_ttm': 35.9708, 'pb': 2.0518, 'circ_mv': 174648.82, 'total_mv': 293588.82, 'bps': 7.4958, 'roe': 2.8092, 'profit_dedt': 39962390.99, 'industry': '文教休闲', 'is_st': False, 'delist': ['', '']},
    '001226.SZ': {'pe_ttm': 131.2199, 'pb': 4.5458, 'circ_mv': 162241.4, 'total_mv': 341226.82, 'bps': 10.0533, 'roe': 2.4166, 'profit_dedt': 16751620.77, 'industry': '工程机械', 'is_st': False, 'delist': ['', '']},
    '001229.SZ': {'pe_ttm': 97.5275, 'pb': 5.3301, 'circ_mv': 273838.21, 'total_mv': 532563.39, 'bps': 6.9661, 'roe': 0.419, 'profit_dedt': 2652151.46, 'industry': 'IT设备', 'is_st': False, 'delist': ['', '']},
    '001230.SZ': {'pe_ttm': 19.6509, 'pb': 1.7639, 'circ_mv': 208632.44, 'total_mv': 356212.02, 'bps': 10.7547, 'roe': 4.5414, 'profit_dedt': 88441405.8, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '001231.SZ': {'pe_ttm': 31.1506, 'pb': 2.1484, 'circ_mv': 114768.45, 'total_mv': 224478.46, 'bps': 10.4551, 'roe': 5.103, 'profit_dedt': 47180366.33, 'industry': '农药化肥', 'is_st': False, 'delist': ['', '']},
    '001233.SZ': {'pe_ttm': 20.2355, 'pb': 1.6791, 'circ_mv': 165183.38, 'total_mv': 814563.05, 'bps': 26.0848, 'roe': 3.8593, 'profit_dedt': 165533962.06, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '001234.SZ': {'pe_ttm': 68.8771, 'pb': 3.0367, 'circ_mv': 236177.07, 'total_mv': 280161.17, 'bps': 8.4959, 'roe': 2.5139, 'profit_dedt': 20751918.49, 'industry': '服饰', 'is_st': False, 'delist': ['', '']},
    '001237.SZ': {'pe_ttm': 19.7524, 'pb': 2.1368, 'circ_mv': 170968.68, 'total_mv': 727663.62, 'bps': 22.9546, 'roe': 7.5813, 'profit_dedt': 173214960.39, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '001256.SZ': {'pe_ttm': 12.7511, 'pb': 2.2229, 'circ_mv': 232214.17, 'total_mv': 309754.57, 'bps': 9.771, 'roe': 12.0786, 'profit_dedt': 67110013.04, 'industry': '轻工机械', 'is_st': False, 'delist': ['', '']},
    '001259.SZ': {'pe_ttm': 297.8366, 'pb': 4.4679, 'circ_mv': 208007.19, 'total_mv': 321951.44, 'bps': 9.792, 'roe': 0.8916, 'profit_dedt': 7124223.39, 'industry': '家用电器', 'is_st': False, 'delist': ['', '']},
    '001260.SZ': {'pe_ttm': 97.7938, 'pb': 2.7233, 'circ_mv': 110640.0, 'total_mv': 220800.0, 'bps': 7.0504, 'roe': 0.4165, 'profit_dedt': 2516173.06, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '001266.SZ': {'pe_ttm': 97.1753, 'pb': 4.2987, 'circ_mv': 232357.81, 'total_mv': 414191.79, 'bps': 9.2958, 'roe': 4.4611, 'profit_dedt': 39073283.67, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '001277.SZ': {'pe_ttm': 24.7372, 'pb': 1.4593, 'circ_mv': 138715.18, 'total_mv': 230204.0, 'bps': 20.7559, 'roe': 2.9888, 'profit_dedt': 44622081.55, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '001279.SZ': {'pe_ttm': 123.9506, 'pb': 4.1384, 'circ_mv': 104000.0, 'total_mv': 416000.0, 'bps': 6.2826, 'roe': 1.1713, 'profit_dedt': 8668459.29, 'industry': '广告包装', 'is_st': False, 'delist': ['', '']},
    '001282.SZ': {'pe_ttm': 47.8072, 'pb': 2.2586, 'circ_mv': 255818.06, 'total_mv': 372160.88, 'bps': 7.416, 'roe': 2.4096, 'profit_dedt': 37662508.45, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '001296.SZ': {'pe_ttm': 27.5392, 'pb': 1.5921, 'circ_mv': 157025.65, 'total_mv': 290057.14, 'bps': 12.1792, 'roe': 3.035, 'profit_dedt': 51259622.31, 'industry': '其他建材', 'is_st': False, 'delist': ['', '']},
    '001299.SZ': {'pe_ttm': 34.4216, 'pb': 2.8247, 'circ_mv': 284878.74, 'total_mv': 317043.14, 'bps': 3.5721, 'roe': 3.4133, 'profit_dedt': 35236051.26, 'industry': '供气供热', 'is_st': False, 'delist': ['', '']},
    '001312.SZ': {'pe_ttm': 18.3906, 'pb': 1.764, 'circ_mv': 101516.69, 'total_mv': 414400.12, 'bps': 10.0679, 'roe': 5.4824, 'profit_dedt': 86439596.46, 'industry': '纺织', 'is_st': False, 'delist': ['', '']},
    '001319.SZ': {'pe_ttm': 21.7941, 'pb': 2.1556, 'circ_mv': 173348.21, 'total_mv': 298919.6, 'bps': 9.8069, 'roe': 3.8272, 'profit_dedt': 51980491.0, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '001324.SZ': {'pe_ttm': 96.4613, 'pb': 2.4251, 'circ_mv': 279588.0, 'total_mv': 279588.0, 'bps': 8.3543, 'roe': 2.0513, 'profit_dedt': 20636961.72, 'industry': '运输设备', 'is_st': False, 'delist': ['', '']},
    '001325.SZ': {'pe_ttm': 20.4239, 'pb': 1.5378, 'circ_mv': 69540.8, 'total_mv': 278163.2, 'bps': 23.0716, 'roe': 2.9162, 'profit_dedt': 50589467.19, 'industry': '橡胶', 'is_st': False, 'delist': ['', '']},
    '001326.SZ': {'pe_ttm': 258.0375, 'pb': 6.0079, 'circ_mv': 242646.78, 'total_mv': 739539.6, 'bps': 16.8162, 'roe': 1.5888, 'profit_dedt': 17783172.56, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '001332.SZ': {'pe_ttm': 20.5615, 'pb': 1.5906, 'circ_mv': 180781.18, 'total_mv': 416105.34, 'bps': 16.9116, 'roe': 3.7734, 'profit_dedt': 92956675.27, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '001333.SZ': {'pe_ttm': 23.7138, 'pb': 1.4606, 'circ_mv': 147482.0, 'total_mv': 255104.0, 'bps': 13.6448, 'roe': 2.4046, 'profit_dedt': 35090816.83, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '001335.SZ': {'pe_ttm': 39.5435, 'pb': 3.0721, 'circ_mv': 84397.38, 'total_mv': 309715.31, 'bps': 10.7548, 'roe': 5.561, 'profit_dedt': 52554577.84, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '001336.SZ': {'pe_ttm': 46.3244, 'pb': 2.3753, 'circ_mv': 120688.66, 'total_mv': 196119.47, 'bps': 10.2728, 'roe': 2.0899, 'profit_dedt': 15844866.87, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '001356.SZ': {'pe_ttm': 80.3655, 'pb': 2.7285, 'circ_mv': 194972.48, 'total_mv': 542763.72, 'bps': 3.3755, 'roe': 2.1088, 'profit_dedt': 40177176.92, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '001358.SZ': {'pe_ttm': 56.7473, 'pb': 2.0623, 'circ_mv': 123209.72, 'total_mv': 297774.4, 'bps': 11.7198, 'roe': 2.6278, 'profit_dedt': 29670089.97, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '001360.SZ': {'pe_ttm': 101.0406, 'pb': 2.7398, 'circ_mv': 237040.03, 'total_mv': 385356.0, 'bps': 6.8946, 'roe': 2.0738, 'profit_dedt': 23951848.91, 'industry': '工程机械', 'is_st': False, 'delist': ['', '']},
    '001365.SZ': {'pe_ttm': 30.1148, 'pb': 2.4799, 'circ_mv': 202707.24, 'total_mv': 1850625.0, 'bps': 14.2141, 'roe': 4.566, 'profit_dedt': 266326329.6, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '001366.SZ': {'pe_ttm': 418.2434, 'pb': 2.6183, 'circ_mv': 210972.84, 'total_mv': 210972.84, 'bps': 5.0147, 'roe': 1.2711, 'profit_dedt': 2839840.26, 'industry': '饲料', 'is_st': False, 'delist': ['', '']},
    '001367.SZ': {'pe_ttm': 23.8122, 'pb': 2.1665, 'circ_mv': 267671.09, 'total_mv': 335324.99, 'bps': 10.1592, 'roe': 4.3225, 'profit_dedt': 63199682.5, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '001369.SZ': {'pe_ttm': 25.9313, 'pb': 1.9462, 'circ_mv': 247699.36, 'total_mv': 1369518.0, 'bps': 6.1351, 'roe': 3.9138, 'profit_dedt': 252144494.96, 'industry': '化纤', 'is_st': False, 'delist': ['', '']},
    '001376.SZ': {'pe_ttm': 30.4022, 'pb': 4.4713, 'circ_mv': 261858.19, 'total_mv': 595114.8, 'bps': 2.8878, 'roe': 7.3506, 'profit_dedt': 91109420.64, 'industry': '供气供热', 'is_st': False, 'delist': ['', '']},
    '001378.SZ': {'pe_ttm': 35.8033, 'pb': 2.0298, 'circ_mv': 185212.62, 'total_mv': 380640.81, 'bps': 10.8191, 'roe': 3.2532, 'profit_dedt': 56259228.92, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '001379.SZ': {'pe_ttm': 53.5605, 'pb': 2.0921, 'circ_mv': 105994.98, 'total_mv': 315000.0, 'bps': 7.5284, 'roe': 1.7508, 'profit_dedt': 24572272.06, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '001382.SZ': {'pe_ttm': 75.3406, 'pb': 4.1579, 'circ_mv': 103509.0, 'total_mv': 676916.0, 'bps': 3.9515, 'roe': 2.7452, 'profit_dedt': 44376464.48, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '001386.SZ': {'pe_ttm': 19.348, 'pb': 1.5739, 'circ_mv': 171853.39, 'total_mv': 1909482.16, 'bps': 10.1531, 'roe': 3.565, 'profit_dedt': 383586333.07, 'industry': '家居用品', 'is_st': False, 'delist': ['', '']},
    '001387.SZ': {'pe_ttm': 30.8323, 'pb': 1.736, 'circ_mv': 144114.96, 'total_mv': 226545.83, 'bps': 7.13, 'roe': 0.8457, 'profit_dedt': 8712354.05, 'industry': '家用电器', 'is_st': False, 'delist': ['', '']},
    '001388.SZ': {'pe_ttm': 45.219, 'pb': 3.0223, 'circ_mv': 244356.63, 'total_mv': 456545.0, 'bps': 9.6833, 'roe': 0.9711, 'profit_dedt': 8584980.76, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '001390.SZ': {'pe_ttm': 19.7938, 'pb': 2.0417, 'circ_mv': 201486.16, 'total_mv': 342200.17, 'bps': 8.3803, 'roe': 5.6302, 'profit_dedt': 91654971.12, 'industry': '纺织', 'is_st': False, 'delist': ['', '']},
    '001393.SZ': {'pe_ttm': 33.5006, 'pb': 2.6974, 'circ_mv': 192401.0, 'total_mv': 998580.27, 'bps': 14.8479, 'roe': 4.9086, 'profit_dedt': 133255756.4, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '001395.SZ': {'pe_ttm': 13.7128, 'pb': 2.6587, 'circ_mv': 157677.98, 'total_mv': 337400.7, 'bps': 11.1895, 'roe': 12.7563, 'profit_dedt': 154912139.75, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '001396.SZ': {'pe_ttm': 29.8361, 'pb': 2.3132, 'circ_mv': 70174.05, 'total_mv': 330221.51, 'bps': 13.354, 'roe': 1.3104, 'profit_dedt': 11499182.44, 'industry': '建筑工程', 'is_st': False, 'delist': ['', '']},
    '001400.SZ': {'pe_ttm': 81.8348, 'pb': 5.5906, 'circ_mv': 273174.72, 'total_mv': 712320.0, 'bps': 15.1683, 'roe': 4.4005, 'profit_dedt': 52757967.99, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '002054.SZ': {'pe_ttm': 20.3698, 'pb': 1.2941, 'circ_mv': 271753.62, 'total_mv': 340855.66, 'bps': 5.4634, 'roe': 3.7374, 'profit_dedt': 90431221.62, 'industry': '染料涂料', 'is_st': False, 'delist': ['', '']},
    '002058.SZ': {'pe_ttm': 86.0595, 'pb': 40.0013, 'circ_mv': 289598.92, 'total_mv': 289765.57, 'bps': 0.505, 'roe': 25.8771, 'profit_dedt': 15571327.21, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '002098.SZ': {'pe_ttm': 14.4502, 'pb': 1.8655, 'circ_mv': 293918.0, 'total_mv': 293918.0, 'bps': 4.401, 'roe': 7.9718, 'profit_dedt': 121516259.76, 'industry': '服饰', 'is_st': False, 'delist': ['', '']},
    '002103.SZ': {'pe_ttm': 25.8178, 'pb': 2.7503, 'circ_mv': 259274.58, 'total_mv': 356894.36, 'bps': 2.4288, 'roe': 3.9293, 'profit_dedt': 40308119.62, 'industry': '文教休闲', 'is_st': False, 'delist': ['', '']},
    '002144.SZ': {'pe_ttm': 75.5232, 'pb': 0.9086, 'circ_mv': 151838.1, 'total_mv': 194969.04, 'bps': 12.1394, 'roe': 1.1016, 'profit_dedt': 16786885.59, 'industry': '纺织', 'is_st': False, 'delist': ['', '']},
    '002148.SZ': {'pe_ttm': 1499.8681, 'pb': 2.8111, 'circ_mv': 278734.44, 'total_mv': 345308.3, 'bps': 2.1984, 'roe': 0.3911, 'profit_dedt': 2639574.33, 'industry': '互联网', 'is_st': False, 'delist': ['', '']},
    '002159.SZ': {'pe_ttm': 18.1961, 'pb': 1.6144, 'circ_mv': 184325.93, 'total_mv': 235988.03, 'bps': 8.2447, 'roe': 3.687, 'profit_dedt': 64073222.78, 'industry': '旅游服务', 'is_st': False, 'delist': ['', '']},
    '002186.SZ': {'pe_ttm': 330.4462, 'pb': 3.4292, 'circ_mv': 294836.34, 'total_mv': 294951.66, 'bps': 2.8024, 'roe': 1.5887, 'profit_dedt': 8297589.38, 'industry': '酒店餐饮', 'is_st': False, 'delist': ['', '']},
    '002188.SZ': {'pe_ttm': 74.5996, 'pb': 5.6746, 'circ_mv': 164712.32, 'total_mv': 209346.5, 'bps': 1.1278, 'roe': 4.0405, 'profit_dedt': 13224025.99, 'industry': '房产服务', 'is_st': False, 'delist': ['', '']},
    '002200.SZ': {'pe_ttm': 34.7585, 'pb': 2.3104, 'circ_mv': 134268.04, 'total_mv': 309472.16, 'bps': 2.9692, 'roe': 4.0528, 'profit_dedt': 28311914.57, 'industry': '建筑工程', 'is_st': False, 'delist': ['', '']},
    '002209.SZ': {'pe_ttm': 17.9572, 'pb': 2.8559, 'circ_mv': 208044.72, 'total_mv': 269570.67, 'bps': 4.615, 'roe': 13.3704, 'profit_dedt': 103463876.32, 'industry': '轻工机械', 'is_st': False, 'delist': ['', '']},
    '002247.SZ': {'pe_ttm': 52.7272, 'pb': 3.0718, 'circ_mv': 159577.61, 'total_mv': 211015.76, 'bps': 0.8073, 'roe': 2.2662, 'profit_dedt': 10753172.5, 'industry': '其他建材', 'is_st': False, 'delist': ['', '']},
    '002248.SZ': {'pe_ttm': 85.3509, 'pb': 23.5097, 'circ_mv': 281665.97, 'total_mv': 281665.97, 'bps': 0.3896, 'roe': 11.3562, 'profit_dedt': 10817809.61, 'industry': '机床制造', 'is_st': False, 'delist': ['', '']},
    '002278.SZ': {'pe_ttm': 89.5389, 'pb': 2.2033, 'circ_mv': 285980.42, 'total_mv': 309728.47, 'bps': 3.6173, 'roe': 0.9962, 'profit_dedt': 6966601.2, 'industry': '化工机械', 'is_st': False, 'delist': ['', '']},
    '002282.SZ': {'pe_ttm': 23.2871, 'pb': 0.889, 'circ_mv': 292886.53, 'total_mv': 319264.01, 'bps': 6.8164, 'roe': 2.2139, 'profit_dedt': 78306352.57, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '002337.SZ': {'pe_ttm': 81.3371, 'pb': 2.0762, 'circ_mv': 278037.72, 'total_mv': 278415.27, 'bps': 2.2782, 'roe': 1.8457, 'profit_dedt': 18408892.32, 'industry': '化工机械', 'is_st': False, 'delist': ['', '']},
    '002363.SZ': {'pe_ttm': 37.1545, 'pb': 1.3336, 'circ_mv': 288951.95, 'total_mv': 289189.71, 'bps': 5.2016, 'roe': 1.9589, 'profit_dedt': 39196516.48, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '002377.SZ': {'pe_ttm': 93.8735, 'pb': 6.7854, 'circ_mv': 258244.58, 'total_mv': 271232.26, 'bps': 0.4362, 'roe': 4.8054, 'profit_dedt': 19431818.79, 'industry': '其他建材', 'is_st': False, 'delist': ['', '']},
    '002381.SZ': {'pe_ttm': 59.8749, 'pb': 1.096, 'circ_mv': 181622.95, 'total_mv': 232129.94, 'bps': 5.1461, 'roe': 0.8127, 'profit_dedt': 4964005.42, 'industry': '橡胶', 'is_st': False, 'delist': ['', '']},
    '002391.SZ': {'pe_ttm': 63.9451, 'pb': 0.7991, 'circ_mv': 238546.53, 'total_mv': 333890.54, 'bps': 6.4324, 'roe': 1.2803, 'profit_dedt': 59011082.44, 'industry': '农药化肥', 'is_st': False, 'delist': ['', '']},
    '002397.SZ': {'pe_ttm': 46.5137, 'pb': 2.1454, 'circ_mv': 232721.1, 'total_mv': 268453.56, 'bps': 1.6733, 'roe': 2.7647, 'profit_dedt': 30590437.36, 'industry': '纺织', 'is_st': False, 'delist': ['', '']},
    '002398.SZ': {'pe_ttm': 63.5885, 'pb': 0.782, 'circ_mv': 255489.5, 'total_mv': 293206.94, 'bps': 5.3711, 'roe': 1.5547, 'profit_dedt': 38588895.02, 'industry': '其他建材', 'is_st': False, 'delist': ['', '']},
    '002404.SZ': {'pe_ttm': 19.1614, 'pb': 1.6621, 'circ_mv': 263900.73, 'total_mv': 324269.82, 'bps': 3.4835, 'roe': 5.1743, 'profit_dedt': 92006164.36, 'industry': '纺织', 'is_st': False, 'delist': ['', '']},
    '002420.SZ': {'pe_ttm': 12.581, 'pb': 2.9992, 'circ_mv': 245955.43, 'total_mv': 250263.07, 'bps': 2.0288, 'roe': 5.2208, 'profit_dedt': 38500080.81, 'industry': '家用电器', 'is_st': False, 'delist': ['', '']},
    '002442.SZ': {'pe_ttm': 100.6796, 'pb': 1.4412, 'circ_mv': 262958.28, 'total_mv': 269304.93, 'bps': 3.7123, 'roe': 1.9142, 'profit_dedt': 19519960.92, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '002476.SZ': {'pe_ttm': 82.9979, 'pb': 3.0336, 'circ_mv': 294984.0, 'total_mv': 294984.0, 'bps': 1.5889, 'roe': 2.0882, 'profit_dedt': 19259515.79, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '002486.SZ': {'pe_ttm': 64.0051, 'pb': 2.0325, 'circ_mv': 218234.38, 'total_mv': 219259.3, 'bps': 1.3087, 'roe': 1.639, 'profit_dedt': 18441978.66, 'industry': '服饰', 'is_st': False, 'delist': ['', '']},
    '002495.SZ': {'pe_ttm': 131.7518, 'pb': 2.0616, 'circ_mv': 168622.12, 'total_mv': 227357.02, 'bps': 1.1787, 'roe': 0.6433, 'profit_dedt': 6833416.47, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '002521.SZ': {'pe_ttm': 20.2442, 'pb': 0.8818, 'circ_mv': 259468.46, 'total_mv': 346677.82, 'bps': 7.0197, 'roe': 2.0191, 'profit_dedt': 70995746.97, 'industry': '造纸', 'is_st': False, 'delist': ['', '']},
    '002566.SZ': {'pe_ttm': 63.0356, 'pb': 1.0469, 'circ_mv': 163864.44, 'total_mv': 233651.83, 'bps': 6.7438, 'roe': 1.2023, 'profit_dedt': 6890181.99, 'industry': '中成药', 'is_st': False, 'delist': ['', '']},
    '002576.SZ': {'pe_ttm': 29.1056, 'pb': 1.819, 'circ_mv': 232995.69, 'total_mv': 237413.8, 'bps': 7.9055, 'roe': 3.1794, 'profit_dedt': 39685823.23, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '002587.SZ': {'pe_ttm': 212.9398, 'pb': 2.6479, 'circ_mv': 281805.02, 'total_mv': 346232.67, 'bps': 1.9978, 'roe': 1.0384, 'profit_dedt': 13103807.39, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '002593.SZ': {'pe_ttm': 23.8359, 'pb': 1.4797, 'circ_mv': 256150.47, 'total_mv': 367363.11, 'bps': 3.0884, 'roe': 3.5017, 'profit_dedt': 85116967.0, 'industry': '钢加工', 'is_st': False, 'delist': ['', '']},
    '002615.SZ': {'pe_ttm': 125.8085, 'pb': 1.4582, 'circ_mv': 188281.19, 'total_mv': 348762.75, 'bps': 4.2724, 'roe': 2.4094, 'profit_dedt': 46442966.76, 'industry': '家居用品', 'is_st': False, 'delist': ['', '']},
    '002616.SZ': {'pe_ttm': 13.2414, 'pb': 1.1679, 'circ_mv': 284340.47, 'total_mv': 415860.74, 'bps': 4.1442, 'roe': 4.302, 'profit_dedt': 154953353.15, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '002633.SZ': {'pe_ttm': 195.9377, 'pb': 6.4404, 'circ_mv': 227786.12, 'total_mv': 228450.0, 'bps': 2.3647, 'roe': 2.5123, 'profit_dedt': 9264952.88, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '002658.SZ': {'pe_ttm': 31.2982, 'pb': 1.7427, 'circ_mv': 255722.94, 'total_mv': 453297.52, 'bps': 4.0913, 'roe': 2.135, 'profit_dedt': 39635227.3, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '002666.SZ': {'pe_ttm': 78.3327, 'pb': 0.9313, 'circ_mv': 210834.19, 'total_mv': 331747.71, 'bps': 4.5203, 'roe': 1.5879, 'profit_dedt': 33663743.23, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '002671.SZ': {'pe_ttm': 157.4443, 'pb': 1.3851, 'circ_mv': 223866.82, 'total_mv': 228005.91, 'bps': 2.9226, 'roe': 0.2655, 'profit_dedt': 3592441.33, 'industry': '水泥', 'is_st': False, 'delist': ['', '']},
    '002687.SZ': {'pe_ttm': 34.1402, 'pb': 1.8484, 'circ_mv': 230798.06, 'total_mv': 281666.96, 'bps': 3.0188, 'roe': 0.7569, 'profit_dedt': 15092337.83, 'industry': '服饰', 'is_st': False, 'delist': ['', '']},
    '002700.SZ': {'pe_ttm': 33.4733, 'pb': 1.8455, 'circ_mv': 259536.84, 'total_mv': 262240.28, 'bps': 3.4354, 'roe': 2.4663, 'profit_dedt': 29227137.11, 'industry': '供气供热', 'is_st': False, 'delist': ['', '']},
    '002722.SZ': {'pe_ttm': 16.5022, 'pb': 0.9779, 'circ_mv': 291954.74, 'total_mv': 291974.44, 'bps': 13.427, 'roe': 2.8743, 'profit_dedt': 80162778.91, 'industry': '钢加工', 'is_st': False, 'delist': ['', '']},
    '002729.SZ': {'pe_ttm': 82.7606, 'pb': 4.3991, 'circ_mv': 239153.75, 'total_mv': 249387.97, 'bps': 3.0984, 'roe': 2.5024, 'profit_dedt': 13115416.79, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '002732.SZ': {'pe_ttm': 61.1567, 'pb': 1.4837, 'circ_mv': 224128.33, 'total_mv': 224538.45, 'bps': 9.6179, 'roe': 0.9287, 'profit_dedt': 13895988.29, 'industry': '乳制品', 'is_st': False, 'delist': ['', '']},
    '002748.SZ': {'pe_ttm': 24.7947, 'pb': 1.9705, 'circ_mv': 290160.0, 'total_mv': 290160.0, 'bps': 6.1356, 'roe': 6.9898, 'profit_dedt': 100254276.42, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '002753.SZ': {'pe_ttm': 90.5911, 'pb': 1.0829, 'circ_mv': 199361.72, 'total_mv': 288929.98, 'bps': 6.2238, 'roe': 1.2306, 'profit_dedt': 33055647.28, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '002760.SZ': {'pe_ttm': 94.285, 'pb': 2.3251, 'circ_mv': 194919.6, 'total_mv': 194919.6, 'bps': 7.7632, 'roe': 0.9328, 'profit_dedt': 7292081.9, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '002763.SZ': {'pe_ttm': 41.4235, 'pb': 1.8785, 'circ_mv': 223491.11, 'total_mv': 301704.06, 'bps': 3.9181, 'roe': 8.0833, 'profit_dedt': 132752697.15, 'industry': '服饰', 'is_st': False, 'delist': ['', '']},
    '002767.SZ': {'pe_ttm': 97.0654, 'pb': 3.1284, 'circ_mv': 236595.56, 'total_mv': 272400.0, 'bps': 5.8049, 'roe': 1.5492, 'profit_dedt': 11777655.54, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '002769.SZ': {'pe_ttm': 160.3427, 'pb': 2.1176, 'circ_mv': 250662.85, 'total_mv': 250869.7, 'bps': 3.1734, 'roe': 1.3675, 'profit_dedt': 14657761.02, 'industry': '仓储物流', 'is_st': False, 'delist': ['', '']},
    '002780.SZ': {'pe_ttm': 26.7198, 'pb': 2.8773, 'circ_mv': 192263.18, 'total_mv': 239774.46, 'bps': 5.0395, 'roe': 6.5548, 'profit_dedt': 43290822.82, 'industry': '服饰', 'is_st': False, 'delist': ['', '']},
    '002802.SZ': {'pe_ttm': 46.1381, 'pb': 3.4389, 'circ_mv': 185007.57, 'total_mv': 224019.93, 'bps': 3.5941, 'roe': 4.4442, 'profit_dedt': 23796293.73, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '002817.SZ': {'pe_ttm': 34.1418, 'pb': 2.0876, 'circ_mv': 197446.02, 'total_mv': 203386.78, 'bps': 3.2573, 'roe': 3.116, 'profit_dedt': 29685407.24, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '002823.SZ': {'pe_ttm': 18.5011, 'pb': 1.9971, 'circ_mv': 283706.5, 'total_mv': 416577.84, 'bps': 6.3524, 'roe': 5.0377, 'profit_dedt': 93958857.95, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '002825.SZ': {'pe_ttm': 43.9778, 'pb': 2.0046, 'circ_mv': 237700.64, 'total_mv': 315269.33, 'bps': 4.5995, 'roe': 2.877, 'profit_dedt': 38449708.97, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '002830.SZ': {'pe_ttm': 63.7747, 'pb': 4.1774, 'circ_mv': 138137.74, 'total_mv': 276613.25, 'bps': 3.562, 'roe': 1.2253, 'profit_dedt': 8311420.18, 'industry': '装修装饰', 'is_st': False, 'delist': ['', '']},
    '002835.SZ': {'pe_ttm': 26.0189, 'pb': 2.3344, 'circ_mv': 168111.69, 'total_mv': 289512.59, 'bps': 5.6503, 'roe': 4.4269, 'profit_dedt': 51297072.27, 'industry': 'IT设备', 'is_st': False, 'delist': ['', '']},
    '002836.SZ': {'pe_ttm': 77.9937, 'pb': 7.7442, 'circ_mv': 279244.8, 'total_mv': 279244.8, 'bps': 1.565, 'roe': 6.7552, 'profit_dedt': 24373089.83, 'industry': '广告包装', 'is_st': False, 'delist': ['', '']},
    '002852.SZ': {'pe_ttm': 43.039, 'pb': 1.3563, 'circ_mv': 248376.45, 'total_mv': 298908.45, 'bps': 6.4069, 'roe': 0.7579, 'profit_dedt': 30226766.64, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '002863.SZ': {'pe_ttm': 44.4599, 'pb': 1.0098, 'circ_mv': 285354.19, 'total_mv': 285354.19, 'bps': 4.7136, 'roe': 1.7512, 'profit_dedt': 29641396.96, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '002864.SZ': {'pe_ttm': 36.6472, 'pb': 1.6582, 'circ_mv': 207546.11, 'total_mv': 287755.45, 'bps': 16.3277, 'roe': 2.4766, 'profit_dedt': 38488160.97, 'industry': '中成药', 'is_st': False, 'delist': ['', '']},
    '002871.SZ': {'pe_ttm': 34.3751, 'pb': 3.8431, 'circ_mv': 252054.18, 'total_mv': 427647.92, 'bps': 4.4183, 'roe': 4.3589, 'profit_dedt': 43532988.16, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '002873.SZ': {'pe_ttm': 254.3109, 'pb': 1.6324, 'circ_mv': 204935.38, 'total_mv': 208708.75, 'bps': 5.2377, 'roe': 0.3836, 'profit_dedt': 3984397.54, 'industry': '中成药', 'is_st': False, 'delist': ['', '']},
    '002877.SZ': {'pe_ttm': 162.9389, 'pb': 2.1727, 'circ_mv': 179423.97, 'total_mv': 265459.4, 'bps': 3.4335, 'roe': 1.1061, 'profit_dedt': 12969052.81, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '002879.SZ': {'pe_ttm': 26.9587, 'pb': 1.7604, 'circ_mv': 231490.67, 'total_mv': 325965.7, 'bps': 9.5886, 'roe': 0.9734, 'profit_dedt': 15491817.14, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '002887.SZ': {'pe_ttm': 25.0734, 'pb': 1.1648, 'circ_mv': 189642.92, 'total_mv': 267127.35, 'bps': 7.4952, 'roe': 1.8409, 'profit_dedt': 42939431.84, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '002888.SZ': {'pe_ttm': 136.8006, 'pb': 7.4173, 'circ_mv': 139356.78, 'total_mv': 274616.72, 'bps': 2.5077, 'roe': 2.0522, 'profit_dedt': 6800766.49, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '002890.SZ': {'pe_ttm': 236.3457, 'pb': 4.2509, 'circ_mv': 244314.25, 'total_mv': 257021.18, 'bps': 3.5592, 'roe': 1.4748, 'profit_dedt': 8101134.42, 'industry': '农用机械', 'is_st': False, 'delist': ['', '']},
    '002893.SZ': {'pe_ttm': 43.1873, 'pb': 2.1388, 'circ_mv': 217175.88, 'total_mv': 282358.44, 'bps': 5.0074, 'roe': 9.8014, 'profit_dedt': 124985005.75, 'industry': '供气供热', 'is_st': False, 'delist': ['', '']},
    '002917.SZ': {'pe_ttm': 19.7808, 'pb': 1.9361, 'circ_mv': 261328.34, 'total_mv': 348657.04, 'bps': 5.1805, 'roe': 5.0384, 'profit_dedt': 87557924.63, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '002918.SZ': {'pe_ttm': 40.4943, 'pb': 1.3353, 'circ_mv': 227914.12, 'total_mv': 434984.37, 'bps': 7.9683, 'roe': 1.4908, 'profit_dedt': 50143647.33, 'industry': '陶瓷', 'is_st': False, 'delist': ['', '']},
    '002923.SZ': {'pe_ttm': 274.1877, 'pb': 3.423, 'circ_mv': 284644.7, 'total_mv': 369387.31, 'bps': 3.2224, 'roe': 3.8489, 'profit_dedt': 33118125.77, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '002942.SZ': {'pe_ttm': 24.7865, 'pb': 2.0718, 'circ_mv': 236721.32, 'total_mv': 265250.07, 'bps': 8.2345, 'roe': 8.1671, 'profit_dedt': 95516467.82, 'industry': '农药化肥', 'is_st': False, 'delist': ['', '']},
    '002949.SZ': {'pe_ttm': 28.4345, 'pb': 1.7657, 'circ_mv': 199630.24, 'total_mv': 257659.5, 'bps': 7.4434, 'roe': 2.22, 'profit_dedt': 24755539.35, 'industry': '建筑工程', 'is_st': False, 'delist': ['', '']},
    '002952.SZ': {'pe_ttm': 157.6997, 'pb': 3.5574, 'circ_mv': 255412.3, 'total_mv': 313067.7, 'bps': 5.355, 'roe': 1.6627, 'profit_dedt': 11556389.12, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '002953.SZ': {'pe_ttm': 37.8692, 'pb': 2.1852, 'circ_mv': 277632.05, 'total_mv': 485189.16, 'bps': 4.5168, 'roe': 3.7364, 'profit_dedt': 81485303.31, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '002968.SZ': {'pe_ttm': 23.8721, 'pb': 1.7858, 'circ_mv': 216124.26, 'total_mv': 229445.69, 'bps': 5.678, 'roe': 5.801, 'profit_dedt': 75276495.28, 'industry': '房产服务', 'is_st': False, 'delist': ['', '']},
    '002972.SZ': {'pe_ttm': 38.672, 'pb': 2.5888, 'circ_mv': 180035.18, 'total_mv': 319811.44, 'bps': 5.0216, 'roe': 3.8119, 'profit_dedt': 43898663.12, 'industry': '运输设备', 'is_st': False, 'delist': ['', '']},
    '002981.SZ': {'pe_ttm': 110.387, 'pb': 3.0366, 'circ_mv': 282439.87, 'total_mv': 317225.89, 'bps': 7.5984, 'roe': 0.7019, 'profit_dedt': 6510837.01, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '002983.SZ': {'pe_ttm': 43.7594, 'pb': 3.5509, 'circ_mv': 279105.36, 'total_mv': 480095.66, 'bps': 6.0598, 'roe': 3.749, 'profit_dedt': 47100130.45, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '002991.SZ': {'pe_ttm': 16.8076, 'pb': 2.1141, 'circ_mv': 192594.28, 'total_mv': 359533.34, 'bps': 18.2441, 'roe': 4.6916, 'profit_dedt': 67842755.21, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '002996.SZ': {'pe_ttm': 17.1166, 'pb': 1.205, 'circ_mv': 272734.61, 'total_mv': 398314.9, 'bps': 4.9379, 'roe': 5.7833, 'profit_dedt': 139062538.25, 'industry': '铝', 'is_st': False, 'delist': ['', '']},
    '002998.SZ': {'pe_ttm': 29.9663, 'pb': 1.6124, 'circ_mv': 215045.31, 'total_mv': 282082.51, 'bps': 4.9305, 'roe': 4.6199, 'profit_dedt': 75896297.84, 'industry': '化纤', 'is_st': False, 'delist': ['', '']},
    '002999.SZ': {'pe_ttm': 32.829, 'pb': 1.7985, 'circ_mv': 219236.32, 'total_mv': 221068.51, 'bps': 3.5363, 'roe': 2.4913, 'profit_dedt': 14784038.11, 'industry': '批发业', 'is_st': False, 'delist': ['', '']},
    '003000.SZ': {'pe_ttm': 17.8614, 'pb': 3.0679, 'circ_mv': 286340.81, 'total_mv': 423604.19, 'bps': 3.0623, 'roe': 7.6499, 'profit_dedt': 96485507.4, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '003002.SZ': {'pe_ttm': 27.7837, 'pb': 2.4302, 'circ_mv': 285594.16, 'total_mv': 379012.83, 'bps': 7.7981, 'roe': 3.8906, 'profit_dedt': 53488278.38, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '003003.SZ': {'pe_ttm': 35.1674, 'pb': 1.5251, 'circ_mv': 131970.29, 'total_mv': 197572.96, 'bps': 7.3309, 'roe': 2.2676, 'profit_dedt': 25166394.36, 'industry': '广告包装', 'is_st': False, 'delist': ['', '']},
    '003008.SZ': {'pe_ttm': 25.7514, 'pb': 1.9792, 'circ_mv': 159156.68, 'total_mv': 206336.0, 'bps': 10.0243, 'roe': 3.11, 'profit_dedt': 26730667.51, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '003011.SZ': {'pe_ttm': 28.1943, 'pb': 1.4598, 'circ_mv': 154068.81, 'total_mv': 202579.75, 'bps': 13.5153, 'roe': 2.6693, 'profit_dedt': 35337572.97, 'industry': '家居用品', 'is_st': False, 'delist': ['', '']},
    '003015.SZ': {'pe_ttm': 36.1751, 'pb': 2.9841, 'circ_mv': 297933.62, 'total_mv': 338966.44, 'bps': 4.0414, 'roe': 3.0649, 'profit_dedt': 33746917.96, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '003017.SZ': {'pe_ttm': 21.355, 'pb': 1.9468, 'circ_mv': 188917.73, 'total_mv': 228110.4, 'bps': 11.6241, 'roe': 4.9901, 'profit_dedt': 53740430.05, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '003020.SZ': {'pe_ttm': 24.1404, 'pb': 2.1407, 'circ_mv': 290914.37, 'total_mv': 393905.48, 'bps': 8.0629, 'roe': 4.8344, 'profit_dedt': 54257968.1, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '003023.SZ': {'pe_ttm': 93.8854, 'pb': 1.7199, 'circ_mv': 266795.32, 'total_mv': 267356.98, 'bps': 14.757, 'roe': 1.5015, 'profit_dedt': 22458561.59, 'industry': '家用电器', 'is_st': False, 'delist': ['', '']},
    '003025.SZ': {'pe_ttm': 22.7655, 'pb': 2.713, 'circ_mv': 274189.65, 'total_mv': 343867.69, 'bps': 4.4637, 'roe': 5.398, 'profit_dedt': 65811034.78, 'industry': '机床制造', 'is_st': False, 'delist': ['', '']},
    '003027.SZ': {'pe_ttm': 55.0933, 'pb': 1.2796, 'circ_mv': 171901.33, 'total_mv': 218569.19, 'bps': 13.0663, 'roe': 1.0593, 'profit_dedt': 15068400.04, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '003028.SZ': {'pe_ttm': 91.0916, 'pb': 1.9578, 'circ_mv': 165988.57, 'total_mv': 338393.99, 'bps': 11.9439, 'roe': 0.9623, 'profit_dedt': 8098255.09, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '300030.SZ': {'pe_ttm': 71.1325, 'pb': 3.2855, 'circ_mv': 188381.5, 'total_mv': 213957.61, 'bps': 2.1063, 'roe': 4.8468, 'profit_dedt': 24229883.38, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '300040.SZ': {'pe_ttm': 170.1574, 'pb': 1.4061, 'circ_mv': 295563.63, 'total_mv': 365457.11, 'bps': 4.0394, 'roe': 0.9008, 'profit_dedt': 21016607.71, 'industry': '新型电力', 'is_st': False, 'delist': ['', '']},
    '300092.SZ': {'pe_ttm': 59.3716, 'pb': 1.9179, 'circ_mv': 242177.06, 'total_mv': 316107.49, 'bps': 6.0172, 'roe': 2.5247, 'profit_dedt': 37539186.58, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '300106.SZ': {'pe_ttm': 94.7418, 'pb': 5.5947, 'circ_mv': 225280.23, 'total_mv': 225280.23, 'bps': 1.9054, 'roe': 7.9658, 'profit_dedt': 10234552.44, 'industry': '乳制品', 'is_st': False, 'delist': ['', '']},
    '300107.SZ': {'pe_ttm': 271.2618, 'pb': 2.6336, 'circ_mv': 247248.19, 'total_mv': 400138.22, 'bps': 2.6998, 'roe': 3.2024, 'profit_dedt': 47505018.29, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '300148.SZ': {'pe_ttm': 69.3267, 'pb': 2.8327, 'circ_mv': 287364.06, 'total_mv': 297635.23, 'bps': 1.2461, 'roe': 2.5968, 'profit_dedt': 24876933.25, 'industry': '出版业', 'is_st': False, 'delist': ['', '']},
    '300172.SZ': {'pe_ttm': 33.4396, 'pb': 1.5937, 'circ_mv': 237674.63, 'total_mv': 307226.34, 'bps': 2.8487, 'roe': 3.3232, 'profit_dedt': 53482567.71, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '300187.SZ': {'pe_ttm': 22.7666, 'pb': 2.464, 'circ_mv': 289712.48, 'total_mv': 291175.57, 'bps': 1.8304, 'roe': 7.6004, 'profit_dedt': 68279026.17, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '300218.SZ': {'pe_ttm': 38.1647, 'pb': 1.8394, 'circ_mv': 271643.16, 'total_mv': 272535.67, 'bps': 6.8285, 'roe': 2.1856, 'profit_dedt': 32999383.21, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '300230.SZ': {'pe_ttm': 20.4646, 'pb': 1.1116, 'circ_mv': 273972.79, 'total_mv': 353747.83, 'bps': 3.9132, 'roe': 3.5568, 'profit_dedt': 109030473.17, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '300235.SZ': {'pe_ttm': 136.7911, 'pb': 4.1342, 'circ_mv': 237771.76, 'total_mv': 292277.8, 'bps': 2.8083, 'roe': 0.8178, 'profit_dedt': 3269735.75, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '300240.SZ': {'pe_ttm': 511.8146, 'pb': 1.5818, 'circ_mv': 223453.18, 'total_mv': 228511.18, 'bps': 3.8879, 'roe': 1.712, 'profit_dedt': 23258222.63, 'industry': '仓储物流', 'is_st': False, 'delist': ['', '']},
    '300247.SZ': {'pe_ttm': 41.3868, 'pb': 2.517, 'circ_mv': 295886.83, 'total_mv': 295886.83, 'bps': 1.4621, 'roe': 3.8218, 'profit_dedt': 20471416.33, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '300250.SZ': {'pe_ttm': 78.9289, 'pb': 5.5638, 'circ_mv': 228350.52, 'total_mv': 309723.03, 'bps': 2.6043, 'roe': 0.3346, 'profit_dedt': 1244534.73, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '300254.SZ': {'pe_ttm': 82.2005, 'pb': 5.2561, 'circ_mv': 211836.94, 'total_mv': 236374.7, 'bps': 1.7563, 'roe': 1.1363, 'profit_dedt': 5325564.39, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '300272.SZ': {'pe_ttm': 125.5732, 'pb': 2.9255, 'circ_mv': 281470.04, 'total_mv': 370550.5, 'bps': 2.0715, 'roe': 3.1973, 'profit_dedt': 25213995.42, 'industry': '家用电器', 'is_st': False, 'delist': ['', '']},
    '300275.SZ': {'pe_ttm': 71.5903, 'pb': 2.8433, 'circ_mv': 240514.89, 'total_mv': 272892.52, 'bps': 3.1161, 'roe': 2.7387, 'profit_dedt': 25810338.33, 'industry': '工程机械', 'is_st': False, 'delist': ['', '']},
    '300279.SZ': {'pe_ttm': 77.0368, 'pb': 2.711, 'circ_mv': 284118.05, 'total_mv': 285145.01, 'bps': 2.1505, 'roe': 1.5504, 'profit_dedt': 12170516.15, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '300306.SZ': {'pe_ttm': 39.8055, 'pb': 1.9308, 'circ_mv': 163957.14, 'total_mv': 279717.05, 'bps': 5.3865, 'roe': 2.2123, 'profit_dedt': 24990023.43, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '300314.SZ': {'pe_ttm': 61.5591, 'pb': 3.006, 'circ_mv': 184851.44, 'total_mv': 370368.0, 'bps': 4.2781, 'roe': 1.9543, 'profit_dedt': 16291601.48, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '300321.SZ': {'pe_ttm': 396.5764, 'pb': 4.5144, 'circ_mv': 286394.29, 'total_mv': 286646.4, 'bps': 7.1505, 'roe': 0.6923, 'profit_dedt': 4271528.49, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '300335.SZ': {'pe_ttm': 65.8974, 'pb': 1.3882, 'circ_mv': 213977.45, 'total_mv': 265695.91, 'bps': 4.0124, 'roe': 2.4524, 'profit_dedt': 43216324.17, 'industry': '供气供热', 'is_st': False, 'delist': ['', '']},
    '300354.SZ': {'pe_ttm': 37.5787, 'pb': 4.2965, 'circ_mv': 225445.69, 'total_mv': 373741.45, 'bps': 6.2888, 'roe': 4.5034, 'profit_dedt': 39111512.61, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '300371.SZ': {'pe_ttm': 50.3396, 'pb': 1.7592, 'circ_mv': 141675.65, 'total_mv': 203631.91, 'bps': 5.7525, 'roe': 1.358, 'profit_dedt': 11487945.05, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '300384.SZ': {'pe_ttm': 24.3122, 'pb': 1.3641, 'circ_mv': 264678.54, 'total_mv': 383127.65, 'bps': 8.8041, 'roe': 2.7604, 'profit_dedt': 68009730.66, 'industry': '建筑工程', 'is_st': False, 'delist': ['', '']},
    '300387.SZ': {'pe_ttm': 40.3941, 'pb': 1.4837, 'circ_mv': 214694.75, 'total_mv': 214769.35, 'bps': 5.0079, 'roe': 3.7959, 'profit_dedt': 52721949.11, 'industry': '农药化肥', 'is_st': False, 'delist': ['', '']},
    '300407.SZ': {'pe_ttm': 55.9699, 'pb': 1.6856, 'circ_mv': 270607.3, 'total_mv': 366532.67, 'bps': 6.2765, 'roe': 0.7483, 'profit_dedt': 6912828.9, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '300411.SZ': {'pe_ttm': 19570.9234, 'pb': 3.4688, 'circ_mv': 254711.17, 'total_mv': 334972.48, 'bps': 2.3754, 'roe': 0.3591, 'profit_dedt': 1383667.63, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '300425.SZ': {'pe_ttm': 227.4545, 'pb': 1.1663, 'circ_mv': 275699.05, 'total_mv': 276301.08, 'bps': 3.4725, 'roe': 0.3013, 'profit_dedt': 3488824.09, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '300439.SZ': {'pe_ttm': 57.5904, 'pb': 1.31, 'circ_mv': 299640.4, 'total_mv': 393440.0, 'bps': 7.8147, 'roe': 2.0797, 'profit_dedt': 44239100.95, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '300445.SZ': {'pe_ttm': 31.2277, 'pb': 2.9398, 'circ_mv': 257059.39, 'total_mv': 391296.06, 'bps': 6.2658, 'roe': 5.7427, 'profit_dedt': 71310848.47, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '300447.SZ': {'pe_ttm': 131.8045, 'pb': 2.2228, 'circ_mv': 287473.15, 'total_mv': 445666.94, 'bps': 6.4198, 'roe': 1.3088, 'profit_dedt': 23862203.1, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '300452.SZ': {'pe_ttm': 17.2998, 'pb': 3.0945, 'circ_mv': 263382.98, 'total_mv': 310889.05, 'bps': 4.285, 'roe': 9.4242, 'profit_dedt': 86705204.41, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '300484.SZ': {'pe_ttm': 97.533, 'pb': 4.0116, 'circ_mv': 223651.96, 'total_mv': 269727.4, 'bps': 3.2456, 'roe': 2.5511, 'profit_dedt': 15696234.08, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '300508.SZ': {'pe_ttm': 61.407, 'pb': 4.5829, 'circ_mv': 299277.9, 'total_mv': 396608.88, 'bps': 7.9508, 'roe': 0.698, 'profit_dedt': 13093935.92, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '300509.SZ': {'pe_ttm': 18.8757, 'pb': 3.634, 'circ_mv': 216709.77, 'total_mv': 259942.8, 'bps': 2.4133, 'roe': 8.0339, 'profit_dedt': 55831843.24, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '300511.SZ': {'pe_ttm': 20.7783, 'pb': 1.9087, 'circ_mv': 277442.78, 'total_mv': 312092.49, 'bps': 2.5515, 'roe': 1.0997, 'profit_dedt': 10066484.54, 'industry': '种植业', 'is_st': False, 'delist': ['', '']},
    '300512.SZ': {'pe_ttm': 103.8908, 'pb': 1.8536, 'circ_mv': 242790.04, 'total_mv': 304472.63, 'bps': 4.0137, 'roe': 2.1509, 'profit_dedt': 33287917.17, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '300519.SZ': {'pe_ttm': 38.2354, 'pb': 2.8602, 'circ_mv': 169096.2, 'total_mv': 237120.0, 'bps': 5.1815, 'roe': 4.7678, 'profit_dedt': 36209446.74, 'industry': '中成药', 'is_st': False, 'delist': ['', '']},
    '300537.SZ': {'pe_ttm': 273.056, 'pb': 4.4992, 'circ_mv': 299730.15, 'total_mv': 409238.37, 'bps': 4.377, 'roe': 1.6416, 'profit_dedt': 12927775.43, 'industry': '染料涂料', 'is_st': False, 'delist': ['', '']},
    '300549.SZ': {'pe_ttm': 297.8273, 'pb': 3.621, 'circ_mv': 151755.34, 'total_mv': 198276.58, 'bps': 4.1066, 'roe': 0.611, 'profit_dedt': 870021.43, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '300562.SZ': {'pe_ttm': 32.3736, 'pb': 2.8705, 'circ_mv': 246736.2, 'total_mv': 322913.18, 'bps': 5.1459, 'roe': 5.5865, 'profit_dedt': 58963453.62, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '300577.SZ': {'pe_ttm': 12.482, 'pb': 2.0845, 'circ_mv': 263754.87, 'total_mv': 443696.97, 'bps': 8.8751, 'roe': 9.8716, 'profit_dedt': 210126064.3, 'industry': '纺织', 'is_st': False, 'delist': ['', '']},
    '300583.SZ': {'pe_ttm': 266.0917, 'pb': 1.4754, 'circ_mv': 255023.99, 'total_mv': 259891.88, 'bps': 9.2854, 'roe': 1.7157, 'profit_dedt': 29278388.88, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '300584.SZ': {'pe_ttm': 83.2102, 'pb': 3.4856, 'circ_mv': 256086.02, 'total_mv': 374160.0, 'bps': 8.9454, 'roe': 3.0154, 'profit_dedt': 36257359.28, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '300610.SZ': {'pe_ttm': 31.8514, 'pb': 1.8675, 'circ_mv': 172531.86, 'total_mv': 220994.97, 'bps': 5.5797, 'roe': 4.7535, 'profit_dedt': 41586703.84, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '300631.SZ': {'pe_ttm': 37.9857, 'pb': 2.0154, 'circ_mv': 267409.61, 'total_mv': 274911.06, 'bps': 10.9099, 'roe': 2.7676, 'profit_dedt': 30040822.3, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '300636.SZ': {'pe_ttm': 76.0022, 'pb': 1.2547, 'circ_mv': 260423.0, 'total_mv': 297745.61, 'bps': 5.6507, 'roe': 1.0371, 'profit_dedt': 19926972.08, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '300642.SZ': {'pe_ttm': 274.8243, 'pb': 2.3529, 'circ_mv': 286978.47, 'total_mv': 338175.47, 'bps': 8.8573, 'roe': 0.8015, 'profit_dedt': 5952393.12, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '300644.SZ': {'pe_ttm': 22.9188, 'pb': 2.139, 'circ_mv': 182596.09, 'total_mv': 228966.74, 'bps': 9.6713, 'roe': 2.2603, 'profit_dedt': 22893545.71, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '300650.SZ': {'pe_ttm': 85.5813, 'pb': 2.0288, 'circ_mv': 204073.67, 'total_mv': 289200.31, 'bps': 5.905, 'roe': 0.9044, 'profit_dedt': 11676233.06, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '300653.SZ': {'pe_ttm': 41.2522, 'pb': 3.2864, 'circ_mv': 286920.0, 'total_mv': 286920.0, 'bps': 4.8503, 'roe': 3.7794, 'profit_dedt': 27157036.26, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '300658.SZ': {'pe_ttm': 56.8528, 'pb': 2.5448, 'circ_mv': 245653.95, 'total_mv': 341415.97, 'bps': 4.0317, 'roe': 3.4041, 'profit_dedt': 39433282.8, 'industry': '纺织', 'is_st': False, 'delist': ['', '']},
    '300668.SZ': {'pe_ttm': 220.1624, 'pb': 4.5358, 'circ_mv': 298184.45, 'total_mv': 360060.47, 'bps': 6.5942, 'roe': 0.1146, 'profit_dedt': 215318.06, 'industry': '装修装饰', 'is_st': False, 'delist': ['', '']},
    '300673.SZ': {'pe_ttm': 60.7229, 'pb': 2.1937, 'circ_mv': 239889.64, 'total_mv': 369722.32, 'bps': 6.8149, 'roe': 1.3093, 'profit_dedt': 24164337.19, 'industry': '饲料', 'is_st': False, 'delist': ['', '']},
    '300690.SZ': {'pe_ttm': 22.6478, 'pb': 2.6395, 'circ_mv': 296580.33, 'total_mv': 439497.43, 'bps': 10.0702, 'roe': 8.2271, 'profit_dedt': 75413694.51, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '300694.SZ': {'pe_ttm': 16.953, 'pb': 1.5324, 'circ_mv': 230900.41, 'total_mv': 231179.98, 'bps': 6.95, 'roe': 1.8231, 'profit_dedt': 26997854.12, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '300701.SZ': {'pe_ttm': 33.6012, 'pb': 2.8279, 'circ_mv': 248119.24, 'total_mv': 275119.81, 'bps': 3.441, 'roe': 3.9336, 'profit_dedt': 20741170.53, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '300717.SZ': {'pe_ttm': 42.7245, 'pb': 2.8824, 'circ_mv': 211855.07, 'total_mv': 213772.0, 'bps': 7.2163, 'roe': 3.1896, 'profit_dedt': 22685425.4, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '300743.SZ': {'pe_ttm': 19.2935, 'pb': 3.5882, 'circ_mv': 183801.14, 'total_mv': 217885.4, 'bps': 4.0159, 'roe': 10.7929, 'profit_dedt': 44231952.22, 'industry': 'IT设备', 'is_st': False, 'delist': ['', '']},
    '300752.SZ': {'pe_ttm': 101.4205, 'pb': 3.1987, 'circ_mv': 282432.12, 'total_mv': 408429.92, 'bps': 3.9548, 'roe': 1.8832, 'profit_dedt': 18884708.46, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '300771.SZ': {'pe_ttm': 23.7792, 'pb': 1.3669, 'circ_mv': 214035.5, 'total_mv': 271680.0, 'bps': 8.2814, 'roe': 2.8585, 'profit_dedt': 16893759.67, 'industry': 'IT设备', 'is_st': False, 'delist': ['', '']},
    '300784.SZ': {'pe_ttm': 60.685, 'pb': 2.9354, 'circ_mv': 71700.67, 'total_mv': 238110.0, 'bps': 14.4237, 'roe': 2.0543, 'profit_dedt': 14380842.74, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '300800.SZ': {'pe_ttm': 51.811, 'pb': 1.2288, 'circ_mv': 248570.23, 'total_mv': 251201.3, 'bps': 8.6346, 'roe': 1.0269, 'profit_dedt': 10919454.63, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '300801.SZ': {'pe_ttm': 29.4612, 'pb': 1.7506, 'circ_mv': 293641.38, 'total_mv': 466732.29, 'bps': 12.2057, 'roe': 5.4208, 'profit_dedt': 134333525.66, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '300804.SZ': {'pe_ttm': 273.2431, 'pb': 3.0152, 'circ_mv': 152377.5, 'total_mv': 410034.0, 'bps': 18.3771, 'roe': 0.2425, 'profit_dedt': 2804241.55, 'industry': '农药化肥', 'is_st': False, 'delist': ['', '']},
    '300805.SZ': {'pe_ttm': 117.3148, 'pb': 2.3502, 'circ_mv': 260298.28, 'total_mv': 367991.3, 'bps': 3.689, 'roe': 1.2509, 'profit_dedt': 18324247.67, 'industry': '广告包装', 'is_st': False, 'delist': ['', '']},
    '300813.SZ': {'pe_ttm': 118.6764, 'pb': 3.4993, 'circ_mv': 194342.89, 'total_mv': 276697.77, 'bps': 6.5242, 'roe': 2.5546, 'profit_dedt': 18252035.87, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '300817.SZ': {'pe_ttm': 48.3506, 'pb': 3.5588, 'circ_mv': 216697.52, 'total_mv': 375036.48, 'bps': 4.8275, 'roe': 4.2276, 'profit_dedt': 41993301.9, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '300823.SZ': {'pe_ttm': 35.1978, 'pb': 2.0267, 'circ_mv': 127528.67, 'total_mv': 192544.72, 'bps': 7.2533, 'roe': 2.6855, 'profit_dedt': 22443225.8, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '300824.SZ': {'pe_ttm': 25.3413, 'pb': 3.5043, 'circ_mv': 260915.99, 'total_mv': 266947.51, 'bps': 2.3343, 'roe': 6.2951, 'profit_dedt': 45580426.72, 'industry': '家用电器', 'is_st': False, 'delist': ['', '']},
    '300828.SZ': {'pe_ttm': 104.4518, 'pb': 3.6452, 'circ_mv': 163636.88, 'total_mv': 287244.37, 'bps': 4.735, 'roe': 1.939, 'profit_dedt': 15669447.37, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '300837.SZ': {'pe_ttm': 37.4652, 'pb': 1.6133, 'circ_mv': 168802.07, 'total_mv': 223315.18, 'bps': 13.8413, 'roe': 1.2249, 'profit_dedt': 16461746.83, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '300848.SZ': {'pe_ttm': 68.0602, 'pb': 3.0394, 'circ_mv': 276409.77, 'total_mv': 467253.26, 'bps': 3.5929, 'roe': 1.6839, 'profit_dedt': 18115971.63, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '300851.SZ': {'pe_ttm': 26.7739, 'pb': 1.6768, 'circ_mv': 141545.42, 'total_mv': 217159.63, 'bps': 14.897, 'roe': 1.4254, 'profit_dedt': 16685817.02, 'industry': '运输设备', 'is_st': False, 'delist': ['', '']},
    '300858.SZ': {'pe_ttm': 50.8096, 'pb': 2.2232, 'circ_mv': 295959.18, 'total_mv': 398404.74, 'bps': 6.801, 'roe': 1.3809, 'profit_dedt': 18681720.57, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '300865.SZ': {'pe_ttm': 121.4688, 'pb': 3.047, 'circ_mv': 175673.46, 'total_mv': 270487.36, 'bps': 9.278, 'roe': 3.2727, 'profit_dedt': 25358099.23, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '300881.SZ': {'pe_ttm': 14.5578, 'pb': 2.5947, 'circ_mv': 169980.05, 'total_mv': 325710.3, 'bps': 11.4118, 'roe': 4.7729, 'profit_dedt': 59821525.6, 'industry': '钢加工', 'is_st': False, 'delist': ['', '']},
    '300886.SZ': {'pe_ttm': 110.9916, 'pb': 2.9716, 'circ_mv': 111883.44, 'total_mv': 178180.13, 'bps': 7.9821, 'roe': 1.1485, 'profit_dedt': 7463449.81, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '300897.SZ': {'pe_ttm': 766.3985, 'pb': 3.3119, 'circ_mv': 198193.22, 'total_mv': 322167.16, 'bps': 4.967, 'roe': 0.8125, 'profit_dedt': 7257565.12, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '300898.SZ': {'pe_ttm': 24.9251, 'pb': 2.5818, 'circ_mv': 236027.38, 'total_mv': 255068.0, 'bps': 7.9672, 'roe': 3.5539, 'profit_dedt': 28729308.86, 'industry': '乳制品', 'is_st': False, 'delist': ['', '']},
    '300899.SZ': {'pe_ttm': 329.3315, 'pb': 3.0492, 'circ_mv': 131326.86, 'total_mv': 193327.49, 'bps': 9.9404, 'roe': 1.6195, 'profit_dedt': 9820233.15, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '300906.SZ': {'pe_ttm': 40.5357, 'pb': 2.4072, 'circ_mv': 202019.46, 'total_mv': 223680.0, 'bps': 11.6153, 'roe': 2.127, 'profit_dedt': 16096403.95, 'industry': '运输设备', 'is_st': False, 'delist': ['', '']},
    '300920.SZ': {'pe_ttm': 267.7203, 'pb': 3.4195, 'circ_mv': 267148.34, 'total_mv': 402220.0, 'bps': 9.0482, 'roe': 0.262, 'profit_dedt': 3147322.21, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '300921.SZ': {'pe_ttm': 116.8518, 'pb': 4.9375, 'circ_mv': 256781.49, 'total_mv': 407198.36, 'bps': 3.2121, 'roe': 2.9103, 'profit_dedt': 18028526.01, 'industry': '互联网', 'is_st': False, 'delist': ['', '']},
    '300922.SZ': {'pe_ttm': 77.8443, 'pb': 2.7547, 'circ_mv': 213574.56, 'total_mv': 272162.26, 'bps': 6.1422, 'roe': 0.9242, 'profit_dedt': 2011090.68, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '300923.SZ': {'pe_ttm': 76.8856, 'pb': 1.6822, 'circ_mv': 183534.09, 'total_mv': 188011.44, 'bps': 14.2194, 'roe': 0.8169, 'profit_dedt': 7809311.17, 'industry': '运输设备', 'is_st': False, 'delist': ['', '']},
    '300930.SZ': {'pe_ttm': 41.5108, 'pb': 2.6821, 'circ_mv': 120074.5, 'total_mv': 254800.0, 'bps': 9.5, 'roe': 3.4419, 'profit_dedt': 29443378.67, 'industry': '小金属', 'is_st': False, 'delist': ['', '']},
    '300940.SZ': {'pe_ttm': 131.9005, 'pb': 2.5326, 'circ_mv': 210395.55, 'total_mv': 297452.92, 'bps': 5.2752, 'roe': 0.5904, 'profit_dedt': 4715588.82, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '300945.SZ': {'pe_ttm': 24.7411, 'pb': 1.6956, 'circ_mv': 267109.55, 'total_mv': 293258.12, 'bps': 6.5993, 'roe': 4.7347, 'profit_dedt': 73383163.98, 'industry': '服饰', 'is_st': False, 'delist': ['', '']},
    '300946.SZ': {'pe_ttm': 246.2211, 'pb': 4.7194, 'circ_mv': 274468.18, 'total_mv': 564904.24, 'bps': 7.6727, 'roe': 2.4562, 'profit_dedt': 28412247.77, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '300950.SZ': {'pe_ttm': 56.4392, 'pb': 3.7401, 'circ_mv': 193906.68, 'total_mv': 305136.98, 'bps': 5.31, 'roe': 1.3494, 'profit_dedt': 10214363.26, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '300952.SZ': {'pe_ttm': 58.3667, 'pb': 2.2924, 'circ_mv': 245005.45, 'total_mv': 397918.54, 'bps': 7.1839, 'roe': 1.2343, 'profit_dedt': 12972480.42, 'industry': '纺织', 'is_st': False, 'delist': ['', '']},
    '300960.SZ': {'pe_ttm': 63.3706, 'pb': 4.28, 'circ_mv': 254606.14, 'total_mv': 280856.35, 'bps': 4.543, 'roe': 1.6016, 'profit_dedt': 8132340.66, 'industry': '运输设备', 'is_st': False, 'delist': ['', '']},
    '300970.SZ': {'pe_ttm': 13.2835, 'pb': 1.7122, 'circ_mv': 215850.57, 'total_mv': 279032.13, 'bps': 10.2698, 'roe': 2.8223, 'profit_dedt': 44558669.31, 'industry': '种植业', 'is_st': False, 'delist': ['', '']},
    '300971.SZ': {'pe_ttm': 28.4687, 'pb': 1.9492, 'circ_mv': 161995.92, 'total_mv': 206505.6, 'bps': 9.0087, 'roe': 3.1413, 'profit_dedt': 29240337.98, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '300978.SZ': {'pe_ttm': 29.5234, 'pb': 2.3438, 'circ_mv': 251203.05, 'total_mv': 369864.86, 'bps': 3.7332, 'roe': 4.122, 'profit_dedt': 63238277.09, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '300980.SZ': {'pe_ttm': 32.7033, 'pb': 2.1346, 'circ_mv': 221572.29, 'total_mv': 306712.29, 'bps': 10.4073, 'roe': 4.4968, 'profit_dedt': 26024568.69, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '300987.SZ': {'pe_ttm': 132.9721, 'pb': 3.1546, 'circ_mv': 247742.87, 'total_mv': 247742.87, 'bps': 4.5298, 'roe': 0.0932, 'profit_dedt': 817743.71, 'industry': '互联网', 'is_st': False, 'delist': ['', '']},
    '300988.SZ': {'pe_ttm': 32.7532, 'pb': 2.43, 'circ_mv': 263901.4, 'total_mv': 320647.79, 'bps': 6.6996, 'roe': 5.1716, 'profit_dedt': 66058860.69, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '300993.SZ': {'pe_ttm': 23.4361, 'pb': 1.9265, 'circ_mv': 236687.12, 'total_mv': 300736.05, 'bps': 5.0662, 'roe': 3.4268, 'profit_dedt': 49320936.19, 'industry': '家居用品', 'is_st': False, 'delist': ['', '']},
    '300994.SZ': {'pe_ttm': 31.4657, 'pb': 2.4037, 'circ_mv': 135226.73, 'total_mv': 266186.5, 'bps': 4.7511, 'roe': 2.037, 'profit_dedt': 20419234.31, 'industry': '文教休闲', 'is_st': False, 'delist': ['', '']},
    '300995.SZ': {'pe_ttm': 90.3771, 'pb': 2.6342, 'circ_mv': 157593.57, 'total_mv': 239027.79, 'bps': 9.8511, 'roe': 2.0015, 'profit_dedt': 10828329.73, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '301002.SZ': {'pe_ttm': 109.8193, 'pb': 3.3959, 'circ_mv': 247319.64, 'total_mv': 380116.68, 'bps': 8.2954, 'roe': 3.9081, 'profit_dedt': 33455025.95, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301006.SZ': {'pe_ttm': 29.0279, 'pb': 1.4852, 'circ_mv': 119609.75, 'total_mv': 193181.5, 'bps': 9.3388, 'roe': 0.7408, 'profit_dedt': 11208167.32, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '301010.SZ': {'pe_ttm': 362.8129, 'pb': 2.582, 'circ_mv': 215136.0, 'total_mv': 215136.0, 'bps': 7.7149, 'roe': 0.8159, 'profit_dedt': 6736253.57, 'industry': '其他建材', 'is_st': False, 'delist': ['', '']},
    '301019.SZ': {'pe_ttm': 26.9225, 'pb': 2.9625, 'circ_mv': 230783.25, 'total_mv': 314750.2, 'bps': 6.4406, 'roe': 5.4266, 'profit_dedt': 51890185.71, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '301023.SZ': {'pe_ttm': 45.0029, 'pb': 3.252, 'circ_mv': 192307.33, 'total_mv': 268798.74, 'bps': 8.8573, 'roe': 3.1521, 'profit_dedt': 25506987.4, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301025.SZ': {'pe_ttm': 294.2738, 'pb': 6.3716, 'circ_mv': 276319.25, 'total_mv': 399108.47, 'bps': 1.5647, 'roe': 1.7532, 'profit_dedt': 7073632.0, 'industry': '出版业', 'is_st': False, 'delist': ['', '']},
    '301026.SZ': {'pe_ttm': 17.4691, 'pb': 2.3809, 'circ_mv': 297661.01, 'total_mv': 412145.46, 'bps': 7.8122, 'roe': 8.6705, 'profit_dedt': 125351061.13, 'industry': '小金属', 'is_st': False, 'delist': ['', '']},
    '301032.SZ': {'pe_ttm': 21.8624, 'pb': 1.9492, 'circ_mv': 167958.35, 'total_mv': 247161.73, 'bps': 5.2585, 'roe': 3.5803, 'profit_dedt': 38565638.18, 'industry': '工程机械', 'is_st': False, 'delist': ['', '']},
    '301036.SZ': {'pe_ttm': 360.9056, 'pb': 1.3758, 'circ_mv': 156081.86, 'total_mv': 224000.0, 'bps': 16.2818, 'roe': 0.0833, 'profit_dedt': 951457.92, 'industry': '染料涂料', 'is_st': False, 'delist': ['', '']},
    '301041.SZ': {'pe_ttm': 142.2132, 'pb': 5.7794, 'circ_mv': 293890.42, 'total_mv': 396849.6, 'bps': 6.4367, 'roe': 1.634, 'profit_dedt': 4980995.4, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '301042.SZ': {'pe_ttm': 98.4333, 'pb': 2.6721, 'circ_mv': 269188.42, 'total_mv': 282395.58, 'bps': 10.9201, 'roe': 1.3096, 'profit_dedt': 13045318.12, 'industry': 'IT设备', 'is_st': False, 'delist': ['', '']},
    '301043.SZ': {'pe_ttm': 56.6425, 'pb': 3.7638, 'circ_mv': 268814.33, 'total_mv': 322116.47, 'bps': 12.5858, 'roe': 3.6405, 'profit_dedt': 23440732.52, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '301056.SZ': {'pe_ttm': 51.3659, 'pb': 4.5539, 'circ_mv': 228618.54, 'total_mv': 346555.79, 'bps': 2.8525, 'roe': 3.6311, 'profit_dedt': 23911175.68, 'industry': '运输设备', 'is_st': False, 'delist': ['', '']},
    '301057.SZ': {'pe_ttm': 65.1058, 'pb': 3.4409, 'circ_mv': 189313.75, 'total_mv': 268210.83, 'bps': 6.6639, 'roe': 4.0984, 'profit_dedt': 30837231.72, 'industry': '化纤', 'is_st': False, 'delist': ['', '']},
    '301059.SZ': {'pe_ttm': 33.0107, 'pb': 4.7464, 'circ_mv': 265409.81, 'total_mv': 297032.89, 'bps': 2.7073, 'roe': 6.8342, 'profit_dedt': 43601125.11, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '301063.SZ': {'pe_ttm': 89.2127, 'pb': 1.2257, 'circ_mv': 180827.1, 'total_mv': 195258.87, 'bps': 15.2647, 'roe': 0.2875, 'profit_dedt': 3264362.38, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301065.SZ': {'pe_ttm': 26.9419, 'pb': 1.5259, 'circ_mv': 173092.73, 'total_mv': 212676.12, 'bps': 13.1461, 'roe': 3.2396, 'profit_dedt': 42108774.31, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '301066.SZ': {'pe_ttm': 84.9939, 'pb': 2.2533, 'circ_mv': 286733.54, 'total_mv': 288955.41, 'bps': 5.4587, 'roe': 1.5393, 'profit_dedt': 12106195.02, 'industry': '纺织', 'is_st': False, 'delist': ['', '']},
    '301068.SZ': {'pe_ttm': 85.7876, 'pb': 4.0129, 'circ_mv': 234852.63, 'total_mv': 330820.05, 'bps': 5.8337, 'roe': 3.8816, 'profit_dedt': 27635635.65, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '301077.SZ': {'pe_ttm': 30.9823, 'pb': 3.0576, 'circ_mv': 203089.23, 'total_mv': 361776.36, 'bps': 6.9567, 'roe': 5.7411, 'profit_dedt': 64713519.42, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '301079.SZ': {'pe_ttm': 415.6755, 'pb': 6.2721, 'circ_mv': 214721.12, 'total_mv': 302705.44, 'bps': 4.4305, 'roe': 1.7863, 'profit_dedt': 7423260.61, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '301083.SZ': {'pe_ttm': 137.316, 'pb': 3.0508, 'circ_mv': 123808.11, 'total_mv': 253282.18, 'bps': 4.6676, 'roe': 0.6506, 'profit_dedt': 2077062.89, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '301092.SZ': {'pe_ttm': 65.8346, 'pb': 2.8669, 'circ_mv': 254850.88, 'total_mv': 562223.02, 'bps': 14.5769, 'roe': 1.9642, 'profit_dedt': 36425299.69, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '301103.SZ': {'pe_ttm': 109.6423, 'pb': 1.4088, 'circ_mv': 207142.02, 'total_mv': 269119.13, 'bps': 12.0881, 'roe': 2.1894, 'profit_dedt': 34759318.72, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '301106.SZ': {'pe_ttm': 56.8159, 'pb': 2.0967, 'circ_mv': 264477.48, 'total_mv': 264477.48, 'bps': 8.8233, 'roe': 0.7597, 'profit_dedt': 470524.88, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '301107.SZ': {'pe_ttm': 65.3725, 'pb': 3.4596, 'circ_mv': 187075.41, 'total_mv': 327745.18, 'bps': 6.6625, 'roe': 2.7228, 'profit_dedt': 22213855.22, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '301113.SZ': {'pe_ttm': 267.6682, 'pb': 3.0865, 'circ_mv': 126773.97, 'total_mv': 209272.7, 'bps': 5.7314, 'roe': 0.6331, 'profit_dedt': 2436326.35, 'industry': '家居用品', 'is_st': False, 'delist': ['', '']},
    '301118.SZ': {'pe_ttm': 67.3358, 'pb': 1.8842, 'circ_mv': 255617.29, 'total_mv': 265697.84, 'bps': 12.7967, 'roe': 4.5625, 'profit_dedt': 60557796.04, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '301130.SZ': {'pe_ttm': 370.7269, 'pb': 2.5028, 'circ_mv': 168258.61, 'total_mv': 218086.39, 'bps': 11.3912, 'roe': 1.0055, 'profit_dedt': 6745622.62, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '301131.SZ': {'pe_ttm': 123.6937, 'pb': 1.718, 'circ_mv': 131952.01, 'total_mv': 184212.62, 'bps': 19.6162, 'roe': 0.389, 'profit_dedt': 4072895.89, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '301138.SZ': {'pe_ttm': 32.2818, 'pb': 2.8725, 'circ_mv': 194436.44, 'total_mv': 340320.0, 'bps': 9.873, 'roe': 3.7747, 'profit_dedt': 42560075.46, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '301141.SZ': {'pe_ttm': 213.6443, 'pb': 3.4786, 'circ_mv': 157263.02, 'total_mv': 438207.18, 'bps': 10.1565, 'roe': 0.649, 'profit_dedt': 5797795.61, 'industry': '矿物制品', 'is_st': False, 'delist': ['', '']},
    '301149.SZ': {'pe_ttm': 21.6829, 'pb': 1.9826, 'circ_mv': 265626.53, 'total_mv': 436579.08, 'bps': 5.121, 'roe': 6.3286, 'profit_dedt': 131559289.28, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '301156.SZ': {'pe_ttm': 43.8772, 'pb': 2.9707, 'circ_mv': 139112.1, 'total_mv': 216509.83, 'bps': 3.9822, 'roe': 2.4757, 'profit_dedt': 17764878.8, 'industry': '饲料', 'is_st': False, 'delist': ['', '']},
    '301157.SZ': {'pe_ttm': 114.3864, 'pb': 3.3401, 'circ_mv': 299049.13, 'total_mv': 370260.0, 'bps': 18.4754, 'roe': 1.3784, 'profit_dedt': 8835940.79, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301158.SZ': {'pe_ttm': 26.6063, 'pb': 1.9591, 'circ_mv': 267356.95, 'total_mv': 274191.37, 'bps': 7.1769, 'roe': 2.2625, 'profit_dedt': 30180124.79, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '301161.SZ': {'pe_ttm': 31.8494, 'pb': 3.4765, 'circ_mv': 220476.27, 'total_mv': 342720.0, 'bps': 8.2152, 'roe': 7.159, 'profit_dedt': 67987856.07, 'industry': '工程机械', 'is_st': False, 'delist': ['', '']},
    '301163.SZ': {'pe_ttm': 93.6361, 'pb': 1.6929, 'circ_mv': 129152.42, 'total_mv': 192576.0, 'bps': 13.9407, 'roe': 0.4397, 'profit_dedt': 1153038.88, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301170.SZ': {'pe_ttm': 28.5369, 'pb': 1.2974, 'circ_mv': 87902.56, 'total_mv': 231300.0, 'bps': 17.8284, 'roe': 1.8368, 'profit_dedt': 32038922.71, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '301172.SZ': {'pe_ttm': 133.6021, 'pb': 2.5699, 'circ_mv': 283022.01, 'total_mv': 382607.22, 'bps': 8.6317, 'roe': 1.0831, 'profit_dedt': 14594186.47, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '301173.SZ': {'pe_ttm': 16.9235, 'pb': 1.867, 'circ_mv': 101518.91, 'total_mv': 289240.0, 'bps': 17.6376, 'roe': 3.7086, 'profit_dedt': 52007425.18, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '301178.SZ': {'pe_ttm': 785.5831, 'pb': 4.0381, 'circ_mv': 232919.65, 'total_mv': 315716.34, 'bps': 8.3974, 'roe': 0.3321, 'profit_dedt': 3525039.31, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '301181.SZ': {'pe_ttm': 36.8572, 'pb': 2.6076, 'circ_mv': 294261.36, 'total_mv': 377010.28, 'bps': 9.6256, 'roe': 3.0549, 'profit_dedt': 36164237.04, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '301185.SZ': {'pe_ttm': 49.4621, 'pb': 2.4251, 'circ_mv': 240766.27, 'total_mv': 262957.77, 'bps': 7.0678, 'roe': 2.8701, 'profit_dedt': 29668130.44, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '301192.SZ': {'pe_ttm': 47.8581, 'pb': 3.0289, 'circ_mv': 99158.86, 'total_mv': 199244.26, 'bps': 6.4975, 'roe': 2.0465, 'profit_dedt': 9964292.3, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '301195.SZ': {'pe_ttm': 27.7784, 'pb': 1.4576, 'circ_mv': 234131.77, 'total_mv': 352626.37, 'bps': 18.3176, 'roe': 1.8799, 'profit_dedt': 34517076.65, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '301202.SZ': {'pe_ttm': 82.853, 'pb': 4.041, 'circ_mv': 283756.77, 'total_mv': 486129.6, 'bps': 8.8196, 'roe': 2.9697, 'profit_dedt': 35025132.53, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '301203.SZ': {'pe_ttm': 29.3818, 'pb': 1.9613, 'circ_mv': 145264.33, 'total_mv': 265520.0, 'bps': 16.9223, 'roe': 3.0431, 'profit_dedt': 38753035.1, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '301208.SZ': {'pe_ttm': 65.6378, 'pb': 2.3245, 'circ_mv': 204487.59, 'total_mv': 363960.3, 'bps': 13.048, 'roe': 1.4734, 'profit_dedt': 22961067.4, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '301210.SZ': {'pe_ttm': 55.17, 'pb': 2.4771, 'circ_mv': 179598.52, 'total_mv': 470262.25, 'bps': 11.7798, 'roe': 2.7489, 'profit_dedt': 53821476.85, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301212.SZ': {'pe_ttm': 190.2325, 'pb': 2.0817, 'circ_mv': 245343.75, 'total_mv': 282636.0, 'bps': 12.5713, 'roe': 1.4095, 'profit_dedt': 18995979.34, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '301220.SZ': {'pe_ttm': 112.5858, 'pb': 1.9034, 'circ_mv': 226710.22, 'total_mv': 327946.01, 'bps': 15.2781, 'roe': 1.3516, 'profit_dedt': 20586327.71, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '301222.SZ': {'pe_ttm': 38.4289, 'pb': 2.4596, 'circ_mv': 266287.05, 'total_mv': 350564.82, 'bps': 10.1562, 'roe': 3.0559, 'profit_dedt': 40768293.86, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301225.SZ': {'pe_ttm': 71.6009, 'pb': 5.174, 'circ_mv': 257417.78, 'total_mv': 843227.55, 'bps': 15.7647, 'roe': 2.9871, 'profit_dedt': 43192064.31, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '301233.SZ': {'pe_ttm': 33.5559, 'pb': 2.6533, 'circ_mv': 144037.77, 'total_mv': 262522.95, 'bps': 13.7866, 'roe': 3.5448, 'profit_dedt': 31910254.47, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '301262.SZ': {'pe_ttm': 25.0441, 'pb': 2.2209, 'circ_mv': 250864.95, 'total_mv': 856935.0, 'bps': 9.2528, 'roe': 4.1927, 'profit_dedt': 144636612.33, 'industry': '影视音像', 'is_st': False, 'delist': ['', '']},
    '301276.SZ': {'pe_ttm': 21.7054, 'pb': 1.0616, 'circ_mv': 125456.55, 'total_mv': 224985.6, 'bps': 16.3529, 'roe': 2.9316, 'profit_dedt': 51954008.91, 'industry': '服饰', 'is_st': False, 'delist': ['', '']},
    '301278.SZ': {'pe_ttm': 111.6941, 'pb': 1.8212, 'circ_mv': 156957.57, 'total_mv': 260332.91, 'bps': 15.9347, 'roe': 1.1253, 'profit_dedt': 9948656.25, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301283.SZ': {'pe_ttm': 16.1784, 'pb': 1.8158, 'circ_mv': 230687.04, 'total_mv': 293462.46, 'bps': 13.9061, 'roe': 4.3102, 'profit_dedt': 70545084.4, 'industry': '日用化工', 'is_st': False, 'delist': ['', '']},
    '301287.SZ': {'pe_ttm': 53.1699, 'pb': 2.3581, 'circ_mv': 70237.49, 'total_mv': 274947.49, 'bps': 17.4885, 'roe': 0.9886, 'profit_dedt': 2796548.78, 'industry': '文教休闲', 'is_st': False, 'delist': ['', '']},
    '301295.SZ': {'pe_ttm': 71.9748, 'pb': 3.6381, 'circ_mv': 103929.0, 'total_mv': 356328.0, 'bps': 9.7167, 'roe': 4.5869, 'profit_dedt': 43840936.04, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301296.SZ': {'pe_ttm': 330.6815, 'pb': 1.0286, 'circ_mv': 274475.42, 'total_mv': 274481.71, 'bps': 6.2477, 'roe': 0.8917, 'profit_dedt': 5992012.19, 'industry': '广告包装', 'is_st': False, 'delist': ['', '']},
    '301298.SZ': {'pe_ttm': 72.1265, 'pb': 2.0724, 'circ_mv': 139150.98, 'total_mv': 188784.8, 'bps': 6.2054, 'roe': 0.5518, 'profit_dedt': 4783356.99, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '301299.SZ': {'pe_ttm': 40.8204, 'pb': 4.6893, 'circ_mv': 206643.81, 'total_mv': 273346.13, 'bps': 9.6091, 'roe': 6.2306, 'profit_dedt': 34488414.99, 'industry': '互联网', 'is_st': False, 'delist': ['', '']},
    '301300.SZ': {'pe_ttm': 29.1061, 'pb': 2.5852, 'circ_mv': 128712.27, 'total_mv': 215523.13, 'bps': 12.7418, 'roe': 2.9279, 'profit_dedt': 23881168.84, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '301309.SZ': {'pe_ttm': 24.2496, 'pb': 1.7808, 'circ_mv': 180008.43, 'total_mv': 298587.17, 'bps': 16.6779, 'roe': 4.1958, 'profit_dedt': 67239060.96, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '301318.SZ': {'pe_ttm': 77.2255, 'pb': 1.5632, 'circ_mv': 197169.3, 'total_mv': 286292.47, 'bps': 13.5363, 'roe': 0.9496, 'profit_dedt': 14674147.41, 'industry': 'IT设备', 'is_st': False, 'delist': ['', '']},
    '301329.SZ': {'pe_ttm': 49.5297, 'pb': 2.1999, 'circ_mv': 134127.24, 'total_mv': 345676.2, 'bps': 9.2321, 'roe': 2.6326, 'profit_dedt': 27649672.88, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '301331.SZ': {'pe_ttm': 75.857, 'pb': 2.1991, 'circ_mv': 224058.74, 'total_mv': 224715.91, 'bps': 9.9315, 'roe': 1.5299, 'profit_dedt': 10802337.65, 'industry': '中成药', 'is_st': False, 'delist': ['', '']},
    '301332.SZ': {'pe_ttm': 30.0551, 'pb': 1.2112, 'circ_mv': 204191.79, 'total_mv': 355864.69, 'bps': 6.3657, 'roe': 1.9489, 'profit_dedt': 44497019.61, 'industry': '家用电器', 'is_st': False, 'delist': ['', '']},
    '301335.SZ': {'pe_ttm': 76.6081, 'pb': 1.634, 'circ_mv': 219915.43, 'total_mv': 317961.18, 'bps': 15.334, 'roe': 1.0407, 'profit_dedt': 15927294.05, 'industry': '文教休闲', 'is_st': False, 'delist': ['', '']},
    '301336.SZ': {'pe_ttm': 87.2883, 'pb': 2.5872, 'circ_mv': 167371.31, 'total_mv': 216286.05, 'bps': 16.1064, 'roe': 1.3193, 'profit_dedt': 3488866.1, 'industry': '家居用品', 'is_st': False, 'delist': ['', '']},
    '301353.SZ': {'pe_ttm': 39.3287, 'pb': 1.9838, 'circ_mv': 82184.55, 'total_mv': 255685.02, 'bps': 10.1473, 'roe': 2.9239, 'profit_dedt': 36406019.44, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '301356.SZ': {'pe_ttm': 45.0413, 'pb': 1.4494, 'circ_mv': 249064.32, 'total_mv': 426384.0, 'bps': 13.619, 'roe': 2.0182, 'profit_dedt': 52659040.99, 'industry': '家居用品', 'is_st': False, 'delist': ['', '']},
    '301359.SZ': {'pe_ttm': 62.9064, 'pb': 2.7687, 'circ_mv': 137100.32, 'total_mv': 231579.15, 'bps': 6.9598, 'roe': 2.1242, 'profit_dedt': 14419414.47, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '301360.SZ': {'pe_ttm': 86.805, 'pb': 3.4849, 'circ_mv': 203032.98, 'total_mv': 416061.51, 'bps': 17.1081, 'roe': 0.0206, 'profit_dedt': 374429.55, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '301361.SZ': {'pe_ttm': 33.143, 'pb': 2.5201, 'circ_mv': 152952.24, 'total_mv': 315852.24, 'bps': 10.7736, 'roe': 3.3416, 'profit_dedt': 38392494.11, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301371.SZ': {'pe_ttm': 23.5803, 'pb': 1.9555, 'circ_mv': 162266.6, 'total_mv': 1082336.42, 'bps': 10.6418, 'roe': 4.559, 'profit_dedt': 166804442.54, 'industry': '日用化工', 'is_st': False, 'delist': ['', '']},
    '301380.SZ': {'pe_ttm': 86.9876, 'pb': 4.5564, 'circ_mv': 277935.3, 'total_mv': 342199.35, 'bps': 7.4071, 'roe': 3.636, 'profit_dedt': 26158601.72, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '301386.SZ': {'pe_ttm': 35.5226, 'pb': 1.9635, 'circ_mv': 173308.3, 'total_mv': 288960.0, 'bps': 10.5118, 'roe': 2.2331, 'profit_dedt': 23937917.61, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301388.SZ': {'pe_ttm': 94.0101, 'pb': 2.6359, 'circ_mv': 182092.48, 'total_mv': 256733.69, 'bps': 9.5074, 'roe': 1.4906, 'profit_dedt': 10821076.07, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301393.SZ': {'pe_ttm': 66.203, 'pb': 3.0202, 'circ_mv': 295155.17, 'total_mv': 725004.0, 'bps': 22.2269, 'roe': 2.9767, 'profit_dedt': 60379920.89, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '301397.SZ': {'pe_ttm': 40.896, 'pb': 2.2594, 'circ_mv': 203224.66, 'total_mv': 444363.95, 'bps': 9.7282, 'roe': 1.7635, 'profit_dedt': 21279619.95, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '301429.SZ': {'pe_ttm': 41.7253, 'pb': 1.5433, 'circ_mv': 76678.4, 'total_mv': 206057.46, 'bps': 11.2939, 'roe': 2.1668, 'profit_dedt': 16033855.56, 'industry': '其他建材', 'is_st': False, 'delist': ['', '']},
    '301439.SZ': {'pe_ttm': 25.4862, 'pb': 1.803, 'circ_mv': 253061.9, 'total_mv': 537349.59, 'bps': 7.6594, 'roe': 3.4021, 'profit_dedt': 102342580.2, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301446.SZ': {'pe_ttm': 55.0868, 'pb': 3.1776, 'circ_mv': 118731.96, 'total_mv': 388648.0, 'bps': 11.7603, 'roe': 3.613, 'profit_dedt': 42116893.55, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '301449.SZ': {'pe_ttm': 36.4629, 'pb': 3.0846, 'circ_mv': 80489.43, 'total_mv': 357730.64, 'bps': 12.7018, 'roe': 4.3791, 'profit_dedt': 47696359.43, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '301456.SZ': {'pe_ttm': 40.3731, 'pb': 1.7141, 'circ_mv': 173915.92, 'total_mv': 385850.55, 'bps': 14.8006, 'roe': 2.0347, 'profit_dedt': 37651996.32, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301459.SZ': {'pe_ttm': 43.0313, 'pb': 2.5451, 'circ_mv': 81428.88, 'total_mv': 324860.02, 'bps': 12.2587, 'roe': 3.5148, 'profit_dedt': 34083471.19, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '301469.SZ': {'pe_ttm': 25.762, 'pb': 1.8368, 'circ_mv': 131877.94, 'total_mv': 257433.96, 'bps': 15.6635, 'roe': 3.8467, 'profit_dedt': 49134231.33, 'industry': '造纸', 'is_st': False, 'delist': ['', '']},
    '301491.SZ': {'pe_ttm': 25.5965, 'pb': 2.1489, 'circ_mv': 138506.15, 'total_mv': 467109.0, 'bps': 16.8508, 'roe': 2.4696, 'profit_dedt': 48903192.61, 'industry': 'IT设备', 'is_st': False, 'delist': ['', '']},
    '301501.SZ': {'pe_ttm': 24.7078, 'pb': 1.899, 'circ_mv': 116667.0, 'total_mv': 396667.8, 'bps': 14.1229, 'roe': 2.9594, 'profit_dedt': 55497310.88, 'industry': '造纸', 'is_st': False, 'delist': ['', '']},
    '301502.SZ': {'pe_ttm': 127.5372, 'pb': 3.3684, 'circ_mv': 116057.93, 'total_mv': 279880.4, 'bps': 14.5557, 'roe': 2.1166, 'profit_dedt': 15827250.97, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301503.SZ': {'pe_ttm': 40.024, 'pb': 2.1174, 'circ_mv': 128426.64, 'total_mv': 231608.0, 'bps': 13.673, 'roe': 0.2462, 'profit_dedt': 2039248.18, 'industry': 'IT设备', 'is_st': False, 'delist': ['', '']},
    '301508.SZ': {'pe_ttm': 33.0101, 'pb': 2.5856, 'circ_mv': 139795.29, 'total_mv': 537178.44, 'bps': 9.1895, 'roe': 4.0918, 'profit_dedt': 75935932.96, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '301509.SZ': {'pe_ttm': 59.4441, 'pb': 1.7699, 'circ_mv': 177315.31, 'total_mv': 376395.62, 'bps': 17.6568, 'roe': 1.3971, 'profit_dedt': 23025106.39, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '301513.SZ': {'pe_ttm': 66.592, 'pb': 4.3442, 'circ_mv': 113882.15, 'total_mv': 565200.0, 'bps': 13.0103, 'roe': 2.1977, 'profit_dedt': 18068432.11, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '301531.SZ': {'pe_ttm': 73.2523, 'pb': 6.8992, 'circ_mv': 219837.67, 'total_mv': 1030329.91, 'bps': 6.7965, 'roe': 6.6049, 'profit_dedt': 71213472.98, 'industry': '矿物制品', 'is_st': False, 'delist': ['', '']},
    '301533.SZ': {'pe_ttm': 87.3743, 'pb': 2.5177, 'circ_mv': 81136.83, 'total_mv': 279387.64, 'bps': 11.2881, 'roe': 1.8851, 'profit_dedt': 18175889.87, 'industry': '农用机械', 'is_st': False, 'delist': ['', '']},
    '301535.SZ': {'pe_ttm': 67.0317, 'pb': 4.8701, 'circ_mv': 229903.92, 'total_mv': 575848.21, 'bps': 2.7802, 'roe': 3.8053, 'profit_dedt': 37891123.52, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '301538.SZ': {'pe_ttm': 24.5942, 'pb': 2.8959, 'circ_mv': 171989.01, 'total_mv': 431905.6, 'bps': 13.5884, 'roe': 5.1401, 'profit_dedt': 74671920.33, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '301548.SZ': {'pe_ttm': 33.2874, 'pb': 2.9337, 'circ_mv': 179865.42, 'total_mv': 463884.0, 'bps': 18.1751, 'roe': 4.5525, 'profit_dedt': 64996027.56, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '301552.SZ': {'pe_ttm': 24.2086, 'pb': 2.6332, 'circ_mv': 75029.5, 'total_mv': 300118.0, 'bps': 9.2094, 'roe': 4.4347, 'profit_dedt': 44920139.26, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '301555.SZ': {'pe_ttm': 31.9877, 'pb': 2.7539, 'circ_mv': 169747.37, 'total_mv': 323948.73, 'bps': 12.7494, 'roe': 5.374, 'profit_dedt': 58294474.33, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '301556.SZ': {'pe_ttm': 52.4883, 'pb': 5.0834, 'circ_mv': 177086.36, 'total_mv': 512276.96, 'bps': 11.8169, 'roe': 4.3535, 'profit_dedt': 36201192.63, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '301557.SZ': {'pe_ttm': 64.0613, 'pb': 4.839, 'circ_mv': 199092.36, 'total_mv': 496558.16, 'bps': 16.5427, 'roe': 2.2355, 'profit_dedt': 23305412.99, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301559.SZ': {'pe_ttm': 80.5682, 'pb': 1.7033, 'circ_mv': 115200.0, 'total_mv': 768000.0, 'bps': 7.5147, 'roe': 0.9962, 'profit_dedt': 41136166.13, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '301560.SZ': {'pe_ttm': 223.1377, 'pb': 4.7716, 'circ_mv': 221223.55, 'total_mv': 460377.6, 'bps': 7.9344, 'roe': 0.067, 'profit_dedt': 1466563.23, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '301565.SZ': {'pe_ttm': 73.4994, 'pb': 3.1214, 'circ_mv': 246035.22, 'total_mv': 768819.22, 'bps': 6.1575, 'roe': 2.7877, 'profit_dedt': 60983867.33, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '301575.SZ': {'pe_ttm': 25.1852, 'pb': 2.0351, 'circ_mv': 172853.45, 'total_mv': 288177.75, 'bps': 11.6704, 'roe': 4.2039, 'profit_dedt': 49202824.04, 'industry': '家居用品', 'is_st': False, 'delist': ['', '']},
    '301578.SZ': {'pe_ttm': 358.3287, 'pb': 2.6625, 'circ_mv': 69880.49, 'total_mv': 245319.12, 'bps': 8.698, 'roe': 0.541, 'profit_dedt': 3895446.76, 'industry': 'IT设备', 'is_st': False, 'delist': ['', '']},
    '301581.SZ': {'pe_ttm': 117.2103, 'pb': 2.8708, 'circ_mv': 150018.59, 'total_mv': 283760.0, 'bps': 12.3556, 'roe': 0.6998, 'profit_dedt': 5158007.95, 'industry': '半导体', 'is_st': False, 'delist': ['', '']},
    '301585.SZ': {'pe_ttm': 44.0722, 'pb': 2.9049, 'circ_mv': 188533.74, 'total_mv': 287872.0, 'bps': 9.5288, 'roe': 2.3447, 'profit_dedt': 20841146.56, 'industry': '染料涂料', 'is_st': False, 'delist': ['', '']},
    '301587.SZ': {'pe_ttm': 51.2539, 'pb': 1.6079, 'circ_mv': 160567.6, 'total_mv': 322059.23, 'bps': 13.5952, 'roe': 1.4131, 'profit_dedt': 29719991.11, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301588.SZ': {'pe_ttm': 93.1259, 'pb': 3.1752, 'circ_mv': 188166.35, 'total_mv': 304895.91, 'bps': 8.0782, 'roe': 2.585, 'profit_dedt': 23881704.16, 'industry': '家居用品', 'is_st': False, 'delist': ['', '']},
    '301591.SZ': {'pe_ttm': 77.5461, 'pb': 4.7051, 'circ_mv': 178823.49, 'total_mv': 437533.36, 'bps': 8.5035, 'roe': 3.5031, 'profit_dedt': 33020309.85, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '301595.SZ': {'pe_ttm': 119.3725, 'pb': 5.505, 'circ_mv': 187666.17, 'total_mv': 497654.88, 'bps': 8.3488, 'roe': 2.2932, 'profit_dedt': 21439849.3, 'industry': '橡胶', 'is_st': False, 'delist': ['', '']},
    '301596.SZ': {'pe_ttm': 48.0609, 'pb': 5.3308, 'circ_mv': 196367.42, 'total_mv': 483873.09, 'bps': 8.4021, 'roe': 5.2462, 'profit_dedt': 43769946.1, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '301598.SZ': {'pe_ttm': 22.3456, 'pb': 3.0052, 'circ_mv': 91245.28, 'total_mv': 298078.73, 'bps': 16.8409, 'roe': 6.1286, 'profit_dedt': 58735573.26, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '301599.SZ': {'pe_ttm': 58.2829, 'pb': 6.5157, 'circ_mv': 178870.64, 'total_mv': 1411332.0, 'bps': 5.0187, 'roe': 6.987, 'profit_dedt': 110727807.47, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '301600.SZ': {'pe_ttm': 65.708, 'pb': 6.3019, 'circ_mv': 239035.48, 'total_mv': 774519.14, 'bps': 11.7584, 'roe': 4.2726, 'profit_dedt': 77690665.7, 'industry': '通信设备', 'is_st': False, 'delist': ['', '']},
    '301608.SZ': {'pe_ttm': 26.4579, 'pb': 2.9492, 'circ_mv': 298246.08, 'total_mv': 664566.64, 'bps': 16.9947, 'roe': 5.6184, 'profit_dedt': 116392112.21, 'industry': '通信设备', 'is_st': False, 'delist': ['', '']},
    '301609.SZ': {'pe_ttm': 36.2531, 'pb': 3.8836, 'circ_mv': 295773.25, 'total_mv': 490757.44, 'bps': 7.7583, 'roe': 3.6974, 'profit_dedt': 42061495.58, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301613.SZ': {'pe_ttm': 34.5271, 'pb': 3.5276, 'circ_mv': 284089.46, 'total_mv': 566825.88, 'bps': 11.1709, 'roe': 4.5853, 'profit_dedt': 59289542.52, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '301616.SZ': {'pe_ttm': 15.2213, 'pb': 1.9544, 'circ_mv': 100542.37, 'total_mv': 283200.0, 'bps': 18.1131, 'roe': 2.5509, 'profit_dedt': 35749290.93, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '301618.SZ': {'pe_ttm': 122.6133, 'pb': 3.9829, 'circ_mv': 143876.38, 'total_mv': 377390.97, 'bps': 7.5021, 'roe': 1.5649, 'profit_dedt': 13879020.67, 'industry': '染料涂料', 'is_st': False, 'delist': ['', '']},
    '301631.SZ': {'pe_ttm': 26.6216, 'pb': 2.9259, 'circ_mv': 259299.49, 'total_mv': 761227.42, 'bps': 20.3285, 'roe': 4.9796, 'profit_dedt': 124042540.45, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '301632.SZ': {'pe_ttm': 46.4327, 'pb': 2.0004, 'circ_mv': 241162.0, 'total_mv': 549150.72, 'bps': 6.5585, 'roe': 1.2924, 'profit_dedt': 32938862.45, 'industry': '建筑工程', 'is_st': False, 'delist': ['', '']},
    '301655.SZ': {'pe_ttm': 52.6228, 'pb': 10.5737, 'circ_mv': 108148.52, 'total_mv': 1095737.72, 'bps': 2.6759, 'roe': 12.6893, 'profit_dedt': 122448622.98, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '301665.SZ': {'pe_ttm': 18.8994, 'pb': 2.7128, 'circ_mv': 229486.51, 'total_mv': 991350.0, 'bps': 8.1209, 'roe': 6.6394, 'profit_dedt': 240102166.35, 'industry': '农药化肥', 'is_st': False, 'delist': ['', '']},
    '301668.SZ': {'pe_ttm': 43.5458, 'pb': 3.1485, 'circ_mv': 143457.6, 'total_mv': 326040.0, 'bps': 9.4139, 'roe': 3.0862, 'profit_dedt': 30274709.56, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301669.SZ': {'pe_ttm': 92.53, 'pb': 6.6878, 'circ_mv': 198003.98, 'total_mv': 1160640.0, 'bps': 3.6155, 'roe': 2.986, 'profit_dedt': 30809144.89, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301677.SZ': {'pe_ttm': 25.5131, 'pb': 3.5084, 'circ_mv': 108958.08, 'total_mv': 508200.0, 'bps': 19.3136, 'roe': 7.1534, 'profit_dedt': 98898953.69, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '301680.SZ': {'pe_ttm': 40.1743, 'pb': 4.3763, 'circ_mv': 169158.26, 'total_mv': 790160.4, 'bps': 21.806, 'roe': 7.6577, 'profit_dedt': 100220913.9, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '301683.SZ': {'pe_ttm': 29.1596, 'pb': 2.6015, 'circ_mv': 138662.6, 'total_mv': 613492.38, 'bps': 37.3636, 'roe': 6.2425, 'profit_dedt': 110079386.34, 'industry': '染料涂料', 'is_st': False, 'delist': ['', '']},
    '301686.SZ': {'pe_ttm': 63.7284, 'pb': 12.5618, 'circ_mv': 144812.26, 'total_mv': 818014.93, 'bps': 17.6004, 'roe': 9.7767, 'profit_dedt': 58905500.0, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '301687.SZ': {'pe_ttm': 57.6008, 'pb': 4.6814, 'circ_mv': 151091.08, 'total_mv': 712290.4, 'bps': 10.3602, 'roe': 3.9345, 'profit_dedt': 54880739.01, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '301688.SZ': {'pe_ttm': 28.5467, 'pb': 6.1349, 'circ_mv': 101757.26, 'total_mv': 522133.59, 'bps': 8.5109, 'roe': 12.3169, 'profit_dedt': 96844205.92, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '301696.SZ': {'pe_ttm': 66.19, 'pb': 16.4212, 'circ_mv': 261966.25, 'total_mv': 3483287.08, 'bps': 5.3029, 'roe': 16.163, 'profit_dedt': 243722205.28, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '301697.SZ': {'pe_ttm': 41.8572, 'pb': 7.3478, 'circ_mv': 115520.37, 'total_mv': 689850.2, 'bps': 4.7657, 'roe': 10.6701, 'profit_dedt': 83184700.0, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '301699.SZ': {'pe_ttm': 48.9617, 'pb': 9.9953, 'circ_mv': 264060.97, 'total_mv': 2733824.0, 'bps': 4.5585, 'roe': 11.024, 'profit_dedt': 268457700.0, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '301707.SZ': {'pe_ttm': 113.0032, 'pb': 17.9316, 'circ_mv': 183660.1, 'total_mv': 2791568.91, 'bps': 4.2067, 'roe': 9.6703, 'profit_dedt': 140706236.24, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '600051.SH': {'pe_ttm': 29.3109, 'pb': 0.5809, 'circ_mv': 198341.44, 'total_mv': 198341.44, 'bps': 10.9821, 'roe': 0.537, 'profit_dedt': 12516927.58, 'industry': '综合类', 'is_st': False, 'delist': ['', '']},
    '600099.SH': {'pe_ttm': 77.529, 'pb': 3.7097, 'circ_mv': 205972.8, 'total_mv': 205972.8, 'bps': 2.5339, 'roe': 2.4813, 'profit_dedt': 12127032.72, 'industry': '汽车整车', 'is_st': False, 'delist': ['', '']},
    '600128.SH': {'pe_ttm': 49.7354, 'pb': 1.0224, 'circ_mv': 213700.65, 'total_mv': 217971.07, 'bps': 8.4705, 'roe': 1.0219, 'profit_dedt': 3999656.75, 'industry': '商贸代理', 'is_st': False, 'delist': ['', '']},
    '600137.SH': {'pe_ttm': 57.5218, 'pb': 3.2183, 'circ_mv': 175283.33, 'total_mv': 175283.33, 'bps': 5.6024, 'roe': 1.5798, 'profit_dedt': 9361979.59, 'industry': '服饰', 'is_st': False, 'delist': ['', '']},
    '600148.SH': {'pe_ttm': 111.0892, 'pb': 4.6239, 'circ_mv': 223596.07, 'total_mv': 223596.07, 'bps': 3.417, 'roe': 2.4608, 'profit_dedt': 12670992.19, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '600149.SH': {'pe_ttm': 137.6274, 'pb': 7.8566, 'circ_mv': 196922.88, 'total_mv': 196922.88, 'bps': 0.6593, 'roe': 1.2519, 'profit_dedt': 2832192.76, 'industry': '综合类', 'is_st': False, 'delist': ['', '']},
    '600182.SH': {'pe_ttm': 19.9857, 'pb': 3.9052, 'circ_mv': 210800.0, 'total_mv': 421600.0, 'bps': 3.1752, 'roe': 6.4589, 'profit_dedt': 68750903.22, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '600232.SH': {'pe_ttm': 49.3219, 'pb': 2.5053, 'circ_mv': 242902.52, 'total_mv': 242902.52, 'bps': 2.6584, 'roe': 2.0467, 'profit_dedt': 17684264.56, 'industry': '纺织机械', 'is_st': False, 'delist': ['', '']},
    '600235.SH': {'pe_ttm': 262.9291, 'pb': 1.3867, 'circ_mv': 209726.1, 'total_mv': 209726.1, 'bps': 4.3051, 'roe': 0.4476, 'profit_dedt': 3161117.43, 'industry': '造纸', 'is_st': False, 'delist': ['', '']},
    '600250.SH': {'pe_ttm': 372.9146, 'pb': 4.3693, 'circ_mv': 273692.47, 'total_mv': 273692.47, 'bps': 2.0026, 'roe': 1.0413, 'profit_dedt': 6101311.39, 'industry': '商贸代理', 'is_st': False, 'delist': ['', '']},
    '600257.SH': {'pe_ttm': 107.9523, 'pb': 3.243, 'circ_mv': 264680.46, 'total_mv': 264680.46, 'bps': 1.696, 'roe': 2.0857, 'profit_dedt': 10449123.73, 'industry': '渔业', 'is_st': False, 'delist': ['', '']},
    '600272.SH': {'pe_ttm': 168.7559, 'pb': 4.9597, 'circ_mv': 240160.0, 'total_mv': 394240.5, 'bps': 3.0264, 'roe': 2.8286, 'profit_dedt': 2653207.8, 'industry': '医药商业', 'is_st': False, 'delist': ['', '']},
    '600287.SH': {'pe_ttm': 103.5311, 'pb': 0.9984, 'circ_mv': 219271.64, 'total_mv': 219271.64, 'bps': 5.028, 'roe': 0.5118, 'profit_dedt': 11503124.58, 'industry': '商贸代理', 'is_st': False, 'delist': ['', '']},
    '600419.SH': {'pe_ttm': 23.9878, 'pb': 1.1395, 'circ_mv': 282070.67, 'total_mv': 282070.67, 'bps': 7.8459, 'roe': 2.1939, 'profit_dedt': 18749941.69, 'industry': '乳制品', 'is_st': False, 'delist': ['', '']},
    '600444.SH': {'pe_ttm': 35.9809, 'pb': 2.7823, 'circ_mv': 212458.18, 'total_mv': 212458.18, 'bps': 5.2151, 'roe': 4.3232, 'profit_dedt': 31354014.11, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '600455.SH': {'pe_ttm': 31.7645, 'pb': 4.2871, 'circ_mv': 153834.05, 'total_mv': 153834.05, 'bps': 5.7451, 'roe': 9.8527, 'profit_dedt': 33621231.62, 'industry': '文教休闲', 'is_st': False, 'delist': ['', '']},
    '600560.SH': {'pe_ttm': 53.7175, 'pb': 2.5666, 'circ_mv': 256297.74, 'total_mv': 256297.74, 'bps': 4.4651, 'roe': 1.7948, 'profit_dedt': 13688128.94, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '600573.SH': {'pe_ttm': 29.2053, 'pb': 1.8858, 'circ_mv': 260500.0, 'total_mv': 260500.0, 'bps': 5.5255, 'roe': 3.5618, 'profit_dedt': 46301102.61, 'industry': '啤酒', 'is_st': False, 'delist': ['', '']},
    '600661.SH': {'pe_ttm': 22.8894, 'pb': 9.9097, 'circ_mv': 262765.25, 'total_mv': 262765.25, 'bps': 0.9254, 'roe': 12.7691, 'profit_dedt': 25398014.47, 'industry': '文教休闲', 'is_st': False, 'delist': ['', '']},
    '600671.SH': {'pe_ttm': 194.016, 'pb': 34.774, 'circ_mv': 265337.92, 'total_mv': 265356.22, 'bps': 0.6266, 'roe': 8.6146, 'profit_dedt': 2320945.14, 'industry': '中成药', 'is_st': False, 'delist': ['', '']},
    '600689.SH': {'pe_ttm': 184.998, 'pb': 4.9987, 'circ_mv': 181427.29, 'total_mv': 239581.63, 'bps': 2.3846, 'roe': 2.713, 'profit_dedt': 7091399.36, 'industry': '综合类', 'is_st': False, 'delist': ['', '']},
    '600692.SH': {'pe_ttm': 54.1019, 'pb': 2.3923, 'circ_mv': 171367.06, 'total_mv': 236385.41, 'bps': 2.809, 'roe': 1.9413, 'profit_dedt': 17450446.54, 'industry': '区域地产', 'is_st': False, 'delist': ['', '']},
    '600768.SH': {'pe_ttm': 18.3651, 'pb': 4.097, 'circ_mv': 197277.12, 'total_mv': 197277.12, 'bps': 3.6002, 'roe': 12.8428, 'profit_dedt': 48496094.58, 'industry': '铝', 'is_st': False, 'delist': ['', '']},
    '600778.SH': {'pe_ttm': 128.0063, 'pb': 7.3028, 'circ_mv': 179571.0, 'total_mv': 179730.54, 'bps': 0.7901, 'roe': 5.4355, 'profit_dedt': 11841028.69, 'industry': '百货', 'is_st': False, 'delist': ['', '']},
    '600796.SH': {'pe_ttm': 32.3208, 'pb': 1.2691, 'circ_mv': 159165.21, 'total_mv': 442825.34, 'bps': 4.0265, 'roe': 2.2445, 'profit_dedt': 77608067.97, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '600833.SH': {'pe_ttm': 33.4504, 'pb': 2.0862, 'circ_mv': 233794.44, 'total_mv': 233794.44, 'bps': 5.0235, 'roe': 2.2936, 'profit_dedt': 1311901.29, 'industry': '医药商业', 'is_st': False, 'delist': ['', '']},
    '600854.SH': {'pe_ttm': 23.0212, 'pb': 1.0025, 'circ_mv': 246742.79, 'total_mv': 246742.79, 'bps': 4.7382, 'roe': 3.9888, 'profit_dedt': 97350854.47, 'industry': '家用电器', 'is_st': False, 'delist': ['', '']},
    '600858.SH': {'pe_ttm': 87.7774, 'pb': 0.936, 'circ_mv': 256186.26, 'total_mv': 257432.97, 'bps': 5.2886, 'roe': 2.7301, 'profit_dedt': 49060128.86, 'industry': '百货', 'is_st': False, 'delist': ['', '']},
    '600883.SH': {'pe_ttm': 77.8574, 'pb': 1.9544, 'circ_mv': 192647.81, 'total_mv': 192647.81, 'bps': 4.1752, 'roe': 1.5304, 'profit_dedt': 15663699.84, 'industry': '种植业', 'is_st': False, 'delist': ['', '']},
    '601007.SH': {'pe_ttm': 39.4965, 'pb': 1.672, 'circ_mv': 264420.0, 'total_mv': 264420.0, 'bps': 4.0551, 'roe': 1.6789, 'profit_dedt': 23022395.21, 'industry': '酒店餐饮', 'is_st': False, 'delist': ['', '']},
    '601026.SH': {'pe_ttm': 46.7984, 'pb': 3.7829, 'circ_mv': 162185.12, 'total_mv': 962724.0, 'bps': 3.8595, 'roe': 3.4726, 'profit_dedt': 85308742.07, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '601083.SH': {'pe_ttm': 11.2346, 'pb': 1.77, 'circ_mv': 245561.8, 'total_mv': 1637061.8, 'bps': 7.147, 'roe': 8.0484, 'profit_dedt': 717206383.06, 'industry': '水运', 'is_st': False, 'delist': ['', '']},
    '601112.SH': {'pe_ttm': 34.6393, 'pb': 4.1971, 'circ_mv': 289092.31, 'total_mv': 2753259.64, 'bps': 3.7693, 'roe': 9.0883, 'profit_dedt': 444206941.71, 'industry': '玻璃', 'is_st': False, 'delist': ['', '']},
    '601123.SH': {'pe_ttm': 31.3425, 'pb': 4.9468, 'circ_mv': 160224.92, 'total_mv': 2064269.93, 'bps': 3.7557, 'roe': 10.5868, 'profit_dedt': 415364200.0, 'industry': '普钢', 'is_st': False, 'delist': ['', '']},
    '601798.SH': {'pe_ttm': 33.7247, 'pb': 2.0404, 'circ_mv': 272277.66, 'total_mv': 272277.66, 'bps': 3.7639, 'roe': 4.0554, 'profit_dedt': 7523107.28, 'industry': '化工机械', 'is_st': False, 'delist': ['', '']},
    '603004.SH': {'pe_ttm': 40.9376, 'pb': 2.2293, 'circ_mv': 116994.56, 'total_mv': 467978.24, 'bps': 8.9132, 'roe': 2.4537, 'profit_dedt': 45167878.68, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '603006.SH': {'pe_ttm': 27.1755, 'pb': 2.1172, 'circ_mv': 275357.41, 'total_mv': 275357.41, 'bps': 5.1151, 'roe': 3.3347, 'profit_dedt': 37888694.31, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '603017.SH': {'pe_ttm': 47.5076, 'pb': 1.8507, 'circ_mv': 277018.87, 'total_mv': 277018.87, 'bps': 5.4195, 'roe': 4.4833, 'profit_dedt': 50368750.36, 'industry': '建筑工程', 'is_st': False, 'delist': ['', '']},
    '603022.SH': {'pe_ttm': 30.1679, 'pb': 2.131, 'circ_mv': 181200.0, 'total_mv': 181200.0, 'bps': 4.2516, 'roe': 2.6146, 'profit_dedt': 22439183.8, 'industry': '广告包装', 'is_st': False, 'delist': ['', '']},
    '603029.SH': {'pe_ttm': 26.6003, 'pb': 2.1264, 'circ_mv': 202034.43, 'total_mv': 202034.43, 'bps': 7.8302, 'roe': 2.0411, 'profit_dedt': 13543145.85, 'industry': '农用机械', 'is_st': False, 'delist': ['', '']},
    '603036.SH': {'pe_ttm': 27.6225, 'pb': 1.9962, 'circ_mv': 278520.11, 'total_mv': 278520.11, 'bps': 6.7728, 'roe': 3.8353, 'profit_dedt': 48235750.99, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '603037.SH': {'pe_ttm': 41.5926, 'pb': 2.8706, 'circ_mv': 275731.5, 'total_mv': 276133.58, 'bps': 3.6089, 'roe': 3.0394, 'profit_dedt': 21222036.07, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '603041.SH': {'pe_ttm': 34.1612, 'pb': 1.3803, 'circ_mv': 211169.3, 'total_mv': 211169.3, 'bps': 8.3533, 'roe': 2.4707, 'profit_dedt': 32261789.88, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '603059.SH': {'pe_ttm': 22.0978, 'pb': 2.3512, 'circ_mv': 263376.49, 'total_mv': 263819.61, 'bps': 11.152, 'roe': 4.3359, 'profit_dedt': 45983822.25, 'industry': '日用化工', 'is_st': False, 'delist': ['', '']},
    '603062.SH': {'pe_ttm': 28.8374, 'pb': 1.9641, 'circ_mv': 136577.3, 'total_mv': 397872.0, 'bps': 18.7571, 'roe': 1.84, 'profit_dedt': 30172084.03, 'industry': '染料涂料', 'is_st': False, 'delist': ['', '']},
    '603066.SH': {'pe_ttm': 24.5402, 'pb': 1.7846, 'circ_mv': 272999.13, 'total_mv': 272999.13, 'bps': 5.2001, 'roe': 9.5739, 'profit_dedt': 56972756.5, 'industry': '仓储物流', 'is_st': False, 'delist': ['', '']},
    '603073.SH': {'pe_ttm': 48.4848, 'pb': 1.7441, 'circ_mv': 242208.0, 'total_mv': 242208.0, 'bps': 11.9716, 'roe': 1.8374, 'profit_dedt': 24703680.78, 'industry': '纺织', 'is_st': False, 'delist': ['', '']},
    '603075.SH': {'pe_ttm': 24.3586, 'pb': 3.8804, 'circ_mv': 83656.98, 'total_mv': 793936.98, 'bps': 5.0835, 'roe': 7.7433, 'profit_dedt': 157034747.21, 'industry': '家用电器', 'is_st': False, 'delist': ['', '']},
    '603076.SH': {'pe_ttm': 122.2234, 'pb': 1.8994, 'circ_mv': 242851.02, 'total_mv': 242851.02, 'bps': 10.593, 'roe': 1.4866, 'profit_dedt': 15529799.82, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '603079.SH': {'pe_ttm': 57.3265, 'pb': 1.6207, 'circ_mv': 257269.29, 'total_mv': 257269.29, 'bps': 8.3979, 'roe': 1.2943, 'profit_dedt': 18921937.84, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '603080.SH': {'pe_ttm': 13.2346, 'pb': 1.7547, 'circ_mv': 282292.5, 'total_mv': 282292.5, 'bps': 11.3697, 'roe': 6.5933, 'profit_dedt': 108594306.56, 'industry': '供气供热', 'is_st': False, 'delist': ['', '']},
    '603082.SH': {'pe_ttm': 47.004, 'pb': 3.9998, 'circ_mv': 167297.21, 'total_mv': 687114.04, 'bps': 10.3687, 'roe': 2.971, 'profit_dedt': 49278575.23, 'industry': '运输设备', 'is_st': False, 'delist': ['', '']},
    '603086.SH': {'pe_ttm': 34.9357, 'pb': 1.1975, 'circ_mv': 255235.29, 'total_mv': 255235.29, 'bps': 4.9018, 'roe': 3.4208, 'profit_dedt': 68236010.46, 'industry': '农药化肥', 'is_st': False, 'delist': ['', '']},
    '603091.SH': {'pe_ttm': 18.8665, 'pb': 2.8447, 'circ_mv': 203708.41, 'total_mv': 648684.65, 'bps': 15.9313, 'roe': 8.6752, 'profit_dedt': 190978662.58, 'industry': '家居用品', 'is_st': False, 'delist': ['', '']},
    '603092.SH': {'pe_ttm': 22.801, 'pb': 3.3854, 'circ_mv': 166601.02, 'total_mv': 1927600.48, 'bps': 14.2347, 'roe': 7.3463, 'profit_dedt': 377428156.78, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '603096.SH': {'pe_ttm': 33.426, 'pb': 1.3709, 'circ_mv': 248317.71, 'total_mv': 248317.71, 'bps': 11.7445, 'roe': 2.402, 'profit_dedt': 38888856.99, 'industry': '出版业', 'is_st': False, 'delist': ['', '']},
    '603102.SH': {'pe_ttm': 20.1178, 'pb': 1.3927, 'circ_mv': 238656.0, 'total_mv': 238656.0, 'bps': 26.7757, 'roe': 3.6848, 'profit_dedt': 60325108.46, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '603107.SH': {'pe_ttm': 30.3633, 'pb': 1.9664, 'circ_mv': 266826.8, 'total_mv': 412560.71, 'bps': 6.2196, 'roe': 2.9782, 'profit_dedt': 67585691.66, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '603120.SH': {'pe_ttm': 60.1571, 'pb': 4.1748, 'circ_mv': 212429.5, 'total_mv': 506692.0, 'bps': 13.4257, 'roe': 3.4488, 'profit_dedt': 37991406.76, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '603136.SH': {'pe_ttm': 30.9358, 'pb': 1.804, 'circ_mv': 244194.54, 'total_mv': 244194.54, 'bps': 5.0112, 'roe': 2.8143, 'profit_dedt': 35721025.31, 'industry': '旅游景点', 'is_st': False, 'delist': ['', '']},
    '603172.SH': {'pe_ttm': 47.4475, 'pb': 2.3764, 'circ_mv': 266493.24, 'total_mv': 266493.24, 'bps': 8.4078, 'roe': 2.9795, 'profit_dedt': 32484785.96, 'industry': '染料涂料', 'is_st': False, 'delist': ['', '']},
    '603182.SH': {'pe_ttm': 25.289, 'pb': 1.9959, 'circ_mv': 225268.95, 'total_mv': 225268.95, 'bps': 6.8591, 'roe': 5.0734, 'profit_dedt': 56390566.1, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '603190.SH': {'pe_ttm': 21.9484, 'pb': 1.2663, 'circ_mv': 273672.0, 'total_mv': 284025.92, 'bps': 18.0093, 'roe': 3.2151, 'profit_dedt': 70074742.16, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '603193.SH': {'pe_ttm': 23.2712, 'pb': 3.5, 'circ_mv': 209256.01, 'total_mv': 819301.43, 'bps': 5.7858, 'roe': 9.8684, 'profit_dedt': 216914376.86, 'industry': '日用化工', 'is_st': False, 'delist': ['', '']},
    '603201.SH': {'pe_ttm': 14.5661, 'pb': 1.629, 'circ_mv': 287429.27, 'total_mv': 289909.02, 'bps': 9.3678, 'roe': 5.0985, 'profit_dedt': 87072368.28, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '603202.SH': {'pe_ttm': 11.9323, 'pb': 1.2066, 'circ_mv': 257595.77, 'total_mv': 820800.0, 'bps': 42.515, 'roe': 4.3006, 'profit_dedt': 276071238.2, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '603205.SH': {'pe_ttm': 29.8147, 'pb': 2.2491, 'circ_mv': 89873.16, 'total_mv': 339144.0, 'bps': 9.6661, 'roe': 3.3615, 'profit_dedt': 45844609.34, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '603207.SH': {'pe_ttm': 20.5871, 'pb': 3.4612, 'circ_mv': 133558.69, 'total_mv': 395722.44, 'bps': 7.1158, 'roe': 9.4406, 'profit_dedt': 105632661.71, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '603214.SH': {'pe_ttm': 24.9408, 'pb': 1.6173, 'circ_mv': 192016.44, 'total_mv': 192016.44, 'bps': 8.5696, 'roe': 3.5702, 'profit_dedt': 25649784.13, 'industry': '百货', 'is_st': False, 'delist': ['', '']},
    '603231.SH': {'pe_ttm': 20.5995, 'pb': 1.649, 'circ_mv': 183415.12, 'total_mv': 330458.41, 'bps': 10.4669, 'roe': 3.3978, 'profit_dedt': 62981722.6, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '603237.SH': {'pe_ttm': 23.4281, 'pb': 1.6649, 'circ_mv': 288506.49, 'total_mv': 297937.78, 'bps': 9.1296, 'roe': 11.5498, 'profit_dedt': 195429951.05, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '603238.SH': {'pe_ttm': 21.7686, 'pb': 1.5595, 'circ_mv': 228276.57, 'total_mv': 228276.57, 'bps': 8.2464, 'roe': 3.611, 'profit_dedt': 48483173.24, 'industry': '纺织', 'is_st': False, 'delist': ['', '']},
    '603248.SH': {'pe_ttm': 56.653, 'pb': 4.1, 'circ_mv': 146084.71, 'total_mv': 939780.0, 'bps': 4.9829, 'roe': 2.5854, 'profit_dedt': 56812884.19, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '603255.SH': {'pe_ttm': 81.5163, 'pb': 2.5653, 'circ_mv': 187365.02, 'total_mv': 413881.86, 'bps': 11.9871, 'roe': 2.7361, 'profit_dedt': 42674301.73, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '603257.SH': {'pe_ttm': 42.4686, 'pb': 3.0192, 'circ_mv': 159858.0, 'total_mv': 770400.0, 'bps': 21.2641, 'roe': 3.7432, 'profit_dedt': 91310400.38, 'industry': '建筑工程', 'is_st': False, 'delist': ['', '']},
    '603262.SH': {'pe_ttm': 62.5674, 'pb': 4.3165, 'circ_mv': 178017.35, 'total_mv': 682417.06, 'bps': 3.9523, 'roe': 4.6696, 'profit_dedt': 70563733.89, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '603273.SH': {'pe_ttm': 132.8478, 'pb': 3.5055, 'circ_mv': 81396.8, 'total_mv': 312809.3, 'bps': 4.1637, 'roe': 1.7464, 'profit_dedt': 8148255.19, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '603275.SH': {'pe_ttm': 29.3233, 'pb': 1.7843, 'circ_mv': 164726.04, 'total_mv': 545117.52, 'bps': 20.5358, 'roe': 3.2401, 'profit_dedt': 76045802.97, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '603276.SH': {'pe_ttm': 47.0452, 'pb': 2.0293, 'circ_mv': 133490.52, 'total_mv': 363386.62, 'bps': 8.6236, 'roe': 2.6041, 'profit_dedt': 41437236.2, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '603280.SH': {'pe_ttm': 43.192, 'pb': 2.1647, 'circ_mv': 298226.83, 'total_mv': 298226.83, 'bps': 12.7086, 'roe': 2.3344, 'profit_dedt': 26642828.68, 'industry': '工程机械', 'is_st': False, 'delist': ['', '']},
    '603282.SH': {'pe_ttm': 28.3804, 'pb': 1.7366, 'circ_mv': 228564.56, 'total_mv': 228564.56, 'bps': 9.8353, 'roe': 1.9662, 'profit_dedt': 23303389.69, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '603284.SH': {'pe_ttm': 14.5213, 'pb': 1.3647, 'circ_mv': 76338.63, 'total_mv': 305354.53, 'bps': 29.6691, 'roe': 6.1653, 'profit_dedt': 113159821.86, 'industry': '造纸', 'is_st': False, 'delist': ['', '']},
    '603285.SH': {'pe_ttm': 43.5619, 'pb': 3.4444, 'circ_mv': 230498.16, 'total_mv': 592000.0, 'bps': 10.7419, 'roe': 4.0731, 'profit_dedt': 65377603.85, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '603293.SH': {'pe_ttm': 25.2606, 'pb': 2.1377, 'circ_mv': 123434.39, 'total_mv': 629861.64, 'bps': 16.4524, 'roe': 4.2091, 'profit_dedt': 86740039.71, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '603307.SH': {'pe_ttm': 23.5644, 'pb': 1.78, 'circ_mv': 247635.35, 'total_mv': 249997.25, 'bps': 14.2282, 'roe': 4.4356, 'profit_dedt': 58231992.93, 'industry': '文教休闲', 'is_st': False, 'delist': ['', '']},
    '603310.SH': {'pe_ttm': 31.8998, 'pb': 1.246, 'circ_mv': 282090.54, 'total_mv': 502124.36, 'bps': 11.669, 'roe': 1.7709, 'profit_dedt': 22478557.78, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '603312.SH': {'pe_ttm': 25.6019, 'pb': 2.9102, 'circ_mv': 167525.15, 'total_mv': 526398.35, 'bps': 11.3051, 'roe': 4.6709, 'profit_dedt': 81213639.07, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '603320.SH': {'pe_ttm': 37.9848, 'pb': 2.2692, 'circ_mv': 269507.18, 'total_mv': 269507.18, 'bps': 7.9631, 'roe': 2.7766, 'profit_dedt': 30928783.69, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '603321.SH': {'pe_ttm': 71.0747, 'pb': 1.7389, 'circ_mv': 248431.0, 'total_mv': 250925.66, 'bps': 4.136, 'roe': 1.4382, 'profit_dedt': 12389885.56, 'industry': '运输设备', 'is_st': False, 'delist': ['', '']},
    '603325.SH': {'pe_ttm': 16.1127, 'pb': 1.553, 'circ_mv': 182743.39, 'total_mv': 427701.38, 'bps': 34.4235, 'roe': 3.5572, 'profit_dedt': 91859489.62, 'industry': '化工机械', 'is_st': False, 'delist': ['', '']},
    '603326.SH': {'pe_ttm': 18.5581, 'pb': 2.1246, 'circ_mv': 245127.86, 'total_mv': 245127.86, 'bps': 3.6147, 'roe': 4.2814, 'profit_dedt': 43581570.14, 'industry': '家居用品', 'is_st': False, 'delist': ['', '']},
    '603329.SH': {'pe_ttm': 19.4601, 'pb': 1.672, 'circ_mv': 211542.68, 'total_mv': 256302.48, 'bps': 6.1304, 'roe': 7.6941, 'profit_dedt': 112857190.65, 'industry': '仓储物流', 'is_st': False, 'delist': ['', '']},
    '603332.SH': {'pe_ttm': 26.5145, 'pb': 1.9895, 'circ_mv': 259832.99, 'total_mv': 259832.99, 'bps': 6.0366, 'roe': 4.1898, 'profit_dedt': 64489725.75, 'industry': '化纤', 'is_st': False, 'delist': ['', '']},
    '603334.SH': {'pe_ttm': 33.3291, 'pb': 2.6381, 'circ_mv': 101177.11, 'total_mv': 456330.0, 'bps': 12.0541, 'roe': 4.7512, 'profit_dedt': 81191438.23, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '603344.SH': {'pe_ttm': 28.2867, 'pb': 1.9617, 'circ_mv': 118706.11, 'total_mv': 427384.61, 'bps': 11.1996, 'roe': 3.1519, 'profit_dedt': 62539418.36, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '603350.SH': {'pe_ttm': 38.0588, 'pb': 2.4361, 'circ_mv': 124739.96, 'total_mv': 329288.96, 'bps': 11.6034, 'roe': 3.4852, 'profit_dedt': 38577431.86, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '603352.SH': {'pe_ttm': 30.0734, 'pb': 1.8992, 'circ_mv': 112471.92, 'total_mv': 562359.83, 'bps': 13.0633, 'roe': 2.2671, 'profit_dedt': 49387868.47, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '603370.SH': {'pe_ttm': 35.9605, 'pb': 2.4668, 'circ_mv': 232501.04, 'total_mv': 481637.35, 'bps': 11.1602, 'roe': 3.4421, 'profit_dedt': 56248729.73, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '603376.SH': {'pe_ttm': 63.544, 'pb': 6.1647, 'circ_mv': 84450.11, 'total_mv': 1055602.64, 'bps': 4.2808, 'roe': 1.8913, 'profit_dedt': 32098906.92, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '603382.SH': {'pe_ttm': 34.4071, 'pb': 1.9293, 'circ_mv': 220693.55, 'total_mv': 346371.43, 'bps': 9.9052, 'roe': 3.6423, 'profit_dedt': 62356482.91, 'industry': '化纤', 'is_st': False, 'delist': ['', '']},
    '603391.SH': {'pe_ttm': 30.7058, 'pb': 2.9277, 'circ_mv': 158249.0, 'total_mv': 632996.0, 'bps': 23.7591, 'roe': 1.4815, 'profit_dedt': 21294556.58, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '603395.SH': {'pe_ttm': 193.399, 'pb': 4.5873, 'circ_mv': 162110.0, 'total_mv': 648440.0, 'bps': 5.4367, 'roe': 1.8629, 'profit_dedt': 21416394.58, 'industry': '农药化肥', 'is_st': False, 'delist': ['', '']},
    '603400.SH': {'pe_ttm': 80.5175, 'pb': 5.7857, 'circ_mv': 192019.53, 'total_mv': 688099.53, 'bps': 11.8931, 'roe': 3.6869, 'profit_dedt': 43604641.43, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '603402.SH': {'pe_ttm': 19.4293, 'pb': 2.064, 'circ_mv': 147909.47, 'total_mv': 591638.47, 'bps': 28.5126, 'roe': 4.3269, 'profit_dedt': 118947347.43, 'industry': '旅游景点', 'is_st': False, 'delist': ['', '']},
    '603406.SH': {'pe_ttm': 23.3251, 'pb': 2.3194, 'circ_mv': 160657.37, 'total_mv': 1094427.36, 'bps': 11.796, 'roe': 5.4544, 'profit_dedt': 235905532.71, 'industry': '化纤', 'is_st': False, 'delist': ['', '']},
    '603407.SH': {'pe_ttm': 85.4611, 'pb': 10.9339, 'circ_mv': 206350.35, 'total_mv': 2343650.9, 'bps': 5.2552, 'roe': 7.5248, 'profit_dedt': 134427224.85, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '603409.SH': {'pe_ttm': 22.1074, 'pb': 2.1832, 'circ_mv': 96172.23, 'total_mv': 335183.55, 'bps': 8.176, 'roe': 4.8625, 'profit_dedt': 72735717.6, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '603435.SH': {'pe_ttm': 77.7022, 'pb': 8.9735, 'circ_mv': 153634.69, 'total_mv': 1976716.09, 'bps': 4.7975, 'roe': 7.4947, 'profit_dedt': 132511588.19, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '603448.SH': {'pe_ttm': 21.7297, 'pb': 3.7215, 'circ_mv': 139454.62, 'total_mv': 711600.0, 'bps': 21.2457, 'roe': 8.8933, 'profit_dedt': 161038050.26, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '603468.SH': {'pe_ttm': 24.1449, 'pb': 3.0568, 'circ_mv': 87894.94, 'total_mv': 906063.3, 'bps': 7.9897, 'roe': 6.5537, 'profit_dedt': 184485100.0, 'industry': '摩托车', 'is_st': False, 'delist': ['', '']},
    '603506.SH': {'pe_ttm': 27.0632, 'pb': 1.8162, 'circ_mv': 230027.8, 'total_mv': 230027.8, 'bps': 6.745, 'roe': 9.4159, 'profit_dedt': 61469969.74, 'industry': '房产服务', 'is_st': False, 'delist': ['', '']},
    '603518.SH': {'pe_ttm': 10.3667, 'pb': 0.7039, 'circ_mv': 268033.65, 'total_mv': 268033.65, 'bps': 11.0261, 'roe': 3.869, 'profit_dedt': 121943827.18, 'industry': '服饰', 'is_st': False, 'delist': ['', '']},
    '603585.SH': {'pe_ttm': 17.376, 'pb': 1.0409, 'circ_mv': 285148.19, 'total_mv': 289949.66, 'bps': 14.1623, 'roe': 1.9324, 'profit_dedt': 52254900.85, 'industry': '农药化肥', 'is_st': False, 'delist': ['', '']},
    '603586.SH': {'pe_ttm': 59.0718, 'pb': 1.2079, 'circ_mv': 271533.13, 'total_mv': 271533.13, 'bps': 11.4658, 'roe': 0.7601, 'profit_dedt': 12786466.1, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '603617.SH': {'pe_ttm': 50.4497, 'pb': 1.6763, 'circ_mv': 260653.53, 'total_mv': 262732.82, 'bps': 4.0209, 'roe': 2.0971, 'profit_dedt': 24568482.4, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '603677.SH': {'pe_ttm': 210.6597, 'pb': 2.4765, 'circ_mv': 276382.29, 'total_mv': 276382.29, 'bps': 5.7944, 'roe': 0.4731, 'profit_dedt': 3344318.68, 'industry': '家用电器', 'is_st': False, 'delist': ['', '']},
    '603700.SH': {'pe_ttm': 35.0409, 'pb': 1.4761, 'circ_mv': 236155.0, 'total_mv': 236155.0, 'bps': 8.0009, 'roe': 1.7129, 'profit_dedt': 24161779.33, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '603755.SH': {'pe_ttm': 32.5226, 'pb': 3.3079, 'circ_mv': 254916.41, 'total_mv': 254916.41, 'bps': 7.8146, 'roe': 3.8182, 'profit_dedt': 29152919.72, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '603787.SH': {'pe_ttm': 103.9191, 'pb': 1.5877, 'circ_mv': 240730.41, 'total_mv': 240730.41, 'bps': 6.5883, 'roe': 2.1963, 'profit_dedt': 380831.25, 'industry': '摩托车', 'is_st': False, 'delist': ['', '']},
    '603797.SH': {'pe_ttm': 22.0121, 'pb': 0.8212, 'circ_mv': 277923.71, 'total_mv': 277923.71, 'bps': 5.8698, 'roe': 2.4536, 'profit_dedt': 82155861.84, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '603810.SH': {'pe_ttm': 73.6694, 'pb': 1.3198, 'circ_mv': 202213.63, 'total_mv': 202213.63, 'bps': 9.2668, 'roe': 1.1614, 'profit_dedt': 14674603.56, 'industry': '农药化肥', 'is_st': False, 'delist': ['', '']},
    '603829.SH': {'pe_ttm': 56.3095, 'pb': 1.902, 'circ_mv': 288526.34, 'total_mv': 288526.34, 'bps': 8.1439, 'roe': 1.9073, 'profit_dedt': 24615450.21, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '603836.SH': {'pe_ttm': 46.0807, 'pb': 1.5722, 'circ_mv': 266377.48, 'total_mv': 273329.88, 'bps': 6.0742, 'roe': 2.9361, 'profit_dedt': 51462952.01, 'industry': '仓储物流', 'is_st': False, 'delist': ['', '']},
    '603860.SH': {'pe_ttm': 36.1685, 'pb': 2.1171, 'circ_mv': 175501.76, 'total_mv': 175501.76, 'bps': 12.432, 'roe': 0.3108, 'profit_dedt': 2467250.93, 'industry': '建筑工程', 'is_st': False, 'delist': ['', '']},
    '603895.SH': {'pe_ttm': 164.0935, 'pb': 13.4102, 'circ_mv': 278522.16, 'total_mv': 278522.16, 'bps': 1.9217, 'roe': 3.7682, 'profit_dedt': 5441170.72, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '603900.SH': {'pe_ttm': 117.8736, 'pb': 1.0966, 'circ_mv': 214887.82, 'total_mv': 215000.68, 'bps': 5.7166, 'roe': 0.9793, 'profit_dedt': 18165467.0, 'industry': '服饰', 'is_st': False, 'delist': ['', '']},
    '603909.SH': {'pe_ttm': 22.7262, 'pb': 2.2539, 'circ_mv': 264061.85, 'total_mv': 264061.85, 'bps': 4.4944, 'roe': 4.4696, 'profit_dedt': 48601625.99, 'industry': '建筑工程', 'is_st': False, 'delist': ['', '']},
    '603937.SH': {'pe_ttm': 110.7407, 'pb': 1.6689, 'circ_mv': 259861.65, 'total_mv': 259861.65, 'bps': 7.4539, 'roe': 2.2616, 'profit_dedt': 26962198.6, 'industry': '铝', 'is_st': False, 'delist': ['', '']},
    '605003.SH': {'pe_ttm': 39.0645, 'pb': 1.8588, 'circ_mv': 266310.0, 'total_mv': 266310.0, 'bps': 13.0244, 'roe': 2.0147, 'profit_dedt': 29053734.09, 'industry': '纺织', 'is_st': False, 'delist': ['', '']},
    '605033.SH': {'pe_ttm': 41.6688, 'pb': 2.2257, 'circ_mv': 264992.0, 'total_mv': 264992.0, 'bps': 8.8062, 'roe': 4.358, 'profit_dedt': 46139848.99, 'industry': '农药化肥', 'is_st': False, 'delist': ['', '']},
    '605151.SH': {'pe_ttm': 73.6905, 'pb': 1.7975, 'circ_mv': 250405.37, 'total_mv': 250405.37, 'bps': 10.3531, 'roe': 2.4905, 'profit_dedt': 31823801.71, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '605155.SH': {'pe_ttm': 26.4344, 'pb': 1.9951, 'circ_mv': 267085.47, 'total_mv': 271490.15, 'bps': 7.1133, 'roe': 3.459, 'profit_dedt': 40129349.9, 'industry': '家居用品', 'is_st': False, 'delist': ['', '']},
    '605166.SH': {'pe_ttm': 27.2563, 'pb': 1.3269, 'circ_mv': 265006.03, 'total_mv': 265006.03, 'bps': 6.3455, 'roe': 3.1521, 'profit_dedt': 62176846.66, 'industry': '化纤', 'is_st': False, 'delist': ['', '']},
    '605259.SH': {'pe_ttm': 15.4989, 'pb': 1.6323, 'circ_mv': 299942.72, 'total_mv': 299942.72, 'bps': 10.6537, 'roe': 4.8121, 'profit_dedt': 87332657.02, 'industry': '农用机械', 'is_st': False, 'delist': ['', '']},
    '605266.SH': {'pe_ttm': 19.1529, 'pb': 0.9768, 'circ_mv': 247886.81, 'total_mv': 247886.81, 'bps': 16.4214, 'roe': 2.0593, 'profit_dedt': 43377249.04, 'industry': '医药商业', 'is_st': False, 'delist': ['', '']},
    '605567.SH': {'pe_ttm': 42.1068, 'pb': 1.7319, 'circ_mv': 192433.0, 'total_mv': 192433.0, 'bps': 5.5557, 'roe': 1.8486, 'profit_dedt': 18628433.94, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '688013.SH': {'pe_ttm': 39.7021, 'pb': 4.0692, 'circ_mv': 201347.04, 'total_mv': 201347.04, 'bps': 6.097, 'roe': 2.5917, 'profit_dedt': 5073030.67, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '688021.SH': {'pe_ttm': 42.9606, 'pb': 1.7145, 'circ_mv': 151375.54, 'total_mv': 151375.54, 'bps': 11.5544, 'roe': 2.0635, 'profit_dedt': 13660116.28, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '688067.SH': {'pe_ttm': 51.3129, 'pb': 3.1739, 'circ_mv': 166736.0, 'total_mv': 166736.0, 'bps': 7.7255, 'roe': 3.4015, 'profit_dedt': 14590779.79, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '688080.SH': {'pe_ttm': 22.703, 'pb': 2.2646, 'circ_mv': 269949.61, 'total_mv': 269949.61, 'bps': 16.184, 'roe': 3.1539, 'profit_dedt': 35137456.49, 'industry': '通信设备', 'is_st': False, 'delist': ['', '']},
    '688092.SH': {'pe_ttm': 58.3353, 'pb': 2.8463, 'circ_mv': 185993.33, 'total_mv': 185993.33, 'bps': 7.9049, 'roe': 1.1003, 'profit_dedt': 6468353.72, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '688113.SH': {'pe_ttm': 25.0877, 'pb': 2.3075, 'circ_mv': 237949.13, 'total_mv': 237949.13, 'bps': 16.0129, 'roe': 5.707, 'profit_dedt': 56416655.45, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '688129.SH': {'pe_ttm': 26.7205, 'pb': 2.5662, 'circ_mv': 272402.57, 'total_mv': 272402.57, 'bps': 8.8108, 'roe': 4.4747, 'profit_dedt': 27146006.14, 'industry': '染料涂料', 'is_st': False, 'delist': ['', '']},
    '688193.SH': {'pe_ttm': 118.6528, 'pb': 2.3655, 'circ_mv': 209124.81, 'total_mv': 209124.81, 'bps': 22.0632, 'roe': 1.0651, 'profit_dedt': 1370518.29, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '688255.SH': {'pe_ttm': 54.8058, 'pb': 2.6701, 'circ_mv': 285413.42, 'total_mv': 285413.42, 'bps': 9.73, 'roe': 2.6549, 'profit_dedt': 24095336.37, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '688273.SH': {'pe_ttm': 39.1038, 'pb': 2.2095, 'circ_mv': 295000.0, 'total_mv': 295000.0, 'bps': 13.3517, 'roe': 2.7408, 'profit_dedt': 33712444.36, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '688285.SH': {'pe_ttm': 51.5202, 'pb': 1.665, 'circ_mv': 285980.32, 'total_mv': 285980.32, 'bps': 4.5646, 'roe': 1.9052, 'profit_dedt': 31051026.66, 'industry': '运输设备', 'is_st': False, 'delist': ['', '']},
    '688314.SH': {'pe_ttm': 28.9685, 'pb': 3.3065, 'circ_mv': 246517.47, 'total_mv': 246517.47, 'bps': 9.1336, 'roe': 5.6575, 'profit_dedt': 37694138.73, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '688357.SH': {'pe_ttm': 30.08, 'pb': 1.5125, 'circ_mv': 263257.6, 'total_mv': 263257.6, 'bps': 17.3946, 'roe': 2.0301, 'profit_dedt': 35107641.48, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '688395.SH': {'pe_ttm': 52.1854, 'pb': 2.8657, 'circ_mv': 226885.97, 'total_mv': 226885.97, 'bps': 9.1426, 'roe': 3.213, 'profit_dedt': 21230782.8, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '688466.SH': {'pe_ttm': 84.4749, 'pb': 1.5153, 'circ_mv': 171015.88, 'total_mv': 171015.88, 'bps': 9.2388, 'roe': 1.1422, 'profit_dedt': 8188723.5, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '688468.SH': {'pe_ttm': 76.4334, 'pb': 1.8812, 'circ_mv': 268341.25, 'total_mv': 268341.25, 'bps': 3.5562, 'roe': 1.0951, 'profit_dedt': 9566055.4, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '688479.SH': {'pe_ttm': 180.1274, 'pb': 3.7169, 'circ_mv': 295134.73, 'total_mv': 686717.53, 'bps': 9.1771, 'roe': 0.627, 'profit_dedt': 1088983.34, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '688501.SH': {'pe_ttm': 21.8069, 'pb': 2.2718, 'circ_mv': 259135.44, 'total_mv': 259135.44, 'bps': 9.1821, 'roe': 4.6191, 'profit_dedt': 50328337.21, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '688517.SH': {'pe_ttm': 44.9721, 'pb': 3.4199, 'circ_mv': 255466.68, 'total_mv': 255466.68, 'bps': 5.468, 'roe': 2.3363, 'profit_dedt': 16749647.43, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '688565.SH': {'pe_ttm': 167.7794, 'pb': 3.4929, 'circ_mv': 173387.34, 'total_mv': 173387.34, 'bps': 3.3153, 'roe': 0.8443, 'profit_dedt': 1329920.75, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '688570.SH': {'pe_ttm': 181.3415, 'pb': 1.4877, 'circ_mv': 275713.0, 'total_mv': 634345.0, 'bps': 9.8475, 'roe': 0.1555, 'profit_dedt': 5154477.59, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '688581.SH': {'pe_ttm': 21.2938, 'pb': 1.7868, 'circ_mv': 232204.43, 'total_mv': 455296.81, 'bps': 31.4111, 'roe': 4.6644, 'profit_dedt': 99537281.69, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '688589.SH': {'pe_ttm': 194.9288, 'pb': 3.052, 'circ_mv': 292546.27, 'total_mv': 292546.27, 'bps': 6.5956, 'roe': 1.8382, 'profit_dedt': 6876743.04, 'industry': '半导体', 'is_st': False, 'delist': ['', '']},
    '688616.SH': {'pe_ttm': 19.3825, 'pb': 2.2486, 'circ_mv': 199289.29, 'total_mv': 199289.29, 'bps': 4.9408, 'roe': 4.4169, 'profit_dedt': 36001362.05, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '688623.SH': {'pe_ttm': 77.7844, 'pb': 2.5622, 'circ_mv': 225194.26, 'total_mv': 542146.71, 'bps': 24.7441, 'roe': 2.0635, 'profit_dedt': 31639526.88, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '688657.SH': {'pe_ttm': 68.7682, 'pb': 2.2759, 'circ_mv': 199260.24, 'total_mv': 318512.96, 'bps': 14.6669, 'roe': 1.5683, 'profit_dedt': 19085600.0, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '688681.SH': {'pe_ttm': 36.3458, 'pb': 2.65, 'circ_mv': 162133.83, 'total_mv': 162133.83, 'bps': 5.8453, 'roe': 2.2555, 'profit_dedt': 12065592.76, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '688712.SH': {'pe_ttm': 104.3717, 'pb': 7.0769, 'circ_mv': 140995.2, 'total_mv': 1289364.0, 'bps': 4.3691, 'roe': 7.0665, 'profit_dedt': 83876794.94, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '688718.SH': {'pe_ttm': 48.2215, 'pb': 3.1129, 'circ_mv': 254897.7, 'total_mv': 254897.7, 'bps': 4.7127, 'roe': 2.7718, 'profit_dedt': 19892098.37, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '688721.SH': {'pe_ttm': 133.2808, 'pb': 4.1034, 'circ_mv': 209086.16, 'total_mv': 506098.5, 'bps': 9.2388, 'roe': 1.3862, 'profit_dedt': 16604173.95, 'industry': '半导体', 'is_st': False, 'delist': ['', '']},
    '688727.SH': {'pe_ttm': 139.1791, 'pb': 7.5493, 'circ_mv': 234752.53, 'total_mv': 1952293.66, 'bps': 5.7555, 'roe': 3.3035, 'profit_dedt': 45334963.97, 'industry': '半导体', 'is_st': False, 'delist': ['', '']},
    '688755.SH': {'pe_ttm': 45.3689, 'pb': 2.6919, 'circ_mv': 241518.41, 'total_mv': 345945.6, 'bps': 11.2338, 'roe': 3.5125, 'profit_dedt': 40026119.14, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '688805.SH': {'pe_ttm': 70.4206, 'pb': 5.3261, 'circ_mv': 130220.29, 'total_mv': 651101.44, 'bps': 7.2904, 'roe': 4.0323, 'profit_dedt': 46524668.41, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '688811.SH': {'pe_ttm': 129.0938, 'pb': 5.9625, 'circ_mv': 171999.43, 'total_mv': 936624.61, 'bps': 3.1312, 'roe': 1.5197, 'profit_dedt': 14709438.34, 'industry': '小金属', 'is_st': False, 'delist': ['', '']},
    '920000.BJ': {'pe_ttm': 21.4104, 'pb': 1.9242, 'circ_mv': 82532.09, 'total_mv': 131377.44, 'bps': 7.4472, 'roe': 4.1559, 'profit_dedt': 25164751.03, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920002.BJ': {'pe_ttm': 51.5536, 'pb': 4.3549, 'circ_mv': 231637.61, 'total_mv': 330879.38, 'bps': 11.9269, 'roe': 4.7323, 'profit_dedt': 32699631.89, 'industry': '工程机械', 'is_st': False, 'delist': ['', '']},
    '920003.BJ': {'pe_ttm': 15.7062, 'pb': 2.554, 'circ_mv': 32761.98, 'total_mv': 152479.8, 'bps': 9.2256, 'roe': 7.9101, 'profit_dedt': 43328904.06, 'industry': '建筑工程', 'is_st': False, 'delist': ['', '']},
    '920005.BJ': {'pe_ttm': 39.4753, 'pb': 3.1931, 'circ_mv': 92254.63, 'total_mv': 199864.0, 'bps': 7.5412, 'roe': 4.5538, 'profit_dedt': 26615621.33, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '920007.BJ': {'pe_ttm': 26.2916, 'pb': 2.2027, 'circ_mv': 37635.59, 'total_mv': 143171.83, 'bps': 14.5954, 'roe': 3.2789, 'profit_dedt': 22267880.51, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920009.BJ': {'pe_ttm': 28.9128, 'pb': 3.8908, 'circ_mv': 35209.44, 'total_mv': 270868.18, 'bps': 8.9776, 'roe': 6.6828, 'profit_dedt': 37626245.19, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '920010.BJ': {'pe_ttm': 73.4027, 'pb': 2.9813, 'circ_mv': 155059.23, 'total_mv': 208705.0, 'bps': 2.9852, 'roe': 2.7971, 'profit_dedt': 18866559.55, 'industry': '供气供热', 'is_st': False, 'delist': ['', '']},
    '920011.BJ': {'pe_ttm': 18.2527, 'pb': 1.6856, 'circ_mv': 32698.8, 'total_mv': 145328.0, 'bps': 10.4294, 'roe': 4.1477, 'profit_dedt': 27840183.72, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920012.BJ': {'pe_ttm': 34.0176, 'pb': 2.7776, 'circ_mv': 92049.12, 'total_mv': 235243.9, 'bps': 17.1728, 'roe': 5.0888, 'profit_dedt': 35889927.52, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '920015.BJ': {'pe_ttm': 40.7396, 'pb': 3.5001, 'circ_mv': 174054.11, 'total_mv': 589715.0, 'bps': 12.4283, 'roe': 3.7951, 'profit_dedt': 58892839.25, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '920016.BJ': {'pe_ttm': 50.3126, 'pb': 2.9017, 'circ_mv': 50671.08, 'total_mv': 113933.25, 'bps': 5.1004, 'roe': 3.3207, 'profit_dedt': 12274181.46, 'industry': '日用化工', 'is_st': False, 'delist': ['', '']},
    '920017.BJ': {'pe_ttm': 17.2816, 'pb': 1.2196, 'circ_mv': 196908.96, 'total_mv': 199087.36, 'bps': 13.1759, 'roe': 3.2405, 'profit_dedt': 49386925.03, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '920018.BJ': {'pe_ttm': 21.1032, 'pb': 2.565, 'circ_mv': 97940.45, 'total_mv': 247630.54, 'bps': 5.8324, 'roe': 6.0998, 'profit_dedt': 55650856.04, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920019.BJ': {'pe_ttm': 77.4653, 'pb': 3.4798, 'circ_mv': 261355.64, 'total_mv': 328016.05, 'bps': 4.483, 'roe': 5.0282, 'profit_dedt': 47062695.33, 'industry': '建筑工程', 'is_st': False, 'delist': ['', '']},
    '920020.BJ': {'pe_ttm': 17.6712, 'pb': 2.8045, 'circ_mv': 82066.93, 'total_mv': 332018.82, 'bps': 5.2629, 'roe': 8.665, 'profit_dedt': 99839099.53, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920021.BJ': {'pe_ttm': 153.1144, 'pb': 3.6688, 'circ_mv': 189626.88, 'total_mv': 236693.99, 'bps': 2.0879, 'roe': 1.0163, 'profit_dedt': 2393150.28, 'industry': '影视音像', 'is_st': False, 'delist': ['', '']},
    '920022.BJ': {'pe_ttm': 15.1854, 'pb': 2.4282, 'circ_mv': 41834.7, 'total_mv': 102063.13, 'bps': 6.968, 'roe': 6.4721, 'profit_dedt': 26445641.46, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920025.BJ': {'pe_ttm': 39.663, 'pb': 5.3207, 'circ_mv': 63699.0, 'total_mv': 281025.0, 'bps': 3.41, 'roe': 6.0794, 'profit_dedt': 33781710.08, 'industry': '', 'is_st': False, 'delist': ['', '']},
    '920026.BJ': {'pe_ttm': 45.8034, 'pb': 3.0586, 'circ_mv': 145958.3, 'total_mv': 193620.22, 'bps': 5.5091, 'roe': 2.747, 'profit_dedt': 16555924.02, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '920027.BJ': {'pe_ttm': 25.166, 'pb': 2.5438, 'circ_mv': 82135.77, 'total_mv': 117379.59, 'bps': 4.4815, 'roe': 3.2624, 'profit_dedt': 14470681.91, 'industry': '运输设备', 'is_st': False, 'delist': ['', '']},
    '920028.BJ': {'pe_ttm': 22.4446, 'pb': 2.1526, 'circ_mv': 98803.55, 'total_mv': 249991.56, 'bps': 7.066, 'roe': 5.305, 'profit_dedt': 49723263.77, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '920033.BJ': {'pe_ttm': 42.1654, 'pb': 2.0236, 'circ_mv': 108924.42, 'total_mv': 156662.85, 'bps': 6.4984, 'roe': 4.5237, 'profit_dedt': 29458939.77, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '920035.BJ': {'pe_ttm': 25.8216, 'pb': 2.7836, 'circ_mv': 36824.11, 'total_mv': 162442.8, 'bps': 8.4065, 'roe': 5.4702, 'profit_dedt': 31381079.81, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920036.BJ': {'pe_ttm': 24.7531, 'pb': 2.7983, 'circ_mv': 40307.1, 'total_mv': 167945.76, 'bps': 11.0283, 'roe': 4.4379, 'profit_dedt': 19625203.92, 'industry': 'IT设备', 'is_st': False, 'delist': ['', '']},
    '920037.BJ': {'pe_ttm': 21.8163, 'pb': 5.3193, 'circ_mv': 296241.53, 'total_mv': 484533.9, 'bps': 7.1137, 'roe': 13.713, 'profit_dedt': 118491655.63, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920038.BJ': {'pe_ttm': 14.3301, 'pb': 4.2467, 'circ_mv': 203553.8, 'total_mv': 399732.39, 'bps': 11.0229, 'roe': 14.4981, 'profit_dedt': 122723433.09, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '920039.BJ': {'pe_ttm': 41.4672, 'pb': 2.1696, 'circ_mv': 115980.2, 'total_mv': 115980.28, 'bps': 3.48, 'roe': 2.622, 'profit_dedt': 13767334.7, 'industry': '建筑工程', 'is_st': False, 'delist': ['', '']},
    '920046.BJ': {'pe_ttm': 125.4678, 'pb': 7.6286, 'circ_mv': 107156.2, 'total_mv': 193803.4, 'bps': 2.5116, 'roe': 4.0717, 'profit_dedt': 9975194.04, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920050.BJ': {'pe_ttm': 17.5699, 'pb': 1.7949, 'circ_mv': 50325.37, 'total_mv': 188148.55, 'bps': 15.4882, 'roe': 6.6554, 'profit_dedt': 63529039.72, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '920055.BJ': {'pe_ttm': 12.1382, 'pb': 1.4105, 'circ_mv': 38601.9, 'total_mv': 171564.0, 'bps': 17.8873, 'roe': 6.2494, 'profit_dedt': 57640348.03, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920056.BJ': {'pe_ttm': 28.1816, 'pb': 2.1254, 'circ_mv': 66866.06, 'total_mv': 114613.46, 'bps': 6.6, 'roe': 2.8211, 'profit_dedt': 13422844.65, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '920058.BJ': {'pe_ttm': 41.7195, 'pb': 3.6891, 'circ_mv': 62611.67, 'total_mv': 127191.31, 'bps': 6.1506, 'roe': 4.4838, 'profit_dedt': 14984417.41, 'industry': '摩托车', 'is_st': False, 'delist': ['', '']},
    '920059.BJ': {'pe_ttm': 14.7364, 'pb': 2.5006, 'circ_mv': 39396.04, 'total_mv': 192345.37, 'bps': 7.0429, 'roe': 4.5487, 'profit_dedt': 29863916.52, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920060.BJ': {'pe_ttm': 43.2906, 'pb': 4.0025, 'circ_mv': 256973.78, 'total_mv': 412041.04, 'bps': 6.7707, 'roe': 3.3473, 'profit_dedt': 33122591.22, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '920061.BJ': {'pe_ttm': 227.6448, 'pb': 4.5878, 'circ_mv': 70777.19, 'total_mv': 127280.42, 'bps': 3.919, 'roe': 1.1806, 'profit_dedt': 1873709.97, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920065.BJ': {'pe_ttm': 14.0164, 'pb': 3.7844, 'circ_mv': 52809.75, 'total_mv': 302608.25, 'bps': 10.9913, 'roe': 14.9763, 'profit_dedt': 109116861.06, 'industry': '互联网', 'is_st': False, 'delist': ['', '']},
    '920066.BJ': {'pe_ttm': 35.8575, 'pb': 2.984, 'circ_mv': 39170.33, 'total_mv': 123883.91, 'bps': 6.6053, 'roe': 4.8024, 'profit_dedt': 19147616.97, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '920068.BJ': {'pe_ttm': 115.4872, 'pb': 7.7647, 'circ_mv': 264865.87, 'total_mv': 999790.02, 'bps': 1.964, 'roe': 0.8071, 'profit_dedt': 9977488.82, 'industry': '小金属', 'is_st': False, 'delist': ['', '']},
    '920069.BJ': {'pe_ttm': 17.0754, 'pb': 2.1191, 'circ_mv': 46951.37, 'total_mv': 168337.97, 'bps': 15.0128, 'roe': 7.6123, 'profit_dedt': 51456325.86, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '920071.BJ': {'pe_ttm': 32.7932, 'pb': 4.4038, 'circ_mv': 79537.5, 'total_mv': 643875.0, 'bps': 7.5872, 'roe': 8.4811, 'profit_dedt': 118587270.29, 'industry': '小金属', 'is_st': False, 'delist': ['', '']},
    '920072.BJ': {'pe_ttm': 25.116, 'pb': 2.3317, 'circ_mv': 32589.65, 'total_mv': 172579.8, 'bps': 9.6627, 'roe': 5.6513, 'profit_dedt': 34590126.54, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '920075.BJ': {'pe_ttm': 149.2062, 'pb': 2.6277, 'circ_mv': 54765.64, 'total_mv': 101046.59, 'bps': 5.9329, 'roe': 1.5454, 'profit_dedt': 6370023.72, 'industry': '广告包装', 'is_st': False, 'delist': ['', '']},
    '920076.BJ': {'pe_ttm': 17.5546, 'pb': 1.4727, 'circ_mv': 46113.58, 'total_mv': 146594.54, 'bps': 8.5013, 'roe': 5.0869, 'profit_dedt': 42632880.89, 'industry': '其他建材', 'is_st': False, 'delist': ['', '']},
    '920078.BJ': {'pe_ttm': 29.1486, 'pb': 2.05, 'circ_mv': 65967.36, 'total_mv': 203040.0, 'bps': 8.2535, 'roe': 2.5865, 'profit_dedt': 22152740.28, 'industry': '小金属', 'is_st': False, 'delist': ['', '']},
    '920079.BJ': {'pe_ttm': 11.7112, 'pb': 2.6873, 'circ_mv': 90097.92, 'total_mv': 516641.21, 'bps': 5.2483, 'roe': 11.9011, 'profit_dedt': 199788599.38, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920080.BJ': {'pe_ttm': 20.4226, 'pb': 2.9489, 'circ_mv': 45698.67, 'total_mv': 165087.0, 'bps': 6.7448, 'roe': 6.9406, 'profit_dedt': 36855446.18, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920081.BJ': {'pe_ttm': 12.1107, 'pb': 2.8345, 'circ_mv': 69887.7, 'total_mv': 296716.2, 'bps': 19.5297, 'roe': 16.8817, 'profit_dedt': 160241582.35, 'industry': '家用电器', 'is_st': False, 'delist': ['', '']},
    '920082.BJ': {'pe_ttm': 47.1681, 'pb': 2.9596, 'circ_mv': 44348.85, 'total_mv': 157710.17, 'bps': 2.6186, 'roe': 4.0383, 'profit_dedt': 14731845.18, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '920083.BJ': {'pe_ttm': 53.2783, 'pb': 5.2249, 'circ_mv': 145590.92, 'total_mv': 341812.82, 'bps': 7.33, 'roe': 5.567, 'profit_dedt': 30254274.79, 'industry': '矿物制品', 'is_st': False, 'delist': ['', '']},
    '920086.BJ': {'pe_ttm': 18.0235, 'pb': 1.9414, 'circ_mv': 54689.46, 'total_mv': 167192.64, 'bps': 10.2915, 'roe': 6.4327, 'profit_dedt': 45680101.29, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920088.BJ': {'pe_ttm': 78.1344, 'pb': 4.7521, 'circ_mv': 124160.97, 'total_mv': 338018.42, 'bps': 8.08, 'roe': 1.9721, 'profit_dedt': 13020167.82, 'industry': '石油开采', 'is_st': False, 'delist': ['', '']},
    '920089.BJ': {'pe_ttm': 12.8138, 'pb': 1.5089, 'circ_mv': 113012.08, 'total_mv': 198414.69, 'bps': 6.7136, 'roe': 4.7425, 'profit_dedt': 60367075.56, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '920093.BJ': {'pe_ttm': 11.9578, 'pb': 4.0951, 'circ_mv': 47602.8, 'total_mv': 266349.0, 'bps': 7.2563, 'roe': 17.3709, 'profit_dedt': 117068365.84, 'industry': '纺织机械', 'is_st': False, 'delist': ['', '']},
    '920096.BJ': {'pe_ttm': 27.2489, 'pb': 2.942, 'circ_mv': 47968.2, 'total_mv': 204309.0, 'bps': 10.0647, 'roe': 6.1015, 'profit_dedt': 33763700.12, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '920098.BJ': {'pe_ttm': 26.0591, 'pb': 1.4996, 'circ_mv': 96353.66, 'total_mv': 141497.44, 'bps': 11.6, 'roe': 2.5422, 'profit_dedt': 22531330.94, 'industry': '橡胶', 'is_st': False, 'delist': ['', '']},
    '920100.BJ': {'pe_ttm': 55.4773, 'pb': 6.4624, 'circ_mv': 79564.35, 'total_mv': 300079.09, 'bps': 4.4937, 'roe': 5.4532, 'profit_dedt': 23776434.89, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920101.BJ': {'pe_ttm': 19.6897, 'pb': 1.8384, 'circ_mv': 162705.54, 'total_mv': 198818.26, 'bps': 8.6652, 'roe': 4.529, 'profit_dedt': 46207255.41, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920106.BJ': {'pe_ttm': 16.756, 'pb': 3.1257, 'circ_mv': 144389.83, 'total_mv': 187154.7, 'bps': 10.5802, 'roe': 7.7565, 'profit_dedt': 41584891.82, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920107.BJ': {'pe_ttm': 14.3649, 'pb': 2.3315, 'circ_mv': 35915.32, 'total_mv': 159623.64, 'bps': 11.8134, 'roe': 8.7709, 'profit_dedt': 58361843.03, 'industry': '染料涂料', 'is_st': False, 'delist': ['', '']},
    '920108.BJ': {'pe_ttm': 124.622, 'pb': 3.0602, 'circ_mv': 61876.57, 'total_mv': 146025.6, 'bps': 2.771, 'roe': 4.3469, 'profit_dedt': 16612134.99, 'industry': '家用电器', 'is_st': False, 'delist': ['', '']},
    '920110.BJ': {'pe_ttm': 25.3228, 'pb': 2.7483, 'circ_mv': 41604.32, 'total_mv': 99918.0, 'bps': 9.3222, 'roe': 3.962, 'profit_dedt': 13847155.25, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920111.BJ': {'pe_ttm': 15.9284, 'pb': 3.6083, 'circ_mv': 192963.46, 'total_mv': 397660.98, 'bps': 7.103, 'roe': 18.0483, 'profit_dedt': 175796977.28, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920112.BJ': {'pe_ttm': 13.5371, 'pb': 1.8423, 'circ_mv': 110685.49, 'total_mv': 174977.67, 'bps': 7.9954, 'roe': 6.5486, 'profit_dedt': 56261461.63, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920117.BJ': {'pe_ttm': 20.5798, 'pb': 3.2928, 'circ_mv': 54901.23, 'total_mv': 244005.47, 'bps': 10.9979, 'roe': 9.1361, 'profit_dedt': 61800058.99, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920118.BJ': {'pe_ttm': 18.6414, 'pb': 1.7735, 'circ_mv': 64245.15, 'total_mv': 106007.55, 'bps': 11.5591, 'roe': 7.7081, 'profit_dedt': 44704142.24, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '920119.BJ': {'pe_ttm': 18.737, 'pb': 2.6249, 'circ_mv': 144799.41, 'total_mv': 530307.95, 'bps': 20.0087, 'roe': 9.8934, 'profit_dedt': 157572731.5, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920121.BJ': {'pe_ttm': 16.2724, 'pb': 2.2244, 'circ_mv': 32377.12, 'total_mv': 179375.12, 'bps': 12.2057, 'roe': 7.3025, 'profit_dedt': 54569353.3, 'industry': '广告包装', 'is_st': False, 'delist': ['', '']},
    '920122.BJ': {'pe_ttm': 83.01, 'pb': 6.1876, 'circ_mv': 86149.69, 'total_mv': 212618.06, 'bps': 3.7252, 'roe': 3.6121, 'profit_dedt': 11290861.49, 'industry': '综合类', 'is_st': False, 'delist': ['', '']},
    '920123.BJ': {'pe_ttm': 27.7715, 'pb': 2.8933, 'circ_mv': 70313.66, 'total_mv': 120686.28, 'bps': 3.7397, 'roe': 5.2179, 'profit_dedt': 21335895.81, 'industry': '日用化工', 'is_st': False, 'delist': ['', '']},
    '920124.BJ': {'pe_ttm': 27.1262, 'pb': 1.6927, 'circ_mv': 93046.41, 'total_mv': 180710.98, 'bps': 7.1779, 'roe': 3.2152, 'profit_dedt': 34387534.82, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '920126.BJ': {'pe_ttm': 17.5214, 'pb': 1.555, 'circ_mv': 43551.3, 'total_mv': 181428.0, 'bps': 6.27, 'roe': 6.7377, 'profit_dedt': 65246551.59, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920128.BJ': {'pe_ttm': 204.1321, 'pb': 2.9408, 'circ_mv': 44687.7, 'total_mv': 149308.41, 'bps': 6.2534, 'roe': 1.2245, 'profit_dedt': 4809729.09, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '920132.BJ': {'pe_ttm': 45.237, 'pb': 3.6071, 'circ_mv': 32538.57, 'total_mv': 95827.37, 'bps': 2.4674, 'roe': 5.5479, 'profit_dedt': 14673390.95, 'industry': '家居用品', 'is_st': False, 'delist': ['', '']},
    '920136.BJ': {'pe_ttm': 11.4243, 'pb': 1.3727, 'circ_mv': 43386.0, 'total_mv': 165280.0, 'bps': 15.0503, 'roe': 9.1335, 'profit_dedt': 43509337.04, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920138.BJ': {'pe_ttm': 23.5418, 'pb': 3.0974, 'circ_mv': 115506.11, 'total_mv': 1444770.83, 'bps': 11.6625, 'roe': 6.9255, 'profit_dedt': 284649045.37, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '920156.BJ': {'pe_ttm': 16.3869, 'pb': 2.9251, 'circ_mv': 75912.88, 'total_mv': 364107.64, 'bps': 11.9551, 'roe': 13.5393, 'profit_dedt': 128220481.31, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920158.BJ': {'pe_ttm': 41.2161, 'pb': 3.0134, 'circ_mv': 41434.64, 'total_mv': 154898.91, 'bps': 2.5752, 'roe': 0.258, 'profit_dedt': 117510.32, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920159.BJ': {'pe_ttm': 19.6165, 'pb': 1.9555, 'circ_mv': 43294.32, 'total_mv': 214776.0, 'bps': 14.4519, 'roe': 10.2687, 'profit_dedt': 95207339.41, 'industry': '农药化肥', 'is_st': False, 'delist': ['', '']},
    '920160.BJ': {'pe_ttm': 23.9995, 'pb': 3.8275, 'circ_mv': 47586.1, 'total_mv': 211493.76, 'bps': 4.8778, 'roe': 8.7786, 'profit_dedt': 46840104.95, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '920161.BJ': {'pe_ttm': 33.1758, 'pb': 2.5498, 'circ_mv': 142112.09, 'total_mv': 262196.32, 'bps': 7.5612, 'roe': 4.9122, 'profit_dedt': 44096342.18, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '920163.BJ': {'pe_ttm': 21.7492, 'pb': 2.3265, 'circ_mv': 57101.32, 'total_mv': 121050.39, 'bps': 4.0447, 'roe': 6.0693, 'profit_dedt': 30031400.47, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '920165.BJ': {'pe_ttm': 21.0332, 'pb': 3.2711, 'circ_mv': 31312.43, 'total_mv': 157374.38, 'bps': 11.7249, 'roe': 8.1399, 'profit_dedt': 36091569.86, 'industry': '日用化工', 'is_st': False, 'delist': ['', '']},
    '920166.BJ': {'pe_ttm': 18.0555, 'pb': 2.277, 'circ_mv': 46151.06, 'total_mv': 146221.18, 'bps': 8.5288, 'roe': 5.3801, 'profit_dedt': 30003276.07, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '920167.BJ': {'pe_ttm': 23.5919, 'pb': 2.1803, 'circ_mv': 87195.33, 'total_mv': 146775.35, 'bps': 6.1183, 'roe': 7.3145, 'profit_dedt': 46990231.97, 'industry': '小金属', 'is_st': False, 'delist': ['', '']},
    '920168.BJ': {'pe_ttm': 17.9247, 'pb': 1.4407, 'circ_mv': 42712.56, 'total_mv': 113962.87, 'bps': 10.5225, 'roe': 2.5438, 'profit_dedt': 16171098.64, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920169.BJ': {'pe_ttm': 55.3835, 'pb': 4.4474, 'circ_mv': 100666.34, 'total_mv': 159984.18, 'bps': 4.4521, 'roe': 6.9164, 'profit_dedt': 2473434.65, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '920171.BJ': {'pe_ttm': 437.6738, 'pb': 6.976, 'circ_mv': 103138.77, 'total_mv': 152855.68, 'bps': 2.1861, 'roe': 0.4907, 'profit_dedt': 1886787.36, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '920174.BJ': {'pe_ttm': 16.727, 'pb': 2.3271, 'circ_mv': 169673.74, 'total_mv': 658306.34, 'bps': 13.0421, 'roe': 10.3446, 'profit_dedt': 270744927.38, 'industry': '工程机械', 'is_st': False, 'delist': ['', '']},
    '920176.BJ': {'pe_ttm': 37.7519, 'pb': 5.4547, 'circ_mv': 47260.81, 'total_mv': 349862.01, 'bps': 12.828, 'roe': 6.7859, 'profit_dedt': 39756983.8, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '920177.BJ': {'pe_ttm': 16.4316, 'pb': 1.7312, 'circ_mv': 41652.08, 'total_mv': 117615.36, 'bps': 12.985, 'roe': 6.0354, 'profit_dedt': 32262690.86, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920178.BJ': {'pe_ttm': 37.8807, 'pb': 5.2245, 'circ_mv': 152520.55, 'total_mv': 522295.48, 'bps': 15.1614, 'roe': 8.2533, 'profit_dedt': 65075099.06, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920180.BJ': {'pe_ttm': 21.0776, 'pb': 1.7796, 'circ_mv': 41724.59, 'total_mv': 137022.73, 'bps': 6.5184, 'roe': 3.7555, 'profit_dedt': 22356985.44, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '920181.BJ': {'pe_ttm': 28.8597, 'pb': 3.4839, 'circ_mv': 64077.9, 'total_mv': 276264.0, 'bps': 18.3561, 'roe': 8.1642, 'profit_dedt': 51195591.43, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '920183.BJ': {'pe_ttm': 18.2531, 'pb': 3.1198, 'circ_mv': 32906.01, 'total_mv': 139660.06, 'bps': 9.1447, 'roe': 9.9496, 'profit_dedt': 35173166.2, 'industry': 'IT设备', 'is_st': False, 'delist': ['', '']},
    '920184.BJ': {'pe_ttm': 65.9078, 'pb': 2.3388, 'circ_mv': 99019.58, 'total_mv': 145953.98, 'bps': 4.7375, 'roe': 1.2142, 'profit_dedt': 3433304.34, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '920187.BJ': {'pe_ttm': 16.3883, 'pb': 1.2175, 'circ_mv': 55834.44, 'total_mv': 152187.36, 'bps': 13.8154, 'roe': 3.0716, 'profit_dedt': 28720721.89, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920188.BJ': {'pe_ttm': 19.1276, 'pb': 2.2195, 'circ_mv': 42180.76, 'total_mv': 176858.5, 'bps': 9.6011, 'roe': 8.4667, 'profit_dedt': 53794368.69, 'industry': '橡胶', 'is_st': False, 'delist': ['', '']},
    '920189.BJ': {'pe_ttm': 27.8354, 'pb': 4.3743, 'circ_mv': 165830.9, 'total_mv': 276315.14, 'bps': 5.2553, 'roe': 8.1432, 'profit_dedt': 48132332.48, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '920190.BJ': {'pe_ttm': 79.485, 'pb': 2.6776, 'circ_mv': 227724.59, 'total_mv': 229000.0, 'bps': 8.5525, 'roe': 2.3647, 'profit_dedt': 19510536.11, 'industry': 'IT设备', 'is_st': False, 'delist': ['', '']},
    '920191.BJ': {'pe_ttm': 18.7294, 'pb': 1.4732, 'circ_mv': 34446.64, 'total_mv': 153092.23, 'bps': 5.8579, 'roe': 5.4079, 'profit_dedt': 43166293.44, 'industry': '其他建材', 'is_st': False, 'delist': ['', '']},
    '920193.BJ': {'pe_ttm': 34.7735, 'pb': 4.091, 'circ_mv': 71845.2, 'total_mv': 317404.11, 'bps': 6.969, 'roe': 8.8702, 'profit_dedt': 56255505.74, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '920195.BJ': {'pe_ttm': 11.8251, 'pb': 1.4807, 'circ_mv': 89368.6, 'total_mv': 123811.89, 'bps': 8.5296, 'roe': 3.861, 'profit_dedt': 30244790.68, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920198.BJ': {'pe_ttm': 38.0708, 'pb': 2.8816, 'circ_mv': 54681.62, 'total_mv': 125218.36, 'bps': 2.6929, 'roe': 0.8947, 'profit_dedt': 5404655.1, 'industry': 'IT设备', 'is_st': False, 'delist': ['', '']},
    '920200.BJ': {'pe_ttm': 24.2996, 'pb': 2.7197, 'circ_mv': 141980.8, 'total_mv': 324992.0, 'bps': 11.7661, 'roe': 4.9664, 'profit_dedt': 45819591.08, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920204.BJ': {'pe_ttm': 45.5847, 'pb': 2.4065, 'circ_mv': 63888.17, 'total_mv': 120499.8, 'bps': 5.3106, 'roe': 3.2153, 'profit_dedt': 15501932.9, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '920206.BJ': {'pe_ttm': 18.3052, 'pb': 3.0246, 'circ_mv': 102723.68, 'total_mv': 267010.52, 'bps': 12.3386, 'roe': 11.7019, 'profit_dedt': 85364777.49, 'industry': '染料涂料', 'is_st': False, 'delist': ['', '']},
    '920207.BJ': {'pe_ttm': 83.9244, 'pb': 6.5779, 'circ_mv': 116965.15, 'total_mv': 191489.02, 'bps': 3.1576, 'roe': 2.3832, 'profit_dedt': 6476645.95, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '920208.BJ': {'pe_ttm': 10.7021, 'pb': 2.0209, 'circ_mv': 183188.07, 'total_mv': 211571.39, 'bps': 5.6213, 'roe': 7.0284, 'profit_dedt': 71994843.59, 'industry': '建筑工程', 'is_st': False, 'delist': ['', '']},
    '920211.BJ': {'pe_ttm': 53.6604, 'pb': 6.8404, 'circ_mv': 99267.5, 'total_mv': 294464.0, 'bps': 12.5138, 'roe': 8.8989, 'profit_dedt': 30272719.7, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920212.BJ': {'pe_ttm': 87.729, 'pb': 2.518, 'circ_mv': 52015.08, 'total_mv': 106936.7, 'bps': 4.0027, 'roe': 0.5785, 'profit_dedt': 2427133.97, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '920218.BJ': {'pe_ttm': 26.3706, 'pb': 1.6119, 'circ_mv': 31298.2, 'total_mv': 139101.79, 'bps': 9.213, 'roe': 2.6324, 'profit_dedt': 11978950.7, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '920220.BJ': {'pe_ttm': 19.5918, 'pb': 2.236, 'circ_mv': 80869.76, 'total_mv': 258204.42, 'bps': 17.4416, 'roe': 4.6087, 'profit_dedt': 39914859.71, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920221.BJ': {'pe_ttm': 26.7279, 'pb': 3.2675, 'circ_mv': 74193.23, 'total_mv': 142142.7, 'bps': 3.6664, 'roe': 6.5946, 'profit_dedt': 27834763.51, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920222.BJ': {'pe_ttm': 34.8088, 'pb': 4.3596, 'circ_mv': 27796.47, 'total_mv': 205895.63, 'bps': 5.5143, 'roe': 6.6028, 'profit_dedt': 25920525.43, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920225.BJ': {'pe_ttm': 25.754, 'pb': 2.6012, 'circ_mv': 129885.2, 'total_mv': 199135.74, 'bps': 6.03, 'roe': 6.2483, 'profit_dedt': 45422037.27, 'industry': '橡胶', 'is_st': False, 'delist': ['', '']},
    '920227.BJ': {'pe_ttm': 43.0209, 'pb': 4.806, 'circ_mv': 87439.4, 'total_mv': 192176.3, 'bps': 10.2684, 'roe': 5.0834, 'profit_dedt': 19141813.14, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '920230.BJ': {'pe_ttm': 82.2103, 'pb': 2.7521, 'circ_mv': 53248.21, 'total_mv': 113234.88, 'bps': 4.095, 'roe': 1.2928, 'profit_dedt': 3553567.68, 'industry': '生物制药', 'is_st': False, 'delist': ['', '']},
    '920237.BJ': {'pe_ttm': 41.3432, 'pb': 2.1916, 'circ_mv': 111505.47, 'total_mv': 113658.37, 'bps': 5.4571, 'roe': 1.1215, 'profit_dedt': 3591107.69, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920238.BJ': {'pe_ttm': 12.0086, 'pb': 4.9354, 'circ_mv': 115781.81, 'total_mv': 568191.44, 'bps': 14.9236, 'roe': 39.6609, 'profit_dedt': 379226418.26, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '920242.BJ': {'pe_ttm': 16.5393, 'pb': 1.843, 'circ_mv': 70820.51, 'total_mv': 126529.0, 'bps': 7.287, 'roe': 5.9395, 'profit_dedt': 40009857.71, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920245.BJ': {'pe_ttm': 48.5255, 'pb': 2.6128, 'circ_mv': 71280.43, 'total_mv': 92558.09, 'bps': 5.1286, 'roe': 4.7865, 'profit_dedt': 12161999.77, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '920247.BJ': {'pe_ttm': 58.538, 'pb': 5.6749, 'circ_mv': 198446.52, 'total_mv': 273559.69, 'bps': 2.5498, 'roe': 4.1671, 'profit_dedt': 18721027.25, 'industry': '橡胶', 'is_st': False, 'delist': ['', '']},
    '920249.BJ': {'pe_ttm': 46.455, 'pb': 4.522, 'circ_mv': 108711.87, 'total_mv': 367661.36, 'bps': 1.9283, 'roe': 6.0261, 'profit_dedt': 50407183.39, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '920258.BJ': {'pe_ttm': 31.8575, 'pb': 4.7863, 'circ_mv': 127807.71, 'total_mv': 388000.0, 'bps': 2.5236, 'roe': 11.4809, 'profit_dedt': 98192297.47, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '920260.BJ': {'pe_ttm': 21.7654, 'pb': 1.894, 'circ_mv': 79663.37, 'total_mv': 83357.75, 'bps': 4.2502, 'roe': 2.7303, 'profit_dedt': 11149588.17, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920261.BJ': {'pe_ttm': 18.2102, 'pb': 2.3834, 'circ_mv': 213768.43, 'total_mv': 388371.09, 'bps': 5.597, 'roe': 8.47, 'profit_dedt': 131879409.06, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '920262.BJ': {'pe_ttm': 24.3664, 'pb': 3.0012, 'circ_mv': 52938.07, 'total_mv': 113872.53, 'bps': 4.5748, 'roe': 7.3317, 'profit_dedt': 26605578.55, 'industry': '纺织', 'is_st': False, 'delist': ['', '']},
    '920268.BJ': {'pe_ttm': 22.7949, 'pb': 4.5469, 'circ_mv': 43026.55, 'total_mv': 161407.6, 'bps': 9.7, 'roe': 10.3138, 'profit_dedt': 34823321.45, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '920269.BJ': {'pe_ttm': 13.6066, 'pb': 2.6885, 'circ_mv': 29608.64, 'total_mv': 199620.0, 'bps': 16.5505, 'roe': 8.0838, 'profit_dedt': 60233682.16, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920270.BJ': {'pe_ttm': 54.0879, 'pb': 2.8771, 'circ_mv': 91604.89, 'total_mv': 117902.23, 'bps': 3.92, 'roe': 1.9692, 'profit_dedt': 7276955.24, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920271.BJ': {'pe_ttm': 16.2668, 'pb': 1.857, 'circ_mv': 51522.48, 'total_mv': 116432.47, 'bps': 4.7928, 'roe': 2.9481, 'profit_dedt': 17582852.75, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920273.BJ': {'pe_ttm': 36.1359, 'pb': 2.7593, 'circ_mv': 117289.25, 'total_mv': 176194.82, 'bps': 6.1972, 'roe': 3.5124, 'profit_dedt': 18093702.0, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '920274.BJ': {'pe_ttm': 53.3842, 'pb': 2.475, 'circ_mv': 122650.67, 'total_mv': 122650.67, 'bps': 6.0929, 'roe': 3.3963, 'profit_dedt': 12786300.94, 'industry': '广告包装', 'is_st': False, 'delist': ['', '']},
    '920278.BJ': {'pe_ttm': 82.6988, 'pb': 2.7876, 'circ_mv': 61061.44, 'total_mv': 108015.5, 'bps': 2.2206, 'roe': 0.9173, 'profit_dedt': 2517899.31, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '920288.BJ': {'pe_ttm': 18.8578, 'pb': 2.363, 'circ_mv': 31182.72, 'total_mv': 131780.13, 'bps': 10.7804, 'roe': 6.1274, 'profit_dedt': 34302012.13, 'industry': '造纸', 'is_st': False, 'delist': ['', '']},
    '920289.BJ': {'pe_ttm': 22.3134, 'pb': 6.5506, 'circ_mv': 38133.59, 'total_mv': 179452.21, 'bps': 6.55, 'roe': 18.8776, 'profit_dedt': 57296869.6, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920299.BJ': {'pe_ttm': 57.5685, 'pb': 4.2551, 'circ_mv': 60219.98, 'total_mv': 119484.9, 'bps': 3.1163, 'roe': 2.1992, 'profit_dedt': 5613695.9, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920304.BJ': {'pe_ttm': 32.4217, 'pb': 2.9453, 'circ_mv': 124324.6, 'total_mv': 155635.88, 'bps': 3.1168, 'roe': 3.1224, 'profit_dedt': 11041440.01, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '920339.BJ': {'pe_ttm': 85.8992, 'pb': 3.95, 'circ_mv': 157980.11, 'total_mv': 230112.97, 'bps': 2.6456, 'roe': 0.6859, 'profit_dedt': 3338196.63, 'industry': '家用电器', 'is_st': False, 'delist': ['', '']},
    '920344.BJ': {'pe_ttm': 222.1446, 'pb': 4.7253, 'circ_mv': 276928.07, 'total_mv': 296607.35, 'bps': 5.1532, 'roe': 0.279, 'profit_dedt': 981195.68, 'industry': '生物制药', 'is_st': False, 'delist': ['', '']},
    '920346.BJ': {'pe_ttm': 26.3854, 'pb': 2.554, 'circ_mv': 67452.04, 'total_mv': 121221.61, 'bps': 5.8849, 'roe': 4.6065, 'profit_dedt': 18292937.43, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '920351.BJ': {'pe_ttm': 74.9722, 'pb': 2.3615, 'circ_mv': 48286.49, 'total_mv': 106698.95, 'bps': 5.1324, 'roe': 3.3818, 'profit_dedt': 14292546.46, 'industry': '仓储物流', 'is_st': False, 'delist': ['', '']},
    '920357.BJ': {'pe_ttm': 23.3687, 'pb': 3.6075, 'circ_mv': 92545.14, 'total_mv': 153032.88, 'bps': 4.07, 'roe': 6.9911, 'profit_dedt': 28252753.75, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '920363.BJ': {'pe_ttm': 217.6468, 'pb': 3.6844, 'circ_mv': 68961.13, 'total_mv': 121293.52, 'bps': 2.6544, 'roe': 0.5948, 'profit_dedt': 2263561.94, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '920367.BJ': {'pe_ttm': 158.6875, 'pb': 3.8287, 'circ_mv': 109432.66, 'total_mv': 184664.42, 'bps': 6.8064, 'roe': 0.7465, 'profit_dedt': 1949104.8, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '920371.BJ': {'pe_ttm': 27.8566, 'pb': 2.4356, 'circ_mv': 139678.49, 'total_mv': 147304.45, 'bps': 2.9438, 'roe': 1.2259, 'profit_dedt': 7127168.89, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '920374.BJ': {'pe_ttm': 95.9991, 'pb': 4.803, 'circ_mv': 50990.34, 'total_mv': 124967.09, 'bps': 3.0398, 'roe': 2.5778, 'profit_dedt': 6596823.82, 'industry': '通信设备', 'is_st': False, 'delist': ['', '']},
    '920378.BJ': {'pe_ttm': 50.4206, 'pb': 2.8976, 'circ_mv': 79337.54, 'total_mv': 112445.3, 'bps': 2.4951, 'roe': 2.1097, 'profit_dedt': 6704866.59, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920392.BJ': {'pe_ttm': 58.7988, 'pb': 3.008, 'circ_mv': 47644.75, 'total_mv': 101356.02, 'bps': 4.3946, 'roe': 2.0773, 'profit_dedt': 7029847.0, 'industry': '广告包装', 'is_st': False, 'delist': ['', '']},
    '920395.BJ': {'pe_ttm': 50.8242, 'pb': 4.589, 'circ_mv': 70198.8, 'total_mv': 101927.25, 'bps': 1.4557, 'roe': 4.8353, 'profit_dedt': 14620847.91, 'industry': 'IT设备', 'is_st': False, 'delist': ['', '']},
    '920396.BJ': {'pe_ttm': 31.4029, 'pb': 3.5519, 'circ_mv': 88247.52, 'total_mv': 128192.52, 'bps': 4.9974, 'roe': 3.6947, 'profit_dedt': 12394736.53, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '920403.BJ': {'pe_ttm': 25.747, 'pb': 3.8404, 'circ_mv': 125975.69, 'total_mv': 211817.95, 'bps': 5.0464, 'roe': 2.7119, 'profit_dedt': 9050415.62, 'industry': '种植业', 'is_st': False, 'delist': ['', '']},
    '920405.BJ': {'pe_ttm': 37.2664, 'pb': 2.7212, 'circ_mv': 181756.82, 'total_mv': 233364.18, 'bps': 6.1143, 'roe': 2.9955, 'profit_dedt': 24759466.9, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '920407.BJ': {'pe_ttm': 61.766, 'pb': 4.2585, 'circ_mv': 47945.7, 'total_mv': 117770.15, 'bps': 3.2389, 'roe': 4.3238, 'profit_dedt': 11124774.38, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '920415.BJ': {'pe_ttm': 43.7115, 'pb': 2.1917, 'circ_mv': 93456.65, 'total_mv': 122526.02, 'bps': 3.9787, 'roe': 1.1353, 'profit_dedt': 4694321.07, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '920418.BJ': {'pe_ttm': 23.8553, 'pb': 3.2276, 'circ_mv': 287004.03, 'total_mv': 297193.48, 'bps': 5.6667, 'roe': 6.1246, 'profit_dedt': 52437504.42, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920419.BJ': {'pe_ttm': 20.2255, 'pb': 1.7782, 'circ_mv': 103749.74, 'total_mv': 110847.77, 'bps': 6.0343, 'roe': 4.0029, 'profit_dedt': 23988328.22, 'industry': '饲料', 'is_st': False, 'delist': ['', '']},
    '920422.BJ': {'pe_ttm': 27.9154, 'pb': 1.8928, 'circ_mv': 61785.79, 'total_mv': 82554.37, 'bps': 4.9291, 'roe': 4.079, 'profit_dedt': 15982424.43, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '920425.BJ': {'pe_ttm': 27.1288, 'pb': 4.8591, 'circ_mv': 114897.48, 'total_mv': 139585.68, 'bps': 3.9933, 'roe': 13.578, 'profit_dedt': 37387508.53, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920429.BJ': {'pe_ttm': 27.0375, 'pb': 1.5566, 'circ_mv': 118494.41, 'total_mv': 129952.94, 'bps': 6.707, 'roe': 2.8209, 'profit_dedt': 22886427.65, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '920436.BJ': {'pe_ttm': 74.2663, 'pb': 2.5112, 'circ_mv': 94210.67, 'total_mv': 100317.23, 'bps': 4.2283, 'roe': 2.4742, 'profit_dedt': 3677935.3, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920445.BJ': {'pe_ttm': 62.2962, 'pb': 3.6751, 'circ_mv': 78629.92, 'total_mv': 107856.86, 'bps': 1.9808, 'roe': 2.6155, 'profit_dedt': 3847472.47, 'industry': '家居用品', 'is_st': False, 'delist': ['', '']},
    '920454.BJ': {'pe_ttm': 43.9285, 'pb': 3.6615, 'circ_mv': 55925.85, 'total_mv': 119393.6, 'bps': 3.1025, 'roe': 4.328, 'profit_dedt': 12317695.81, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920455.BJ': {'pe_ttm': 35.5429, 'pb': 3.3752, 'circ_mv': 63057.81, 'total_mv': 114987.6, 'bps': 1.9199, 'roe': 6.0095, 'profit_dedt': 20319134.11, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920476.BJ': {'pe_ttm': 41.9556, 'pb': 4.4669, 'circ_mv': 180581.69, 'total_mv': 201388.74, 'bps': 5.2929, 'roe': 2.5208, 'profit_dedt': 10691610.23, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '920491.BJ': {'pe_ttm': 40.6252, 'pb': 2.7788, 'circ_mv': 242069.96, 'total_mv': 294989.25, 'bps': 7.52, 'roe': 2.7218, 'profit_dedt': 26321110.93, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '920496.BJ': {'pe_ttm': 149.9807, 'pb': 1.8548, 'circ_mv': 91639.01, 'total_mv': 104643.4, 'bps': 3.4074, 'roe': 0.2973, 'profit_dedt': 337448.53, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920504.BJ': {'pe_ttm': 43.7988, 'pb': 3.8919, 'circ_mv': 31414.92, 'total_mv': 79646.24, 'bps': 4.7226, 'roe': 3.3603, 'profit_dedt': 5828413.03, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
    '920505.BJ': {'pe_ttm': 123.0203, 'pb': 4.7678, 'circ_mv': 89451.14, 'total_mv': 160927.1, 'bps': 5.21, 'roe': 2.1322, 'profit_dedt': 7008999.59, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '920509.BJ': {'pe_ttm': 50.4118, 'pb': 10.252, 'circ_mv': 190395.16, 'total_mv': 363413.01, 'bps': 2.2113, 'roe': 8.939, 'profit_dedt': 30933195.19, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '920519.BJ': {'pe_ttm': 22.7944, 'pb': 1.793, 'circ_mv': 85161.1, 'total_mv': 95773.95, 'bps': 5.9843, 'roe': 5.1497, 'profit_dedt': 26722756.36, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '920523.BJ': {'pe_ttm': 12.9061, 'pb': 2.1229, 'circ_mv': 122743.4, 'total_mv': 154484.59, 'bps': 7.1835, 'roe': 6.7434, 'profit_dedt': 48018006.84, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920526.BJ': {'pe_ttm': 109.0445, 'pb': 7.8387, 'circ_mv': 113899.28, 'total_mv': 179872.5, 'bps': 2.7747, 'roe': 3.843, 'profit_dedt': 8496247.48, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '920527.BJ': {'pe_ttm': 34.2261, 'pb': 2.2532, 'circ_mv': 68765.13, 'total_mv': 88683.07, 'bps': 6.5553, 'roe': 3.8912, 'profit_dedt': 14818781.92, 'industry': '纺织', 'is_st': False, 'delist': ['', '']},
    '920533.BJ': {'pe_ttm': 50.4406, 'pb': 5.541, 'circ_mv': 118701.57, 'total_mv': 212219.14, 'bps': 2.9291, 'roe': 5.5897, 'profit_dedt': 20302342.26, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920541.BJ': {'pe_ttm': 16.3577, 'pb': 2.7349, 'circ_mv': 85105.46, 'total_mv': 129181.5, 'bps': 3.4553, 'roe': 6.8401, 'profit_dedt': 29772363.5, 'industry': '运输设备', 'is_st': False, 'delist': ['', '']},
    '920547.BJ': {'pe_ttm': 22.4616, 'pb': 2.0433, 'circ_mv': 73112.85, 'total_mv': 150403.97, 'bps': 9.4747, 'roe': 5.6986, 'profit_dedt': 36670217.35, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '920553.BJ': {'pe_ttm': 75.5295, 'pb': 2.6803, 'circ_mv': 62191.64, 'total_mv': 95266.77, 'bps': 2.4736, 'roe': 2.0506, 'profit_dedt': 6864374.87, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920556.BJ': {'pe_ttm': 54.9415, 'pb': 1.9798, 'circ_mv': 74948.65, 'total_mv': 103452.86, 'bps': 3.2393, 'roe': 2.3642, 'profit_dedt': 11063641.09, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '920566.BJ': {'pe_ttm': 15.733, 'pb': 1.5832, 'circ_mv': 104145.26, 'total_mv': 133916.16, 'bps': 5.8552, 'roe': 4.3866, 'profit_dedt': 32774457.47, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '920570.BJ': {'pe_ttm': 68.6316, 'pb': 3.4964, 'circ_mv': 63216.98, 'total_mv': 124421.16, 'bps': 7.09, 'roe': 2.7662, 'profit_dedt': 9090673.64, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '920571.BJ': {'pe_ttm': 27.2835, 'pb': 3.535, 'circ_mv': 256408.96, 'total_mv': 480982.85, 'bps': 2.4498, 'roe': 9.1412, 'profit_dedt': 106132005.44, 'industry': '水运', 'is_st': False, 'delist': ['', '']},
    '920578.BJ': {'pe_ttm': 308.4943, 'pb': 4.7784, 'circ_mv': 61203.76, 'total_mv': 131849.96, 'bps': 3.6079, 'roe': 3.7203, 'profit_dedt': 6971759.78, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920580.BJ': {'pe_ttm': 36.5956, 'pb': 2.6935, 'circ_mv': 60184.62, 'total_mv': 94514.0, 'bps': 4.0802, 'roe': 4.3763, 'profit_dedt': 14843555.84, 'industry': '其他建材', 'is_st': False, 'delist': ['', '']},
    '920608.BJ': {'pe_ttm': 25.3863, 'pb': 1.6791, 'circ_mv': 64854.4, 'total_mv': 83712.8, 'bps': 8.0101, 'roe': 3.8572, 'profit_dedt': 14658285.1, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '920627.BJ': {'pe_ttm': 59.6703, 'pb': 2.8044, 'circ_mv': 67361.77, 'total_mv': 129585.4, 'bps': 4.8922, 'roe': 0.9112, 'profit_dedt': 4842736.18, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920634.BJ': {'pe_ttm': 58.3538, 'pb': 3.3823, 'circ_mv': 63146.96, 'total_mv': 98915.29, 'bps': 4.576, 'roe': 2.8028, 'profit_dedt': 7776522.68, 'industry': '铅锌', 'is_st': False, 'delist': ['', '']},
    '920639.BJ': {'pe_ttm': 120.7856, 'pb': 1.4688, 'circ_mv': 73152.24, 'total_mv': 124185.6, 'bps': 4.194, 'roe': 1.8223, 'profit_dedt': 13998030.45, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920641.BJ': {'pe_ttm': 57.6961, 'pb': 2.7865, 'circ_mv': 59095.08, 'total_mv': 97897.8, 'bps': 4.6798, 'roe': 3.2551, 'profit_dedt': 8797657.97, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '920651.BJ': {'pe_ttm': 16.2137, 'pb': 2.133, 'circ_mv': 75595.02, 'total_mv': 111203.0, 'bps': 8.5465, 'roe': 4.2052, 'profit_dedt': 20559432.73, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '920656.BJ': {'pe_ttm': 17.1905, 'pb': 2.0833, 'circ_mv': 120891.9, 'total_mv': 148100.0, 'bps': 7.1088, 'roe': 6.6492, 'profit_dedt': 44158628.82, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '920662.BJ': {'pe_ttm': 186.3283, 'pb': 3.5202, 'circ_mv': 48637.95, 'total_mv': 158812.18, 'bps': 5.153, 'roe': 0.6959, 'profit_dedt': 1197821.31, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '920663.BJ': {'pe_ttm': 16.9256, 'pb': 4.0082, 'circ_mv': 77917.05, 'total_mv': 162399.85, 'bps': 3.0338, 'roe': 10.4696, 'profit_dedt': 42689259.75, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920665.BJ': {'pe_ttm': 43.9771, 'pb': 1.4859, 'circ_mv': 53077.78, 'total_mv': 107501.73, 'bps': 5.5655, 'roe': 1.9477, 'profit_dedt': 12118047.26, 'industry': '橡胶', 'is_st': False, 'delist': ['', '']},
    '920675.BJ': {'pe_ttm': 33.9121, 'pb': 2.0588, 'circ_mv': 56892.87, 'total_mv': 123958.08, 'bps': 3.5, 'roe': 3.8891, 'profit_dedt': 23140813.93, 'industry': '矿物制品', 'is_st': False, 'delist': ['', '']},
    '920679.BJ': {'pe_ttm': 43.5656, 'pb': 1.7564, 'circ_mv': 57772.87, 'total_mv': 86958.45, 'bps': 8.8646, 'roe': 1.5833, 'profit_dedt': 6684302.43, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '920682.BJ': {'pe_ttm': 29.1881, 'pb': 1.9781, 'circ_mv': 129161.48, 'total_mv': 222723.07, 'bps': 2.8916, 'roe': 2.8453, 'profit_dedt': 30572942.07, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920685.BJ': {'pe_ttm': 30.5285, 'pb': 2.0741, 'circ_mv': 74233.69, 'total_mv': 116499.79, 'bps': 6.1375, 'roe': 2.6465, 'profit_dedt': 11567448.12, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '920689.BJ': {'pe_ttm': 26.1451, 'pb': 2.9539, 'circ_mv': 73737.12, 'total_mv': 142689.6, 'bps': 6.5811, 'roe': 5.7799, 'profit_dedt': 25901154.1, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920690.BJ': {'pe_ttm': 18.21, 'pb': 2.2335, 'circ_mv': 87404.93, 'total_mv': 134456.4, 'bps': 6.9847, 'roe': 5.3773, 'profit_dedt': 31158488.23, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920694.BJ': {'pe_ttm': 16.9659, 'pb': 2.2876, 'circ_mv': 101758.39, 'total_mv': 214228.95, 'bps': 7.0898, 'roe': 9.7612, 'profit_dedt': 85749040.95, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '920699.BJ': {'pe_ttm': 77.5132, 'pb': 11.1544, 'circ_mv': 175147.94, 'total_mv': 382805.39, 'bps': 7.5219, 'roe': 5.5599, 'profit_dedt': 17456039.71, 'industry': '家用电器', 'is_st': False, 'delist': ['', '']},
    '920701.BJ': {'pe_ttm': 38.9369, 'pb': 1.7944, 'circ_mv': 104716.57, 'total_mv': 143374.0, 'bps': 5.8237, 'roe': 0.163, 'profit_dedt': 2385577.17, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '920703.BJ': {'pe_ttm': 17.2938, 'pb': 1.2918, 'circ_mv': 74511.05, 'total_mv': 147257.35, 'bps': 7.5632, 'roe': 1.9512, 'profit_dedt': 20425357.24, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920706.BJ': {'pe_ttm': 58.4211, 'pb': 2.5509, 'circ_mv': 77085.41, 'total_mv': 152976.68, 'bps': 6.4898, 'roe': 2.4357, 'profit_dedt': 13580950.62, 'industry': '工程机械', 'is_st': False, 'delist': ['', '']},
    '920720.BJ': {'pe_ttm': 39.8526, 'pb': 3.8549, 'circ_mv': 57067.24, 'total_mv': 204231.22, 'bps': 2.0, 'roe': 3.8712, 'profit_dedt': 20415746.5, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '920726.BJ': {'pe_ttm': 38.3861, 'pb': 3.2355, 'circ_mv': 83422.87, 'total_mv': 151154.52, 'bps': 4.6114, 'roe': 6.7403, 'profit_dedt': 27635333.06, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '920729.BJ': {'pe_ttm': 65.021, 'pb': 2.7774, 'circ_mv': 51542.18, 'total_mv': 181744.5, 'bps': 2.3938, 'roe': 0.9646, 'profit_dedt': 184988.79, 'industry': '农业综合', 'is_st': False, 'delist': ['', '']},
    '920735.BJ': {'pe_ttm': 12.5384, 'pb': 2.4181, 'circ_mv': 296973.95, 'total_mv': 335781.83, 'bps': 10.7606, 'roe': 9.5462, 'profit_dedt': 124031452.67, 'industry': '化学制药', 'is_st': False, 'delist': ['', '']},
    '920751.BJ': {'pe_ttm': 28.6824, 'pb': 3.2424, 'circ_mv': 128089.51, 'total_mv': 133814.76, 'bps': 4.7557, 'roe': 5.8971, 'profit_dedt': 23458830.1, 'industry': '小金属', 'is_st': False, 'delist': ['', '']},
    '920753.BJ': {'pe_ttm': 36.2322, 'pb': 3.0868, 'circ_mv': 48639.25, 'total_mv': 121048.3, 'bps': 4.8173, 'roe': 3.2832, 'profit_dedt': 10675681.58, 'industry': '纺织', 'is_st': False, 'delist': ['', '']},
    '920768.BJ': {'pe_ttm': 17.7339, 'pb': 1.5221, 'circ_mv': 76353.95, 'total_mv': 110459.54, 'bps': 4.6712, 'roe': 5.2345, 'profit_dedt': 35951619.48, 'industry': '家用电器', 'is_st': False, 'delist': ['', '']},
    '920770.BJ': {'pe_ttm': 30.6511, 'pb': 2.1098, 'circ_mv': 81730.01, 'total_mv': 129871.91, 'bps': 4.7303, 'roe': 3.3097, 'profit_dedt': 17563951.25, 'industry': '电气设备', 'is_st': False, 'delist': ['', '']},
    '920779.BJ': {'pe_ttm': 41.3834, 'pb': 5.0008, 'circ_mv': 57257.21, 'total_mv': 228975.95, 'bps': 5.7181, 'roe': 4.6611, 'profit_dedt': 19857358.63, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '920786.BJ': {'pe_ttm': 21.423, 'pb': 1.9809, 'circ_mv': 101743.92, 'total_mv': 154072.06, 'bps': 3.7205, 'roe': 5.11, 'profit_dedt': 34991211.6, 'industry': '乳制品', 'is_st': False, 'delist': ['', '']},
    '920792.BJ': {'pe_ttm': 19.0044, 'pb': 1.4399, 'circ_mv': 94004.36, 'total_mv': 153124.5, 'bps': 6.4239, 'roe': 3.4973, 'profit_dedt': 35752005.32, 'industry': '其他建材', 'is_st': False, 'delist': ['', '']},
    '920802.BJ': {'pe_ttm': 32.6806, 'pb': 1.9716, 'circ_mv': 38354.48, 'total_mv': 86846.78, 'bps': 5.3714, 'roe': 2.3327, 'profit_dedt': 8693499.4, 'industry': '环境保护', 'is_st': False, 'delist': ['', '']},
    '920826.BJ': {'pe_ttm': 25.6427, 'pb': 3.0269, 'circ_mv': 52354.79, 'total_mv': 116487.61, 'bps': 2.74, 'roe': 4.748, 'profit_dedt': 16741989.48, 'industry': '食品', 'is_st': False, 'delist': ['', '']},
    '920833.BJ': {'pe_ttm': 112.8628, 'pb': 1.8669, 'circ_mv': 81790.94, 'total_mv': 108091.78, 'bps': 5.0566, 'roe': 0.2532, 'profit_dedt': 665816.98, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920834.BJ': {'pe_ttm': 20.4847, 'pb': 3.1703, 'circ_mv': 51493.08, 'total_mv': 98400.0, 'bps': 2.5865, 'roe': 7.2361, 'profit_dedt': 21798165.77, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '920837.BJ': {'pe_ttm': 17.2503, 'pb': 2.6129, 'circ_mv': 136103.85, 'total_mv': 151963.05, 'bps': 3.8335, 'roe': 8.7747, 'profit_dedt': 48993903.74, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920839.BJ': {'pe_ttm': 23.2377, 'pb': 4.2757, 'circ_mv': 153356.51, 'total_mv': 278029.85, 'bps': 5.5008, 'roe': 9.2949, 'profit_dedt': 59196582.33, 'industry': '工程机械', 'is_st': False, 'delist': ['', '']},
    '920855.BJ': {'pe_ttm': 43.6349, 'pb': 1.6726, 'circ_mv': 86544.65, 'total_mv': 96480.73, 'bps': 7.7186, 'roe': 2.062, 'profit_dedt': 12085367.81, 'industry': '机床制造', 'is_st': False, 'delist': ['', '']},
    '920856.BJ': {'pe_ttm': 210.9405, 'pb': 2.264, 'circ_mv': 58206.86, 'total_mv': 104353.91, 'bps': 4.8454, 'roe': 0.6071, 'profit_dedt': 2557979.04, 'industry': '专用机械', 'is_st': False, 'delist': ['', '']},
    '920866.BJ': {'pe_ttm': 37.7467, 'pb': 1.4498, 'circ_mv': 69679.42, 'total_mv': 111186.2, 'bps': 4.2556, 'roe': 1.7869, 'profit_dedt': 11245304.54, 'industry': '农药化肥', 'is_st': False, 'delist': ['', '']},
    '920870.BJ': {'pe_ttm': 78.201, 'pb': 3.3949, 'circ_mv': 75177.3, 'total_mv': 152626.0, 'bps': 3.355, 'roe': 2.3723, 'profit_dedt': 8662181.02, 'industry': '机床制造', 'is_st': False, 'delist': ['', '']},
    '920871.BJ': {'pe_ttm': 69.1743, 'pb': 3.6201, 'circ_mv': 51973.91, 'total_mv': 93767.25, 'bps': 3.0524, 'roe': 4.31, 'profit_dedt': 11425192.26, 'industry': '塑料', 'is_st': False, 'delist': ['', '']},
    '920876.BJ': {'pe_ttm': 192.4316, 'pb': 4.0306, 'circ_mv': 62640.21, 'total_mv': 102432.33, 'bps': 3.9597, 'roe': 0.2537, 'profit_dedt': 239060.05, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '920879.BJ': {'pe_ttm': 29.9537, 'pb': 3.8685, 'circ_mv': 184820.9, 'total_mv': 247872.12, 'bps': 3.1899, 'roe': 5.0971, 'profit_dedt': 32362597.61, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '920885.BJ': {'pe_ttm': 86.6553, 'pb': 4.8413, 'circ_mv': 103265.54, 'total_mv': 200542.56, 'bps': 2.425, 'roe': 2.6786, 'profit_dedt': 9496444.86, 'industry': '电器仪表', 'is_st': False, 'delist': ['', '']},
    '920892.BJ': {'pe_ttm': 17.4544, 'pb': 4.9968, 'circ_mv': 170915.93, 'total_mv': 193008.08, 'bps': 2.27, 'roe': 11.8888, 'profit_dedt': 48222902.63, 'industry': '建筑工程', 'is_st': False, 'delist': ['', '']},
    '920895.BJ': {'pe_ttm': 1047.1028, 'pb': 6.0211, 'circ_mv': 59427.91, 'total_mv': 116891.63, 'bps': 3.4346, 'roe': 0.1563, 'profit_dedt': 223507.82, 'industry': '农用机械', 'is_st': False, 'delist': ['', '']},
    '920896.BJ': {'pe_ttm': 20.0581, 'pb': 2.7427, 'circ_mv': 53237.77, 'total_mv': 121367.86, 'bps': 4.3716, 'roe': 6.2807, 'profit_dedt': 27220849.5, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920914.BJ': {'pe_ttm': 31.4463, 'pb': 1.7015, 'circ_mv': 169028.41, 'total_mv': 169162.05, 'bps': 9.9737, 'roe': 3.1875, 'profit_dedt': 28187889.0, 'industry': '小金属', 'is_st': False, 'delist': ['', '']},
    '920924.BJ': {'pe_ttm': 139.4882, 'pb': 4.6535, 'circ_mv': 92174.72, 'total_mv': 151288.07, 'bps': 3.03, 'roe': 2.3596, 'profit_dedt': 8650397.31, 'industry': '通信设备', 'is_st': False, 'delist': ['', '']},
    '920926.BJ': {'pe_ttm': 66.1566, 'pb': 3.5086, 'circ_mv': 81144.57, 'total_mv': 89422.08, 'bps': 2.8844, 'roe': 0.3727, 'profit_dedt': 690312.62, 'industry': '家用电器', 'is_st': False, 'delist': ['', '']},
    '920931.BJ': {'pe_ttm': 430.0523, 'pb': 2.3645, 'circ_mv': 28344.16, 'total_mv': 92521.05, 'bps': 4.1658, 'roe': 0.9035, 'profit_dedt': 2774140.01, 'industry': '化工机械', 'is_st': False, 'delist': ['', '']},
    '920942.BJ': {'pe_ttm': 83.0632, 'pb': 2.057, 'circ_mv': 69245.9, 'total_mv': 102267.21, 'bps': 8.1285, 'roe': 1.3824, 'profit_dedt': 6068206.09, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '920943.BJ': {'pe_ttm': 33.0915, 'pb': 2.1872, 'circ_mv': 71598.43, 'total_mv': 140302.54, 'bps': 6.3185, 'roe': 0.6015, 'profit_dedt': 3313843.8, 'industry': '机械基件', 'is_st': False, 'delist': ['', '']},
    '920950.BJ': {'pe_ttm': 24.8878, 'pb': 3.0699, 'circ_mv': 46900.49, 'total_mv': 96269.16, 'bps': 4.2771, 'roe': 6.7508, 'profit_dedt': 20900538.22, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '920953.BJ': {'pe_ttm': 46.1364, 'pb': 4.6593, 'circ_mv': 133964.43, 'total_mv': 286595.47, 'bps': 4.8012, 'roe': 2.1464, 'profit_dedt': 11473283.82, 'industry': '软件服务', 'is_st': False, 'delist': ['', '']},
    '920957.BJ': {'pe_ttm': 57.7882, 'pb': 2.6177, 'circ_mv': 40953.84, 'total_mv': 102901.98, 'bps': 3.6636, 'roe': 3.0865, 'profit_dedt': 11750861.87, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '920971.BJ': {'pe_ttm': 56.2634, 'pb': 5.9377, 'circ_mv': 224627.04, 'total_mv': 272500.13, 'bps': 4.4142, 'roe': 2.6818, 'profit_dedt': 11954597.82, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '920974.BJ': {'pe_ttm': 29.9296, 'pb': 1.5392, 'circ_mv': 77668.54, 'total_mv': 101392.2, 'bps': 4.0214, 'roe': 2.8959, 'profit_dedt': 18992674.68, 'industry': '化工原料', 'is_st': False, 'delist': ['', '']},
    '920976.BJ': {'pe_ttm': 20.8948, 'pb': 6.1182, 'circ_mv': 101430.96, 'total_mv': 163121.87, 'bps': 3.7576, 'roe': 13.8021, 'profit_dedt': 36956378.39, 'industry': '通信设备', 'is_st': False, 'delist': ['', '']},
    '920978.BJ': {'pe_ttm': 19.2868, 'pb': 3.7366, 'circ_mv': 172800.63, 'total_mv': 308208.74, 'bps': 4.5817, 'roe': 8.9406, 'profit_dedt': 70945501.75, 'industry': '汽车配件', 'is_st': False, 'delist': ['', '']},
    '920981.BJ': {'pe_ttm': 140.8946, 'pb': 4.1602, 'circ_mv': 108003.25, 'total_mv': 214722.14, 'bps': 6.7497, 'roe': 1.6121, 'profit_dedt': 6771469.88, 'industry': '元器件', 'is_st': False, 'delist': ['', '']},
    '920992.BJ': {'pe_ttm': 70.083, 'pb': 1.9994, 'circ_mv': 63526.4, 'total_mv': 125363.29, 'bps': 6.4819, 'roe': 1.5697, 'profit_dedt': 6487950.05, 'industry': '医疗保健', 'is_st': False, 'delist': ['', '']},
}
# --- V2SNAPSHOT-END ---
# --- V2LEDGER-BEGIN ---
PERIOD_LEDGER = {
    'account_id': '67014907',
    'asof_date': '2026-10-16',
    'cash_hint': 69806.0,
    'nav_peak': 1.01215,
    'last_rebal_month': 10,
    'suspended_sells': [],
    'holdings': {
        '000153.SZ': {'vol': 200, 'entry_date': '2026-10-13'},
        '002054.SZ': {'vol': 100, 'entry_date': '2026-10-13'},
        '002144.SZ': {'vol': 100, 'entry_date': '2026-10-13'},
        '002391.SZ': {'vol': 200, 'entry_date': '2026-10-13'},
        '002398.SZ': {'vol': 200, 'entry_date': '2026-10-13'},
        '002442.SZ': {'vol': 200, 'entry_date': '2026-10-13'},
        '002566.SZ': {'vol': 100, 'entry_date': '2026-10-13'},
        '002615.SZ': {'vol': 200, 'entry_date': '2026-10-13'},
        '002666.SZ': {'vol': 300, 'entry_date': '2026-10-13'},
        '002753.SZ': {'vol': 100, 'entry_date': '2026-10-13'},
        '002852.SZ': {'vol': 100, 'entry_date': '2026-10-13'},
        '002863.SZ': {'vol': 200, 'entry_date': '2026-10-13'},
        '002996.SZ': {'vol': 200, 'entry_date': '2026-10-13'},
        '300230.SZ': {'vol': 200, 'entry_date': '2026-10-13'},
        '300335.SZ': {'vol': 200, 'entry_date': '2026-10-13'},
        '300384.SZ': {'vol': 100, 'entry_date': '2026-10-13'},
        '300407.SZ': {'vol': 100, 'entry_date': '2026-10-13'},
        '300439.SZ': {'vol': 100, 'entry_date': '2026-10-13'},
        '300636.SZ': {'vol': 100, 'entry_date': '2026-10-13'},
        '300650.SZ': {'vol': 100, 'entry_date': '2026-10-13'},
        '300771.SZ': {'vol': 100, 'entry_date': '2026-10-13'},
        '301032.SZ': {'vol': 100, 'entry_date': '2026-10-13'},
        '301332.SZ': {'vol': 100, 'entry_date': '2026-10-13'},
        '600051.SH': {'vol': 200, 'entry_date': '2026-10-13'},
        '600419.SH': {'vol': 100, 'entry_date': '2026-10-13'},
        '603041.SH': {'vol': 100, 'entry_date': '2026-10-13'},
        '603585.SH': {'vol': 100, 'entry_date': '2026-10-13'},
        '603617.SH': {'vol': 100, 'entry_date': '2026-10-13'},
        '603700.SH': {'vol': 100, 'entry_date': '2026-10-13'},
        '603797.SH': {'vol': 200, 'entry_date': '2026-10-13'},
        '605166.SH': {'vol': 100, 'entry_date': '2026-10-13'},
    },
}
# --- V2LEDGER-END ---

# ============ 全局状态 ============
_cash = CAPITAL_INIT       # 专属资金池现金（与账户其他策略资金完全隔离）
_holdings = {}
_entry_prices = {}
_entry_dates = {}
_nav_peak = 1.0
_last_rebal_month = -1
_last_rebal_log_min = -1   # 换仓日志节流（同一分钟内只打一次，防模拟端刷屏）
_inited = False
_pending_orders = {}  # 待成交订单跟踪 {code: {"type": "buy/sell", "order_id": None, "time": datetime, "retries": 0}}
_today_orders = {}    # 当日下单记录（收盘对账校准用） {code: {"dir": "buy/sell", "amount": 下单量, "price": 估算价, "entry_price": 卖出前成本价, "entry_date": 卖出前买入日}}
_suspended_sells = []  # 跌停暂缓卖出队列
_last_pending_min = -1  # 挂单巡检节流（同一分钟只查一次，防模拟端刷屏）
_last_reconcile_date = ""  # 收盘对账每日闸门（同一天只跑一次）
_ACCT_QUERY_FAIL = object()  # 账户持仓查询失败哨兵（对账兜底：无法判定时保守处理）
_passive = False           # 被动实例标志（2b）：预检失败/期初账本缺失/同进程双实例时不交易不落盘，仅 print 心跳
_passive_reason = ""       # 被动原因描述（预检失败/期初账本未填/双实例），防误导性诊断
_g_running = False         # 同进程单实例标志（2b，文件锁替代）：init 置位 / exit 复位，防同进程双实例重复下单
_last_heart_at = 0.0       # 上次 [HEART] 心跳 print 时间（epoch 秒，HEARTBEAT_INTERVAL_SEC 节流）

def _get_market_time(C):
    """QMT行情时间（生产验证方案）: get_tick_timetag -> get_bar_timetag -> datetime.now()
    注意: QMT模拟端 __PyContext 无 get_current_time, 必须用 timetag 方案。"""
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

def _log(msg, C=None):
    """日志函数，优先用QMT时间，fallback到datetime.now()"""
    if C is not None:
        try:
            ts = _get_market_time(C).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "%s %s" % (ts, msg)
    # 2b 打印化：仅输出到 QMT 公式输出窗口（FormulaOutput 日志），不再写盘
    try:
        print(line)
    except Exception:
        pass

def _heart(C):
    """心跳（2b 打印化，替代遥测 heart json 落盘）：[HEART] print，
    HEARTBEAT_INTERVAL_SEC 节流，含 持仓数/现金/被动标志/build。"""
    global _last_heart_at
    now_ts = _time.time()
    if (now_ts - _last_heart_at) < HEARTBEAT_INTERVAL_SEC:
        return
    _last_heart_at = now_ts
    _log("[HEART] 持仓=%d 现金=%.0f passive=%s build=%s"
         % (len(_holdings), _cash, _passive, BUILD_TAG), C)

def _load_pool(C):
    """V2 自闭环 (2a): 股票池改读内嵌 SNAPSHOT 键（原 QMT 板块池 fallback + selected.txt CSV 已废止）
    SNAPSHOT 即预筛选候选宇宙（E1 回测口径）；为空时返回空池并警示，
    评分管线自然空转（2b 预检会拦住空快照启动）。"""
    if not SNAPSHOT:
        print("[SNAPSHOT-EMPTY] _load_pool: SNAPSHOT 未内嵌/为空 -> 返回空池")
        return []
    return list(SNAPSHOT.keys())

def _load_financial():
    """V2 自闭环 (2a): 财务/估值/质量字段改读内嵌 SNAPSHOT（原 financial_*.csv 已废止）
    返回同构: {code: {pe_ttm, pb, circ_mv, industry, total_mv, bps, roe, profit_dedt}}
    数值字段 float 强转（与原 CSV 解析同行为，坏值跳过）；total_mv 为快照可选字段，
    缺失时 _delist_hit_qmt 市值红线自然跳过（total_mv<=0 分支）。"""
    result = {}
    if not SNAPSHOT:
        print("[SNAPSHOT-EMPTY] _load_financial: SNAPSHOT 未内嵌/为空 -> 返回空表")
        return result
    num_fields = ["pe_ttm", "pb", "circ_mv", "total_mv", "bps", "roe", "profit_dedt"]
    for code, rec in SNAPSHOT.items():
        if not rec:
            continue
        d = {}
        for name in num_fields:
            if rec.get(name) is None:
                continue
            try:
                d[name] = float(rec[name])
            except Exception:
                pass
        ind = rec.get("industry")
        if ind:
            d["industry"] = ind
        if d:
            result[code] = d
    return result

def _load_bp_history():
    """V2 自闭环 (2a): BP 月度历史分位改读内嵌 SNAPSHOT（原 bp_hist_pct.csv 已废止）
    快照字段约定: bp_hist = [(date 'YYYY-MM-DD', hp_pct float), ...] 升序
    返回同构: {code: [(date, hp_pct), ...]}"""
    result = {}
    if not SNAPSHOT:
        print("[SNAPSHOT-EMPTY] _load_bp_history: SNAPSHOT 未内嵌/为空 -> 返回空表")
        return result
    for code, rec in SNAPSHOT.items():
        if not rec:
            continue
        hist = rec.get("bp_hist")
        if not hist:
            continue
        lst = []
        for item in hist:
            try:
                lst.append((str(item[0]), float(item[1])))
            except Exception:
                pass
        if lst:
            result[code] = lst
    return result

def _load_industry_map():
    """V2 自闭环 (2a): 行业映射改读内嵌 SNAPSHOT（原 industry_map.csv 已废止）
    返回同构: {code: industry}"""
    result = {}
    if not SNAPSHOT:
        print("[SNAPSHOT-EMPTY] _load_industry_map: SNAPSHOT 未内嵌/为空 -> 返回空表")
        return result
    for code, rec in SNAPSHOT.items():
        if not rec:
            continue
        ind = rec.get("industry")
        if ind:
            result[code] = ind
    return result

def _load_delist_info():
    """V2 自闭环 (2a): 退市信息改读内嵌 SNAPSHOT（原 delist_info.csv 已废止）
    快照字段约定: delist = [list_status, delist_date 'YYYY-MM-DD']
    返回同构: code -> (list_status, delist_date)"""
    result = {}
    if not SNAPSHOT:
        print("[SNAPSHOT-EMPTY] _load_delist_info: SNAPSHOT 未内嵌/为空 -> 返回空表")
        return result
    for code, rec in SNAPSHOT.items():
        if not rec:
            continue
        d = rec.get("delist")
        if not d:
            continue
        try:
            result[code] = (str(d[0]), str(d[1]))
        except Exception:
            pass
    return result

def _load_st_list():
    """V2 自闭环 (2a): ST 兜底集合改读内嵌 SNAPSHOT（原 st_list.csv 已废止）
    快照可选字段 is_st 真值入集合；ST 主判已改 C.get_stock_name 实时含 "ST"（见 _score_stocks），
    本集合仅作实时名称不可用/漏判时的兜底。"""
    result = set()
    if not SNAPSHOT:
        print("[SNAPSHOT-EMPTY] _load_st_list: SNAPSHOT 未内嵌/为空 -> 返回空集合")
        return result
    for code, rec in SNAPSHOT.items():
        if rec and rec.get("is_st"):
            result.add(code)
    return result

def _delist_hit_qmt(code, fin_data, delist_info, today_str):
    """v2.3 退市排雷判断: True = 剔除 (市值红线缓冲区 + 退市临近)"""
    try:
        # 退市临近: 距退市日 <= DELIST_NEAR_DAYS 天
        info = delist_info.get(code)
        if info:
            list_status, delist_date = info[0], info[1]
            if list_status == "D":
                return True  # 已退市
            if delist_date:
                try:
                    t0 = datetime.strptime(today_str, "%Y-%m-%d")
                    t1 = datetime.strptime(delist_date, "%Y-%m-%d")
                    if (t1 - t0).days <= DELIST_NEAR_DAYS:
                        return True
                except Exception:
                    pass
        # 市值红线 (北交所不适用)
        if code.endswith(".BJ"):
            return False
        total_mv = fin_data.get(code, {}).get("total_mv", 0) or 0
        if total_mv <= 0:
            return False
        thr = DELIST_MV_GEMSTAR if (code.startswith("30") or code.startswith("688")) else DELIST_MV_MAIN
        return total_mv < thr
    except Exception:
        return False

def _limit_pct(code):
    """涨跌停幅度"""
    if code.startswith("688") or code.startswith("30"):
        return 0.20
    if code.startswith("8") or code.startswith("4") or code.startswith("92"):
        return 0.30
    return 0.10

def _is_limit_up(C, code):
    """检测是否涨停"""
    try:
        data = C.get_market_data_ex(["close", "pre_close"], [code], period="1d")
        if data and code in data and len(data[code]) > 0:
            row = data[code].iloc[-1]
            close = float(row.get("close", 0))
            pre_close = float(row.get("pre_close", 0))
            if pre_close > 0:
                pct = (close - pre_close) / pre_close
                limit = _limit_pct(code)
                if pct >= limit - 0.001:  # 允许0.1%误差
                    return True
    except Exception:
        pass
    return False

def _is_limit_down(C, code):
    """检测是否跌停"""
    try:
        data = C.get_market_data_ex(["close", "pre_close"], [code], period="1d")
        if data and code in data and len(data[code]) > 0:
            row = data[code].iloc[-1]
            close = float(row.get("close", 0))
            pre_close = float(row.get("pre_close", 0))
            if pre_close > 0:
                pct = (close - pre_close) / pre_close
                limit = _limit_pct(code)
                if pct <= -(limit - 0.001):  # 允许0.1%误差
                    return True
    except Exception:
        pass
    return False

def _is_suspended(C, code):
    """检测是否停牌
    集合竞价早段(09:15-09:25)正常股 volume 可能为 0，不能仅凭 volume 判停牌。
    综合判据：volume==0 且当日无有效开盘价(open<=0 或无 open) → 停牌。
    连续竞价中(本策略下单窗口 09:30+)，volume 持续为0且无开盘价基本确证停牌/无成交。"""
    try:
        data = C.get_market_data_ex(["open", "volume"], [code], period="1d")
        if data and code in data and len(data[code]) > 0:
            row = data[code].iloc[-1]
            vol = float(row.get("volume", 0) or 0)
            opn = float(row.get("open", 0) or 0)
            if vol == 0 and opn <= 0:
                return True
    except Exception:
        pass
    return False

def _query_orders():
    """反查当日全部委托（QMT全局函数，非C方法；不可用时返回None）"""
    try:
        return get_trade_detail_data(ACCOUNT_ID, "STOCK", "order") or []
    except Exception:
        return None

def _norm_code(code):
    """规范化证券代码为6位（兼容 '600000' 与 '600000.SH' 两种格式）"""
    return str(code or "").split(".")[0]

def _get_acct_position(code):
    """查询账户真实持仓量（position 接口，兜底防误撤销）
    返回三态：
      - POS 对象  → 账户有该 code 持仓（可读 m_nVolume）
      - None      → 账户确无该 code 持仓（接口正常，查询成功）
      - _ACCT_QUERY_FAIL → 接口异常/查询失败（无法判定，调用方须保守处理）
    仅用于对账兜底：deal 反查可能因模拟端/remark 时序漏记录，账户持仓是最终真相。
    注意：共享账户 position 含其他策略持仓，本函数只用于"该代码账户是否有货"的
    存在性判断与仓位下限校准，不覆盖虚拟账本的全部口径。"""
    try:
        c6 = _norm_code(code)
        for p in (get_trade_detail_data(ACCOUNT_ID, "STOCK", "position") or []):
            inst = getattr(p, "m_strInstrumentID", "") or getattr(p, "m_strSecurityCode", "") or ""
            if _norm_code(inst) == c6:
                return p
        return None
    except Exception:
        return _ACCT_QUERY_FAIL

def _find_order(orders, code, direction):
    """在委托列表中找 code+方向 的订单（属性风格访问，兼容模拟端）
    direction: 'buy'/'sell'；返回 order 对象 或 None
    共享账户防串撤：多策略可能对同 code 同方向下单。
    红线：remark 只作候选优先级，不硬过滤；唯一候选即使 remark 空也返回。
    本实现: 候选集内优先返回 remark 含 'V2' 的订单(本策略)；无 V2 且唯一候选时返回之；
    多候选且均非 V2(含 remark 空) 时返回 None(歧义, 宁可跳过不撤他人单)。"""
    if not orders:
        return None
    c6 = _norm_code(code)
    cand = []
    for o in reversed(orders):
        inst = getattr(o, "m_strInstrumentID", "") or ""
        if _norm_code(inst) != c6:
            continue
        op = getattr(o, "m_strOptName", "") or ""
        if direction == "buy" and "买" not in op and "buy" not in op.lower():
            continue
        if direction == "sell" and "卖" not in op and "sell" not in op.lower():
            continue
        cand.append(o)
    if not cand:
        return None
    for o in cand:
        remark = str(getattr(o, "m_strRemark", "") or "")
        if remark.find("V2") >= 0:
            return o
    if len(cand) == 1:
        return cand[0]
    return None

def _extract_order_id(order):
    """从QMT订单对象提取委托标识（字段名随QMT版本变化，多字段兜底）。
    模拟端无 m_nOrderID（DIAG 实锤），必须回退 m_strOrderSysID/m_strOrderRef 等，否则恒取不到；
    取不到返回 ""（调用方据此判定"无可撤标识"，不撤也不重下）。"""
    if order is None:
        return ""
    for attr in ("m_nOrderID", "m_strOrderID", "m_strOrderRef",
                 "m_strOrderSysID", "m_strSysid", "m_nOrderRef", "m_strUserOrderId"):
        try:
            v = getattr(order, attr, "")
            if v:
                return str(v)
        except Exception:
            continue
    return ""

def _extract_sysid(order):
    """提取柜台合同号（撤单官方推荐 orderId=m_strOrderSysID）。取不到返回 ""。"""
    if order is None:
        return ""
    for attr in ("m_strOrderSysID", "m_strSysid"):
        try:
            v = getattr(order, attr, "")
            if v:
                return str(v)
        except Exception:
            continue
    return ""

def _refresh_last_price(C, code, fallback=0.0):
    """重试前刷新最新价：避免用陈旧触发价重挂。取不到行情退回 fallback（fail-safe 不阻断）。"""
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

def _get_pre_close(C, code):
    """取昨收价（涨跌停 clamp 用）。取不到返回 0。"""
    try:
        data = C.get_market_data_ex(["pre_close"], [code], period="1d")
        if data and code in data and len(data[code]) > 0:
            return float(data[code].iloc[-1].get("pre_close", 0) or 0)
    except Exception:
        pass
    return 0.0

def _aggressive_price(C, code, side, ref_price):
    """激进限价：买入上浮 / 卖出下探 AGGRESSIVE_BUFFER 促成交，
    并按当日涨跌停价 clamp 防废单。ref_price<=0 返回 0（调用方回退最新价单）。"""
    try:
        ref = float(ref_price or 0)
        if ref <= 0:
            return 0.0
        limit = _limit_pct(code)
        pre_close = _get_pre_close(C, code)
        if side == "buy":
            p = ref * (1.0 + AGGRESSIVE_BUFFER)
            hi = (pre_close * (1.0 + limit - 0.001)) if pre_close > 0 else (ref * (1.0 + limit - 0.001))
            return round(min(p, hi), 2)
        p = ref * (1.0 - AGGRESSIVE_BUFFER)
        lo = (pre_close * (1.0 - limit + 0.001)) if pre_close > 0 else (ref * (1.0 - limit + 0.001))
        return round(max(p, lo), 2)
    except Exception:
        return 0.0

def _place_order(C, side, code, amount, ref_price):
    """统一下单入口（2026-10-13 修复）：prType=11 指定价 + 激进限价促成交；
    取价失败回退 prType=5 最新价单。11参数格式，末尾必须传 C。
    side: 'buy'/'sell'；remark 与 _find_order 的 V2 优先级一致。返回 (prType, 限价)。"""
    op_type = 23 if side == "buy" else 24
    remark = "V2买入" if side == "buy" else "V2卖出"
    price = _aggressive_price(C, code, side, ref_price)
    if price and price > 0:
        passorder(op_type, 1101, ACCOUNT_ID, code, PRTYPE_PRICE, price, amount, remark, 2, "", C)
        return PRTYPE_PRICE, price
    passorder(op_type, 1101, ACCOUNT_ID, code, PRTYPE_LATEST, -1, amount, remark, 2, "", C)
    return PRTYPE_LATEST, 0.0

def _confirm_cancelled(C, code, direction):
    """撤单确认：轮询委托列表，直到原单消失或其状态为死透终态（53/54/57）才返回 True。
    返回 False = 未确认死透（调用方延后复查，绝不乐观重下，防重复下单）。"""
    for _ in range(CANCEL_CONFIRM_RETRIES):
        try:
            _time.sleep(CANCEL_CONFIRM_INTERVAL)
        except Exception:
            pass
        orders = _query_orders()
        if orders is None:
            return False
        matched = _find_order(orders, code, direction)
        if matched is None:
            return True
        try:
            st = int(getattr(matched, "m_nOrderStatus", 0) or 0)
        except Exception:
            st = 0
        if st in CONFIRM_DEAD_STATUS:
            return True
    return False

def _cancel_order_by(C, code, order_id, sysid=""):
    """按订单号撤单（官方 cancel() 入口，2026-10-13 修复）。
    passorder opType 无撤单值（24=卖出），官方唯一撤单入口：
      can_cancel_order(oid, acct, 'stock') 预检 → cancel(oid, acct, 'stock', C)。
    oid 优先 sysid（m_strOrderSysID 柜台合同号）。cancel() 返回 True 仅=指令送达，
    调用方须轮询确认终态（_confirm_cancelled），不得乐观认为已撤。"""
    oid = sysid or order_id
    if not oid:
        return False
    try:
        if not can_cancel_order(oid, ACCOUNT_ID, "stock"):
            _log("[cancel] %s oid=%s 当前不可撤（预检未通过）" % (code, oid), C)
            return False
    except Exception:
        pass
    try:
        r = cancel(oid, ACCOUNT_ID, "stock", C)
        _log("[cancel] %s oid=%s 返回:%s" % (code, oid, r), C)
        return True
    except Exception as e:
        _log("[cancel error] %s oid=%s: %s" % (code, oid, str(e)), C)
        return False

def _cancel_pending_orders(C):
    """撤销所有待成交订单（换仓前清理；模拟端同样可用，全局函数反查）"""
    global _pending_orders
    orders = _query_orders()
    for code, info in list(_pending_orders.items()):
        if orders:
            order = _find_order(orders, code, info["type"])
            order_id = _extract_order_id(order)
            if order_id:
                _cancel_order_by(C, code, order_id, _extract_sysid(order))
        # 无论是否反查到，先清跟踪（真实成交由收盘对账校准）
    _pending_orders = {}

def _rollback_pending(C, code, info):
    """重试耗尽/跨日残留：回滚估算记账（真实成交交给收盘对账校准）
    2026-08-14 P1 修复: ①回滚前先反查并撤销 QMT 端活单，防止次日意外成交；
    ②卖出回滚后若仍跌停/停牌，进暂缓队列（解封后自动补卖），不留管理空窗。
    按 original_amount 全额反冲估算，并标记 _today_orders rolled_back 防止对账二次反冲"""
    global _cash, _holdings, _entry_prices, _entry_dates, _today_orders, _suspended_sells
    try:
        # 1) 先撤 QMT 端活单（防次日意外成交）。
        #    反查失败/查不到 = 可能已成交或已撤，不做动作，留给收盘对账按真实成交校准。
        orders = _query_orders()
        if orders:
            order = _find_order(orders, code, info["type"])
            order_id = _extract_order_id(order)
            if order_id:
                _cancel_order_by(C, code, order_id, _extract_sysid(order))
        orig = info.get("original_amount", info.get("amount", 0))
        if info["type"] == "buy":
            _holdings.pop(code, None)
            _entry_prices.pop(code, None)
            _entry_dates.pop(code, None)
            _cash += orig * info["price"]
            _log("[pending] %s 买入放弃(重试%d次)，回滚估算 现金=%.0f"
                 % (code, info.get("retries", 0), _cash), C)
        else:
            # 卖出回滚：恢复未卖出部分持仓（已成交部分留给对账按真实成交处理）
            remain = info.get("amount", 0)
            if remain > 0:
                _holdings[code] = remain
                _entry_prices[code] = info.get("entry_price", 0) or info["price"]
                _entry_dates[code] = info.get("entry_date", "") or _get_market_time(C).strftime("%Y-%m-%d")
                # 2) 恢复后仍跌停/停牌卖不出 -> 进暂缓队列，解封后自动补卖
                try:
                    if _is_limit_down(C, code) or _is_suspended(C, code):
                        if code not in _suspended_sells:
                            _suspended_sells.append(code)
                            _log("[pending] %s 卖出放弃但跌停/停牌，进暂缓队列" % code, C)
                except Exception:
                    pass
            _cash -= orig * info["price"]
            _log("[pending] %s 卖出放弃(重试%d次)，恢复持仓 %d股 现金=%.0f"
                 % (code, info.get("retries", 0), remain, _cash), C)
        # 标记对账跳过估算反冲（回滚已全额反冲；若有真实成交，对账仍按 deal 校准）
        if code in _today_orders:
            _today_orders[code]["rolled_back"] = True
    except Exception as e:
        _log("[rollback error] %s: %s" % (code, str(e)), C)

def _retry_pending(C, code, info, order=None):
    """撤单重下（2026-10-13 修复）：撤旧单 → 确认死透 → 刷新现价重挂（prType=11 激进价），
    重置超时与冷却。order: 已反查到的旧单对象（None 表示反查不可用）。
    关键约束（防重复下单）：
      ① 取不到撤单标识时只递增 retries + 重置 time，绝不重下；
      ② 撤单后必须确认死透（_confirm_cancelled）才重报，未死透延后 60s 复查，绝不乐观重下；
      ③ 重下前刷新最新价，避免用陈旧价重挂。"""
    global _pending_orders
    try:
        order_id = _extract_order_id(order)
        sysid = _extract_sysid(order)
        if not order_id and not sysid:
            # 无撤单标识：不撤不重下（原单可能已成交，重下会重复买卖）。
            # 必须递增 retries + 重置 time 让计数收敛到上限后由调用方回滚，
            # 否则永久停在本分支日内空转（本修复针对的核心缺陷）。
            info["retries"] = info.get("retries", 0) + 1
            info["time"] = _get_market_time(C)
            _pending_orders[code] = info
            _log("[pending] %s 未反查到可撤订单，retry=%d（不重下，等重试耗尽回滚）"
                 % (code, info["retries"]), C)
            return
        # 1) 撤旧单（官方 cancel()）
        _cancel_order_by(C, code, order_id, sysid)
        # 2) 撤单确认：确认死透才重报；未死透延后 60s 复查，绝不乐观重下
        if not _confirm_cancelled(C, code, info["type"]):
            info["time"] = _get_market_time(C) - timedelta(seconds=PENDING_TIMEOUT_MIN * 60 - 60)
            _pending_orders[code] = info
            _log("[pending] %s 撤单未确认死透，延后复查（不重下）" % code, C)
            return
        # 3) 刷新现价后重挂（激进限价）
        ref = _refresh_last_price(C, code, info.get("price", 0) or 0)
        _place_order(C, info["type"], code, info["amount"], ref)
        # 4) 更新跟踪：retries+1，重置 time（超时重计）
        info["retries"] = info.get("retries", 0) + 1
        info["time"] = _get_market_time(C)
        _pending_orders[code] = info
        _log("[pending] %s %s重下 数量=%d retry=%d"
             % (code, info["type"], info["amount"], info["retries"]), C)
    except Exception as e:
        _log("[retry error] %s: %s" % (code, str(e)), C)

def _check_pending_orders(C):
    """挂单巡检（每bar调用，同分钟节流）：超时未成交 → 撤单重下，重试封顶后回滚
    避免错过交易黄金期。模拟端同样可用（全局函数反查订单）。"""
    global _pending_orders, _last_pending_min
    if not _pending_orders:
        return
    now = _get_market_time(C)
    log_min = now.hour * 100 + now.minute
    if log_min == _last_pending_min:
        return
    _last_pending_min = log_min
    orders = _query_orders()
    if orders is None:
        # 反查接口不可用：本分钟跳过动作（不撤单不重下，防重复下单）
        _log("[pending] 订单反查不可用，巡检跳过（防重复下单）", C)
        return
    for code, info in list(_pending_orders.items()):
        try:
            elapsed_min = (now - info["time"]).total_seconds() / 60.0
        except Exception:
            elapsed_min = 0
        # 跨日强制清理：QMT 模拟端只保留当日委托数据，隔日反查必失败。
        # pending 的 time 与当前时刻不在同一交易日 → 直接回滚并移出跟踪，
        # 不再走"未反查到就跳过"的死路（该路径会让残留跨天滞留、永不收敛）。
        try:
            same_day = info["time"].strftime("%Y-%m-%d") == now.strftime("%Y-%m-%d")
        except Exception:
            same_day = False
        if not same_day:
            _rollback_pending(C, code, info)
            _pending_orders.pop(code, None)
            _log("[pending] %s 跨日残留，强制回滚并移出" % code, C)
            continue
        if elapsed_min < PENDING_TIMEOUT_MIN:
            continue
        # 反查成交状态：已全部成交则移出跟踪
        order = _find_order(orders, code, info["type"])
        if order is not None:
            vol_traded = getattr(order, "m_nVolumeTraded", 0) or 0
            if vol_traded >= info["amount"]:
                _pending_orders.pop(code, None)
                _log("[pending] %s 已全部成交 %d股，移出跟踪" % (code, vol_traded), C)
                continue
            if vol_traded > 0:
                # 部分成交：撤剩余并重下剩余
                info["amount"] = info["amount"] - vol_traded
                info["already_traded"] = info.get("already_traded", 0) + vol_traded
                _pending_orders[code] = info
                _log("[pending] %s 部分成交 %d股，剩余 %d股" % (code, vol_traded, info["amount"]), C)
        else:
            # 订单不在委托列表：可能已成交被移除或已撤。不重下，等收盘对账校准
            _log("[pending] %s 未找到 %s 委托，跳过重下（等收盘对账校准）" % (code, info["type"]), C)
            continue
        # 冷却检查（time 已重置则天然满足，此处兜底防极端情况）
        if elapsed_min < RETRY_COOLDOWN_MIN:
            continue
        # 重试封顶：放弃并回滚估算记账
        if info.get("retries", 0) >= PENDING_MAX_RETRIES:
            _rollback_pending(C, code, info)
            _pending_orders.pop(code, None)
            continue
        # 撤单重下（传入已反查到的 order 供提取撤单标识；此路径下 order 必非 None）
        _retry_pending(C, code, info, order)

def _get_price(C, code, field="close"):
    """安全取价"""
    try:
        data = C.get_market_data_ex([field], [code], period="1d")
        if data and code in data and len(data[code]) > 0:
            return float(data[code][field].iloc[-1])
    except Exception:
        pass
    return None

def _score_stocks(C, pool, fin_data, bp_hist, ind_map, today_str, st_set=None):
    """评分: 行业中性BP z-score * Z_WEIGHT + hp * HP_WEIGHT (当前 Z=1.0 / HP=0.0)

    E1 (2026-10-13): 候选池先过回测同款宇宙筛选 (镜像 runner.get_candidates):
      circ_mv∈(MARKET_CAP_MIN, MARKET_CAP_MAX) 且 pe_ttm>PE_TTM_MIN 且 pb>PB_MIN
      且 质量排雷 (bps>0 & roe>0 & profit_dedt>0) 且 非ST。
    仅通过筛选的股票进入 bp_vals 参与评分 → 生产候选池与回测一致。
    (停牌在买入时由 _is_suspended 实时剔除; 退市排雷由 _delist_hit_qmt 在换仓流程处理。)
    """
    if st_set is None:
        st_set = set()
    scores = {}
    # 收集BP值 (先过宇宙筛选)
    bp_vals = {}
    for code in pool:
        # ST 实时主判 (2a): C.get_stock_name 含 "ST" 即剔（快照 ST 状态可能过期）；
        # 名称查询失败/为空时回落 st_set 兜底（_load_st_list 改读 SNAPSHOT is_st）
        try:
            stock_name = C.get_stock_name(code)
        except Exception:
            stock_name = None
        if stock_name and "ST" in str(stock_name):
            continue
        if code in st_set:
            continue  # exclude_st 兜底
        fd = fin_data.get(code, {})
        circ_mv = fd.get("circ_mv", 0)
        if not (circ_mv and circ_mv > MARKET_CAP_MIN and circ_mv < MARKET_CAP_MAX):
            continue
        pe = fd.get("pe_ttm", 0)
        if not (pe and pe > PE_TTM_MIN):
            continue
        pb = fd.get("pb", 0)
        if not (pb and pb > PB_MIN):
            continue
        # 质量排雷: bps>0 and roe>0 and profit_dedt>0 (缺字段视为不通过)
        bps = fd.get("bps", 0)
        roe = fd.get("roe", 0)
        pdt = fd.get("profit_dedt", 0)
        if not (bps and bps > QUALITY_BPS_MIN
                and roe and roe > QUALITY_ROE_MIN
                and pdt and pdt > QUALITY_PROFIT_DEDT_MIN):
            continue
        bp_vals[code] = 1.0 / pb

    if not bp_vals:
        # 非静默（2026-10-14）：候选为空是"空转"最可疑根因，必须留痕
        _log("[score] [!] 宇宙筛选后候选为空 (pool=%d, st=%d, fin=%d) -> 无候选"
             % (len(pool), len(st_set), len(fin_data)), C)
        return scores

    # 行业中性 z-score
    ind_groups = {}
    for code, bp in bp_vals.items():
        ind = ind_map.get(code, "其他")
        if ind not in ind_groups:
            ind_groups[ind] = []
        ind_groups[ind].append((code, bp))

    z_scores = {}
    for ind, items in ind_groups.items():
        bps = [b for _, b in items]
        mean_bp = sum(bps) / len(bps)
        std_bp = max((sum((b - mean_bp) ** 2 for b in bps) / len(bps)) ** 0.5, 1e-9)
        for code, bp in items:
            z_scores[code] = (bp - mean_bp) / std_bp

    # 历史分位
    hp_vals = {}
    for code in bp_vals:
        hist = bp_hist.get(code, [])
        if len(hist) >= HP_MIN:
            latest = hist[-1][1] if hist else None
            if latest is not None:
                count = sum(1 for _, h in hist[-HP_WINDOW:] if h <= latest)
                hp_vals[code] = count / min(len(hist), HP_WINDOW)

    # 加权合成
    for code in bp_vals:
        z = z_scores.get(code, 0)
        hp = hp_vals.get(code, 0.5)
        scores[code] = z * Z_WEIGHT + hp * HP_WEIGHT

    return scores

def _io_preflight():
    """预检（2b 接口探针版，2026-10-16）：原「state/log 文件可读写」探针随文件链路废止，
    改三探针：① 账户持仓接口可用（L2 唯一真相源） ② SNAPSHOT 非空 ③ PERIOD_LEDGER 非空。
    任一失败 → 转被动防盲跑（保留疑似沙箱/受限提示语义；
    恢复=重启QMT客户端并确认交易账号登录成功后重载策略）。"""
    # 探针1: 账户持仓接口（自闭环后状态重建/对账/兜底的唯一真相源）
    try:
        pos = get_trade_detail_data(ACCOUNT_ID, "STOCK", "position")
        if not isinstance(pos, list):
            raise RuntimeError("非list返回: %r" % (pos,))
    except Exception as e_pf:
        print("[preflight] [!] 账户持仓接口不可用: %s" % str(e_pf))
        print("[preflight] [!] 疑似QMT环境受限生效，本实例转被动防盲跑; 恢复: 重启QMT客户端并确认交易账号登录成功后重载策略")
        return False
    # 探针2: SNAPSHOT 非空（2a 评分/宇宙筛选数据源，未填充则评分管线空转）
    if not SNAPSHOT:
        print("[preflight] [!] SNAPSHOT 未内嵌/为空（2a 占位未填充）→ 转被动（宁可不交易）")
        return False
    # 探针3: PERIOD_LEDGER 非空（期初账本，init 状态重建唯一源）
    if not PERIOD_LEDGER:
        print("[preflight] [!] 期初账本未填（PERIOD_LEDGER 未内嵌/为空）→ 转被动（宁可不交易）")
        return False
    return True

def _rebuild_state_from_ledger(C):
    """init 状态重建（2b，替代 _load_state，三层状态模型 L2/L3）：
    白名单 = PERIOD_LEDGER.holdings ∩ 账户 position（账户为唯一真相）；
    成本=position.m_dOpenPrice，数量=m_nVolume（m_nCanUseVolume 一并打印；账本量封顶防共享账户纳管他人份额）；
    现金=ledger.cash_hint（共享账户推算值，打印口径警示）；
    当日去重=扫当日 order 表按 V2 remark 重建 _today_orders/_pending_orders（防重启后重复下单）。"""
    global _cash, _holdings, _entry_prices, _entry_dates, _nav_peak
    global _suspended_sells, _last_rebal_month, _today_orders, _pending_orders, _passive, _passive_reason
    # 干净起步（防同进程上一实例残留）
    _holdings = {}
    _entry_prices = {}
    _entry_dates = {}
    _today_orders = {}
    _pending_orders = {}
    _suspended_sells = []
    _nav_peak = 1.0
    _last_rebal_month = -1
    if not PERIOD_LEDGER:
        _passive = True
        _passive_reason = "期初账本未填"
        print("[rebuild] [!] 期初账本未填（PERIOD_LEDGER 为空）→ 转被动（宁可不交易）")
        return
    # 账号戳校验（T-20260823-004 精神延续）：账本必须内嵌 account_id 且匹配，严禁拿错账号账本交易
    stamp = str(PERIOD_LEDGER.get("account_id", "") or "")
    if stamp != str(ACCOUNT_ID):
        _passive = True
        _passive_reason = "期初账本账号戳不匹配(账本=%s 本策略=%s)" % (stamp or "无戳", ACCOUNT_ID)
        print("[rebuild] [!] %s → 转被动（fail-safe）" % _passive_reason)
        return
    # 现金：cash_hint 缺失/非法 → 转被动（不臆造现金）
    cash_hint = PERIOD_LEDGER.get("cash_hint")
    try:
        cash_val = float(cash_hint)
    except Exception:
        _passive = True
        _passive_reason = "期初账本缺 cash_hint/非法(%r)" % (cash_hint,)
        print("[rebuild] [!] %s → 转被动（不臆造现金）" % _passive_reason)
        return
    # 账户 position（L2 唯一真相源；探针已验证接口可用，此处异常仍 fail-safe）
    try:
        positions = get_trade_detail_data(ACCOUNT_ID, "STOCK", "position") or []
    except Exception as e_pos:
        _passive = True
        _passive_reason = "账户持仓接口异常: %s" % str(e_pos)
        print("[rebuild] [!] %s → 转被动" % _passive_reason)
        return
    pos_map = {}
    for p in positions:
        inst = getattr(p, "m_strInstrumentID", "") or getattr(p, "m_strSecurityCode", "") or ""
        c6 = _norm_code(inst)
        if c6:
            pos_map[c6] = p
    # 白名单 = LEDGER.holdings ∩ position
    led_holdings = PERIOD_LEDGER.get("holdings") or {}
    cost_total = 0.0
    n_hit = 0
    for code, hinfo in led_holdings.items():
        info = hinfo if isinstance(hinfo, dict) else {}
        if "." not in str(code):
            print("[rebuild] [!] 账本代码 %s 缺交易所后缀（应为 600000.SH 格式），请核对期初账本" % code)
        c6 = _norm_code(code)
        p = pos_map.get(c6) if c6 else None
        if p is None:
            print("[rebuild] %s 期初账本有、账户无持仓 → 不入账本（期初后已卖出/数据漂移）" % code)
            continue
        acct_vol = float(getattr(p, "m_nVolume", 0) or 0)
        can_use = float(getattr(p, "m_nCanUseVolume", 0) or 0)
        cost = float(getattr(p, "m_dOpenPrice", 0) or 0)
        led_vol = float(info.get("vol", 0) or 0)
        if acct_vol <= 0:
            print("[rebuild] %s 账户持仓量=0 → 跳过" % code)
            continue
        # 账本量封顶（共享账户防纳管他人份额，R1 同款口径）
        vol = int(min(acct_vol, led_vol)) if led_vol > 0 else int(acct_vol)
        _holdings[code] = vol
        _entry_prices[code] = cost
        _entry_dates[code] = str(info.get("entry_date", "") or "")
        cost_total += cost * vol
        n_hit += 1
        if led_vol > 0 and acct_vol != led_vol:
            print("[rebuild] %s 账户量(%.0f)与账本量(%.0f)不一致，取=%d" % (code, acct_vol, led_vol, vol))
        print("[rebuild] %s 持仓=%d股 可用=%d股 成本=%.3f 入场日=%s"
              % (code, vol, int(can_use), cost, _entry_dates[code] or "无"))
    _cash = cash_val
    print("[rebuild] 现金=%.0f（来源: 期初账本 cash_hint，共享账户推算值，口径仅供参考）" % _cash)
    # 暂缓卖出队列 / 上次换仓月
    _suspended_sells = [str(s) for s in (PERIOD_LEDGER.get("suspended_sells") or [])]
    try:
        _last_rebal_month = int(PERIOD_LEDGER.get("last_rebal_month", -1))
    except Exception:
        _last_rebal_month = -1
    # nav_peak：优先账本可选字段 nav_peak，缺省从期初成本推（保守口径）
    npk_val = 0.0
    try:
        npk_val = float(PERIOD_LEDGER.get("nav_peak") or 0)
    except Exception:
        npk_val = 0.0
    if npk_val > 0:
        _nav_peak = npk_val
    else:
        cost_nav = (_cash + cost_total) / CAPITAL_INIT if CAPITAL_INIT > 0 else 1.0
        _nav_peak = cost_nav if cost_nav > 0 else 1.0
    print("[rebuild] nav_peak=%.3f 暂缓卖出=%d 上次换仓月=%d 持仓交集=%d/%d"
          % (_nav_peak, len(_suspended_sells), _last_rebal_month, n_hit, len(led_holdings)))
    # 当日去重：扫当日 order 表按 V2 remark 重建水位（防重启后重复下单/对账失真）
    _rescan_today_orders(C, pos_map)

def _rescan_today_orders(C, pos_map):
    """当日去重重建（2b）：扫当日 order 表，按 V2 remark 重建 _today_orders/_pending_orders。
    QMT 模拟端 order 表仅当日有效，天然过滤隔日；委托列表时间正序，同 code 后写覆盖（最新单为准）。
    pos_map: {6位码: position对象}，供当日 V2 买入成交兜底纳管（L2 账户即账本）。
    注意: 重建条目一律标 rolled_back=True——估算从未计入 cash_hint 基数，对账时不得再反冲估算
    （否则重启后对账产生幻影现金）；重启重建的 pending 若后续回滚，其现金反冲为 cash_hint 推算口径
    的近似值（期初账本固有限制，规格已接受）。"""
    global _today_orders, _pending_orders
    try:
        orders = get_trade_detail_data(ACCOUNT_ID, "STOCK", "order") or []
    except Exception:
        print("[rebuild] [!] 当日委托反查不可用，跳过去重重建（风险: 重启前活单水位缺失，巡检/对账降级）")
        return
    if not orders:
        return
    now = _get_market_time(C)
    today_str = now.strftime("%Y-%m-%d")
    # 6位码 → 内部全格式码 映射（账本持仓键优先，SNAPSHOT 键兜底）
    full_map = {}
    for k in (PERIOD_LEDGER.get("holdings") or {}):
        full_map[_norm_code(k)] = k
    for k in (SNAPSHOT or {}):
        if _norm_code(k) not in full_map:
            full_map[_norm_code(k)] = k
    n_scan = 0
    for o in orders:
        try:
            remark = str(getattr(o, "m_strRemark", "") or "")
            if remark.find("V2") < 0:
                continue  # 只归集本策略委托（共享账户防串账）
            op = str(getattr(o, "m_strOptName", "") or "")
            if "买" in op:
                dirn = "buy"
            elif "卖" in op:
                dirn = "sell"
            else:
                continue
            inst = getattr(o, "m_strInstrumentID", "") or getattr(o, "m_strSecurityCode", "") or ""
            c6 = _norm_code(inst)
            vol = int(float(getattr(o, "m_nVolume", 0) or 0))
            traded = int(float(getattr(o, "m_nVolumeTraded", 0) or 0))
            price = 0.0
            for pa in ("m_dPrice", "m_nPrice"):
                try:
                    pv = getattr(o, pa, None)
                except Exception:
                    pv = None
                if pv:
                    price = float(pv)
                    break
            if vol <= 0 or not c6:
                continue
            code = full_map.get(c6, inst)
            od = {"dir": dirn, "amount": vol, "price": price, "entry_date": today_str,
                  "rolled_back": True}
            if dirn == "sell":
                od["entry_price"] = _entry_prices.get(code, 0) or 0
                od["entry_date"] = (_entry_dates.get(code, "") or "") or today_str
            # 后写覆盖（最新单为准）
            _today_orders[code] = od
            _pending_orders.pop(code, None)
            # 仍存活且未成交完 → 重建待成交跟踪（time 重置，巡检重新计时）
            remaining = vol - traded
            dead = False
            try:
                st = int(getattr(o, "m_nOrderStatus", 0) or 0)
                dead = st in CONFIRM_DEAD_STATUS
            except Exception:
                dead = False
            if (not dead) and remaining > 0:
                pd_info = {
                    "type": dirn, "amount": remaining, "original_amount": vol,
                    "price": price, "time": now, "retries": 0,
                }
                if dirn == "sell":
                    pd_info["entry_price"] = od.get("entry_price", 0)
                    pd_info["entry_date"] = od.get("entry_date", "")
                _pending_orders[code] = pd_info
            n_scan += 1
        except Exception as e_o:
            print("[rebuild] [!] 委托扫描异常跳过一条: %s" % str(e_o))
    if n_scan:
        print("[rebuild] 当日V2委托重建: 命中=%d 今日下单=%d 待成交跟踪=%d"
              % (n_scan, len(_today_orders), len(_pending_orders)))
    # 当日 V2 买入已成交但不在期初账本（期初后买入）→ 账户有货则纳管（L2 账户即账本，
    # 下单量封顶防共享账户他人份额），防重启后换仓重复买入同一标的
    for code, od in list(_today_orders.items()):
        if od.get("dir") != "buy" or code in _holdings:
            continue
        p = pos_map.get(_norm_code(code))
        if p is None:
            continue
        acct_vol = float(getattr(p, "m_nVolume", 0) or 0)
        cost = float(getattr(p, "m_dOpenPrice", 0) or 0)
        vol = int(min(acct_vol, float(od.get("amount", 0) or 0)))
        if vol <= 0:
            continue
        _holdings[code] = vol
        _entry_prices[code] = cost if cost > 0 else float(od.get("price", 0) or 0)
        _entry_dates[code] = str(od.get("entry_date", "") or today_str)
        print("[rebuild] %s 当日V2买入成交兜底纳管 %d股（期初账本外，防重复买入）" % (code, vol))

def init(C):
    """初始化（2b 自闭环状态侧）：同进程双实例防抖(_g_running) → 接口探针预检 → 期初账本重建。
    文件锁/state 落盘/遥测链路已全部废止（运行期零文件IO）。"""
    global _inited, _passive, _passive_reason, _g_running
    _passive = False
    _passive_reason = ""
    if _g_running:
        # 同进程双实例防抖（2b，文件锁替代）：上一实例未 exit 又 init → 转被动，不动全局状态
        _passive = True
        _passive_reason = "同进程双实例(_g_running已置位，先前实例未exit)"
        _log("[init] [!] 被动实例(%s) build=%s" % (_passive_reason, BUILD_TAG), C)
        _inited = True
        return
    if not _io_preflight():
        _passive = True
        _passive_reason = "预检失败(账户接口/SNAPSHOT/期初账本)"
    else:
        # init 状态重建（2b，替代 _load_state）：期初账本 ∩ 账户 position + 当日委托去重
        _rebuild_state_from_ledger(C)
    if not _passive:
        _g_running = True
    if _passive:
        _log("[init] [!] 被动实例(%s) build=%s" % (_passive_reason or "未知原因", BUILD_TAG), C)
    else:
        _log("[init] 策略初始化完成, 持仓=%d, 现金=%.0f, build=%s" % (len(_holdings), _cash, BUILD_TAG), C)
    _inited = True

def handlebar(C):
    global _last_rebal_month, _nav_peak, _last_rebal_log_min

    # 被动守卫（2b）：被动实例不买不卖不落盘，仅 print 心跳
    if _passive:
        _heart(C)
        return
    # 心跳（2b 打印化，替代遥测 heart json 落盘）：[HEART] 60 秒节流 print
    _heart(C)

    now = _get_market_time(C)
    today_str = now.strftime("%Y-%m-%d")
    current_month = now.month
    current_hour = now.hour
    current_minute = now.minute

    # ============ 时间过滤 ============
    # A股连续竞价时段: 09:30-11:30, 13:00-14:57
    # 交易信号(买入/卖出/挂单巡检)只在连续竞价内触发; 收盘集合竞价(14:57-15:00)
    # 不收市价单(本策略全用市价单), 故下单窗口收至 14:56, 避免产生废单/挂单.
    # 日终对账/状态保存独立于下单窗口: 14:50 之后直到收盘都执行.
    _h, _m = current_hour, current_minute
    is_trading_time = (
        (_h == 9 and _m >= 30) or
        (_h == 10) or
        (_h == 11 and _m <= 30) or
        (_h == 13) or
        (_h == 14 and _m <= 56)
    )
    is_save_time = ((_h == 14 and _m >= 50) or (_h == 15 and _m == 0))
    # 日志节流：同一分钟内只打印一次换仓/补仓日志
    log_min = current_hour * 100 + current_minute
    log_gate = (log_min != _last_rebal_log_min)

    # ============ 挂单巡检（每bar，内部分钟节流） ============
    # 委托超时未成交自动撤单重下，避免错过交易黄金期
    if is_trading_time:
        _check_pending_orders(C)

    # ============ 换仓判断 ============
    need_rebal = False

    # 正常换仓：每2个月的第一个交易日
    # 注意: _last_rebal_month 只在真正执行换仓后提交（交易时点），
    #       避免"非交易时序先置标记、换仓body被跳过、整月丢失调仓"。
    if current_month != _last_rebal_month and current_month % REBALANCE_MONTHS == 1:
        need_rebal = True

    # 补仓触发：持仓低于60%时提前补仓（避免空仓资金闲置）
    # 保护：当日已对账（14:50 后）不再因空仓触发补仓，防止对账撤销持仓后
    #       尾盘重复建仓（2026-08-14 事故：对账误撤销 → 空仓 → 14:50 重买67只）。
    current_pct = len(_holdings) / N_STOCKS if N_STOCKS > 0 else 0
    reconciled_today = (_last_reconcile_date == today_str)
    if current_pct < MIN_POSITION_PCT and current_month != _last_rebal_month and not reconciled_today:
        need_rebal = True
        # 只在交易时点打日志，避免每个 bar 刷屏
        if is_trading_time and log_gate:
            _last_rebal_log_min = log_min
            _log("[rebal] 持仓比例=%.1f%% < %.0f%%, 触发补仓" % (current_pct * 100, MIN_POSITION_PCT * 100))

    # ============ 止损 + 持有期 + 组合回撤 + 卖出执行（仅交易时段） ============
    # 非交易时段不计算/不执行任何卖出信号（避免基于陈旧收盘价触发、或盘后误下单）
    if is_trading_time:
        sells = []
        for code in list(_holdings.keys()):
            if code in _pending_orders:
                continue
            price = _get_price(C, code, "close")
            if price is None:
                continue
            entry = _entry_prices.get(code, price)
            pnl = price / entry - 1.0 if entry > 0 else 0
            # 止损
            if pnl <= -STOP_LOSS:
                sells.append(code)
                continue
            # 持有期
            entry_d = _entry_dates.get(code, today_str)
            try:
                days_held = (now - datetime.strptime(entry_d, "%Y-%m-%d")).days
            except Exception:
                days_held = 0
            if days_held >= MAX_HOLDING_DAYS:
                sells.append(code)

        # 组合回撤检查
        nav = _calc_nav(C)
        if nav > _nav_peak:
            _nav_peak = nav
        dd = 1.0 - nav / _nav_peak if _nav_peak > 0 else 0
        if dd >= MAX_DRAWDOWN:
            sells = list(_holdings.keys())
            _nav_peak = nav
            _log("[risk] 触发组合回撤止损, dd=%.1f%%" % (dd * 100))

        # 执行卖出（每次bar都执行，防止跌停无法卖出）
        for code in sells:
            # 检查是否跌停/停牌，暂缓卖出
            if _is_limit_down(C, code):
                if code not in _suspended_sells:
                    _suspended_sells.append(code)
                    _log("[sell suspended] %s 跌停，暂缓卖出" % code, C)
                continue
            if _is_suspended(C, code):
                if code not in _suspended_sells:
                    _suspended_sells.append(code)
                    _log("[sell suspended] %s 停牌，暂缓卖出" % code, C)
                continue
            # 从暂缓队列移除（如果不在sells中但之前暂缓了）
            if code in _suspended_sells:
                _suspended_sells.remove(code)
            _execute_sell(C, code)

        # 处理暂缓队列中可以卖出的
        for code in list(_suspended_sells):
            if code in _pending_orders:
                continue
            if not _is_limit_down(C, code) and not _is_suspended(C, code):
                _suspended_sells.remove(code)
                if code in _holdings:
                    _execute_sell(C, code)

    # ============ 换仓选股（仅交易时段执行） ============
    if need_rebal and is_trading_time:
        # 真正开始执行换仓时才提交月度标记，防止非交易时序提前消费
        _last_rebal_month = current_month
        pool = _load_pool(C)
        fin_data = _load_financial()
        bp_hist = _load_bp_history()
        ind_map = _load_industry_map()
        delist_info = _load_delist_info()   # v2.3 退市排雷
        st_set = _load_st_list()            # E1 exclude_st

        scores = _score_stocks(C, pool, fin_data, bp_hist, ind_map, today_str, st_set)
        if not scores:
            # 非静默（2026-10-14）：候选为空时 _last_rebal_month 已被消费，本月不再重试 -> 必须留痕
            _log("[rebal] [!] 评分候选为空 -> 本月不再重试 (pool=%d, fin=%d)" % (len(pool), len(fin_data)), C)
            return

        # v2.3 退市排雷: 剔除退市风险股 (已退市/退市临近/市值红线)
        n_before = len(scores)
        scores = dict((c, s) for c, s in scores.items()
                      if not _delist_hit_qmt(c, fin_data, delist_info, today_str))
        n_screened = n_before - len(scores)
        if not scores:
            _log("[rebal] [!] 退市排雷后候选为空 (排雷前=%d, 剔除=%d) -> 本月不再重试" % (n_before, n_screened), C)
            return

        # 先撤旧单（避免挂单冲突）——必须在 buffer 卖出之前，否则新下卖单会被撤
        _cancel_pending_orders(C)

        # v2.3 buffer: 换仓卖出排名超出 BUFFER_KEEP_MAX 的持仓 (降换手; 0=关闭保持旧行为)
        if BUFFER_KEEP_MAX > 0:
            ranked_codes = [c for c, s in sorted(scores.items(), key=lambda x: -x[1])]
            rank_map = {}
            for i, c in enumerate(ranked_codes):
                rank_map[c] = i + 1
            buf_sells = 0
            buf_defer = 0
            for code in list(_holdings.keys()):
                rk = rank_map.get(code)
                if rk is not None and rk <= BUFFER_KEEP_MAX:
                    continue  # 保留: 排名在保留界内
                # 排名超界 / 落出候选池: 卖出 (跌停/停牌暂缓, 复用暂缓队列)
                if _is_limit_down(C, code) or _is_suspended(C, code):
                    if code not in _suspended_sells:
                        _suspended_sells.append(code)
                    buf_defer += 1
                    continue
                if code in _suspended_sells:
                    _suspended_sells.remove(code)
                _execute_sell(C, code)
                buf_sells += 1
            if buf_sells or buf_defer or n_screened:
                _log("[buffer] 排雷剔除%d只 | 卖出超界(>%d)持仓%d只 | 暂缓%d只(跌停/停牌)"
                     % (n_screened, BUFFER_KEEP_MAX, buf_sells, buf_defer), C)

        # 排除已持仓，取top N
        target = []
        for code, sc in sorted(scores.items(), key=lambda x: -x[1]):
            if len(target) >= N_STOCKS:
                break
            if code not in _holdings:
                target.append(code)

        # 买入
        for code in target:
            # 检查是否涨停/停牌，跳过
            if _is_limit_up(C, code):
                _log("[buy skip] %s 涨停，跳过买入" % code, C)
                continue
            if _is_suspended(C, code):
                _log("[buy skip] %s 停牌，跳过买入" % code, C)
                continue
            _execute_buy(C, code)

        if log_gate:
            _last_rebal_log_min = log_min
            _log("[rebal] 换仓完成, 持仓=%d, 候选=%d" % (len(_holdings), len(target)), C)

    # ============ 日终处理（2b：对账保留，真相源=账户position；state/遥测写回废止改 print 摘要） ============
    if is_save_time:
        if current_hour == 14 and current_minute >= 50 and _last_reconcile_date != today_str:
            _reconcile(C)
            _log("[eod] 日终摘要 持仓=%d 现金=%.0f nav=%.3f peak=%.3f（状态不落盘，真相源=账户position）"
                 % (len(_holdings), _cash, _calc_nav(C), _nav_peak), C)

def _holdings_value(C):
    """当前持仓市值（专属资金池口径）"""
    total = 0.0
    for code, amount in _holdings.items():
        price = _get_price(C, code, "close")
        if price and price > 0:
            total += amount * price
    return total

def _calc_nav(C):
    """专属资金池净值 = (现金 + 持仓市值) / 初始资金，收益滚动"""
    total = _cash + _holdings_value(C)
    return total / CAPITAL_INIT if CAPITAL_INIT > 0 else 1.0

def _reconcile(C):
    """收盘对账：用当日V2成交记录校准专属资金池（估算价→真实成交价）

    买入: 撤销估算扣减，按实际成交金额/数量回写持仓
    卖出: 撤销估算回笼，按实际成交金额回笼；部分成交恢复剩余持仓
    """
    global _cash, _holdings, _entry_prices, _entry_dates, _today_orders, _pending_orders, _last_reconcile_date
    try:
        if not _today_orders:
            return
        # 获取当日全部成交，按code汇总（deal记录当日有效）
        # 生产/模拟端统一走全局函数（C 对象无反查接口；qmt_wrapper 生产验证）
        # 共享账户防串账：只归集 remark 含 'V2' 的本策略成交；
        # 其他策略（如 ATR 低波）同 code 的 deal 不计入，避免把他人成交算进本资金池。
        try:
            deals = get_trade_detail_data(ACCOUNT_ID, "STOCK", "deal") or []
        except Exception:
            deals = []
        traded = {}
        for d in deals:
            code = _norm_code(getattr(d, "m_strSecurityCode", "") or getattr(d, "m_strInstrumentID", ""))
            direction = str(getattr(d, "m_strOptName", "") or "")
            remark = str(getattr(d, "m_strRemark", "") or "")
            vol = float(getattr(d, "m_nVolume", 0) or 0)
            price = float(getattr(d, "m_nPrice", 0) or 0)
            if not code or vol <= 0 or price <= 0:
                continue
            if remark.find("V2") < 0:
                continue
            if code not in traded:
                traded[code] = {"buy_vol": 0.0, "buy_amt": 0.0, "sell_vol": 0.0, "sell_amt": 0.0}
            if "买入" in direction:
                traded[code]["buy_vol"] += vol
                traded[code]["buy_amt"] += vol * price
            elif "卖出" in direction:
                traded[code]["sell_vol"] += vol
                traded[code]["sell_amt"] += vol * price

        for code, od in list(_today_orders.items()):
            tr = traded.get(_norm_code(code))
            rolled = od.get("rolled_back", False)
            if od["dir"] == "buy":
                if not rolled:
                    # 撤销估算扣减，按实际成交校准
                    _cash += od["amount"] * od["price"]
                if tr and tr["buy_vol"] > 0:
                    _cash -= tr["buy_amt"]
                    _holdings[code] = tr["buy_vol"]
                    _entry_prices[code] = tr["buy_amt"] / tr["buy_vol"]
                    _log("[reconcile] %s 买入校准 成交=%.0f股 均价=%.3f 现金=%.0f"
                         % (code, tr["buy_vol"], _entry_prices[code], _cash), C)
                elif not rolled:
                    # deal 反查为空时，以账户真实持仓兜底：
                    # 模拟端 deal 可能漏记 remark 或当日时序，账户 position 是最终真相。
                    # 账户有货 → 买入实际成交（至少部分），以实际持仓量校准；
                    # 账户确无货 → 才确认未成交（涨停买不进等），撤销虚拟持仓；
                    # 查询失败 → 无法判定，保守保留持仓，等下一交易日再对（不主动撤销）。
                    pos = _get_acct_position(code)
                    if pos is not None and pos is not _ACCT_QUERY_FAIL:
                        acct_vol = float(getattr(pos, "m_nVolume", 0) or 0)
                        if acct_vol > 0:
                            # 撤销估算扣减后需按估算价扣回（无真实成交价，用下单时价）
                            _cash -= od["amount"] * od["price"]
                            # R1修复(2026-08-16): 共享账户 position 含其他策略(如ATR)同code份额，
                            # 兜底量按当日下单量封顶，防把他人份额纳管进本策略ledger导致后续误卖串账。
                            _holdings[code] = min(acct_vol, od["amount"])
                            _entry_prices[code] = od.get("price", 0) or 0
                            _entry_dates[code] = od.get("entry_date", "") or _get_market_time(C).strftime("%Y-%m-%d")
                            _log("[reconcile] %s 买入成交(账户持仓兜底,min封顶) 持仓=%.0f股(账户%.0f) 现金=%.0f"
                                 % (code, min(acct_vol, od["amount"]), acct_vol, _cash), C)
                            continue
                    if pos is _ACCT_QUERY_FAIL:
                        _log("[reconcile] %s 买入对账兜底查询失败，保守保留持仓" % code, C)
                        continue
                    _holdings.pop(code, None)
                    _entry_prices.pop(code, None)
                    _entry_dates.pop(code, None)
                    _log("[reconcile] %s 买入未成交（账户无货）撤销虚拟持仓" % code, C)
                # rolled 且无成交：回滚已恢复现金/撤销持仓，跳过
            elif od["dir"] == "sell":
                if not rolled:
                    # 撤销估算回笼，按实际成交校准
                    _cash -= od["amount"] * od["price"]
                if tr and tr["sell_vol"] > 0:
                    _cash += tr["sell_amt"]
                    remain = od["amount"] - tr["sell_vol"]
                    if remain > 0:
                        # 部分成交：剩余持仓恢复（保留原成本）
                        _holdings[code] = remain
                        _entry_prices[code] = od.get("entry_price", 0)
                        _entry_dates[code] = od.get("entry_date", "")
                        _log("[reconcile] %s 卖出部分成交 剩余=%.0f股" % (code, remain), C)
                    else:
                        # 全部成交：持仓清零（回滚曾恢复，此处按实际成交移除）
                        _holdings.pop(code, None)
                        _entry_prices.pop(code, None)
                        _entry_dates.pop(code, None)
                        _log("[reconcile] %s 卖出全部成交，持仓清零" % code, C)
                elif not rolled:
                    # deal 反查为空时，以账户真实持仓兜底：
                    # 账户仍有货 → 未成交（跌停卖不出等），恢复持仓；
                    # 账户已无货（pos None）→ 实际已卖出（deal 漏记），维持回笼现金、持仓清零；
                    # 查询失败 → 无法判定，保守恢复持仓（宁可不回笼也不误判已卖出）。
                    pos = _get_acct_position(code)
                    acct_vol = 0
                    if pos is not None and pos is not _ACCT_QUERY_FAIL:
                        acct_vol = float(getattr(pos, "m_nVolume", 0) or 0)
                    if pos is _ACCT_QUERY_FAIL:
                        _log("[reconcile] %s 卖出对账兜底查询失败，保守恢复持仓" % code, C)
                    elif pos is None or acct_vol <= 0:
                        # 账户确无货：实际已卖出（deal 漏记）。
                        # ★P0修复(2026-10-16)：QMT 全部成交后 position 记录仍存在但
                        #   m_nVolume=0，必须按持仓量判定，不能只判对象是否存在——
                        #   否则会把已成交的卖出误判为「未成交」撤销回持仓。
                        _cash += od["amount"] * od["price"]
                        _holdings.pop(code, None)
                        _entry_prices.pop(code, None)
                        _entry_dates.pop(code, None)
                        _log("[reconcile] %s 卖出成交(账户无货兜底 acct_vol=%.0f) 现金=%.0f"
                             % (code, acct_vol, _cash), C)
                        continue
                    # 账户仍有货：未成交/部分成交（跌停卖不出等），恢复剩余持仓。
                    # R1同款封顶：共享账户 position 含其他策略同 code 份额，按当日下单量封顶。
                    _holdings[code] = min(acct_vol, od["amount"])
                    _entry_prices[code] = od.get("entry_price", 0)
                    _entry_dates[code] = od.get("entry_date", "")
                    _log("[reconcile] %s 卖出未成交(账户仍有货 acct_vol=%.0f) 恢复持仓=%.0f"
                         % (code, acct_vol, min(acct_vol, od["amount"])), C)
                # rolled 且无成交：回滚已恢复持仓/现金，跳过
        _today_orders = {}
        _pending_orders = {}
        _last_reconcile_date = _get_market_time(C).strftime("%Y-%m-%d")
        _log("[reconcile] 对账完成 持仓=%d 现金=%.0f" % (len(_holdings), _cash), C)
    except Exception as e:
        _log("[reconcile error] %s" % str(e), C)

def _execute_buy(C, code):
    """买入：从专属资金池等权分配，估算记账（收盘对账校准）"""
    global _pending_orders, _cash, _today_orders
    try:
        if code in _pending_orders:
            return
        price = _get_price(C, code, "close")
        if price is None or price <= 0:
            return
        # 待买只数（补仓场景 < 80）
        n_held = len(_holdings)
        n_to_buy = N_STOCKS - n_held
        if n_to_buy <= 0:
            return
        # 等权分配：每只金额 = min(资金池总资产/80, 剩余现金/待买只数)
        port_value = _cash + _holdings_value(C)
        per_stock = min(port_value / N_STOCKS, _cash / n_to_buy)
        amount = int(per_stock / price / 100) * 100
        if amount <= 0:
            return
        # 现金兜底：不够买100股就跳过
        if amount * price > _cash:
            amount = int(_cash / price / 100) * 100
            if amount <= 0:
                return
        # 下单（统一入口，2026-10-13 修复）：prType=11 指定价 + 激进限价促成交，
        # 取价失败回退 prType=5 最新价；11 参数格式末尾必须传 C。
        _place_order(C, "buy", code, amount, price)
        # 记录待成交订单 + 当日下单（对账用）
        _pending_orders[code] = {
            "type": "buy",
            "amount": amount,
            "original_amount": amount,
            "price": price,
            "time": _get_market_time(C),
            "retries": 0
        }
        _today_orders[code] = {"dir": "buy", "amount": amount, "price": price, "entry_date": _get_market_time(C).strftime("%Y-%m-%d")}
        # 估算记账（真实成交价收盘对账校准）
        _holdings[code] = amount
        _entry_prices[code] = price
        _entry_dates[code] = _get_market_time(C).strftime("%Y-%m-%d")
        _cash -= amount * price
        _log("[buy] %s 数量=%d 价格=%.2f 现金=%.0f" % (code, amount, price, _cash), C)
        # 2b：下单即落盘/遥测链路废止（重启水位由 init 扫当日 order 表重建）
    except Exception as e:
        _log("[buy error] %s: %s | %s" % (code, str(e), _exc_tb()), C)

def _exc_tb():
    """返回异常堆栈（单行，QMT3.6兼容）"""
    try:
        import traceback
        return traceback.format_exc().replace("\n", " | ")[:800]
    except Exception:
        return "no-tb"

def _execute_sell(C, code):
    """卖出：回笼资金到专属资金池，估算记账（收盘对账校准）"""
    global _pending_orders, _cash, _today_orders
    try:
        if code in _pending_orders:
            return
        amount = _holdings.get(code, 0)
        if amount <= 0:
            return
        price = _get_price(C, code, "close")
        if price is None or price <= 0:
            price = _entry_prices.get(code, 0) or 0
        entry_price = _entry_prices.get(code, price)
        entry_date = _entry_dates.get(code, _get_market_time(C).strftime("%Y-%m-%d"))
        # 下单（统一入口，2026-10-13 修复）：prType=11 指定价 + 激进限价促成交，
        # 取价失败回退 prType=5 最新价；11 参数格式末尾必须传 C。
        _place_order(C, "sell", code, amount, price)
        # 记录待成交订单 + 当日下单（对账用，保留原成本价/日期用于部分成交/回滚恢复）
        _pending_orders[code] = {
            "type": "sell",
            "amount": amount,
            "original_amount": amount,
            "price": price,
            "time": _get_market_time(C),
            "retries": 0,
            "entry_price": entry_price,
            "entry_date": entry_date,
        }
        _today_orders[code] = {
            "dir": "sell", "amount": amount, "price": price,
            "entry_price": entry_price, "entry_date": entry_date,
        }
        # 估算记账（真实成交价收盘对账校准）
        _cash += amount * price
        _log("[sell] %s 数量=%d 价格=%.2f 现金=%.0f" % (code, amount, price, _cash), C)
        _holdings.pop(code, None)
        _entry_prices.pop(code, None)
        _entry_dates.pop(code, None)
        # 2b：下单即落盘/遥测链路废止（重启水位由 init 扫当日 order 表重建）
    except Exception as e:
        _log("[sell error] %s: %s | %s" % (code, str(e), _exc_tb()), C)

def exit(C):
    # 2b：文件锁/state落盘/遥测链路废止；被动实例不对账直接退出，活跃实例对账后复位 _g_running
    global _g_running
    if _passive:
        _log("[exit] 被动实例退出（%s），不对账" % (_passive_reason or "被动"), C)
        return
    _reconcile(C)
    _g_running = False   # 释放同进程单实例标志（同进程后续重启可重新接管）
    _log("[exit] 策略退出, 持仓=%d 现金=%.0f（状态不落盘，真相源=账户position）" % (len(_holdings), _cash), C)
