# 只读体检报告：夜间检修任务（nightly_check）遗漏与优化审计（2026-09-12）

> 审计对象：`D:\QuantLab\projects\Project_16_LightGBM股票大师\scripts\nightly_check.py`（342 行，A-G 七模块）+ `nightly_check_task.ps1`（Quant_P16_NightlyCheck_0100，每日 01:00 --fix）
> 审计方式：纯只读（源码通读 + schtasks /query + 文件时间戳/内容读取 + parquet 元数据读取），未修改任何源文件、未运行任何写文件脚本、无 git 操作。
> 审计范围：09-09~09-12 夜检报告、rebalance_daily.py / reconcile_ens.py / g2_config.py / g2_ens_config.py / data_source_router.py / refresh_panel_v3.py / run_scheduled.ps1 / heartbeat_alarm.ps1 / check_bridge_heartbeat.py、QMT_POOL 三桥现场、schtasks 全量 Quant_* 任务。

---

## 1. 结论摘要表

| 模块 | 现状（09-12 报告） | 核心问题 | 一句话建议 |
|---|---|---|---|
| A 数据完整性 | 4 项全 PASS | **A1 恒真（tautology）**：`last_trade_date()` 就是增量库自身，A1 永不可能 FAIL，数据断流静默 | 用 is_trade_day 日历做外部锚（P0，必须做） |
| B 面板与模型 | 4 项全 PASS | B1 同受 A1 tautology 连带；**enh 面板（冻结 08-14）与 ENS 候选完全没查**；B4"最近"显示是 `_bak` 目录名 | 补 enh/ENS/merged 新鲜度三项 + 修 B4 排序（P1） |
| C 调度健康 | C1 WARN / C2 PASS | **C1 纯人工占位**；实证缺位：Heartbeat_Alarm 曾缺失 9 天无人发现，V13_Guard Last Result 0x800710E0 无人发现 | schtasks 白名单自动查询 + TW 任务用产物痕迹兜底（P1，必须做） |
| D 数据源连通 | D1 双探活 PASS / D2 WARN | **D2 口径过时**：`opened_at` 残留 ≠ 熔断中（300s 早已到期），连续两晚假 WARN"熔断中 mcp_tdx" | 按 remaining 时长判真熔断；超期未恢复另立信息项（P1） |
| E QMT/执行层 | 5 项全 PASS | **E2 只查 G2 一座桥、仅 mtime、24h 阈值过松**——09-11 V13 NUL 心跳 P0 事故在该口径下照样 PASS（三重漏检实证）；E3 缺 ENS 账本；E5 只查 V1.3 对账 | 三桥 + JSON 内容校验 + "最后交易日 ≥14:50 有心跳"阈值；E3/E5 补 ENS（P0+P1，必须做） |
| F 环境与资源 | F1/F2/F3 PASS / F4 FAIL | **F4 是误报**（字面量匹配被 T0_LIQUIDATE 重构打破）；F3 冒烟清单缺整条 ENS 链；F2 清理模式过宽（当前无实害） | F4 改语义匹配；F3 补 rebalance_ens.py 等（P1，必须做） |
| G 报告与告警 | FAIL 1 / WARN 2，exit 1 | **--push-alert 无实现**（形同虚设）；FAIL 后无主动推送，09-12 01:00 的 FAIL 静默 11h+ 至本审计 | 接现成 notify_feishu 组件，FAIL≠0 即推（P1，必须做） |
| 基础设施 | 任务 Last Result=1 可观察 | `run()` subprocess 无 encoding → GBK 解码 UTF-8 子进程输出抛 UnicodeDecodeError（日志实证），输出丢失可能掩盖 FAIL 细节 | `encoding="utf-8", errors="replace"`（P1） |

**总评**：nightly_check 骨架健康（fail-loud exit 1 → LastTaskResult=1 已实证闭环、--fix 修复逻辑克制、无越权误修实害），但它体检的仍是 **09-03 时代的单策略（V1.3+G2雏形）体系**；对照 09-10/09-11 后的**三策略（V1.3/G2/ENS）+ 三桥 + 心跳告警 + 资金池滚动**现状，存在 2 个 P0 级漏检（数据新鲜度恒真、桥健康三重漏检）和一批 P1 缺口。09-12 唯一的 FAIL（F4）是**误报**，反而掩盖了它本该具备的真回归检测能力。

---

## 2. 逐项明细（A-G，附证据）

### A 数据完整性

| 项 | 现状 | 判定 | 证据 |
|---|---|---|---|
| A1 增量库最新日 | PASS 2026-09-11 | **检查恒真，P0** | `nightly_check.py:65-69`：`last_trade_date()` 直接 `return incr_max_date()`（增量库自身）；`:76-81` 比较 `incr == latest` → 永远相等。增量断流 N 天 A1 仍 PASS，次日 09:15 候选静默用旧数据。唯一弱兜底是 A3 的 5 自然日滞后窗口（`nightly_check.py:92`），最坏 5 天静默 |
| A3 moneyflow | PASS（09-10 vs 09-11，滞后 1 天） | 口径本身合理，但锚同受 A1 连带（latest=增量自身） | `nightly_check.py:88-96`；刷新任务 Quant_Tushare_Moneyflow_Refresh 19:30（schtasks 实证 Last Result=0） |
| A2 主源文件 | PASS | OK（5 文件存在性检查） | `nightly_check.py:99-104` |
| A4 财务 PIT | PASS（ann 最大日 2026-08-21） | **无断言**：`:110` 无条件 `log(... "PASS", ...)`，打印日期不做任何阈值判断；08-21 属主库随 astock kit 周更的预期冻结口径，但检查无鉴别力 | `nightly_check.py:105-112` |

**--fix 修复评估**：A1 的修复（重跑 xtdata_update.py，`nightly_check.py:83-84`）本身克制、无越权；但因检查恒真，该修复**永远不会被触发**。

### B 面板与模型同步

| 项 | 现状 | 判定 | 证据 |
|---|---|---|---|
| B1 面板最新日 | PASS 2026-09-11 | 与 A1 同源缺陷：panel vs `latest`(=增量自身)。当天链路正常时 PASS 有效，但增量断流时 B1 也恒 PASS（面板与增量同步陈旧） | `nightly_check.py:118-131`；面板实测 mtime 09-11 16:40（与 16:30 daily 链一致） |
| B1 修复-重刷面板 | （未触发） | 逻辑可靠：refresh_panel_v3 重建 v2+v3+交易日历（`refresh_panel_v3.py:79-133`），1800s 超时覆盖实测 ~5-10 分钟耗时 | `nightly_check.py:126-129` |
| B2 模型-面板同步 | PASS | OK（verify_model_panel_sync.py 冒烟，120s） | `nightly_check.py:133-134` |
| B3 正式模型存在 | PASS 5314 KB | 仅存在性+大小，无新鲜度/绑定校验（绑定由 B2 间接覆盖） | `nightly_check.py:136-141` |
| B4 G2 候选产物 | PASS "最近: _bak_20260909" | **显示误导 + 无新鲜度断言**：`os.listdir` 把目录 `_bak_20260909` 也算进来，Python 序数排序 `'_'(0x5F) > '2'(0x32)` → `sorted()[-1]` 恒取到 `_bak` 目录名而非真实最新候选 `20260911_g2_top10.csv`；且只判"目录非空"，20:05 候选任务断流数日仍 PASS | `nightly_check.py:143-145`；实测目录内 `_bak_20260909`（目录）与 `20260911_g2_top10.csv`（09-11 20:07）并存 |

**B 模块遗漏**（详见第 3 节）：enh 面板、ENS 候选、merged_daily_full。

### C 调度与任务健康

| 项 | 现状 | 判定 | 证据 |
|---|---|---|---|
| C2 前日任务日志 | PASS "当日日志 82 条" | 信号价值趋零：`data/schedules/` 下混入大量 `de_*` 报告/prompt 文件（含本审计产物）；且硬编码 `"2026090"` 只匹配 09-01~09-09，09-10 起是死代码 | `nightly_check.py:151-155` |
| C1 次日任务 Active | WARN "由检修任务指令核对" | **纯人工占位**（`:156-157`），脚本侧零查询。实证缺位后果：① `Quant_P16_Heartbeat_Alarm` 看板 09-03 即声明已部署，实际 9 天不存在（`de_recheck_20260912.md:238`："系统找不到指定的文件"），09-12 补注册后才出现（本次 schtasks 实证：Enabled / Last Run 1999-11-30 / Last Result 267011 从未运行）；② `Quant_V13_Rebalance_Guard` Last Result = **-2147020576（0x800710E0，请求被拒）**、Last Run 2026-09-11 12:05:29，无人发现——注意其 10:05 正式触发是正常执行过的（guard 日志 `rebalance_daily_guard_20260911_100502_098.log` 存在，10:05:02），Last Run/Result 被 12:05 的一次被拒重跑尝试覆盖（拒因疑似 InteractiveToken 会话限制，待人工确认） | `nightly_check.py:156-157`；schtasks /query /v 实测；guard 任务 XML（MultipleInstancesPolicy=IgnoreNew、LogonType=InteractiveToken、无 StartWhenAvailable） |

**C1 自动化可行性（问题 3 答复）**：完全可行，纯只读。建议两层：
1. **schtasks 白名单层**：脚本内置期望任务清单（本次实测全量：`quant_daily_update`、`quant_weekly_retrain`、`quant_monthly_factor`、`Quant_Monitor_0945/1030/1100/1330/1400/1430`、`Quant_P16_CfgPremarket_0900`、`Quant_P16_NightlyCheck_0100`、`Quant_P16_Heartbeat_Alarm`、`Quant_V13_Rebalance_Guard`、`Quant_P16_G2Candidates_Night`、`Quant_Tushare_Moneyflow_Refresh`、`Quant_P16_DE_{G2,ENS,V13}_Compare`、`Quant_P16_DE_Report_{Midday,Close}`、`paper_forward_daily`、`QuantLab_CardWebhook_{Boot,Guard}`、`Quant_AstockKit_Update`），逐个 `schtasks /query /tn <name>`：rc≠0=缺失 FAIL；解析 `Scheduled Task State`≠Enabled=FAIL；上一交易日应触发任务的 `Last Result`≠0=WARN/FAIL（可直接抓住 V13_Guard 0x800710E0 这类异常）。
2. **TW 任务产物痕迹层**：V1.3 换仓 09:45 / G2 换仓 ~10:00 / ENS 换仓 09:52 / 三对账 15:45 是 TW 调度器任务，schtasks 不可见（`de_health_check_20260911.md:49` 实证），改为核对最后交易日的执行产物：`data/rebalance_<date>.json`、`data/rebalance_g2/rebalance_g2_<date>.json`、`data/rebalance_ens/rebalance_ens_<date>.json`（含 written 标志防 dry-run 误判）、`data/reconcile_g2*/reconcile*_<date>.md`。注意双调度器红线（T-20260910-008）：只查产物、不碰 TW 注册表。

### D 数据源连通性

| 项 | 现状 | 判定 | 证据 |
|---|---|---|---|
| D1 悟道/腾讯探活 | 双 PASS | OK：直连探活 + `--fix` 时 record ok/fail 回写健康文件，超时 30s/10s 合理 | `nightly_check.py:164-185`；datasource_health.json 实测 wudao last_ok 09-12 01:00:07（即夜检自己 record 的） |
| D2 熔断状态 | WARN "熔断中: ['mcp_tdx']"（09-11、09-12 连续两晚同 WARN） | **口径过时（问题 10 答复）**：`:191` 只判 `opened_at` 字段存在。实测 mcp_tdx `opened_at = 2026-09-11 09:53:56`，熔断窗口仅 300s（`data_source_router.py:31`），09-11 09:58 即到期转 PROBE（`data_source_router.py:81-84`）——**当前并非熔断中**，只是之后再无调用 record ok 清除记录。该 WARN 是陈旧残留假信号，连续两晚重复出现正说明无人消费/无人修 | `nightly_check.py:187-196`；`data_source_router.py:81-87`；datasource_health.json 实测内容 |

**D 模块遗漏**：不查"长期熔断未恢复"。建议：真熔断（remaining > HALF_OPEN_AFTER）才 WARN；`opened_at` 超过 24h 且期间无 `last_ok` → 单列"源长期未恢复/未再探测"信息项，区分"记录残留"与"真长期故障"。
**附带小瑕疵**：`record ok` 清 `opened_at` 但残留 `circuit: true` 字段（`data_source_router.py:96-97`；wudao 现状即如此），因 check() 只认 opened_at 无功能影响，建议顺手清理。

### E QMT / 账户 / 执行层

| 项 | 现状 | 判定 | 证据 |
|---|---|---|---|
| E1 QMT 进程 | PASS（3 项） | 小 bug：`:206` 进程清单 `["XtMiniQmt.exe", "XtItClient.exe", "XtItClient.exe"]` 重复 XtItClient 两次 → 报告显示 3 项（09-12 报告 :20 实证）。且 01:00 进程存活 ≠ 桥功能正常，仅 smoke 级 | `nightly_check.py:203-209` |
| E2 桥心跳 | PASS "heart_20260911.json 距今 4h" | **三重漏检，P0（问题 7 答复）**：① **只查 `D:/QMT_POOL/g2_bridge/state` 一座桥**（`:211`），V13（p16_v13_risk）与 ENS（p16_ensemble_bridge）桥完全不查——09-11 V13 桥 NUL 心跳 P0 事故（`heart_v13_20260911.json` 全 NUL 字节、11:10 起 3.5 小时未刷新、67014907 止损止盈监控离线，`de_health_check_20260911.md:43`）在夜检口径下照样 PASS（当天 G2 桥心跳正常 → E2 PASS）；② **仅看 mtime 不验内容**，NUL 文件/写坏 JSON 也 PASS；③ **24h 阈值过松**：交易日 11:00 桥崩溃，到次日 01:00 心跳年龄仅 ~14h < 24h → 仍 PASS，抓不到"盘中已死" | `nightly_check.py:210-222`；三桥心跳实测：g2_bridge heart_20260912.json 08:55:42 / ENS heart_20260912.json 08:59:04 / V13 heart_v13_20260912.json 08:55:32（周六客户端 08:52 启动后均有刷新，`de_recheck_20260912.md:165` 同证） |
| E3 账本戳 | 双 PASS（67014907 / 70180771） | **只查 2/3 本账（问题 8 答复）**：清单只有 `data/strategy_capital.json`（V13）与 `g2_bridge/g2_strategy_capital.json`（`:224-225`），**ENS `p16_ensemble_bridge/ens_strategy_capital.json` 不在检查范围**——该账本正是"无滚动机制"的问题账本（capital 恒 100,000.0、updated 09-10 23:36，`de_recheck_20260912.md:202,206`，T-20260912-002）。且 E3 只验 account_id 戳存在，**不验新鲜度/滚动**：G2 97,863.5（09-11 15:49 ✓）、V13 88,001.78（09-11 16:41 ✓）、ENS 100,000（09-10 后未动 ✗）三本账新旧悬殊，E3 无从分辨 | `nightly_check.py:223-235`；三账本实测值（本次 Get-Content） |
| E5 对账报告 | PASS "reconcile_20260911.md" | **只覆盖 V1.3**：`:237` 只扫 `data/` 根目录 `reconcile_*.md`（V1.3 reconcile_trades 产物）；G2（`data/reconcile_g2/reconcile_g2_20260911.md`）与 ENS（`data/reconcile_g2_ens/reconcile_g2_ens_20260911.md`）对账产物在子目录，均实测存在但夜检看不见。15:45 对账任务断流 → 次日 01:00 无告警 | `nightly_check.py:236-238`；子目录产物实测 |

**E2 阈值设计建议（问题 7 答复——交易日/非交易日区分）**：
- 交易日次日 01:00：期望最后交易日内 **≥14:50** 有心跳（三桥 ~2 分钟一写，健康交易日最后心跳应≈15:00；V13 09-11 的 11:10 即会被此阈值抓住）；
- 非交易日（周末/节假日）01:00：不要求当日心跳，但**内容必须可解析**（防 NUL 残留跨周末），mtime 允许到上一客户端会话；
- 内容校验：JSON 可解析 + `build_tag` 字段存在且与 `build/` 产物一致（三方核对目前靠 DE 日检 `de_recheck_20260912.md:167`，可固化进夜检）。
盘中实时告警已由 Quant_P16_Heartbeat_Alarm（600s 阈值，`check_bridge_heartbeat.py:21-27` 覆盖三桥）补位——但该任务 09-12 才真正注册成功（Last Result 267011 待首跑），夜检 E2 仍是唯一兜底层，不能弱化。

### F 环境与资源

| 项 | 现状 | 判定 | 证据 |
|---|---|---|---|
| F1 磁盘 | 双 PASS 80.3 GB | OK（>10GB 阈值合理） | `nightly_check.py:245-252` |
| F2 QMT_POOL 清理 | PASS 清理 0 个 | **模式过宽但当前无实害**：`:259-263` 对根目录 mtime>14d 的任意 `.txt/.log/.json` 直接删除。实测根目录当前 >14d 文件均为 `.csv` 与 `.bak_acct_*` 后缀（不在模式内），零误删。理论风险：某策略闲置 2 周，其根目录账本/状态 json 即被误删。建议白名单化（仅 `*_nav*.txt`、`strategy_log_*` 等已知日志前缀）或排除 capital/holdings/state 关键词 | `nightly_check.py:253-266`；QMT_POOL 根目录实测清单 |
| F3 脚本冒烟 | PASS | 设计好（py_compile + guard.ps1 用 pwsh7 Parser 解析规避 PS5.1 GBK 误报，`:276-287`），但**清单严重滞后**：有 7 个 py + 1 个 ps1，**缺整条 ENS 链**——`rebalance_ens.py`、`reconcile_ens.py`、`gen_positions_cfg_ens.py`、`qmt_bridge_client_ens.py`、`g2_ens_config.py`（均实测存在于项目根），以及 `check_bridge_heartbeat.py`、`is_trade_day.py`、`paper_forward.py`、`strategy_capital.py` 和其余 ps1（`heartbeat_alarm.ps1`、`nightly_check_task.ps1` 自身、`run_scheduled.ps1`、`g2_candidates_night.ps1`、`gen_positions_cfg_premarket.ps1`）。rebalance_ens.py 语法坏 = 次日 ENS 静默不换仓，当前无任何防护 | `nightly_check.py:268-288`；项目目录实测 |
| F4 卖出规则一致性 | **FAIL "rebalance_daily.py 无 MATURE 到期制"** | **误报（定性见第 5 节）**：字面量匹配被 09-11 16:02 的 T0_LIQUIDATE 重构打破 | `nightly_check.py:289-306` |

**--fix 修复逻辑总体评估**：修复动作仅三种（重下增量 / 重刷面板 / 熔断 record），全部克制、无越权写 QMT 池、无删源码风险；F2 清理是唯一删除类动作，建议收敛（见上）。**fail-loud 闭环已实证**：main() `return 1 if fails else 0`（`nightly_check.py:338`）→ ps1 `exit $rc`（`nightly_check_task.ps1:41`）→ schtasks Last Result=1（本次实测 09-12 01:00:01 / Result 1）✓。

### G 报告与告警

- **write_report**：落 `data/cache/nightly_check_<date>.md` ✓（09-11、09-12 两份实测在；09-09/09-10 缺失属历史：09-10 17:17 首跑因 PS5.1 ErrorActionPreference Stop 静默崩溃（`nightly_check_task.ps1:8-9` 注释载明根因），09-11 12:05 为手动补跑——夜间任务 09-10 挂载后真正按点跑通是 09-12 01:00 首次）。
- **--push-alert（问题 4 答复）**：**无实现**。`main()` 读取 `push` 标志后从未使用（`nightly_check.py:327`）；全文件 imports 仅 json/os/shutil/subprocess/sys/time（`:13-18`），无任何飞书/notify 代码。`nightly_check_task.ps1:7` 注释如实记载"推送由检修指令按报告执行飞书推送，脚本内当前无实际推送实现"。
- **告警链路现状与缺口**：FAIL → exit 1 → LastTaskResult=1（可观察 ✓）→ **无任何自动消费者** → 依赖次日 agent/LLM 检修指令（本任务即该指令）读报告再人工推送。缺口：**无会话时段（周末/节假日/清晨）FAIL 完全静默**——实证：09-12（周六）01:00 的 F4 FAIL 至本审计（约 11h+）无任何推送发生。若换成真 P0（面板断流/桥宕机），同样静默到下一次会话。
- **现成组件**：`qmt_bridge_client.notify_feishu` 已被 `check_bridge_heartbeat.py:19,73` 和 `reconcile_ens.py:24,159` 使用，nightly_check 接线成本≈10 行，无需引新依赖。
- **附带发现（基础设施）**：`run()` 用 `subprocess.run(..., text=True)` 未指定 encoding（`nightly_check.py:38-39`），中文 Windows GBK locale 下解码 UTF-8 子进程输出（ps1 已设 `PYTHONIOENCODING=utf-8`）→ 09-12 01:00 运行日志实证出现 `UnicodeDecodeError: 'gbk' codec can't decode byte 0x8f`（B2 段 reader 线程崩溃 traceback，见 nightly_check_task.log）。当夜 B2 仍 PASS 但捕获输出丢失（报告 note 为空），更坏情况是 FAIL 详情被吞。建议 `encoding="utf-8", errors="replace"`。

---

## 3. 遗漏清单（对照实盘体系 G2/V13/ENS 每日链路，按 P0/P1/P2）

### P0（漏检会造成次日策略误跑/无告警）

| # | 遗漏项 | 后果 | 证据 |
|---|---|---|---|
| P0-1 | **数据新鲜度无外部锚（A1/B1 恒真）** | xtdata_update/16:30 daily 链断流 N 天，A1/B1 恒 PASS，次日 09:15 三策略候选全部用陈旧数据静默误跑；A3 兜底最坏滞后 5 天 | `nightly_check.py:65-69` vs `:76-81`；修复路径现成：`is_trade_day.py` 日历（refresh_panel_v3.py:125-133 每日重建）+ quant_daily_update Last Result |
| P0-2 | **E2 桥健康三重漏检（单桥/不验内容/阈值过松）** | 09-11 V13 NUL 心跳事故（止损监控离线 3.5h，账户敞口 601058 距止损仅 2%）若发生在夜检间隔，次日开盘前无任何告警；同型事故可复现于 ENS 桥 | `nightly_check.py:210-222`（只查 g2_bridge）；`de_health_check_20260911.md:43`（事故实录）；三桥目录实测存在 |

### P1（会漏报重要异常）

| # | 遗漏项 | 现状证据 | 建议 |
|---|---|---|---|
| P1-1 | **enh 面板新鲜度不查（问题 5）** | `feature_panel_v3_enh.parquet` max trade_date = **2026-08-14**（冻结 29 天，T-20260912-001；mtime 08-23 但数据止于 08-14）；消费方：`build_g2_daily.py:31`（G2/ENS 每日候选的 14 个慢变量 asof）、`train_g2.py:44`（6 个 enh 独有特征）——08-14 后新上市股票无 enh 行、慢变量全体陈旧 | 新增 B5：enh max_date 距增量最新日 >14 自然日 WARN、>30 天 FAIL（慢变量可容忍陈旧但需可见） |
| P1-2 | **ENS 候选产物不查** | B4 只看 `selections/g2`；`selections/g2_ens/20260911_g2_top10.csv`（09-11 16:47）无检查 | B4 扩展双目录 + 日期新鲜度断言（== 最后交易日） |
| P1-3 | **C 任务清单不覆盖新任务（问题 6）** | C1 人工占位；Heartbeat_Alarm（09-12 补注册，267011 未运行）、ENS 相关任务（TW：换仓 09:52/对账 15:45；schtasks：DE_ENS_Compare 10:12）均不在任何自动核对范围；V13_Guard 0x800710E0 无人发现 | 见第 2 节 C1 两层自动化方案（必须做） |
| P1-4 | **资金池滚动不查 + ENS 账本不在 E3（问题 8）** | ENS 恒 100,000（09-10 23:36 后未动，无滚动机制，T-20260912-002；reconcile_ens.py 无 capital 逻辑）；G2/V13 已滚动（97,863.5@09-11 15:49 / 88,001.78@09-11 16:41）但夜检不验新鲜度 | E3 补第三本账 + `updated_at` ≥ 最后交易日 + 数值合理性（0 < capital ≤ 3×START_CAPITAL）；ENS 滚动机制本体另案修（超出夜检范围） |
| P1-5 | **merged_daily_full.parquet 新鲜度不查（问题 9）** | 纸面收益唯一数据源（T-20260910-105）；mtime 09-11 16:35 正常，但由 16:30 链产出，断流即无人知 | 新增 B6：max(trade_date) == 增量最新日（与 A1 外锚联动） |
| P1-6 | **G2/ENS 对账产物不查（E5 只扫 data 根）** | `reconcile_g2_20260911.md`、`reconcile_g2_ens_20260911.md` 实测存在于子目录；15:45 对账断流无告警 | E5 扩三目录 + 日期新鲜度 |
| P1-7 | **F3 冒烟缺 ENS 链** | rebalance_ens.py 等 5 个 ENS 脚本 + check_bridge_heartbeat.py 等均不在冒烟清单（实测存在） | 补齐清单（问题 11） |
| P1-8 | **D2 熔断口径过时 + 不查长期未恢复（问题 10）** | mcp_tdx 假 WARN 连续两晚（opened_at 09-11 09:53:56，300s 早已到期转 PROBE） | 按 remaining 判真熔断；超 24h 无 last_ok 另立"源长期未恢复"WARN |
| P1-9 | **FAIL 无主动推送（--push-alert 虚设）** | 09-12 01:00 FAIL 静默 11h+；无会话时段完全无告警 | main() 接 notify_feishu（现成组件） |
| P1-10 | **run() subprocess GBK 解码崩溃** | 09-12 01:00 日志 UnicodeDecodeError traceback 实证（B2 段） | `encoding="utf-8", errors="replace"` |

### P2（体验/成本优化）

| # | 项 | 证据 |
|---|---|---|
| P2-1 | E1 进程清单重复 XtItClient.exe 两次 | `nightly_check.py:206` |
| P2-2 | B4 `sorted()[-1]` 恒取 `_bak_20260909` 目录名（序数排序 `'_'>'2'`），显示误导 | `nightly_check.py:145`；目录实测 |
| P2-3 | C2 计数混入 de_* 文件 + 硬编码 "2026090" 死代码 | `nightly_check.py:154` |
| P2-4 | F2 清理模式过宽（根目录任意 .json>14d 即删），当前零实害但属隐患 | `nightly_check.py:259-263`；根目录实测 |
| P2-5 | A4 财务 PIT 恒 PASS 无断言 | `nightly_check.py:110` |
| P2-6 | 夜检任务日志 UTF-16/UTF-8 混编（`*>>` 重定向 + Add-Content UTF8 混写），可读性差 | nightly_check_task.log 实测（read 工具判为 binary） |
| P2-7 | hold_dates 陈旧条目不查（g2_hold_dates.json 含 600262/300964/001266 陈旧条目，会导致重新入选误判到期） | `de_report_close_20260911.md:122` |
| P2-8 | heart build_tag 与 build/ 产物一致性不查（现靠 DE 日检人工核对） | `de_recheck_20260912.md:167` |
| P2-9 | 夜检/guard 任务 Logon Mode = Interactive only：无人登录会话则 01:00/10:05 不跑。属已接受口径（QMT 交易本身也需登录会话，repowiki 部署文档注明"风险敞口一致"），但 V13_Guard 12:05 手动重跑被拒（0x800710E0）提示该口径的边界行为值得记录 | schtasks XML 实测；repowiki 环境搭建与部署配置.md:132 |

---

## 4. 优化建议清单（按优先级，标注必须/建议）

**P0（必须做）**
1. A1/B1 外部锚改造：`last_trade_date()` 改用 `is_trade_day` 日历推"期望最近完整交易日"（非交易日自动回退上一交易日），增量/面板/enh/merged 四者统一对锚比较；同时记录 quant_daily_update 的 Last Result 作旁证。【修 `nightly_check.py:65-69`】
2. E2 三桥化 + 内容校验 + 交易日感知阈值：覆盖 g2_bridge / p16_ensemble_bridge / p16_v13_risk；最新 heart 必须 JSON 可解析且含 build_tag；交易日次日 01:00 要求最后交易日 ≥14:50 心跳；非交易日只验内容可解析。【修 `nightly_check.py:210-222`】
3. C1 自动化两层（schtasks 白名单 + TW 产物痕迹），见第 2 节 C 方案。【修 `nightly_check.py:149-157`】

**P1（必须做）**
4. F4 matcher 语义化：`"MATURE" in s and "HOLD_DAYS" in s`（rebalance_daily.py:318/433 的 MATURE 语义仍健在），保留 PK_OUT 防回归排除法，加 T0_LIQUIDATE 兼容注释；理想方案 AST 级检查 reason 取值。
5. G 模块实现 --push-alert：fails 非空 → `notify_feishu`（import qmt_bridge_client），失败不阻断 exit code。【修 `nightly_check.py:325-338`】
6. E3 补 ENS 账本 + 三本账统一验 updated_at/数值合理性；E5 扩 reconcile_g2 / reconcile_g2_ens 两目录 + 日期新鲜度。
7. B 模块补三项：B5 enh 面板新鲜度（>14 天 WARN / >30 天 FAIL）、B6 merged_daily_full 新鲜度、B4 扩 ENS 候选 + 日期断言 + 修 `_bak` 排序（`sorted()[-1]` 改按文件名日期或 mtime 取最新、过滤目录）。
8. F3 冒烟补齐：rebalance_ens.py / reconcile_ens.py / gen_positions_cfg_ens.py / qmt_bridge_client_ens.py / g2_ens_config.py / check_bridge_heartbeat.py / is_trade_day.py / paper_forward.py / strategy_capital.py + 其余 ps1 的 Parser 冒烟（复用 :282-285 现成模式）。
9. D2 按 remaining 时长判真熔断（`opened_at` now 差 > 300s 即非熔断中），超 24h 无 last_ok 另立信息项；顺手让 record ok 清残留 `circuit:true`。
10. `run()` 加 `encoding="utf-8", errors="replace"`。

**P2（建议做）**
11. E1 进程清单去重；F2 清理白名单化；C2 计数改为"上一交易日任务日志存在性"断言；A4 加阈值或降级为信息项；B4 显示修（同 7）；夜检日志统一编码（ps1 侧改用 `| Out-File -Append -Encoding utf8` 或全程 Add-Content）；hold_dates 陈旧条目、build_tag 三方一致两项检查择机纳入。

---

## 5. 特别结论

### 5.1 F4 FAIL 定性：**误报（检查口径过时），非真实回归**

证据链：
1. 检查逻辑要求字面量 `'"reason": "MATURE"'` 出现 + `HOLD_DAYS` 存在（`nightly_check.py:294`）。
2. 现行 `rebalance_daily.py:318` 为条件表达式：`"reason": "T0_LIQUIDATE" if force_liquidate else "MATURE"`——字面量 `"reason": "MATURE"` 因三元表达式写法消失；**MATURE 到期制机制完整健在**：`HOLD_DAYS = 5`（:47，T-20260904-001 到期制）、`_build_last_buy_dates`/`_trading_days_between` 到期判定（:81-118）、到期卖出主路径（:309-318）、兜底 `s.get("reason", "MATURE")`（:433）。
3. 时间线：备份 `rebalance_daily.py.bak_20260908b` 含字面量、无 T0_LIQUIDATE；现行文件 mtime **2026-09-11 16:02:10** → 改动发生在 09-11 12:05 夜检（F4 PASS）之后、09-12 01:00 夜检（F4 FAIL）之前，故 09-11 PASS / 09-12 FAIL 的翻转与该改动精确吻合。
4. pk_ok 分支正常（`"reason": "PK_OUT"` 字面量确已不存在；:434 的 `"planA_pk_out"` 仅为 remark 兼容分支，符合 :295 注释允许范围）；guard_ok 分支正常。

结论：**09-12 F4 FAIL 判定为误报，rebalance_daily.py 卖出规则无回归**。两点附注：① 该误报的根因是脆弱的字面量匹配，若不修 matcher，未来任何含 reason 条件表达式的重构都会再触发（且狼来了效应会钝化真 FAIL 的响应）；② 09-11 16:02 落盘的 T0_LIQUIDATE 改动（T-20260904-003 top=0 清仓信号的 reason 标注）发生在盘后、项目内最新备份停留在 09-08——本次审计受"禁止 git 操作"约束未追溯提交记录，建议人工确认该改动属有意落地（改动内容本身与看板 T-20260904-003 描述一致，未见异常）。

### 5.2 --push-alert 链路现状：**无实现、有缺口、组件现成**

- 现状：`--push-alert` 为无副作用死参数（`nightly_check.py:327` 读取后未用）；脚本无任何飞书代码；ps1 注释（`nightly_check_task.ps1:7`）如实声明推送由检修指令执行。
- fail-loud 闭环：FAIL → exit 1 → LastTaskResult=1，**已实证**（schtasks：Quant_P16_NightlyCheck_0100 Last Run 2026-09-12 01:00:01 / **Last Result 1**；任务日志 `[2026-09-12 01:00:09] nightly_check exit=1`）。
- 缺口：LastTaskResult 无自动消费者；告警实际依赖"次日有 agent 会话读报告"——09-12（周六）的 FAIL 已静默 11h+，周末/节假日/清晨时段的任何真 P0（面板断流、桥宕机、数据陈旧）都会静默到下一次人工/会话介入。心跳告警任务（Quant_P16_Heartbeat_Alarm）只覆盖桥心跳一种信号，且 09-12 才注册成功尚未首跑（267011）。
- 修复成本：项目内 `qmt_bridge_client.notify_feishu` 已被 check_bridge_heartbeat.py 与 reconcile_ens.py 双点验证可用，nightly_check 接入约 10 行，建议与 F4 matcher 同批修复（下一次检修任务改动窗口）。

---

## 附：本次审计的现场快照（2026-09-12 审计时点）

- 三桥心跳：g2 heart_20260912.json 08:55:42 / ENS heart_20260912.json 08:59:04 / V13 heart_v13_20260912.json 08:55:32（周六客户端 08:52 启动后刷新，内容非 NUL）
- 三资金池：G2 97,863.5（09-11 15:49）/ V13 88,001.78（09-11 16:41）/ ENS 100,000.0（09-10 23:36，无滚动）
- 面板：v3 2026-09-11（16:40）/ **enh 2026-08-14（冻结 29 天）** / merged_daily_full 09-11 16:35 / incremental 09-11 16:33
- 熔断记录：mcp_tdx opened_at 09-11 09:53:56（300s 窗口早已过期，属记录残留）；wudao last_ok 09-12 01:00:07（夜检探活回写）
- schtasks：Quant_* 共 22 项全部 Enabled；NightlyCheck Last Result=1（F4 误报所致）；Heartbeat_Alarm / DE_ENS_Compare / DE_G2_Compare 均 267011 待首跑；V13_Rebalance_Guard Last Result=0x800710E0（12:05 被拒重跑尝试覆盖，10:05 正式触发有日志为证）
- 夜检报告存档：仅 09-11（12:05 手动）、09-12（01:00 定时）两份；09-09/09-10 缺失（首跑失败史 + 挂载前空窗），属历史已知，非新问题

*审计声明：全程只读，唯一写入本报告文件；未修改任何源文件/数据/任务，无 git 操作，无下单类脚本执行。所有结论均附文件路径+行号或实测命令输出；无法取证处已标 MISSING（本报告无 MISSING 项）。*
