# coding: utf-8
"""融合（ensemble）大QMT 文件桥 · 独立配置 —— 与 G2 桥（g2_config.py）完全隔离，绝不 import g2_config.py。

- 账号：70180771（与 G2 同券商账号，虚拟子账户独立账本；绝不纳管/卖出他人持仓）
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
ACCOUNT_ID = "70180771"              # 大QMT 模拟端（与 G2 同一券商账号，虚拟子账户）
STRATEGY = "Project_16_ens"
BRIDGE_DIR = "D:/QMT_POOL/p16_ensemble_bridge"
CMD_DIR = os.path.join(BRIDGE_DIR, "cmd")
STATE_DIR = os.path.join(BRIDGE_DIR, "state")

# ---- 路径（外部信号层） ----
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
ENS_SELECT_DIR = os.path.join(DATA_DIR, "selections", "g2_ens")

# ---- 策略资金池（独立文件，与 G2/V1.3 完全分离） ----
START_CAPITAL = 100000.0             # 初始 10 万（2026-09-10 拍板）
ENS_CAPITAL_FILE = os.path.join(BRIDGE_DIR, "ens_strategy_capital.json")

# ---- 交易参数（对齐回测 融合-live_trail/N10/红线60/TOP2） ----
TOP_N = 2                            # 目标持仓数（等权，对齐回测 TOP）
SELECT_TOP = 10                      # 候选池大小（对齐回测 TOP10；deploy_predict_g2 --ensemble --top 10 产出）
RESERVE_CASH_PCT = 0.05              # 保留现金 5%（总仓 95%）
MIN_ORDER_VOL = 100                  # 整手
REDLINE = 60.0                       # 评分红线（deploy_predict_g2 --threshold 60 已过滤）
HOLD_DAYS = 10                       # 持有期（交易日，对齐回测 N=10：满 N 个交易日到期卖出，止损/止盈优先）

# 持仓建仓日持久化（rebalance_ens 维护：{code: "YYYYMMDD"}）
HOLD_DATES_FILE = os.path.join(DATA_DIR, "rebalance_g2_ens", "g2_ens_hold_dates.json")

# ---- 重叠规避：G2 账本（融合换仓时跳过这些票，防同账户双桥争持仓） ----
G2_HOLD_DATES_FILE = os.path.join(DATA_DIR, "rebalance_g2", "g2_hold_dates.json")

# ---- 飞书（沿用同一接收人，仅推送通道） ----
FEISHU_OPEN_ID = "ou_bd13444d8ea53c28249c669f43f3eeff"
LARK_CLI = r"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\1.0.5\bin\lark-cli.exe"


def load_ens_capital():
    """读融合独立资金池（account_id 戳校验；失败/不匹配回退 START_CAPITAL）。
    收益滚动、亏损不补：capital = 初始 + 已实现盈亏 + 策略持仓浮盈。"""
    try:
        with open(ENS_CAPITAL_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if str(d.get("account_id", "")) != ACCOUNT_ID:
            raise ValueError("account_id 戳不匹配: %s" % d.get("account_id"))
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
GAP_VR       = float(os.environ.get("GAP_VR", "2.0"))          # 低开放行量比


def today_str():
    return time.strftime("%Y%m%d")


def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")
