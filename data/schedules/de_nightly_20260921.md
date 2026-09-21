# Project_16 夜间检修 FAIL 分析报告（2026-09-21）

- **分析助手**：DE 夜间 FAIL 分析（只读保障任务，未下单 / 未 --live / 未修改任何业务文件 / 未 commit）
- **触发**：`projects/Project_16_LightGBM股票大师/data/cache/nightly_check_20260921.md` 含 FAIL（FAIL 1 / WARN 0 / 总检查 32）
- **分析时间**：2026-09-21 凌晨（01:30 任务窗口）
- **方法**：读检修报告与 09-11~09-21 全部 11 份历史报告、`data/cache/datasource_health.json`、`scripts/nightly_check.py` / `scripts/wudao_client.py` / `scripts/data_source_router.py` 源码、计划任务 `Quant_P16_NightlyCheck_0100` 包装 `nightly_check_task.ps1` 及其日志；另做两次**只读在线探针**（直连悟道 MCP 端点，不写任何状态文件）复现并验证修复方案。
- **先说结论**：**与 09-15/16/17/18 四次夜检 FAIL 完全同根因（第 6 次出现），已知误报，09-18 报告给出的修复建议至今未落地，本次再次确认并催办。**

---

## 一、FAIL 清单

| # | 模块 | 检查项 | 状态 | 说明 |
|---|---|---|---|---|
| 1 | D | D1 悟道探活 | **FAIL** | `market_overview` 调用失败 |

其余 31 项全部 PASS（数据/面板/模型/任务/QMT 三桥/账本/对账/TW 调度/磁盘均正常）。今日（周一 2026-09-21）为 A 股交易日。

## 二、逐项根因分析

### FAIL-1：D1 悟道探活（market_overview）

**现象与模式（确定性，非偶发）**：

11 份历史报告逐日核对，D1 悟道探活结果与「当天是否交易日」完全相关：

| 日期 | 星期 | 交易日 | D1 结果 | 原因 |
|---|---|---|---|---|
| 09-11 | 五 | 是 | PASS | 旧版服务端凌晨回退 T-1 数据 |
| 09-12/13 | 六/日 | 否 | PASS | 非交易日，「今日」解析到周五（已就绪） |
| 09-14~09-18 | 一~五 | 是 | **FAIL ×5** | 服务端行为变更后，凌晨「今日概要未就绪」必报错 |
| 09-19/20 | 六/日 | 否 | PASS | 「今日」解析到 09-18（已就绪） |
| 09-21 | 一 | 是 | **FAIL** | 同 09-14~09-18 根因（本次） |

- `datasource_health.json` wudao 条目：`fail_count=1`（周末 PASS 已清零，本次是新一周的第 1 次失败）、`last_fail=2026-09-21 01:00:12`、`last_ok=2026-09-20 01:00:12`、**无 `opened_at`**（未达 3 次阈值，未熔断；`check("wudao")` 返回 OK）。
- `nightly_check_task.log`：09-21 01:00:06 启动、01:00:14 exit=1；09-20 同窗口 exit=0。

**在线复现（只读探针，本次实测）**：

1. `python scripts/wudao_client.py market_overview "{}"` → **失败**，服务端返回 JSON-RPC 业务错误：
   - `code=-32052`，`MARKET_SUMMARY_NOT_READY`，HTTP 语义 425，`retryable=true`
   - 报文关键句：**"今日市场概要尚未就绪（requested=2026-09-21）。不会回退为前一交易日数据。"**
   - 上游双源（开盘啦 `apphwshhq.kaipanhong.com` / 龙虎VIP `apphwhq.longhuvip.com`）均只有 2026-09-18 的数据：`requested=2026-09-21, actual=2026-09-18` 日期不匹配
   - 服务端建议：`suggestedArgs={"date": "2026-09-18"}`，hint「开市后重试今日；若要昨收复盘请显式传 date=2026-09-18」
2. `python scripts/wudao_client.py market_overview '{"date": "2026-09-18"}'` → **成功**（「上涨 4234 家，下跌 1152 家，平盘 167 家，强度 69，昨涨停今日均涨 +2.50%」，exit 0）。

**根因结论**：

**探针设计与悟道服务端行为变更叠加的确定性误报，非悟道服务故障。与 09-18 报告结论一致。**

- 夜检探活代码 `scripts/nightly_check.py:299` 至今仍以**空参数** `"{}"` 调用 `market_overview` → 服务端默认取「今日」；凌晨 01:00 当日（交易日）概要必然未生成（上游开盘啦数据开市后才有当日值），服务端自 09-13~09-14 之间上线「未就绪即报错、不回退 T-1」的数据质量守卫 → **每个交易日凌晨夜检必 FAIL**；非交易日（周末）「今日」解析到上一交易日已就绪数据 → PASS。这解释了完整的时间模式。
- **09-18 报告修复建议未落地**：`nightly_check.py:299` 探活参数仍为 `"{}"`（本次实读确认），修复锚点 `recent_trade_date()`（line 130）仍在原处可用。

**排除的备择假设（均有证据）**：
- ✗ key/鉴权失效：服务端返回工具级业务错误（425/-32052）而非 401/403，且显式传 date 即成功。
- ✗ 网络不通：响应 1 秒内正常返回完整 JSON-RPC（health 记录 latency_ms=107）。
- ✗ 免费版盘中受限窗口（09:15-10:30）：凌晨 01:00 不在窗口内，且受限时是 `[SKIP]` exit 0 不会 FAIL。
- ✗ 熔断影响生产：fail_count=1 未达阈值 3，无 `opened_at`，路由 `check()` 返回 OK，白盘不会跳过悟道。

## 三、影响评估（次日 2026-09-21 开盘前是否必须处理）

**定性：低影响，不阻塞开盘，非必须盘前处理。但这是同一根因第 6 次触发夜检 FAIL + DE 保障任务，修复欠账应尽快清掉。**

- **策略链路无依赖**：G2/ENS 候选产物（B4/B7 已至 20260921）、三桥心跳（E2）、三账本戳（E3 账号对应正确）、三对账（E5 20260918）全 PASS；悟道 `market_overview` 仅是检修探活与报告类任务的大盘综述辅助源，**不在任何下单/换仓/风控关键路径上**（大盘 T 档门控走腾讯行情 `market_tier.py`，不涉悟道）。
- **白盘使用不受影响**：开市后（09:30+）当日概要就绪，盘中/收盘报告任务（DE_Report_Midday/Close 及午间报告 `lunch_report` 系列的 stock_rank/capital_flow/official_announcements 等工具）不受凌晨 425 影响；且悟道盘中 09:15-10:30 本就按设计跳过（FREE_TIER 受限窗口）。
- **真实风险是「告警疲劳」**：该 FAIL 每个交易日凌晨必现且永不自愈——本任务已是同一根因第 **5 次**触发 DE 夜间保障分析（09-15/16/17/18/21）；若悟道真宕机将淹没在误报里无法区分（狼来了效应）。
- **顺带提醒（本次新发现，非阻塞）**：`datasource_health.json` wudao 条目残留 `"circuit": true` 但无 `opened_at`——`data_source_router.py record()` 的 ok 分支（line 96-99）只 pop `opened_at` 不清 `circuit` 键，周末恢复后该标记永久残留。纯展示层瑕疵（`check()` 只看 `opened_at`，不受影响），可在修 D1 时顺手一行修掉。

## 四、修复建议（精确动作，待有写权限的会话执行；与 09-18 报告一致，再次催办）

**首选修复（根因级，改探活参数锚定 T-1）**：

1. 编辑 `projects/Project_16_LightGBM股票大师/scripts/nightly_check.py` `check_d()` D1 悟道探活块（line 297-307）：
   - 将 `"{}"` 参数改为显式传最近已收盘交易日。仓库内现成锚：同文件 `recent_trade_date()`（line 130，01:00 时正确返回 T-1，本次为 `2026-09-18`）；其可能返回 `None`（日历与增量库均不可用），需保留 `None → "{}"` 降级分支。
   - 参考写法（示意）：
     `d = recent_trade_date(); args = json.dumps({"date": d}) if d else "{}"`
2. **可选加固（双保险）**：`wudao_client` 输出若含 `MARKET_SUMMARY_NOT_READY` / `-32052`，探活判 PASS 并备注「端点可达、当日概要未就绪（预期行为）」——探活目标是连通性而非数据新鲜度。
3. **可选顺手修**：`data_source_router.py record()` ok 分支补 `st.pop("circuit", None)`，消除 `circuit:true` 残留标记。
4. 验证（无需等次日夜检）：修后手动跑
   `python scripts/wudao_client.py market_overview '{"date": "2026-09-18"}'` → 预期 exit 0（本次实测已通过）；再手动跑一次 `scripts/nightly_check.py` 观察 D1 PASS。
5. 修复合入后观察 2026-09-22 01:00 夜检：D1 应恢复 PASS，`datasource_health.json` wudao `fail_count` 停止增长、`last_ok` 刷新。
6. 知识沉淀（AGENTS 硬规则）：该修复属「新机制」收尾项，需同步 Project_16 的 repowiki 知识卡与 `PROJECT_MEMORY.md`——注意 09-18 报告第 5 条同样要求过知识沉淀，若当时也未落地，一并补上。

**无需处理项**：key、端点、网络均无需任何操作；wudao 熔断状态无需手工 reset（未熔断）。

---

## 附：本次分析只读声明

- 未运行任何 `--live` / 下单 / 改仓 / commit；未修改任何业务文件（唯一写入为本报告文件本身，即 prompt 指定输出路径）。
- 仅有的两次外呼为只读探针（`scripts/wudao_client.py market_overview`，该脚本只读 key 配置 + HTTP POST，不落任何状态文件），用于根因复现与修复方案验证。
- 数据缺失标注：无。所有结论均有历史报告、健康状态文件、任务日志、在线复现四重证据支撑；唯一推断成分是悟道服务端行为变更的确切上线时间（依据 09-13 PASS → 09-14 FAIL 边界，误差 ≤ 24h，与 09-18 报告口径一致）。
