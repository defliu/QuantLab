# coding: utf-8
"""G2（大QMT 文件桥）独立配置 —— 与 V1.3（miniQMT 67014907）**完全隔离**，绝不 import qmt_config.py。

- 账号：67014907（国金 miniQMT；2026-10-22 拍板从 70180771 迁入，70180771 未开通 miniQMT）
- 执行：外部 Python 经 xtquant 直连下单（2026-10-22 起，不再写文件桥 cmd/orders）
- 资金池：独立文件 g2_strategy_capital.json（不读 V1.3 data/strategy_capital.json）
- 候选：data/selections/g2/<date>_g2_top10.csv（deploy_predict_g2 --top 10 产出，对齐回测 TOP10），不混 V1.3 D_model_top10
- 边界红线：只能动 G2 自己账本（g2_trade_log）的票，绝不纳管/卖出他人持仓
"""
import json
import os
import time

# ---- 账号与桥 ----
# 2026-10-22 拍板：由 70180771（无 miniQMT 权限）迁至 67014907，改为 xtquant 直连下单。
# 原文件桥 cmd/orders_*.json 通道作废（QMT 沙箱下内置侧读不到）。
ACCOUNT_ID = "67014907"
STRATEGY = "Project_16_g2"
BRIDGE_DIR = "D:/QMT_POOL/g2_bridge"
CMD_DIR = os.path.join(BRIDGE_DIR, "cmd")
STATE_DIR = os.path.join(BRIDGE_DIR, "state")

# ---- xtquant 连接与下单参数（供 order_guard.configure() 消费）----
QMT_PATH = r"D:\国金QMT交易端模拟"
USERDATA = os.path.join(QMT_PATH, "userdata_mini")
XTPACK = os.path.join(QMT_PATH, "bin.x64", "Lib", "site-packages")
BUY_PRICE_TYPE = "FIX"               # 买入限价（对齐 V1.3）
AUTO_SELL_PRICE_TYPE = "LATEST"      # 卖出最新价（对齐 V1.3）
# G2 独立待补价队列（绝不与 V1.3 / ENS 共用）
PENDING_FILL_FILE = os.path.join(BRIDGE_DIR, "g2_pending_fills.jsonl")
# G2 独立成交账本（2026-10-22 新建；原靠内置桥写 state/fills_*.json，已随桥作废）
TRADE_LOG = os.path.join(BRIDGE_DIR, "g2_trade_log.csv")
TRADE_LOG_FIELDS = ["time", "account_id", "code", "side", "vol", "price",
                    "order_id", "remark", "reason"]
# 下单备注（归属隔离：同账号三策略靠 remark 区分，绝不交叉纳管）
REMARK_BUY = "p16_g2_new"
REMARK_SELL = "p16_g2_risk"


# ---- 路径（外部信号层） ----
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
G2_SELECT_DIR = os.path.join(DATA_DIR, "selections", "g2")

# ---- 策略资金池（独立文件，与 V1.3 完全分离） ----
START_CAPITAL = 100000.0             # 初始 10 万
G2_CAPITAL_FILE = os.path.join(BRIDGE_DIR, "g2_strategy_capital.json")

# ---- 交易参数（对齐 V1.3 口径） ----
TOP_N = 3                            # 目标持仓数（等权；2026-09-21 拍板 TOP_N 2→3，依据《持仓只数最优论证_20260921.md》）
SELECT_TOP = 10                      # 候选池大小（对齐回测 TOP10；deploy_predict_g2 --top 10 产出 _g2_top10.csv）
RESERVE_CASH_PCT = 0.02              # 保留现金 2%（总仓 98%，T-20261003-006 拍板：吸收 P2-14 卖出费盲区年累积 ~1-1.7% + 换仓日时序错配）
MIN_ORDER_VOL = 100                  # 整手
REDLINE = 60.0                       # g2 评分红线（deploy_predict_g2 --threshold 60 已过滤）
HOLD_DAYS = 15                       # 持有期（交易日，2026-09-10 拍板升级 N15-live_trail：对齐网格最优 G2-live_trail/N15/红线60/TOP2 +0.213%；桥出场=STOP→TP→TRAIL 与回测 live_trail 一致）

# 持仓建仓日持久化（rebalance_g2 维护：{code: "YYYYMMDD"}）
HOLD_DATES_FILE = os.path.join(DATA_DIR, "rebalance_g2", "g2_hold_dates.json")

# 融合桥 ENS 账本（G2 反向互斥用：G2 换仓跳过 ENS 已持有的票，防同账户双桥争同一持仓，2026-09-13 补对称化）
ENS_HOLD_DATES_FILE = os.path.join(DATA_DIR, "rebalance_g2_ens", "g2_ens_hold_dates.json")

# ---- 飞书（沿用同一接收人，仅推送通道） ----
FEISHU_OPEN_ID = "ou_bd13444d8ea53c28249c669f43f3eeff"
# lark-cli 可执行文件（2026-09-23 路径加固 T-20260923-002）：主路径独立于 TRAE，缺失自动回退旧路径
_LARK_CLI_MAIN = r"C:\Users\Administrator\bin\lark-cli.exe"
_LARK_CLI_FALLBACK = r"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\1.0.5\bin\lark-cli.exe"
LARK_CLI = _LARK_CLI_MAIN if os.path.exists(_LARK_CLI_MAIN) else _LARK_CLI_FALLBACK


def _backup_stale_capital(path, old_stamp):
    """account_id 戳不匹配 → 备份旧档（AGENTS T-20260823-004 fail-safe）。

    2026-10-22 账号由 70180771 迁至 67014907：旧资金池带 70180771 戳，
    直接沿用会把「别的账号的收益」当成自己的起点。故备份后按 START_CAPITAL 空仓起步。
    """
    try:
        bak = "%s.bak_acct_%s_%s" % (path, old_stamp or "nostamp",
                                     time.strftime("%Y%m%d_%H%M%S"))
        os.replace(path, bak)
        print("[g2_config] 资金池 account_id 戳不匹配(旧=%s, 本策略=%s) → 已备份为 %s，"
              "按 %.0f 元空仓起步" % (old_stamp, ACCOUNT_ID, os.path.basename(bak), START_CAPITAL))
        return bak
    except Exception as e:
        print("[g2_config] 资金池备份失败（人工核对）: %r" % (e,))
        return None


def load_g2_capital():
    """读 G2 独立资金池（account_id 戳校验；失败/不匹配回退 START_CAPITAL）。
    收益滚动、亏损不补：capital = 初始 + 已实现盈亏 + 策略持仓浮盈。"""
    try:
        with open(G2_CAPITAL_FILE, encoding="utf-8") as f:
            d = json.load(f)
        stamp = str(d.get("account_id", ""))
        if stamp != ACCOUNT_ID:
            _backup_stale_capital(G2_CAPITAL_FILE, stamp)
            return float(START_CAPITAL)
        cap = float(d.get("capital", 0) or 0)
        if cap > 0:
            return cap
    except Exception as e:
        print("[g2_config] 资金池读取失败(%s)，回退 START_CAPITAL" % e)
    return float(START_CAPITAL)


def save_g2_capital(capital, note=""):
    """写 G2 独立资金池（原子写 + account_id 戳）。"""
    d = {
        "account_id": ACCOUNT_ID,
        "strategy": STRATEGY,
        "capital": round(float(capital), 2),
        "note": note,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = G2_CAPITAL_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, G2_CAPITAL_FILE)
    return d


# ---- 位置过滤与低开校验参数（G2-V1.0，2026-09-08 落地，可环境变量覆盖） ----
# 【重要修正 2026-09-08】G2 模型(g2_strong_real)口径回测验证：位置过滤（full/lite）均无增益甚至有害
# （红线60/N=10/0.1%：Base +0.149% → full -0.108%），故 G2 默认【不过滤】；仅显式 PF_MODE_G2 才启用。
# V1.3 链路(v3_enh)的 lite 过滤单独保留（58-lite +0.157% 验证有效，见 qmt_config.POSFILTER_MODE）。
POSFILTER_MODE = os.environ.get("PF_MODE_G2", "")       # G2 默认不过滤（""=关）；lite/full 需显式设置
GAP_HARD_PCT = float(os.environ.get("GAP_HARD_PCT", "-5.0"))   # 极端低开阈值，直接跳过
GAP_OPEN_PCT = float(os.environ.get("GAP_OPEN_PCT", "-3.0"))   # 低开触发阈值
GAP_VR       = float(os.environ.get("GAP_VR", "3.3"))          # 低开放行量比（单位时间口径，T-20261023-007）

# ---- 大盘门控（TIER_RULES，2026-09-11 补上，对齐 V1.3 口径 T-20260904-004）----
# 沪深300 当日涨跌幅（%）→ T 档：
#   T=0：<= TIER_STOP_PCT (-1.5)  → 停买（只卖不买，空位不补）
#   T=1：<= TIER_HALF_PCT (-1.0)  → 半仓（买入预算 = 资金池 × HALF_DEPLOY_PCT）
#   T=2：其余                     → 满仓（买入预算 = 资金池 × DEPLOY_PCT）
# 数据缺失（取不到沪深300）→ fail-safe 按 T=1 半仓 + 醒目告警（刹车数据缺失时降速不裸奔）。
TIER_STOP_PCT = float(os.environ.get("G2_TIER_STOP", "-1.5"))   # 停买线
TIER_HALF_PCT = float(os.environ.get("G2_TIER_HALF", "-1.0"))   # 半仓线
DEPLOY_PCT     = 0.98                                            # 满仓部署比例（保留 2% 现金，T-20261003-006：三档对照 98% 拿满仓收益增量 40-60%，回撤代价 <0.5pp）
HALF_DEPLOY_PCT = 0.50                                           # 半仓部署比例
HS300_QT_SYMBOL = "sh000300"                                     # 腾讯行情沪深300 代码


def today_str():
    return time.strftime("%Y%m%d")


def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")
