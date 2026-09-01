# coding: utf-8
"""G2（大QMT 文件桥）独立配置 —— 与 V1.3（miniQMT 67014907）**完全隔离**，绝不 import qmt_config.py。

- 账号：70180771（国金QMT模拟端大QMT），信号层留外部、执行走桥 D:/QMT_POOL/g2_bridge
- 资金池：独立文件 g2_strategy_capital.json（不读 V1.3 data/strategy_capital.json）
- 候选：data/selections/g2/<date>_g2_top2.csv（deploy_predict_g2 产出），不混 V1.3 D_model_top10
- 边界红线：只能动 G2 自己账本（positions_cfg/fills）的票，绝不纳管/卖出他人持仓
"""
import json
import os
import time

# ---- 账号与桥 ----
ACCOUNT_ID = "70180771"              # 大QMT 模拟端（跑 G2 桥）
STRATEGY = "Project_16_g2"
BRIDGE_DIR = "D:/QMT_POOL/g2_bridge"
CMD_DIR = os.path.join(BRIDGE_DIR, "cmd")
STATE_DIR = os.path.join(BRIDGE_DIR, "state")

# ---- 路径（外部信号层） ----
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
G2_SELECT_DIR = os.path.join(DATA_DIR, "selections", "g2")

# ---- 策略资金池（独立文件，与 V1.3 完全分离） ----
START_CAPITAL = 100000.0             # 初始 10 万
G2_CAPITAL_FILE = os.path.join(BRIDGE_DIR, "g2_strategy_capital.json")

# ---- 交易参数（对齐 V1.3 口径） ----
TOP_N = 2                            # 目标持仓数（等权）
RESERVE_CASH_PCT = 0.05              # 保留现金 5%（总仓 95%）
MIN_ORDER_VOL = 100                  # 整手
REDLINE = 60.0                       # g2 评分红线（deploy_predict_g2 --threshold 60 已过滤）

# ---- 飞书（沿用同一接收人，仅推送通道） ----
FEISHU_OPEN_ID = "ou_bd13444d8ea53c28249c669f43f3eeff"
LARK_CLI = r"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\1.0.4\bin\lark-cli.exe"


def load_g2_capital():
    """读 G2 独立资金池（account_id 戳校验；失败/不匹配回退 START_CAPITAL）。
    收益滚动、亏损不补：capital = 初始 + 已实现盈亏 + 策略持仓浮盈。"""
    try:
        with open(G2_CAPITAL_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if str(d.get("account_id", "")) != ACCOUNT_ID:
            raise ValueError("account_id 戳不匹配: %s" % d.get("account_id"))
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


def today_str():
    return time.strftime("%Y%m%d")


def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")
