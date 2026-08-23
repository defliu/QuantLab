# coding: utf-8
"""QMT/miniQMT 交易与盯盘配置。

使用前请按实际情况修改：
  - QMT_PATH / USERDATA：miniQMT 客户端安装路径与数据目录
  - ACCOUNT_ID：券商资金账号（模拟盘/实盘账号）
  - 风控参数按你的资金与偏好调整
"""
import os

# ---- miniQMT 客户端路径 ----
QMT_PATH = r"E:\国金QMT交易端模拟"
USERDATA = os.path.join(QMT_PATH, "userdata_mini")          # xtquant 连接路径
XTPACK = os.path.join(QMT_PATH, "bin.x64", "Lib", "site-packages")  # xtquant 包位置

# ---- 交易账户（必填：你的资金账号）----
ACCOUNT_ID = "70180771"                # 国金证券 QMT 测试资金账号
# 登录密码在 miniQMT 客户端 GUI 登录时输入（xtquant 连接本地客户端不需要密码）

# ---- 策略资金池（账户资金隔离）----
# 账户总资金约 1000 万，但策略只用 START_CAPITAL 作为启动资金建仓；
# 之后的收益（已实现盈亏 + 持仓浮盈）会滚动进资金池，用于加仓。
START_CAPITAL = 100000                 # 策略启动资金（元），账户中仅此额度可用于策略建仓

# ---- 交易参数 ----
BUY_PRICE_TYPE = "FIX"                 # FIX=限价 / MARKET=市价（模拟盘建议先用限价）
MAX_POSITION_PCT = 0.50                # 单票最大仓位（95%总仓/2只=47.5%，留取整余量到50%）
MAX_POSITION_NUM = 2                   # 目标持仓数（回测最优=2只均分）
RESERVE_CASH_PCT = 0.05                # 保留现金比例（常态总仓 95%，留 5% 交易缓冲，不做T无抄底意义）
MIN_ORDER_AMOUNT = 2000.0              # 单笔最小委托金额（元）
MIN_ORDER_VOL = 100                    # 单笔最小委托股数（1手=100股）

# ---- 交易费率（结算统一按实盘口径；模拟盘仅验证流程，不用模拟盘费率）----
# 与用户实盘费用一致（2026 实测）：佣金 万2 双边最低5元 / 印花税 万5 仅卖出 / 过户费 万0.1 仅沪市(60开头)双边
COMM_RATE = 0.0002                     # 佣金费率（双边）
COMM_MIN = 5.0                         # 单笔最低佣金（元）
STAMP_RATE = 0.0005                    # 印花税（仅卖出）
TRANS_RATE = 0.00001                   # 过户费（仅沪市 60 开头，双边；深市不收取）

# ---- 盯盘/风控 ----
STOP_LOSS_PCT = -0.07                  # 止损线（成本价 -7%）
TAKE_PROFIT_PCT = 0.15                 # 止盈线（成本价 +15%）
TRAILING_PCT = 0.08                    # 移动止盈：最高价回撤 8% 平仓
MONITOR_INTERVAL = 5                   # 盯盘轮询间隔（秒）
MONITOR_WATCHLIST = [                  # 盯盘股票池（可含持仓与候选）
    "603969.SH", "300919.SZ", "001260.SZ", "603066.SH", "300201.SZ",
]

# ---- 信号自动执行（风控自动卖出）----
AUTO_SELL = False                      # 触发止损/止盈后是否自动下卖出单（默认关，风险提示见下）
AUTO_SELL_PRICE_TYPE = "LATEST"        # 卖出价类型: LATEST=市价(最新价) / FIX=限价(现价下一档)
# ⚠️ 自动卖出为真实交易委托，请确认账户、参数后再开启（建议先用模拟盘验证）

# ---- 输出 ----
SIGNAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "qmt_signal.json")  # 盯盘预警输出
TRADE_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "qmt_trade_log.csv")  # 成交记录

# ---- 飞书推送（lark-cli bot 私聊通道，已验证连通）----
LARK_CLI = r"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\1.0.4\bin\lark-cli.exe"  # lark-cli 可执行文件
FEISHU_OPEN_ID = "ou_76deaecde50e10576f8fdc8ba954a7b0"  # 接收人 open_id（刘诚，bot 私聊已测试连通）
