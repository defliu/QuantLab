# SPEC：模拟端验证流程 + 可观测性增强（A+C）

**日期**: 2026-07-03
**作者**: CC
**状态**: 待诚哥确认 C 日志清单后出工单实施

---

## 背景

最近 BUG 8成在 QMT 集成层（时序/异步/字段/文件/生命周期），MOCK 模拟不到。`adapters/context_mock.py` 只测信号逻辑：`passorder` 同步返回 True、`MockPos.m_nVolume=0`（永远空仓）、`get_trade_detail_data('order'/'deal')` 返回 `[]`、`get_current_time` 固定 2024-05-30。所以 MOCK 跑通 ≠ 实盘跑通。

模拟端 `\\192.168.31.131`（67014907）是真实 QMT 环境（真实行情+模拟撮合），今天的 BUG 全是它暴露的。**模拟端就是最好的 MOCK**，问题是没有流程化、部署后靠记忆看日志、漏检。

---

## 方案 A：模拟端验证流程化

**部署实盘前强制流程**：

```
改代码 → MOCK+单测(抓逻辑) → rebuild + validate 6/6 → 部署模拟端 → 跑1交易日 → 过 checklist → 部署实盘
```

**部署前 checklist**（看模拟端 `userdata/log/XtClient_FormulaOutput_YYYYMMDD.log`）：

| # | 检查项 | 日志关键字 | 通过标准 | 踩坑来源 |
|---|--------|-----------|---------|---------|
| 1 | 新代码生效 | `策略版本=vYYYY.MM.DD-xxx` | 版本号=本次 bump | 0702 诚哥粘错中间版 |
| 2 | 时钟正常 | `[时间校验] 行情时间= 设备时间=` | 差<5min | 0702 CMOS 快3h |
| 3 | 持仓纳管 | `[持仓纳管] 已纳入` | 孤儿票被纳 | 0702 603283 没纳管 |
| 4 | 卖出评估 | `[卖出评估] <code>` | 持仓都被评估 | 0702 BUG4 |
| 5 | 导出CSV | `成交明细_*.csv`/`持仓明细_*`/`资金概况_*` | 15:00 产出3文件 | 0703 1505 不触发 |
| 6 | 反查无死循环 | `[卖出反查失败]` | 不反复+冷却；`lookup_diag_*.csv` 有数据 | 0703 BUG5 |
| 7 | 盘前预埋(启用时) | `集合竞价预埋扫描 (mode=G3_ONLY)` | 09:25 出现 | 0703 OFF→G3 |
| 8 | 策略名 | `[主升浪6+2]` | 名称对（自包含config生效）| 0701 __file__ 坑 |

不通过 → 不上实盘，回炉修。

---

## 方案 C：可观测性日志增强

**现有**（0703 确认有）：`[时间校验]`/`[持仓纳管]`/`[卖出评估]`/`[反查诊断]`/`premarket_diag_*.csv`/`[导出]`尝试/`[持仓同步]`

**补充 4 项**（待工单实施，目标 `adapters/qmt_wrapper.py`）：

### C-1. handlebar 时段进入日志
在 `_handlebar_impl` 各时段分支首次进入打一行，每时段每天一次（防刷屏，用类似 `_g_wait_printed` 的去重集合）：
- 0925 `[时段] 0925 集合竞价预埋窗口`
- 0930 `[时段] 0930 开盘卖出评估(底线层)`
- 0940 `[时段] 0940 全层卖出开启`
- 1000 `[时段] 1000 买入窗口10:00-10:10`
- 1458 `[时段] 1458 收盘序列`
- 1500 `[时段] 1500 收盘帧导出`
**目的**：确认 handlebar 时序正常、各时段真触发（今天导出 1505 不触发就是没这种日志才没早发现）。

### C-2. 导出结果明细
`export_daily_data` 成功后打 `[导出] 完成 产出N文件: <文件名列表>`；失败打 `[导出] 失败 原因=<异常类型:msg>`（当前 try 只打笼统"自动导出失败"，看不出哪步崩）。
**目的**：导出问题快速定位。

### C-3. init 步骤耗时
`init` 关键步骤各打 `[init] <步骤> 耗时<Ns>`：config 读取 / 数据加载 / 交易通道就绪 / 持仓同步 / 累计盈亏重建。
**目的**：定位启动卡点（如 init 首帧通道未就绪导致 get_holdings 空返——0702 BUG2 根因）。

### C-4. 持仓对账
`init` 末尾 + 15:00 收盘帧各打一次 `[对账] _g_my_codes(N只) vs account(M只) 差集=<列表>`；不一致打 `[对账告警]`。
**目的**：孤儿持仓第一时间发现（0702 603283 就是对账不一致才暴露）。

---

## 实施

- **A**：本 SPEC 的 checklist 落盘到 `knowledge_base/60_工程知识库/QMT模拟端部署验证清单.md`（诚哥部署时查的运营文档）
- **C**：出工单给 MIMO 改 `adapters/qmt_wrapper.py`（C-1~C-4）+ rebuild + validate 6/6 + commit

## 关联踩坑 memory

`orphan-holdings-adopt-20260702` / `sell-order-instant-lookup-false-fail` / `export-dual-entry-cooling-off` / `qmt-account-asset-fields` / `qmt-reinstall-check-strategy-startup` / `qmt-sim-lan-log-path` / `holdings-time-dedup-fix-20260630`
