# Project_16 夜间检修 FAIL 分析报告（2026-09-18）

- **分析助手**：DE 夜间 FAIL 分析（只读保障任务，未下单 / 未 --live / 未修改任何业务文件 / 未 commit）
- **触发**：`projects/Project_16_LightGBM股票大师/data/cache/nightly_check_20260918.md` 含 FAIL（FAIL 1 / WARN 0 / 总检查 32）
- **分析时间**：2026-09-18 凌晨 ~01:35
- **方法**：读检修报告与 09-11~09-18 历史报告、`data/cache/datasource_health.json`、`scripts/nightly_check.py` / `scripts/wudao_client.py` 源码、任务包装 `nightly_check_task.ps1`；另做两次**只读在线探针**（直连悟道 MCP 端点，不写任何状态文件）复现并定位根因。

---

## 一、FAIL 清单

| # | 模块 | 检查项 | 状态 | 说明 |
|---|---|---|---|---|
| 1 | D | D1 悟道探活 | **FAIL** | `market_overview` 调用失败 |

其余 31 项全部 PASS（数据/面板/模型/任务/QMT 三桥/账本/对账/磁盘均正常）。

## 二、逐项根因分析

### FAIL-1：D1 悟道探活（market_overview）

**现象与持续性（非偶发）**：
- D1 连续 **5 晚 FAIL**：09-14 / 09-15 / 09-16 / 09-17 / 09-18（`nightly_check_2026091[4-8].md` 逐日核对）；09-11 / 09-12 / 09-13 均 PASS。
- `datasource_health.json` wudao 条目：`fail_count=5`、`last_fail=2026-09-18 01:00:12`、`last_ok=2026-09-14 01:15:59`、熔断 `opened_at=2026-09-16 01:00:11`（已过期自动恢复，D2 判 PASS）。

**在线复现（只读探针，2026-09-18 01:35 实测）**：

1. `python scripts/wudao_client.py market_overview "{}"` → **失败**，服务端返回 JSON-RPC 业务错误：
   - `code=-32052`，`MARKET_SUMMARY_NOT_READY`，HTTP 语义 425，`retryable=true`
   - 报文原文（关键句）：**"今日市场概要尚未就绪（requested=2026-09-18）。不会回退为前一交易日数据。"**
   - 上游双源（开盘啦 `apphwshhq.kaipanhong.com` / 龙虎VIP `apphwhq.longhuvip.com`）均只有 2026-09-17 的数据，`requested=2026-09-18, actual=2026-09-17` 日期不匹配
   - 服务端建议：`suggestedArgs={"date": "2026-09-17"}`，hint「开市后重试今日；若要昨收复盘请显式传 date=2026-09-17」
2. `python scripts/wudao_client.py market_overview '{"date": "2026-09-17"}'` → **成功**，正常返回盘面概要（「上涨 2576 家，下跌 2820 家，平盘 157 家，强度 49…」），exit 0。

**根因结论**：

**探针设计与服务端行为变更的叠加误报，非悟道服务故障。**

- 夜检探活代码 `scripts/nightly_check.py:299` 以**空参数**调用 `market_overview` → 服务端默认取「今日」（01:00 时=当日 2026-09-18）。
- 悟道服务端在 **09-13 01:00 ~ 09-14 01:00 之间**上线了「今日概要未就绪即报错、**不再回退前一交易日**」的数据质量守卫（报文明确写「不会回退为前一交易日数据」「勿把昨日数据说成今天」）。凌晨 01:00 当日概要必然未生成（上游开盘啦数据要到开市后才有当日值），故探活**每晚必 FAIL、确定性复现**。
- 09-13 前旧版服务端凌晨会回退返回 T-1 数据 → 探活恒 PASS。这解释了 5 连 FAIL 的时间边界。

**排除的备择假设（均有证据）**：
- ✗ key/鉴权失效：服务端返回的是工具级业务错误（425/-32052），非 401/403，且显式传 date 即成功。
- ✗ 网络不通：响应 1~2 秒内正常返回完整 JSON-RPC。
- ✗ 免费版盘中受限窗口（09:15-10:30，`wudao_client.py:_market_open_restricted`）：凌晨 01:00 不在窗口内，且受限时是 `[SKIP]` exit 0 不会 FAIL。
- ✗ 偶发抖动：连续 5 晚 + 实测确定性复现。

## 三、影响评估（次日 2026-09-18 开盘前是否必须处理）

**定性：低影响，不阻塞开盘，非必须在盘前处理。但会产生持续性误报，应尽快（当日盘中或当晚前）修复。**

- **策略链路无依赖**：G2/ENS/V13 候选产物（B4/B7 20260918）、三桥心跳（E2）、三账本戳（E3）、三对账（E5 20260917）全 PASS；悟道 `market_overview` 仅是检修探活与 DE 报告类任务的大盘综述辅助数据源，**不在任何下单/换仓/风控关键路径上**。
- **白盘使用不受影响**：开市后（09:30+）当日概要就绪，盘中/收盘报告任务（DE_Report_Midday/Close）不带 date 调用也正常；熔断标记 `circuit:true` 已按 600s 窗口过期自动恢复（D2 PASS），路由层白盘仍会正常尝试悟道。
- **真实风险是「告警疲劳」**：该 FAIL 每晚必现且永不自愈 → ①夜检 exit=1 恒触发 DE 保障任务（本报告即由它触发），消耗值守资源；②5 连 FAIL 后若悟道真宕机，将淹没在误报里无法区分（狼来了效应）。
- **顺带提醒**：任何在**开盘前**调用 `market_overview` 且不显式传 date 的任务/脚本，都会撞同一个 425 错误。若盘前任务（如晨报）有此调用，应显式传 `date=<T-1>`。

## 四、修复建议（精确动作，待有写权限的会话执行）

**首选修复（根因级，改探活参数锚定 T-1）**：

1. 编辑 `projects/Project_16_LightGBM股票大师/scripts/nightly_check.py` `check_d()` D1 悟道探活块（约 297-307 行）：
   - 将 `"{}"` 参数改为显式传最近已收盘交易日：探活命令改为
     `wudao_client.py market_overview '{"date": "<recent_trade_date()>"}'`
   - 仓库内已有现成锚：同文件 `recent_trade_date()`（line 130，P0-1 引入，01:00 时正确返回 T-1 如 `2026-09-17`）。注意其可能返回 `None`（日历与增量库均不可用时），需保留 `None → "{}"` 降级分支。
   - 参考写法（示意）：
     `d = recent_trade_date(); args = json.dumps({"date": d}) if d else "{}"`
2. **可选加固**（双保险）：`wudao_client` 输出中若含 `MARKET_SUMMARY_NOT_READY` / `-32052`，探活可判 PASS 并备注「端点可达、当日概要未就绪（预期行为）」——探活目标是连通性而非数据新鲜度，连通性此时已被证明正常。
3. 验证（不依赖等次日夜检）：
   `python scripts/wudao_client.py market_overview '{"date": "2026-09-17"}'` → 预期 exit 0、返回上涨/下跌家数（本次实测已通过）。
4. 修复合入后观察 2026-09-19 01:00 夜检：D1 应恢复 PASS，`datasource_health.json` wudao `fail_count` 停止增长、`last_ok` 刷新。
5. 知识沉淀（AGENTS 硬规则）：悟道服务端「概要未就绪不回退」行为变更 + 夜检探活 T-1 锚定修复，需同步 Project_16 的 repowiki 知识卡与 `PROJECT_MEMORY.md`（属「新机制」类收尾项）。

**无需处理项**：`datasource_health.json` 的 `circuit:true` 残留标记无需手工清理（600s 窗口外按已恢复处理）；key、端点、网络均无需任何操作。

---

## 附：本次分析只读声明

- 未运行任何 `--live` / 下单 / 改仓 / commit；未修改任何业务文件。
- 仅有的两次外呼为只读探针（`scripts/wudao_client.py market_overview`，该脚本只读 key 配置 + HTTP POST，不落任何状态文件），用于根因复现，符合 prompt「读引用日志定位，不臆测」要求。
- 数据缺失标注：无。所有结论均有日志/报告/在线复现三重证据支撑；唯一推断成分是服务端行为变更的确切上线时间（依据 09-13 PASS → 09-14 FAIL 的边界，误差 ≤ 24h）。
