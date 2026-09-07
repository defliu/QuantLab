# API 参考 · 数据层

> 速查向。设计原理见 `项目概述/系统架构设计/数据层架构/` 下的五篇专题文档，本文只列接口契约、返回结构与调用约束。

---

## 一、鸭子类型契约（最重要的一条）

数据层**不用抽象基类**，而是约定四个方法。任何对象只要实现这四个方法，就可以直接塞进 `run_backtest(reader=...)`：

| 方法 | 签名 | 返回 |
| --- | --- | --- |
| `load_window` | `(codes, start_date, end_date)` | 行情窗口，供引擎切片 |
| `trading_calendar` | `(start_date, end_date)` | 交易日历（升序日期序列） |
| `coverage` | `(codes=None, start_date=None, end_date=None)` | 数据覆盖情况（用于空窗检测） |
| `close` | `()` | 释放连接/句柄 |

三个内置实现全部满足该契约：`AstockParquetReader`、`DuckDBDailyReader`、`GpsjDuckDBReader`；`MaskParquetReader` 继承自 `AstockParquetReader`。

---

## 二、DataFeed —— 统一门面（`data/feed.py`）

```python
from data.feed import DataFeed
feed = DataFeed(source="astock")     # "astock" | "duckdb"
```

| 方法 | 签名 | 说明 |
| --- | --- | --- |
| `get_daily` | `(codes=None, start_date=None, end_date=None, fields=None)` | 返回 **MultiIndex `(date, code)`** 的 DataFrame，字段至少含 `open/high/low/close/vol/amount` |
| `get_universe` | `(end_date=None, top_n=500)` | 按成交额排序取股票池；取 `<= end_date` 的最近交易日 |
| `get_finance` | 见财务读取器 | PIT 安全 |

关键路径常量（模块级，可直接覆盖）：

```python
ASTOCK_DAILY   = "D:/astock/daily/stock_daily.parquet"
ASTOCK_FINANCE = "D:/astock/finance/fina_indicator.parquet"
ASTOCK_BASIC   = "D:/astock/basic/stock_basic.parquet"
DUCKDB_PATH    = os.environ.get("DUCKDB_PATH", "F:/金策智算/_internal/databases/duckdb/quantifydata.duckdb")
```

> 2026-08-29 起：`F:` 盘不存在、原 duckdb 已缺失，故 `DUCKDB_PATH` 改为环境变量可配置，**未配置时 duckdb 分支延迟报错，不影响默认 astock 分支**。`DataFeed` 内部带 `_cache` 做结果缓存。

传入未知 `source` 会抛 `ValueError("Unknown source: ...")`。

---

## 三、AstockParquetReader（`data/astock_reader.py`）

```python
r = AstockParquetReader(db_path=None, data_source=DATA_SOURCE_ASTOCK, adjustment="raw")
```

- 主源 reader，读 `D:/astock/` 下 parquet；
- `load_window(codes, start_date, end_date)` → 引擎主用；
- `trading_calendar(start, end)` / `coverage(codes, start, end)` → 引擎启动时空窗检测；
- `close(code=None, date=None)` → 便捷取收盘价；
- `__del__` 自动释放。

**`adjustment` 参数决定复权方式（`raw` 为不复权），回测与实盘口径必须一致**，否则产生 train-serving skew。

---

## 四、AstockFinanceReader —— PIT 安全财务（`data/astock_finance_reader.py`）

财务数据是前视偏差的高发区，**必须走这个 reader，不许直接 merge 报告期**。

```python
fr = AstockFinanceReader(finance_dir=None, daily_path=None)
```

| 方法 | 用途 |
| --- | --- |
| `get_fundamentals_pit(code, asof_date, fields=None)` | 按**实际披露日**取该时点可见的财务指标（PIT 核心） |
| `get_daily_pe(code, asof_date)` | 取 asof 日可见的 PE |
| `get_fundamentals_for_scoring(codes, asof_date)` | 批量取，供打分 |
| `close()` | 释放 |

PIT 依据字段：`ann_date` / `f_ann_date`。任何绕过它、直接用 `end_date`（报告期）对齐的写法都是未来函数。

---

## 五、DuckDBDailyReader（`data/duckdb_reader.py`）

```python
r = DuckDBDailyReader(db_path, data_source=JINCE_ZHISUAN, default_filters=None)
```

- 面向实时/增量场景；
- 内含 `_read_mtime()` 与 `_check_wal()`，**会检查数据文件修改时间与 WAL 状态**，用于判断数据新鲜度；
- 同样实现四方法契约。

---

## 六、GpsjDuckDBReader —— 备用源（`data/gpsj_reader.py`）

备用数据源，`adjustment` 参数同 astock；交叉验证规则见 `research_audit/gpsj备用数据源验证与交叉验证规则_20260816.md`，对拍脚本 `research_audit/compare_gpsj_astock_2025.py`。

使用前须知：备用源与主源在**停牌、ST、退市处理**上可能有差异，交叉验证不通过时以主源为准。

---

## 七、MaskParquetReader —— 涨跌停掩码（`data/mask_reader.py`）

继承 `AstockParquetReader`，为回测叠加涨跌停/停牌掩码。

**研究结论（T-20260829）：掩码前置（MASK=1）应固化为全框架标准地基**——它能挤掉约 18% 的涨跌停虚增收益。未做掩码前置的回测结果不可信，也不可与其它候选横向对比。

---

## 八、Universe 加载（`data/universe.py`）

```python
from data.universe import load_universe
codes = load_universe("data/universe_liquid_30e.csv")
```

Schema 校验规则（违反即报错，不静默降级）：

- 首列必须是 `code`，否则 `ValueError`；
- `code` 必须匹配 `^\d{6}\.(SZ|SH)$`，非法行**丢弃 + 告警 + 记录**；
- 可选列 `enabled`：`false` / `0` / `no` / 空串视为关闭，缺省为 true；
- 重复 code 保留首次出现，后续告警跳过；
- **空池（无有效行）抛 `ValueError`**；
- 容忍 UTF-8 BOM（`encoding='utf-8-sig'`）。

内置池：`universe_all_a.csv`（全 A）、`universe_liquid_30e.csv`（成交额 30 亿以上）、`universe_sample.csv`（抽样调试）。

---

## 九、常见错误与排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `empty trading_calendar` | 区间内无交易日或数据源未覆盖 | 先用 `coverage()` 查真实覆盖范围 |
| duckdb 分支报错 | `DUCKDB_PATH` 未配置 | 设环境变量或改用 `source="astock"` |
| 财务类因子 IC 异常偏高 | 用了报告期而非披露期对齐 | 改走 `get_fundamentals_pit` |
| 回测收益虚高约 18% | 未做涨跌停掩码前置 | 换 `MaskParquetReader` / 开启 MASK |
| `universe CSV first column must be 'code'` | CSV 首列名不对或有 BOM 外的隐藏字符 | 检查表头 |

⚠️ **`Updatedata/` 目录是人工周更的，常滞后 7 天以上。** 任何依赖它的管道都必须加数据新鲜度硬断言（如「候选日期最大值 > 收益数据源最新日」即判定为死局并 fail-loud），否则会出现「信号照常产出、收益一笔也算不出」的静默失败。
