## 1. 系统/方案概述

仓库未引入第三方日志框架（如 loguru、logbook、python-json-logger），全部采用 **Python 标准库 `logging`**，并在核心模块中通过 `logging.getLogger(__name__)` 获取命名 logger；顶层脚本在入口处统一调用 `logging.basicConfig(...)` 设定根 handler。该做法使回测引擎、数据读取层、策略模块、券商适配器各自独立输出结构化日志，再通过根 handler 统一路由到 stdout / 每跑一 run 独立的 `reports/<run_id>/logs.txt`。

此外，项目存在一份已生成的 repowiki 知识卡《基于Python标准logging的回测日志系统》以及多处 `reports/.../logs.txt` 产物，表明“以运行 ID 为目录隔离的一次性日志文件”是已固化的工程约定。

## 2. 关键文件与位置

| 组件 | 文件 | 角色 |
|---|---|---|
| 回测 CLI 入口 | `scripts/run_backtest.py` | `basicConfig(level=INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")`，创建命名 logger `run_backtest` |
| 回测主循环 | `backtest/engine.py` | `log = logging.getLogger(__name__)`，记录 backtest 各阶段事件 |
| 组合/再平衡 | `backtest/portfolio.py`、`backtest/rebalance.py` | 同一 pattern：模块级 `log`，写入 reconfigure/trade/position 信息 |
| 策略模块 | `strategy/rps_momentum.py`、`strategy/sector_rps.py`、`strategy/registry.py` | 使用模块 logger 写 strategy signal / gate / RPS 计算日志 |
| 数据读取层 | `data/astock_reader.py`、`data/universe.py`、`data/benchmark_reader.py`、`data/duckdb_reader.py`、`data/astock_finance_reader.py` | 读 parquet/DuckDB 时 `log.warning` 增量缺失、universe 重复、benchmark 为空等异常路径 |
| 券商执行器 | `broker/qmt_order.py` | 不直接打 Python logger，而是通过 `QmtOrderExecutor.__init__(..., log_fn=None)` 注入回调函数（便于 QMT GBK 单文件策略把 C 端 `C.write_log_text` 或自定义打印接入）；同时含大量 `[QMT_ORDER]...` 文本标记 |
| 研究脚本 | `projects/*/research/*.py`、`research_audit/*.py` | 各自 `basicConfig(level=logging.INFO, format=...)` 自行配置，属于一次性实验流程，非共享基础库 |

## 3. 架构与约定

### 3.1 命名 logger 模式
- 每个业务模块顶部统一声明 `import logging` + `log = logging.getLogger(__name__)`。
- 调用形式为无模板字串式占位：`log.info("... %s", value)`、`log.warning("... %d codes from %s", ...)`——利用 logging 自身的 `%s` 格式化，避免不必要的字符串拼接。
- 模块间不共享单一全局 logger，也不注册自定义 formatter；formatter 由顶层 `basicConfig` 决定。

### 3.2 层级划分
- `scripts/run_backtest.py`：唯一负责根 handler 配置的位置（其他脚本独立 basicConfig，互不干扰）。
- `backtest/*`、`strategy/*`、`data/*`、`factors/*`、`mcp/*`：消费该根 handler 输出，默认进 stdout。
- `broker/qmt_order.py` 及构建出的 QMT GBK 策略不使用 Python `logging`，原因见下节。

### 3.3 运行时日志落盘
- `scripts.run_backtest` 并不显式 FileHandler 到文件；实际落地 `reports/<YYYYMMDD_HHMMSS_xxx>/<config_name>/logs.txt` 由外部 shell/cron 重定向 `stdout`，或通过 runner 进程启动时的 stdout 捕获完成。
- 已有历史产证：大量 `reports/202608xx_*/logs.txt` 即本次机制的产物。

### 3.4 QMT 侧日志惯例
- `broker/qmt_order.py` 内没有 import `logging`；所有下单/反查/pending 相关状态通过构造参数 `log_fn` 回调输出（默认空 lambda），由 GBK 单文件策略将其绑定到 `C.write_log_text` 或 QMT 控制台，从而绕过生产环境可能缺少的 `logging` module。
- 错误码集中定义于本模块（`OrderResult.status ∈ {SUBMITTED, SAFEMODE, REJECT, ERR, EXC}`），便于策略侧按 status 分类处理。

## 4. 约定与约束

- **模块级命名 logger 是唯一约定**：除 CLI 入口和一次性脚本外，不新增全局 logger，新模块应仿照现有写法添加 `log = logging.getLogger(__name__)`。
- **消息格式**：人类可读纯文本 + Python `%s` 占位；禁止 f-string 用于底层模块，因 QMT GBK 产物需兼容 Python 3.6 语法（见 AGENTS.md 的 QMT 红线，虽针对 GBK 产物，但保持代码可移植性贯穿整个仓库）。
- **日志级别**：根 handler 默认 `INFO`，`warning` 用于异常降级路径（如 `astock_reader._merge_update_daily` 导入 `data_config` 失败、`benchmark_reader` 为空等），`info` 用于正常运行轨迹。
- **结构字段**：仓库未强制 JSON 结构化日志；结构化语义通过 key-value 文本约定表达，例如 `universe loaded: %d codes from %s`、`[QMT_ORDER][SAFEMODE] %s %s %.2f x%s` 这类以前缀区分来源的形式。
- **多运行隔离**：每次 backtest run 产生独立 `reports/<run_id>_*/` 目录，含 `logs.txt`、performance JSON、持仓 CSV；这与 `backtest.engine._make_run_id` 生成 `YYYYMMDD_HHMMSS_<hash>` 的运行 ID 联动。
- **QMT 限制**：GBK 单文件策略不得依赖 Python `logging`（QMT Python 3.6.8 环境第三方包不可信），必须使用 `log_fn` 回调或 `C.write_log_text`；真实下单逻辑集中在 `broker/qmt_order.py`，禁止裸调 `C.passorder`。
- **禁止混用多个 basicConfig**：仅 `scripts/run_backtest.py` 等入口文件应调用 basicConfig；业务模块不应再次配置根 handler，避免覆盖同一次运行的输出。

综上，该仓库的日志体系是以 stdlib `logging` + 模块命名 logger 为基础的轻量分布式方案，配合 CLI 入口的 `basicConfig` 将 stdout 管道化产出，并通过运行 ID 目录实现天然的多并发隔离；对实盘 QMT 侧则单独抽象了 `log_fn`/状态码通道以规避生产环境依赖限制。