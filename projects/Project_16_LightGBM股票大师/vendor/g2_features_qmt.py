# -*- coding: utf-8 -*-
# =============================================================================
# g2_features_qmt.py  —— G2/ENS 信号内产版 21 特征 QMT 内置计算模块 (s4-5b)
#
# 运行环境: QMT 内置 Python 3.6.8 + numpy + pandas
#          禁止 f-string / 海象运算符 / 泛型下标标注 / match-case
#
# 口径权威（逐字复刻，禁止擅自改口径）:
#   1) build_g2_daily.py  compute_price_features  L39-91   -> 17 个价量/估值特征
#   2) refresh_panel_enh.py  turnover_rank        L107-111/L125
#   3) refresh_panel_enh.py  industry_mom20       L120-121/L129-135
#   4) train_g2_sandbox.py   SANDBOX_B            L45-51    -> 特征装配顺序
#
# 设计分层:
#   [L1] 原始列构造  build_raw_columns()  : QMT 行情 + 静态快照 -> astock 口径原始列
#   [L2] 价量特征    compute_price_features() : 逐字复刻 build_g2_daily L39-91
#   [L3] 增强特征    compute_turnover_rank() / compute_industry_mom20()
#   [L4] 装配        assemble()  : 严格按 FEATURE_ORDER(=SANDBOX_B) 输出 21 列
#   [L5] QMT 入口    build_from_qmt() : 面向沙箱的对外接口
#
# 单位契约（astock / Tushare 口径，务必对齐）:
#   close      : 元（未复权，get_market_data_ex dividend_type='none'）
#   vol        : 手
#   amount     : 千元        <-- xtdata 的成交额是「元」，调用方须先 ÷1000
#   float_share: 万股
#   circ_mv    : 万元        = float_share(万股) * close(元)
#   turnover_rate: %         = vol(手) / float_share(万股)   << 单位恰好相等
#   pe_ttm/pb/dv_ttm : as-is（静态快照，astock 口径原值）
#   adj_factor : 后复权因子（每股全局唯一常数，静态快照 1 个 float 即可永久纠正）
#                训练面板量价特征口径 = 后复权价 hfq = close_未复权 * adj_factor
#                QMT 侧取得方式见 compute_price_features docstring『复权口径』
#   推导: 1 手 / 1 万股 = 100 股 / 10000 股 = 1e-2 -> 数值上恰等于换手率(%)
#
# 行集契约（train-serving skew 头号来源，务必遵守）:
#   模型 B21 的训练行集 = 「过滤帧」= build_features_v2.py L95-103:
#     universe(enabled) -> is_st==0 -> 剔 688*/4*/8* 开头 -> float32 量化
#   横截面特征（turnover_rank / rel_mom_20 的 median / industry_mom20）依赖行集，
#   调用方必须先用 filter_universe() 对齐，否则该三项系统性偏移。
#   残留差异: 训练侧另有 universe_all_a.csv(enabled) 白名单，沙箱内无此清单，
#   由调用方在传入前自行剔除（见 filter_universe docstring）。
# =============================================================================

# ★ QMT 嵌入安全（s4-5d）：顶层 import 必须 try/except（AGENTS「QMT import 必须
#   try/except fallback」红线）。内联进单文件后，顶层裸 import 失败会导致整个策略
#   加载失败（引擎无法隔离捕获）。失败置 None，调用期 AttributeError 由信号引擎
#   的 try/except 捕获 -> 转被动 fail-safe。
try:
    import numpy as np
except Exception:
    np = None
try:
    import pandas as pd
except Exception:
    pd = None

# ---------------------------------------------------------------------------
# 特征顺序（= train_g2_sandbox.SANDBOX_B，字典序即模型输入序，禁止改动）
# ---------------------------------------------------------------------------
FEATURE_ORDER = [
    "vol_ratio_5_20", "amount_ma5", "volume_ratio", "above_ma60", "rsi6",
    "above_ma20", "turn_ma5", "vol20", "mom_20", "rel_mom_20",
    "mom_10", "mom_5", "dist_250_low", "mom_60", "macd_hist",
    "turnover_rank", "industry_mom20",
    "pb", "dv_ttm", "pe_ttm", "log_mv",
]
# ★ 本文件会被 build_p16_g2.py 构入门禁（含禁用词扫描），不得出现断言语句；
#   长度一致性由构建期脚本与离线对齐 harness 校验。
N_FEATURES = len(FEATURE_ORDER)

# 单位换算（QMT 侧调用方按实测 xtdata 单位覆盖）
AMOUNT_YUAN_TO_KYUAN = 0.001   # 成交额 元 -> 千元
VOL_LOT_TO_LOT = 1.0           # 成交量 手 -> 手（xtdata 股票 volume 即为手，待 QMT 实测确认）

# 求一部分特征所需的最少历史 bar 数（dist_250_low 需 250 根）
MIN_HISTORY_BARS = 250


# ---------------------------------------------------------------------------
# [L2] 逐字复刻 build_g2_daily.py L39-91
#   前置条件: df 按 (ts_code, trade_date) 升序排列（同 code 连续）
# ---------------------------------------------------------------------------
def _gshift(s, gkey, n):
    return s.groupby(gkey).shift(n)


def _groll(s, gkey, w, agg, base_index):
    # ★ 必修⑤ QMT pandas 1.x 兼容性：
    #   groupby().rolling().agg() 返回 MultiIndex(ts_code, 原索引)，而原实现里
    #   .rolling() 保留原索引（astock live 用 pandas 2.x）。此处显式
    #   reset_index(level=0, drop=True) 去掉 group key、再 index = base_index 强制对齐，
    #   两版行为一致（唯一依赖：原索引在 groupby 内单调递增，前置条件已保证）。
    #   若 QMT 旧版 pandas 的 rolling 在 groupby 内因缺值/重复索引改变顺序，对齐校验
    #   校验（s4-5e）会暴露差异；此时退化为显式循环实现 _groll_loop（保序保基）。
    r = s.groupby(gkey).rolling(w, min_periods=w).agg(agg)
    r = r.reset_index(level=0, drop=True)
    r.index = base_index
    return r


def compute_price_features(df):
    """输入 df 需含: ts_code/trade_date/close/vol/amount/turnover_rate/
    volume_ratio/circ_mv/pe_ttm/pb/dv_ttm；价格类特征另需 adj_factor 或 adj_close。
    返回 dict of Series（与 df 同行索引）。

    逐字复刻 build_g2_daily.compute_price_features，不含 turnover_rank/industry_mom20。

    ★ 复权口径（2026-08-22 审计修复 R2，2026-10-16 s4-5c 补齐，2026-10-17 s4-5d 定稿 front_ratio）:
      训练面板 (build_features_v2.py L110-114) 的量价特征一律用后复权价
          hfq = close(未复权) * adj_factor        <- adj_factor 为「每股全局唯一常数」
      QMT 侧供给优先级（三源等价，只走 1 或 2）:
        1) df 含 adj_close 列 + adj_factor 列（推荐）
           adj_close 必须来自 C.get_market_data_ex(dividend_type='front_ratio')['close']
             该列 = close * (adj_factor / adj_factor_最新) = 前复权价 qfq
           -> hfq = adj_close * adj_factor(最新静态快照)   ★ 不可再乘未复权 close
        2) df 仅含 adj_factor 列  -> hfq = close(未复权) * adj_factor
        3) 都无 -> 退回未复权 close（仅与 build_g2_daily 旧口径一致，不推荐）
        ★ 禁用 dividend_type='back_ratio': 实测与训练面板 hfq 不同基
        （macd_hist rel_to_std 5.847 >> 对齐判据 0.05），故一律用 front_ratio + 静态 adj_factor。
      唯一对复权基数敏感的特征是 macd_hist（差分量，随基数线性缩放）；
      mom_* / above_ma* / dist_250_low / rsi6 / vol20 / rel_mom_20 均为比值或比率，对逐股常数缩放不变。

    ★ 未复权口径（禁止替换）: circ_mv / log_mv 用未复权 close；industry_mom20 亦用未复权 close。
    """
    gkey = df["ts_code"]
    close_unadj = df["close"].astype(float)          # 未复权（估值口径）
    if "adj_close" in df.columns and "adj_factor" in df.columns:
        # QMT front_ratio 的 close = qfq = close * (C / C_latest);
        # 乘最新静态 adj_factor 还原为训练面板口径 hfq（见 docstring『复权口径』）
        close = df["adj_close"].astype(float) * df["adj_factor"].astype(float)
    elif "adj_close" in df.columns:
        close = df["adj_close"].astype(float)
    elif "adj_factor" in df.columns:
        close = close_unadj * df["adj_factor"].astype(float)
    else:
        close = close_unadj
    idx = df.index
    f = {}

    def groll(s, w, agg="mean"):
        return _groll(s, gkey, w, agg, idx)

    f["mom_5"] = close / _gshift(close, gkey, 5) - 1.0
    f["mom_10"] = close / _gshift(close, gkey, 10) - 1.0
    f["mom_20"] = close / _gshift(close, gkey, 20) - 1.0
    f["mom_60"] = close / _gshift(close, gkey, 60) - 1.0

    high250 = groll(close, 250, "max")
    low250 = groll(close, 250, "min")
    f["pos_250"] = close / high250                    # 不输出，保持与参考实现一致
    f["dist_250_low"] = close / low250 - 1.0

    f["volume_ratio"] = df["volume_ratio"].astype(float)

    vol_ma5 = groll(df["vol"].astype(float), 5)
    vol_ma20 = groll(df["vol"].astype(float), 20)
    f["vol_ratio_5_20"] = vol_ma5 / vol_ma20

    f["amount_ma5"] = np.log1p(groll(df["amount"].astype(float), 5))
    f["turn_ma5"] = groll(df["turnover_rate"].astype(float), 5)

    ma20 = groll(close, 20)
    ma60 = groll(close, 60)
    f["above_ma20"] = close / ma20 - 1.0
    f["above_ma60"] = close / ma60 - 1.0

    delta = close.groupby(gkey).diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.groupby(gkey).transform(lambda s: s.ewm(alpha=1.0 / 6, min_periods=6).mean())
    avg_loss = loss.groupby(gkey).transform(lambda s: s.ewm(alpha=1.0 / 6, min_periods=6).mean())
    f["rsi6"] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss.replace(0.0, np.nan))

    ema12 = close.groupby(gkey).transform(lambda s: s.ewm(span=12, adjust=False).mean())
    ema26 = close.groupby(gkey).transform(lambda s: s.ewm(span=26, adjust=False).mean())
    dif = ema12 - ema26
    dea = dif.groupby(gkey).transform(lambda s: s.ewm(span=9, adjust=False).mean())
    f["macd_hist"] = (dif - dea) * 2.0

    ret1 = close / _gshift(close, gkey, 1) - 1.0
    f["vol20"] = groll(ret1, 20, "std")

    f["log_mv"] = np.log(df["circ_mv"].astype(float))
    f["pe_ttm"] = df["pe_ttm"].astype(float)
    f["pb"] = df["pb"].astype(float)
    f["dv_ttm"] = df["dv_ttm"].astype(float)

    mkt_mom20 = f["mom_20"].groupby(df["trade_date"]).transform("median")
    f["rel_mom_20"] = f["mom_20"] - mkt_mom20
    return f


# ---------------------------------------------------------------------------
# [L3] 增强特征（refresh_panel_enh.py 口径）
# ---------------------------------------------------------------------------
def compute_turnover_rank(df, turnover_col="true_turnover"):
    """全市场换手横截面排名（pct rank, method='average'）。

    参考: refresh_panel_enh.py L125  mg.groupby("trade_date")["true_turnover"].rank(pct=True)
    注: rank 对全局单调缩放不变 -> 只要 vol 与 float_share 单位一致即可。
    """
    return df.groupby("trade_date")[turnover_col].rank(pct=True)


def compute_industry_mom20(df, industry_map):
    """行业 20 日动量（stock_basic.industry 映射 + 行业等权均值）。

    参考: refresh_panel_enh.py L120-121/L129-135
      mom20 = close / close.groupby(ts_code).shift(20) - 1.0   （未复权 close）
      ind_mom = mg.dropna(subset=[industry, mom20])
                  .groupby([trade_date, industry])["mom20"].mean()
    注意: 行业分类来源 = stock_basic.industry（Tushare 行业），非 881 / 非同花顺。
    """
    gkey = df["ts_code"]
    close = df["close"].astype(float)
    mom20 = close / _gshift(close, gkey, 20) - 1.0
    ind = df["ts_code"].map(industry_map)
    tmp = pd.DataFrame({
        "trade_date": df["trade_date"].values,
        "industry": ind.values,
        "mom20": mom20.values,
    })
    tmp = tmp.dropna(subset=["industry", "mom20"])
    ind_mom = tmp.groupby(["trade_date", "industry"])["mom20"].mean().rename("industry_mom20")
    ind_mom = ind_mom.reset_index()
    merged = pd.DataFrame({"trade_date": df["trade_date"].values, "industry": ind.values})
    merged["_row"] = np.arange(len(df))
    out = merged.merge(ind_mom, on=["trade_date", "industry"], how="left")
    out = out.sort_values("_row")
    return pd.Series(out["industry_mom20"].values, index=df.index)


# ---------------------------------------------------------------------------
# [L4] 装配：严格按 FEATURE_ORDER 输出 21 列
# ---------------------------------------------------------------------------
def assemble(df, industry_map, snap_map=None):
    """df 需已含 astock 口径原始列 + true_turnover 列；返回仅含 21 特征的 DataFrame。

    snap_map: {ts_code: {'pb':.., 'dv_ttm':.., 'pe_ttm':..}}，None 时用 df 内列。
    """
    work = df.copy()
    work = work.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    f = compute_price_features(work)
    f["turnover_rank"] = compute_turnover_rank(work)
    f["industry_mom20"] = compute_industry_mom20(work, industry_map)

    # 静态快照优先覆盖 pb/dv_ttm/pe_ttm（QMT 内无原生估值字段）
    if snap_map is not None:
        for col in ("pb", "dv_ttm", "pe_ttm"):
            vals = work["ts_code"].map(lambda c: (snap_map.get(c) or {}).get(col, np.nan))
            f[col] = vals.astype(float)

    out = pd.DataFrame(index=work.index)
    out["ts_code"] = work["ts_code"].values
    out["trade_date"] = work["trade_date"].values
    for name in FEATURE_ORDER:
        out[name] = np.asarray(f[name], dtype=np.float64)
    return out


# ---------------------------------------------------------------------------
# [L1] 原始列构造：QMT 行情 + 静态快照 -> astock 口径原始列
# ---------------------------------------------------------------------------
def build_raw_columns(bars, float_share_map, snap_map,
                      amount_yuan=True, volume_is_lot=True):
    """把 QMT 行情拼成长表并推导 astock 口径原始列。

    bars: {ts_code: DataFrame(index=trade_date 'YYYYMMDD',
                              columns=['close','volume','amount', (可选)'adj_factor'])}
          未复权 close；若提供 adj_factor 则价量特征走复权口径
    float_share_map: {ts_code: 万股}
      ★ 必修③ 来源契约（沙箱内唯一可取路径，2026-10-17 实测）:
        float_share_map = {code: C.get_instrument_detail(code)['FloatVolume'] / 1e4}
        —— get_instrument_detail 的 FloatVolume 单位是「股」
           （实测 600000.SH = 33305838300.0 股 -> 3330583.83 万股），
           而本模块单位契约要求「万股」，故须 ÷1e4。
        ★ 该映射是「静态快照」：QMT 无逐日股数接口（get_market_data_ex 的 fields
           参数被忽略、恒回 ['open','high','low','close','volume','amount',
           'settelementPrice','openInterest','preClose','suspendFlag']，
           total_share/float_share/circ_mv 均不可得）。
        ★ 已知系统误差（沙箱固有口径，需在验收中量化）: 股本并非常量，送转/增发/
           回购发生后 float_share 会跳变，静态快照会使 true_turnover(turnover_rate)
           与 circ_mv 在该变动日之后整体偏移（乘性常数）。影响面: turn_ma5 /
           turnover_rank / log_mv / （经由排名）三类；mom_* / rsi6 / vol20 /
           above_ma* / dist_250_low / macd_hist / volume_ratio / vol_ratio_5_20 /
           amount_ma5 不受影响（纯价量比值）。
           缓解: 每次换仓重建快照即可（策略换仓频率 2 月级，与快照刷新同频）。
    snap_map: {ts_code: {'pb','dv_ttm','pe_ttm'}}
      ★ 同上为静态快照，来源为外部管道落盘的最近一期估值（astock 口径原值，
        不做 PIT 还原）；覆盖率不足的 code 记为 NaN，由下层模型按缺失处理。
    返回: 长表 df（含 ts_code/trade_date/close/vol/amount/turnover_rate/
          volume_ratio/circ_mv/pe_ttm/pb/dv_ttm/true_turnover[, adj_factor]）
    ★ 复权: bars 含 adj_factor 时原样透传，供 compute_price_features 复用
      （训练面板量价特征口径 = close * adj_factor，见 build_features_v2 L110-114）
    """
    frames = []
    for code, d in bars.items():
        if d is None or len(d) == 0:
            continue
        t = d.reset_index()
        t.columns = ["trade_date"] + list(d.columns)
        t["ts_code"] = code
        frames.append(t)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["ts_code"] = df["ts_code"].astype(str)
    df["trade_date"] = df["trade_date"].astype(str).str.slice(0, 8)
    df["close"] = df["close"].astype(float)
    df["vol"] = df["volume"].astype(float)
    df["amount"] = df["amount"].astype(float)
    # 复权透传：训练面板量价特征口径 = close * adj_factor（build_features_v2 L110-114）
    for _adjc in ("adj_factor", "adj_close"):
        if _adjc in df.columns:
            df[_adjc] = df[_adjc].astype(float)
    if amount_yuan:
        df["amount"] = df["amount"] * AMOUNT_YUAN_TO_KYUAN
    if not volume_is_lot:
        df["vol"] = df["vol"] * VOL_LOT_TO_LOT

    fs = df["ts_code"].map(lambda c: float_share_map.get(c, np.nan)).astype(float)
    df["true_turnover"] = df["vol"] / fs.replace(0.0, np.nan)
    df["turnover_rate"] = df["true_turnover"]          # 主库区即原生换手率，量纲一致
    df["circ_mv"] = fs * df["close"]                   # 万股 * 元 = 万元

    # volume_ratio（量比）: astock 原生口径为「多日均量加权」，无闭式精确复刻。
    #   2026-10-17 探针实证（2024-07-01~2026-10-16，n≈3.0e6，对照列=merged_daily_full.volume_ratio）:
    #     v1 = vol_t / mean(vol_{t-1..t-5})  ->  pear=0.9891 spear=0.9857
    #                                           round(…,2) 与原生值相等命中率 98.14%
    #     其他候选(prev6/incl5/incl6/ewm5)均更差（pear≤0.92）；
    #     残差尾部全为停牌/ST 极端行（vol=0 时原生写 25.67 等哨兵值，long 期间无成交）。
    #   故保留本近似式；round 到 2 位可进一步贴合原生发布精度（下游 Z-score 排序等价）。
    #   若后续 QMT 内能取到原生 volume_ratio 列，则直接透传（见 compute_price_features L135 优先取 df 列）。
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    g = df.groupby("ts_code")["vol"]
    prev5 = g.transform(lambda s: s.shift(1).rolling(5, min_periods=5).mean())
    df["volume_ratio"] = df["vol"] / prev5.replace(0.0, np.nan)

    for col in ("pe_ttm", "pb", "dv_ttm"):
        df[col] = df["ts_code"].map(lambda c: (snap_map.get(c) or {}).get(col, np.nan)).astype(float)
    return df


# ---------------------------------------------------------------------------
# [L1b] 行集对齐门（必修⑥）：逐字复刻 build_features_v2.py L95-103
# ---------------------------------------------------------------------------
def filter_universe(df, enabled_codes=None, st_codes=None, quantize=True):
    """把长表裁到与模型 B21 训练行集一致的行集合（train-serving skew 头号防线）。

    逐字复刻 build_features_v2.py L95-103 的过滤链，顺序固定不可调换：
        1) universe(enabled) 白名单 isin   （universe_all_a.csv 中 enabled==True）
        2) is_st == 0
        3) 剔除 688* / 4* / 8* 前缀（科创板 + 北交所/新三板）
        4) float64 列 -> float32 量化（训练侧 L101-103 同款，保证数值位一致）
        5) 按 (ts_code, trade_date) 升序稳定排序

    参数:
      df           : 长表（需含 ts_code / trade_date 列）
      enabled_codes: 白名单 code 集合；None = 不过滤
                     ★ 沙箱内无 universe_all_a.csv，须由构建期生成静态快照
                       （build_p16_g2.py 内联字面量）后经 init 传入
      st_codes     : 当日 ST/*ST code 集合；None = 若 df 无 is_st 列则跳过该步
                     ★ 沙箱内无逐行 is_st（QMT get_market_data_ex 无该字段），
                       同样须构建期静态快照（astock 最近交易日 is_st==1）
      quantize     : 是否做 float32 量化；离线对齐 harness 置 False 可分离纯口径差

    返回: 过滤后的新 DataFrame（reset_index）

    ★ 调用时机: 必须在「原始列就绪 + 特征计算之前」；
      横截面特征（turnover_rank / rel_mom_20 的 median / industry_mom20）依赖参与行集合，
      若在全量上算再裁行，训练侧会系统性偏移。
    """
    if len(df) == 0:
        return df
    out = df
    if enabled_codes is not None:
        want = set([str(c) for c in enabled_codes])
        out = out[out["ts_code"].isin(want)]
    if "is_st" in out.columns:
        out = out[out["is_st"].astype(float) == 0]
    elif st_codes is not None:
        st = set([str(c) for c in st_codes])
        out = out[~out["ts_code"].isin(st)]
    codes = out["ts_code"]
    out = out[~codes.str.startswith("688") & ~codes.str.startswith(("4", "8"))]
    out = out.copy()
    if quantize:
        for c in out.columns:
            if out[c].dtype == "float64":
                out[c] = out[c].astype("float32")
    out = out.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# [L5] QMT 沙箱对外入口
# ---------------------------------------------------------------------------
def build_from_qmt(bars, float_share_map, industry_map, snap_map,
                   target_codes=None, target_date=None,
                   amount_yuan=True, volume_is_lot=True,
                   enabled_codes=None, st_codes=None, align_universe=True):
    """沙箱内一步到位：QMT 行情 -> 21 特征长表。

    target_codes: 候选池；None = 全市场（横截面特征需全市场参与计算）
    target_date : 'YYYYMMDD'；None = 全市场最新交易日
    align_universe: True  = 先过 filter_universe（必修⑥，对齐 B21 训练行集，生产默认）
                    False = 保留未过滤全量（仅用于 train-serving skew 对照实验）
    enabled_codes/st_codes: 见 filter_universe docstring（构建期静态快照）
    返回: DataFrame[ts_code, trade_date] + FEATURE_ORDER(21)
    """
    raw = build_raw_columns(bars, float_share_map, snap_map,
                            amount_yuan=amount_yuan, volume_is_lot=volume_is_lot)
    if len(raw) == 0:
        return raw
    if align_universe:
        raw = filter_universe(raw, enabled_codes=enabled_codes, st_codes=st_codes)
        if len(raw) == 0:
            return raw
    full = assemble(raw, industry_map, snap_map=snap_map)
    if target_date is not None:
        full = full[full["trade_date"] == str(target_date)]
    if target_codes is not None:
        want = set([str(c) for c in target_codes])
        full = full[full["ts_code"].isin(want)]
    return full.reset_index(drop=True)


def cross_section_ready(history_len, need=None):
    """历史 bar 是否足够（dist_250_low 需 250 根）。"""
    n = MIN_HISTORY_BARS if need is None else int(need)
    return int(history_len) >= n
