# SPEC: 回测工厂通用化讨论稿（v0.2/v0.3 架构复盘）

> 状态：**讨论稿**，未拍板。本 SPEC 不是工程任务单，是给 Hermes / RS / MIMO / DeepSeek Pro / Doubao CIO 等相关 Agent 的**讨论分发文档**。
> 发起人：诚哥
> 整理：CC
> 日期：2026-06-23

---

## 一、诚哥的原话与意图

> "你建的回测工厂是绑定 6+2 评分器来做的吗，还是不管什么策略来都是可以用的？"
>
> "那这和我当初建回测工厂的初衷不一样了，这样，现在有回测工厂的详细说明吧，我研究一下，怎么样才可以不同想法，策略都能用这回测工厂，而不是绑定某个策略。"

### 意图拆解

1. **当初建回测工厂的初衷是"通用回测工厂"**——任何想法、任何策略都能丢进来跑。
2. **当前 v0.2/v0.3 实际形态偏离了初衷**——架构上还分层（engine / strategy_core / data_tools），但 frozen contract 把 6+2 评分体系的语义焊死进了接口，换策略要改 import、改 schema。
3. **要做的事**：复盘当前架构、识别"焊死点"、提出通用化改造方案，让回测工厂支持**多策略可插拔**。
4. **本 SPEC 的角色**：先把问题、现状、改造方向写清楚分发给相关角色讨论，**不直接开工**。

---

## 二、当前架构现状（CC 复盘）

### 2.1 已 freeze 的接口

文件：`agent_hub/2026-06-13_backtest_mvp/03_interface_freeze.md`

```
DailyBacktestEngine（通用主循环）
        ↓ 调用
evaluate_day(8 个固定参数)  ← frozen contract
        ↓ 内部
score_universe → make_decision
        ↓
StrategyDecision（固定 6 键 dict）
```

`evaluate_day` 8 参：

```
current_date, market_window, positions, cash, universe,
account_state, strategy_config, aux_data
```

`StrategyDecision` 6 键：

```
sell_decisions, buy_candidates, target_positions,
blocked_candidates, diagnostics, logs
```

`diagnostics` 内部嵌套：

```
diagnostics:
  scores: {}                  # 6+2 打分字典
  filter_counts:              # 10 个 6+2 专用过滤计数
    blocked_min_score
    blocked_min_core
    blocked_max_bias5
    blocked_max_daily_pct
    blocked_already_held
    blocked_limit_up
    blocked_suspended
    blocked_insufficient_history
    candidate_total
    candidate_passed
  warnings: []
  trigger_counts:             # 6+2 卖出/换仓触发计数
    early_stop, early_kick, stop_loss,
    score_drop, replace, warning, confirm
```

### 2.2 焊死点（让回测工厂"实际只能跑 6+2"的根源）

| 焊死点 | 位置 | 影响 |
|---|---|---|
| **A. 策略硬 import** | `backtest/strategy_core/interface.py` 27-28 行 | `evaluate_day` 直接 import `scoring_adapter.score_universe` + `decision.make_decision`，没有 strategy registry，换策略要改源码 |
| **B. diagnostics schema 是 6+2 语义** | `interface.py` `make_empty_decision()` 39-70 行 | `filter_counts` 10 个 key、`trigger_counts` 7 个 key 全是 6+2 专有名词。换策略要么硬填 0、要么改 schema 破坏 freeze |
| **C. 配置 schema 6+2 化** | `configs/baseline.yaml` 等 | `min_score` / `min_core` / `max_bias5` / `max_daily_pct` 字段直接出现在配置里 |
| **D. trading model 写死 next_open** | `daily_engine.py` 10-17 行注释 | T 日 close 信号 → T+1 open 成交，只支持组合回测范式，不支持事件研究 / 因子 IC / 当日成交 |
| **E. 输出 schema 假设"组合 P&L"** | `04_output_schema_freeze.md` trades/equity/positions | 默认产物是持仓-成交-净值三件套，事件统计/因子研究用不上 |

### 2.3 影响实例

近期两个真实例子证明了"焊死"的痛：

1. **P2.1.b full-A PIT 终审**：6+2 在全 A PIT 口径下失效，Hermes 判定阶段收口。但此时回测工厂跟 6+2 已经强绑定，**无法快速切换到另一套打分体系验证**——只能等"重新定义 6+2 或替换为新信号体系"。
2. **算力 P0 验证任务**（SPEC_compute_power_main_uptrend_p0_validation）：本来是"事件触发后跟踪 T+1~T+40 标签"的统计任务，CC 评估后认为**回测工厂的 engine/strategy_core 完全用不上**，只能复用 data_tools 数据底座，事件循环要自己写。

---

## 三、初衷 vs 现状的 Gap（需要讨论的核心问题）

### Q1：回测工厂的定位是什么？

候选：

- **A. 组合回测工厂**：只做"在交易日做买卖决策→组合 P&L 模拟"这一类任务。事件研究 / 因子 IC / 概率统计另起 framework。
- **B. 通用研究工厂**：组合回测、事件统计、因子检验、概率分布全部纳入。引入"研究范式"维度（paradigm: portfolio / event_study / factor_ic / ...）。
- **C. 二层架构**：底层是"数据底座 + 时间轴循环"通用，上层按范式分模块；当前 engine 降级为"组合范式"的实现之一。

**诚哥需要拍板这个**。后续所有架构方案都从这里分叉。

### Q2：策略如何插拔？

候选：

- **A. Strategy Registry**：`@register_strategy("ima_uptrend_v31")` 装饰器；配置写 `strategy: "ima_uptrend_v31"`；引擎按名字查表。
- **B. Entry Point / 动态 import**：配置写 `strategy_module: "backtest.strategies.ima_uptrend_v31"`，引擎 `importlib` 加载。
- **C. Plugin 目录约定**：`backtest/strategies/*/strategy.py` 自动扫描，每个目录是一个策略。

### Q3：StrategyDecision 怎么分层？

候选：

- **A. 双层结构**：内核字段（任何策略都有的：buy/sell/blocked/logs）+ 策略私有字段（diagnostics.strategy_specific.*）。
- **B. 完全自由**：内核只定义 `actions: [{type:'buy'|'sell', code, price, ...}]`，其他全交给策略。
- **C. 范式驱动**：portfolio 范式有一套 schema，event 范式另一套，factor 范式又另一套。

### Q4：6+2 怎么平滑剥离？

候选：

- **A. 原地降级**：把 `strategy_core/` 下的 6+2 实现挪到 `strategy_core/strategies/ima_uptrend_v31/`，跟其他策略平级，frozen contract 同步松绑。
- **B. 整体清空重写**：6+2 + frozen contract 一起作废，从头设计通用接口，6+2 作为第一个 reference 实现重新实现。
- **C. 双轨过渡**：新版接口跟旧版 frozen 并存一段时间，6+2 跑旧接口，新策略跑新接口，6+2 拖到下个大版本再迁。

### Q5：trading model 抽象到哪个层级？

候选：

- **A. 配置项**：`trading_model: next_open | same_close | event_trigger`，引擎主循环按配置切换。
- **B. 策略自选**：策略声明自己用什么 model，引擎适配。
- **C. 不动**：只支持 next_open，需要其他模型的研究任务自己写循环。

### Q6：是否需要保留向后兼容？

- 已有产物（P2 core100 / P2.1 intra-pool / P2.1.b full-A PIT）是否需要在新架构下能重跑？
- 已有配置文件（15 个 yaml）是否需要兼容？
- 还是允许大版本切换、旧产物冻结仅作历史参考？

---

## 四、复盘材料清单（讨论参与者必读）

### 必读

| 文件 | 看点 | 行数 |
|---|---|---|
| `agent_hub/回测工厂使用说明书.md` | **用户视角总览**——对照初衷 | 691 |
| `agent_hub/2026-06-13_backtest_mvp/00_brief.md` | 立项时的需求 brief | 434 |
| `agent_hub/2026-06-13_backtest_mvp/99_decision.md` | freeze 当时的决策记录 | 139 |
| `agent_hub/2026-06-13_backtest_mvp/03_interface_freeze.md` | **焊死点的根源**——8 参 + 6 键 + 10 个 6+2 filter | 394 |
| `agent_hub/2026-06-13_backtest_mvp/04_output_schema_freeze.md` | 输出产物 schema | 519 |
| `agent_hub/2026-06-13_backtest_mvp/90_hermes_summary.md` | freeze 阶段 Hermes 总结 | - |

### 可选

| 文件 | 看点 |
|---|---|
| `agent_hub/2026-06-13_backtest_mvp/05a_perf_baseline.md` | 性能基线 |
| `agent_hub/2026-06-13_backtest_mvp/05d_phase3_engine_acceptance.md` | engine 主循环设计 |
| `agent_hub/2026-06-14_backtest_v03/` 整目录 | v0.3 阶段 P0~P2.1.b 演进 |
| `backtest/strategy_core/interface.py` | 130 行实际 frozen 接口代码 |
| `backtest/engine/daily_engine.py` | 实际引擎实现 |
| `specs/SPEC_BACKTEST_MVP_OFFLINE_FACTORY_v0.2.md` | v0.2 SPEC |
| `specs/SPEC_BACKTEST_FACTORY_V0.3_DATA_EXPANSION_AND_VALIDATION.md` | v0.3 SPEC |

---

## 五、参与角色与讨论流程

参与角色按 `docs/HERMES_PROFILES.md` 准确命名，分两层：**Hermes 内部 profile** 与**外部 Agent**。

### 5.1 核心讨论团（Hermes profile）

由 Hermes `default` 调度，并行短会收集意见。

| Profile | 模型 | 视角 | 重点回答 |
|---|---|---|---|
| `default` | deepseek-v4-flash | Hermes 主控/调度官/汇总 | 全 Q（不出立场，最后汇总） |
| `deepseek-quant-scientist-pro` | deepseek-v4-pro | 高级量化科学家/抽象设计 | Q1 定位 / Q3 Decision 分层 / Q5 trading model |
| `minimax-agent-architect` | minimax-m3 | Agent 架构师/可插拔设计 | Q2 策略插拔 / Q3 Decision 分层 / Q5 trading model |
| `deepseek-quant-review-flash` | deepseek-v4-flash | 量化红队/快速审查 | 全 Q（挑刺、找漏洞、frozen 风险） |
| `doubao-cio` | doubao-seed-2.0-pro | 投资总监/优先级拍板 | Q1 定位 / Q6 兼容性 |
| `doubao-code-planner` | doubao-seed-2.0-code | 工程任务规划/步骤拆解 | Q4 6+2 剥离 / Q6 兼容性 / 阶段划分 |

### 5.2 外部 Agent

| Agent | 视角 | 介入时机 |
|---|---|---|
| **RS**（Reasonix） | 研究端实际使用方——"我希望工厂能让我做什么" | Round 1，与核心讨论团并行 |
| **MIMO** | 工程改造方——具体实现成本与红线 | **讨论阶段不入**；拍板后接重构 SPEC |
| **CC** | SPEC 整理/讨论协调员/最终重构 SPEC 起草 | 全程，但不出架构立场 |

### 5.3 不动用的 profile（明示给 Hermes default 避免乱调）

- `glm-research-analyst`：架构议题不是行业研究
- `doubao-altdata-lite`：另类数据无关
- `kimi-code-reviewer` / `kimi-general-research`：留给重构 SPEC 实施后做事后审查
- `doubao-code-lite`：轻量代码初筛，本议题用不上
- `minimax-agent-architect-lite`：本议题用全量 `minimax-agent-architect`
- `agnes-quant-review`：不可用

### 5.4 讨论流程

参考 PROFILES 第四节"常用调用组合 2/3"的标准模式：**Pro 推导 → Architect 设计 → Flash 红队 → CIO 拍板 → Hermes 汇总**。

```
Round 0  CC 把 SPEC 交付 Hermes default
         Hermes default 阅读，确认讨论范围与边界，不出立场

Round 1  Hermes default 并行短会（4 个 profile 独立写意见，互不可见）
         - deepseek-quant-scientist-pro  → 出抽象设计建议
         - minimax-agent-architect       → 出可插拔架构建议
         - doubao-cio                    → 出定位与优先级建议
         - doubao-code-planner           → 出剥离 6+2 的工程拆解与阶段划分
         同时 CC 协调 RS 独立写"研究端使用诉求"

Round 2  Hermes default 汇齐 Round 1 五份意见
         → 喂给 deepseek-quant-review-flash 做红队挑刺
         红队产出"风险与漏洞清单"

Round 3  Hermes default 与 deepseek-quant-scientist-pro 联合复盘
         → 出"决策建议草稿"，含每个 Q 的主选/备选/不推荐

Round 4  诚哥拍板
         → CC 起正式重构 SPEC（含 phase 划分、frozen 替换流程、迁移路线）
         → MIMO 接活
```

### 5.5 每个角色至少回答

1. 对 Q1~Q6 的立场（选 A/B/C 或自创方案，并给理由）
2. 当前架构最痛的一点是什么？
3. 重构如果做，最不能破的红线是什么？
4. 推荐分几个阶段做？

### 5.6 输出文件结构

```
agent_hub/2026-06-23_backtest_generalization/
  00_brief.md                              # 本 SPEC 副本 + 讨论目标
  round1_deepseek_pro_意见.md
  round1_minimax_architect_意见.md
  round1_doubao_cio_意见.md
  round1_doubao_code_planner_意见.md
  round1_RS_研究端使用诉求.md
  round2_deepseek_flash_红队挑刺.md
  round3_hermes_default_决策建议草稿.md
  round4_诚哥拍板.md                        # 拍板记录
```

### 5.7 收敛标准

- Q1~Q6 每题必须有"主选 + 备选 + 不推荐"的明确结论
- 主选方案需至少 3/5 角色（核心讨论团 Pro/Architect/CIO/Code Planner + RS）支持，否则升级诚哥拍板
- 新增 Q7+ 问题需 ≥2 个角色支持才纳入正式讨论
- 红队挑刺为强制环节，不可跳过
- 最终产物：决策建议草稿（不是工单），由诚哥决定是否进入重构 SPEC

---

## 六、CC 的初步倾向（仅供参考，不作结论）

不绑定具体答案，但 CC 视角的几个判断：

- Q1 倾向 **C（二层架构）**：保护 v0.2 已经投入的工程量（data_tools / paths / 报告产物），上层按范式分模块演进，避免推倒重来。
- Q2 倾向 **A（Registry）**：明确、可测、易于 Hermes 自动 review。
- Q3 倾向 **A（双层 StrategyDecision）**：内核字段保 frozen 不破坏，6+2 字段挪到 `diagnostics.strategy_specific.ima_uptrend_v31.*`。
- Q4 倾向 **A（原地降级）**：6+2 是 reference 实现，不需要重写。
- Q5 倾向 **A（配置项）**：trading model 是范式级别的参数，不该藏在 engine 里。
- Q6：旧产物冻结仅作历史参考，**不强求新架构重跑**——P2.1.b 已经证伪 6+2，没必要花成本兼容。

但以上是 CC 的工程视角，**最终方向由 Hermes / 诚哥拍板**。

---

## 七、本 SPEC 边界

### Always

- 仅做**讨论分发**，不做工程改造
- 输出位置：`D:\QMT_STRATEGIES\agent_hub\2026-06-23_backtest_generalization\`
- 不改动 `backtest/` 任何文件
- 不破坏当前 frozen contract（讨论阶段）

### Never

- 不在讨论结束前启动重构
- 不修改 6+2 实现
- 不影响当前冻结期 / 模拟盘 / 实盘策略
- 不要求 MIMO 立即开工

---

## 八、下一步

1. 诚哥确认 SPEC 措辞与参与角色名单
2. CC 把本 SPEC 复制到 `agent_hub/2026-06-23_backtest_generalization/00_brief.md`
3. CC 交付 Hermes `default`，由 Hermes `default` 按 5.4 节流程调度：
   - Round 1 并行调度 4 个 Hermes profile（Pro / Architect / CIO / Code Planner）
   - CC 同步协调 RS 独立写研究端意见
4. Hermes `default` 推进 Round 2（Flash 红队）与 Round 3（决策建议草稿）
5. 诚哥拍板（Round 4）
6. 拍板后由 CC 起**正式重构 SPEC**（含 phase 划分、frozen 替换流程、迁移路线），交 MIMO 执行

---

*本 SPEC 由 CC 整理自 2026-06-23 与诚哥的讨论。如有理解偏差，以诚哥原话为准。*
*角色命名遵循 `docs/HERMES_PROFILES.md`，讨论流程参考 PROFILES 第四节"常用调用组合 2/3"。*
