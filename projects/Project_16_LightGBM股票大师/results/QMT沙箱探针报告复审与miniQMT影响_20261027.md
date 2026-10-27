# QMT 沙箱探针报告 · 复审与 miniQMT 影响评估

- **日期**：2026-10-27
- **复审对象**：`projects/Project_16_LightGBM股票大师/specs/QMT沙箱能力全量探针研究报告_20261026.md`（266 行，2026-10-27 11:17 更新）
- **复审人**：Qoder（只读复核 + 运行态实测）
- **方法**：通读报告全文；核对今日 11:07 探针日志；检查两台 QMT 客户端二进制与 miniQMT 日志；核对生产内置策略今日日志；抽查 V13/G2 构建产物的 remark 常量
- **边界**：未修改任何业务代码、未执行交易脚本、未重跑探针、未提交 git

---

## 一、一句话结论

**报告是可信的「通道能力清单」，但不是「miniQMT 死亡后的迁移可行性验收」。**

- 它证明了：文件桥出局、内置行情 API 可用、sqlite 可跨重启、KV 仅小数据、网络 GET/子进程基础可用。
- 它没有证明：P16 能否在客户端内**自给自足地产出信号**（数据面未测）、能否在**实盘上下文**完成交易（交易 API 未测）、以及现有 V13/G2 的跨进程托管链路在 miniQMT 消失后如何续接。
- **真正决定 P16 生死的是数据面探针，不是 HTTP POST。**

---

## 二、本次实测补充的关键事实（报告未覆盖）

| # | 事实 | 证据 | 影响 |
|---|---|---|---|
| 1 | **67014907 客户端已无 `XtMiniQmt.exe`** | `D:\国金QMT交易端模拟\bin.x64\` 下不存在；仅 `D:\QMT交易端模拟\bin.x64\XtMiniQmt.exe`（2.1.19.200，2026-08-19）存在 | xtquant 不是"未启动"，是**客户端层面被移除**；67014907 的外部通道不可恢复 |
| 2 | **miniQMT 日志终止于 10-26 09:55** | `userdata_mini\log\XtMiniQmt_20261026.log` 末段：`login dialog button 3 clicked → daemon stop miniQuote → daemon kill miniQuote → CMiniBrokerDaemon stop minibroker` | 与事实 1 一致，确认外部通道已收口 |
| 3 | **今日 10:05 / 10:07 两次 live 换仓均连接失败** | `data/schedules/rebalance_daily_guard_20261027_100502_172.log`、`rebalance_g2_guard_20261027_100701_898.log`：`连接 miniQMT 失败 rc=-1` | V1.3 / G2 今日零成交；ENS 虽 exit=0 但为 `[NOOP]`，guard 打印「已真实下单」属误导文案 |
| 4 | **G2 内置版在 67014907 客户端因账号戳错位转被动** | `XtClient_FormulaOutput_20261027.log`：`[P16G2I][INIT-SNAP-ERR] 快照账号戳 70180771 != 策略 67014907` | 不是沙箱问题；迁移前必须重做 L3 快照或显式空仓起步 |
| 5 | **V13 的托管链路依赖外部买单 remark** | `build/strategy_p16_v13_risk.py:62`：`EXTERNAL_REMARK_BUY = "planA_new_top"`；`build/strategy_p16_g2_selfcontained.py:54`：`EXTERNAL_REMARK_BUY = "p16_g2_new"` | miniQMT 消失后无「外部买单」，V13 的 `_late_bind_whitelist` 将永远绑不到票 → 永远被动；这是报告完全未触及的架构耦合点 |
| 6 | **今日探针仍在生产客户端运行** | 11:07 `SUMMARY seg=AUTO: 55 OK / 25 FAIL`；全天 `file I/O check ... deny all`；10-26 Formula 日志 230 MB、10-27 至 11:24 已 18.7 MB | 结题后应撤掉常驻探针，否则日志膨胀拖慢客户端 |
| 7 | **报告/源码/AGENTS 均未提交** | `git status`：报告 untracked；`AGENTS.md`、`broker/qmt_sandbox_probe.py` modified | 按本项目交付纪律，审计结论目前只活在工作区 |

---

## 三、报告站得住的部分（可直接采信）

| 结论 | 证据强度 | 使用建议 |
|---|---|---|
| 文件桥彻底出局（B 端 open/os.open/pathlib/makedirs 全拒、tempfile 挂死；A 端读也拒） | 强 | 可据此废弃 `_read_json`/`_atomic_write_json` |
| 内置行情 API 可用（`get_market_data_ex(stock_code=)` + `get_full_tick`） | 强（11:07 复测 rows=5，000001.SZ 11.49 / 600000.SH 9.78，K 线与 tick 交叉一致） | 「内置自算信号」必要条件成立 |
| sqlite 跨重启持久 | 中强（PID 23504→12596，rows 2→4，跨两天四轮） | 状态层首选，优于 KV |
| KV 仅小数据 | 强（1MB 写入 rc=0 但读回空串） | 仅存水位/摘要 |
| 网络 GET/HTTPS/raw socket 可用 | 中（只测 GET） | 仅作降级/过渡，不作主链路 |
| subprocess 能启进程拿 stdout | 弱（只测 echo/system/popen） | 仅作应急，不得假设可写文件 |
| 「权限位=1 不等于文件可写」「拦截在本地 C 层」 | 强（日志 + 实测交叉） | 别再等服务端权限位恢复 |

报告最值得肯定：主动收录独立审计意见（§九 9 条勘误），并推翻了 10-18 轮的旧归因。这一点应保留。

---

## 四、不能当结论用的部分（按对 P16 的杀伤力排序）

1. **数据面完全没测（最关键）**
   报告只验证 OHLCV。P16 的 21 特征需要 PE/PB/流通市值/财务/板块/资金流，而 AGENTS 明确写「`C.get_market_data_ex` 缺少财务字段，PE/PB/circ_mv 必须来自预生成 CSV」。报告只在 §3.7 提到 `C.dir()` 里有 `get_finance`/`get_financial_data`/`get_turnover_rate`/`get_float_caps`，**一次实调都没有，也没有与 D:/astock 对齐**。这是「P16 能否在客户端内自给自足」的生死项。

2. **交易 API 未在实盘上下文验证**
   `passorder`/`get_trade_detail_data`/`get_current_time` 在探针上下文不存在（报告 §九-1 自认）。生产 V13/G2 的历史日志可作旁证，但没有「实盘模式 + 新 build + 探针」的直接证据。

3. **参考架构与自身结论矛盾**
   §五 参考架构写「告警推送 → HTTP POST 飞书 webhook」，但 §九 明确「HTTP 只测了 GET，未测 POST」。属于把能力当已验证。

4. **sqlite 主线程/并发/崩溃恢复未测；subprocess 写文件未测；客户端 A 未跑全量 9 段**
   报告都标注了，但 §五 的参考架构没有为这些不确定性留降级分支。

5. **上下文差异**
   探针在「模型交易/回测」模式跑，实盘模式的沙箱强度是否一致未知。这一条决定所有结论能否直接搬到生产。

---

## 五、治理问题

1. **与 AGENTS 红线冲突**：AGENTS 第 127 行仍写「策略进程零外部交互」，而报告 §五 把 HTTP 拉数据、HTTP POST 告警、subprocess 调外部脚本都写成落地方向。10-27 拍板「先不降级」只解决「要不要停」，没解决「红线要不要改」。下一个 agent 读到旧红线，会把 HTTP 方案直接判违规。**必须在 AGENTS 补正式修订（或明确过渡期例外）。**
2. **交付未收尾**：报告 untracked、AGENTS/探针源码 modified、4 个残留 `_probe4_*.db` 未清理、生产客户端探针未撤。按本项目硬规则，这四项不补齐等于没交付。

---

## 六、建议下一步（v4.7 定向探针，别再全量跑）

### P0（决定可行性）
1. **数据面**：客户端内实调 `get_finance` / `get_financial_data` / `get_turnover_rate` / `get_float_caps` / `get_stock_list_in_sector`，抽 20 只 × 3 天与 D:/astock 对齐；同时验证 `get_market_data_ex(front_ratio)` 与 D:/astock 复权口径一致。
2. **交易面**：在实盘模式（非模型/回测）跑最小探针：`get_current_time` → `passorder` 1 手 → `get_trade_detail_data` 反查 → `cancel`。非交易时段只测 API 存在性，交易时段测最小单。

### P1
3. HTTP POST + 飞书 webhook + 断网降级（一次跑完）。
4. sqlite 主线程直调 + 两进程并发写；subprocess 外部写文件。
5. 客户端 A 全 9 段（若 70180771 仍承担生产）。

### P2 收尾
6. 撤掉生产客户端常驻探针；删除 4 个残留 `_probe4_*.db`；提交报告 + AGENTS 修订；看板登记。
7. G2 迁移到 67014907 前，重做 L3 快照账号戳；V1.3 若迁入客户端，需把 V13 的「外部买单延迟绑定」改为同进程直接登记，或把风控并入 V1.3 自闭环。

---

## 七、复审结论分级

| 分级 | 内容 |
|---|---|
| **已实证（可采信）** | 文件桥出局；内置行情 API 可用；sqlite 跨重启；KV 仅小数据；网络 GET/HTTPS 可用；subprocess 能启进程拿 stdout |
| **未实证（不得当结论）** | 实盘上下文交易 API；财务/估值/板块数据 API；HTTP POST/webhook；sqlite 主线程/并发/崩溃恢复；subprocess 写文件；客户端 A 全量；实盘模式沙箱一致性 |
| **报告未覆盖的架构缺口** | G2 L3 快照账号戳迁移；V13 外部买单托管链路在 miniQMT 消失后的替代方案；P16 21 特征的内置数据来源 |

---

*复审完成：2026-10-27。本文只读复核，未改代码、未下单、未提交。*
