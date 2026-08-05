# SPEC: 市场环境评分系统 Phase 1

## Objective
在 v3 风控基础上构建复合市场环境评分系统，替代当前二值（bear/normal）判断。目标：跨年亏损从 -8.5% 降至 -5% 以内，正常年份保留 10-15% 年化。

## 背景
当前 v3 的 _market_regime() 只有 binary 输出：bear 时全部清仓。这太粗糙——2024 年不是"全熊"，而是"大部分时间因子失效但偶有反弹"。需要连续评分来精细控制暴露。

## 方案：复合市场环境评分

### 信号组件（4个）

| 信号 | 计算方式 | 归一化范围 | 权重 |
|---|---|---|---|
| **趋势结构** | 多周期 MA 排列评分：MA5/MA20/MA60/MA120 多头排列=+1，空头排列=-1，混合=0 | [-1, 1] | 0.35 |
| **波动率** | 20日ATR / 60日ATR中位数，超出1.5倍=风险信号 | [-1, 1] | 0.20 |
| **成交量** | 20日成交量 vs 60日成交量中位数，缩量<70%=弱势确认 | [-1, 1] | 0.15 |
| **市场宽度** | 全市场（或universe内）收盘>MA20的股票占比，>60%=健康，<30%=弱势 | [-1, 1] | 0.30 |

### 复合评分 → 策略行为映射

| 评分范围 | 仓位系数 | max_positions | fb_weight | 说明 |
|---|---|---|---|---|
| > 0.3 | 1.0 | 15 | 0.75 | 正常操作 |
| 0 ~ 0.3 | 0.7 | 10 | 0.80 | 轻度防御 |
| -0.3 ~ 0 | 0.4 | 6 | 0.85 | 中度防御 |
| < -0.3 | 0.0 | 0 | 1.00 | 清仓（同原bear） |

### 文件结构

`
backtest/strategies/research/market_environment/
    __init__.py
    scorer.py          # CompositeEnvironmentScorer 类
    signals/
        __init__.py
        trend.py       # 趋势结构信号
        volatility.py  # 波动率信号
        volume.py      # 成交量信号
        breadth.py     # 市场宽度信号
`

### 接口

`python
class CompositeEnvironmentScorer:
    def __init__(self, weights: dict = None):
        # weights: {"trend": 0.35, "volatility": 0.20, "volume": 0.15, "breadth": 0.30}
        pass
    
    def score(self, panel: dict, current_date: str, aux_data: dict) -> dict:
        \"\"\"
        Returns:
        {
            "composite": float,  # [-1, 1]
            "signals": {
                "trend": float,
                "volatility": float,
                "volume": float,
                "breadth": float
            },
            "details": {...}  # per-signal debug info
        }
        \"\"\"
        pass
`

### 集成到 v3 策略

v3 strategy 中的 _market_regime() 替换为新 scorer。策略 receive strategy_config 中新参数：

`yaml
# v3 yaml 新增
strategy_params:
  env_scorer:
    enabled: true
    weights:
      trend: 0.35
      volatility: 0.20
      volume: 0.15
      breadth: 0.30
    position_map:
      - score_min: 0.3
        pos_ratio: 1.0
        max_pos: 15
        fb_weight: 0.75
      - score_min: 0.0
        pos_ratio: 0.7
        max_pos: 10
        fb_weight: 0.80
      - score_min: -0.3
        pos_ratio: 0.4
        max_pos: 6
        fb_weight: 0.85
      - score_min: -1.0
        pos_ratio: 0.0
        max_pos: 0
        fb_weight: 1.0
`

### 验收标准

1. 	ests/test_market_environment.py 跑通，覆盖各信号组件
2. 回测 2023-2024 跨年，v3 + env_scorer vs 原 v3 对比
   - 跨年总收益改进 >= +3%（即从 -8.5% 提升到 -5.5% 以上）
   - 最大回撤从 -20.5% 降至 -18% 以内
   - 2023H1 正常年份年化不下降超过 2%
3. 产出对比报告到 esults/market_env_v1_comparison.md

## Boundaries

1. 不改 v3 现有选股逻辑（ICIR权重、行业中性化、动量过滤）
2. 只替换市场环境判断部分
3. 所有信号须能从 ux_data 或 market_window 中计算，不新增数据源
4. 回测对比用同一组随机种子，确保可比性

## 优先级
P0: scorer + 4 signals 实现
P1: 集成到 v3 策略
P2: 回测验证 + 报告
