# Project_16 夜间检修 2026-09-10 17:22

**结论**：FAIL 0 / WARN 1 / 总检查 23

| 模块 | 检查项 | 状态 | 说明 |
|---|---|---|---|
| A | A1 增量库最新日 | **PASS** | 2026-09-10 |
| A | A3 moneyflow最新日 | **PASS** | moneyflow 2026-09-08（增量 2026-09-10，滞后应<=5自然日） |
| A | A2 主数据源文件 | **PASS** |  |
| A | A4 财务PIT最新日 | **PASS** | 2026-08-21 |
| B | B1 面板最新日 | **PASS** | 2026-09-10 |
| B | B2 模型-面板同步 | **PASS** |  |
| B | B3 正式模型存在 | **PASS** | 5314 KB |
| B | B4 G2 候选产物 | **PASS** | 最近: _bak_20260909 |
| C | C2 前日任务日志 | **PASS** | 当日日志 87 条 |
| C | C1 次日任务Active | **WARN** | 由检修任务指令核对 Schedule 状态 |
| D | D1 悟道探活 | **PASS** | market_overview |
| D | D1 腾讯探活 | **PASS** | sh000001 |
| D | D2 熔断状态 | **PASS** | 熔断中: 无 |
| E | E1 QMT 进程 | **PASS** | 存活: ['XtMiniQmt.exe', 'XtItClient.exe', 'XtItClient.exe'] |
| E | E2 桥心跳 | **PASS** | 最近 heart_20260910.json 距今 2h |
| E | E3 账本戳 strategy_capital.json | **PASS** | account_id=67014907 |
| E | E3 账本戳 g2_strategy_capital.json | **PASS** | account_id=70180771 |
| E | E5 对账报告 | **PASS** | 最近: reconcile_20260910.md |
| F | F1 磁盘 D盘 | **PASS** | 剩余 80.1 GB |
| F | F1 磁盘 QMT_POOL | **PASS** | 剩余 80.1 GB |
| F | F2 QMT_POOL清理 | **PASS** | 清理 0 个旧文件 |
| F | F3 脚本冒烟 | **PASS** |  |
| F | F4 卖出规则一致性 | **PASS** |  |

---
自动生成：`scripts/nightly_check.py`
