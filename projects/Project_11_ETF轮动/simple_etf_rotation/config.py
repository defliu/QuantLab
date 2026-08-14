"""
策略配置文件
所有可调参数都在这里集中管理
"""

# ============ ETF池 ============
# 宽基 + 行业（10只，覆盖度足够）
ETF_POOL = {
    "510300": {"name": "沪深300ETF", "type": "broad", "category": "大盘宽基"},
    "510500": {"name": "中证500ETF", "type": "broad", "category": "中盘宽基"},
    "512100": {"name": "中证1000ETF", "type": "broad", "category": "小盘宽基"},
    "159915": {"name": "创业板ETF", "type": "broad", "category": "成长宽基"},
    "588200": {"name": "科创50ETF", "type": "broad", "category": "硬科技"},
    "512480": {"name": "半导体ETF", "type": "industry", "category": "科技"},
    "516160": {"name": "新能源ETF", "type": "industry", "category": "新能源"},
    "512010": {"name": "医药ETF", "type": "industry", "category": "医药"},
    "512660": {"name": "军工ETF", "type": "industry", "category": "军工"},
    "512880": {"name": "证券ETF", "type": "industry", "category": "金融"},
}

# ============ 策略参数 ============
STRATEGY_PARAMS = {
    # 资金
    "initial_capital": 100000,  # 初始资金10万
    # 持仓
    "top_n": 5,  # 持有ETF数量
    # 择时参数（均线）
    "fast_ma": 90,
    "slow_ma": 240,
    "benchmark": "510300",  # 用沪深300作择时基准
    # 动量参数
    "momentum_short": 20,  # 短期动量周期
    "momentum_long": 40,  # 长期动量周期
    "momentum_short_weight": 0.6,  # 短期动量权重
    "momentum_long_weight": 0.4,  # 长期动量权重
    # 调仓
    "rebalance_day": 10,  # 每月10号调仓
    # 交易成本
    "commission": 0.0001,  # 万1
    "stamp_tax": 0.0001,  # 印花税万1（ETF卖出收）
    "slippage": 0.001,  # 滑点千1
}

# ============ 仓位规则 ============
POSITION_RULES = {
    "full_position": 1.0,  # 满仓信号
    "half_position": 0.5,  # 半仓信号
    "empty_position": 0.0,  # 空仓信号
}

# ============ 风险控制 ============
RISK_CONFIG = {
    # 单只ETF止损
    "single_etf_stop_loss": -0.10,  # 单只ETF亏10%止损
    # 组合止损
    "portfolio_stop_loss": -0.08,  # 组合亏8%清仓
    # 行业集中度
    "max_industry_weight": 0.6,  # 单行业ETF不超过60%仓位
    # 流动性过滤
    "min_avg_amount": 20000000,  # 日均成交额>2000万
    # 调仓阈值
    "min_trade_amount": 1000,  # 调仓金额>1000才交易
}

# ============ 日志配置 ============
LOG_CONFIG = {
    "log_file": "logs/strategy.log",
    "log_level": "INFO",
}