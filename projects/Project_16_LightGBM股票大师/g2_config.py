# coding: utf-8
"""G2（大QMT 文件桥）独立配置 —— 与 V1.3（miniQMT 67014907）**完全隔离**，绝不 import qmt_config.py。

- 账号：70180771（国金QMT模拟端大QMT），信号层留外部、执行走桥 D:/QMT_POOL/g2_bridge
- 资金池：独立文件 g2_strategy_capital.json（不读 V1.3 data/strategy_capital.json）
- 候选：data/selections/g2/<date>_g2_top10.csv（deploy_predict_g2 --top 10 产出，对齐回测 TOP10），不混 V1.3 D_model_top10
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
TOP_N = 2                            # 目标持仓数（等权，对齐回测 TOP）
SELECT_TOP = 10                      # 候选池大小（对齐回测 TOP10；deploy_predict_g2 --top 10 产出 _g2_top10.csv）
RESERVE_CASH_PCT = 0.05              # 保留现金 5%（总仓 95%）
MIN_ORDER_VOL = 100                  # 整手
REDLINE = 60.0                       # g2 评分红线（deploy_predict_g2 --threshold 60 已过滤）
HOLD_DAYS = 15                       # 持有期（交易日，2026-09-10 拍板升级 N15-live_trail：对齐网格最优 G2-live_trail/N15/红线60/TOP2 +0.213%；桥出场=STOP→TP→TRAIL 与回测 live_trail 一致）

# 持仓建仓日持久化（rebalance_g2 维护：{code: "YYYYMMDD"}）
HOLD_DATES_FILE = os.path.join(DATA_DIR, "rebalance_g2", "g2_hold_dates.json")

# 融合桥 ENS 账本（G2 反向互斥用：G2 换仓跳过 ENS 已持有的票，防同账户双桥争同一持仓，2026-09-13 补对称化）
ENS_HOLD_DATES_FILE = os.path.join(DATA_DIR, "rebalance_g2_ens", "g2_ens_hold_dates.json")

# ---- 飞书（沿用同一接收人，仅推送通道） ----
FEISHU_OPEN_ID = "ou_bd13444d8ea53c28249c669f43f3eeff"
LARK_CLI = r"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\1.0.5\bin\lark-cli.exe"


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


# ---- 位置过滤与低开校验参数（G2-V1.0，2026-09-08 落地，可环境变量覆盖） ----
# 【重要修正 2026-09-08】G2 模型(g2_strong_real)口径回测验证：位置过滤（full/lite）均无增益甚至有害
# （红线60/N=10/0.1%：Base +0.149% → full -0.108%），故 G2 默认【不过滤】；仅显式 PF_MODE_G2 才启用。
# V1.3 链路(v3_enh)的 lite 过滤单独保留（58-lite +0.157% 验证有效，见 qmt_config.POSFILTER_MODE）。
POSFILTER_MODE = os.environ.get("PF_MODE_G2", "")       # G2 默认不过滤（""=关）；lite/full 需显式设置
GAP_HARD_PCT = float(os.environ.get("GAP_HARD_PCT", "-5.0"))   # 极端低开阈值，直接跳过
GAP_OPEN_PCT = float(os.environ.get("GAP_OPEN_PCT", "-3.0"))   # 低开触发阈值
GAP_VR       = float(os.environ.get("GAP_VR", "2.0"))          # 低开放行量比

# ---- 大盘门控（TIER_RULES，2026-09-11 补上，对齐 V1.3 口径 T-20260904-004）----
# 沪深300 当日涨跌幅（%）→ T 档：
#   T=0：<= TIER_STOP_PCT (-1.5)  → 停买（只卖不买，空位不补）
#   T=1：<= TIER_HALF_PCT (-1.0)  → 半仓（买入预算 = 资金池 × HALF_DEPLOY_PCT）
#   T=2：其余                     → 满仓（买入预算 = 资金池 × DEPLOY_PCT）
# 数据缺失（取不到沪深300）→ fail-safe 按 T=1 半仓 + 醒目告警（刹车数据缺失时降速不裸奔）。
TIER_STOP_PCT = float(os.environ.get("G2_TIER_STOP", "-1.5"))   # 停买线
TIER_HALF_PCT = float(os.environ.get("G2_TIER_HALF", "-1.0"))   # 半仓线
DEPLOY_PCT     = 0.95                                            # 满仓部署比例（保留 5% 现金）
HALF_DEPLOY_PCT = 0.50                                           # 半仓部署比例
HS300_QT_SYMBOL = "sh000300"                                     # 腾讯行情沪深300 代码


def today_str():
    return time.strftime("%Y%m%d")


def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")
