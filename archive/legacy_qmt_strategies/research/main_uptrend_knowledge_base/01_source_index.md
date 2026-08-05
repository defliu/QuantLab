# 资料来源与已有报告索引

日期：2026-06-14

---

## 一、诚哥提供的一手资料

### 1. 通达信筹码密集启动突破公式

位置：已整理到：

```text
D:/QMT_STRATEGIES/research/main_uptrend_knowledge_base/02_tdx_chip_breakout_formula.md
```

核心思想：

```text
筹码密集 → 突破密集区顶部 → 蓄势 → 均线多头 → MA5 角度达标 → 阳线 → 排除急拉坑底
```

---

## 二、Mimo 相关报告

### 1. IMA V3.1 v2 信号研究报告

```text
D:/QMT_STRATEGIES/agent_hub/2026-06-13_ima_main_uptrend/90_mimo_final_signal_research_report_v2.md
```

定位：修复前视偏差后，评估 SC 6/7/8/9 阈值。

核心结论：

```text
SC≥6 的 10 日胜率 55.7%，信号有效性存在但较弱，样本期短，不可作为最终结论。
```

### 2. IMA V3.1 H1 price_range_proxy 对照报告

```text
D:/QMT_STRATEGIES/agent_hub/2026-06-13_ima_main_uptrend/93_mimo_signal_research_v3.md
```

定位：H1 disabled vs H1 price_range_proxy 对照。

核心结论：

```text
price_range_proxy 信号少，但 10 日胜率和尾部风险改善，适合作为筹码/区间收敛方向的研究线索。
```

### 3. Hermes 对 IMA v3 的验收

```text
D:/QMT_STRATEGIES/agent_hub/2026-06-13_ima_main_uptrend/94_hermes_validation_accept_v3.md
```

定位：研究线索通过，不进入实盘/模拟。

---

## 三、回测工厂 v0.3 相关基础设施报告

### 1. P2.1.b full-A PIT 最终验收

```text
D:/QMT_STRATEGIES/agent_hub/2026-06-14_backtest_v03/18_hermes_p2_1b_final_acceptance.md
```

结论：当前 6+2 作为全 A PIT 选股器基本失效。

### 2. A/B/D 阶段验收

```text
D:/QMT_STRATEGIES/agent_hub/2026-06-14_backtest_v03/22_hermes_a_b_d_stage_acceptance.md
```

结论：benchmark / huicexitong / universe 约束均已验证，数据底座可用于下一代策略研究。

### 3. E0 下一代评分设计

```text
D:/QMT_STRATEGIES/agent_hub/2026-06-14_backtest_v03/23_e0_next_scoring_design.md
D:/QMT_STRATEGIES/agent_hub/2026-06-14_backtest_v03/24_hermes_e0_design_acceptance.md
```

结论：E0 设计通过，但不进入 E1 实现；turnover channel 是候选方向。

---

## 四、待诚哥补充资料

1. 历史每日通达信选股池。
2. 当前 QMT 读取池文件路径。
3. 通达信公式运行时间：盘中 / 收盘后 / 盘前。
4. 每天池子规模。
5. 典型成功案例。
6. 典型失败案例。
7. 买点截图 / 卖点截图。
8. 诚哥对“真主升浪”和“假主升浪”的经验判断。
