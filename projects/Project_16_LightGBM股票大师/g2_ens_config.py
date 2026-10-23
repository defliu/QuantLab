# coding: utf-8
"""融合（ensemble）大QMT 文件桥 · 独立配置 —— 与 G2 桥（g2_config.py）完全隔离，绝不 import g2_config.py。

- 账号：67014907（国金 miniQMT；2026-10-22 拍板从 70180771 迁入，70180771 未开通 miniQMT）
- 执行：外部 Python 经 xtquant 直连下单（2026-10-22 起，不再写文件桥 cmd/orders）
- 选股：deploy_predict_g2 --ensemble（v3_enh + G2 rank 融合 w=0.5），红线 60，TOP2
- 配置：live_trail/N10/红线60/TOP2（2026-09-10 拍板上线，对齐网格最优 融合-live_trail/N10 +0.211%）
- 资金池：独立文件 ens_strategy_capital.json（不读 G2/V1.3 资金池）
- 目录：D:/QMT_POOL/p16_ensemble_bridge（cmd/state 与 g2_bridge 完全分离）
- 重叠规避：融合换仓跳过 G2 账本（g2_hold_dates.json）已持有的票，防同账户双桥争同一持仓
"""
import json
import os
import time

# ---- 账号与桥 ----
# 2026-10-22 拍板：由 70180771（无 miniQMT 权限）迁至 67014907，改为 xtquant 直连下单。
ACCOUNT_ID = "67014907"
STRATEGY = "Project_16_ens"
BRIDGE_DIR = "D:/QMT_POOL/p16_ensemble_bridge"
CMD_DIR = os.path.join(BRIDGE_DIR, "cmd")
STATE_DIR = os.path.join(BRIDGE_DIR, "state")

# ---- xtquant 连接与下单参数（供 order_guard.configure() 消费）----
QMT_PATH = r"D:\国金QMT交易端模拟"
USERDATA = os.path.join(QMT_PATH, "userdata_mini")
XTPACK = os.path.join(QMT_PATH, "bin.x64", "Lib", "site-packages")
BUY_PRICE_TYPE = "FIX"               # 买入限价（对齐 V1.3）
AUTO_SELL_PRICE_TYPE = "LATEST"      # 卖出最新价（对齐 V1.3）
# ENS 独立待补价队列（绝不与 V1.3 / G2 共用）
PENDING_FILL_FILE = os.path.join(BRIDGE_DIR, "ens_pending_fills.jsonl")
# ENS 独立成交账本（2026-10-22 新建；原靠内置桥写 state/fills_*.json，已随桥作废）
TRADE_LOG = os.path.join(BRIDGE_DIR, "ens_trade_log.csv")
TRADE_LOG_FIELDS = ["time", "account_id", "code", "side", "vol", "price",
                    "order_id", "remark", "reason"]
# 下单备注（归属隔离：同账号三策略靠 remark 区分，绝不交叉纳管）
REMARK_BUY = "p16_ens_new"
REMARK_SELL = "p16_ens_risk"


# ---- 路径（外部信号层） ----
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
ENS_SELECT_DIR = os.path.join(DATA_DIR, "selections", "g2_ens")

# ---- 策略资金池（独立文件，与 G2/V1.3 完全分离） ----
START_CAPITAL = 100000.0             # 初始 10 万（2026-09-10 拍板）
ENS_CAPITAL_FILE = os.path.join(BRIDGE_DIR, "ens_strategy_capital.json")

# ---- 交易参数（对齐回测 融合-live_trail/N10/红线60/TOP2） ----
TOP_N = 5                            # 目标持仓数（等权；2026-09-21 拍板 TOP_N 2→5，依据《持仓只数最优论证_20260921.md》）
SELECT_TOP = 10                      # 候选池大小（对齐回测 TOP10；deploy_predict_g2 --ensemble --top 10 产出）
RESERVE_CASH_PCT = 0.02              # 保留现金 2%（总仓 98%，T-20261003-006 与 G2/V1.3 同拍板）
MIN_ORDER_VOL = 100                  # 整手
REDLINE = 60.0                       # 评分红线（deploy_predict_g2 --threshold 60 已过滤）
HOLD_DAYS = 10                       # 持有期（交易日，对齐回测 N=10：满 N 个交易日到期卖出，止损/止盈优先）

# ---- 大盘门控（T-20261009-008 / E-4，2026-10-10 补上）：对齐 G2/V1.3 三档 ----
# T=0（≤-1.5%）停买 / T=1（<-1.0%）半仓 / T=2 满仓；数据缺失 fail-safe 半仓。
# 阈值与部署比例**不在此重复定义**——rebalance_ens.py import 复用 rebalance_g2 的
# load_hs300_pct/_calc_tier + g2_config 的 TIER_STOP_PCT/TIER_HALF_PCT/DEPLOY_PCT/
# HALF_DEPLOY_PCT（口径同源：G2 调阈值/换数据源同步生效；测试注入环境变量同为
# G2_HS300_PCT）。T=2 时 DEPLOY_PCT(0.98) ≡ 1-RESERVE_CASH_PCT(2%)，行为零变化。
# 风险声明：ENS 网格寻优未含门控，纸面 ens 臂自此与实盘口径分叉（防御性偏离），
# 季评报告需带此注记（ens_alignment_design 差异二）。

# 持仓建仓日持久化（rebalance_ens 维护：{code: "YYYYMMDD"}）
HOLD_DATES_FILE = os.path.join(DATA_DIR, "rebalance_g2_ens", "g2_ens_hold_dates.json")

# ---- 重叠规避：G2 账本（融合换仓时跳过这些票，防同账户双桥争持仓） ----
G2_HOLD_DATES_FILE = os.path.join(DATA_DIR, "rebalance_g2", "g2_hold_dates.json")

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
        print("[g2_ens_config] 资金池 account_id 戳不匹配(旧=%s, 本策略=%s) → 已备份为 %s，"
              "按 %.0f 元空仓起步" % (old_stamp, ACCOUNT_ID, os.path.basename(bak), START_CAPITAL))
        return bak
    except Exception as e:
        print("[g2_ens_config] 资金池备份失败（人工核对）: %r" % (e,))
        return None


def load_ens_capital():
    """读融合独立资金池（account_id 戳校验；失败/不匹配回退 START_CAPITAL）。
    收益滚动、亏损不补：capital = 初始 + 已实现盈亏 + 策略持仓浮盈。"""
    try:
        with open(ENS_CAPITAL_FILE, encoding="utf-8") as f:
            d = json.load(f)
        stamp = str(d.get("account_id", ""))
        if stamp != ACCOUNT_ID:
            _backup_stale_capital(ENS_CAPITAL_FILE, stamp)
            return float(START_CAPITAL)
        cap = float(d.get("capital", 0) or 0)
        if cap > 0:
            return cap
    except Exception as e:
        print("[g2_ens_config] 资金池读取失败(%s)，回退 START_CAPITAL" % e)
    return float(START_CAPITAL)


def save_ens_capital(capital, note=""):
    """写融合独立资金池（原子写 + account_id 戳）。"""
    d = {
        "account_id": ACCOUNT_ID,
        "strategy": STRATEGY,
        "capital": round(float(capital), 2),
        "note": note,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = ENS_CAPITAL_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ENS_CAPITAL_FILE)
    return d


# ---- 低开校验参数（执行侧安全闸，与 G2/V1.4 同规则） ----
GAP_HARD_PCT = float(os.environ.get("GAP_HARD_PCT", "-5.0"))   # 极端低开阈值，直接跳过
GAP_OPEN_PCT = float(os.environ.get("GAP_OPEN_PCT", "-3.0"))   # 低开触发阈值
GAP_VR       = float(os.environ.get("GAP_VR", "3.3"))          # 低开放行量比（单位时间口径，T-20261023-007）


def today_str():
    return time.strftime("%Y%m%d")


def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")
