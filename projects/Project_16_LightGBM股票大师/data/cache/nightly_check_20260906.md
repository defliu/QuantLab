# Project_16 夜间检修 2026-09-06 20:51

**结论**：FAIL 0 / WARN 1 / 总检查 22

| 模块 | 检查项 | 状态 | 说明 |
|---|---|---|---|
| A | A1 增量库最新日 | **PASS** | 2026-09-04 |
| A | A3 moneyflow最新日 | **PASS** | moneyflow 2026-09-04（增量 2026-09-04，滞后应<=5自然日） |
| A | A2 主数据源文件 | **PASS** |  |
| A | A4 财务PIT最新日 | **PASS** | 2026-08-21 |
| B | B1 面板最新日 | **PASS** | 2026-09-04 |
| B | B2 模型-面板同步 | **PASS** | 校验结果: 一致（面板与正式模型同版） |
| B | B3 正式模型存在 | **PASS** | 22625 KB |
| B | B4 G2 候选产物 | **PASS** | 最近: 20260904_g2_top2.csv |
| C | C2 前日任务日志 | **PASS** | 当日日志 49 条 |
| C | C1 次日任务Active | **WARN** | 由检修任务指令核对 Schedule 状态 |
| D | D1 悟道探活 | **PASS** | market_overview |
| D | D1 腾讯探活 | **PASS** | sh000001 |
| D | D2 熔断状态 | **PASS** | 熔断中: 无 |
| E | E1 QMT 进程 | **PASS** | 存活: ['XtMiniQmt.exe', 'XtItClient.exe', 'XtItClient.exe'] |
| E | E2 桥心跳 | **PASS** | 最近 heart_20260906.json 距今 10h |
| E | E3 账本戳 strategy_capital.json | **PASS** | account_id=67014907 |
| E | E3 账本戳 g2_strategy_capital.json | **PASS** | account_id=70180771 |
| E | E5 对账报告 | **PASS** | 最近: reconcile_20260904.md |
| F | F1 磁盘 D盘 | **PASS** | 剩余 77.2 GB |
| F | F1 磁盘 QMT_POOL | **PASS** | 剩余 77.2 GB |
| F | F3 脚本冒烟 | **PASS** |  |
| F | F4 卖出规则一致性 | **PASS** |  |

---
自动生成：`scripts/nightly_check.py`
