# SPEC: 回测工厂 v0.4 通用化重构 · 第一阶段（Phase 1）

> 文档类型：正式重构 SPEC
> 拍板依据：`agent_hub/2026-06-23_backtest_generalization/round4_诚哥拍板.md`
> 起草：CC
> 日期：2026-06-23
> 状态：**Phase 1 已落地（4 commit + 2 hotfix 已 ff-merge 至 master，commit ee9aa6b）**
> 阶段：Phase 1（第一阶段）
> 风险等级：中（涉及 frozen contract 替换，但 6+2 实现不动）

---

## 一、Objective

把回测工厂从"6+2 专用工具"改造成"**组合回测通用底座 + 可插拔策略**"。

第一阶段只做三件事：

1. 引入 **Strategy Registry**，解除 engine 与 6+2 的硬绑定。
2. 把 **6+2 降级为第一个 reference strategy**，物理迁出 frozen contract 核心。
3. **trading_model 配置化**，策略声明 `allowed_trading_models`，启动时校验。

事件研究、因子 IC、多范式抽象层**全部 Phase 2+ 再说**，本阶段不做。

---

## 二、Scope

### 2.1 In Scope（本阶段做）

- 新建 `backtest/strategies/` 目录，提供 Strategy Registry。
- 把 `backtest/strategy_core/scoring_adapter.py` / `decision.py` / `ima_uptrend_v31.py` / `risk_adapter.py` 物理迁移到 `backtest/strategies/production/ima_uptrend_v31/`。
- 改造 `evaluate_day` 8 参接口：保留参数顺序与名称，但内部不再硬 import，改为从 registry 取策略实现。
- 改造 `StrategyDecision.diagnostics`：内核字段保留，6+2 专有字段（10 个 filter_counts + 7 个 trigger_counts + scores）下沉到 `diagnostics.strategy_specific.ima_uptrend_v31.*`。
- 引入 `ALLOWED_TRADING_MODELS` 策略级声明 + engine 启动校验。
- 改写一份 baseline yaml（`baseline.yaml`）作为新 schema 示例。
- 写一个一次性 yaml 迁移脚本（不长期兼容）。
- 一致性验证：选 1 个 yaml 跑迁移前后对比，核心产物完全一致。

### 2.2 Out of Scope（本阶段不做）

- ❌ 事件研究范式（event_study）
- ❌ 因子 IC 范式（factor_ic）
- ❌ 多 paradigm 抽象层
- ❌ 通用 trading_model 引擎抽象 —— **永久不做**。工厂只提供 `next_open` 单一日 K 撮合，撮合时点决策属于策略代码内部职责（与 QMT 实盘 passorder 调用时机一致语义）。`ALLOWED_TRADING_MODELS` hook 保留作零成本对称声明，实际只取 `["next_open"]`。详见 §九。
- ❌ 自动迁移全部 15 个旧 yaml（只迁 baseline 1 个做样本，其余按需手动迁）
- ❌ 重写 6+2 任何逻辑
- ❌ 改 6+2 的字段语义
- ❌ 改任何 QMT 实盘/模拟盘策略代码

---

## 三、Architecture

### 3.1 二层结构（仅 Phase 1 落地的部分）

```
┌─────────────────────────────────────────────────┐
│  上层（组合回测范式）                            │
│  - DailyBacktestEngine                          │
│  - Portfolio / Execution / Metrics              │
│  - Report                                       │
└─────────────────────────────────────────────────┘
                    ↓ 通过 registry 调用
┌─────────────────────────────────────────────────┐
│  策略层（可插拔）                                │
│  backtest/strategies/                           │
│    production/                                  │
│      ima_uptrend_v31/   ← Phase 1 唯一 reference│
│    research/             ← Phase 1 留空目录     │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│  底层（数据底座）—— 本阶段不动                   │
│  - backtest/data_tools/                         │
│  - backtest/paths.py                            │
│  - DuckDB / huicexitong / PIT manifest          │
└─────────────────────────────────────────────────┘
```

### 3.2 Strategy Registry 设计（最简方案）

`backtest/strategies/__init__.py`：

```python
# coding: utf-8
"""Strategy Registry —— 最简装饰器实现，不引入插件框架。"""

_REGISTRY = {}

def register_strategy(name):
    """装饰器：在 strategy 模块顶层注册 evaluate_day。

    name 格式：'<category>/<strategy_id>'，例如 'production/ima_uptrend_v31'
    """
    def _wrap(evaluate_fn):
        if name in _REGISTRY:
            raise ValueError("strategy already registered: " + name)
        _REGISTRY[name] = evaluate_fn
        return evaluate_fn
    return _wrap

def get_strategy(name):
    if name not in _REGISTRY:
        raise KeyError(
            "strategy not found: " + name +
            "; registered: " + ",".join(sorted(_REGISTRY.keys()))
        )
    return _REGISTRY[name]

def list_strategies():
    return sorted(_REGISTRY.keys())

# 触发注册：import 子包即可，子包顶层模块包含 @register_strategy
from backtest.strategies.production import ima_uptrend_v31  # noqa: F401
```

策略侧（`backtest/strategies/production/ima_uptrend_v31/__init__.py`）：

```python
from backtest.strategies.production.ima_uptrend_v31.strategy import evaluate_day  # noqa
```

`strategy.py` 顶层：

```python
from backtest.strategies import register_strategy

ALLOWED_TRADING_MODELS = ["next_open"]

@register_strategy("production/ima_uptrend_v31")
def evaluate_day(current_date, market_window, positions, cash,
                 universe, account_state, strategy_config, aux_data):
    # 原 backtest/strategy_core/interface.py 的逻辑搬进来
    ...
```

Engine 侧改造（`daily_engine.py`）：

```python
# 旧：
# from backtest.strategy_core.interface import evaluate_day

# 新：
from backtest.strategies import get_strategy, list_strategies

# 在 DailyBacktestEngine.__init__ 里：
strategy_name = config["strategy"]
self._evaluate_day = get_strategy(strategy_name)

# 校验 trading_model
import importlib
mod = importlib.import_module(_strategy_module_path(strategy_name))
allowed = getattr(mod, "ALLOWED_TRADING_MODELS", ["next_open"])
if config["trading_model"] not in allowed:
    raise ValueError(
        "trading_model=" + config["trading_model"] +
        " not in strategy.ALLOWED_TRADING_MODELS=" + str(allowed)
    )
```

### 3.3 StrategyDecision 分层（最小破坏改造）

**内核字段（任何策略都必填，schema 不变）：**

```
sell_decisions, buy_candidates, target_positions,
blocked_candidates, logs
```

**diagnostics 重构：**

```python
diagnostics = {
    # —— 通用字段（任何策略都需要）——
    "warnings": [],
    "candidate_total": 0,
    "candidate_passed": 0,

    # —— 策略私有字段（按策略名 namespace）——
    "strategy_specific": {
        "ima_uptrend_v31": {
            "scores": {},
            "filter_counts": {
                "blocked_min_score":            0,
                "blocked_min_core":             0,
                "blocked_max_bias5":            0,
                "blocked_max_daily_pct":        0,
                "blocked_already_held":         0,
                "blocked_limit_up":             0,
                "blocked_suspended":            0,
                "blocked_insufficient_history": 0,
            },
            "trigger_counts": {
                "early_stop":  0,
                "early_kick":  0,
                "stop_loss":   0,
                "score_drop":  0,
                "replace":     0,
                "warning":     0,
                "confirm":     0,
            },
        }
    },
}
```

**取数辅助函数**（避免下游每个 report 都写长路径）：

```python
def get_strategy_diag(decision, strategy_name, key, default=None):
    return (decision.get("diagnostics", {})
                    .get("strategy_specific", {})
                    .get(strategy_name, {})
                    .get(key, default))
```

### 3.4 配置 schema（v0.4 新格式）

```yaml
# baseline.yaml（v0.4 新格式示例）
strategy: production/ima_uptrend_v31
trading_model: next_open

strategy_params:
  min_score: 60
  min_core: 4
  max_bias5: 0.08
  max_daily_pct: 0.05
  # ... 其他 6+2 专用参数

universe: ...
date_range: ...
benchmark: sh000300
```

关键变化：
- 顶层新增 `strategy` 与 `trading_model` 两个 key。
- 原 `min_score` / `min_core` / `max_bias5` 等 6+2 参数下沉到 `strategy_params`。
- 其他字段（universe / date_range / benchmark）保留位置。

---

## 四、Implementation

### 4.1 改动文件清单

**新增：**

| 文件 | 作用 |
|---|---|
| `backtest/strategies/__init__.py` | Registry 实现（约 30 行） |
| `backtest/strategies/production/__init__.py` | 空 |
| `backtest/strategies/production/ima_uptrend_v31/__init__.py` | 暴露 evaluate_day |
| `backtest/strategies/production/ima_uptrend_v31/strategy.py` | 注册入口 + ALLOWED_TRADING_MODELS（薄壳，逻辑沿用 ima_uptrend_v31.py） |
| `backtest/strategies/research/__init__.py` | 空（Phase 2 用） |
| `backtest/scripts/migrate_yaml_v03_to_v04.py` | 一次性迁移脚本 |

**移动（git mv，保留 history）：**

| 旧路径 | 新路径 |
|---|---|
| `backtest/strategy_core/ima_uptrend_v31.py` | `backtest/strategies/production/ima_uptrend_v31/ima_uptrend_v31.py` |
| `backtest/strategy_core/scoring_adapter.py` | `backtest/strategies/production/ima_uptrend_v31/scoring_adapter.py` |
| `backtest/strategy_core/decision.py` | `backtest/strategies/production/ima_uptrend_v31/decision.py` |
| `backtest/strategy_core/risk_adapter.py` | `backtest/strategies/production/ima_uptrend_v31/risk_adapter.py` |

**改动（最小化）：**

| 文件 | 改动 |
|---|---|
| `backtest/strategy_core/interface.py` | 改为薄 shim：从 registry 取 production/ima_uptrend_v31 转调；保留 `make_empty_decision()` 但新 schema |
| `backtest/strategy_core/enums.py` | 保留位置不动（共享枚举） |
| `backtest/engine/daily_engine.py` | 改为从 registry 取策略 + 校验 trading_model（约 20 行改动，不动主循环） |
| `backtest/configs/baseline.yaml` | 改为 v0.4 新格式（作为样本） |

**冻结不动：**

- `backtest/data_tools/` 全部文件
- `backtest/paths.py`
- `backtest/engine/portfolio.py` / `execution.py` / `metrics.py` / `report.py` / `hashing.py`
- 其余 14 个 yaml（不迁，按需手动）

### 4.2 frozen contract 处理

旧 `agent_hub/2026-06-13_backtest_mvp/03_interface_freeze.md` 仍作历史参考；新增：

`agent_hub/2026-06-23_backtest_generalization/05_interface_freeze_v04.md`

内容：
- `evaluate_day` 8 参签名与名称 → **保留不变**（兼容现有策略实现）
- `StrategyDecision` 顶层 6 键 → **保留不变**
- `diagnostics` 子结构 → **变更**（通用字段提升 + 私有字段 namespace 化）
- `ALLOWED_TRADING_MODELS` → **新增**策略级声明
- registry 命名空间约定：`<category>/<strategy_id>`，category ∈ {production, research}

### 4.3 不引入的复杂度

明示不做：

- ❌ entry_points / importlib 动态加载（用 explicit import 触发 registry，简单可控）
- ❌ 抽象基类 BaseStrategy（装饰器够用）
- ❌ 多 paradigm 接口（Phase 1 只有 portfolio 一种）
- ❌ 配置 schema 校验框架（pydantic / cerberus 等）（手写 assert 够用）
- ❌ 插件热加载

---

## 五、Migration

### 5.1 6+2 迁移步骤（CC 视角拆解，MIMO 执行）

```
Step 1  git mv 四个文件到 backtest/strategies/production/ima_uptrend_v31/
        - 同步改这些文件内部的 import 路径

Step 2  新建 strategy.py（薄壳）
        - @register_strategy("production/ima_uptrend_v31")
        - ALLOWED_TRADING_MODELS = ["next_open"]
        - 内部转调原 ima_uptrend_v31 主函数

Step 3  改 backtest/strategy_core/interface.py 为 thin shim
        - 保留 evaluate_day 8 参签名
        - 内部从 registry 取 production/ima_uptrend_v31
        - 重写 make_empty_decision 输出 v0.4 schema
        - 加 DeprecationWarning：建议直接用 registry

Step 4  改 daily_engine.py
        - 删除 from backtest.strategy_core.interface import evaluate_day
        - 改为从 config["strategy"] 取 + 校验 trading_model
        - 主循环不动

Step 5  改 baseline.yaml 为 v0.4 格式

Step 6  迁移 6+2 的字段输出位置（在 strategy.py 内）
        - 原 diagnostics.scores → diagnostics.strategy_specific.ima_uptrend_v31.scores
        - 原 diagnostics.filter_counts → diagnostics.strategy_specific.ima_uptrend_v31.filter_counts
        - 原 diagnostics.trigger_counts → diagnostics.strategy_specific.ima_uptrend_v31.trigger_counts
        - 提升 candidate_total / candidate_passed / warnings 到通用层

Step 7  改 report.py 取数路径
        - 旧：decision["diagnostics"]["scores"]
        - 新：get_strategy_diag(decision, strategy_name, "scores", {})

Step 8  写迁移脚本 migrate_yaml_v03_to_v04.py
        - 输入：1 个旧 yaml 路径
        - 输出：v0.4 新 yaml
        - 不做自动批量

Step 9  跑测试 + 一致性验证（见 §六）
```

### 5.2 旧 yaml 处理策略

- **baseline.yaml**：本阶段手动改为 v0.4 格式，作为新格式样本。
- **其余 14 个 yaml**：保留原状不动，标注 deprecated。需要重跑时调 migrate 脚本一次性迁移。
- **迁移脚本只迁 schema**，不改语义。

### 5.3 旧产物处理

- P2 core100 / P2.1 / P2.1.b / 历史 results/ 目录 → **全部冻结**，不强求重跑。
- 在 `D:\QMT_STRATEGIES\backtest\results\README.md` 加一行说明：v0.3 及以前产物为历史参考，新产物以 v0.4 schema 为准。

### 5.4 里程碑节奏（无人值守 / CC 代验收模式）

**总原则**：MIMO 分三个里程碑交付，**每个里程碑独立 commit + CC 独立验收**，绝不一把梭。

| 里程碑 | 覆盖 Step | 交付物 | 通过条件 | 预计墙钟 |
|---|---|---|---|---|
| **Milestone A** | Step 1-5 | registry + 6+2 物理迁移 + interface shim + engine 接 registry + baseline.yaml v0.4 | `test_strategy_registry.py` / `test_trading_model_validation.py` 全 PASS + engine 跑 baseline.yaml 不报错（不要求 sha256） | 1-2 天 |
| **Milestone B** | Step 6-7 | diagnostics namespace 化 + report.py 取数路径 + `get_strategy_diag` 辅助函数 | `test_strategy_decision_schema_v04.py` PASS + 全量 `backtest/tests/` PASS | 1 天 |
| **Milestone C** | Step 8-9 | migrate_yaml 脚本 + 一致性验证 + 性能对比 + 全部文档 | sha256 三件一致（trades/equity/positions 在 v0.3 master vs v0.4 分支 bit-identical）+ 性能 ≤ 105% + 全部测试 PASS + 验收报告 | 1-2 天 |

**关键约束（防 dirty 污染 / 防偷懒）**：

1. **分支**：MIMO 一律在 `feat/backtest-v04-phase1` 分支工作，A/B/C 各自独立 commit（commit message 用 `[MS-A]` / `[MS-B]` / `[MS-C]` 前缀）。
2. **commit 必须 `git add <具体文件>`，严禁 `git add .` / `git add -A` / `git add backtest/`**。
   - 当前 `backtest/` 有 19 个无关 dirty 文件（诚哥与其他工作未 commit 的内容），任何 `git add .` 都会把这些带进重构 commit，构成红线违规。
   - 参考历史教训：`[[cc-ticket-must-check-dirty-target-file]]`。
3. **每个里程碑跑完后 MIMO 必须 `git status --short` 输出贴回执**，让 CC 比对。
4. **MIMO 遇任何 fail 必须停**（参考 `[[mimo-must-stop-on-any-failure]]`），不得自判"无关"继续。
5. **里程碑 C 的 sha256 一致性验证不通过，不得继续推进、不得"差不多放行"**（参考 SPEC §8.2 Ask First）。
6. **诚哥不在线**：CC 代验收每个里程碑，CC 不放行不进入下一里程碑。

**派单顺序**：
```
[CC 起 Mimo_TODO_MS_A.md] → mimo run → CC 验收 → 通过则起 Mimo_TODO_MS_B.md → mimo run → CC 验收 → 通过则起 Mimo_TODO_MS_C.md → mimo run → CC 验收 → 早上交诚哥
```

---

## 六、Acceptance

### 6.1 一致性验证（最关键）

选 **baseline.yaml** 作为一致性验证样本：

```
Step A  在 master 当前 commit 跑 baseline.yaml → 保存产物 P_old
        - trades.csv / equity.csv / positions.csv / summary.json
        - 计算每个文件的 sha256

Step B  在 v0.4 重构分支跑同一 baseline.yaml（迁移后格式） → 保存产物 P_new
        - 同样的四个产物 + sha256

Step C  对比 P_old vs P_new：
        - trades.csv：完全一致（同时间、同 code、同方向、同价、同量）
        - equity.csv：完全一致（每日净值、现金、市值）
        - positions.csv：完全一致（每日持仓快照）
        - summary.json：除以下字段允许变更外，其他必须一致：
          * version 字段（_STRATEGY_CORE_VERSION）允许从 0.2.0 → 0.4.0
          * schema_version 字段允许变化
          * diagnostics_aggregate 内部路径允许从扁平改为 nested
          * 数值结果（return / sharpe / drawdown）必须 bit-identical
```

**通过门槛：**
- trades / equity / positions 三个文件 sha256 完全一致。
- summary.json 中所有数值结果完全一致（容差 0）。

### 6.2 性能不退化

- 同一台机器、同一份 baseline.yaml：
- 迁移后总耗时不超过迁移前 105%（5% 容差留给 registry import 开销）。

### 6.3 测试通过

`backtest/tests/` 全量跑通，**不允许**任何 test skip 或 xfail 新增。

新增测试（至少）：

- `test_strategy_registry.py`
  - register / get / list 正确性
  - 重复注册抛 ValueError
  - 未知名抛 KeyError
- `test_trading_model_validation.py`
  - 策略 ALLOWED_TRADING_MODELS=["next_open"] + config 写 next_open → 通过
  - 同上 + config 写 same_close → 启动 raise
- `test_strategy_decision_schema_v04.py`
  - `make_empty_decision()` 返回 v0.4 schema
  - 通用字段在顶层、私有字段在 strategy_specific.{name} 下
  - get_strategy_diag 取数正确
- `test_migrate_yaml_v03_to_v04.py`
  - 拿 baseline.yaml 旧版本测试迁移输出格式

### 6.4 文档同步

- 新增 `agent_hub/2026-06-23_backtest_generalization/05_interface_freeze_v04.md`
- `agent_hub/回测工厂使用说明书.md` 加一节 "v0.4 通用化与策略 registry"
- 旧 `agent_hub/2026-06-13_backtest_mvp/03_interface_freeze.md` 顶部加 deprecated 横幅，指向新 freeze 文件

---

## 七、Testing Strategy

### 7.1 必测场景

1. **registry 基本功能**：register / get / list / 重复 / 未知
2. **trading_model 校验**：正向通过、反向 raise
3. **schema v0.4 结构**：通用字段 + strategy_specific namespace
4. **一致性回归**：baseline.yaml 迁移前后 trades/equity/positions sha256 一致
5. **DeprecationWarning**：旧 import `from backtest.strategy_core.interface import evaluate_day` 触发 warning（不 raise）

### 7.2 验收报告必含

```
- 测试命令与全量 PASS 截图
- baseline.yaml 迁移前后产物 sha256 对照表
- 性能对比表（迁移前/后耗时）
- registry list_strategies() 输出
- 迁移脚本对 baseline.yaml 的输入输出对照
```

---

## 八、Boundaries

### 8.1 Always

- 保持简单：装饰器 registry + namespace 字段，不引插件框架。
- 6+2 实现逻辑**不动**，只搬位置 + 包薄壳。
- frozen contract 替换前后产物**必须 bit-identical**（一致性验证是硬门槛）。
- 文档同步更新。
- 在 `worklog/系统更新日志.md` 记录本次重构。

### 8.2 Ask First

- 如果一致性验证不通过且查不出原因 → 立即停止，向诚哥汇报，不要靠"差不多"放行。
- 如果性能退化超过 5% → 同上。
- 如果发现 6+2 内部存在隐藏的 IO / 全局状态 → 暂停搬迁，单独 spec 处理。
- 如果迁移脚本发现 yaml 字段命名冲突 → 单独 spec。

### 8.3 Never

- 不重写 6+2 任何逻辑。
- 不改 6+2 字段语义或名称（搬位置 ≠ 改语义）。
- 不引入 entry_points / pluggy / 抽象基类等"现代插件框架"。
- 不动 QMT 实盘/模拟盘策略代码。
- 不动 `backtest/data_tools/` 和 `paths.py`。
- 不长期兼容旧 yaml（不写运行时双格式适配）。
- 不在 Phase 1 实现 event_study / factor_ic / 多 paradigm 抽象。
- 不破坏冻结期红线（不接 passorder / xttrader / xtbp）。
- 不删除任何历史产物或历史文档。

---

## 九、阶段后续预留（不在本 SPEC 范围）

Phase 2+ 候选（仅记录方向，不开发）：

| 候选 | 触发条件 |
|---|---|
| event_study 范式 | RS / Hermes 明确需要事件统计 |
| factor_ic 范式 | 因子库扩展时 |
| 多 paradigm engine 抽象 | 至少两个范式并存时 |
| yaml-DSL 策略 | RS 三问答案倾向 yaml |
| 策略热加载 | 当注册策略 > 10 个时 |

**已剔除候选（2026-06-24 拍板）**：

- ~~trading_model 引擎扩展~~ —— 撮合时点是**策略代码内部职责**，与 QMT 实盘 passorder 调用时机一致。工厂永远只提供 `next_open`（信号 @ T 收盘 → 成交 @ T+1 开盘）作为唯一日 K 撮合假设；策略要别的时点（尾盘 / 当日 open / VWAP），自己在 evaluate_day 里按 `bar["close"]` / `bar["open"]` / 自定义算法决定目标价。
  - `ALLOWED_TRADING_MODELS` hook 保留，但实际只会取值 `["next_open"]`，作为零成本对称声明。

Phase 1 完成且稳定运行后再讨论。

---

## 十、交付物清单

完成 Phase 1 时 MIMO 应交付：

- [ ] `backtest/strategies/` 新目录（registry + 6+2 reference）
- [ ] `backtest/strategy_core/interface.py` 改为 shim（含 DeprecationWarning）
- [ ] `backtest/engine/daily_engine.py` 接 registry + 校验 trading_model
- [ ] `backtest/configs/baseline.yaml` 改为 v0.4 格式
- [ ] `backtest/scripts/migrate_yaml_v03_to_v04.py` 一次性迁移脚本
- [ ] 4 个新增测试文件全 PASS
- [ ] `backtest/tests/` 全量 PASS
- [ ] 一致性验证报告（baseline.yaml 迁移前后 sha256 对照）
- [ ] 性能对比报告
- [ ] `agent_hub/2026-06-23_backtest_generalization/05_interface_freeze_v04.md`
- [ ] `agent_hub/回测工厂使用说明书.md` 新增 v0.4 章节
- [ ] `worklog/系统更新日志.md` 一行记录

---

## 十一、风险与回滚

### 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| 一致性验证不通过 | 高 | sha256 硬门槛，不通过即停 |
| 性能退化 | 中 | 5% 容差，超出即停 |
| 隐藏的全局状态被 registry 破坏 | 中 | 测试场景 4 强制覆盖 |
| MIMO 误改 6+2 逻辑 | 中 | SPEC 明示 git mv + 薄壳，禁止改实现 |
| 旧 yaml 用户重跑老 baseline 失败 | 低 | 提供迁移脚本 + 文档说明 |

### 回滚策略

- 全程在 feature 分支开发（`feat/backtest-v04-phase1`）。
- 一致性验证不通过 → 分支不合入，回退到 v0.3 master。
- 不合入前不删除任何原文件（git mv 也只在 feature 分支生效）。

---

## 十二、签字栏

- [x] 诚哥确认：诚哥（2026-06-24，授权 CC 代验收 A/B/C 三里程碑 + 2 Hotfix + ff-merge 到 master）
- [ ] Hermes default 复核：____________
- [x] CC 起草：CC（2026-06-23）
- [x] MIMO 接单：mimo-auto（2026-06-24）

---

*本 SPEC 严格遵循拍板"保持简单"原则。一切超出 Phase 1 范围的诱惑都应推迟到 Phase 2+ 讨论。*
