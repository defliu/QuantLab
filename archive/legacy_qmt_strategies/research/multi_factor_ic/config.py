# coding=utf-8
"""多因子IC测试配置"""

# 数据路径
DATA_DIR = "E:/astock"
DAILY_PATH = f"{DATA_DIR}/daily/stock_daily.parquet"
BASIC_PATH = f"{DATA_DIR}/basic/stock_basic.parquet"
FINANCE_PATH = f"{DATA_DIR}/finance/fina_indicator.parquet"

# 选股范围：市值排名区间 [rank_start, rank_end)
# 中证500 ≈ 市值排名 301~800, 中证1000 ≈ 801~1800
# 合计 ≈ 301~1500
UNIVERSE_RANK_START = 301
UNIVERSE_RANK_END = 1801

# 回测区间
START_DATE = "2018-01-01"
END_DATE = "2026-06-30"

# 调仓频率：月频
REBALANCE_FREQ = "M"  # pandas offset string

# 因子定义：名称 -> 类别
FACTOR_CATEGORIES = {
    "EP": "价值",
    "BP": "价值",
    "dividend_yield": "价值",
    "ROE": "质量",
    "grossprofit_margin": "质量",
    "momentum_1m": "动量",
    "momentum_3m": "动量",
    "momentum_6m": "动量",
    "turnover_change": "情绪",
    "volatility_60d": "情绪",
    "liquidity_avg": "情绪",
    "vwap_volume_corr": "量价",
}

# 极值处理：百分位截尾
WINSORIZE_PCT = (0.01, 0.99)

# 中性化选项
NEUTRALIZE_INDUSTRY = True

# 输出目录
OUTPUT_DIR = "D:/QMT_STRATEGIES/research/multi_factor_ic/reports"
