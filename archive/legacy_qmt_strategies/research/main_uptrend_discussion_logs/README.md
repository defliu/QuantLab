# 主升浪重构讨论日志库

建立日期：2026-06-14
维护人：诚哥 / Hermes / CC / Mimo / RS
定位：记录主升浪 QMT 一体化重构过程中的讨论、决策、分歧、验收结论，保证后续可追溯。

---

## 一、日志库原则

1. 一个重大议题建一个 `agent_hub/YYYY-MM-DD_topic/` 讨论室。
2. Hermes 先写 `00_hermes_brief.md`，统一背景和问题。
3. CC / Mimo / RS 分别输出 review，不在聊天里散落。
4. Hermes 汇总成 `03_hermes_summary.md` 或 `04_decision.md`。
5. 只有在讨论收敛后才转 SPEC。

---

## 二、建议讨论室结构

```text
agent_hub/YYYY-MM-DD_main_uptrend_rebuild/
  00_hermes_brief.md
  01_cc_engineering_review.md
  02_mimo_strategy_review.md
  03_rs_batch_or_research_review.md      可选
  04_hermes_summary.md
  05_decision.md
```

---

## 三、当前讨论主题

```text
主升浪战法 QMT 一体化重构
```

核心问题：

1. 如何从通达信选股池 + QMT 打分，升级为 QMT 全流程策略。
2. 如何复刻或替代 PPART 筹码密集。
3. Mimo IMA / price_range_proxy 如何进入知识体系。
4. 现有 6+2 如何降级、拆解或重写。
5. 后续如何验证：历史通达信池、QMT 近似复刻、新主升浪评分。

---

## 四、当前讨论室

```text
D:/QMT_STRATEGIES/agent_hub/2026-06-14_main_uptrend_rebuild/
```
