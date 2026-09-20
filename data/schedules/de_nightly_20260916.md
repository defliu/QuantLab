# Project_16 夜间检修 FAIL 分析（DE 保障，只读）2026-09-16

- **触发**：夜间检修报告 `projects/Project_16_LightGBM股票大师/data/cache/nightly_check_20260916.md` 含 FAIL（FAIL 1 / WARN 1 / 总检查 32）
- **总结论（先说）**：唯一 FAIL（D1 悟道探活）与关联 WARN（D2 wudao 熔断）**同根因：探活参数缺陷复发**——昨晚（09-15）报告 §2.2 已定性并给出修复建议，但 `nightly_check.py` 未改，今晚必然复发。**悟道服务本身存活且数据正常，次日（2026-09-16）开盘前无需任何紧急处理。**
- 与昨晚对比：09-15 的 5 项 FAIL 中 A1/B1/B6 已由 `recent_trade_date()` 修复根治（今晚全 PASS），**只剩 D1 探活参数这一项修复未落地**。

## 一、FAIL 清单

| # | 模块 | 检查项 | 状态 | 说明 |
|---|---|---|---|---|
| 1 | D | D1 悟道探活 | **FAIL** | market_overview |
| 关联 | D | D2 熔断状态 | **WARN** | 真熔断中(≤600s): ['wudao']（D1 假 FAIL 的连带污染，见 §2.2） |

其余 30 项全 PASS（A 数据完整性 / B 面板模型 / C 调度 / E QMT 三桥账本对账 / F 环境均健康）。

## 二、逐项根因与证据

### 2.1 D1 悟道探活 FAIL —— 探活参数缺陷复发（非服务故障）

**根因**：`nightly_check.py:299` 探活调用 `wudao_client.py market_overview {}` **不带 date** → 悟道服务端按「今日 2026-09-16」向上游（开盘啦）请求市场概要 → 凌晨上游只有 09-15 数据 → 返回结构化业务错误 `-32052 MARKET_SUMMARY_NOT_READY`（HTTP 425，retryable=true，附 `suggestedArgs: {"date": "2026-09-15"}`）→ 客户端按约定打印 `[WUDAO-ERR]` → 检查判 FAIL。**凌晨探活永远拿不到「今日」概要，服务本身存活且响应正常。**

**证据（本会话 01:3x 复探还原，非臆测）**：

1. **同参数复现**：`python scripts/wudao_client.py market_overview "{}"` →
   `[WUDAO-ERR] RuntimeError('悟道 RPC 错误: {"code": -32052, "message": "今日市场概要尚未就绪（requested=2026-09-16）。不会回退为前一交易日数据。", "data": {..., "suggestedArgs": {"date": "2026-09-15"}, "hint": "开市后重试今日；若要昨收复盘请显式传 date=2026-09-15..."}}')`，exit 1。与昨晚报告 §2.2 记录的错误完全同型。
2. **修复参数验证 PASS**：`wudao_client.py market_overview '{"date":"2026-09-15"}'` → 正常返回「上涨 1120 家，下跌 4376 家，平盘 52 家，强度 34，昨涨停今日均涨 +0.55%」，exit 0 → **服务存活、T-1 数据可用**，且 suggestedArgs 与 T-1 完全一致。
3. **健康文件** `data/cache/datasource_health.json`：wudao `last_ok_ts=2026-09-14 01:15:59`、`last_fail_ts=2026-09-16 01:00:11`、`fail_count=3`——失败窗口夹在 09-14 夜检成功之后，全部为夜间探活场景。
4. **修复未落地的直接证据**：`nightly_check.py:299` 仍为 `wudao_client.py, "market_overview", "{}"`，:307 note 仍硬编码 `"market_overview"`；git log 显示该文件最后一次提交是 `1d55965`（T-20260913-001，09-13 升级版），**昨晚报告 de_nightly_20260915.md §2.2 的修复建议 a/b 均未实施**。
5. **排除本机网络问题**：同刻腾讯探活 PASS（800ms，`datasource_health.json` tencent `last_ok_ts=2026-09-16 01:00:11`）。

### 2.2 D2 熔断 WARN —— D1 假 FAIL 的连带污染，已自愈，无需动作

**根因**：夜检以 `--fix` 运行，D1 失败后执行 `data_source_router.py record wudao fail`（`nightly_check.py:304-306`）。连续失败累计至阈值 3（`CIRCUIT_FAIL_THRESHOLD=3`，`data_source_router.py:30`）→ 09-16 01:00:11 熔断开启（`opened_at=1789491611`）。D2 检查发生在开启后 600s 窗口内 → 判「真熔断中」WARN。

**证据**：`datasource_health.json` wudao `opened_at=1789491611`（= 2026-09-16 01:00:11，即本晚夜检 D1 失败回写时刻）；熔断窗口 `CIRCUIT_OPEN_SECONDS=300`（`data_source_router.py:31`）→ **01:05:11 已自动过期**，当前时点状态已恢复（下一次 `check wudao` 返回 PROBE 半开探测，成功即彻底复位）。

**附带说明**：`record ok` 只清 `opened_at` 不清残留 `circuit: true` 字段，属已知无害瑕疵（de_nightly_review_20260912.md 已记录，`check()` 只认 `opened_at`）。

## 三、影响评估

**次日开盘前：无需处理。**（与昨晚结论一致，confidence 高）

- 悟道**不是 P16 任何开盘链路的依赖**：盘前/盘中数据走 tencent / ifind / tushare / eastmoney 路由，本晚 tencent 探活 PASS（800ms），健康文件中 ifind / tushare / eastmoney_mx / eastmoney_curl 均 `fail_count=0`。
- 悟道免费版**盘中 09:15-10:30 本就受限跳过**（`wudao_client.py:31-40`、`data_source_router.py:75`），开盘链路从不指望它。
- wudao 熔断 300s 已过期，不影响午间 lunch_report（其调用成功即 `record ok` 全量复位）。

**若不修探活的持续危害**：D1 将**每晚必然 FAIL**（凌晨永远拿不到「今日」概要）→ 夜检报告长期 FAIL 噪音 → 真故障被「狼来了」效应淹没（09-14/09-15 连续两晚 5 项假 FAIL 已实际占用排查注意力），且每晚报一次假 fail 会持续污染健康文件、易触发真熔断误伤午间悟道调用。

## 四、修复建议（按优先级；本任务只读，均未执行）

1. **【P1·根因】D1 探活改带最近已收盘交易日**
   - 位置：`projects/Project_16_LightGBM股票大师/scripts/nightly_check.py:299`
   - 改法：`"{}"` → `'{"date":"%s"}' % recent_trade_date()`
   - `recent_trade_date()` 已在同文件 :130 定义（09-15 修复版语义：仅当日已收盘 ≥15:00 才返回今天，否则回退 T-1），凌晨运行返回 `2026-09-15`，与服务端 `suggestedArgs` 完全一致——**本会话已实测该参数探活 PASS**（证据 §2.1-2）。
   - 备选更稳方案：D1 语义是「探活」（存活）而非「数据就绪」，可改调 `wudao_client.py tools/list`（不依赖上游数据、无日期语义）；数据就绪度交给业务调用方自行判断。
2. **【P1·可观测】note 写入探活输出尾部**
   - 位置：`nightly_check.py:307`，硬编码 `"market_overview"` → 改为截取 `out.stdout` 尾部约 200 字符（含 `[WUDAO-ERR]` 详情）。连续两晚报错细节在既有日志零痕迹、全靠复探还原，此缺陷必须一并修。
3. **【P2】record 语义区分「存活但数据未就绪」**
   - `wudao_client.py` 可识别 `-32052 MARKET_SUMMARY_NOT_READY` 返回特殊退出码（如 2），`nightly_check` 对 2 记 WARN 不 `record fail`——防健康文件被假 fail 污染进而误触发熔断。
4. **【P3】顺手清理**：`data_source_router.record` ok 时清除残留 `circuit: true` 字段（09-12 review 遗留建议）。
5. **修后验证**：改完手动跑 `python scripts/nightly_check.py`（不带 `--fix`，只读体检）确认 D1 PASS；次日 01:00 `Quant_P16_NightlyCheck_0100` 自然复核；不必手工 `reset wudao`（熔断已自愈）。

## 五、数据缺失 / 不可追溯标注（如实）

- `fail_count=3` 中第 1 次 fail 的具体时间与触发场景**不可追溯**：健康文件仅保留最近一次 fail 时间戳（09-16 01:00:11），上一次（09-15 01:26:58）见昨晚报告引用；只能确认全部失败发生在 `last_ok=2026-09-14 01:15:59` 之后，且复现错误与夜检场景同型。
- 本晚 D1 探活的 stdout/stderr 被 `check_d` 丢弃（`nightly_check_task.log` 无错误文本，P1-10 仅统一解码不落盘），报错细节同样只能靠复探还原——即修复建议 2 的动机。

## 六、硬约束遵守声明

只读完成：**未下单、未 `--live`、未修改任何文件（除本报告）、未 commit**。两次复探均为只读网络调用（`wudao_client.py` main() 仅 print 不写文件；未执行 `data_source_router.py record/reset`）。
