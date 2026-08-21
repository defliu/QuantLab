# coding: utf-8
"""阶段0：构建 LightGBM 训练特征面板（无未来函数）。

数据源：
  - 日线行情：E:/astock/daily/stock_daily.parquet
      索引 (trade_date, ts_code)，含 close/pct_chg/vol/amount/turnover_rate/
      volume_ratio/pe_ttm/pb/dv_ttm/circ_mv/is_st/up_limit/down_limit 等字段
  - 股票池：D:/QuantLab/data/universe_all_a.csv (code, enabled)

规则（全部严格时点对齐，禁止前视偏差）：
  - 每行特征只用 当日(T日)及之前 的行情信息
  - 标签 = T日收盘 预测 未来 fwd 个交易日 的收益
  - 剔除：未启用 / ST / 科创板(688) / 北交所(4,8开头)
  - 剔除涨停/跌停当日作为买入样本的干扰：标签仍保留，特征不加当日未来信息

用法示例：
  python build_features.py --limit 300 --start 2022-01-01 --panel_start 2023-01-01   # 小规模调试
  python build_features.py                                     # 全量（warmup 自 2018）
输出：
  data/feature_panel.parquet   特征面板（含 trade_date/ts_code/特征/label/fwd_ret）
"""
import argparse
import json
import os
import numpy as np
import pandas as pd

import data_config as DC

HERE = DC.PROJECT_DIR
DATA = DC.MAIN_DAILY
UNIVERSE = DC.UNIVERSE
OUT_DIR = DC.DATA_DIR
OUT_PANEL = os.path.join(OUT_DIR, "feature_panel.parquet")
OUT_META = os.path.join(OUT_DIR, "features.json")

# 需要读取的原始列（不含未来信息，全部为 T 日及之前值）
RAW_COLS = [
    "close", "pct_chg", "vol", "amount", "turnover_rate", "volume_ratio",
    "pe_ttm", "pb", "dv_ttm", "circ_mv", "is_st",
]

# 最终特征顺序（train 脚本按此顺序取列）
FEATURE_COLS = [
    # 动量/位置（F1 类）
    "mom_5", "mom_10", "mom_20", "mom_60",
    "pos_250",          # 当前价 / 250日高点 位置（越低越安全 → F1 核心）
    "dist_250_low",     # 距250日低点涨幅
    # 量价/资金（F2 类，用量价近似主力资金）
    "volume_ratio",     # 原始量比
    "vol_ratio_5_20",   # 5日均量/20日均量（放量）
    "amount_ma5",       # 5日成交额均值（对数）
    "turn_ma5",         # 5日平均换手率
    # 技术形态（F4 类）
    "above_ma20",       # 收盘相对MA20 偏离
    "above_ma60",       # 收盘相对MA60 偏离
    "rsi6",             # RSI(6)
    "macd_hist",        # MACD 柱
    "vol20",            # 20日收益波动率
    # 估值/流动性（F6 类）
    "log_mv",           # log(流通市值)
    "pe_ttm", "pb", "dv_ttm",
    # 相对强度（F5 简化：个股动量 - 市场动量中位数）
    "rel_mom_20",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2018-01-01", help="读取行情起始日期(含warmup，早于面板起始)")
    ap.add_argument("--panel_start", default="2019-01-01", help="特征面板保留起始日期")
    ap.add_argument("--fwd", type=int, default=1, help="标签前瞻天数(次日=1)")
    ap.add_argument("--limit", type=int, default=0, help="仅取前N只股票(调试用，0=全部)")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    print("[1/5] 读取行情与股票池 ...")
    df = pd.read_parquet(DATA, columns=RAW_COLS)
    idx = df.index
    dates = pd.to_datetime(idx.get_level_values("trade_date"))
    codes = idx.get_level_values("ts_code")
    df = df[(dates >= args.start)]
    # 重新取对齐后的索引
    dates = pd.to_datetime(df.index.get_level_values("trade_date"))
    codes = df.index.get_level_values("ts_code")

    uni = pd.read_csv(UNIVERSE)
    uni_codes = set(uni[uni["enabled"] == True]["code"].astype(str).tolist())
    df = df[codes.isin(uni_codes)]

    codes = df.index.get_level_values("ts_code")
    df = df[df["is_st"] == 0]
    codes = df.index.get_level_values("ts_code")
    df = df[~codes.str.startswith("688") & ~codes.str.startswith(("4", "8"))]

    if args.limit > 0:
        keep = list(dict.fromkeys(df.index.get_level_values("ts_code")))[: args.limit]
        df = df[df.index.get_level_values("ts_code").isin(keep)]

    for c in df.columns:
        if df[c].dtype == "float64":
            df[c] = df[c].astype("float32")
    # 转为普通列，按 (股票, 日期) 排序，保证 groupby 时每只股票时间连续
    df = df.reset_index()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["ts_code"] = df["ts_code"].astype(str)
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    print(f"    面板原始行数: {len(df):,}  股票数: {df['ts_code'].nunique()}")

    close = df["close"].astype(float)
    codes_arr = df["ts_code"]
    gkey = codes_arr

    def gshift(s, n):
        return s.groupby(gkey).shift(n)

    def groll(s, w, agg="mean"):
        """每只股票独立滚动窗口，返回与 df.index 对齐的 Series。"""
        r = s.groupby(gkey).rolling(w, min_periods=w).agg(agg)
        r = r.reset_index(level=0, drop=True)
        r.index = df.index
        return r

    print("[2/5] 构造特征 ...")
    feats = pd.DataFrame(index=df.index)

    # --- 动量/位置 ---
    feats["mom_5"] = close / gshift(close, 5) - 1.0
    feats["mom_10"] = close / gshift(close, 10) - 1.0
    feats["mom_20"] = close / gshift(close, 20) - 1.0
    feats["mom_60"] = close / gshift(close, 60) - 1.0
    high250 = groll(close, 250, "max")
    low250 = groll(close, 250, "min")
    feats["pos_250"] = close / high250
    feats["dist_250_low"] = close / low250 - 1.0

    # --- 量价/资金 ---
    feats["volume_ratio"] = df["volume_ratio"]
    vol_ma5 = groll(df["vol"].astype(float), 5)
    vol_ma20 = groll(df["vol"].astype(float), 20)
    feats["vol_ratio_5_20"] = vol_ma5 / vol_ma20
    feats["amount_ma5"] = np.log1p(groll(df["amount"].astype(float), 5))
    feats["turn_ma5"] = groll(df["turnover_rate"].astype(float), 5)

    # --- 技术形态 ---
    ma20 = groll(close, 20)
    ma60 = groll(close, 60)
    feats["above_ma20"] = close / ma20 - 1.0
    feats["above_ma60"] = close / ma60 - 1.0

    delta = close.groupby(gkey).diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.groupby(gkey).transform(lambda s: s.ewm(alpha=1.0 / 6, min_periods=6).mean())
    avg_loss = loss.groupby(gkey).transform(lambda s: s.ewm(alpha=1.0 / 6, min_periods=6).mean())
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    feats["rsi6"] = 100.0 - 100.0 / (1.0 + rs)

    ema12 = close.groupby(gkey).transform(lambda s: s.ewm(span=12, adjust=False).mean())
    ema26 = close.groupby(gkey).transform(lambda s: s.ewm(span=26, adjust=False).mean())
    dif = ema12 - ema26
    dea = dif.groupby(gkey).transform(lambda s: s.ewm(span=9, adjust=False).mean())
    feats["macd_hist"] = (dif - dea) * 2.0

    ret1 = close / gshift(close, 1) - 1.0
    feats["vol20"] = groll(ret1, 20, "std")

    # --- 估值/流动性 ---
    feats["log_mv"] = np.log(df["circ_mv"].astype(float))
    feats["pe_ttm"] = df["pe_ttm"].astype(float)
    feats["pb"] = df["pb"].astype(float)
    feats["dv_ttm"] = df["dv_ttm"].astype(float)

    # --- 相对强度（横截面）---
    mkt_mom20 = feats["mom_20"].groupby(df["trade_date"]).transform("median")
    feats["rel_mom_20"] = feats["mom_20"] - mkt_mom20

    print("[3/5] 构造标签 ...")
    fwd_ret = gshift(close, -args.fwd) / close - 1.0
    feats["fwd_ret"] = fwd_ret
    feats["label"] = (fwd_ret > 0.0).astype("int8")
    feats["trade_date"] = df["trade_date"]
    feats["ts_code"] = df["ts_code"]

    print("[4/5] 过滤 NaN 与面板起始日期 ...")
    panel = feats[feats["trade_date"] >= pd.Timestamp(args.panel_start)].copy()
    panel = panel.replace([np.inf, -np.inf], np.nan)
    before = len(panel)
    panel = panel.dropna(subset=FEATURE_COLS).reset_index(drop=True)
    print(f"    过滤前: {before:,} 行 -> 过滤后: {len(panel):,} 行")

    print("[5/5] 保存面板 ...")
    panel.to_parquet(OUT_PANEL, index=False)
    with open(OUT_META, "w", encoding="utf-8") as f:
        json.dump(
            {
                "feature_cols": FEATURE_COLS,
                "label_col": "label",
                "fwd_ret_col": "fwd_ret",
                "fwd_days": args.fwd,
                "n_rows": len(panel),
                "n_stocks": panel["ts_code"].nunique(),
                "date_range": [str(panel["trade_date"].min()), str(panel["trade_date"].max())],
            },
            f, ensure_ascii=False, indent=2,
        )
    print("    保存到:", OUT_PANEL)
    print("    特征数:", len(FEATURE_COLS))
    print("    面板日期范围:", panel["trade_date"].min(), "->", panel["trade_date"].max())


if __name__ == "__main__":
    main()
