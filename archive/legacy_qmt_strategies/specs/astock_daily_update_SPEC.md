# SPEC: astock 数据每日增量更新系统

> 本 SPEC 对应方案文档: `specs/astock_daily_update_plan.md`
> 作者: Hermes
> 日期: 2026-06-28
> 执行人: CC

---

## 一、Objective

每天收盘后自动将当日全A股行情数据增量追加到 `E:/astock/` 的 parquet 数据集中。在线源拉不到时降级到 QMT xtdata 本地数据。

并提供一个 HTML 控制页面，用于查看更新状态、手动触发更新。

---

## 二、数据源分工

> ⚠️ 本节已按 `specs/astock_data_source_probe_report.md` 实测结论修订 (2026-06-28)。
> 原"QMT xtdata 一行拿 adjFactor + 衍生字段"方案**实测不成立** (get_market_data_ex 不返回这些字段)。
> 2026-06-28 诚哥二次拍板: OHLCV 改 QMT 批量主源 (全市场~30s), 衍生字段只补关注池 (不全市场)。

```
QMT get_market_data_ex (批量) → 日线 OHLCV + vol + amount + pre_close (主源, 全市场一次取, ~30s)
                                field_list=['open','high','low','close','volume','amount','preClose']
                                stock_list=xtdata.get_stock_list_in_sector('沪深A股') (~5208只)
                                ⚠️ QMT 离线时降级 mootdx 逐只 (慢, ~30min, 仅补当天)
mootdx freq=9 (通达信TCP)      → QMT 离线时的 OHLCV 降级源 (逐只)
                                ⚠️ 必须用 frequency=9 (freq=4 的 vol 是手, 会×100)
                                ⚠️ start/count 语义诡异: 取最近 N 根后按 datetime 筛最后一行
                                ⚠️ mootdx 无 pre_close 列, 需取 target 前一根 K线的 close 作 pre_close
akshare stock_value_em        → pe/pe_ttm/pb/ps/total_mv/circ_mv/total_share/float_share (历史日频)
                                ⚠️ 只补关注池 (D:/QMT_POOL/QMTslelected.txt, ~406只), 不全市场
akshare stock_zh_a_daily      → 关注池 turnover_rate 来源 (turnover×100)
QMT instrument_detail         → up_limit/down_limit (当日截面, 只补关注池)
adj_factor                    → 当天沿用该股上一交易日值, 次日补 (不自算, dr累乘对齐已验证失败)
```

**单位转换是硬约束** (详见 4.4): amount÷1000, total_mv/circ_mv÷10000, turnover_rate×100, QMT vol 直接用(股)。

**adj_factor 不自算。** QMT xtdata 不直出 adjFactor, get_divid_factors 的 dr 累乘与 astock (tushare) 口径对不上 (107.65 vs 139.008), 自算方案废弃。当天沿用旧值, 次日补。

**关注池定义**: `D:/QMT_POOL/QMTslelected.txt` (纯 ts_code 每行一个, ~406只)。衍生字段 (pe/pb/市值/换手/涨跌停) 只为关注池补; 非关注池股票这些字段当天沿用旧值或留空。关注池路径可在脚本顶部常量配置。

---

## 三、输出产物

| 产物 | 路径 | 说明 |
|:-----|:------|:------|
| 更新脚本 | `scripts/update_astock.py` | 入口脚本，支持 CLI 参数 |
| 控制服务 | `scripts/astock_ctl_server.py` | HTTP 服务，端口 8001 |
| 控制页面 | `scripts/templates/index.html` | 单页 HTML 控制台 |
| 状态文件 | `E:/astock/update_status.json` | 最新状态，由更新脚本写入 |
| 日志目录 | `E:/astock/update_logs/` | 每次更新生成 `.log` |
| 失败队列 | `E:/astock/update_staging/failed_codes.csv` | 当天拉失败的股票，下次重试 |

---

## 四、update_astock.py 详细设计

### 4.1 CLI 参数

```
usage: update_astock.py [-h] [--auto | --check | --force-date YYYY-MM-DD | --pool-only --codes CODE1,CODE2]

--auto             自动模式: 更新当日数据 (用于 cron)
--check            检查上次更新时间, 不执行更新
--force-date YYYY-MM-DD  强制重拉指定日期
--pool-only        只更新指定股票池 (配合 --codes)
--codes            股票代码列表 (逗号分隔, ts_code 格式如 000001.SZ)
```

### 4.2 主流程

```python
def main(mode="auto", force_date=None, pool_codes=None):
    # 1. 确定目标日期 T
    #    auto → 当天
    #    force-date → 指定日期
    #    跳过非交易日 (从 astock parquet 取最新交易日历判断)

    # 2. 检查 T 日数据是否已有
    #    读 astock parquet 最后一天 trade_date
    #    如果 T <= 已有最大日期 → skip

    # 3. 获取全市场股票列表
    #    astock basic/stock_basic.parquet → ts_code 列表
    #    过滤: list_status != 'D' (未退市), is_hs == 'N'/'S' (沪深港通标的)
    #    pool_only 时缩小到指定 codes

    # 4. 从 QMT 批量拉 T 日 OHLCV (主源, 全市场一次, ~30s)
    #    codes = xtdata.get_stock_list_in_sector('沪深A股')
    #    xtdata.download_history_data2(codes, '1d', T, T)
    #    d = xtdata.get_market_data_ex(['open','high','low','close','volume','amount','preClose'],
    #                                  codes, '1d', T, T, dividend_type='none', fill_data=False)
    #    返回 dict{code: df}, df 含 open/high/low/close/volume(股)/amount(元)/preClose
    #    QMT 离线 (connect 失败) → 降级 mootdx freq=9 逐只 (client.bars start=2,count=N 筛 T 日)
    #    mootdx 无 pre_close 列 → 取 target 前一根 K线 close 作 pre_close

    # 5. 衍生字段: 只为关注池补 (D:/QMT_POOL/QMTslelected.txt, ~406只)
    #    ak.stock_value_em(symbol=六位代码) → pe/pe_ttm/pb/ps/total_mv/circ_mv/total_share/float_share (按数据日期筛T)
    #    turnover_rate: ak.stock_zh_a_daily 的 turnover 列 (×100)
    #    up_limit/down_limit: QMT instrument_detail UpStopPrice/DownStopPrice (当日截面)
    #    非关注池股票: 这些字段当天沿用旧值/留空
    #    adj_factor: 沿用该股 astock 最后一交易日值 (不自算, 一次性 duckdb 查全市场最后行, 禁逐只读1.3GB)
    #    change/pct_chg: 自算 (close-pre_close) / (close-pre_close)/pre_close*100

    # 6. 数据格式对齐 + 单位转换 (硬约束, 见 4.4)
    #    ts_code: QMT 返回已是 000001.SZ 格式; mootdx 需补后缀 (6→.SH, 0/3→.SZ, 8→.BJ)
    #    amount ÷1000 (元→千元); total_mv/circ_mv ÷10000 (元→万元);
    #    turnover_rate ×100 (小数→%); vol 直接用 (股, float)

    # 7. 读现有 parquet → 合并新数据 → 去重 → 写回
    #    读: pd.read_parquet(path)
    #    合并: pd.concat([old, new])
    #    去重: drop_duplicates(subset=['trade_date','ts_code'])
    #    写回: df.to_parquet(path)

    # 8. 更新 update_status.json
    #    {"daily": {"last_update": "2026-06-26", "n_codes": 5518,
    #               "data_source": "mootdx", "qmt_used": true},
    #     "minute": {...}, "finance": {...},
    #     "failed_codes": [...], "update_time": "..."}

    # 9. 写日志
    #    按日期写入 E:/astock/update_logs/YYYY-MM-DD.log
    #    含: 开始时间、数据源选择、成功/失败股票数、耗时
```

### 4.3 数据降级策略

按字段类型分层, 不同字段降级链独立:

```
OHLCV+amount+pre_close:
  QMT get_market_data_ex 批量 (主, ~30s) → QMT离线则 mootdx freq=9 逐只 (降级, ~30min)
  → mootdx 也失败则 akshare stock_zh_a_daily 逐只 → 跳过该股, 记 failed_codes.csv

衍生字段 (pe/pb/市值/股本/ps): 只补关注池
  akshare stock_value_em (唯一源) → 缺失则该字段沿用该股上一交易日值, 不阻断
  非关注池股票: 这些字段当天沿用旧值/留空

turnover_rate: 只补关注池
  akshare stock_zh_a_daily.turnover (唯一源) → 缺失沿用旧值

up_limit/down_limit: 只补关注池
  QMT instrument_detail (唯一源, 当日截面) → QMT 离线则沿用旧值

adj_factor:
  沿用该股上一交易日值 (不自算, 次日补)
```

**整体原则**: 任一字段缺失不阻断整条记录写入, 该字段沿用旧值或留空, 记日志。只有 OHLCV 主源+降级全失败时才跳过该股。

### 4.4 Schema 映射 + 单位转换 (硬约束)

> 实测对齐依据: `specs/astock_data_source_probe_report.md` 第二节/第四节。
> **单位转换必须逐条遵守**, 否则会出现 amount 差 1000 倍、vol 差 100 倍、市值差 10000 倍的隐蔽错误。

| astock 列 | 数据源 | 源字段 | 源单位 | 转换 | astock 单位 |
|:--|:--|:--|:--|:--|:--|
| `ts_code` | QMT 批量返回 | code | — | 已是 000001.SZ 格式, 直接 | ts_code |
| `trade_date` | 输入 T | — | — | 转 date | date |
| `open`/`high`/`low`/`close` | QMT get_market_data_ex | 同名 | 元 | 直接 | float 元 |
| `vol` | QMT get_market_data_ex | volume | 股 | 直接 (float) | float 股 |
| `amount` | QMT get_market_data_ex | amount | 元 | **÷1000** | float 千元 |
| `pre_close` | QMT get_market_data_ex | preClose | 元 | 直接 | float 元 |
| `adj_factor` | 沿用该股上日值 | — | — | 不变 | float |
| `change` | 自算 | close-pre_close | 元 | 直接 | float 元 |
| `pct_chg` | 自算 | (close-pre_close)/pre_close*100 | % | 直接 | float % |
| `pe` | akshare stock_value_em (关注池) | PE(静) | — | 直接 | float |
| `pe_ttm` | akshare stock_value_em (关注池) | PE(TTM) | — | 直接 | float |
| `pb` | akshare stock_value_em (关注池) | 市净率 | — | 直接 | float |
| `ps` | akshare stock_value_em (关注池) | 市销率 | — | 直接 | float |
| `total_mv` | akshare stock_value_em (关注池) | 总市值 | 元 | **÷10000** | float 万元 |
| `circ_mv` | akshare stock_value_em (关注池) | 流通市值 | 元 | **÷10000** | float 万元 |
| `total_share` | akshare stock_value_em (关注池) | 总股本 | 股 | 直接 | float 股 |
| `float_share` | akshare stock_value_em (关注池) | 流通股本 | 股 | 直接 | float 股 |
| `turnover_rate` | akshare stock_zh_a_daily (关注池) | turnover | 小数 | **×100** | float % |
| `up_limit` | QMT instrument_detail (关注池) | UpStopPrice | 元 | 直接 | float 元 |
| `down_limit` | QMT instrument_detail (关注池) | DownStopPrice | 元 | 直接 | float 元 |

**QMT 离线降级 mootdx freq=9 时**: mootdx vol 直接用(股), amount÷1000, pre_close 取 target 前一根 close。
**mootdx 再降级 akshare stock_zh_a_daily 时**: volume÷100 (手→股), amount÷1000, outstanding_share→float_share, turnover×100。

**不补/沿用旧值的次要列**: `volume_ratio`, `ps_ttm`, `dv_ratio`, `dv_ttm`, `free_share`, `is_st`, `suspend_timing` — 当天沿用旧值或留空, 不阻断, 后续单开通道。

### 4.5 健壮性约束 (编码必须实现)

1. **max_date 缓存**: `update_status.json` 记 `daily.max_date`。`--auto` / `--check` 判断 T 是否已有数据时**只读 status.json, 不读 1.3GB parquet**。仅当确实要写时才读 parquet。status.json 与 parquet 不一致时以 parquet 为准 (启动时一次性 duckdb 聚合校验 max_date)。

2. **status 字段必须真实**: `n_codes`/`min_date`/`max_date` **必须用 duckdb 单次聚合查询从 parquet 实读** (`SELECT count(distinct ts_code), min(trade_date), max(trade_date) FROM read_parquet(...)`), 禁止写本次成功只数 / 目标日。`--check` 显示的值必须真实。

3. **原子写**: 写 parquet 时先写 `stock_daily.parquet.tmp`, 写完 `os.replace` 原子替换。避免写到一半进程被杀导致整个数据集报废。**回补/force-date 前必须先 `cp stock_daily.parquet stock_daily.parquet.bak`** (1.3GB, 确认 E: 磁盘够)。

4. **force-date 不污染已有完整记录**: force-date 模式下, 对 (date, ts_code) 已有记录, **若新取数据 pre_close/up_limit 等关键字段为 NaN, 不得用 NaN 覆盖原值** (drop_duplicates 用 keep='first' 保留原完整记录, 或合并时按字段优先取非 NaN)。这是 v1 污染 000001@0618 的根因修复。

5. **并发锁**: 更新进程启动即写 `E:/astock/update_staging/update.lock` (含 pid + 时间)。`--auto` / ctl_server POST 启动前检查锁: 锁存在且进程存活 → 拒绝启动, 返回 `{"status":"already_running"}`。ctl_server 的 `update_running` 字段同源。进程退出 (正常/异常) 删锁 (atexit + try/finally)。

6. **独立交易日历**: 判断 T 是否交易日**不依赖 astock parquet**。用 `akshare.tool_trade_date_hist_sina()` 取交易日历, 缓存到 `E:/astock/update_staging/trade_calendar.parquet` (30天刷新)。T 非交易日 → 直接 skip, 不报错。注意 0619 端午/周六周日确实非交易日, 日历判断本身正确。

7. **failed_codes 消费**: `failed_codes.csv` 含 `code, date, reason`。每次 `--auto` 先读队列, 对队列中的 (code, date) 优先重试; 重试成功移出队列, 仍失败保留并更新 reason。队列超过 30 天的记录归档不再重试。

8. **性能约束 (实测)**: QMT 在线时 OHLCV 全市场批量 ~30s; 衍生字段只补关注池 ~406只×2s≈15min。akshare 逐只有限流, 失败重试不崩。**禁逐只读 1.3GB parquet** (get_adj_factor 必须一次性 duckdb 查全市场最后行 adj_factor 建 dict)。

9. **本轮范围 = 日线增量**。minute / finance 不在本次工单范围。astock 当前 max=2026-06-18, 首次运行需支持 `--force-date 2026-06-22` 起回补 0622~当日 (0619端午/0620-21周末/0627周日跳过) 的历史缺口。涨跌停历史值无源 → 沿用旧值, 不用 QMT 当日截面覆盖历史。

---

## 五、astock_ctl_server.py 详细设计

### 5.1 API 端点

```
GET  /                    → 返回 templates/index.html
GET  /api/status          → 读取 update_status.json 并返回
POST /api/update/daily    → 后台执行 update_astock.py --auto (非阻塞)
POST /api/update/minute   → 后台执行 update_astock.py --pool-only (暂未实现)
POST /api/update/finance  → 返回"手动更新, 暂未实现"
GET  /api/logs            → 返回 update_logs/ 下最近3条日志内容
GET  /api/config          → 返回配置信息 (数据源顺序、路径等)
```

### 5.2 /api/status 返回格式

```json
{
  "daily": {
    "last_update": "2026-06-26",
    "min_date": "2009-01-05",
    "max_date": "2026-06-26",
    "n_codes": 5518,
    "last_data_source": "mootdx",
    "qmt_used": true
  },
  "minute": {
    "last_update": "2026-06-18",
    "n_codes": 5793,
    "coverage_note": "需手动或按需触发"
  },
  "finance": {
    "last_quarter": "2026Q1",
    "last_update": "2026-06-18"
  },
  "failed_queue": [
    {"code": "000001.SZ", "date": "2026-06-26", "reason": "mootdx超时"}
  ],
  "update_running": false,
  "last_update_time": "2026-06-28T17:30:01"
}
```

### 5.3 技术实现

- 使用 Python 内置 `http.server` 模块，无需 Flask/FastAPI 等外部依赖
- `/api/update/daily` 用 `subprocess.Popen` 后台启动更新进程，不阻塞请求
- 设置 `CORS` 响应头以便前端跨域访问
- 启动端口 8001，默认 bind 127.0.0.1

---

## 六、控制页面 (templates/index.html)

### 6.1 页面布局

```
┌─────────────────────────────────────────────────────┐
│  📊 astock 数据控制台                    [🔄刷新]   │
├─────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐│
│  │ 日线 📅  │ │ 分钟线 ⏱ │ │ 财务 📊  │ │ 数据源 📡││
│  │ 06-26    │ │ 06-18    │ │ 2026Q1  │ │ mootdx   ││
│  │ 5518只   │ │ 5793只   │ │ 6张表   │ │ ✅在线   ││
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘│
│                                                      │
│  ── 操作 ──                                           │
│  [🔄更新日线] [🔄更新分钟线] [🔄更新财务] [📋查看日志]│
│                                                      │
│  ── 最近日志 ──                                       │
│  17:30:01 ✅ 日线更新完成 (5132只, mootdx, +1日)     │
│  17:30:01 ⚠️ mootdx超时→降级akshare                  │
│  17:28:15 ❌ 分钟线: 未实现                           │
│                                                      │
│  ── 失败队列 ──                                       │
│  (空)                                                 │
└─────────────────────────────────────────────────────┘
```

### 6.2 技术要求

- 单页 HTML + 内联 CSS/JS，无外部依赖
- 页面加载时自动 fetch `/api/status` 渲染状态
- "刷新"按钮重新请求 `/api/status`
- 操作按钮点击后调对应 POST 端点，显示 loading 状态
- 日志区域显示最近3条，可展开查看更多
- 颜色示意：✅绿色 ⚠️黄色 ❌红色

---

## 七、执行环境

```bash
# 运行环境: py -3.10 (Windows Python 3.10)
# 已修复: __editable__.qmt_mcp_server-0.1.0.pth GBK→UTF-8

# 运行更新 (手动)
py -3.10 scripts/update_astock.py --auto

# 启动控制台
py -3.10 scripts/astock_ctl_server.py --port 8001
# 浏览器访问 http://localhost:8001

# 检查状态
py -3.10 scripts/update_astock.py --check
```

---

## 八、代码风格 & 约束

- Python 3.10 兼容语法，不超出此版本特性
- 编码：所有 .py 文件用 UTF-8
- **只读不碰回测代码**：不要修改 `backtest/` 下的 reader/engine/test 文件
- 更新脚本只写 `E:/astock/daily/stock_daily.parquet` 和状态文件
- 错误处理：任何一步失败不能导致脚本崩溃，必须记录并优雅降级
- 耗时长的操作（全市场拉取）控制在 5 分钟内

---

## 九、测试验收

| # | 验收项 | 通过标准 |
|:--|:-------|:---------|
| 1 | 脚本 --check | 返回上次更新时间, 不抛异常 |
| 2 | 脚本 --auto (mootdx正常) | 日志显示从 mootdx 拉取, 成功写入 parquet |
| 3 | 脚本 --auto (mootdx断开) | 日志显示降级 akshare, 成功写入 |
| 4 | 脚本 --auto (全部离线) | 日志显示 "跳过当日", failed_codes.csv 有记录 |
| 5 | 控制台 GET / | 返回 index.html, 状态页面正常渲染 |
| 6 | 控制台 GET /api/status | 返回正确 JSON |
| 7 | 控制台 POST /api/update/daily | 返回 `{"status": "started"}`, 后台开始更新 |
| 8 | 重复运行 --auto (当日已有数据) | 脚本快速退出, 不重复拉取 |
| 9 | astock parquet 完整性 | 更新后 parquet 仍可被 AstockParquetReader 正常读取 |
| 10 | 单位转换 | 抽样 5 只股对齐 astock 既有日: amount 千元 / total_mv 万元 / turnover_rate % / vol 股, 量级与历史一致 (不差 1000/100/10000 倍) |
| 11 | 并发锁 | 更新进行中再发 `--auto` 或 POST /api/update/daily → 返回 already_running, 不启动第二进程 |
| 12 | 原子写 | 更新写 tmp 过程中 kill 进程 → 原 parquet 未损坏, --check 仍可正常读 |
| 13 | akshare 衍生字段 | 更新后当日记录 pe/pb/total_mv/turnover_rate 非全 NaN (akshare 在线时) |
| 14 | 历史回补 | `--force-date 2026-06-19` 起回补到当日, 每日记录齐全, parquet max_date 推进到当日 |
| 15 | 非交易日 skip | 周末/节假日跑 --auto → 读 trade_calendar 判断后 skip, 不写空记录, 不报错 |

---

## 十、边界

1. **非交易日**：识别并跳过，参考 astock parquet 已有交易日历
2. **退市股票**：基础信息 `list_status='D'` 的不参与更新
3. **新股**：当日新上市股票，mootdx 有数据则加入，无则跳过
4. **停牌**：停牌股 OHLCV 数据为当日值(即不变)，adj_factor 沿用
5. **北交所**：`.BJ` 后缀，2009年时无北交所，早期数据跳过
6. **节假日**：调休上班日是交易日，正常更新；放假休市日跳过
