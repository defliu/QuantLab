# astock 数据每日增量更新方案

> 作者: Hermes (架构设计)
> 日期: 2026-06-28
> 状态: 待诚哥审

---

## 一、需求摘要

每天收盘后自动将当日A股行情数据增量更新到 `E:/astock/` 的 parquet 数据集中。在线源拉不到时，降级到 QMT xtdata 本地数据补全。

---

## 二、数据现状

| 数据 | 路径 | 当前截止 | 格式 | 大小 |
|:-----|:------|:---------|:-----|:-----|
| 日线 | `E:/astock/daily/stock_daily.parquet` | 2026-06-18 | 单文件, MultiIndex (trade_date, ts_code) | 1.3GB |
| 1分钟 | `E:/astock/minute/1min/<code>.parquet` | 同 | 每股一文件 | 67GB |
| 财务6表 | `E:/astock/finance/*.parquet` | 2026Q1 | 按表分文件 | 553MB |

日线 parquet 关键列：`ts_code, open, high, low, close, vol, amount, adj_factor, pre_close, change, pct_chg, turnover_rate, volume_ratio, pe, pb, up_limit, down_limit, total_mv, circ_mv, is_st, suspend_timing...`

---

## 三、数据源选择与优先级

### 3.1 数据源层次

```
优先级1: mootdx (通达信TCP直连)
  优势: 全A股覆盖, free, 有xdxr(除权除息)可算adj_factor, 返回原始价
  风险: 通达信服务器可能限流

优先级2: akshare (东方财富后端)
  优势: akshare.stock_zh_a_daily() 免费, 返回原始价(adjust='')
  劣势: 无adj_factor直出字段, 需从raw/qfq价差推算

优先级3: QMT xtdata (本地)
  优势: 本地数据, 有完整 adj_factor, 不依赖网络
  限制: 需要QMT模拟端运行, py -3.10
```

### 3.2 数据源分工（诚哥拍板）

```
mootdx → 日线 OHLCV + vol + amount (原始价)
         全市场, 最快, 2分钟左右完成

QMT xtdata → adj_factor + pe/pb/市值/股本等衍生字段
             get_market_data_ex(fields=['adjFactor','pe','pb','totalShares',...])
             一行代码, 零计算零验证成本, 已有项目在用的方式

akshare → mootdx 失败时的降级 (日线 OHLCV)
          仅当通达信TCP不通时启用

三源全挂 → 跳过当日, 第二天重试
```

不自己算 adj_factor。mootdx xdxr 计算复杂且验证成本高，直接用 QMT xtdata 的 adjFactor 更可靠。

---

## 四、数据更新策略

### 4.1 日线更新（核心，每日执行）

**流程：**

```
1. 获取当日日期 T (如 2026-06-26)
2. 检查 T 在 astock parquet 中是否已有数据 → 跳过
3. 检查 T 是否为交易日 → 跳过非交易日
4. 通过 mootdx 拉全A股 T 日数据
5. 若 mootdx 失败 → 降级到 akshare 逐个拉取
6. 通过 QMT xtdata 补 adj_factor + pe/pb/股本等衍生字段
7. 若 QMT xtdata 也失败 → adj_factor 沿用上一交易日
8. 数据格式对齐（列名映射、ts_code补后缀 .SZ/.SH）
9. 读现有 parquet → 合并新数据 → 去重 → 写回
```

### 4.2 分钟线更新（按需，只看池子）

只更新关注池/持仓池的 1min parquet，不全市场扫。

mootdx 分钟线接口：`client.bars(symbol, frequency=8, start=0, count=240)` 取当天全部1分钟线。

### 4.3 财务数据更新（季度触发）

不自动跑。每季度末检查 `fina_indicator.parquet` 的最大 `ann_date`，若有新季度已披露则手动触发。

---

## 五、adj_factor 处理（简化方案）

**核心原则：不自己算，直接用 QMT xtdata 的 adjFactor。**

### 5.1 流程

```
1. mootdx 拉取当日 OHLCV + vol + amount（原始不复权价）
2. QMT xtdata.get_market_data_ex(fields=['adjFactor','pe','pb',...]) 拉取当日 adj_factor
3. 直接写入 parquet
```

### 5.2 QMT xtdata 数据获取

```python
from xtquant import xtdata

# 下载当日数据到本地缓存
xtdata.download_history_data2(codes, '1d', T_str, T_str)

# 取 adj_factor 等字段
fields = ['adjFactor', 'pe', 'pe_ttm', 'pb', 'totalShares', 'circulatingShares',
          'turnover', 'upLimit', 'downLimit', 'isST']
market_data = xtdata.get_market_data_ex(fields, codes, '1d', 1)

# market_data 格式: (stock_code × field) MultiIndex DataFrame
# 取出每个股票的 adj_factor
```

> ✅ 此方式已在项目中验证（`build_core_universe.py` 和 `build_full_a_pit_manifest.py` 都在用）
> ✅ 零计算、零验证成本、零除权事件处理
> ❌ mootdx xdxr 自算 adj_factor 方案已废弃（复杂且验证成本高）

### 5.3 当 QMT xtdata 不可用时

QMT 未启动或 xtquant 不可用 → adj_factor 沿用上一交易日值，pe/pb/股本留空。
下一交易日 QMT 恢复后再补拉。

---

## 六、降级策略明细

```
mootdx 失败条件:
  - 通达信TCP连接超时
  - 返回空数据
→ 降级到 akshare

akshare 失败条件:
  - 东方财富API被限流
  - 网络不通
→ 降级到 QMT xtdata

QMT xtdata 失败条件:
  - QMT未启动/xtquant不可用
→ 记录失败日志, 跳过当日, 第二天重试
```

---

## 七、输出产物

| 产物 | 路径 | 说明 |
|:-----|:------|:------|
| 主脚本 | `D:\QMT_STRATEGIES\scripts\update_astock.py` | 每日更新入口 |
| 日志 | `E:/astock/update_logs/YYYY-MM-DD.log` | 每次更新的详细日志 |
| 状态文件 | `E:/astock/update_status.json` | 最新更新时间、覆盖范围等 |
| 错误队列 | `E:/astock/update_staging/failed_codes.csv` | 当天拉取失败的股票, 下次重试 |

---

## 八、执行方式

```
# 手动执行
py -3.10 scripts/update_astock.py

# 检查上次更新时间
py -3.10 scripts/update_astock.py --check

# 强制重拉某日
py -3.10 scripts/update_astock.py --force-date 2026-06-23

# 仅更新指定股票池
py -3.10 scripts/update_astock.py --pool-only --codes 000001.SZ,600000.SH

# 自动模式 (Hermes cronjob 17:30 触发)
py -3.10 scripts/update_astock.py --auto
```

Hermes cron 配置：
```yaml
# cronjob 创建:
#   schedule: "30 17 * * 1-5"    # 工作日 17:30
#   prompt: "运行每日 astock 数据更新"
#   script: "scripts/update_astock.py --auto"
#   注意: 需确认 py -3.10 在 cron 环境下可用
```

---

## 九、schema 映射表

### mootdx bars → astock parquet

| mootdx 列 | astock 列 | 转换 |
|:----------|:----------|:------|
| datetime | trade_date | 提取日期部分, 转 date 类型 |
| (股票代码) | ts_code | 补后缀: 6位数字+SZ/SH |
| open | open | 直接用 |
| high | high | 直接用 |
| low | low | 直接用 |
| close | close | 直接用 |
| vol | vol | 直接用(股) |
| amount | amount | 直接用(元) |
| (无) | adj_factor | 从xdxr计算或沿用旧值 |
| (无) | pre_close | 昨收价(从旧数据取) |
| (无) | pct_chg | (close - pre_close)/pre_close × 100 |
| (无) | turnover_rate | mootdx无, 从akshare补充或留空 |

### akshare stock_zh_a_daily → astock parquet

| akshare 列 | astock 列 | 转换 |
|:-----------|:----------|:------|
| date | trade_date | 直接 |
| (symbol) | ts_code | 补后缀 |
| open | open | 直接 |
| high | high | 直接 |
| low | low | 直接 |
| close | close | 直接 |
| volume | vol | volume(股) |
| amount | amount | 直接 |
| outstanding_share | float_share | 流通股本(股) |
| turnover | turnover_rate | 换手率 |

### QMT xtdata → astock parquet

QMT xtdata `get_market_data_ex()` 字段：
- 返回 MultiIndex (stock_code, field) DataFrame
- fields: open, high, low, close, preClose, volume, amount, adjFactor, turnover, pe, pb, totalShares, circulatingShares
- 天然包含 adj_factor 字段，无需计算

---

## 十、HTML 控制页面设计

文章中的系统有前端仪表盘（实时查看持仓、信号、权益曲线），这里单独给 astock 数据更新做一个**控制页面**，功能聚焦在数据更新状态监控和手动操作。

### 10.1 页面布局

```
┌─────────────────────────────────────────────────────┐
│  📊 astock 数据控制台                    [刷新]     │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌───────┐  ┌───────┐  ┌───────┐  ┌───────┐       │
│  │ 日线   │  │ 分钟线 │  │ 财务   │  │ 数据源 │       │
│  │ 2026.. │  │ 2026.. │  │ 2026Q1│  │ mootdx│       │
│  └───────┘  └───────┘  └───────┘  └───────┘       │
│                                                     │
│  ── 操作面板 ──                                      │
│  [🔄 更新日线]  [🔄 更新分钟线]  [🔄 更新财务]       │
│  [🔍 检查状态]  [📋 查看日志]                        │
│                                                     │
│  ── 最近更新日志 ──                                   │
│  2026-06-28 17:30:01 ✅ 日线更新完成 (5132只)        │
│  2026-06-28 17:30:01 ⚠️ mootdx超时, 降级akshare      │
│  2026-06-28 17:28:15 ❌ 分钟线: 网络不通, 跳过       │
│                                                     │
│  ── 数据覆盖概况 ──                                   │
│  日线: 2009-01-05 ~ 2026-06-26 (5518只)             │
│  1分钟: 5793只                                       │
│  财务: 2005Q1 ~ 2026Q1 (6表)                        │
│                                                     │
│  ── 更新失败队列 ──                                   │
│  (空)                                                │
└─────────────────────────────────────────────────────┘
```

### 10.2 技术方案

**架构：Python HTTP 服务 + 单页 HTML**

```
┌──────────────────────────────────────────────────┐
│            用户浏览器 (HTML + JS)                   │
│  显示状态面板, 点击按钮触发操作                      │
└──────────────┬───────────────────────────────────┘
               │ fetch API
┌──────────────▼───────────────────────────────────┐
│        Python HTTP Server (8001端口)               │
│                                                    │
│  GET  /api/status    ← 读取 update_status.json     │
│  POST /api/update/daily    ← 执行更新脚本          │
│  POST /api/update/minute   ← 执行分钟线更新        │
│  GET  /api/logs            ← 返回最近日志          │
│  GET  /                    ← 返回 index.html       │
└──────────────────────────────────────────────────┘
```

### 10.3 API 设计

| 端点 | 方法 | 功能 | 返回 |
|:-----|:------|:-----|:------|
| `/api/status` | GET | 当前数据状态 | `{daily, minute, finance: {last_update, coverage}, data_source, failed_queue}` |
| `/api/update/daily` | POST | 触发日线更新 | `{status: running/ok/error, message}` |
| `/api/update/minute` | POST | 触发分钟线更新 | 同上 |
| `/api/update/finance` | POST | 触发财务更新 | 同上 |
| `/api/logs` | GET | 最近50条日志 | `[{time, level, message}]` |
| `/api/config` | GET | 当前配置 | data source order, pool path, etc. |

### 10.4 启动方式

```
# 手动启动服务
py -3.10 scripts/astock_ctl_server.py --port 8001

# 浏览器打开
http://localhost:8001

# 后台常驻 (Windows)
start /B py -3.10 scripts/astock_ctl_server.py --port 8001
```

### 10.5 与文章的区别

文章的系统是完整的**交易仪表盘**（实时持仓、信号、权益曲线）。这里只做**数据更新控制台**，聚焦：
- ✅ 数据更新状态一目了然
- ✅ 手动触发更新（网络出问题时排查用）
- ✅ 降级/失败告警
- ❌ 不做交易信号显示（那是回测系统的事）
- ❌ 不做持仓/权益（那是 QMT 的事）

如果之后想把回测状态的监控也加进来，可以扩展这个页面。

---

## 十一、决策记录 (2026-06-28 诚哥拍板)

| 序号 | 问题 | 决策 |
|:-----|:------|:------|
| 1 | 主数据源 | **mootdx 做主源 + QMT xtdata 兜底** |
| 2 | adj_factor | **不自己算，QMT xtdata 直接拿 adjFactor** |
| 3 | QMT 模拟端状态 | — |
| 4 | 分钟线范围 | **全市场**（不限于关注池） |
| 5 | 日线范围 | **全市场 5000+ 只** |
| 6 | py -3.10 GBK 问题 | ✅ **已修复** — `__editable__.qmt_mcp_server-0.1.0.pth` 从 GBK 转 UTF-8，验证通过 |
| 7 | 调度方式 | 后续再说 |
| 8 | 控制台自启动 | 后续再说 |

py -3.10 修复详情：
- 根因：`C:\Users\Administrator\AppData\Local\Programs\Python\Python310\Lib\site-packages\__editable__.qmt_mcp_server-0.1.0.pth` 包含路径 `D:\Administrator\Desktop\量化\qmt-mcp-server\src`，其中"量化"二字为 GBK 编码，Python site 模块按 UTF-8 读导致 `UnicodeDecodeError`
- 修复：用 GBK 解码后以 UTF-8 重写该文件
- 验证：`py -3.10 -c "import sys; print(sys.version)"` 返回 Python 3.10.11，正常
