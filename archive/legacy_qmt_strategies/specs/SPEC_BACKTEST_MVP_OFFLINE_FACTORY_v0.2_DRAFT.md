# SPEC_BACKTEST_MVP_OFFLINE_FACTORY_v0.2_DRAFT

日期：2026-06-13
作者：CC（基于 Hermes v0.1 修订）
状态：**草案**，等 Hermes / 诚哥确认后落地

> 本草案只列出相对 v0.1 的**变更点**与**新增条款**，未提及部分继承 v0.1 原文。

---

## 修订背景

诚哥指出本地已有数据源 `F:\金策智算\_internal\databases\duckdb\quantifydata.duckdb`（5GB / 5197 只 / 7 个月日线）。CC 实测勘察后发现：

1. v0.1 假设的「xtquant 下载 → parquet 落盘」会重复造轮子，浪费 IO + 受 Python 3.6.8/WSL 边界拖累。
2. 本地 DuckDB 已有完整 OHLCV，复权口径明确（hfq）。
3. **但 DuckDB 数据范围与 v0.1 的 base.yaml 默认区间完全不重叠**，且发现一处 schema 异常需要 reader 处理。

---

## 1. 数据接入路径变更（核心修订）

### 1.1 新主路径：DuckDB 只读直连

**替代** v0.1 §2.1 / §3.1 中的「xtquant 下载 → backtest/data/daily/{code}.parquet」。

| 项 | v0.1 | v0.2 |
|---|---|---|
| 数据源 | xtquant 下载落 parquet | DuckDB 只读直连 |
| 路径 | `backtest/data/daily/{code}.parquet` | `F:\金策智算\_internal\databases\duckdb\quantifydata.duckdb`（外部，只读） |
| 入口模块 | `data_tools/download_daily.py`（必须） | `data_tools/duckdb_reader.py`（必须） + `data_tools/download_daily.py`（**降级为可选补数通道**） |
| 复权 | 未指定 | **强制 hfq（后复权）**，与金策智算 `adjustment_mode` 对齐 |

### 1.2 reader 接口契约（新增）

```python
class DuckDBDailyReader:
    def __init__(self, db_path: str, read_only: bool = True): ...
    def load_window(
        self,
        codes: list,            # ["000001.SZ", ...]
        start_date: str,        # "YYYY-MM-DD"
        end_date: str,          # "YYYY-MM-DD"
    ) -> dict:                  # code -> DataFrame[date, open, high, low, close, vol, amount]
        ...
    def trading_calendar(self, start_date, end_date) -> list: ...
    def coverage(self) -> dict: ...   # {min_date, max_date, n_codes, n_rows, db_mtime}
```

强制要求：

1. **只读模式**打开 DuckDB（防止误写坏 5GB 数据）。
2. **强制按 `CAST(trade_time AS DATE)` 去重**，处理 §1.4 的重复时间戳问题。
3. **不缓存到磁盘**：直接返回 in-memory DataFrame；如内存压力大，按 universe 分批查询。
4. 每次实例化记录 `db_path` + DuckDB 文件 mtime，写入 summary.json 的 `data_hash` 字段（替代 v0.1 §1.3.5 的 parquet 文件哈希）。

### 1.3 schema 适配（必须）

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
date         DATE       (CAST(trade_time AS DATE))
open/high/low/close  float
vol          int64
amount       float64
```

### 1.4 已知数据问题：双时间戳重复（必须处理）

CC 实测发现：

- `trade_time` 字段同一交易日存在 `00:00:00+08` 与 `08:00:00+08` 两条记录，OHLCV 完全相同（疑似同步链路混用时区导致）。
- 5197 只股票 × 7 个月数据中，**18620 个 (code, date) 对存在重复**，**682732 个只有 1 行**。
- 影响范围：2025-08-01 ~ 2026-02-27 区间。

reader 必须采用以下任一方式去重：

```sql
-- 方案 A（推荐）：QUALIFY 取最新时间戳
SELECT code, CAST(trade_time AS DATE) AS date, open, high, low, close, vol, amount
FROM dat_day
WHERE code IN (...) AND trade_time BETWEEN ? AND ?
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY code, CAST(trade_time AS DATE)
    ORDER BY trade_time DESC
) = 1
```

回归测试必须包含：**同一 (code, date) 重复行场景下 reader 输出唯一**。

---

## 2. base.yaml 默认区间调整（核心修订）

### 2.1 v0.1 默认区间不可用

| 项 | v0.1 | DuckDB 实有 |
|---|---|---|
| 默认 start_date | 2024-01-01 | **2025-08-01** |
| 默认 end_date | 2024-12-31 | **2026-02-27** |
| 覆盖度 | 0% | 100% |

### 2.2 v0.2 默认区间

```yaml
backtest:
  start_date: "2025-09-01"   # 留 1 个月作为信号预热缓冲
  end_date:   "2026-02-27"   # 当前 DuckDB 最大日期
  initial_cash: 1000000
  benchmark: "000300.SH"

data:
  source: "duckdb"
  duckdb_path: "F:/金策智算/_internal/databases/duckdb/quantifydata.duckdb"
  adjustment: "hfq"
```

### 2.3 数据补全（不在 MVP 范围内）

- 缺失：2024 全年、2025-01 ~ 2025-07、2026-02-28 至今。
- 补数渠道：金策智算自带的 `history_sync` 流程（已有 tushare token 配置）。
- 不在本 SPEC 范围内：CC 不实现 xtquant 下载器作为主路径，仅保留 §1.1 中的「补数通道」骨架以备未来。

---

## 3. summary.json 字段扩展（必须）

替换 v0.1 §3.5 的 sector_heat 字段，新增以下字段：

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
    "n_rows": 719972
  },
  "data_dedup_applied": true,
  "data_dedup_count": 18620,

  "sector_heat_available": false,
  "sector_heat_mode": "zero",
  "sector_heat_warning": "..."
}
```

`data_hash` 改为：`sha256(db_path + db_mtime + n_codes + n_rows)`。

---

## 4. 测试新增项（在 v0.1 §5 基础上追加）

1. **DuckDB 只读保护**：reader 以读写方式打开应失败。
2. **重复时间戳去重**：构造一个含双时间戳的 sample 库，验证 reader 输出每个 (code, date) 仅一行。
3. **数据范围越界**：start/end 超出 DuckDB 覆盖范围时，明确报错而不是静默返回空。
4. **db_mtime 一致性**：两次相同配置运行 `data_hash` 相同（前提是 DuckDB 文件未变）。

---

## 5. 目录结构调整（v0.1 §3.1 增量）

```text
D:\QMT_STRATEGIES\backtest\
  data_tools\
    duckdb_reader.py           # ← 新增，主入口
    download_daily.py          # ← 降级为可选补数通道
    validate_data.py           # ← 改为校验 DuckDB（覆盖度 + 重复检测）

  data\
    universe\
      strategy_pool_base.csv
    sample\
      sample_quantifydata.duckdb   # ← 测试用 mini DuckDB（5-10 只 × 1 个月）
    daily\                          # ← 保留目录但 MVP 不写入；留作未来 parquet 导出
```

---

## 6. 边界条款（v0.1 §6.1 追加）

新增禁止事项：

15. 禁止以读写模式打开 `quantifydata.duckdb`。
16. 禁止在 backtest 内部写入 `F:\金策智算\` 任何子目录。
17. 禁止假设 DuckDB 数据完整、连续 —— reader 必须暴露 coverage API 让上层判断。

---

## 7. Phase Plan 调整

### Phase 1A 替换：DuckDB Reader

| 旧（v0.1） | 新（v0.2） |
|---|---|
| 设计 parquet/pkl/csv 本地缓存格式 | 实现 `DuckDBDailyReader`（只读、去重、coverage） |

验收：

1. 可从真实 5GB DuckDB 加载任意 universe × 区间数据。
2. 双时间戳重复场景下输出唯一。
3. 越界请求明确报错。
4. coverage() 返回真实统计与 db_mtime。

### Phase 1B 降级：xtquant 下载器（可选）

仅实现脚本骨架，明确标注「补数通道，非主路径」。MVP 验收**不要求** Phase 1B 完成。

其余 Phase 2 ~ Phase 6 保持 v0.1 不变。

---

## 8. 待 Hermes / 诚哥确认的开放问题

1. **base.yaml 默认区间** `2025-09-01 ~ 2026-02-27` 是否可接受？还是要等金策智算补完 2024 数据再启动？
2. **双时间戳重复**是否要先反向修复 DuckDB 数据，还是 reader 永久承担去重责任？（CC 倾向后者，因为不动数据源最稳）
3. **复权**确认为 hfq 后复权？还是另外用前复权？
4. **基准 000300.SH** —— DuckDB 里有这个 code 吗？需要先勘察。CC 待办。
5. xtquant 下载器在 v0.2 里被降级为补数通道，是否完全砍掉留到 v0.3？

---

## 附录 A：DuckDB 勘察实测数据（2026-06-13）

```text
表：dat_day
行数：719,972
股票数：5,197
日期范围：2025-08-01 ~ 2026-02-27
NULL/零值 OHLC：0 行
重复 (code, date)：18,620 个 / 总 701,352 个 (code, date)
```

时间戳分布：
- `00:00:00+08`：38,998 行（仅出现于部分日期）
- `08:00:00+08`：680,974 行（主流）

样例（000001.SZ / 2025-08-01）：两行 OHLCV 完全一致，仅时间戳不同。
