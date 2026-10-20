# DE 二次复核报告：G2/ENS 丢单修复「独立复验结论」的再核验

- 执行：DE（DevEco Code CLI），依据 `research_audit/DE_taskbook_reverify_20261015.md`
- 日期：2026-10-15（复核执行时段约 08:20~08:30）
- 总判定：**A1~A15 全部成立（A15 含一处快照表述修正）；R1~R10 中 R1~R9 表述准确（R6 有两处需注明的精度问题），R10 结论成立但「日志停在 08:20」为时点快照、非停写。未发现主控把推断写成结论的实质性过度推断。**

---

## 0. 我理解的范围

1. **读取的文件清单**（与任务书 §2 逐条一致，未替换）：
   - 源码 4 个：`strategy_p16_g2_bridge_src.py`、`strategy_p16_ens_bridge_src.py`、`strategy_p16_v13_risk_src.py`（均在 `projects/Project_16_LightGBM股票大师/strategy/`）、`projects/Project_10_价值小盘V2/strategy_v2.py`
   - 构建产物 4 个：上述 4 源码对应的 `build/strategy_p16_g2_bridge.py`、`build/strategy_p16_ens_bridge.py`、`build/strategy_p16_v13_risk.py`、`build/strategy_v2.py`
   - 桥 state/cmd：`D:\QMT_POOL\g2_bridge\{state,cmd}\`、`D:\QMT_POOL\p16_ensemble_bridge\{state,cmd}\`（含 heart/positions/asset_20261014.json 与两 cmd 目录全量列举）
   - QMT 客户端日志：两实例的 `XtClient_FormulaOutput_20261014.log`、`XtClient_20261014.log`、`XtClient_20261015.log`
   - 对照：`research_audit/DE_report_g2_order_loss_20261015.md`（已通读）
2. **核验的断言清单**：§3 的 A1~A15，以及对 R1~R10 逐条判「主控表述是否准确」。
3. **不修改任何文件**：除本报告（§4 指定输出文件）外未写入/改名/创建任何文件；未做 git 写操作；未执行任何下单/脚本/构建/部署命令；未动 `D:\QMT_POOL\` 下任何文件（仅只读列举与读取）。

**范围偏差声明（先写明再执行）**：为核验 A13/R8 的「10-06~10-13 早间均为 0、10-13 晚间翻转为 1」逐日时间线，§2 清单内的 10-14/10-15 两日日志不足以覆盖 10-06~10-13，因此**追加只读**了两实例 `userdata\log\XtClient_20261006/07/08/09/12/13.log` 共 12 个文件（仅 grep `setPyFileIOAccess`，未修改）；另为排除 `Foribdden` 出现在非 Output 日志的可能，追加了只读 grep 两实例 `XtClient_Formula_20261014.log`（均 0 命中）。被占用锁定的日志先复制到系统临时目录再 grep（临时副本，原文件未动）。除此之外范围无增减。

---

## 1. 断言核验表（A1~A15）

> 判定分三档：成立 / 不成立 / 无法判定。所有行号可按来源文件复现。

| # | 断言 | 判定 | 原始证据（来源文件 + 行号/字段） |
|---|---|---|---|
| A1 | G2 源码含 Fix A+B+C | **成立** | `strategy_p16_g2_bridge_src.py`：Fix A `_read_json` 异常打印 L153（`[P16G2][READ-JSON-ERR]`，函数体 L144-154，ENOENT 静默、其余显式打印）；Fix B `_write_cmd_alert` def L126、`[P16G2][CMD-READ-FAIL]` L1583（orders）/L1620（cancel）+ 调用 L1584/L1621；Fix C `_scan_stale_orders` def L303（内写 `stale_cmd_alert.json` L320）、init 调用 L1658 |
| A2 | ENS 源码含 Fix A+B+C | **成立** | `strategy_p16_ens_bridge_src.py`：Fix A `[P16ENS][READ-JSON-ERR]` L151（`_read_json` L142-152）；Fix B `_write_cmd_alert` L124、`[P16ENS][CMD-READ-FAIL]` L1586/L1623；Fix C `_scan_stale_orders` L301（`stale_cmd_alert.json` L318）、init 调用 L1661 |
| A3 | V13 源码含 Fix A（仅 A） | **成立** | `strategy_p16_v13_risk_src.py` L126：`[P16V13][READ-JSON-ERR]`（`_read_json` L117-127）；全文无 CMD-READ-FAIL / `_write_cmd_alert` / `_scan_stale_orders` / `stale_cmd_alert`（grep 0 命中） |
| A4 | V2 源码含 Fix A | **成立** | `strategy_v2.py` 为混合编码，按字节搜索 `[V2][READ-JSON-ERR]` 命中 1 处（字节偏移 44746；按其前 LF 计数折算 **L972**，在 `_lock_read` 内，errno!=2 时打印）；同文件字节搜索 CMD-READ-FAIL / `_write_cmd_alert` / `_scan_stale_orders` / `stale_cmd_alert` 均 0 命中（仅 Fix A，符合范围） |
| A5 | 4 产物首行 `# coding=gbk` 且 GBK 可解码 | **成立** | 4 产物全文件 GBK 解码均成功；首行均为 `# coding=gbk`。文件大小：G2 76,198 B / ENS 75,218 B / V13 59,460 B / V2 66,376 B |
| A6 | 4 产物 BUILD_TAG 为 2026-10-15 02:18:3x~4x | **成立** | G2 `20261015-021837`、ENS `20261015-021839`、V13 `20261015-021840`、V2 `20261015-021841`（各产物内 `BUILD_TAG` 正则提取） |
| A7 | 产物修复标记与源码一致 | **成立** | G2 产物：READ-JSON-ERR×1、CMD-READ-FAIL×2、`_write_cmd_alert`×3（def+2 调用）、`_scan_stale_orders`×2（def+调用）、`stale_cmd_alert`×1 —— 全 6 项齐；ENS 产物同构同计数；V13 产物仅 READ-JSON-ERR×1；V2 产物仅 READ-JSON-ERR×1。与 A1~A4 源码标记分布完全一致 |
| A8 | 两桥 10-14 命令文件已归档，无未归档 `orders_<8位>.json` | **成立** | `g2_bridge\cmd\orders_20261014_done_1.json`（mtime 2026-10-14 09:55:56，重命名保留原写入时间）、`p16_ensemble_bridge\cmd\orders_20261014_done_1.json`（mtime 09:34:55）均存在；正则 `^(orders|cancel)_\d{8}\.json$` 在两 cmd 目录命中 **0** 个文件；且消费侧佐证未消费而非归档：两桥 `state\heart_20261014.json` `last_cmd_seq_processed=0`、`pending=[]`，`state\fills_20261014.json` 不存在（两桥均 False） |
| A9 | 桥 10-14 存活：state 文件 mtime 15:00 之后 | **成立** | 两桥 `positions_20261014.json`、`asset_20261014.json` mtime 均 **2026-10-14 15:00:06**（收盘后）；`heart_20261014.json` G2 20:55:30 / ENS 20:57:15（内容 `last_heartbeat` 同值） |
| A10 | positions_20261014.json 含 000607.SZ(7700) 且无 000960.SZ | **成立** | `g2_bridge\state\positions_20261014.json` 共 5 条：600691.SH 6700 / 605098.SH 500 / 688758.SH 600 / **000607.SZ volume=7700**（L28-29）/ 002841.SZ 400；全文无 `000960.SZ`；`account_id="70180771"`（L2） |
| A11 | 第一实例 10-14 FormulaOutput 含 Foribdden FileIO（含 `_load_state`/`PermissionError`） | **成立**（两处精度问题见 §3-1/§3-2） | `D:\国金QMT交易端模拟\userdata\log\XtClient_FormulaOutput_20261014.log`：含 `Foribdden`（日志实际拼写，**非** "Foribidden"）的行 **8 行**（L131/133/136/138/140/143/145/147，全文出现 11 次）；其中 `_load_state` + `PermissionError: Foribdden FileIO` **3 处**（L131/L138/L145，堆栈 `File "<string>", line 829, in _load_state`）；带时间戳的宿主行：L129 23:46:13、L135 23:46:44、L142 23:59:03 |
| A12 | 第二实例 10-14 FormulaOutput 无任何 Foribdden 记录 | **成立** | `D:\QMT交易端模拟\userdata\log\XtClient_FormulaOutput_20261014.log`（2,218 B，共 20 行）：`Foribdden`/`Foribidden` 均 **0 命中**；追加核对同实例 `XtClient_Formula_20261014.log` 亦 0 命中 |
| A13 | nAuthValue 时间线：10-13 晚 0→1，10-14 全天为 1 | **成立** | 逐日 `onInitTradeData setPyFileIOAccess nAuthValue`（两实例各日晨/晚各一次登录事件）：10-06~10-12 全部为 0（含晚间）；10-13 晨 08:52:08 两实例均 0，**晚 第一实例 20:54:11=1、第二实例 20:53:29=1**；10-14 第一实例 08:52:08=1、20:54:11=1（XtClient_20261014.log L24302/L62092），第二实例 08:52:08=1、20:53:29=1（L23635/L59790）。10-06~10-13 部分来自 §0 声明的追加只读日志（按时间戳可复现） |
| A14 | G2/ENS 10-14 运行 BUILD_TAG 为旧版 20261002-193332 / 20261002-200314 | **成立** | 第二实例 FormulaOutput 10-14：`[P16G2][BUILD] BUILD_TAG=20261002-193332`（L7 晨 08:52:26、L15 晚 20:53:49）、`[P16ENS][BUILD] BUILD_TAG=20261002-200314`（L10、L18）；两桥 heart_20261014.json `build_tag` 字段同值；全部 QMT 日志中无任何 `20261015-0218xx` 新 TAG → 修复产物未部署 |
| A15 | 10-15 两实例日志停在 08:20 且反复出现 `Proxy client not logged` | **成立（附修正）** | 两实例 `XtClient_20261015.log`：`[LOGIN] Proxy client not logged` 第一实例 **132 次**（首 08:15:36，末次见下）、第二实例 **131 次**（首 08:15:39）；本复核取数时最后时间戳：第一实例 08:28:14（仍在写，非停写）、第二实例 08:26:33；两日志 `setPyFileIOAccess`/`onInitTradeData` 均 **0 条** → 今日未登录成立。「停在 08:20」是主控复核时点的快照，见 §3-3 |

---

## 2. R1~R10 判真伪表

| # | 主控表述 | 判定 | 说明 |
|---|---|---|---|
| R1 | 4 源码含 Fix A/B/C（G2/ENS 全，V13 仅 A，V2 仅 A） | **准确** | 与 A1~A4 一致，行号见 §1 |
| R2 | 4 产物合规：GBK 可解码、首行 `# coding=gbk`、BUILD_TAG 20261015-0218xx、修复标记齐全 | **准确** | 与 A5~A7 一致；TAG 实值 021837/021839/021840/021841 |
| R3 | stale 证据已归档为 `orders_20261014_done_1.json`，两 cmd 目录无未归档命令文件 | **准确** | 与 A8 一致；且 heart seq=0、无 fills 佐证是「人工归档未消费文件」而非桥消费后归档 |
| R4 | 桥 10-14 全天存活：state mtime 15:00:06，heart 20:5x 仍在写 | **准确** | 与 A9 一致（heart 20:55:30 / 20:57:15） |
| R5 | 持仓未变铁证：000607.SZ vol=7700 在、000960.SZ 不在 | **准确** | 与 A10 一致 |
| R6 | 第一实例 10-14 FormulaOutput 有 8 行 Foribidden（含 `_load_state` 三处 PermissionError） | **准确（数字全对），但有两处须注明的精度问题** | ①日志实际拼写为 `Foribdden`，任务书/主控写作 `Foribidden`（该日志中 `Foribidden` 0 命中）；②这 8 行时间戳全部为 **23:46:13~23:59:03（盘后）**，且产出者为事故后当晚重建的 V2 测试版（build 20261014-150609 / 20261014-234532），**不是**盘中部署版 20261013-144730 的直接输出——盘中该实例同样无直接 Foribdden 日志（部署版静默吞异常）。结论「第一实例命中过 Foribdden」不受影响，但「盘中间也命中」与第二实例一样属推断（详见 §3-1） |
| R7 | 第二实例 10-14 无任何 Foribdden → 「G2/ENS 也命中 Foribbidden」属推断非直接证据 | **准确** | 与 A12 一致（0 命中）；且第二实例 10-14 两次 INIT 均 `positions=0`（fo2 L8/L11/L16/L19）构成强间接证据，主控把直接证据与推断分开表述，定性正确 |
| R8 | nAuthValue：10-06~10-13 早间均 0；10-13 晚约 20:53/20:54 同步翻转为 1；10-14 全天为 1 | **准确** | 与 A13 一致，逐日逐实例复现无偏差（翻转点：第一实例 20:54:11、第二实例 20:53:29）。补充一致性佐证：10-14 晚 23:46 Foribdden 仍发生，而当时最近一次登录（20:54）nAuthValue=1 —— 与「1=受限」的语义方向自洽 |
| R9 | G2/ENS 当前 QMT 内仍运行旧产物（20261002-193332 / 20261002-200314），修复产物尚未部署 | **准确** | 与 A14 一致（10-14 晨/晚两次 BUILD 行均为旧 TAG，heart 同值，日志无新 TAG）。依据为 10-14 证据 + 今日未登录（R10）无新部署迹象；「当前」严格说是 as-of 10-14 晚 20:53 的运行态外推，但今日客户端未登录，结论稳 |
| R10 | 10-15 两实例日志停在 08:20，反复 `Proxy client not logged` → 今日未登录，无法判定今日 nAuthValue | **基本准确，一处表述需修正** | 未登录结论成立（132/131 次 WARN、0 条 onInitTradeData/setPyFileIOAccess → 今日 nAuthValue 无法判定，正确）。但「日志停在 08:20」是主控复核时点（约 08:2x）的快照：本复核 08:26~08:28 实测第一实例日志仍持续写入（最后行 08:28:14 `Balance timer is running!`），**并非停写**，正确表述应为「日志持续产出但仅存活心跳/未登录告警」。不影响结论 |

---

## 3. 我认为主控复验中存在的漏洞或反例

1. **R6 的证据时点易被误读为「盘中有直接证据」**：8 行 Foribdden 全部产生于 23:46:13~23:59:03（盘后），且来自事故后当晚的 V2 中间测试版（20261014-150609、20261014-234532，均非本次 4 产物）。即：**两个实例在交易时段内其实都没有 Foribdden 的直接日志**，第一实例的「已证实」实质是「当晚同进程故障态直接证实 + 盘中间接证据」。主控对第二实例做了事实/推断区分（R7，正确），但对第一实例未做同等区分（R6 只说「10-14 日志有 8 行」，未注明全为盘后）。建议后续引用时补注时点，避免被下游报告引述成「盘盘中 8 行直接证据」。
2. **拼写以讹传讹的风险**：日志实际字符串为 `Foribdden FileIO`（单 b），任务书与主控表述均为 `Foribidden`。若监控/审计脚本按 `Foribidden` 精确匹配 grep 日志，将 **0 命中、静默漏报**——这恰好重演本次事故「静默失败」的教训。建议任何日志扫描用 `Forib` 前缀或双拼写兼容。
3. **R10「停在 08:20」为快照而非停写**（见 §2-R10 修正）：客户端进程存活、日志仍在写，只是未登录。若有人据「停写」去查进程崩溃/磁盘问题会走错方向。
4. **10-14 晚间第一实例存在多实例竞争测试**：23:46 出现 `[lock] [!] 抢锁竞争落败 -> 本实例转被动`（fo1 L130/L137/L144），说明当晚有 ≥2 个 V2 实例并行抢锁。与主线结论无冲突，但意味着当晚的 Foribdden 复现环境并非单实例纯净环境，未来若做「复现实验」需排除锁竞争变量。
5. **无其他反例**：A8 的归档文件 mtime（09:55:56/09:34:55）早于归档动作属 rename 保留原 mtime 的正常现象，且 heart seq=0 + 无 fills 佐证「未消费即归档」，与 R3 自洽；未发现主控数字错误或伪造迹象。

---

## 4. 未决问题与建议

1. **【今日风险，最优先】修复产物尚未部署 + 今日客户端未登录**：若今日（10-15）外部管道照常向两桥推 `orders_20261015.json`，旧产物（20261002）仍会静默丢单。建议：登录 QMT 客户端 → 人工粘贴部署 4 个新产物 → 核对日志 `BUILD_TAG=20261015-0218xx`；在部署完成前暂停/顺延当日推单。
2. **登录后立即记录当日 `nAuthValue`**：10-15 目前无法判定（未登录）。登录后查 `XtClient_20261015.log` 的 `setPyFileIOAccess` 行——若仍为 1 且新产物的 `[READ-JSON-ERR]` 复现，说明故障态跨日持续，应考虑 QMT 客户端升级/重装（触发条件未确证，前序报告遗留风险 #2）。
3. **监控接线用正确拼写**：`qmt_monitor`/`qmt_card` 对 `Foribdden`（实际拼写）或 `Forib` 前缀做扫描，勿用 `Foribidden`（见 §3-2）。
4. **部署核对清单**沿用前序 DE 报告 §5（STALE-CMD 启动告警预期对 10-14 残留已由 op 归档消除，应不再触发；正常消费路径与 IO 故障路径的预期日志行已列明），另加一条：核对 `[P16G2][BUILD]`/`[P16ENS][BUILD]` 行 TAG = `20261015-021837`/`20261015-021839`。
5. **引用规范**：后续报告引用 R6 时注明「8 行 Foribdden 全部为 10-14 23:46~23:59 盘后、测试版产物」；引用 R8 时可直接使用（本复核已逐日复现）。
6. **本报告边界**：未验证 QMT 客户端内当前实际粘贴的策略内容（客户端加密存储，超出清单范围）；R9「当前仍在运行旧产物」以日志+heart 为据，最终以部署核对 BUILD_TAG 为准。

---

*复核方法说明：被进程占用的日志先复制到系统临时目录再 grep（临时副本，原文件未动）；大日志（~10-16MB）仅做关键词抽取（Foribdden/Foribbidden/setPyFileIOAccess/onInitTradeData/Proxy client not logged/[BUILD]），未整文件读入。V2 源码（混合编码）按字节搜索定位标记后 GBK 解码取证。*
