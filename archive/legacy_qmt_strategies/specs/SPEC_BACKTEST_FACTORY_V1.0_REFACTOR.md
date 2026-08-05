# SPEC: 回测工厂 V1.0 重构(组合回测通用底座收口)

**版本**: v1.0
**日期**: 2026-06-27
**作者**: CC
**状态**: 起草待诚哥确认 → 确认后派 MIMO 实施
**承接**: `agent_hub/2026-06-23_backtest_generalization/round4_诚哥拍板.md`(已拍板,不可改)
**前置**: v0.4 Phase 1 已 ff-merge 到 master(commit ee9aa6b),三层 freeze 已存在(03/04/05)

---

## §0 文档定位与拍板承接

本 SPEC 不是从零设计,是**收口 v0.4 Phase 1 落地后的遗留债务**,把回测工厂从"已解硬绑定但残留 6+2 硬编码"推进到"组合回测真通用底座"。

Round 4 拍板(2026-06-23)已确定七项核心决策,本 SPEC 全部承接,不重新讨论:

1. 二层架构:底层(数据底座+交易日轴+切片)+ 上层先只做 portfolio 范式
2. Strategy Registry 解除 engine 与 6+2 硬绑定(v0.4 已实现)
3. 6+2 降级为 reference strategy,不重写不删(v0.4 已物理迁出)
4. trading_model 配置化 + 策略声明 allowed_trading_models(v0.4 已实现);**trading_model 引擎扩展永久不做,工厂永远只 next_open**
5. 旧产物冻结,旧 yaml 一次性迁移
6. RS 三问补做(本 SPEC §15 附录代答)
7. Python 边界:工厂本体可用现代 Python,QMT 策略守 3.6.8

**Round 4 红线**:正式 SPEC 完成并经诚哥确认前,不得启动工程改造。本 SPEC 定稿 + 诚哥签字后,才派 MIMO 执行 Phase 2-4。

---

## §1 Objective

一句话:**清掉 v0.4 残留的 6+2 硬编码 + 配置/schema/测试三类债务,让"任何组合回测策略丢进来都能跑"成为事实,而非理论。**

不碰二层架构本身(底层 + 上层 portfolio 范式),只清债。诚哥原则:"不要搞太复杂,先做组合回测通用化。"

---

## §2 Scope

### §2.1 In(V1.0 做)

10 项遗留债务(§3 详列)中:
- **必做核心**(Phase 1-3):D1 diagnostics 聚合硬编码、D2 yaml schema 迁移、D3 registry 自动扫描、D4 测试解耦、D8 freeze 门禁
- **可后置**(Phase 4,按需):D5 astock reader、D6 event_study stub、D7 batch 续跑、D9 huang 文档化、D10 paths 占位符

### §2.2 Out(V1.0 不做)

- event_study 完整实现(只 stub)
- factor_ic 范式
- 多 paradigm 抽象层
- trading_model 引擎扩展(永久不做)
- yaml-DSL 声明式策略
- 策略热加载

### §2.3 永不条款(单列 §16)

见 §16。

---

## §3 现状盘点与债务清单

v0.4 Phase 1 已落地的通用化(不重做):
- registry 已存在并 wired-in(`backtest/strategies/__init__.py`,3 策略已注册)
- 6+2 已物理迁出到 `backtest/strategies/production/ima_uptrend_v31/`
- engine `daily_engine.py` 已走 registry(`resolve_strategy()` 用 `get_strategy` + 校验 ALLOWED_TRADING_MODELS)
- diagnostics 已 namespace 化(通用字段提顶,6+2 私有字段下沉到 `strategy_specific.{name}.*`)

### 10 项遗留债务(逐项带行号定位)

| ID | 症状 | 精确位置 | 影响 | 归属 Phase |
|---|---|---|---|---|
| **D1** | diagnostics 聚合仍硬编码 ima_uptrend_v31,新策略 namespace 不进 summary | `daily_engine.py` L146-167(死代码 `_avg_filter_counts`/`_sum_trigger_counts`)、L226-229(`_aggregate_strategy_specific` 对 trigger_counts 特例)、L460-463(ima 旁路提取 filter/trigger counts)、L516-531(聚合构造) | 最痛,违背通用化初衷 | P2 |
| **D2** | yaml `strategy:` 块注释自承"临时",Milestone B 规划迁 `strategy_params:` 未落地 | `baseline.yaml` L7-9 注释;16 个 config + grid 点路径 | schema 不收口,onboarding 永远带 TODO | P3 |
| **D3** | registry 启动 import 清单手写,加策略要改 `__init__.py` | `strategies/__init__.py` L50-53(三个硬 import) | onboarding 痛点 | P3 |
| **D4** | 测试 spy 与策略名强耦合 | `test_daily_engine.py` L259 硬编码 `{"ima_uptrend_v31"}`、L298-320 猴补丁;`test_pit_manifest.py` L155-174/L198-217 共 3 处 `_REGISTRY[name]=spy`;`test_strategy_decision_schema_v04.py` L33-34/L42/L56/L66/L73 | registry/策略名一改全挂 | P2 |
| **D5** | 主 reader 只支持 DuckDB,astock parquet 不在主 OHLCV 路径 | `duckdb_reader.py` `SUPPORTED_SOURCES=(jince_zhisuan,qmt_self_owned)`;astock 仅 `indicators/tdx_chip_1min.py` 引用 | 第一数据源 astock 接不进工厂 | P4 |
| **D6** | event_study 全仓零代码 | — | Round 4 留接口未做 | P4(接口 P1) |
| **D7** | batch 串行无断点续跑 | `run_batch.py` L242-247 串行 for,无 checkpoint | 大 grid 中断要重跑 | P4 |
| **D8** | L2 freeze 已被 v0.4 静默打破(顶层 trigger_counts/filter_counts 下沉到 strategy_specific),未走重冻结流程 | `04_output_schema_freeze.md` §1.5 规定顶层必有,实际产出已下沉 | 契约与实现不一致 | P1 |
| **D9** | huang_zhongjun_combo 第三策略未文档化,用 pop/rename 绕过 6+2 namespace | `huang_zhongjun_combo/strategy.py` L283-292(`ss.pop("ima_uptrend_v31")` 改名) | 耦合样板,新策略可能误抄 | P4 |
| **D10** | paths.py 占位符与实际不一致 | `paths.py` L28 `PROJECT_MARKET_DB_V03_PLACEHOLDER`(D盘) vs yaml 实际(F盘) | OQ-1 开放问题 | P4 |

---

## §4 架构定型(V1.0 二层架构最终形态)

复用 v0.4 二层架构,V1.0 的"通用化完成线":

```
┌─────────────────────────────────────────────────┐
│ 策略层:registry 自动扫描,三策略平级            │
│   production/ima_uptrend_v31  (6+2 reference)   │
│   research/example_ma_cross   (minimal demo)    │
│   research/huang_zhongjun_combo (zhongjun+6+2)  │
├─────────────────────────────────────────────────┤
│ 上层:portfolio 范式 (DailyBacktestEngine)       │
│   - diagnostics 聚合彻底去 6+2 硬编码 (V1.0 修) │
│   - event_study 范式 stub (V1.0 预留接口位)     │
├─────────────────────────────────────────────────┤
│ 底层:数据底座 + 交易日轴 + 切片                │
│   - DuckDBDailyReader (jince/qmt_self_owned)    │
│   - AstockParquetReader (V1.0 P4 新增,可选)     │
│   - engine 鸭子类型 4 方法,加 reader 不动 engine│
└─────────────────────────────────────────────────┘
```

**关键边界**:engine 永远只鸭子类型依赖 reader 的 4 方法(`load_window`/`trading_calendar`/`coverage`/`close`)+ 属性(`db_path`/`wal_detected`),任何新 reader 实现这 4 方法即可接入,不改 engine。

---

## §5 Phase 划分与依赖图

### 依赖关系

```
Phase 1 (契约定型 + L2/L3 重冻结, CC-owned GATE)
   │  产出 06 freeze + 附录A/B/C
   │
   ├──> Phase 2 (diagnostics 通用化 + 测试解耦)   [必做核心, 最痛]
   │       依赖 P1 的 diagnostics_aggregate schema 定型
   │
   ├──> Phase 3 (yaml 迁移 + registry 自动扫描)  [必做核心]
   │       依赖 P1 的 strategy_params schema 定型
   │
   └──> Phase 4 (astock + event_study stub + batch + 尾债)  [可后置]
           独立子任务,可任意并行或拆 V1.1
```

### 必做 vs 后置

| 债务 | Phase | 必做/后置 | 理由 |
|---|---|---|---|
| D1 diagnostics | P2 | 必做 | 最痛,违背通用化初衷 |
| D2 yaml 迁移 | P3 | 必做 | schema 不收口则永远 TODO |
| D3 registry 扫描 | P3 | 必做 | onboarding 痛点 |
| D4 测试解耦 | P2 | 必做 | 与 D1 同文件,顺手清 |
| D5 astock reader | P4 | 可后置 | 独立基础设施,不阻塞通用化 |
| D6 event_study stub | P4 | 可后置(接口 P1) | Round 4 只要接口 |
| D7 batch 续跑 | P4 | 可后置 | 工程便利性 |
| D8 freeze 门禁 | P1 | 必做 | 其他 Phase 前置 GATE |
| D9 huang 文档化 | P4 | 可后置 | 仅文档 |
| D10 paths | P4 | 可后置 | 一行常量 |

**关键依赖判断**:D1 修复依赖 schema 定型(P1),因为新 diagnostics_aggregate 形状必须先在 L2 重冻结钉死。D2 同理依赖 P1 的 strategy_params schema。D5/D7/D10 彼此独立,可并行。

---

## §6 Phase 1 — 契约定型与三层 freeze 松绑(CC-owned GATE)

**目标**:定义 V1.0 目标 schema,补走 v0.4 漏掉的 L2 重冻结,为 P2/P3 提供契约依据。**不改引擎行为**,只产 freeze 文档 + SPEC 章节。

### 产出

1. `agent_hub/2026-06-23_backtest_generalization/06_interface_freeze_v10.md`(本 SPEC 同步产出)
2. 附录 A:diagnostics_aggregate V1.0 目标 schema(见本 SPEC 附录 A)
3. 附录 B:yaml V1.0 目标 schema(见本 SPEC 附录 B)
4. 附录 C:三层 freeze 变更对照表(见 06 freeze 文档)
5. event_study stub 接口签名(§12,契约在此钉死)
6. Hermes Agent 接口规范(§14)

### 关键决策:diagnostics_aggregate V1.0 形状

- 顶层 5 通用 key 保留:`warnings_unique` / `candidate_total_avg_per_day` / `candidate_passed_avg_per_day` / `unfilled_order_count` / `strategy_specific`
- **删除** L2(04 §1.5)顶层 `trigger_counts_total` / `filter_counts_avg_per_day` —— 已下沉到 `strategy_specific.{name}`,V1.0 正式承认并重冻结(L2 破坏性变更,走 GATE)
- `strategy_specific.{name}` 内部:由 `_aggregate_strategy_specific` 通用规则产出,**去掉 trigger_counts 特例**(L226-229)。所有 `dict[str,number]` 子键统一走 avg_per_day;策略想要 total 自己声明 `_total` 后缀键,引擎不做语义猜测
- **freeze 原则**:引擎不对策略私有字段做语义假设(只按值类型聚合:number-dict 求和除天数,其他标 `_present`)

### 验收

- 06 freeze 经诚哥签字(或 CC 代签 + 诚哥追认,沿用 v0.4 夜班授权)
- 附录 A/B/C 三表完整,每变更有"旧值→新值→理由"
- 诚哥确认 L2 顶层 trigger_counts/filter_counts 下沉 acceptable

### 是否重跑 GATE

本 Phase **就是新 GATE**,产出 06 freeze 即 V1.0 契约门禁。v0.4 漏走的 L2 重冻结在此补登。

---

## §7 Phase 2 — diagnostics 聚合通用化 + 测试解耦(MIMO,最痛)

**目标**:daily_engine 的 diagnostics 聚合彻底不认 ima_uptrend_v31,新策略自定义 namespace 自动进 summary。

### 要改的关键文件(精确路径)

**`backtest/engine/daily_engine.py`**:
- 删 L146-167 死代码 `_avg_filter_counts` / `_sum_trigger_counts`
- 删 L359-360 `daily_filter_counts` / `daily_trigger_counts` 列表声明
- 删 L460-463 ima 旁路(`_ima = ss_today.get("ima_uptrend_v31", {})` + 两个 append)
- 改 L226-229:`_aggregate_strategy_specific` 去掉 `if sub_key == "trigger_counts"` 特例,统一走 avg_per_day(按附录 A)

**`backtest/strategies/__init__.py`**:
- 新增测试辅助:`register_test_spy(name, fn)` + 上下文管理器 `strategy_spy(name)`(替代猴补丁,try/finally 还原)

**`backtest/tests/test_daily_engine.py`**:
- L259 `{"ima_uptrend_v31"}` → 从 `strategy_name` 配置动态取,断言"含当前配置策略 namespace"
- L262-263 对 filter_counts/trigger_counts 断言 → 改为断言"任一注册策略 namespace 下有这两键"
- L298-320 三处 `_REGISTRY[name]=spy` → 改用 `strategy_spy()` 上下文管理器

**`backtest/tests/test_pit_manifest.py`** L155-174/L198-217:同上解耦
**`backtest/tests/test_strategy_decision_schema_v04.py`** L33-34/L42/L56/L66/L73:同上解耦

**`backtest/tests/test_decision_logic.py`**:6+2 专属单测,直接 import 6+2 内部,**保留硬编码**(reference strategy 私有测试,非通用化测试)。SPEC 明示此边界。

### 验收标准(含一致性 diff)

- `backtest/tests/` 全量 PASS(238 → 预期 238+,不新增 skip/xfail)
- 一致性:`p2_core100.yaml` 迁移前后,`_compare_sha256.py` 对 trades/equity/positions 三件业务列 sha256 **bit-identical**;summary.json 的 `performance`(total_return/annualized_return/sharpe/max_drawdown)容差 0
- summary.json 的 `diagnostics_aggregate` **允许结构变更**(顶层不再有 trigger_counts_total,下沉到 strategy_specific)—— Phase 1 批准的破坏,验收单独标注"允许差异项"
- 新增测试 `test_diagnostics_aggregate_arbitrary_namespace`:注册临时 spy 策略返回自定义 namespace,断言 summary 里 `strategy_specific.{name}.{key}_avg_per_day` 存在

### 风险

- 删 trigger_counts 特例后,6+2 summary 里 trigger_counts 变 `trigger_counts_avg_per_day` 而非 `_total` —— 若 RS/诚哥依赖 total 语义,需在 6+2 strategy.py 自己补 total 投影(策略侧职责)。SPEC 给迁移说明
- 测试解耦引入新 spy 机制,保证不污染生产 registry(上下文管理器 try/finally 还原)

### 是否重跑 GATE

Phase 1 已重冻结 L2,本 Phase 按 06 freeze 实现,**不需再跑 GATE**,但一致性 diff 是回归门槛。

---

## §8 Phase 3 — yaml 一次性迁移 + registry 自动扫描(MIMO)

**目标**:16 个 config 的 `strategy:` 块迁到 `strategy_params:`,顶层 `strategy_name:` 改回 `strategy:`;registry 改自动扫描,加策略不改 `__init__.py`。

### 要改的关键文件

**`backtest/scripts/migrate_yaml_v03_to_v04.py`**:升级为 `migrate_yaml_to_v10.py`(或加 `--v10` 模式)。转换规则:
1. 顶层 `strategy_name: X` → 重命名为 `strategy: X`
2. 旧 `strategy:` 块(6+2 参数)→ 整块改键名 `strategy_params:`
3. `trading_model:` 保留
4. grid yaml `grid.strategy.X` 点号键 → `grid.strategy_params.X`
5. 头部注释追加 `# V1.0 migrated by migrate_yaml_to_v10.py`
6. **严格模式**:迁完扫描,若 yaml 仍含顶层 `strategy:` 块(非单值)或 `strategy_name:` → 报错退出,防漏迁

**`backtest/scripts/run_backtest.py`** L91/L94-95:`cfg.get("strategy", {})` → `cfg.get("strategy_params", {})`;`strategy_name` → `strategy`
**`backtest/scripts/run_batch.py`** L122/L125-126:同上

**`backtest/strategies/__init__.py`** L50-53:删三个手写 import,改自动扫描:
```python
import pkgutil, importlib
def _autodiscover():
    for cat in ("production", "research"):
        pkg = importlib.import_module("backtest.strategies." + cat)
        for _, modname, ispkg in pkgutil.iter_modules(pkg.__path__):
            if ispkg:
                importlib.import_module(
                    "backtest.strategies.%s.%s.strategy" % (cat, modname))
_autodiscover()
```
约束:不引 entry_points/pluggy,保持简单。加 `ispkg` + 约定目录结构防误 import。

### 验收(P2 core100 一致性)

- 迁移后 `p2_core100.yaml` 跑出的 trades/equity/positions 与迁移前 `_compare_sha256.py` bit-identical
- `list_strategies()` 输出与迁移前一致(3 策略)
- 删手写 import 后,新加空策略目录能被自动发现(新增 `test_registry_autodiscover`)
- 全量测试 PASS

### 风险

- 自动扫描遇 `__pycache__` 或非策略子包会误 import —— `ispkg` + 目录约定约束
- 16 yaml 批量迁移若漏迁,run_backtest 静默回退默认值 —— 严格模式 assert 无残留

### 是否重跑 GATE

不改 frozen contract(yaml schema 不在三层 freeze 内),**不需 GATE**。P2 core100 一致性 diff 是硬门槛。

---

## §9 Phase 4 — 数据底座扩展 + 范式预留 + 工程尾债(可后置,MIMO)

5 项可后置债务,不阻塞通用化主线,可独立派单或拆 V1.1。

### 4a. astock parquet reader(D5)

- 新建 `backtest/data_tools/astock_reader.py`,`AstockParquetReader` 鸭子类型 4 方法:`load_window(codes,start,end)` / `trading_calendar(start,end)` / `coverage(codes,start,end)` / `close(code,date)` + 属性 `db_path`/`data_source`/`wal_detected`/`wal_warning_message`
- 数据源 `E:\astock\`(1min 2009起 + 日线 + 财务全量)。先只做日线 OHLCV(与 DuckDBDailyReader 输出列对齐:date/open/high/low/close/vol/amount)
- `SUPPORTED_SOURCES` 不动;run_backtest 按 `data.source` 分流(`if source=="astock": AstockParquetReader(...) else DuckDBDailyReader(...)`)
- 不改 engine
- 验收:astock reader 跑 example_ma_cross smoke 产出合法 summary

### 4b. event_study stub(D6,接口 Phase 1 已定)

见 §12。

### 4c. batch 续跑(D7)

- `run_batch.py`:每 leaf 跑完写 checkpoint(`batch_id`+`leaf_index`+`results_dir`)到 `F:/backtest_workspace/batch_summary/{batch_id}_checkpoint.json`
- 重跑 `--resume {batch_id}` 跳过已完成 leaf
- 不做并行(诚哥原则"不要太复杂")
- 验收:中断后 `--resume` 跳过已完成 leaf 续跑

### 4d. huang_zhongjun_combo 文档化(D9)

- 06 freeze 附录记录第三策略 + namespace 改名模式(`ss.pop("ima_uptrend_v31")` → `ss["huang_zhongjun_combo"]`)
- **建议**:不正规化,记为 "reference coupling pattern",新策略不抄;复用 6+2 评分应直接 import `score_universe`/`make_decision` 在自己 namespace 下产出,不 pop 别人 namespace
- 验收:文档补充

### 4e. paths.py 占位符(D10)

- `PROJECT_MARKET_DB_V03_PLACEHOLDER` 改实际 F 盘路径或删占位符,OQ-1 收口
- 验收:grep 全仓无残留 D 盘占位符

### 是否重跑 GATE

4a/4c/4e 不碰 freeze;4b 接口 Phase 1 已 freeze。**不需 GATE**。

---

## §10 frozen contract 松绑流程

### 三层 freeze 现状

- L1 `03_interface_freeze.md`(8参6键)—— **V1.0 不动**(红线)
- L2 `04_output_schema_freeze.md`(6文件产物)—— **V1.0 动 diagnostics_aggregate**(顶层 trigger_counts/filter_counts 下沉),需重冻结
- L3 `05_interface_freeze_v04.md`(registry+namespace)—— **V1.0 扩展**(paradigm registry 接口位 + 自动扫描),需补充

### 松绑步骤(SPEC 写死)

1. **起草 06 freeze**:CC 起 `06_interface_freeze_v10.md`,逐字段标"不变/变更/新增/废弃",附附录 C 对照表
2. **诚哥签字**:06 freeze 经诚哥确认(或 CC 代签 + 追认,沿用 v0.4 夜班授权)
3. **git tag**:freeze 通过后打 `tag: freeze-v1.0-L2` + `tag: freeze-v1.0-L3`,作回滚锚点
4. **双轨切换**:V1.0 **不做运行时双轨**(诚哥原则"不长期背兼容债")。破坏性变更一次性切换,旧产物冻结
5. **回滚**:一致性 diff 不通过 → `git reset --hard` 到 tag 前,06 freeze 作废,回 v0.4 状态
6. **GATE 触发条件**:触碰 L1(8参/6键)→ 重跑完整 GATE + 诚哥签字;L2/L3 扩展性变更 → 06 freeze 签字即生效

**关键**:V1.0 的 L2 变更属于"承认既成事实"——v0.4 已把 trigger_counts 下沉,06 freeze 补登,非新破坏。降低诚哥决策成本。

---

## §11 迁移指南

### 16 yaml 的 `strategy:` → `strategy_params:` 一次性迁移

脚本:`backtest/scripts/migrate_yaml_to_v10.py`(升级现有 migrate 脚本)。

- **输入**:单 yaml 路径或 `--batch <dir>` 批量
- **输出**:默认就地改写,迁完 git diff 人工 review
- **转换规则**:见 §8
- **严格模式**:迁完扫描,残留顶层 `strategy:` 块或 `strategy_name:` → 报错退出

### P2 core100 一致性验收

1. 迁移前:当前 master 跑 `p2_core100.yaml` → 产物存 `F:/backtest_workspace/v04_baseline/p2_core100/`
2. 迁移后:V1.0 分支跑迁移后 `p2_core100.yaml` → 存 `v10_migrated/p2_core100/`
3. `_compare_sha256.py v04_baseline/p2_core100 v10_migrated/p2_core100`:
   - trades.csv / equity_curve.csv / positions.csv 业务列 sha256 bit-identical(剥除 run_id)
   - summary.json `performance` 四指标(total_return/annualized_return/sharpe/max_drawdown)容差 0
   - diagnostics_aggregate 允许结构差异(Phase 1 批准的破坏)
4. 不通过 → 停,向诚哥汇报(沿用 v0.4 §8.2 Ask First)

---

## §12 event_study stub 设计

Round 4 结论:预留接口,V1.0 第一阶段做到 stub + hello world。

### 范式注册(Paradigm Registry)—— 决策点

**推荐方案(更简单)**:不引入 paradigm registry 第二级,只留函数签名 stub。理由:V1.0 只有 portfolio 一个范式在跑,引入 paradigm 抽象是 YAGNI;真要加第二个范式再抽象。

**备选方案**:引入 `backtest/paradigms/__init__.py` 第二级注册 `register_paradigm("portfolio"/"event_study")`。若诚哥选此,V1.0 注册 portfolio(包装现有 engine)+ event_study stub runner。

### 接口签名(Phase 1 freeze 钉死)

```python
def run_event_study(reader, events, label_windows, **kwargs):
    """event_study stub.
    events = [{code, event_date, ...}]
    label_windows = [(offset_days, ...)]
    V1.0 不实现,完整实现后置 Phase 2+。
    """
    raise NotImplementedError("event_study paradigm: V1.0 stub, see SPEC §12")
```

- 不接入 daily_engine 主循环(event_study 不走 T→T+1 撮合)
- 不定义完整 StrategyDecision(事件范式产物是事件标签统计,非 trades/equity)

### hello world 程度

- `backtest/paradigms/event_study/__init__.py` + `stub.py`:含 `run_event_study` 抛 NotImplementedError + docstring 说明未来形态
- 测试 `test_event_study_stub.py`:断言调用抛 NotImplementedError 且 message 含 SPEC 引用
- **不做**:事件循环实现、标签计算、产物 schema

---

## §13 策略开发指南更新(7 步 onboarding V1.0 版)

| 步 | v0.4 | V1.0 变化 |
|---|---|---|
| 0 决定类型 | 4 问(组合回测?/日K?/next_open?/production vs research?) | 不变 |
| 1 建包 | `research/<strat>/{__init__.py, strategy.py}` | 不变 |
| 2 写 strategy.py | 手写 `_make_empty_decision` | 不变,强调:namespace key=策略名末段;**不要 pop 别人 namespace**(反例:huang_zhongjun_combo) |
| 3 注册 | 手动编辑 `__init__.py` 加 import | **删除此步**——registry 自动扫描,建目录即注册 |
| 4 写 yaml | `strategy_name:` + `strategy:` 块 | **改**:`strategy:`(名字) + `strategy_params:`(参数) |
| 5 写测试 | 3 测试 | 加 1:`test_<strat>_diagnostics_aggregated`——断言自己 namespace 出现在 summary.diagnostics_aggregate.strategy_specific |
| 6 跑通 | pytest + smoke | 不变 |
| 7 commit | 逐文件 add | 少一个文件(不再改 `__init__.py`) |

### 新策略接入契约(V1.0)

- 8 参签名不变(L1 frozen)
- 顶层 6 keys 不变(L1 frozen)
- diagnostics 4 keys(warnings/candidate_total/candidate_passed/strategy_specific)不变(L3 frozen)
- `strategy_specific.{your_strat}` 内部结构**自由定义**,引擎按值类型自动聚合(number-dict → avg_per_day)
- `ALLOWED_TRADING_MODELS = ["next_open"]` 声明不变
- 禁止:import xtquant / 读时间 / 读文件 / pop 他人 namespace

---

## §14 Hermes Agent 接口规范

Hermes(调度/验收角色)通过 registry 查询策略元信息。V1.0 规范三个接口:

### 1. `list_strategies()`(已存在)

返回 `sorted(_REGISTRY.keys())`,如 `["production/ima_uptrend_v31", "research/example_ma_cross", "research/huang_zhongjun_combo"]`。
V1.0 可选扩展 `list_strategies_detail()` 返回 `[{name, category, allowed_trading_models}]`。

### 2. `config_schema(strategy_name)`(新增)

返回该策略期望的 `strategy_params` 字段 schema(字段名/类型/默认/是否必填)。
- 实现:策略在 strategy.py 顶层声明 `STRATEGY_PARAM_SCHEMA = {...}`(手写 dict,不引 pydantic);`config_schema(name)` 从模块读这个常量
- 用途:Hermes/RS 拿 schema 自动生成 yaml 骨架或校验配置
- 策略未声明 → 返回 `{"error": "no schema declared", "strategy": name}`(不抛异常,Hermes 可降级)

### 3. 错误格式统一

- `get_strategy(unknown)` → `KeyError("strategy not found: X; registered: ...")`(已存在)
- `resolve_strategy(name, bad_model)` → `ValueError("trading_model=X not in ALLOWED_TRADING_MODELS=...")`(已存在)
- `config_schema(unknown)` → 同 KeyError 格式
- 不引异常类层级,沿用现有消息格式

---

## §15 RS 三问附录(代答)

诚哥要求正式 SPEC 前补 RS 三问,基于研究方向代答如下:

| 问 | 代答 | 影响校准 |
|---|---|---|
| **Q1** 写新策略愿用 Python 类/函数还是 yaml? | **Python 类/函数为主**。6+2/huang/example_ma_cross 三策略均为 Python 函数(`@register_strategy` 装饰 evaluate_day);yaml-DSL 引入解析/校验复杂度,与"不要太复杂"冲突。yaml 只承载 strategy_params 数值配置 | yaml 不承载策略逻辑;§13 onboarding 以 Python 策略类为主线 |
| **Q2** 最需组合回测/事件研究/因子 IC? | **组合回测为主 + 事件研究其次**。组合回测是已落地主线;事件研究 Round 4 留接口,V1.0 stub;因子 IC 后置(无明确诉求时不做) | event_study V1.0 做 stub(§12);factor_ic 不做 |
| **Q3** 旧 yaml 一次性迁移能接受吗? | **可接受,但需结果一致性 diff 验收**。一次性迁移脚本 + P2 core100 迁移前后 bit-identical(§11)。不长期背兼容债 | 不写运行时双格式;§10 双轨切换一次性 |

**声明**:本代答基于当前研究方向(6+2/黄氏/算力池主升浪均为组合回测)。若 RS 实际诉求与假设偏差大,Q1/Q2 校准可能调整,但不推翻 Round 4 大方向。

---

## §16 永不条款(V1.0 明示不做)

承接 v0.4 §九 + Round 4:

1. **永不扩展 trading_model 引擎**:工厂永远只 next_open(T收盘信号→T+1开盘成交)。撮合时点是策略代码内部职责。`ALLOWED_TRADING_MODELS` hook 保留但只取 `["next_open"]`
2. **永不重写 6+2**:6+2 是 reference strategy,只搬位置/包薄壳,不改 evaluate_day 逻辑、不改字段语义
3. **永不改 L1 frozen contract**:evaluate_day 8 参签名、StrategyDecision 6 顶层键,任何变更需重跑完整 GATE + 诚哥签字
4. **永不影响冻结期/模拟盘/实盘策略**:QMT 策略守 3.6.8 + GBK + QMT 红线;工厂本体可用现代 Python,但不反向污染 QMT 代码
5. **永不长期兼容旧 yaml**:不写运行时双格式适配;迁移一次性,迁完旧格式不支持
6. **永不引入插件框架**:不引 entry_points/pluggy/抽象基类;registry 是装饰器 + 自动扫描,保持简单
7. **永不在 V1.0 完整实现 event_study / factor_ic**:只 stub 接口,完整实现后置
8. **永不删除历史产物/文档**:P2 core100 等历史 results 冻结为参考,不删
9. **永不绕过一致性 diff 验收**:任何触碰 engine/数据路径的改动,必须过 `_compare_sha256.py` 门槛,不"差不多放行"

---

## §17 交付物清单

V1.0 完成时交付:

- `specs/SPEC_BACKTEST_FACTORY_V1.0_REFACTOR.md`(本 SPEC)
- `agent_hub/2026-06-23_backtest_generalization/06_interface_freeze_v10.md`(L2/L3 重冻结)
- git tag `freeze-v1.0-L2` / `freeze-v1.0-L3`
- Phase 2:daily_engine.py 去硬编码 + 死代码清理 + 测试解耦 + 新测试
- Phase 3:`migrate_yaml_to_v10.py` + 16 yaml 迁移 + registry 自动扫描 + run_backtest/run_batch 改读 strategy_params
- Phase 4:astock_reader.py + event_study stub + batch resume + paths.py fix + huang 文档
- P2 core100 一致性验收报告(`_compare_sha256.py` 输出)
- `agent_hub/回测工厂使用说明书.md` onboarding 章节更新(第3步删除、第4步改 strategy_params)
- worklog 记录

---

## §18 派单顺序

```
CC 起 SPEC + Phase 1(06 freeze) → 诚哥确认
  → 派 MIMO Phase 2(diagnostics,最痛,优先) → CC 验收(一致性 diff)
  → 派 MIMO Phase 3(yaml 迁移,可与 P2 并行不同文件) → CC 验收(P2 core100 diff)
  → 派 MIMO Phase 4(可后置,按需) → CC 验收
```

Phase 2 改 engine+tests,Phase 3 改 scripts+yaml+registry,文件不重叠,理论上可并行派两个 MIMO;MIMO 资源有限则优先 P2(最痛)。

---

## 附录 A:diagnostics_aggregate V1.0 目标 schema

```json
{
  "diagnostics_aggregate": {
    "warnings_unique": [str, ...],
    "candidate_total_avg_per_day": float,
    "candidate_passed_avg_per_day": float,
    "unfilled_order_count": int,
    "strategy_specific": {
      "<strategy_namespace>": {
        "<number_dict_key>_avg_per_day": {key: float, ...},
        "<other_key>_present": bool
      }
    }
  }
}
```

**变更说明**:
- 删除 L2(04 §1.5)顶层 `trigger_counts_total` / `filter_counts_avg_per_day`
- 引擎按值类型聚合:`dict[str,number]` → `{key}_avg_per_day`;其他类型 → `{key}_present`
- 策略想要 total → 自己在 namespace 声明 `{key}_total` 后缀键
- 引擎不做语义假设(去掉 L226-229 trigger_counts 特例)

---

## 附录 B:yaml V1.0 目标 schema

```yaml
strategy: production/ima_uptrend_v31      # 顶层,registry key(原 strategy_name)
trading_model: next_open                   # 顶层,策略 ALLOWED_TRADING_MODELS 校验

backtest: { name, start_date, end_date, initial_cash, benchmark_code, benchmark_db_path }
data:    { source: jince_zhisuan|qmt_self_owned|astock, path, adjustment }
universe: { csv | pit_manifest }
execution: { price: next_open|close, slippage, commission_rate, tax_rate }

strategy_params:                           # 顶层,策略私有参数(原 strategy 块)
  max_positions: 3
  min_score: 60.0
  # ... 策略自定义
```

**变更说明**:
- `strategy_name:` → `strategy:`(键名改回,值是 registry key)
- 旧 `strategy:` 块 → `strategy_params:`(策略私有参数)
- grid yaml 点号键 `strategy.X` → `strategy_params.X`
- `data.source` 新增 `astock` 选项(Phase 4a)

---

## 附录 C:三层 freeze 变更对照表

见 `agent_hub/2026-06-23_backtest_generalization/06_interface_freeze_v10.md` 附录 C。

---

## 签字栏

| 角色 | 状态 | 日期 |
|---|---|---|
| CC 起草 | ✅ 完成 | 2026-06-27 |
| 诚哥确认 | ⏳ 待审 | — |
| Hermes 评审 | ⏳ 可选 | — |
| 派 MIMO | ⏳ 待诚哥确认后 | — |

---

*本 SPEC 由 CC 基于三轮 Explore + Plan agent 摸底起草,承接 Round 4 拍板(2026-06-23)。所有债务定位均带文件:行号,可溯源。诚哥确认后派 MIMO 实施 Phase 2-4。*
