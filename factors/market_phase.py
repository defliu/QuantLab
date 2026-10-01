# coding: utf-8
"""市场情绪周期 6 阶段(涨停梯队法) — 市场级 regime 模块。

移植自 TSP tick-stock-panel `services/market_phase.py` + `services/regime_builder.py`
(shy3130, MIT)。规则结构/EMA 平滑/2 日确认/否决机制与 TSP 原版一致; 阈值经
astock 数据重标定(2026-09-26, 见下方常量块注释), 5 档 state 权重保持 TSP 原值。
数据输入为 D:/astock 日频 parquet。

两个输出层:
  1) 5 档 state(strong/lean_strong/range/lean_weak/weak): 4 维综合分(赚钱/投机/抗跌/趋势),
     日频打分, 切换频繁 — 供否决与横向参考;
  2) 6 阶段 phase(ice/ignite/rally/climax/ebb/repair): 基于连板梯队驱动量,
     EMA 平滑 + 2 日确认, 段长 ~10 天 — 供环境判断。

驱动量(全部由 per-stock consecutive_limit_ups 派生):
  height 高度 / first_board 首板数 / ge2 宽度 / promo_rate 晋级率 / seal_rate 封板率

PIT 安全: 全部输入为 T 日收盘数据, T 日标签供 T+1 决策使用, 无前视
(rolling MA20 / streak 均只向后看; 涨停价用 tushare up_limit, 不用未来复权)。

与 TSP 的已知差异:
  - 数据源: astock(tushare up_limit 精确涨停价) vs TSP TickFlow;
  - universe: astock 全部 SH/SZ/BJ 股票 vs TSP 全量标的;
  - index_pct: HS300 ETF 日涨幅 vs TSP 指数行情, 缺失日记 0(中性);
  - promo 分布系统性偏高(astock p10=0.154 vs TSP p20=0.15) → 阈值已重标定;
  - streak 跨停牌不断链(无行即延续), 妖股停牌核查后连板计数延续。

定位声明: 本模块是描述性 regime 标签(研究侧资产), 不是 P17 已证伪的
「情绪温度计反转择时」假设翻案; 消费时必须遵守 T 日标签 T+1 可用。
"""
import numpy as np
import pandas as pd

# ───────────────────────── 阶段词汇 ─────────────────────────
PHASE_ICE = "ice"
PHASE_IGNITE = "ignite"
PHASE_RALLY = "rally"
PHASE_CLIMAX = "climax"
PHASE_EBB = "ebb"
PHASE_REPAIR = "repair"

PHASE_LABELS = {
    PHASE_ICE: "冰点",
    PHASE_IGNITE: "启动",
    PHASE_RALLY: "主升",
    PHASE_CLIMAX: "高潮",
    PHASE_EBB: "退潮",
    PHASE_REPAIR: "修复",
}

# ─────────────── 6 阶段阈值(astock 重标定, 2026-09-26 定稿) ───────────────
# 括号内为 TSP 原标定值(其 TickFlow 数据)。移植实证: TSP 原阈值在 astock 数据上
# 退潮/冰点永不触发(我们的 promo 分布系统性偏高: p10=0.154 vs TSP p20=0.15),
# 2024-01 微盘崩被误标 rally。以下按 TSP 同款分位数配方在 astock 2020-08-24~
# 2026-09-11(1468 交易日)重标定; 标定脚本: scripts/backfill_market_phase.py --calibrate
CLIMAX_GE2 = 62            # 2*p90(ge2)                     (TSP: 50)
CLIMAX_FIRST_BOARD = 218   # 2.5*p90(fb)                    (TSP: 220)
RALLY_HEIGHT = 8           # p60(height)                    (TSP: 7)
RALLY_GE2 = 19             # p60(ge2)                       (TSP: 15)
RALLY_PROMO = 0.270        # p60(promo)                     (TSP: 0.23)
RALLY_PROMO_ALT = 0.346    # p85(promo)                     (TSP: 0.30)
RALLY_GE2_ALT = 15         # 0.8*p60(ge2)                   (TSP: 12)
RALLY_HEIGHT_ALT = 6       # 5/7*p60(height)                (TSP: 5)
EBB_PROMO = 0.181          # p20(promo)                     (TSP: 0.15)
EBB_PROMO_STRICT = 0.157   # 0.867*p20(promo)               (TSP: 0.13)
EBB_SEAL = 0.565           # p10(seal_rate)                 (TSP: 0.57)
EBB_RECENT_GE2 = 15        # 0.48*p90(ge2), "自高位退潮"判据 (TSP: 12)
EBB_RECENT_HEIGHT = 7      # 0.857*p60(height)              (TSP: 6)
IGNITE_GE2_DELTA = 3       # ge2 较 5 日前增加量(绝对值, 不重标) (TSP: 3)
IGNITE_GE2 = 13            # p30(ge2)                       (TSP: 8)
IGNITE_PROMO = 0.257       # p55(promo)                     (TSP: 0.20)
IGNITE_HEIGHT_DELTA = 1    # height 较 5 日前抬升(绝对值)   (TSP: 1)
IGNITE_HEIGHT = 6          # p40(height)                    (TSP: 5)
IGNITE_PROMO_SOFT = 0.246  # p50(promo)                     (TSP: 0.19)
ICE_HEIGHT = 4             # p10(height)                    (TSP: 4)
ICE_GE2 = 8                # p10(ge2)                       (TSP: 6)
ICE_FIRST_BOARD = 26       # p10(fb)                        (TSP: 24)

PROMO_MIN_POOL = 10        # 晋级率最小池(家数), 低于记 NaN
_EMA_ALPHA = 1.0 / 3.0     # ≈5 日平滑
_CONFIRM_DAYS = 2          # 阶段切换需连续 N 日同标签
_POSITIVE_PHASES = frozenset({PHASE_CLIMAX, PHASE_RALLY, PHASE_IGNITE})
_VETO_STATES = frozenset({"weak", "lean_weak"})

# ─────────────── 5 档 state 权重与阈值(TSP regime_builder 原值) ───────────────
STATE_WEIGHTS = {
    "profit": 0.35,       # 赚钱(涨家数/均涨幅/中位涨幅/强弱差)
    "speculation": 0.25,  # 投机(涨停数/封板率/连板高度)
    "resilience": 0.20,   # 抗跌(大跌股占比)
    "trend": 0.20,        # 趋势(指数涨幅/MA20 上方占比)
}
STATE_STRONG = 70
STATE_LEAN_STRONG = 55
STATE_RANGE = 45
STATE_LEAN_WEAK = 30

PRICE_TOL = 0.005  # 涨停价判定容差(元)


def _score(value, low, high):
    """TSP 同款归一化: value 在 [low, high] 线性映射到 [0, 100], 钳制边界。"""
    if high <= low:
        return 50.0
    v = float(value) if value is not None and not (isinstance(value, float) and np.isnan(value)) else 0.0
    return float(max(0.0, min(100.0, (v - low) / (high - low) * 100.0)))


def add_limit_flags(df):
    """给日频行情加涨停标志与连板数(保留输入的全部其余列, 免去事后 merge)。

    参数 df: DataFrame, 必含列 [trade_date, ts_code, high, close, up_limit]
      (不复权价 + tushare 涨停价), 其余列原样保留;
      up_limit/close/high 为空的行(无涨停约束/异常)剔除。
    返回: 按 (ts_code, trade_date) 排序, 追加列
      limit_up(bool) / touched(bool) / consecutive_limit_ups(int) / _prev_consec(float, NaN=该股首行)
    """
    for col in ("trade_date", "ts_code", "high", "close", "up_limit"):
        if col not in df.columns:
            raise ValueError("add_limit_flags 缺少列: %s" % col)
    out = df.loc[(df["up_limit"].notna() & df["close"].notna() & df["high"].notna())].copy()
    out["limit_up"] = (out["close"] - out["up_limit"]).abs() <= PRICE_TOL
    out["touched"] = out["high"] >= out["up_limit"] - PRICE_TOL
    out = out.sort_values(["ts_code", "trade_date"], kind="mergesort").reset_index(drop=True)

    v = out["limit_up"].to_numpy()
    n = len(v)
    idx = np.arange(n)
    code_arr = out["ts_code"].to_numpy()
    code_change = np.empty(n, dtype=bool)
    code_change[0] = True
    code_change[1:] = code_arr[1:] != code_arr[:-1]
    # run 边界: 组边界或涨停标志翻转; run 内位置+1 即连板数
    change = code_change.copy()
    change[1:] |= v[1:] != v[:-1]
    run_start = np.maximum.accumulate(np.where(change, idx, -1))
    consec = np.where(v, idx - run_start + 1, 0).astype(np.int32)
    out["consecutive_limit_ups"] = consec
    # _prev_consec: 昨日连板数 — 先整体右移, 再把每只票首行(含全局首行)置 NaN 防串票
    prev = np.roll(consec.astype(np.float64), 1)
    prev[0] = np.nan
    prev[code_change] = np.nan
    out["_prev_consec"] = prev
    return out


def build_daily_metrics(flagged, index_pct=None):
    """按交易日聚合梯队指标 + 5 档 state 输入(全向量化)。

    参数:
      flagged: add_limit_flags 的输出, 需追加列 change_pct(小数, 用 pre_close 计算)
        与 above_ma20(bool, 后复权价 close*adj_factor 对其自身 MA20)
      index_pct: {trade_date: 指数日涨幅(小数)}, 可选, 缺失日按 0(中性)
    返回: DataFrame(index=trade_date), 列
      height/first_board/ge2/ge3/ge5/ladder_completeness/promo_pool/promo_rate/
      seal_rate/limit_up/up_pct/avg_pct/median_pct/strong_diff_pct/
      strong_down_pct/above_ma20_pct/index_pct
    """
    required = {"change_pct", "above_ma20"}
    missing = required - set(flagged.columns)
    if missing:
        raise ValueError("flagged 缺少列: %s (change_pct 用 pre_close 算, above_ma20 用后复权价算)" % sorted(missing))
    df = flagged
    consec = df["consecutive_limit_ups"]
    df = df.assign(
        _first=(consec == 1),
        _ge2=(consec >= 2),
        _ge3=(consec >= 3),
        _ge5=(consec >= 5),
        _consec_ge2=np.where(consec >= 2, consec, np.nan),
        _pool=(df["_prev_consec"] >= 1),
        _ok=((df["_prev_consec"] >= 1) & (consec == df["_prev_consec"] + 1)),
        _broken=(df["touched"] & ~df["limit_up"]),
        _up=(df["change_pct"] > 0),
        _sup=(df["change_pct"] >= 0.03),
        _sdown=(df["change_pct"] <= -0.03),
    )
    g = df.groupby("trade_date", sort=True)
    agg = pd.DataFrame({
        "height": g["consecutive_limit_ups"].max(),
        "first_board": g["_first"].sum(),
        "ge2": g["_ge2"].sum(),
        "ge3": g["_ge3"].sum(),
        "ge5": g["_ge5"].sum(),
        "rungs_filled": g["_consec_ge2"].nunique(),
        "promo_pool": g["_pool"].sum(),
        "promo_ok": g["_ok"].sum(),
        "sealed": g["limit_up"].sum(),
        "broken": g["_broken"].sum(),
        "n": g.size(),
        "avg_pct": g["change_pct"].mean(),
        "median_pct": g["change_pct"].median(),
        "strong_up_pct": g["_sup"].sum() / g.size() * 100.0,
        "strong_down_pct": g["_sdown"].sum() / g.size() * 100.0,
        "above_ma20_pct": g["above_ma20"].sum() / g.size(),
    })
    agg["limit_up"] = agg["sealed"]
    agg["ladder_completeness"] = np.where(
        agg["height"] >= 3, agg["rungs_filled"] / (agg["height"] - 1).clip(lower=1), 0.0)
    agg["promo_rate"] = np.where(
        agg["promo_pool"] >= PROMO_MIN_POOL,
        agg["promo_ok"] / agg["promo_pool"].clip(lower=1), np.nan)
    touched = agg["sealed"] + agg["broken"]
    agg["seal_rate"] = np.where(touched > 0, agg["sealed"] / touched.clip(lower=1), 0.5)
    agg["up_pct"] = g["_up"].sum() / agg["n"] * 100.0
    agg["strong_diff_pct"] = agg["strong_up_pct"] - agg["strong_down_pct"]
    if index_pct is not None:
        agg["index_pct"] = pd.Series(index_pct).reindex(agg.index).fillna(0.0)
    else:
        agg["index_pct"] = 0.0
    keep = ["height", "first_board", "ge2", "ge3", "ge5", "ladder_completeness",
            "promo_pool", "promo_rate", "seal_rate", "limit_up", "up_pct", "avg_pct",
            "median_pct", "strong_diff_pct", "strong_down_pct", "above_ma20_pct", "index_pct"]
    return agg[keep]


def classify_state(metrics_row):
    """单日 4 维综合分 → (state, score)。指标口径与 TSP regime_builder 一致。"""
    profit = (
        _score(metrics_row.get("up_pct", 50.0), 21, 75) * 0.45
        + _score(metrics_row.get("avg_pct", 0.0) * 100.0, -1.2, 1.3) * 0.25
        + _score(metrics_row.get("median_pct", 0.0) * 100.0, -1.2, 1.3) * 0.20
        + _score(metrics_row.get("strong_diff_pct", 0.0), -13, 14) * 0.10
    )
    speculation = (
        _score(metrics_row.get("limit_up", 0), 35, 97) * 0.30
        + _score((metrics_row.get("seal_rate", 0.5) or 0.5) * 100.0, 57, 75) * 0.40
        + _score(metrics_row.get("height", 0), 4, 9) * 0.30
    )
    resilience = 100.0 - _score(metrics_row.get("strong_down_pct", 0.0), 2, 18)
    trend = (
        _score(metrics_row.get("index_pct", 0.0) * 100.0, -2.5, 2.5) * 0.50
        + _score((metrics_row.get("above_ma20_pct", 0.5) or 0.5) * 100.0, 22, 76) * 0.50
    )
    score = (profit * STATE_WEIGHTS["profit"] + speculation * STATE_WEIGHTS["speculation"]
             + resilience * STATE_WEIGHTS["resilience"] + trend * STATE_WEIGHTS["trend"])
    score = max(0.0, min(100.0, score))
    s = round(score)
    if s >= STATE_STRONG:
        state = "strong"
    elif s >= STATE_LEAN_STRONG:
        state = "lean_strong"
    elif s >= STATE_RANGE:
        state = "range"
    elif s >= STATE_LEAN_WEAK:
        state = "lean_weak"
    else:
        state = "weak"
    return state, score


def classify_state_series(daily):
    """对日序逐日打 5 档 state。返回 (states list, scores list)。"""
    states, scores = [], []
    for _, row in daily.iterrows():
        st, sc = classify_state(row.to_dict())
        states.append(st)
        scores.append(sc)
    return states, scores


def ema_ffill(values, alpha=_EMA_ALPHA):
    """TSP 同款 EMA: None/NaN 沿用上一平滑值(ffill), 开头缺失回填首个有效值, 全缺失记 0。"""
    out = []
    cur = None
    for v in values:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            out.append(cur)  # 可能为 None
            continue
        cur = float(v) if cur is None else cur + alpha * (float(v) - cur)
        out.append(cur)
    first_valid = next((i for i, x in enumerate(out) if x is not None), None)
    if first_valid is not None:
        for i in range(first_valid):
            out[i] = out[first_valid]
    else:
        out = [0.0] * len(values)
    return out


def classify_phase_series(daily, states=None):
    """对完整日序打 6 阶段标签。

    参数 daily: DataFrame(index=trade_date 升序), 需含列
      height / first_board / ge2 / promo_rate / seal_rate (promo_rate 允许 NaN)。
    states: 可选 list, 与 daily 等长的 5 档 state 字符串, 用于弱档否决。
    处理: promo 前向填充 → 各驱动 EMA 平滑 → 逐日规则判定(按优先级)
      → 连续 _CONFIRM_DAYS 日同标签才切换。
    返回: daily 副本追加 phase 列(字符串)。
    """
    required = {"height", "first_board", "ge2", "promo_rate", "seal_rate"}
    missing = required - set(daily.columns)
    if missing:
        raise ValueError("classify_phase_series 缺少列: %s" % sorted(missing))
    daily = daily.sort_index()
    n = len(daily)
    height_s = ema_ffill([float(v) if pd.notna(v) else None for v in daily["height"]])
    first_s = ema_ffill([float(v) if pd.notna(v) else None for v in daily["first_board"]])
    ge2_s = ema_ffill([float(v) if pd.notna(v) else None for v in daily["ge2"]])
    promo_s = ema_ffill([float(v) if pd.notna(v) else None for v in daily["promo_rate"]])
    seal_s = ema_ffill([float(v) if pd.notna(v) else None for v in daily["seal_rate"]])

    def raw_label(i):
        h, fb, g2, pr, sr = height_s[i], first_s[i], ge2_s[i], promo_s[i], seal_s[i]
        g2_prev = ge2_s[max(0, i - 5)]
        h_prev = height_s[max(0, i - 5)]
        # 高潮
        if g2 >= CLIMAX_GE2 or fb >= CLIMAX_FIRST_BOARD:
            return PHASE_CLIMAX
        # 主升
        if h >= RALLY_HEIGHT and g2 >= RALLY_GE2 and pr >= RALLY_PROMO:
            return PHASE_RALLY
        if pr >= RALLY_PROMO_ALT and g2 >= RALLY_GE2_ALT and h >= RALLY_HEIGHT_ALT:
            return PHASE_RALLY
        # 冰点(优先于退潮: 持续死寂是"冰点"而非"自高位退潮")
        if h <= ICE_HEIGHT and g2 <= ICE_GE2 and fb <= ICE_FIRST_BOARD:
            return PHASE_ICE
        # 退潮: 自高位回落 + 晋级率坍塌, 或晋级/封板双弱
        from_high = g2_prev >= EBB_RECENT_GE2 or h_prev >= EBB_RECENT_HEIGHT
        if from_high and (pr <= EBB_PROMO and g2 < g2_prev):
            return PHASE_EBB
        if pr <= EBB_PROMO_STRICT and sr <= EBB_SEAL:
            return PHASE_EBB
        # 启动: 自低位扩张
        if g2 - g2_prev >= IGNITE_GE2_DELTA and g2 >= IGNITE_GE2 and pr >= IGNITE_PROMO:
            return PHASE_IGNITE
        if h - h_prev >= IGNITE_HEIGHT_DELTA and h >= IGNITE_HEIGHT and pr >= IGNITE_PROMO_SOFT:
            return PHASE_IGNITE
        return PHASE_REPAIR

    labels = []
    current = None
    pending = None
    pending_run = 0
    for i in range(n):
        raw = raw_label(i)
        if (states is not None and raw in _POSITIVE_PHASES and states[i] in _VETO_STATES):
            raw = PHASE_REPAIR
        if current is None:
            current = raw
            labels.append(raw)
            continue
        if raw == current:
            labels.append(current)
            pending, pending_run = None, 0
            continue
        if raw == pending:
            pending_run += 1
        else:
            pending, pending_run = raw, 1
        if pending_run >= _CONFIRM_DAYS:
            current = raw
            labels.append(current)
            pending, pending_run = None, 0
        else:
            labels.append(current)
    out = daily.copy()
    out["phase"] = labels
    return out


def segment_stats(phases):
    """连续同标签段长统计。phases: pd.Series(按日)。返回 DataFrame(phase/count/avg_len/max_len)。"""
    lab = pd.Series(phases.values, name="lab")
    seg_id = lab.ne(lab.shift()).cumsum().values
    seg = pd.DataFrame({"seg_id": seg_id, "phase": lab.values, "len": 1})
    seg = seg.groupby(["seg_id", "phase"], as_index=False)["len"].sum()
    return seg.groupby("phase")["len"].agg(count="count", avg_len="mean", max_len="max")
