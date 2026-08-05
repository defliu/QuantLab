# SPEC：DeepSeek 选股策略 — QMT 回测实现

> **日期**：2026-07-02
> **状态**：待 CC 执行（今晚加班回测）
> **来源**：黄氏策略库 `gs_1_deepseek.txt`（AI 优化版）
> **关联**：`knowledge_base/20_策略知识库/黄氏策略/gs_1_deepseek.txt`
> **红线**：Hermes 只出 SPEC，CC 负责全部编码执行

---

## §0 策略概述

DeepSeek 版在黄氏基础主升浪上做了 **4 项 AI 优化**，是目前黄氏策略库中逻辑最完善、QMT 可移植性最强的版本。

**核心逻辑**：MA 多头排列 + 斜率替代角度 + 前段涨幅限制 + 有效阳线统计 + 大盘安全 + 换手/量比/市值区间

---

## §1 Objective

在 QMT 回测框架中实现 DeepSeek 选股策略，验证其历史表现。

**回测目标**：
1. 验证策略在 2019-2025 年（含牛熊）的收益/回撤表现
2. 关键参数做敏感性分析（斜率阈值、换手率区间、市值区间）
3. 与基础主升浪版（无 AI 优化）做对比，量化 4 项优化的边际贡献
4. 输出：年化收益、最大回撤、夏普比率、胜率、交易次数、月度收益分布

---

## §2 Commands（CC 执行路径）

```bash
# 1. 阅读本 SPEC
# 2. 阅读踩坑大全
skill_view(name='qmt-pitfalls')

# 3. 在回测框架中实现策略
#    回测框架位置：D:\QMT_STRATEGIES\backtest\
#    参考已有策略实现格式

# 4. 运行回测
cd D:\QMT_STRATEGIES
python -m pytest backtest/tests/test_deepseek_strategy.py -v

# 5. 输出回测报告到
#    D:\QMT_STRATEGIES\trade_reports\deepseek_backtest_YYYYMMDD.md

# 6. 验收：validate_qmt_file.py 检查
python scripts/validate_qmt_file.py backtest/strategies/deepseek_strategy.py
```

---

## §3 策略逻辑（精确到数学公式）

### 3.1 选股条件（买入信号）

所有条件 **AND** 连接，同时满足才买入：

#### 条件 A：MA 多头排列
```
MA5 = MA(CLOSE, 5)
MA10 = MA(CLOSE, 10)
MA20 = MA(CLOSE, 20)
MA60 = MA(CLOSE, 60)
多头 = CLOSE > MA5 AND MA5 > MA10 AND MA10 > MA20 AND MA20 > MA60
```

#### 条件 B：斜率替代角度（优化 1）
```
斜率5 = (MA5 - REF(MA5, 5)) / REF(MA5, 5) / 5 * 100
斜率OK = 斜率5 >= 2.5    # 5 日平均每天涨幅 >= 2.5%
```
> 用斜率替代 ATAN 角度，不受股价绝对值影响，更稳定。

#### 条件 C：阳线 + 回踩不破
```
阳线 = CLOSE > OPEN
回踩OK = LOW >= MA5 * 0.98    # 最低价不破 MA5 的 98%
```

#### 条件 D：10 日内阳线比例 >= 60%
```
阳线比例 = COUNT(CLOSE > OPEN, 10) / 10
阳线比例OK = 阳线比例 >= 0.6
```

#### 条件 E：前段涨幅限制（优化 2）— 防追高
```
涨幅距60日线 = (CLOSE - MA60) / MA60 * 100
涨幅OK = 涨幅距60日线 < 30    # 距 60 日线不超过 30%
```

#### 条件 F：有效阳线统计（优化 3）
```
有效阳线 = CLOSE > REF(CLOSE, 1) * 1.005    # 涨幅 > 0.5%
有效比例 = COUNT(有效阳线, 10) / 10 * 100
有效OK = 有效比例 >= 50    # 10 日内 >= 5 日有效阳线
```

#### 条件 G：换手率区间
```
换手率 = VOL / CAPITAL * 100
换手率OK = 换手率 > 3 AND 换手率 < 10
```

#### 条件 H：量比区间
```
量比 = VOL / MA(REF(VOL, 1), 5)
量比OK = 量比 > 1.2 AND 量比 < 4
```

#### 条件 I：流通市值区间
```
流通市值_亿 = FINANCE(7) * CLOSE / 100000000
市值OK = 流通市值_亿 >= 50 AND 流通市值_亿 < 500
```

#### 条件 J：大盘安全（优化 4）
```
上证 = INDEXC    # 上证指数收盘价
上证MA20 = MA(上证, 20)
大盘OK = 上证 > 上证MA20    # 上证在 20 日线上方
```

#### 条件 K：非 ST
```
非ST = NOT(NAMELIKE('ST') OR NAMELIKE('*ST'))
```

#### 条件 L（可选推荐）：筹码集中度（从 529 版借鉴）
```
成本95 = COST(95)
成本5 = COST(5)
筹码集中度 = (成本95 - 成本5) / 成本5 * 100
筹码OK = 筹码集中度 <= 25    # 筹码相对集中
```
> 注意：COST 函数在 QMT 中可能不可用，如果不可用则跳过此条件，在报告中说明。

### 3.2 综合买入信号
```
买入信号 = 多头 AND 斜率OK AND 阳线 AND 回踩OK AND 阳线比例OK
       AND 涨幅OK AND 有效OK
       AND 换手率OK AND 量比OK AND 市值OK
       AND 大盘OK AND 非ST
       [AND 筹码OK]    # 可选
```

### 3.3 卖出规则

| 规则 | 条件 | 说明 |
|------|------|------|
| 止损 | 亏损 >= -8%（从买入价算） | 硬止损 |
| 止盈 | 盈利 >= 20%（从买入价算） | 分批止盈，到 20% 卖 50% |
| 趋势破坏 | CLOSE < MA10 AND MA10 < MA20 | 短期趋势走坏 |
| 大盘风险 | 上证 < 上证MA20 连续 3 日 | 系统性风险离场 |
| 持仓上限 | 最长持有 60 个交易日 | 防止死扛 |

### 3.4 仓位管理

```
单票仓位 = min(20%, 可用资金 / 持仓数)
最大持仓数 = 5
```

---

## §4 回测配置

| 参数 | 值 |
|------|-----|
| 回测区间 | 2019-01-01 ~ 2025-06-30 |
| 股票池 | 全 A 股（剔除 ST/停牌/次新<60日） |
| 调仓频率 | 日频（每日收盘检查买入信号） |
| 复权方式 | 前复权（qfq） |
| 手续费 | 买入万 1.5，卖出万 1.5 + 印花税千 1 |
| 滑点 | 0.1% |
| 基准 | 沪深 300（000300.SH） |

### 4.1 参数敏感性分析（单独跑）

| 参数 | 默认值 | 扫描范围 |
|------|--------|---------|
| 斜率阈值 | 2.5% | [1.5, 2.0, 2.5, 3.0, 3.5] |
| 换手率下限 | 3% | [1, 2, 3, 4] |
| 换手率上限 | 10% | [8, 10, 12, 15] |
| 市值下限(亿) | 50 | [30, 50, 80, 100] |
| 市值上限(亿) | 500 | [300, 500, 800, 1000] |
| 涨幅限制 | 30% | [20, 30, 40, 50] |

### 4.2 对比回测（验证 4 项优化贡献）

跑 5 个版本对比：

| 版本 | 包含的优化 |
|------|-----------|
| V0 基础版 | 只有 MA 多头 + 角度 45° + 阳线比例（原始主升浪） |
| V1 | V0 + 斜率替代角度 |
| V2 | V1 + 前段涨幅限制 |
| V3 | V2 + 有效阳线统计 |
| V4（完整版） | V3 + 大盘安全 + 换手/量比/市值区间 |

---

## §5 代码结构

```
backtest/
├── strategies/
│   ├── deepseek_strategy.py      # 策略主文件（CC 新建）
│   └── deepseek_strategy_v0.py   # 基础版对比（CC 新建）
├── tests/
│   └── test_deepseek_strategy.py # 单元测试（CC 新建）
└── config/
    └── deepseek_config.yaml      # 参数配置（CC 新建）
```

### 5.1 deepseek_strategy.py 接口要求

```python
class DeepSeekStrategy:
    def __init__(self, config: dict):
        pass

    def calculate_signals(self, context: ContextInfo) -> pd.DataFrame:
        """
        返回 DataFrame，列：
        - code: 股票代码
        - signal: 1=买入, 0=持有, -1=卖出
        - score: 综合评分（可选）
        """
        pass

    def get_params(self) -> dict:
        """返回当前参数，用于日志记录"""
        pass
```

---

## §6 验收标准

- [ ] 回测能正常跑通，无运行时错误
- [ ] 所有 5 个版本（V0-V4）都有回测结果
- [ ] 参数敏感性分析完成，输出表格
- [ ] 输出回测报告到 `D:\QMT_STRATEGIES\trade_reports\deepseek_backtest_YYYYMMDD.md`
- [ ] 报告包含：年化收益、最大回撤、夏普、胜率、交易次数、月度收益热力图
- [ ] 文件编码 GBK（`file deepseek_strategy.py` 显示 GBK）
- [ ] `validate_qmt_file.py` 6 项 ALL PASS

---

## §7 Boundaries & 约束

1. **不修改** 回测框架核心代码，只新增策略文件
2. **不引入** 外部数据源，使用回测框架已有的数据接口
3. **不使用** 通达信专有函数（SCR.SCR、PPART、WINNER、L2_AMO、CONST、DYNAINFO）
4. **Python 3.6.8 兼容**：不用 `dict[str,...]`、`str|None`、`:=`、`match-case`
5. **COST 函数**：QMT 可能不支持，如果不可用则跳过筹码条件，在报告中注明
6. **所有参数** 使用 `deepseek_config.yaml` 管理，不在代码中硬编码
7. **回测报告** 放在 `D:\QMT_STRATEGIES\trade_reports\` 下，文件名含日期
