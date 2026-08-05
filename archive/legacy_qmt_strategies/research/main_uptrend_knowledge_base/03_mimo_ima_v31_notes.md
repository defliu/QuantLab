# Mimo IMA V3.1 研究笔记

日期：2026-06-14
来源：MimoCode IMA 主升浪研究报告
定位：主升浪资料库中的“信号研究原型”，不是完整交易策略。

---

## 一、已有报告

```text
D:/QMT_STRATEGIES/agent_hub/2026-06-13_ima_main_uptrend/90_mimo_final_signal_research_report_v2.md
D:/QMT_STRATEGIES/agent_hub/2026-06-13_ima_main_uptrend/93_mimo_signal_research_v3.md
D:/QMT_STRATEGIES/agent_hub/2026-06-13_ima_main_uptrend/94_hermes_validation_accept_v3.md
```

---

## 二、当前定位

Mimo IMA V3.1 不是完整的：

```text
全 A 选股 → 打分 → 买入 → 卖出 → 风控
```

它更像：

```text
主升浪信号筛选 + SC 阈值研究 + H1 筹码/区间收敛 proxy 研究
```

可作为 QMT 主升浪一体化策略的资料模块。

---

## 三、关键结果

### 1. v2 修复前视偏差后

```text
SC≥6 10 日胜率约 55.7%
1 日 / 5 日胜率接近随机
样本期约 6 个月，不可作为最终结论
```

### 2. H1 price_range_proxy

```text
信号数量：513 → 22，过滤极其激进
10 日胜率：45.6% → 66.7%
最大单笔亏损：-25.0% → -12.1%
但样本太少，仅 22 条，统计意义不足
```

---

## 四、对主升浪重构的启发

1. `price_range_proxy` 与通达信 `PPART(90)` 的筹码密集思想接近。
2. “价格区间压缩 / 筹码收敛”可能是主升浪候选识别中的关键模块。
3. H1 不能直接作为强硬主配置，但适合进入候选层或辅助过滤层。
4. Mimo 的 IMA 不应整体塞进 6+2，而应拆出可验证因子。

---

## 五、后续研究问题

1. 用 huicexitong 真换手率替代 price_range_proxy 后，信号是否更稳定？
2. H1 与通达信 `PPART(90)` 历史池重合度如何？
3. H1 是否能作为“排除假突破/急拉坑”的辅助条件？
4. H1 在行业内排名后是否改善？
5. 是否应由 Mimo 参与主升浪一体化策略分层设计？
