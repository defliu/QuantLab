# DE 任务书 7：审阅 op 的六文件复核报告 **并直接修掉你确认的缺陷**

- 任务编号：T-20260929-OPFIX
- 执行者：Deveco
- 性质：**审阅 + 自修**。op 已独立复核完一轮并落了报告；你先审这份报告对不对、有没有漏，**然后由你动手修**。
- 权威范围：**只有下面「二」列出的 6 个文件可以改。op 不再改代码。**
- 输出文件：`projects/Project_16_LightGBM股票大师/results/DE_REVIEW_op_report_20260929.md`

---

## 零、开工前必须先做的两件事（不做，任务直接作废）

### 0.1 先回报范围确认（硬要求）

**你的第一条回复必须是一行范围确认，形如：**

```
[范围确认] 我理解本次审阅对象 = op 的复核报告 §8.7（specs/候选可审计性修复方案_20260928.md），
涉及 6 个文件：<逐个列出你读到的路径>。只读审阅 + 仅改这 6 个文件。
```

若你读到的文件清单与「二」不一致，**在范围确认里直接指出差异，不要自行改审别的项目**。

⚠️ **特别警告**：本仓库存在**完全无关**的 `Project_21_日内做T` 项目。前一轮委派就是因为范围含糊，审成了 P21 并得出错误结论。**本任务与 P21 毫无关系，禁止审 P21、禁止碰第 7 个文件。**

### 0.2 安全红线（比格式要求更高，违反即任务失败）

这 6 个文件里，`rebalance_daily.py` / `rebalance_g2.py` / `rebalance_ens.py` **连 `--dryrun` 都会连 QMT 账户读真实持仓，具备下单能力**。

- **绝对禁止执行这三个脚本的任何形式**（含 `--dryrun`、含 import 后调 main、含子进程调用）。一个都不许跑。
- **绝对禁止发送任何真实委托**。
- **绝对禁止运行 `data/schedules/compare_*.py` 和 `test_g2_rerun.py`**——它们会重放 `deploy_predict_g2.py` 并覆写生产候选文件。
- 允许的唯一"执行"= `python -m py_compile <你改过的文件>`（纯语法检查，不执行业务逻辑、不碰数据）。
- **禁止 git commit / checkout / stash / reset / clean**（只允许 `git diff` 只读查看）。
- **禁止修改生产数据**：任何 `data/`、`data_live/`、`D:/QMT_POOL/` 下的 csv/json/parquet/log 一律只读。
- **禁止修改**：AGENTS.md、全局控制台.md、.qoder/ 下知识库、config/、任何 build 产物、任何 qmt_config.py。
- 你唯一被允许的写操作：①修改「二」列出的 6 个文件；②新建/覆盖上面那一个 DE 报告 md。

---

## 一、审阅对象

op 的报告：`projects/Project_16_LightGBM股票大师/specs/候选可审计性修复方案_20260928.md` 的 **§8.7 独立复核**（从 `### 8.7` 开始读到文件末尾）。

**用 `git diff -- <下面6个路径>` 看这 6 个文件本次改了什么**（op 的修复 + 更早的 DE 修复都在这个 diff 里）。

**读源码时注意**：行号可能与 op 报告漂移——**以当前源码实际内容为准，不要因为行号对不上就说报告错**。评的是**逻辑对不对**，不是行号准不准。

---

## 二、可改文件清单（6 个，一个不多一个不少）

| # | 路径 | 你在这里要干什么 |
|---|---|---|
| 1 | `projects/Project_16_LightGBM股票大师/rebalance_daily.py` | 审 7 字段口径 + 读盘同源；发现缺陷才改 |
| 2 | `projects/Project_16_LightGBM股票大师/rebalance_g2.py` | 同上 |
| 3 | `projects/Project_16_LightGBM股票大师/rebalance_ens.py` | 同上 |
| 4 | `data/schedules/compare_v13_daily.py` | 审 F7 恢复链；发现缺陷才改 |
| 5 | `data/schedules/compare_g2_daily.py` | 同上（含已知的 P2 静默跳过） |
| 6 | `data/schedules/test_g2_rerun.py` | 同上 |

---

## 三、这次修复到底是干什么的（一句话）

给三个换仓脚本（V1.3 / G2 / ENS）的 plan json 加 7 个候选溯源字段（`candidate_file / candidate_loader / candidate_data_date / candidate_rows / candidate_rows_raw / candidate_sha256 / candidate_asof`），读候选文件时**单次读盘**并把原始字节按 sha 存档到 `candidates_asof/`；同时给三个「复跑类」脚本加**备份 → 复跑 → 恢复原件**（代号 F7），防止复跑把历史候选文件覆盖掉。

---

## 四、op 报告的核心主张（逐条验真伪）

op 的结论是 `PASS_WITH_ISSUES`。**默认它们都可能是错的，你的任务就是证伪或证实。**

### 主张 A：未改动交易行为

op 称唯一逻辑改动是把 `load_selection` 的内联循环抽成 `csv.DictReader(io.StringIO(text))`，语义等价；`REDLINE` / `top_n` / `eff_top` / 卖出计划 / 仓位计算均未动。

**验**：逐个 diff hunk 看有没有改变任何交易语义。特别看新增的 `return` 分支（候选读取失败时从「抛未捕获异常崩溃」变成「print + return」，两条路径都不构造卖出计划）。

### 主张 B：单次读盘，hash / 留档 / 解析三者同源

op 称三个 rebalance 脚本都是「一次读入 → sha256 → 留档 → decode → 计数」。

**验**：有没有哪个脚本仍然读了两遍盘；asof 留档的是不是**被真正解析的那份字节**。注意 `rebalance_daily.py` 里 `load_selection(sel_path, eff_top, text=_sel_text)` 传了 text——确认 `load_selection` 内部**没有再自己 open 一遍**（`rebalance_daily.py` 的 `load_selection` 签名是 `(csv_path, top_n, text=None)`）。

### 主张 C：F7 恢复链已加固（op 自己改的）

op 称补了 3 处：`compare_g2_daily.py` 和 `test_g2_rerun.py` 加 `try/finally`；`compare_v13_daily.py` 的 `_restore_baseline` 加 per-item `try/except`；裸 `print(out[...])` 换 `_safe_print`。

**验（重点）**：
1. `finally` 是不是覆盖了**所有**抛异常路径？有没有 `return` 在 `try` 之前、或某分支整个绕过 `try`？
2. `_restore_baseline` 逐文件恢复时，单个失败会不会中断后续文件？
3. `_safe_print` 是否真兜住 GBK 控制台编码异常？
4. **ENS 分支的重放会写 G2 主目录**（`deploy_predict_g2.py` 无 ensemble 分支时仍写 G2 的 csv+md）——备份/恢复清单里到底有没有把 G2 主目录的文件算进去？这是 op 说的"最严重场景"，请重点核对清单完整性。
5. **多臂备份覆盖**：`compare_g2_daily.py` 的 G2 臂先备份出 `<runDay>_g2_top10.csv.compare_base` 并在 `finally` 恢复；随后 ENS 臂又对**同一路径**再备份一次（`_backup_baseline(g2_csv_baseline, "ENS")` 会 `copy2` 覆盖同一个 `.compare_base`）。判断这会不会造成「G2 臂恢复失败时，ENS 臂的覆盖备份把已污染内容当成原件固化下来」——如果是，评估定级。

### 主张 D：7 字段口径

`candidate_rows` = 过滤后候选数；`candidate_rows_raw` = 过滤前原始行数。

**验**：三个脚本各自算这两个数的地方口径是否真的如描述（V1.3 = `len(target)` / DictReader 计数；G2、ENS = `len(pool)`）。有没有写反或写重。

---

## 五、op 自认没解决的问题（请你独立判断）

1. **零候选日（`rebalance_daily.py` 的 early return）** 没有 plan json 承载 7 字段。op 查 `git grep` 发现盘上 `data/rebalance_20260929.json` 里的 `no_order_reason` / `candidates_over_redline` / `redline_empty_target_early_return` 等键名**在任何 `.py` 里都不存在**，判定那是 TRAE 会话 inline 补写的留档、不是代码路径输出，据此判断"不修"。**请验证这个判断是否站得住。**

2. **`compare_g2_daily.py` 遗留 P2**：当 `existed=True` 但 `.compare_base` 缺失时，`_restore_baseline` 静默跳过恢复且不告警。op 认为实际难触发。**请判断这个定级对不对**——有没有更容易触发的路径（备份本身失败、人工删过备份、上一轮崩溃残留）。

3. op 特别提醒：`test_g2_rerun.py` **不是 pytest**，没有 `def test_*`、没有断言，靠退出码表达成败；「合成测试 12/12」只覆盖 `compare_v13_daily.py`，**不能外推**为整个 F7 已验证。**请核对该说法与源码是否一致。**

---

## 六、你要交付的东西（分两阶段，都要写进同一个报告）

### 阶段 1：审阅结论（先写这段，再动手改）

1. **范围确认**（照 §0.1 写，作为报告第一节）
2. **对主张 A/B/C/D 的逐条裁定**，每条给 `证实 / 证伪 / 部分成立` + **源码行号证据**（必须带具体文件和行，不许只写结论）
3. **op 漏掉的问题**：你独立看出来、op 报告里没有的缺陷。每条给：文件、行号、为什么是问题、危害、建议怎么改、P0/P1/P2
4. **明确列出你不同意 op 的地方**——不要为了礼貌附和。全对也要明说「逐条验完，未发现反例」

### 阶段 2：动手修（改完再写这段）

**修改纪律（硬要求）：**

- **最小改动**：只修你已论证的缺陷，不做重构、不做格式化、不动无关行、不"顺手优化"。
- **不得改变任何交易语义**：`REDLINE` / `top_n` / `eff_top` / 卖出计划 / 仓位计算 / 下单逻辑，一行都不许动。你只允许动「溯源字段」和「备份-恢复链」这两类代码。
- **不得新增依赖**（不许 import 新第三方包）。
- 每改一处，必须在报告里写：**文件:行号 → 改前是什么 → 改后是什么 → 为什么必须改（对应哪条缺陷）**。说不清"为什么必须改"的一律不许改。
- 改完对每个动过的文件跑 `python -m py_compile <文件>`，把结果贴进报告。**只许 py_compile，不许运行脚本。**
- 跑完 `git diff -- <6个路径>` 把最终 diff 贴进报告末尾（**只贴这 6 个文件**）。

**拿不准的、可能影响交易语义的改动 —— 不要改，写进报告「建议但我没改」一节，附理由和风险，交 op 拍板。** 宁可少改，不可乱改。

---

## 七、再次提醒

- 你是第三轮复核，前两轮 DE 委派都失败了（第一次范围跑偏到 P21、第二次任务书被命令行截断）。这次任务书写死了文件清单就是为了避免重蹈覆辙。**发现清单与实际不符，请在报告里立刻指出，不要自行去审你以为相关的项目。**
- op 的报告是自己写的，**默认它有错**。本项目历史上出现过 DE 报告引用过时文档当结论的情况——所有引用以**当前源码**为准。
- **审阅和修复都要有行号证据。** 拒绝空泛结论。
