## 1. 使用的体系/方案

仓库没有统一的自定义异常层次（未找到任何 `errors.py`、`exceptions.py` 或自定义 `class *Exception`），整个代码库采用 **Python 内置异常 + 标准 logging + 宽泛 `except Exception`** 的轻量方式：
- **数据读取层** (`data/astock_reader.py`, `data/duckdb_reader.py`, `data/benchmark_reader.py`) 在构造期用 `ValueError` / `FileNotFoundError` 拒绝非法参数与缺失资源。
- **回测引擎** (`backtest/engine.py`) 对配置值做白名单校验，不合法时抛 `ValueError`；benchmark 数据不足返回 `(None, note)` 字符串，由调用方判定失败而非抛异常。
- **策略注册表** (`strategy/registry.py`) 对重复注册抛 `ValueError`，找不到策略抛 `KeyError`；模块 autodiscover 把每个模块导入包在 `try/except Exception` 里，单模块失败仅 `log.warning(...)`，不影响整体加载。
- **Broker LocalContext** (`broker/local_context.py`) 把本地不支持的 QMT C API 显式 `raise NotImplementedError("... -> fail-open")`，触发上层 fail-open 行为。
- **交易组合** (`backtest/portfolio.py` `apply_trade`) 遇到未知 `side` 抛 `ValueError`。

没有发现任何 `panic/recover`（Python 无此概念）、中间件拦截、错误码映射或结构化错误对象（如 pydantic `ValidationError`）的统一框架。

## 2. 关键文件

| 文件 | 职责 | 错误处理方式 |
|---|---|---|
| `data/astock_reader.py` | astock parquet 读取器 | 构造期 `ValueError`(adjustment/codes/索引列)、`FileNotFoundError`(文件缺失) |
| `data/duckdb_reader.py` | DuckDB 读取器 | 同 astock_reader，新增 WAL 告警通过 `log.warning` + `print` |
| `data/benchmark_reader.py` | 基准指数读取 | `FileNotFoundError`(duckdb 缺失) |
| `backtest/engine.py` | 回测主循环 | 校验 trading_model、trading_calendar 为空后 `raise ValueError`；benchmark 读失败回退 `(None, note)` 字符串 |
| `strategy/registry.py` | 策略自动注册 | 重复/缺失策略抛 `ValueError`/`KeyError`；autodiscover 捕获异常并 `log.warning` 继续 |
| `broker/local_context.py` | 本地 miniQMT 适配 | 接口不存在则 `raise NotImplementedError("... -> fail-open")`，由策略侧 fail-open 降级 |
| `backtest/portfolio.py` | 回测持仓记账 | 未知 `side` 抛 `ValueError` |
| `scripts/check_capital_allocation.py` | 资金分配校验脚本 | 缺依赖 `sys.stderr.write` + `sys.exit(2)`，配置文件缺失同样 stderr + exit |
| `projects/*/research/*.py`（研究脚本） | 各因子/策略研究批跑 | 统一 `except Exception as e:` 包住段级执行，记录 `traceback.print_exc()` 并把 `{"error": str(e)}` 写入结果字典，防止单段崩溃终止整份批量任务 |
| `test_connection*.py` | 连接测试 | `except Exception as e: print(err); traceback.print_exc(); sys.exit(1)` |

## 3. 架构与约定

- **分层失败语义**：基础设施层（reader/engine）用 `raise ...` 让调用方感知不可恢复配置错误；数据获取层（benchmark/xtdata）用返回空/null + 描述字符串的“可恢复失败”模式，使回测流程不因外部数据缺失而中断。
- **容错自包含**：`LocalContext.get_stock_name` / `get_stock_basic_info` 内部 `try/except Exception` 对外返回空串或 `None`，保证策略即便 xtdata 链路故障也能运行（fail-open）。
- **Autodiscover 隔离**：策略模块导入失败不会导致回测入口崩溃——`_autodiscover` 中 `importlib.import_module` 被逐模块 try/except，仅记录 warning 跳过。
- **研究/批量任务粒度化兜底**：各 `projects/Project_XX/research/*verify*` 脚本把每种子样本放入 `try/except Exception`，打印 `traceback.print_exc()` 后把错误写进结果 dict，确保一份报告跑完所有 segment。
- **配置前置校验**：reader/engine/registry 均在函数入口处用 `if not valid: raise ValueError(...)` 拒绝非法参数，避免脏数据流入后续计算。

## 4. 观察到的约束与规则（从实现反推）

1. **读者必须使用白名单参数校验**：新建 reader 时需按 astock_reader/duckdb_reader 的模式，在构造期验证枚举值并抛出 `ValueError`，缺失文件抛 `FileNotFoundError`。
2. **QMT 桥接层不得直接崩溃策略**：对无法实现的 C API 必须 `raise NotImplementedError("... -> fail-open")`，由策略端按 fail-open 逻辑降级，而不是吞掉异常伪装成功。
3. **模块加载失败应可恢复**：新策略模块被 `@register_strategy` 装饰并通过 autodiscover 加载时，若 import 报错应只记 `log.warning`，不中断其他策略注册（见 registry._autodiscover）。
4. **批量研究脚本必须以 segment 为单位 try/except**：参考 `B1_trail15_only.py`、`C1_max5_verify.py`、`A5_largecap_verify.py` 的模板：包裹单段逻辑、`traceback.print_exc()`、将 `str(e)` 写入结果字典的 `error` 键，最终仍产出完整报告。
5. **工具脚本以退出码表达失败**：`check_capital_allocation.py`、`test_connection*.py` 等独立脚本在依赖缺失、文件缺失时使用 `sys.stderr.write(...)` + `sys.exit(非0)`，便于 CI/Cron 判断成败。
6. **不存在全局错误中心**：未发现 logger 初始化集中管理的地方——每个模块各自 `logging.getLogger(__name__)`；也没有统一的 middleware/global except handler。错误主要通过局部 try/except 和标准异常向上传播或被脚本层捕获并序列化到结果 JSON/CSV。
7. **日志与异常并存**：可恢复的下游问题走 `log.warning`（如 DuckDB WAL 检测、增量 parquet 读取失败、data_config 导入失败），不可恢复的参数错误走 `raise ValueError`，两者并行存在但互不替代。