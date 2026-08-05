# SPEC_BACKTEST_MVP_OFFLINE_FACTORY_v0.2

日期：2026-06-13
作者：CC（基于 Hermes 决策修订自 v0.1）
状态：**正式版**，已收到 Hermes 决策（A–I 九条），可执行
基线：本 SPEC 是对 `SPEC_BACKTEST_MVP_OFFLINE_FACTORY_v0.1.md` 的增量修订；v0.1 中未被本文件改写的条款继续有效。

---

## 0. Hermes 决策摘要（2026-06-13）

| 编号 | 决策 | 落地条款 |
|---|---|---|
| A | 接受 DuckDB 只读直连作为主数据源 | §1 |
| B | base.yaml 默认区间 = `2025-09-01 ~ 2026-02-27` | §2.2 |
| C | 重复时间戳由 reader 永久去重，**不修 DuckDB 原库** | §1.4、§6.I |
| D | 复权口径采用 hfq（后复权） | §1.1、§2.2 |
| E | **benchmark 默认禁用** —— Hermes 实测 `000300.SH / 000905.SH / 399300.SZ / 000001.SH` 在 DuckDB 中均无数据 | §2.2、§2.4 |
| F | data_hash 公式增强（含 universe_hash、actual_min/max、dedup_count 等 9 项） | §3.2 |
| G | xtquant 下载器**完全移出 v0.2**，留到 v0.3 补数通道 | §5、§8 |
| H | report.md / summary.json **必须显式标注**样本期较短，仅用于 MVP 管线验证，**不用于策略最终定论** | §7 |
| I | 禁止以读写模式打开 `quantifydata.duckdb`；禁止在 `F:\金策智算\` 下写任何文件 | §6 |
| J | **磁盘分区**：D 盘仅放代码/配置/SPEC/测试（小产物）；results/cache/sample_db 等大产物全部落 `F:\backtest_workspace\`；**禁止在 C:\ 下写任何 backtest 产物**（决策日 2026-06-13，CC 提议，诚哥批准。原因：D 盘剩 24G 已 94% 使用） | §1.5、§2.2、§5、§6 |
| K | **长期数据链路定调（v0.3+）**：xtquant/MiniQMT 免费下载历史行情 → 标准化清洗 → 写入**项目自有 DuckDB**（`D:\QMT_STRATEGIES\data\duckdb\qmt_market_data.duckdb`，待 v0.3 创建）→ 回测引擎统一只读 DuckDB。**v0.2 不实现此链路**，仅完成现有金策智算 DuckDB 的只读消费。诚哥确认 QMT 会员开通后 xtquant 下载历史行情不额外收费 | §6.24、§9 |
| L | **CC 落地建议 9 条**（高 3 / 中 3 / 低 3）全部进 v0.2：results retention、DuckDB 并发读保护、universe schema、sample DB 生成脚本、trading_calendar 定义、coverage() 增强、读性能基线、summary_schema_version、logs.txt 纯文本警告（决策日 2026-06-13） | §1.6、§2.5、§3.3、§4.4、§7.5、附录 D |

> 以下条款全部以上述决策为准，不再保留 v0.2_DRAFT 中的开放问题。

---

## 1. 数据接入路径

### 1.1 主路径：DuckDB 只读直连（决策 A、D）

替代 v0.1 §2.1 / §3.1 中的「xtquant 下载 → backtest/data/daily/{code}.parquet」。

| 项 | v0.1 | v0.2 |
|---|---|---|
| 数据源 | xtquant 下载落 parquet | **DuckDB 只读直连** |
| 路径 | `backtest/data/daily/{code}.parquet` | `F:\金策智算\_internal\databases\duckdb\quantifydata.duckdb`（外部，**只读**） |
| 入口模块 | `data_tools/download_daily.py`（必须） | `data_tools/duckdb_reader.py`（必须） |
| 复权 | 未指定 | **强制 hfq（后复权）**，与金策智算 `adjustment_mode` 对齐 |

### 1.2 reader 接口契约

```python
class DuckDBDailyReader:
    def __init__(self, db_path: str):
        # 强制 read_only=True；任何写模式调用必须报错
        ...

    def load_window(
        self,
        codes: list,            # ["000001.SZ", ...]
        start_date: str,        # "YYYY-MM-DD"
        end_date: str,          # "YYYY-MM-DD"
    ) -> dict:                  # code -> DataFrame[date, open, high, low, close, vol, amount]
        ...

    def trading_calendar(self, start_date: str, end_date: str) -> list:
        # 基于 dat_day 实有 trade_time 推导，不依赖外部交易日历
        ...

    def coverage(self) -> dict:
        # {min_date, max_date, n_codes, n_rows_after_dedup, dedup_count, db_mtime}
        ...
```

强制要求：

1. **只读模式**打开 DuckDB（决策 I）。
2. **强制按 `CAST(trade_time AS DATE)` 去重**（决策 C），处理 §1.4 的重复时间戳问题。
3. **不写回原库**：任何情况下都不得对 `quantifydata.duckdb` 执行 INSERT/UPDATE/DELETE/CREATE/ATTACH 写模式。
4. **不缓存到磁盘**：直接返回 in-memory DataFrame；如内存压力大，按 universe 分批查询。
5. 每次实例化记录 `db_path` + DuckDB 文件 mtime + 实有覆盖统计，供 §3.2 的 `data_hash` 使用。

### 1.3 schema 适配

DuckDB 实际表结构（CC 已勘察）：

```text
TABLE dat_day (
    code         VARCHAR,                    -- "000001.SZ"
    trade_time   TIMESTAMP WITH TIME ZONE,   -- +08 时区
    open         DOUBLE,
    high         DOUBLE,
    low          DOUBLE,
    close        DOUBLE,
    vol          BIGINT,
    amount       DOUBLE
)
```

reader 内部统一规范化为：

```text
date         DATE        (CAST(trade_time AS DATE))
open/high/low/close   float64
vol          int64
amount       float64
```

### 1.4 已知数据问题：双时间戳重复（决策 C）

CC 实测发现：

- `trade_time` 字段同一交易日存在 `00:00:00+08` 与 `08:00:00+08` 两条记录，OHLCV 完全相同（疑似同步链路混用时区导致）。
- 5197 只股票 × 7 个月数据中，**18,620 个 (code, date) 对存在重复**，**682,732 个只有 1 行**。
- 影响范围：2025-08-01 ~ 2026-02-27 区间。

**Hermes 决策**：reader 永久承担去重责任，**不反向修复 DuckDB 原库**。理由是金策智算同步链路非本项目控制，修复后下次同步可能再次引入。

reader 必须采用以下方式去重：

```sql
SELECT
    code,
    CAST(trade_time AS DATE) AS date,
    open, high, low, close, vol, amount
FROM dat_day
WHERE code IN (...) AND trade_time BETWEEN ? AND ?
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY code, CAST(trade_time AS DATE)
    ORDER BY trade_time DESC
) = 1
```

回归测试必须包含：**同一 (code, date) 重复行场景下 reader 输出唯一**（见 §4.2）。

### 1.5 路径常量与磁盘分区（决策 J）

**背景**：D 盘当前剩余 24 GB（94% 使用），F 盘剩余 582 GB。回测 results、sample_db、cache 等会快速膨胀（一次 batch ≈ 14 个 run × 6 文件，长期跑会刷满 D 盘）。

**强制约定**：

| 类别 | 路径 | 盘 | 写权限 | 说明 |
|---|---|---|---|---|
| 代码 | `D:\QMT_STRATEGIES\backtest\` | D | 读写 | 含 strategy_core / engine / data_tools / scripts / tests / configs |
| SPEC / 评审 | `D:\QMT_STRATEGIES\specs\`、`D:\QMT_STRATEGIES\agent_hub\` | D | 读写 | 文档类 |
| universe 配置 | `D:\QMT_STRATEGIES\backtest\data\universe\` | D | 读写 | 几 KB 量级 CSV |
| **回测结果** | `F:\backtest_workspace\results\` | **F** | 读写 | 每个 run 独立子目录 |
| **batch 汇总** | `F:\backtest_workspace\batch_summary\` | **F** | 读写 | batch_summary.json 等 |
| **测试样本 DuckDB** | `F:\backtest_workspace\sample_db\` | **F** | 读写 | sample_quantifydata.duckdb |
| **临时缓存 / 中间产物** | `F:\backtest_workspace\cache\` | **F** | 读写 | 含 Python tempfile 重定向（见 §6.22） |
| **运行时日志** | `F:\backtest_workspace\logs\` | **F** | 读写 | 跨 run 的全局日志（与 run 内 logs.txt 区分） |
| 数据源 DuckDB | `F:\金策智算\_internal\databases\duckdb\quantifydata.duckdb` | F | **只读** | 决策 I |
| C 盘 | `C:\` | C | **完全禁止写 backtest 产物** | 决策 J |

**目录初始化**：

`F:\backtest_workspace\` 必须由工厂在首次运行时自动创建：

```text
F:\backtest_workspace\
  results\
  batch_summary\
  sample_db\
  cache\
  logs\
  README.txt              # 自动写入：本目录由 D:\QMT_STRATEGIES\backtest 工厂管理，请勿手动修改
```

工厂代码中以**单点常量**定义这些路径（建议 `backtest/paths.py`），所有写操作必须通过常量引用，禁止硬编码或拼接：

```python
# backtest/paths.py 示例（仅作 SPEC 说明，实现细节由 plan 决定）
WORKSPACE_ROOT = "F:/backtest_workspace"
RESULTS_DIR    = WORKSPACE_ROOT + "/results"
SAMPLE_DB_DIR  = WORKSPACE_ROOT + "/sample_db"
CACHE_DIR      = WORKSPACE_ROOT + "/cache"
LOGS_DIR       = WORKSPACE_ROOT + "/logs"
BATCH_DIR      = WORKSPACE_ROOT + "/batch_summary"
```

### 1.6 DuckDB 并发读保护（建议 #2）

**背景**：金策智算自带 `history_sync` 流程会刷新 `quantifydata.duckdb`，半小时一次。如果 reader 正在读、金策智算正在写，可能读到不一致快照或撞锁。

**强制要求**：

1. reader 实例化时检测同目录下是否存在 `quantifydata.duckdb.wal`（DuckDB Write-Ahead Log 文件）：
   - 存在 → 视为「金策智算可能正在同步」，**必须打印明显警告**到 stdout 与 logs.txt：
     > ⚠️ 检测到 `quantifydata.duckdb.wal`，金策智算可能正在同步数据。本次回测的 data_hash 在同步完成前不稳定，请同步结束后重跑确认。
   - reader 不主动中断，但警告必须落 summary.json：`data_concurrent_sync_warning: true`。
2. reader 必须使用 DuckDB read-only 连接参数（`access_mode='read_only'`），任何情况下不得 ATTACH 写模式或对原库执行 `CHECKPOINT`。
3. 集成测试中模拟 .wal 文件存在场景，验证警告路径正常。
4. **建议但不强制**：在 README 中提示用户「跑 batch 前停掉金策智算同步」，但工厂代码不主动检查/杀进程。

`summary.json` 增加字段：

```json
{
  "data_concurrent_sync_warning": false,
  "data_wal_detected": false,
  "data_wal_warning_message": ""
}
```

---

## 2. base.yaml 配置变更

### 2.1 v0.1 默认区间不可用

| 项 | v0.1 | DuckDB 实有 | 重叠率 |
|---|---|---|---|
| 默认 start_date | 2024-01-01 | 2025-08-01 | 0% |
| 默认 end_date | 2024-12-31 | 2026-02-27 | 0% |

### 2.2 v0.2 默认 base.yaml（决策 B、D、E）

```yaml
backtest:
  name: "baseline"
  start_date: "2025-09-01"   # DuckDB 起点 + 1 个月预热缓冲
  end_date:   "2026-02-27"   # 当前 DuckDB 最大日期
  initial_cash: 1000000
  benchmark: null            # 决策 E：默认禁用基准（DuckDB 中无指数数据）

data:
  source: "duckdb"
  duckdb_path: "F:/金策智算/_internal/databases/duckdb/quantifydata.duckdb"
  adjustment: "hfq"
  read_only: true            # 不可改

execution:
  price: "next_open"
  slippage: 0.001
  commission_rate: 0.00025
  tax_rate: 0.0001

strategy:
  max_positions: 5
  rebalance_policy: "daily"

scoring:
  min_score: 60
  min_core: 32
  max_bias5: 10
  max_daily_pct: 9
  sector_heat_mode: "zero"

risk:
  early_stop_days: 3
  early_stop_loss: -0.05
  stop_loss: -0.08
  warning_score_threshold: 50
  score_gap_threshold: 15

universe:
  file: "backtest/data/universe/strategy_pool_base.csv"

output:
  dir: "F:/backtest_workspace/results"   # 决策 J：results 落 F 盘
  workspace_root: "F:/backtest_workspace"
  warn_short_sample: true   # 决策 H：报告中显式标注样本期较短
```

### 2.3 数据缺失说明（不在 v0.2 范围内）

- 缺失：2024 全年、2025-01 ~ 2025-07、2026-02-28 至今。
- 补数渠道：金策智算自带的 `history_sync` 流程（已配 tushare token），由诚哥/Hermes 在金策智算 GUI 中手动触发。
- v0.2 不实现 xtquant 下载器（决策 G），相关脚本统一推迟到 **v0.3 补数通道**。

### 2.4 benchmark 禁用规则（决策 E）

Hermes 实测以下基准 code 在 DuckDB `dat_day` 中均无数据：

```text
000300.SH（沪深 300）
000905.SH（中证 500）
399300.SZ（沪深 300 深交所代码）
000001.SH（上证综指）
```

因此 v0.2 强制：

1. `benchmark: null` 为默认值。
2. 配置中如显式指定 benchmark 但 reader 加载结果为空，引擎必须**降级为 None + 警告日志**，不得报错中断回测。
3. summary.json / report.md 中相应字段：
   - `benchmark_code: null`
   - `benchmark_available: false`
   - `benchmark_note: "DuckDB 当前无指数数据，benchmark 已禁用"`
4. 净值曲线（equity_curve.csv）不输出 benchmark 列，或输出全 NaN 列并附 note。
5. 业绩指标中涉及基准的项（如超额收益、信息比率）在 v0.2 **不计算**，summary 中以 `null` 表示。

### 2.5 universe CSV schema（建议 #3）

`backtest/data/universe/strategy_pool_base.csv` 字段定义：

| 列 | 类型 | 必填 | 示例 | 说明 |
|---|---|---|---|---|
| code | str | ✅ | `000001.SZ` | 标准化后缀 `.SZ` / `.SH`；reader 不做格式转换，配置者负责 |
| name | str | ⏸ | `平安银行` | 可选，仅作可读性。若缺失 reader 不报错 |
| sector | str | ⏸ | `银行` | 可选，预留给 sector_heat 模式（v0.2 zero 模式下不使用） |
| enabled | bool | ⏸ | `true` | 可选，缺失视为 `true`；为 `false` 的行被 reader 跳过 |

要求：

1. 文件编码 **UTF-8**（首行可有 BOM 或无）。
2. **必须含表头**。
3. **第一列必须为 `code`**。
4. 重复 `code` 行 reader 必须去重并打印警告（保留首条）。
5. 空文件或仅表头 → 引擎报错，提示「universe 为空」。
6. `code` 格式不符合 `\d{6}\.(SZ|SH)` 的行被忽略并记入 `logs.txt`。

### 2.6 results 目录 retention 策略（建议 #1）

**背景**：每个 run 产 6 文件，batch 跑 7 experiments × 2 成交模型 = 14 个 run/批。长期跑会刷满 F 盘 results。

**v0.2 要求**：

1. `run_id` 必须**带时间戳前缀**：`YYYYMMDD_HHMMSS_<short_hash>`，便于按时间排序与清理。
2. `scripts/clean_results.py`（新增）：
   - 默认保留 `F:\backtest_workspace\results\` 下最近 30 天的 run。
   - 超过 30 天的 run 移动到 `F:\backtest_workspace\results_archive\<YYYY-MM>\` 子目录（**不直接删除**）。
   - archive 区超过 90 天的 run 才允许 `--delete-archived` 真删。
   - 默认 dry-run 模式，需 `--apply` 才执行。
3. `clean_results.py` 不在自动化路径上，由人手动跑或纳入 cron（v0.2 不强制 cron）。
4. batch_summary / sample_db / logs 目录**不在 retention 范围内**，由人工管理。

`summary.json` 增加字段：

```json
{
  "run_id": "20260613_103045_a3f9c2",
  "run_started_at": "2026-06-13T10:30:45+08:00",
  "results_dir": "F:/backtest_workspace/results/20260613_103045_a3f9c2_baseline"
}
```

---

## 3. summary.json 字段扩展

### 3.1 替换 v0.1 §3.5 sector_heat 字段块

替换为更完整的 data 元数据 + benchmark 元数据 + 样本期警告：

```json
{
  "data_source": "duckdb",
  "data_path": "F:/金策智算/_internal/databases/duckdb/quantifydata.duckdb",
  "data_mtime": "2026-05-06T23:59:00",
  "data_adjustment": "hfq",
  "data_coverage_actual": {
    "min_date": "2025-08-01",
    "max_date": "2026-02-27",
    "n_codes": 5197,
    "n_rows_after_dedup": 701352,
    "dedup_count": 18620
  },
  "data_dedup_applied": true,

  "benchmark_code": null,
  "benchmark_available": false,
  "benchmark_note": "DuckDB 当前无指数数据，benchmark 已禁用",

  "sector_heat_available": false,
  "sector_heat_mode": "zero",
  "sector_heat_warning": "historical sector heat unavailable; sector score set to 0",

  "sample_period_warning": {
    "is_short_sample": true,
    "requested_range": ["2025-09-01", "2026-02-27"],
    "actual_range": ["2025-09-01", "2026-02-27"],
    "trading_days": 119,
    "warning": "样本期仅约 6 个月，结果仅用于 MVP 管线验证，不可作为策略最终定论"
  }
}
```

### 3.2 data_hash 公式增强（决策 F）

替换 v0.1 §1.3.5 的 parquet 文件哈希。新公式：

```text
data_hash = sha256(
    db_path
    + "|" + db_mtime_iso8601
    + "|" + adjustment                # "hfq"
    + "|" + requested_start            # "2025-09-01"
    + "|" + requested_end              # "2026-02-27"
    + "|" + actual_min_date            # "2025-09-01"（reader.coverage() 返回）
    + "|" + actual_max_date            # "2026-02-27"
    + "|" + str(n_codes)               # 5197
    + "|" + str(n_rows_after_dedup)    # 701352
    + "|" + str(dedup_count)           # 18620
    + "|" + universe_hash              # sha256(sorted universe code list)
)
```

输入项含义：

| 输入 | 用途 |
|---|---|
| `db_path` | 防止跨机器/跨路径误用 |
| `db_mtime` | DuckDB 文件被同步刷新后 hash 自动失效 |
| `adjustment` | 防止 hfq/qfq 切换后 hash 误命中 |
| `requested_start/end` | 区分不同回测区间 |
| `actual_min/max` | 区分实际加载到的数据（防 universe 部分股票数据缺失被忽略） |
| `n_codes` | 防止 universe 增删 |
| `n_rows_after_dedup` | 反映去重后真实数据规模 |
| `dedup_count` | 反映去重发生程度（数据质量指纹） |
| `universe_hash` | 同 v0.1 |

要求：相同配置 + DuckDB 文件未变 → 两次运行 `data_hash` 一致；任一项变化 → `data_hash` 变化。

### 3.3 summary 元数据增强（建议 #5、#6、#8）

#### 3.3.1 summary_schema_version（建议 #8）

`summary.json` 顶层必须含字段：

```json
{
  "summary_schema_version": "0.2"
}
```

下游 RS 据此区分 schema；v0.3 起改为 `"0.3"`，新增字段不破坏 RS 解析。

#### 3.3.2 coverage 维度细化（建议 #6）

`reader.coverage()` 必须支持两种调用模式：

```python
# 模式 1：全表覆盖（无参数）
reader.coverage()
# → {"min_date": "2025-08-01", "max_date": "2026-02-27", "n_codes": 5197,
#    "n_rows_after_dedup": 701352, "dedup_count": 18620, "db_mtime": "..."}

# 模式 2：universe + 区间覆盖（带参数）
reader.coverage(codes=universe, start_date="2025-09-01", end_date="2026-02-27")
# → 模式 1 字段 + 以下 per-universe 维度：
#   {
#     "universe_size": 200,
#     "universe_codes_with_data": 197,
#     "universe_codes_missing": ["688001.SH", "300999.SZ", "..."],   # 完全无数据
#     "universe_per_code_coverage": {                                 # 部分缺失
#         "000001.SZ": {"min_date": "...", "max_date": "...", "trading_days": 119, "missing_days": 0},
#         "600519.SH": {"min_date": "...", "max_date": "...", "trading_days": 100, "missing_days": 19},
#         ...
#     }
#   }
```

`summary.json` 中 `data_coverage_actual` 字段在 universe 模式下扩展为：

```json
{
  "data_coverage_actual": {
    "min_date": "2025-09-01",
    "max_date": "2026-02-27",
    "n_codes": 5197,
    "n_rows_after_dedup": 701352,
    "dedup_count": 18620,
    "universe_coverage": {
      "universe_size": 200,
      "codes_with_data": 197,
      "codes_missing": ["688001.SH", "300999.SZ", "..."],
      "missing_count": 3
    }
  }
}
```

> per-code 详细列表（`universe_per_code_coverage`）**不写入 summary.json**（避免文件臃肿），仅在 logs.txt 末尾以表格形式输出，且 `--verbose` 时才打印。

#### 3.3.3 trading_calendar 定义（建议 #5）

`reader.trading_calendar(start_date, end_date)` 返回区间内**有任意股票数据的日期列表**，规则：

```sql
SELECT DISTINCT CAST(trade_time AS DATE) AS trade_date
FROM dat_day
WHERE CAST(trade_time AS DATE) BETWEEN ? AND ?
ORDER BY trade_date
```

要求：

1. 以 `dat_day.trade_time` 全表 distinct 为准，不依赖外部交易日历。
2. **个股停牌不影响日历**：只要当天有任意一只股票有数据，就视为交易日。
3. 引擎按 trading_calendar 推进循环；某日 universe 内某些 code 没数据视为停牌，**不报错**，按持仓 `last_price` 计算净值。
4. 集成测试必须验证：构造一个停牌样本，引擎能跳过停牌日的成交而保留持仓估值。

---

## 4. 测试增量（在 v0.1 §5 基础上追加）

### 4.1 reader 单元测试（必须）

1. **DuckDB 只读保护**：reader 内部任何 SQL 都不得包含 INSERT/UPDATE/DELETE/CREATE/ATTACH（写模式）；以读写方式打开 DuckDB 应在构造函数报错。
2. **重复时间戳去重**：构造一个含双时间戳的 sample 库，验证 reader 输出每个 (code, date) 仅一行，且为最新时间戳那条。
3. **数据范围越界**：start/end 超出 DuckDB 覆盖范围时，明确报错（含覆盖范围提示），而不是静默返回空。
4. **db_mtime 一致性**：两次相同配置运行 `data_hash` 相同（前提是 DuckDB 文件未变）。
5. **F:\ 写禁止**：reader 测试运行后 `F:\金策智算\` 目录 mtime 不应被改变（决策 I）。
6. **benchmark 缺失降级**：基准 code 加载结果为空时，引擎不报错，summary.benchmark_available=false。

### 4.2 集成测试（必须）

1. 用 base.yaml（默认 2025-09-01 ~ 2026-02-27）跑通 next_open 与 close 两种模型，输出标准 6 文件**到 `F:\backtest_workspace\results\`**。
2. report.md 头部必须包含**样本期警告横幅**（决策 H，见 §7）。
3. summary.json `sample_period_warning.is_short_sample == true` 时，report.md 必须可视化为醒目段落。

### 4.3 磁盘分区合规测试（决策 J，必须）

1. **C 盘零写入**：跑完整套测试（reader 单测 + 集成测试 + batch 测试）后，C 盘 `Users\Administrator\AppData\Local\Temp\` 与项目相关的子目录不应有 backtest 产物（pickle、parquet、csv、json、log 等）。测试方法：测试运行前后对比 C 盘相关目录文件清单 diff。
2. **D 盘代码区零产物**：跑完整套测试后，`D:\QMT_STRATEGIES\backtest\` 下不得出现 `results/`、`cache/`、`logs/`、`sample_db/` 等大产物目录（仅可有 `__pycache__/` 和 `.pytest_cache/`）。
3. **F 盘 workspace 自动初始化**：首次运行 `init_workspace.py` 后，`F:\backtest_workspace\` 五个子目录（results、batch_summary、sample_db、cache、logs）和 README.txt 必须存在。
4. **F:\金策智算\ 写入零变化**：reader 测试前后，`F:\金策智算\_internal\databases\duckdb\` 目录 mtime 与 size 完全一致，无 .wal / .tmp 副产物（决策 I + J 联合）。
5. **tempfile 重定向**：Python `tempfile.gettempdir()` 在 backtest 进程内应返回 `F:\backtest_workspace\cache` 或其子目录，不得返回 C 盘 Temp。

### 4.4 sample DuckDB 生成脚本（建议 #4）

新增 `tests/fixtures/build_sample_db.py`：

```python
# 伪代码 / SPEC 描述，非实现
def build_sample_db(
    source_db: str = "F:/金策智算/_internal/databases/duckdb/quantifydata.duckdb",
    target_db: str = "F:/backtest_workspace/sample_db/sample_quantifydata.duckdb",
    n_codes:   int = 10,
    start_date: str = "2025-09-01",
    end_date:   str = "2025-09-30",
):
    """
    从真实 5GB DuckDB 抽样，生成测试用 mini DuckDB（约几 MB）。
    必须显式注入双时间戳重复样本，覆盖回归场景。
    """
    ...
```

要求：

1. **从 source 只读读**，不修改 5GB 原库（决策 I）。
2. 抽样规则：固定 codes 列表（建议含 `000001.SZ`、`600519.SH`、`300750.SZ` 等流动性好的，便于人工核对）。
3. **必须注入双时间戳重复样本**：至少 5 个 (code, date) 对同时含 `00:00+08` 和 `08:00+08` 两条 OHLCV 完全一致的记录，用于 dedup 回归测试。
4. **必须注入停牌样本**：至少 1 只股票在某 5 个交易日完全无数据，用于 trading_calendar / 持仓估值测试。
5. 输出文件**不进 git**（在 F 盘 workspace），但脚本本身（`build_sample_db.py`）进 git，便于他人重现。
6. 测试 fixture 在 `conftest.py` 中调用此脚本（如目标文件不存在则自动构建）。

---

## 5. 目录结构调整

### 5.1 D 盘 — 代码与配置（小产物）

```text
D:\QMT_STRATEGIES\backtest\
  paths.py                  # 路径常量单点定义（决策 J）
  data_tools\
    duckdb_reader.py        # 主入口（决策 A）
    validate_data.py        # 校验 DuckDB（覆盖度 + 重复检测 + benchmark 探测）
    # download_daily.py    -- 决策 G：v0.2 不实现，留到 v0.3
    # downloader_xtquant/  -- 决策 G：v0.2 不实现

  data\
    universe\
      strategy_pool_base.csv     # 几 KB CSV，留 D 盘
    # sample\               -- 决策 J：sample DuckDB 移到 F 盘 §5.2
    # daily/                -- v0.2 不创建：MVP 不写本地 parquet 缓存

  strategy_core\               # 同 v0.1 §3.1
    ...
  engine\                      # 同 v0.1 §3.1
    ...
  configs\
    base.yaml                  # 已按 §2.2 更新（output.dir 指向 F 盘）
    experiments\
      exp_001.yaml
      exp_002.yaml
      exp_003.yaml
  scripts\
    run_backtest.py
    run_batch.py
    init_workspace.py          # 新增：首次运行自动创建 F:\backtest_workspace\ 目录树
    clean_results.py           # 新增（建议 #1）：results retention，30 天移 archive，90 天才允许真删
  tests\
    conftest.py
    fixtures\
      build_sample_db.py       # 新增（建议 #4）：从源库抽样构造 mini DuckDB，注入重复 + 停牌样本
    test_duckdb_reader.py     # 新增（§4.1）
    test_dedup.py             # 新增
    test_data_hash.py         # 新增
    test_benchmark_disabled.py # 新增
    test_paths_disk_partition.py # 新增（决策 J）：验证无任何写操作落到 C:\ / D:\backtest\results 等错误位置
    test_concurrent_sync.py   # 新增（建议 #2）：模拟 .wal 存在场景的警告路径
    test_universe_schema.py   # 新增（建议 #3）：校验 universe CSV 各字段处理
    test_coverage_universe.py # 新增（建议 #6）：coverage(codes,...) 模式正确性
    test_trading_calendar.py  # 新增（建议 #5）：停牌日不影响日历
    test_execution.py         # 同 v0.1
    test_tplus1.py            # 同 v0.1
    test_metrics.py           # 同 v0.1
    test_no_future_data.py    # 同 v0.1
    test_reproducibility.py   # 同 v0.1
    test_output_schema.py     # 同 v0.1
```

### 5.2 F 盘 — 大产物 workspace（决策 J）

```text
F:\backtest_workspace\
  README.txt                            # 由 init_workspace.py 自动生成
  results\
    {run_id}_{config_name}\        # run_id 格式：YYYYMMDD_HHMMSS_<short_hash>（建议 #1）
      summary.json
      report.md                         # 含样本期警告横幅（§7）
      trades.csv
      equity_curve.csv
      positions.csv
      logs.txt
  results_archive\                      # 新增（建议 #1）：clean_results.py 30 天后归档目标
    {YYYY-MM}\
      {run_id}_{config_name}\
        ...
  batch_summary\
    {batch_id}\
      batch_summary.json
      failed_experiments.txt
  sample_db\
    sample_quantifydata.duckdb          # 测试用 mini DuckDB（5-10 只 × 1 个月，含双时间戳重复样本）
  cache\                                # Python tempfile 重定向目标（§6.22）
  logs\                                 # 跨 run 全局日志（与 run 内 logs.txt 区分）
```

**workspace 不进 git**：F 盘 workspace 整体不纳入版本控制；D 盘代码仓库的 `.gitignore` 不需要管 F 盘内容。

### 5.3 F:\金策智算\ — 数据源（只读）

```text
F:\金策智算\_internal\databases\duckdb\
  quantifydata.duckdb              # 5GB 数据源（只读，决策 I）
  duckdb.exe                       # 金策智算自带 CLI
```

工厂任何代码不得写入此目录或其任何子目录。

---

## 6. 边界条款（在 v0.1 §6.1 基础上追加）

新增禁止事项（决策 I 对应 15、16；决策 G 对应 18；其他延伸）：

15. **禁止以读写模式打开 `quantifydata.duckdb`**。reader 必须使用 DuckDB 的 read-only 连接参数。
16. **禁止在 `F:\金策智算\` 下写任何文件**（包括子目录、日志、缓存、临时文件）。该目录是金策智算应用专属目录，本工厂仅作为消费者。
17. 禁止假设 DuckDB 数据完整、连续 —— reader 必须暴露 `coverage()` API 让上层判断。
18. **v0.2 不实现 xtquant 下载器**。任何对 xtquant 的 import、调用、CLI 调用都不得出现在 v0.2 代码库中（决策 G）。
19. 禁止在 reader 内部缓存 5GB 全量数据到内存或磁盘 —— 必须按 universe × 区间按需查询。
20. 禁止在 v0.2 计算依赖基准的指标（超额收益、信息比率、跟踪误差等）；这些字段在 summary 中以 `null` 表示，等基准数据可用后再启用（决策 E）。
21. **禁止在 `C:\` 下写任何 backtest 产物**（决策 J）。包括但不限于：results、cache、sample_db、logs、临时文件、pickle 缓存、pytest cache、Python `__pycache__` 之外的运行时产物。`C:\` 仅允许：操作系统进程必需的 stdlib 临时文件（如 import 期 .pyc）和 Claude 框架的 memory 索引（不属于 backtest 产物）。
22. **禁止在 `D:\` 下写大产物**（决策 J）。`D:\QMT_STRATEGIES\backtest\` 仅可写代码、配置、universe CSV；results、cache、sample_db、logs 必须落 `F:\backtest_workspace\`。Python tempfile 在 backtest 进程内必须显式重定向到 `F:\backtest_workspace\cache\`（通过 `tempfile.tempdir` 设置或 `TMPDIR` 环境变量），不得使用系统默认（C 盘 Temp）。
23. **禁止在 `F:\金策智算\` 下写**（决策 I 强化）。reader / validate / 任何工厂代码都不得在该目录下创建文件、子目录、临时文件、日志，包括 DuckDB 的 `.wal` / `.tmp` 副产物。reader 必须使用纯 read-only 连接，DuckDB 在只读模式下不会生成 wal。
24. **v0.2 禁止任何 xtquant / MiniQMT 调用**（决策 K）。包括 import xtquant、调用 xtdata、启动 MiniQMT 进程、xtquant 行情订阅等。即使 QMT 会员已开通、xtquant 免费可用，v0.2 仍不引入此链路 —— 该能力规划在 v0.3 的 `sync_xtquant_to_duckdb.py` 中独立实现，目标库为**项目自有** `D:\QMT_STRATEGIES\data\duckdb\qmt_market_data.duckdb`，**绝不写入金策智算的 quantifydata.duckdb**。详见 §9 Future Work。

---

## 7. 报告样本期警告（决策 H）

### 7.1 强制要求

`report.md` 顶部必须输出**醒目的样本期警告横幅**，且不可被关闭。模板：

```markdown
> ⚠️ **样本期警告**
>
> 本回测样本区间 `2025-09-01 ~ 2026-02-27`，约 6 个月（119 个交易日），
> **仅用于 MVP 管线验证**，**不可作为策略最终定论**。
>
> 缺失数据：2024 全年 / 2025-01 ~ 2025-07 / 2026-02-28 至今。
> 数据补全后请重跑完整回测再做策略评估。
```

### 7.2 触发条件

满足以下任一条件即触发：

1. `sample_period_warning.is_short_sample == true`
2. 样本期 < 12 个月
3. `benchmark_available == false`

### 7.3 summary.json 中

`sample_period_warning` 块结构见 §3.1，包含 `requested_range / actual_range / trading_days / warning` 四项。

### 7.4 batch 报告

`batch_summary.json` 中每条 run 记录必须含 `is_short_sample` 布尔字段；`run_batch.py` 在终端输出最后一行必须打印「⚠️ 全部 run 处于短样本期，结果仅用于管线验证」（如适用）。

### 7.5 logs.txt 纯文本警告（建议 #9）

`logs.txt` **首行**必须以纯文本（无 markdown）输出样本期警告，便于程序化读取与终端 grep：

```text
[WARN] SHORT_SAMPLE_PERIOD requested=2025-09-01..2026-02-27 actual=2025-09-01..2026-02-27 trading_days=119 message="样本期约 6 个月，仅用于 MVP 管线验证，不可作为策略最终定论"
[WARN] BENCHMARK_DISABLED reason="DuckDB 当前无指数数据"
[WARN] DATA_DEDUP_APPLIED count=18620
```

要求：

1. 顶部 WARN 块在所有 run 文件中格式统一，便于 RS 用 grep / awk 汇总。
2. WARN 后是正常运行日志（INFO/DEBUG/ERROR）。
3. 触发条件与 §7.2 一致；若条件不满足（如未来 sample 期足够长），WARN 块为空但日志结构保留。

---

## 8. Phase Plan 调整

### 8.1 Phase 1A 替换：DuckDB Reader

| 旧（v0.1） | 新（v0.2） |
|---|---|
| 设计 parquet/pkl/csv 本地缓存格式 | 实现 `DuckDBDailyReader`（只读、去重、coverage） + `init_workspace.py`（首次创建 F:\backtest_workspace\） |

验收：

1. 可从真实 5GB DuckDB 加载任意 universe × 区间数据。
2. 双时间戳重复场景下输出唯一。
3. 越界请求明确报错。
4. coverage() 返回真实统计与 db_mtime。
5. 任何写模式调用应失败。
6. benchmark 缺失场景下 reader 返回 None / 空 DataFrame，不报错。
7. **首次运行自动创建 `F:\backtest_workspace\` 五个子目录 + README.txt（决策 J）**。
8. **进程内 `tempfile.gettempdir()` 已重定向到 F 盘 workspace cache（决策 J）**。

### 8.2 Phase 1B 砍除（决策 G）

v0.1 中的 Phase 1B（xtquant 数据下载器）**完全移出 v0.2**。MVP 验收**不涉及** xtquant。

后续 v0.3 补数通道再单独立项。

### 8.3 Phase 2 ~ Phase 6

继承 v0.1 §7 原文不变，唯需注意：

- Phase 4 报告输出必须实现 §7 的样本期警告横幅。
- Phase 6 RS 批量实验：每个 experiment 配置都自动带短样本期警告，RS 汇总报告必须可见。

---

## 9. Future Work / v0.3+ 路线（决策 K，**非 v0.2 实现项**）

> ⚠️ 本章描述的**所有内容都不在 v0.2 范围内**，仅作为后续版本的设计指引，便于 v0.2 的 reader / engine 接口预留扩展空间。CC 在 v0.2 实现期间**不得**编写本章涉及的任何代码。

### 9.1 长期数据链路定调

诚哥确认：QMT 会员已开通，**只要 MiniQMT 客户端处于打开状态，xtquant 下载历史行情不额外收费**。因此长期数据链路应设计为：

```text
xtquant / MiniQMT
  ↓ 免费下载历史行情
[标准化清洗]
  ↓ 统一 code 格式 / 日期 / 复权口径
项目自有 DuckDB
  D:\QMT_STRATEGIES\data\duckdb\qmt_market_data.duckdb
  ↓ 只读
[DuckDBDailyReader]
  ↓
回测引擎
```

### 9.2 数据源职责矩阵（v0.3+ 目标态）

| 数据源 | 路径 | 用途 | 写权限 |
|---|---|---|---|
| 金策智算 quantifydata.duckdb | `F:\金策智算\_internal\databases\duckdb\quantifydata.duckdb` | v0.2 唯一数据源；v0.3+ 降级为辅助/对照源 | **永远只读** |
| 项目自有 qmt_market_data.duckdb | `D:\QMT_STRATEGIES\data\duckdb\qmt_market_data.duckdb` | v0.3 起为主数据源 | xtquant 同步脚本写；reader 只读 |
| xtquant / MiniQMT | （应用进程） | 数据采集/补数入口，**不直接被回测引擎调用** | n/a |

> ⚠️ **金策智算的 DuckDB 永久只读，绝不写入**。即使发现数据缺失，也只允许通过金策智算自带 GUI 的 `history_sync` 流程补，或转用项目自有库。

### 9.3 v0.3 任务清单：xtquant → 项目自有 DuckDB 同步

待实现脚本：`backtest/data_tools/sync_xtquant_to_duckdb.py`

#### 9.3.1 输入

```python
sync_xtquant_to_duckdb(
    universe: list,                # ["000001.SZ", "600519.SH", ...]
    start_date: str,               # "2024-01-01"
    end_date:   str,               # "2026-06-13"
    period:     str = "1d",        # 日线
    adjustment: str = "hfq",       # 与现有金策智算口径一致
    target_db:  str = "D:/QMT_STRATEGIES/data/duckdb/qmt_market_data.duckdb",
)
```

#### 9.3.2 处理流程

1. 通过 xtquant API（`xtdata.get_market_data` 或 `download_history_data`）拉取行情。
2. **标准化清洗**：
   - code 格式统一：`000001.SZ` / `600000.SH`（与 v0.2 reader 输出一致）
   - 日期字段统一：`trade_date DATE`（不带时区，避开 v0.2 双时间戳问题）
   - 复权口径统一：`hfq`（与现有金策智算口径一致）
3. **staging 表 + upsert**：
   - 先写入 `staging_dat_day_{batch_id}` 临时表
   - 校验完成后 MERGE 进 `dat_day` 主表
   - 主键 `(code, trade_date)`，重复行直接覆盖
4. **去重**：staging → main 时按 `(code, trade_date)` 去重，避免引入 v0.2 处理的双时间戳问题。
5. **失败隔离**：单只股票失败不中断批量，记录到 `failed_codes` 列表。

#### 9.3.3 输出 sync_report.json

```json
{
  "sync_id": "20260620_104500_abc123",
  "started_at": "2026-06-20T10:45:00+08:00",
  "finished_at": "2026-06-20T10:52:33+08:00",
  "duration_seconds": 453,
  "input": {
    "universe_size": 5197,
    "start_date": "2024-01-01",
    "end_date": "2026-06-13",
    "period": "1d",
    "adjustment": "hfq"
  },
  "result": {
    "success_count": 5180,
    "failed_count": 17,
    "failed_codes": ["...退市股票", "..."],
    "rows_inserted": 3245678,
    "rows_updated": 12345,
    "rows_skipped_duplicate": 234
  },
  "environment": {
    "xtquant_version": "...",
    "miniqmt_status": "running",
    "target_db_path": "D:/QMT_STRATEGIES/data/duckdb/qmt_market_data.duckdb",
    "target_db_size_after": "12.4 GB"
  }
}
```

#### 9.3.4 关键原则

| 原则 | 说明 |
|---|---|
| 金策智算 DuckDB 只读 | v0.3 也不写入金策智算的库 |
| xtquant 仅作采集入口 | 回测引擎不 import xtquant，永远通过 DuckDB 中转 |
| DuckDB 是统一数据仓库 | 所有数据源最终写入项目自有 DuckDB |
| 回测时不自动补数 | 必须先 sync → validate → 再 backtest，工厂不在回测过程中触发同步 |
| 同步前后写 sync_report | 失败追溯、复现指纹、覆盖度变化全部留痕 |

### 9.4 版本路线图

| 版本 | 目标 | 数据源 | 状态 |
|---|---|---|---|
| **v0.2** | 离线回测工厂 MVP，跑通 6+2 策略 next_open / close 两种模型 | 只读金策智算 `quantifydata.duckdb` | **进行中** |
| **v0.3** | xtquant/MiniQMT 免费补数链路 → 写入项目自有 DuckDB | 双源：金策智算（辅助）+ 项目自有（主） | 规划 |
| **v0.4** | 形成统一 `qmt_market_data.duckdb`，实现多数据源 merge / 去重 / 复权一致性校验 | 项目自有为主，金策智算用于交叉验证 | 规划 |

> v0.2 的 reader 接口（§1.2 `DuckDBDailyReader`）已被设计为**与具体 DuckDB 路径解耦**：未来只需把 `db_path` 从 `F:\金策智算\...\quantifydata.duckdb` 切到 `D:\QMT_STRATEGIES\data\duckdb\qmt_market_data.duckdb`，回测引擎无需任何修改即可切换数据源。v0.2 的 `paths.py` 应预留 `MARKET_DB_PATH` 常量便于未来切换。

### 9.5 v0.2 给 v0.3 的接口预留

为避免 v0.3 落地时大改 v0.2 代码，v0.2 实现需注意：

1. **paths.py 预留**：除 §1.5 的 workspace 常量外，建议同时定义（但不使用）：
   ```python
   # v0.3 占位（v0.2 不读取）
   PROJECT_MARKET_DB = "D:/QMT_STRATEGIES/data/duckdb/qmt_market_data.duckdb"
   ```
2. **reader 路径参数化**：`DuckDBDailyReader(db_path)` 必须接受任意路径，不得硬编码金策智算路径。
3. **summary.json `data_source` 字段**：v0.2 写 `"jince_zhisuan"`，v0.3 起可写 `"qmt_self_owned"` 或 `"merged"`，下游 RS 据此区分。
4. **schema 兼容**：v0.3 的 `dat_day` 表结构应与 v0.2 reader 输出对齐（code / trade_date / OHLCV / amount），允许 reader 通过同一接口消费两个库。

---

## 附录 A：DuckDB 勘察实测数据（2026-06-13）

```text
表：dat_day
行数：719,972（去重前）
去重后：701,352
股票数：5,197
日期范围：2025-08-01 ~ 2026-02-27
NULL/零值 OHLC：0 行
重复 (code, date)：18,620 个
```

时间戳分布：
- `00:00:00+08`：38,998 行（部分日期）
- `08:00:00+08`：680,974 行（主流）

样例（000001.SZ / 2025-08-01）：两行 OHLCV 完全一致，仅时间戳不同。

## 附录 B：基准数据缺失实测（Hermes，2026-06-13）

下列 code 在 DuckDB `dat_day` 中均无数据（COUNT(*) = 0）：

| code | 含义 |
|---|---|
| 000300.SH | 沪深 300（上交所代码） |
| 000905.SH | 中证 500 |
| 399300.SZ | 沪深 300（深交所代码） |
| 000001.SH | 上证综指 |

→ 决策 E：v0.2 默认禁用基准；相关指标在 summary 中 `null`；report 显式标注。

## 附录 D：实现注意事项（决策 L 低优先级建议）

> 本附录列低优先级建议（不强制），实现时可参考；不影响 v0.2 验收。

### D.1 DuckDB 读性能基线（建议 #7）

Phase 1A 完成 reader 后，CC 必须跑一次 benchmark 并记录到 `agent_hub` 评审帖：

| 测试场景 | 期望 |
|---|---|
| 加载 5 只 × 1 个月 | < 1 秒 |
| 加载 200 只 × 6 个月（典型 universe） | < 5 秒 |
| 加载 5197 只 × 7 个月（全表） | < 30 秒 |

如全表 > 30 秒，考虑：

1. 分页查询（按 universe 分批，每批 500 只）。
2. 在内存 attach 副本 DuckDB，对副本建 `(code, trade_date)` 索引（不动原库）。
3. 仅加载 universe 涉及的 codes，不全量扫描。

> 性能不达标不阻塞 v0.2 验收，但需在交付报告中说明实测数字。

### D.2 9 条建议来源索引

| 编号 | 优先级 | 建议内容 | 落地章节 |
|---|---|---|---|
| #1 | 🔴 高 | results 目录 retention 策略 | §2.6、§5.2、§8.1 验收（间接） |
| #2 | 🔴 高 | DuckDB 并发读保护（.wal 检测） | §1.6 |
| #3 | 🔴 高 | universe CSV schema 定义 | §2.5 |
| #4 | 🟡 中 | sample DuckDB 生成脚本 | §4.4 |
| #5 | 🟡 中 | trading_calendar 以全表 distinct 为准 | §3.3.3 |
| #6 | 🟡 中 | coverage() 增加 universe 维度 | §3.3.2 |
| #7 | 🟢 低 | DuckDB 读性能基线 | 附录 D.1 |
| #8 | 🟢 低 | summary_schema_version 字段 | §3.3.1 |
| #9 | 🟢 低 | logs.txt 纯文本警告 | §7.5 |

---

## 附录 C：变更对照（v0.1 → v0.2）

| 章节 | 变更 |
|---|---|
| §1 数据源 | xtquant→parquet → DuckDB 直读 |
| §1.5 路径常量 | **新增**：D 盘代码 / F 盘 workspace 分区（决策 J） |
| §2 base.yaml 区间 | 2024 → 2025-09-01 ~ 2026-02-27 |
| §2 base.yaml output.dir | `backtest/results` → `F:/backtest_workspace/results`（决策 J） |
| §2 benchmark | 默认 000300.SH → 默认 null |
| §3 summary 字段 | 新增 data_*、benchmark_*、sample_period_warning |
| §3 data_hash | parquet 文件哈希 → 9 项组合 sha256 |
| §4.3 磁盘分区合规测试 | **新增**：C 盘零写、D 盘代码区零产物、F 盘自动初始化、tempfile 重定向 |
| §5 目录结构 | 拆分为 D 盘代码 / F 盘 workspace 两部分；砍除 download_daily.py / daily/ 缓存 |
| §6 边界 | 新增禁止条款 15–24（含决策 J 的 21/22/23、决策 K 的 24） |
| §7 报告 | 新增样本期警告横幅强制要求；§7.5 logs.txt 纯文本警告（建议 #9） |
| §8 Phase | Phase 1B 砍除；Phase 1A 增加 init_workspace.py |
| §9 Future Work | **新增**：v0.3 xtquant→项目自有 DuckDB 同步链路；v0.4 多源 merge（决策 K） |
| §1.6 | **新增**：DuckDB 并发读保护，.wal 检测（建议 #2） |
| §2.5 | **新增**：universe CSV schema 字段定义（建议 #3） |
| §2.6 | **新增**：results retention，30 天移 archive、90 天才允许真删（建议 #1） |
| §3.3 | **新增**：summary_schema_version、coverage 增强、trading_calendar 定义（建议 #5/#6/#8） |
| §4.4 | **新增**：sample DuckDB 生成脚本（建议 #4） |
| 附录 D | **新增**：实现注意事项 + 9 条建议索引（决策 L） |
