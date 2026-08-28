# coding: utf-8
"""QMT/miniQMT 交易与盯盘配置。

使用前请按实际情况修改：
  - QMT_PATH / USERDATA：miniQMT 客户端安装路径与数据目录
  - ACCOUNT_ID：券商资金账号（模拟盘/实盘账号）
  - 风控参数按你的资金与偏好调整
"""
import csv
import json
import os
import time

# ---- miniQMT 客户端路径 ----
QMT_PATH = r"E:\国金QMT交易端模拟"
USERDATA = os.path.join(QMT_PATH, "userdata_mini")          # xtquant 连接路径
XTPACK = os.path.join(QMT_PATH, "bin.x64", "Lib", "site-packages")  # xtquant 包位置

# ---- 交易账户（必填：你的资金账号）----
ACCOUNT_ID = "67014907"                # 国金证券 QMT 测试资金账号
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


# ---- 账本 account_id 戳（红线 T-20260823-004，2026-08-28 补齐 P16）----
CAPITAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "strategy_capital.json")
TRADE_LOG_FIELDS = ["time", "code", "side", "vol", "price", "score", "order_id", "account_id"]


def _bak_stamped(path, stamp):
    """按红线命名规范备份账本：.bak_acct_<旧戳|nostamp>_<时间戳>。返回备份路径。"""
    bak = "%s.bak_acct_%s_%s" % (path, stamp or "nostamp", time.strftime("%Y%m%d_%H%M%S"))
    try:
        with open(path, "rb") as a, open(bak, "wb") as b:
            b.write(a.read())
    except Exception as e:
        print("    !! [account_id 校验] 备份失败: %r" % (e,))
        bak = ""
    return bak


def append_trade_rows(rows):
    """追加成交记录到 qmt_trade_log.csv，自动补 account_id 戳（写档加字段）。
    rows 为字段列表的列表；静默，成功打印由调用方负责（保持调度链日志行稳定）。"""
    if not rows:
        return
    os.makedirs(os.path.dirname(TRADE_LOG), exist_ok=True)
    need_header = not os.path.exists(TRADE_LOG) or os.path.getsize(TRADE_LOG) == 0
    with open(TRADE_LOG, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if need_header:
            w.writerow(TRADE_LOG_FIELDS)
        for r in rows:
            w.writerow(list(r) + [ACCOUNT_ID])


def load_trade_log_rows():
    """读取成交记录（DictReader 行列表），带 account_id 校验（红线 T-20260823-004）。
    文件缺失 -> []；表头缺 account_id 列 -> 备份(.bak_acct_nostamp_*)并返回 []；
    存在与 ACCOUNT_ID 不符的记录 -> 整文件不可信：备份(.bak_acct_<旧戳>_*)并返回 []（fail-safe）。"""
    if not os.path.exists(TRADE_LOG):
        return []
    with open(TRADE_LOG, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "account_id" not in (reader.fieldnames or []):
            _bak_stamped(TRADE_LOG, "")
            print("    !! [account_id 校验] 成交记录缺账号戳，已备份并按空账本处理（fail-safe）")
            return []
        rows = list(reader)
    foreign = [r for r in rows if (r.get("account_id") or "").strip() != ACCOUNT_ID]
    if foreign:
        _bak_stamped(TRADE_LOG, (foreign[0].get("account_id") or "").strip())
        print("    !! [account_id 校验] 成交记录含 %d 条非本账号(%s)记录（首条 %s %s），已备份并按空账本处理（fail-safe）"
              % (len(foreign), ACCOUNT_ID, foreign[0].get("time", ""), foreign[0].get("code", "")))
        return []
    return rows


def load_capital_pool():
    """读取 strategy_capital.json，带 account_id 校验（红线 T-20260823-004）。
    文件缺失/损坏/缺戳/账号不符 -> 备份(.bak_acct_*)并返回 None（调用方回退 START_CAPITAL）。"""
    if not os.path.exists(CAPITAL_FILE):
        return None
    try:
        with open(CAPITAL_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print("    !! [account_id 校验] 资金池读取失败: %r" % (e,))
        return None
    stamp = str(data.get("account_id", "") or "")
    if stamp != str(ACCOUNT_ID):
        _bak_stamped(CAPITAL_FILE, stamp)
        print("    !! [account_id 校验] 资金池账号戳缺失或不符(=%r, 本策略=%s)，已备份并按无资金池处理（fail-safe）" % (stamp, ACCOUNT_ID))
        return None
    return data
