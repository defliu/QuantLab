# Project_16 夜间检修 FAIL 分析报告（2026-09-17）

- **任务**：夜间检修含 FAIL 的只读根因分析（DE 保障任务，触发文件 `data/schedules/de_nightly_20260917.prompt.txt`）
- **检修报告**：`projects/Project_16_LightGBM股票大师/data/cache/nightly_check_20260917.md`（FAIL 1 / WARN 0 / 总检查 32，任务 exit=1 @ 01:00:16）
- **总结论（先说）**：唯一 FAIL（D1 悟道探活）是**已知缺陷第三晚复发**——09-15、09-16 两晚报告已定性并给出修复建议，`nightly_check.py` 的 D1 探活至今未改。根因：凌晨 01:00 探活不带 date → 服务端按「今日 2026-09-17」请求上游 → 上游只有 09-16 数据 → 返回业务错误 `-32052 MARKET_SUMMARY_NOT_READY` → 判 FAIL。**悟道服务本身存活且 T-1 数据正常（本次已独立复探验证），次日（2026-09-17）开盘前无需任何紧急处理。**

## 1. FAIL 清单

| 模块 | 检查项 | 状态 | 说明 |
|---|---|---|---|
| D | D1 悟道探活 | **FAIL** | market_overview |

关联但未报警的项：D2 熔断状态 PASS（wudao 熔断记录已过期自动恢复）——详见 §3 附带观察。

## 2. 逐项根因与证据

### 2.1 D1 悟道探活 FAIL —— 探活参数缺陷第三晚复发（假 FAIL）

**根因**：`nightly_check.py:299` 探活调用 `wudao_client.py market_overview {}` **不带 date** → 悟道服务端按「今日 2026-09-17」向上游（开盘啦 apphwshhq.kaipanhong.com / apphwhq.longhuvip.com）请求市场概要 → 凌晨上游只有 09-16 数据，两侧均报「市场概要数据日期不匹配: requested=2026-09-17, actual=2026-09-16」 → 服务端返回结构化业务错误 `-32052 MARKET_SUMMARY_NOT_READY`（HTTP 425，retryable=true，附 `suggestedArgs: {"date": "2026-09-16"}`）→ 客户端按约定打印 `[WUDAO-ERR]`（`wudao_client.py:110`）→ 检查判 FAIL。**凌晨探活永远拿不到「今日」概要；服务本身存活、响应正常（结构化业务错误，非超时/断连）。**

**证据（本次独立复探，2026-09-17 凌晨）**：

1. **同参数复现**：`wudao_client.py market_overview "{}"` →
   `[WUDAO-ERR] RuntimeError('悟道 RPC 错误: {"code": -32052, "message": "今日市场概要尚未就绪（requested=2026-09-17）。不会回退为前一交易日数据。", "data": {"error": "MARKET_SUMMARY_NOT_READY", "statusCode": 425, "details": {"requestedDate": "2026-09-17", "availableDate": "2026-09-16", "reason": "开盘啦市场概要数据获取失败...requested=2026-09-17, actual=2026-09-16"}, "suggestedArgs": {"date": "2026-09-16"}, ...}}')`，exit 1。与 09-15/09-16 报告记录的错误完全同型（仅日期推进一天）。
2. **修复参数验证 PASS**：`wudao_client.py market_overview '{"date":"2026-09-16"}'` → 正常返回「上涨 4170 家，下跌 1189 家，平盘 191 家，强度 74，昨涨停今日均涨 +3.65%」，exit 0 → **服务存活、T-1 数据可用**，suggestedArgs 与 T-1 完全一致。
3. **健康文件** `data/cache/datasource_health.json`：wudao `fail_count=4`、`last_fail_ts=2026-09-17 01:00:14`（本晚夜检回写）、`last_ok_ts=2026-09-14 01:15:59`——last_ok 之后 4 次失败全部为凌晨夜检探活场景；同刻 tencent 探活 PASS（213ms）→ 网络出站正常，排除本机网络问题。
4. **修复未落地的直接证据**：`nightly_check.py:299` 仍为 `wudao_client.py, "market_overview", "{}"`，:307 note 仍硬编码 `"market_overview"`；git log 该文件最后一次提交仍是 `1d55965`（T-20260913-001）。**注意**：工作区有一份未提交的本地修改（`git status` 显示 M），但该 diff 只修了 `recent_trade_date()`（A1/B1/B6 盘前假 FAIL 问题，09-15 修的），**未触及 D1 探活参数**——即开发者改了同一文件的其他缺陷却漏掉了 D1，本报告建议 D1 修复随该未提交改动一并落地。
5. **历史同型记录**：`de_nightly_20260915.md` §2.2（首晚定性 + 修复建议）、`de_nightly_20260916.md` §2.2（第二晚复发 + 修复建议）——今晚为第三晚，修复建议两次均未实施。

### 2.2 附带观察：D2 本晚 PASS 的原因与熔断记录残留

本晚 D1 失败后夜检仍执行了 `data_source_router.py record wudao fail`（--fix 模式，fail_count 3→4），但**没有重新触发熔断**：`record()` 只在 `not opened_at` 时才重置 `opened_at`（`data_source_router.py:104`），而健康文件里残留着 09-16 01:00:11 的旧 `opened_at=1789491611`，距本晚检查已 >600s → D2 判「熔断已过期自动恢复」PASS。

即：**D2 本晚安静是旧熔断记录残留的副作用，不是真实状态**。若 `record ok` 复位（清除 opened_at）后再次连续夜检失败 ≥3 次，熔断会重新开启并连带 D2 转 WARN（09-16 晚即如此）。

## 3. 影响评估

**次日（2026-09-17）开盘前无需处理。**

- 悟道**不是 P16 任何开盘链路的依赖**：盘前/盘中数据源走 tencent / ifind / tushare / eastmoney 路由，健康文件显示 tencent（09-16 09:37 ok）、tushare（09-16 09:37 ok）、ifind（09-14 ok）、eastmoney_mx（09-15 ok）均正常。
- 悟道免费版**盘中 09:15-10:30 本就受限跳过**（`wudao_client.py:31-40`），开盘链路从不指望它。
- 悟道服务存活、T-1 数据可用（§2.1 证据 2），午间/收盘报告若调用悟道，成功即 `record ok` 全量复位。
- 熔断记录残留（opened_at/circuit:true）无功能影响，`check()` 只认 opened_at 且已过期。

**但不修复的持续代价**：夜检每天 exit=1 → 每天 FAIL 告警噪音，且「FAIL 1/32」的检修报告会持续掩盖**其他真 FAIL**（狼来了效应，第三晚已现）。

## 4. 修复建议（第三晚重申，按优先级）

1. **根治（推荐，随未提交改动一并落地）**：`nightly_check.py:299` 探活参数从 `"{}"` 改为带最近已收盘交易日：
   ```python
   # 伪代码：date 取 recent_trade_date()（本地未提交修复后，凌晨返回 T-1，语义正好匹配）
   out = subprocess.run([PY, wudao_client, "market_overview",
                         json.dumps({"date": recent_trade_date()}), ...)
   ```
   注意 `recent_trade_date()` 的本地修复（未提交）语义正是「<15:00 返回 T-1」，与探活需求完全吻合，直接复用。
   **备选更稳方案**：D1 语义是「探活」（存活）而非「数据就绪」，可改调 `wudao_client.py tools/list`（不依赖上游数据、无日期语义、凌晨恒可用）；数据就绪度交给业务调用方自行判断。
2. **可观测性（必做，上次报告已提）**：`nightly_check.py:307` note 硬编码 `"market_overview"` → 改为截取 `out.stdout` 尾部约 200 字符（含 `[WUDAO-ERR]` 详情）。三晚报错细节在既有日志零痕迹、全靠复探还原，此缺陷必须一并修。
3. **防污染（建议）**：`wudao_client.py` 识别 `-32052 MARKET_SUMMARY_NOT_READY` 返回特殊退出码（如 2），`nightly_check` 对 2 记 WARN 且不 `record fail`——防健康文件被假 fail 污染进而误触发熔断（09-16 晚 D2 WARN 即此连锁）。
4. **顺手清理（可选）**：健康文件 wudao 残留的 `opened_at`/`circuit:true` 字段（`data_source_router.py:96-97` record ok 清 opened_at 但不清 circuit 标记），无功能影响，下次 `record ok` 自然复位，不必手工干预。
5. **修后验证**：改完手动跑 `python scripts/nightly_check.py`（**不带 --fix**，只读体检）确认 D1 PASS；次日 01:00 `Quant_P16_NightlyCheck_0100` 自然复核。

## 5. 只读合规声明

只读完成：**未下单、未 `--live`、未修改任何文件（除本报告）、未 commit**。两次复探均为只读网络调用（`wudao_client.py main()` 仅 print 不写文件；未执行 `data_source_router.py record/reset`，健康文件数据为夜检任务自身写入，非本任务所改）。`de_nightly_20260917.log` 被当前 DE 会话进程占用无法读取（不影响分析，检修报告与健康文件证据已充分）。
