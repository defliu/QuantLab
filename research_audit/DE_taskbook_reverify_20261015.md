# DE 复核任务书：G2/ENS 丢单修复「二次独立复验」的复核

> 任务类型：**只读复核（Review / Verification）**，禁止修改任何被复核文件。
> 生成时间：2026-10-15
> 前序材料：`DE_taskbook_g2_order_loss_20261015.md`、`DE_report_g2_order_loss_20261015.md`（均已存在，本次是对其结论 + 主控复验结论的**第二次复核**）

---

## 0. 开工前必读（硬要求）

**你（DE）必须先在产出报告的第一节写出「我理解的范围」**，逐条列出：
1. 你将读取的文件清单（应与下方 §2 逐条一致，不许自行增减或替换）
2. 你将要核验的断言清单（对应 §3）
3. 你**不会**修改任何文件（除 §4 指定输出文件）

写完这一节后，再继续执行复核。若你认为范围有偏差，先在该节写明偏差再执行；**严禁替换成你自己选的另一个任务/目录**。

---

## 1. 背景（你不需要重新调查根因，只需复核以下事实是否成立）

2026-10-14，账号 `70180771` 的两个大 QMT 文件桥（G2 = `D:/QMT_POOL/g2_bridge`，ENS = `D:/QMT_POOL/p16_ensemble_bridge`）**各丢失一次换仓指令**（命令文件 `orders_20261014.json` 未被消费、未归档、无 `fills_20261014.json`、账户持仓未变）。

主控（opencode）已完成：
- 根因定位 + Fix A/B/C 修复（4 个源码文件）
- 4 个 QMT 产物重建
- 证据归档
- **一次独立复验**（本次请你复核该复验的结论）

**主控复验中提出的关键修正结论（本次复核重点，请逐条判真伪）：**
- R1. 4 个源码文件确含 Fix A/B/C（G2/ENS 含 A+B+C，V13 仅 A，V2 仅 A）。
- R2. 4 个构建产物合规：GBK 可解码、首行 `# coding=gbk`、BUILD_TAG 为 20261015-0218xx、修复标记齐全。
- R3. stale 证据已归档：两桥 `orders_20261014.json` → `orders_20261014_done_1.json`；两 cmd 目录无未归档命令文件。
- R4. 桥 10-14 全天存活：`state/` 下文件 mtime 15:00:06（盘后），heart 20:5x 仍在写。
- R5. 持仓未变铁证：`positions_20261014.json` 含 `000607.SZ vol=7700`，**无** `000960.SZ`。
- R6. **第一实例**（`D:\国金QMT交易端模拟`，跑 V2/V13）10-14 FormulaOutput 日志有 8 行 `Foribidden FileIO`（含 `_load_state` 三处 `PermissionError`）。
- R7. **第二实例**（`D:\QMT交易端模拟`，跑 G2/ENS）10-14 FormulaOutput 日志**无任何 Foribidden 记录** → 因此「G2/ENS 也命中 Foribidden」属**推断**，非直接证据（因桥 `_read_json` 静默吞异常，异常从未被 print）。
- R8. 两实例 `XtClient_*.log` 中 `setPyFileIOAccess nAuthValue`：10-06~10-13 **早间**均为 0；**10-13 晚间（约 20:53/20:54）同步翻转为 1**；10-14 全天为 1。
- R9. G2/ENS 当前 QMT 内**仍在运行旧产物**（G2 `20261002-193332`、ENS `20261002-200314`），本次修复产物**尚未部署**。
- R10. 10-15（今日）两实例日志停在 08:20，且反复出现 `[LOGIN] Proxy client not logged` → 今日客户端**未登录**，无法判定今日 `nAuthValue` 状态。

---

## 2. 允许读取的文件清单（**只读**，逐条核对，不得替换）

### 2.1 源码（4 个）
1. `d:\QuantLab\projects\Project_16_LightGBM股票大师\strategy\strategy_p16_g2_bridge_src.py`
2. `d:\QuantLab\projects\Project_16_LightGBM股票大师\strategy\strategy_p16_ens_bridge_src.py`
3. `d:\QuantLab\projects\Project_16_LightGBM股票大师\strategy\strategy_p16_v13_risk_src.py`
4. `d:\QuantLab\projects\Project_10_价值小盘V2\strategy_v2.py`

### 2.2 QMT 构建产物（4 个）
5. `d:\QuantLab\projects\Project_16_LightGBM股票大师\build\strategy_p16_g2_bridge.py`
6. `d:\QuantLab\projects\Project_16_LightGBM股票大师\build\strategy_p16_ens_bridge.py`
7. `d:\QuantLab\projects\Project_16_LightGBM股票大师\build\strategy_p16_v13_risk.py`
8. `d:\QuantLab\projects\Project_10_价值小盘V2\build\strategy_v2.py`

### 2.3 桥 state / cmd（目录 + 文件）
9. `D:\QMT_POOL\g2_bridge\state\heart_20261014.json`
10. `D:\QMT_POOL\g2_bridge\state\positions_20261014.json`
11. `D:\QMT_POOL\g2_bridge\state\asset_20261014.json`
12. `D:\QMT_POOL\p16_ensemble_bridge\state\heart_20261014.json`
13. 目录列举 `D:\QMT_POOL\g2_bridge\cmd\`（核对归档文件名与未归档数）
14. 目录列举 `D:\QMT_POOL\p16_ensemble_bridge\cmd\`（同上）

### 2.4 QMT 客户端日志
15. `D:\国金QMT交易端模拟\userdata\log\XtClient_FormulaOutput_20261014.log`
16. `D:\QMT交易端模拟\userdata\log\XtClient_FormulaOutput_20261014.log`
17. `D:\国金QMT交易端模拟\userdata\log\XtClient_20261014.log`
18. `D:\QMT交易端模拟\userdata\log\XtClient_20261014.log`
19. `D:\国金QMT交易端模拟\userdata\log\XtClient_20261015.log`
20. `D:\QMT交易端模拟\userdata\log\XtClient_20261015.log`
21. （对照用）`d:\QuantLab\research_audit\DE_report_g2_order_loss_20261015.md`

> 说明：`XtClient_*.log` 很大（最大 ~16MB），**请勿整文件读入内存后打印**；用 grep/Select-String 抽行即可（只取 `setPyFileIOAccess` / `nAuthValue` / `Foribidden` / `onInitTradeData` / `Proxy client not logged` 等关键行 + 上下文）。

---

## 3. 待核验断言（逐条给出「成立 / 不成立 / 无法判定」+ 原始证据）

| 编号 | 断言 | 判定要点 |
|---|---|---|
| A1 | G2 源码含 Fix A + Fix B + Fix C | 找出 `_read_json` 异常打印、`[P16G2][CMD-READ-FAIL]` + `_write_cmd_alert`、`_scan_stale_orders`/`stale_cmd_alert.json`，给行号 |
| A2 | ENS 源码含 Fix A + Fix B + Fix C | 同上，tag 为 `[P16ENS]` |
| A3 | V13 源码含 Fix A（仅 A） | 找出 `[P16V13][READ-JSON-ERR]`；确认**无** Fix B/C 标记 |
| A4 | V2 源码含 Fix A | 找出 `[V2][READ-JSON-ERR]`（注意文件为混合编码，需按字节/GBK 处理） |
| A5 | 4 产物首行均为 `# coding=gbk` 且 GBK 可解码 | 读 bytes 后 GBK 解码 |
| A6 | 4 产物 BUILD_TAG 为 2026-10-15 02:18:3x~4x | 提取各产物 BUILD_TAG 实际值 |
| A7 | 产物修复标记与源码一致（G2/ENS 全 6 项，V13/V2 仅 READ-JSON-ERR） | 逐产物搜标记 |
| A8 | 两桥 10-14 命令文件均已归档（`*_done_1.json`），cmd 目录无未归档 `orders_<8位>.json` | 目录列举 + 正则 `^(orders|cancel)_\d{8}\.json$` |
| A9 | 桥 10-14 存活：state 文件 mtime 为 15:00 之后 | 读 mtime |
| A10 | `positions_20261014.json` 含 `000607.SZ` 且**不含** `000960.SZ`（持仓未变铁证） | 逐条列持仓 |
| A11 | 第一实例 10-14 FormulaOutput 含 `Foribidden FileIO`（含 `_load_state` / `PermissionError`） | 给命中行数与原文 |
| A12 | 第二实例 10-14 FormulaOutput **无** `Foribidden` 任何记录 | 给命中行数（应为 0） |
| A13 | `setPyFileIOAccess nAuthValue` 时间线：10-13 晚 0→1，10-14 全天为 1 | 逐日逐实例列值 |
| A14 | G2/ENS 10-14 运行的 BUILD_TAG 为旧版 `20261002-193332` / `20261002-200314` | 从第二实例 FormulaOutput 的 `[BUILD]` 行提取 |
| A15 | 10-15 两实例日志停在 08:20 且出现 `Proxy client not logged` | 给最后时间戳 + 命中行 |

**结论要求**：对 R1~R10 逐条给出「主控表述是否准确」的判断；**若发现主控表述过度推断（把推断写成结论）或数字有误，必须明确指出并给出正确表述**。

---

## 4. 输出要求

- **唯一允许写入的文件**：`d:\QuantLab\research_audit\DE_report_reverify_20261015.md`（ASCII 文件名，UTF-8）
- 报告结构：
  1. `## 0. 我理解的范围`（见 §0，先写）
  2. `## 1. 断言核验表`（A1~A15 逐条：判定 + 原始证据 + 行号/文件名）
  3. `## 2. R1~R10 判真伪表`（逐条：准确 / 不准确（含修正表述） / 无法判定）
  4. `## 3. 我认为主控复验中存在的漏洞或反例`（若无，写「无」）
  5. `## 4. 未决问题与建议`（只给建议，不要动手改任何文件）
- 报告内所有数字必须能被 §2 清单中的原始文件复现（写清来源文件 + 行号）。

---

## 5. 禁止事项（硬约束）

1. **禁止修改**：除 §4 输出文件外的任何文件（尤其 §2 清单里的一切文件）。禁止加写、禁止改名、禁止创建目录。**尤其禁止动 `D:\QMT_POOL\` 下任何文件**（涉及实盘）。
2. **禁止 commit / push / 任何 git 写操作**（只读 `git status`/`git log` 可，但本任务无必要）。
3. **禁止执行任何下单、脚本、构建、部署类命令**（禁止运行 `build*.py`、`qmt_*`、`*.ps1`；禁止 QMT 相关操作）。
4. **禁止替换任务范围**：不得改去做其他项目/目录的排查。
5. 若某文件不存在或无权限读取，如实记录为「无法判定」，**不要伪造证据**。

---

## 6. 一句话任务概括

> 只读复核：主控对「2026-10-14 G2/ENS 丢单修复」的**二次复验结论（R1~R10）**是否成立；逐条以原始文件复算验证，把推断与事实严格区分；结论写入 `DE_report_reverify_20261015.md`。
