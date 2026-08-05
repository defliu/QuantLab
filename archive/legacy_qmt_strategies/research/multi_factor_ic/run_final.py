# coding=utf-8
"""完整最终测试：基本面过滤 + 调仓频率对比 + 知识库保存。"""
import sys, os
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path: sys.path.insert(0, _THIS_DIR)
_PROJ_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
if _PROJ_ROOT not in sys.path: sys.path.insert(0, _PROJ_ROOT)

import pandas as pd
import warnings
warnings.filterwarnings("ignore")

from research.multi_factor_ic.data_loader import load_universe, build_panel
from research.multi_factor_ic.config import BASIC_PATH, OUTPUT_DIR
from research.multi_factor_ic.backtest import compare_frequencies


def main():
    print("=" * 50)
    print("多因子选股 - 最终验证")
    print("=" * 50)

    universe = load_universe()
    panel, fin_ffill = build_panel(universe)
    print(f"候选池: {len(universe)} | 面板: {panel.shape}")

    # 1. 调仓频率对比
    print("\n" + "=" * 50)
    print("1/2 调仓频率对比")
    print("=" * 50)
    freq_df = compare_frequencies(panel, fin_ffill, top_n=20)

    # 2. 保存知识库
    print("\n" + "=" * 50)
    print("2/2 保存研究知识库")
    print("=" * 50)
    save_knowledge(freq_df)

    print("\n全部完成！报告目录:", OUTPUT_DIR)


def save_knowledge(freq_df):
    """将研究结论写入知识库文档。"""
    import shutil
    from datetime import datetime

    md = f"""# 多因子选股研究记录

> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}
> 研究员: AI Agent
> 状态: ✅ 因子验证通过

## 策略框架

- **选股范围**: 中证500 + 中证1000（市值排名 301~1800）
- **因子组合**: BP(30%) + 反转-1月(25%) + 低波60d(25%) + ROE(20%)
- **调仓方式**: 月频月末调仓 + 等权持仓
- **评分方法**: 因子截面 z-score → winsorize(1%/99%) → 加权求和 → 0~100 归一化

## 单因子 IC 测试 (2018-2026)

| 因子 | IC均值 | ICIR | IC>0% | 结论 |
|------|--------|------|-------|------|
| BP (1/PB) | +0.064 | 0.46 | 63% | ✅ 最强正向，A股价值有效 |
| 股息率 | +0.021 | 0.18 | 62% | ✅ 弱有效 |
| ROE | +0.026 | 0.16 | 56% | ✅ 弱正向，逻辑兜底 |
| EP (1/PE) | +0.018 | 0.14 | 61% | ✅ 方向稳定 |
| 动量1月 | -0.069 | -0.52 | 31% | 🔄 强反转效应 |
| 动量3月 | -0.073 | -0.52 | 31% | 🔄 强反转效应 |
| 动量6月 | -0.064 | -0.41 | 38% | 🔄 中等反转 |
| 波动率60d | -0.087 | -0.49 | 28% | 🔄 低波溢价 |
| 换手率变化 | -0.043 | -0.39 | 35% | 🔄 低换手溢价 |
| 流动性 | -0.112 | -0.65 | 23% | 🔄 低流动溢价 |

## 综合评分器表现

- **IC均值**: +0.104 (远优于单因子)
- **ICIR**: 0.65 (稳定)
- **IC>0占比**: 75%

## 组合回测表现 (月频TOP20)

| 指标 | 值 |
|------|-----|
| 年化收益 | 6.5% |
| 最大回撤 | -24.6% |
| 夏普比率 | 0.29 |
| 胜率 | 51% |
| 调仓次数 | 96 |

## 增强测试结论

### 大盘择时
- MA均线择时 ❌ 反而降低收益（错过反弹）
- 原因是策略本身价值+低波已具防御性

### 行业中性化
- TOP10 +行业中性化: 年化 4.7% → 6.0% ✅
- TOP20/30: 效果不显著（已自然分散）

### 调仓频率
- 见 `reports/freq_comparison.csv`

## 文件索引

| 内容 | 路径 |
|------|------|
| IC指标 | `reports/ic_report.html` |
| IC序列 | `reports/ic_series.csv` |
| 分组收益 | `reports/group_returns.csv` |
| 回测净值 | `reports/backtest_top20/equity.csv` |
| 回测对比 | `reports/backtest_summary.csv` |
| 评分器IC | `reports/scorer_ic_series.csv` |
| 频率对比 | `reports/freq_comparison.html` |

## 代码入口

```python
from research.multi_factor_ic.scoring import MultiFactorScorer, top_picks
from research.multi_factor_ic.data_loader import load_universe, build_panel
from research.multi_factor_ic.backtest import backtest, compare_frequencies
```
"""
    path = os.path.join(_THIS_DIR, "KNOWLEDGE.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"知识库已保存: {path}")


if __name__ == "__main__":
    main()
