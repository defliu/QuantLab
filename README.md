# QuantLab — A股量化研究框架

> 项目根目录：E:\QuantLab
> 创建时间：2026-07-22

## 项目结构

```
QuantLab/
├── config/                 # 配置文件
│   ├── settings.yaml       # 全局配置
│   ├── factors.yaml        # 因子配置
│   └── settings.json       # 看板配置（自动生成）
│
├── data/                   # 数据管理
│   ├── feed.py             # 数据源适配
│   ├── universe.py         # 股票池管理
│   ├── cache/              # 本地缓存
│   ├── sentiment/          # 舆情数据
│   ├── equity_curve.csv    # 净值曲线
│   ├── positions.json      # 持仓
│   └── trades.csv          # 交易记录
│
├── factors/                # 因子库
│   ├── base.py             # 因子基类 + 预处理
│   ├── value.py            # 估值因子
│   ├── quality.py          # 质量因子
│   ├── growth.py           # 成长因子
│   ├── momentum.py         # 动量因子
│   ├── sentiment.py        # 舆情因子
│   ├── ml_factor.py        # ML因子挖掘
│   └── engine.py           # 因子引擎
│
├── strategy/               # 策略库
│   ├── multi_factor.py     # 多因子选股
│   ├── ml_strategy.py      # ML增强策略
│   └── portfolio.py        # 组合构建
│
├── optimization/           # 组合优化
│   ├── optimizer.py        # cvxpy组合优化
│   └── covariance.py       # 协方差估计
│
├── risk/                   # 风控
│   └── manager.py          # 风控管理
│
├── backtest/               # 回测引擎
│   ├── engine.py           # 回测引擎
│   └── analyzer.py         # 绩效分析
│
├── broker/                 # 券商接口
│   ├── base.py             # Broker抽象
│   ├── mini_qmt.py         # miniQMT实盘
│   ├── ptrade.py           # PTrade实盘
│   ├── paper.py            # 模拟盘
│   └── live_engine.py      # 实盘执行引擎
│
├── sentiment/              # 舆情分析
│   ├── collector.py        # 新闻采集
│   ├── analyzer.py         # NLP情绪分析
│   └── scheduler.py        # 定时采集
│
├── dashboard/              # 可视化
│   └── app.py              # Streamlit看板
│
├── projects/               # 策略项目
│   └── Project_01_多因子IC小盘Alpha/
│
├── main.py                 # 主入口（回测）
├── live_trading.py         # 实盘入口
├── requirements.txt        # 依赖
└── README.md               # 本文件
```

## 快速开始

```bash
# 运行回测
python main.py --strategy multi_factor_ic

# 运行特定项目
cd projects/Project_01_多因子IC小盘Alpha
python -m research.backtest
```

## 当前策略

| 编号 | 策略 | 年化 | 夏普 | 回撤 | 状态 |
|---|---|---|---|---|---|
| 01 | 多因子IC小盘Alpha | 15.5% | 0.50 | -20.5% | 回测完成 |

## 开发环境

- Python 3.11
- pandas 3.0+
- numpy
- QMT Python 3.6.8（实盘）
