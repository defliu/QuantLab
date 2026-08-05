---
kind: logging_system
name: 基于Python标准logging的回测日志系统
category: logging_system
scope:
    - '**'
source_files:
    - scripts/run_backtest.py
    - backtest/report.py
    - backtest/engine.py
    - data/astock_reader.py
    - data/duckdb_reader.py
    - data/universe.py
    - strategy/registry.py
---

该仓库使用 Python 标准库 `logging` 模块作为统一的日志系统，采用“每个模块独立 logger + CLI 统一配置”的轻量级架构，无第三方日志框架依赖。

**系统与框架**
- 所有模块通过 `import logging` 后调用 `log = logging.getLogger(__name__)` 获取模块级 logger 实例，形成以包/模块名为命名空间的层次化 logger 树。
- CLI 入口 `scripts/run_backtest.py` 在 `main()` 中通过 `logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")` 一次性配置根 logger 的输出格式与级别，后续所有子模块 logger 自动继承该配置。

**核心文件与职责**
- `scripts/run_backtest.py`：唯一负责 `basicConfig` 的地方，设定 INFO 级别与时间戳+级别+名称+消息的标准格式；同时输出回测关键信息（universe 加载、industry_map 状态、完成路径）。
- `backtest/report.py`：将引擎产出的结构化日志行（`daily_logs`）与 WARN 块写入 `logs.txt`，并在 `report.md` 中摘录最近 10 条 `[WARN]` / `[ERROR]` 日志。
- `backtest/engine.py`、`data/*_reader.py`、`strategy/registry.py` 等模块仅负责 `log.info/warning/debug` 调用，不关心输出目标。

**架构与约定**
- **分层记录**：CLI 层用 `info` 记录运行阶段事件（加载数据、策略名、结果目录），业务层用 `warning` 记录可恢复异常（benchmark 不可用、fundamentals 读取失败、rebalance 异常、去重/无效代码），调试信息用 `debug`（如财务数据行数）。未观察到 `error`/`critical` 的使用。
- **结构化字段**：日志本身为纯文本行，但 `report.py` 将回测元数据（run_id、config_hash、data_hash、universe_hash、benchmark_available、sector_heat_mode 等）以固定键写入 `summary.json`，并通过 `_build_warn_block` 生成带 `[WARN]` 前缀的标准化警告行，便于报告抽取。
- **输出位置**：默认结果目录 `RESULTS_DIR = "E:/QuantLab/reports"`，每次运行创建 `<run_id>_<config_name>/` 子目录，产出 `trades.csv`、`equity_curve.csv`、`positions.csv`、`logs.txt`、`report.md`、`summary.json` 六类文件，其中 `logs.txt` 即本次运行的完整日志。
- **日志聚合**：engine 每日循环收集 `daily_logs` 列表，最终由 `write_all` 统一写入 `logs.txt`，保证一次回测一份完整日志。

**约束与规范**
- 所有 logger 必须通过 `logging.getLogger(__name__)` 获取，禁止直接 `print` 替代日志（除 CLI 末尾性能摘要外）。
- 日志级别遵循：运行流程/关键里程碑用 `info`，可恢复异常或降级行为用 `warning`，详细诊断用 `debug`。
- 报告中的警告行必须以 `[WARN]` 或 `[ERROR]` 开头，以便 `report.md` 自动抽取最近 10 条关键日志。
- 结果目录路径通过 `report.set_results_dir()` 覆盖，但默认硬编码为 `E:/QuantLab/reports`，且 `make_results_dir` 强制使用 `/` 分隔符以保证跨平台一致性。