# coding: utf-8
"""含真实交易成本的轮动回测 —— 真实评分卡版（F2/F5 换真实数据）。

本文件是 scan_rotate_cost.py 的副本，唯一区别：逐日拼分改用真实版评分卡
（scorecard_real.compute_real_scorecard）：
  - F2 = RF.score_f2(main_net, volume_ratio)   真实主力净额 + 量比（替代量比代理）
  - F5 = RF.score_f5(industry_pct)             真实行业当日涨幅（替代相对动量代理）
  - F6 = RF.score_f6(pe_ttm, turnover_rate)    与原版 F6_new 一致
  - F1/F3/F4 沿用 DP.compute_scorecard 代理分
其余（成本/滑点/可执行口径/前100池/红线/top2/止损止盈）与原版完全一致。

用法（用环境变量指定 v3_sc 面板 + v3_enh 模型）：
  $env:BT_PANEL="data\feature_panel_v3_sc.parquet"
  $env:BT_MODEL="D:\QuantLab\models\lgb_model_v3_enh.txt"
  $env:BT_META="data\features_v3_enh.json"
  python scan_rotate_cost_real.py --exec
输出：data/real/scan_rotate_cost_real_report.md
"""
import argparse
import json
import os

import numpy as np
import pandas as pd
import lightgbm as lgb

import data_config as DC
import deploy_predict as DP
import qmt_config as C
import review_full as RF
import scorecard_real as SR
import position_filter as PF

HERE = DC.PROJECT_DIR
PANEL = os.path.join(DC.DATA_DIR, "feature_panel_v3_sc.parquet")
MODEL = DC.model_file("_v3_enh")
META = os.path.join(DC.DATA_DIR, "features_v3_enh.json")
OUT_MD = os.path.join(DC.DATA_DIR, "real", "scan_rotate_cost_real_report.md")
# 支持自定义面板/模型/输出（A/B 对比测试用），可用环境变量覆盖
PANEL = os.environ.get("BT_PANEL", PANEL)
MODEL = os.environ.get("BT_MODEL", MODEL)
META = os.environ.get("BT_META", META)
OUT_MD = os.environ.get("BT_OUT", OUT_MD)
# ---- 模型融合（BT_ENSEMBLE，2026-09-09）：rank 融合两模型概率 ----
# 用法示例：
#   $env:BT_PANEL="data\feature_panel_v3_enh2_n3_bt.parquet"
#   $env:BT_MODEL="D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260825_1964t.txt"
#   $env:BT_META="data\features_v3_g2_strong_real_20260825.json"
#   $env:BT_MODEL2="D:\QuantLab\models\lgb_model_v3_enh.txt"
#   $env:BT_META2="data\features_v3_enh.json"
#   $env:BT_ENSEMBLE="0.5"   # 融合分 = w*rank(模型1) + (1-w)*rank(模型2)
ENS_W = os.environ.get("BT_ENSEMBLE", "").strip()
MODEL2 = os.environ.get("BT_MODEL2", "")
META2 = os.environ.get("BT_META2", "")

THRESHOLD = float(os.environ.get("BT_THRESHOLD", "58.0"))  # 红线评分阈值，可用 BT_THRESHOLD 覆盖
PRE_POOL = 100
TOP10 = 10
TOP = int(os.environ.get("BT_TOP", "2"))  # 持仓只数，可用环境变量 BT_TOP 覆盖（默认 2）
# 位置过滤开关（T5，2026-09-08 落地；默认 off 保持官方报告数值不变）："" / "lite" / "full"
# 显式设置时 simulate 买入候选经 position_filter.apply_rules（与选股端/纸面/实盘同口径）。
BT_POSFILTER = os.environ.get("BT_POSFILTER", "")
STOP = float(os.environ.get("BT_STOP", "-0.07"))  # 止损，可用 BT_STOP 覆盖
TP = float(os.environ.get("BT_TP", "0.15"))  # 止盈，可用 BT_TP 覆盖
N_LIST = [int(x) for x in os.environ.get("BT_N_LIST", "1,3,5,10").split(",") if x.strip()]
SLIPS = [float(x) for x in os.environ.get("BT_SLIPS", "0.0,0.001,0.002").split(",") if x.strip()]  # 单边滑点
START = os.environ.get("BT_START", "2024-07-01")  # 测试区间起，可用 BT_START/BT_END 覆盖
END = os.environ.get("BT_END", "2026-08-14")
W = {"F1": 0.25, "F2": 0.20, "F3": 0.20, "F4": 0.15, "F5": 0.10, "F6": 0.10}

# 卖出侧可执行性过滤（审计 P1-3）：一字跌停卖不掉 + 停牌/退市不冻结市值
_DOWN_LIMIT_MAP = {}  # (date, code) -> 当日跌停价，build_per_day 填充
DELIST_DAYS = int(os.environ.get("BT_DELIST_DAYS", "60"))   # 连续无价天数视为退市
DELIST_LOSS = float(os.environ.get("BT_DELIST_LOSS", "0.5"))  # 退市损失假设（按最后估值×(1-loss) 清仓）
SELL_SKIP_DOWN = [0]  # 卖出被一字跌停挡住的次数（审计量级统计）
SELL_DELIST = [0]     # 退市强制清仓次数

# ---------- 出场规则开关（追盈止损 ablation 用，EXIT_MODE=fixed 时完全等同原行为）----------
# fixed      : 成本价 -7% 止损 / +15% 止盈 / N 日期满（回测原口径）
# none       : 只保留 N 日期满（纯 alpha 上界，用于衡量止损规则到底是帮忙还是帮倒忙）
# live_trail : 实盘真跑口径 = -7% 硬止损 + 峰值回撤 8% 移动止盈 + +15% 止盈（回测从未验证过，关键对照）
# atr        : 自适应 = 成本 - k1×ATR%(建仓日) 硬止损 + 峰值 - k2×ATR% 移动止盈，无百分比止盈
# time       : N 日期满 + 时间止损（持有过半仍不盈利则换股，释放资金占用）
EXIT_MODE = os.environ.get("BT_EXIT", "fixed")
ATR_K1 = float(os.environ.get("BT_ATR_K1", "2.0"))     # 初始硬止损 ATR 倍数
ATR_K2 = float(os.environ.get("BT_ATR_K2", "2.5"))     # 移动止盈 ATR 倍数
TRAIL_PCT = float(os.environ.get("BT_TRAIL", "0.08"))  # 固定比例移动止盈回撤阈值（live_trail 用）
# 追盈激活阈值（2026-09-07 语义修复，T-20260907-002）：峰值须 ≥ 成本×(1+激活) 才开始追踪，
# 否则"高点仅微盈即回撤8%"会在亏损位以"追盈"名义卖出（追盈=追跌）。触发线同时受保本底线保护：
# line = max(成本, peak×(1-TRAIL_PCT))，激活后永不亏损出局。
TRAIL_ACTIVATE_PCT = float(os.environ.get("BT_TRAIL_ACTIVATE", "0.08"))
# 追盈优先开关（2026-09-08 止盈线敏感性评估）：=1 时激活追盈后固定止盈让位（STOP→TRAIL→TP），
# 强势票不再被 +15% 固定止盈提前掐断（601999 案例）；=0 保持原顺序（STOP→TP→TRAIL，默认行为不变）
TRAIL_FIRST = os.environ.get("BT_TRAIL_FIRST", "0") == "1"
EXIT_REASON_CNT = {}  # 出场原因计数，诊断各模式实际靠哪条规则在卖
_ATR_MAP = {}    # (date, code) -> ATR14 / close（建仓日快照的波动率百分比）
_HIGH_MAP = {}   # (date, code) -> 当日最高价（移动止盈峰值追踪用）
_MA5_MAP = {}    # (date, code) -> 5 日均线（ma 模式用）

# PK_OUT 开关（T-20260904-001 ablation 用，默认关闭以保持原行为）
# 实盘 V1.x（V1.0 起，git 2634f5d 已存在）的卖出条件比回测多一条「掉出当日 top2 即卖（PK_OUT）」，属 train-serving skew，
# 非 V1.3 引入（VERSIONS.md 登记 V1.3 仅模型换版，卖出规则未动）。
# 开启后回测复刻该规则：持仓 code 不在当日「过红线 + 可执行」的 top TOP 集合中即卖出（最长不超过 N 天）。
# 忠实复刻实盘 rebalance_daily.py 的 `if not target: return` 行为：当日无候选时 PK_OUT 不触发（不清仓）。
PK_OUT = os.environ.get("BT_PKOUT", "0") == "1"


def _executable(s):
    """次日买入可执行性：一字涨停 / 停牌 / 无量 均买不进（买入侧过滤）。

    原定义在买入循环内部，PK_OUT 需要在卖出侧复用同一口径判定「当日目标持仓集合」，
    故提到模块级，保证买入与 PK_OUT 判定用的是同一套可执行性标准。
    """
    if s["vol_next"] is None or (isinstance(s["vol_next"], float) and np.isnan(s["vol_next"])):
        return False
    if s["vol_next"] <= 0:
        return False
    if s["suspend_next"] is not None and not (isinstance(s["suspend_next"], float) and np.isnan(s["suspend_next"])):
        return False
    if s["up_limit_next"] is not None and s["open_next"] is not None \
            and not (isinstance(s["up_limit_next"], float) and np.isnan(s["up_limit_next"])) \
            and not (isinstance(s["open_next"], float) and np.isnan(s["open_next"])) \
            and s["open_next"] >= s["up_limit_next"]:
        return False
    return True

COMM_RATE, STAMP_RATE, TRANS_RATE = C.COMM_RATE, C.STAMP_RATE, C.TRANS_RATE  # 统一取 qmt_config（实盘口径）


def is_sh(code):
    return code.startswith("6")


def sell_fee(amt, code, slip):
    comm = max(C.COMM_MIN, amt * COMM_RATE)
    return comm + amt * STAMP_RATE + (amt * TRANS_RATE if is_sh(code) else 0.0) + amt * slip


def buy_fee(amt, code, slip):
    comm = max(C.COMM_MIN, amt * COMM_RATE)
    return comm + (amt * TRANS_RATE if is_sh(code) else 0.0) + amt * slip


def build_per_day():
    print("[1/3] 加载 + 逐日打分（真实版 F2/F5） ...")
    panel = pd.read_parquet(PANEL)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    meta = json.load(open(META, encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    booster = lgb.Booster(model_file=MODEL)
    # 可执行口径需开盘价/涨跌停/停牌；一次取全量并按 ts_code shift(-1) 得到 T+1 快照
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["open", "high", "low", "close", "up_limit", "down_limit",
                                                    "vol", "suspend_timing", "pe_ttm", "turnover_rate"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    # 只在模型 universe 内计算 ATR/MA5/最高价：全市场 5000+ 只 × 全历史建 dict 会爆内存，
    # 而持仓只可能来自 panel（per_day 由 panel 派生），按 ts_code + 日期窗口收窄是安全的。
    _uni = set(panel["ts_code"].astype(str).unique())
    daily = daily[daily["ts_code"].isin(_uni)]
    # 日期窗口：测试期起点前 45 天足够 ATR14/MA5 预热，再早的行情回测用不到
    _d0 = pd.Timestamp(START) - pd.Timedelta(days=45)
    daily = daily[daily["trade_date"] >= _d0]
    daily = daily.sort_values(["ts_code", "trade_date"])
    # ATR14（占收盘价百分比）与 MA5：供 atr / ma 出场模式使用（Wilder 1978；Chandelier Exit 同族）
    _pc = daily.groupby("ts_code")["close"].shift(1)
    daily["_tr"] = pd.concat([
        daily["high"] - daily["low"],
        (daily["high"] - _pc).abs(),
        (daily["low"] - _pc).abs(),
    ], axis=1).max(axis=1)
    daily["_atr14"] = daily.groupby("ts_code")["_tr"].transform(
        lambda s: s.rolling(14, min_periods=7).mean())
    daily["_atr_pct"] = daily["_atr14"] / daily["close"]
    daily["_ma5"] = daily.groupby("ts_code")["close"].transform(
        lambda s: s.rolling(5, min_periods=3).mean())
    g = daily.groupby("ts_code")
    nxt = pd.DataFrame({
        "open_next": g["open"].shift(-1),
        "up_limit_next": g["up_limit"].shift(-1),
        "vol_next": g["vol"].shift(-1),
        "suspend_next": g["suspend_timing"].shift(-1),
    }, index=daily.index)
    daily = pd.concat([daily, nxt], axis=1)
    daily = daily.set_index(["trade_date", "ts_code"])
    open_map = daily["open"].to_dict()  # 全市场 open，(date, code) -> open，供持仓收益取价
    global _DOWN_LIMIT_MAP, _ATR_MAP, _HIGH_MAP, _MA5_MAP
    _DOWN_LIMIT_MAP = daily["down_limit"].to_dict()  # 全市场当日跌停价，(date, code) -> down_limit，卖出侧一字跌停过滤用
    _ATR_MAP = daily["_atr_pct"].to_dict()   # (date, code) -> ATR14/close，自适应出场用
    _HIGH_MAP = daily["high"].to_dict()      # (date, code) -> 当日最高价，移动止盈峰值追踪用
    _MA5_MAP = daily["_ma5"].to_dict()       # (date, code) -> 5 日均线，ma 出场模式用

    dates = sorted(panel.loc[(panel["trade_date"] >= START) & (panel["trade_date"] <= END), "trade_date"].unique())
    per_day, market_avgs = {}, []
    for d in dates:
        day = panel[panel["trade_date"] == d].copy()
        if len(day) < 20:
            continue
        day["prob"] = booster.predict(day[feat_cols].astype("float32").values)
        if ENS_W:
            # 融合：rank 分 = w*rank(模型1) + (1-w)*rank(模型2)，模型2 特征需在面板内
            _m2 = json.load(open(META2, encoding="utf-8"))
            _b2 = lgb.Booster(model_file=MODEL2)
            _p2 = _b2.predict(day[_m2["feature_cols"]].astype("float32").values)
            _w = float(ENS_W)
            day["prob"] = (_w * day["prob"].rank(pct=True)
                           + (1 - _w) * pd.Series(_p2, index=day.index).rank(pct=True))
        idx = pd.MultiIndex.from_arrays([day["trade_date"], day["ts_code"]])
        est = daily.reindex(idx)
        sc_real = SR.compute_real_scorecard(day, est)
        for k in ("F1", "F2", "F3", "F4", "F5"):
            day[k] = sc_real[k].values
        day["F6_new"] = sc_real["F6"].values
        day["total_new"] = sc_real["total_new_real"].values
        # T+1 快照（shift(-1) 已对齐）：用于可执行口径的一字板/停牌过滤与 open→open 收益
        day["open_next"] = est["open_next"].values
        day["up_limit_next"] = est["up_limit_next"].values
        day["vol_next"] = est["vol_next"].values
        day["suspend_next"] = est["suspend_next"].values
        market_avgs.append(float(day["fwd_ret"].mean()))
        pre = day.nlargest(PRE_POOL, "prob")[["ts_code", "total_new", "fwd_ret", "prob", "open_next",
                                              "up_limit_next", "vol_next", "suspend_next"]].set_index("ts_code")
        per_day[d] = pre.nlargest(TOP10, "prob")
    return dates, per_day, pd.Series(market_avgs), open_map


def simulate(dates, per_day, N, slip, open_map, exec_ok=True):
    """资金模拟：真实金额(初始10万)，满仓2只，换仓扣真实费用。返回 trades 与日收益序列。

    exec_ok=True  → 可执行口径（open→open + 一字板/停牌过滤，审计 R1 新基线）
    exec_ok=False → 原 close→close 口径（对比用，保留历史行为）
    """
    M = len(dates)
    cash = 100000.0  # 真实金额，佣金最低5元才正确生效
    SELL_SKIP_DOWN[0] = 0
    SELL_DELIST[0] = 0
    hold = {}      # code -> dict(value=市值, buy_val=买入成本市值, buy_i, invest=投入本金)
    trades, daily_ret = [], []
    prev_total = 100000.0
    n_skip = 0
    for i, d in enumerate(dates):
        row = per_day[d]
        # PK_OUT 用：当日目标持仓集合 = 过红线 + 可执行的 top TOP 只（与买入侧同口径）
        # 忠实复刻实盘 rebalance_daily.py 的 `if not target: return`：当日无候选时 PK_OUT 不触发、不清仓
        top_codes = set()
        if PK_OUT and exec_ok:
            _c = row[row["total_new"] >= THRESHOLD]
            if len(_c):
                _c = _c[_c.apply(_executable, axis=1)]
            if len(_c):
                top_codes = set(_c.nlargest(TOP, "total_new").index)
        if i > 0:
            for c in list(hold):
                h = hold[c]
                if exec_ok:
                    # 可执行口径：持仓按全市场 open 逐日盯市（buy_i 决策 → buy_i+1 开盘买入）
                    o_buy = open_map.get((dates[h["buy_i"] + 1], c)) if h["buy_i"] + 1 < len(dates) else None
                    o_cur = open_map.get((d, c))
                    if o_buy and o_cur and o_buy > 0:
                        h["value"] = h["invest"] * o_cur / o_buy
                else:
                    prev = per_day[dates[i - 1]]
                    r = float(prev.loc[c, "fwd_ret"]) if c in prev.index else 0.0
                    h["value"] *= (1 + r)
        # 卖出：止损/止盈/期满
        for c in list(hold):
            h = hold[c]
            if exec_ok:
                o_buy = open_map.get((dates[h["buy_i"] + 1], c)) if h["buy_i"] + 1 < len(dates) else None
                o_cur = open_map.get((d, c))
                if not (o_buy and o_cur and o_buy > 0):
                    # 取不到价（停牌/退市）：保持最后估值不冻结；连续 DELIST_DAYS 天无价按退市损失假设清仓
                    h["missing_days"] = h.get("missing_days", 0) + 1
                    if h["missing_days"] >= DELIST_DAYS:
                        amt = h["value"]
                        cash += amt * (1 - DELIST_LOSS) - sell_fee(amt, c, slip)
                        trades.append((h["buy_i"], i, amt * (1 - DELIST_LOSS) / h["buy_val"] - 1))
                        del hold[c]
                        SELL_DELIST[0] += 1
                    continue
                h["missing_days"] = 0
                # 峰值追踪：先用「截至昨日」的峰值判定（今日 high 尚未走完，不能拿未来价触发），再并入今日 high
                # T+1 卫生（2026-09-07 修复，T-20260907-002）：买入当日(i-buy_i=1) T+1 锁不可卖，
                # 不并入当日 high（避免买入日高点污染峰值，导致次日以"追盈"名义亏损离场）
                hi = _HIGH_MAP.get((d, c))
                peak = h.get("peak")
                if not peak or peak <= 0:
                    peak = o_buy
                if (i - h["buy_i"]) >= 2 and hi and hi > 0 and not (isinstance(hi, float) and np.isnan(hi)):
                    h["peak"] = max(peak, hi)
                ret = o_cur / o_buy - 1
                # 一字跌停（open <= down_limit）卖不掉：推迟到下个交易日再评估，不按开盘价成交（审计 P1-3）
                down = _DOWN_LIMIT_MAP.get((d, c))
                one_word_down = down is not None and not (isinstance(down, float) and np.isnan(down)) \
                    and o_cur <= down + 1e-9
                if one_word_down:
                    SELL_SKIP_DOWN[0] += 1
                    continue
                # PK_OUT：掉出当日 top TOP 即卖（实盘 V1.x 自 V1.0 就存在的第四条出场规则，回测原本没有）
                # T+1 约束：买入在 buy_i+1 开盘成交，最早 buy_i+2 开盘才能卖（(i - buy_i) >= 2）。
                # 不约束会出现「同一时刻买入并卖出」的零收益交易（均持有 0.23 日、胜率 12.6% 即此假象），
                # 白白刷掉卖出手续费与滑点，使 PK_OUT 的损害被高估。
                pk_out = bool(top_codes) and (c not in top_codes) and (i - h["buy_i"]) >= 2
                sell, reason = False, ""
                if (i - h["buy_i"]) >= N + 1:          # 持有期满（所有模式共有的出口）
                    sell, reason = True, "MATURE"
                elif (i - h["buy_i"]) < 2:             # T+1：买入当日与次日开盘前不可卖
                    sell, reason = False, ""
                elif EXIT_MODE == "none":              # 纯 alpha 上界：只靠期满
                    sell = False
                elif EXIT_MODE == "time":              # 时间止损：过半程仍不盈利即换股
                    if (i - h["buy_i"]) >= max(2, int(round(N / 2.0))) and ret <= 0:
                        sell, reason = True, "TIME"
                elif EXIT_MODE == "atr":               # 波动率自适应（ATR 占建仓日收盘价百分比）
                    apct = _ATR_MAP.get((dates[h["buy_i"] + 1], c))
                    if apct is None or not np.isfinite(apct) or apct <= 0:
                        apct = 0.035  # ATR 缺失兜底：小盘股日均真实波幅经验值约 3.5%
                    if ret <= -ATR_K1 * apct:
                        sell, reason = True, "ATR_STOP"
                    elif peak > o_buy and o_cur <= peak * (1 - ATR_K2 * apct):
                        sell, reason = True, "ATR_TRAIL"
                elif EXIT_MODE == "ma":                # 跌破 5 日均线（用昨收 MA5，开盘即可判定）
                    ma5 = _MA5_MAP.get((dates[i - 1], c))
                    if ma5 and np.isfinite(ma5) and o_cur < ma5:
                        sell, reason = True, "MA5_BREAK"
                    elif ret <= STOP:
                        sell, reason = True, "STOP"
                elif EXIT_MODE == "live_trail":        # 实盘真跑口径：-7% 硬止损 + 8% 移动止盈 + 15% 止盈
                    # 追盈激活阈值 + 保本底线（2026-09-07 修复，T-20260907-002）：
                    # 峰值须 ≥ 成本×(1+TRAIL_ACTIVATE_PCT) 才追踪；触发线 = max(成本, peak×(1-TRAIL_PCT))，
                    # 避免"高点仅微盈即回撤8%"在亏损位以追盈名义卖出（现语义 64% 追盈为亏损单）
                    trail_act = peak >= o_buy * (1 + TRAIL_ACTIVATE_PCT)
                    trail_hit = trail_act and o_cur <= max(o_buy, peak * (1 - TRAIL_PCT))
                    if ret <= STOP:
                        sell, reason = True, "STOP"
                    elif TRAIL_FIRST:
                        # 追盈优先（T-20260908 评估）：已激活→只跟移动线（TP 让位，未回撤持有）；
                        # 未激活→固定止盈兜底
                        if trail_hit:
                            sell, reason = True, "TRAIL"
                        elif (not trail_act) and ret >= TP:
                            sell, reason = True, "TP"
                    elif ret >= TP:
                        sell, reason = True, "TP"
                    elif trail_hit:
                        sell, reason = True, "TRAIL"
                else:                                  # fixed：回测原口径
                    if ret <= STOP or ret >= TP:
                        sell, reason = True, "STOP" if ret <= STOP else "TP"
                if pk_out:
                    sell, reason = True, "PK_OUT"
                if sell:
                    EXIT_REASON_CNT[reason] = EXIT_REASON_CNT.get(reason, 0) + 1
                    amt = h["value"]
                    cash += amt - sell_fee(amt, c, slip)
                    trades.append((h["buy_i"], i, h["value"] / h["buy_val"] - 1))
                    del hold[c]
            else:
                if h["value"] / h["buy_val"] - 1 <= STOP or h["value"] / h["buy_val"] - 1 >= TP or (i - h["buy_i"]) >= N:
                    amt = h["value"]
                    cash += amt - sell_fee(amt, c, slip)
                    trades.append((h["buy_i"], i, h["value"] / h["buy_val"] - 1))
                    del hold[c]
        # 买入：补足到 TOP
        while len(hold) < TOP:
            cand = row[~row.index.isin(hold)]
            cand = cand[cand["total_new"] >= THRESHOLD]
            # 位置过滤（T5）：显式设置 BT_POSFILTER=lite/full 时启用，默认 off 保持官方口径
            if BT_POSFILTER and len(cand) > 0:
                cand = PF.apply_rules(cand.reset_index(), target_date=dates[i], mode=BT_POSFILTER)
                if len(cand) == 0:
                    break
                cand = cand.set_index("ts_code")
            if len(cand) == 0:
                break
            # 可执行口径：一字涨停/停牌买不进 → 剔除
            if exec_ok:
                n_before = len(cand)
                cand = cand[cand.apply(_executable, axis=1)]
                n_skip += (n_before - len(cand))
                if len(cand) == 0:
                    break
            best = cand.nlargest(1, "total_new").index[0]
            n = TOP - len(hold)
            budget = cash * 0.95 / n
            fee = buy_fee(budget, best, slip)
            invest = budget - fee
            if invest <= 0:
                break
            cash -= budget
            hold[best] = {"value": invest, "buy_val": invest, "buy_i": i, "invest": invest}
        # 轮动：持仓未满2 且 len>0，新 top 超最弱 X 分则换（此处固定不设 X，X=None 即不做）
        total_now = cash + sum(h["value"] for h in hold.values())
        daily_ret.append(total_now / prev_total - 1 if prev_total > 0 else 0.0)
        prev_total = total_now
    # 期末清仓（不计费，仅补 trades）
    for c, h in hold.items():
        trades.append((h["buy_i"], M, h["value"] / h["buy_val"] - 1))
    return trades, pd.Series(daily_ret), n_skip


def stats(trades, daily_ret, market_daily):
    rets = [t[2] for t in trades]
    fwd = pd.Series(rets)
    win = float((fwd > 0).mean()) if len(fwd) else np.nan
    aw = float(fwd[fwd > 0].mean()) if (fwd > 0).any() else np.nan
    al = float(fwd[fwd < 0].mean()) if (fwd < 0).any() else np.nan
    pr = float(aw / abs(al)) if al and al != 0 else np.nan
    nav = (1 + daily_ret).cumprod()
    mdd = float((nav / nav.cummax() - 1).min())
    excess = float(daily_ret.mean() - market_daily.mean())
    return {"n_trades": len(trades), "win_rate": win, "profit_loss_ratio": pr,
            "max_drawdown": mdd, "daily_excess": excess}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--naive", action="store_true", help="仅跑原 close→close 口径（对比用）")
    ap.add_argument("--exec", action="store_true", help="仅跑可执行 open→open 口径")
    args = ap.parse_args()
    # 默认双口径都跑；--exec / --naive 只跑指定的一种
    do_exec = not args.naive
    do_naive = not args.exec

    dates, per_day, market_daily, open_map = build_per_day()
    print(f"    测试期 {dates[0].date()} ~ {dates[-1].date()} | {len(dates)} 日 | 持仓{TOP} | 止损{STOP:.0%}/止盈{TP:.0%}")
    print("[2/3] 含成本模拟（持有期 × 滑点 × 口径）...")
    rows = []
    for N in N_LIST:
        for slip in SLIPS:
            if do_naive:
                trades, daily_ret, _ = simulate(dates, per_day, N, slip, open_map, exec_ok=False)
                s = stats(trades, daily_ret, market_daily)
                rows.append({"N": N, "slip": slip, "口径": "close→close(原)", **s})
                print(f"    N={N} 滑点{slip:.1%} [原口径]: 超额{s['daily_excess']:.3%} 胜率{s['win_rate']:.1%} "
                      f"盈亏比{s['profit_loss_ratio']:.2f} 回撤{s['max_drawdown']:.1%} 交易{s['n_trades']}")
            if do_exec:
                EXIT_REASON_CNT.clear()
                trades, daily_ret, n_skip = simulate(dates, per_day, N, slip, open_map, exec_ok=True)
                s = stats(trades, daily_ret, market_daily)
                rows.append({"N": N, "slip": slip, "口径": "open→open(可执行)", **s})
                print(f"    N={N} 滑点{slip:.1%} [可执行]: 超额{s['daily_excess']:.3%} 胜率{s['win_rate']:.1%} "
                      f"盈亏比{s['profit_loss_ratio']:.2f} 回撤{s['max_drawdown']:.1%} 交易{s['n_trades']} 跳过{n_skip}")
                reasons = " ".join("%s=%d" % (k, v) for k, v in sorted(EXIT_REASON_CNT.items()))
                if reasons:
                    print(f"      出场原因: {reasons}")
                if SELL_SKIP_DOWN[0] or SELL_DELIST[0]:
                    print(f"      卖出跌停跳过 {SELL_SKIP_DOWN[0]} 次 / 退市清仓 {SELL_DELIST[0]} 次")
    res = pd.DataFrame(rows)
    print("[3/3] 保存报告 ...")
    lines = [
        "# 含真实交易成本的轮动回测 —— 真实评分卡版（F2/F5 真实数据）",
        "",
        f"> 测试期 {START} ~ {END} | 前10池+红线{THRESHOLD}+top{TOP} | 成本：佣金万2(双边,最低5元) + 印花税万5(卖出) + 过户费万0.1(沪市) | 滑点敏感性 0/0.1%/0.2%",
        "",
        "> **评分差异**：F2 = RF.score_f2(真实主力净额, 量比)、F5 = RF.score_f5(真实行业当日涨幅)、F6 = RF.score_f6(PE,换手)；F1/F3/F4 沿用 DP 代理分。",
        "",
        "| 口径 | 持有期 | 滑点/边 | 交易数 | 胜率 | 盈亏比 | 最大回撤 | 日均超额 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in res.to_dict("records"):
        lines.append(
            f"| {r['口径']} | {r['N']}天 | {r['slip']:.1%} | {r['n_trades']} | {r['win_rate']:.1%} "
            f"| {r['profit_loss_ratio']:.2f} | {r['max_drawdown']:.1%} | {r['daily_excess']:.3%} |"
        )
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("    报告:", OUT_MD)


if __name__ == "__main__":
    main()
