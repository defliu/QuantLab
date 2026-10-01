# DE 审阅报告 7：op 六文件复核报告的第三轮复核 + 自修（T-20260929-OPFIX）

- 审阅人：Deveco（DE 任务书 7）
- 日期：2026-09-29
- 审阅对象：`specs/候选可审计性修复方案_20260928.md` §8.7 独立复核（op 亲审报告）
- 结论总览：op 的 `PASS_WITH_ISSUES` **主体成立**；主张 A/B 证实，主张 C/D **部分成立**——各挖出 1 个 op 未闭环的真缺陷（C.5 多臂备份覆盖可固化污染、D 的 V1.3 candidate_rows 口径偏差），均已由本次 DE 修复。另发现 1 个 op 未察觉的 P2（复跑对 paper_forward 追加日志的污染）与 2 个 P3（未用 import、死变量），P2 建议未改待 op 拍板，P3 已顺手清掉。
- 安全红线遵守声明：未执行任何 rebalance/compare/rerun 脚本（含 dryrun/import/子进程）；唯一执行 = `python -m py_compile`（5 个改动文件 + 1 个未改对照，全部退出码 0）；未 commit/checkout/stash/reset；未动 data/、data_live/、D:/QMT_POOL/、AGENTS.md、config/、build 产物、qmt_config.py。

---

## 1. 范围确认

[范围确认] 本次审阅对象 = op 的复核报告 §8.7（specs/候选可审计性修复方案_20260928.md），涉及 6 个文件：

1. `projects/Project_16_LightGBM股票大师/rebalance_daily.py`
2. `projects/Project_16_LightGBM股票大师/rebalance_g2.py`
3. `projects/Project_16_LightGBM股票大师/rebalance_ens.py`
4. `data/schedules/compare_v13_daily.py`
5. `data/schedules/compare_g2_daily.py`
6. `data/schedules/test_g2_rerun.py`

只读审阅 + 仅改这 6 个文件。与 `Project_21_日内做T` 无关，未读未碰。清单与任务书「二」完全一致，无差异。

---

## 2. 阶段 1：对主张 A/B/C/D 的逐条裁定

### 主张 A（未改动交易行为）：**证实**

- `load_selection` 重构：改前 `csv.DictReader(f)`（文件对象，`encoding="utf-8-sig"`）vs 改后 `csv.DictReader(io.StringIO(text))`（text 由同一批字节 `decode("utf-8-sig")` 而来）——解码器、迭代语义、过滤/排序/截断逐行一致（`rebalance_daily.py:168-185`，截断行 `return stocks[:top_n]` 原样保留）。
- 新增 `return` 分支（`rebalance_daily.py:330-338`）：改前候选读取失败 = `load_selection` 内 `open/read` 抛未捕获异常 → 崩溃；改后 = `print + return`。两条路径都在 `sell_plan` 构造之前（卖出计划自 `:352` 起才构建），均不产生卖出/买入指令。op 对「失败可见性从非零退出降为退出码 0」的 P2 定级我认同（见 §4-3）。
- `REDLINE`（`:176` 判断原样）、`eff_top` 归一（`:318`）、卖出/仓位计算 diff 中均无改动。
- **注意**：本次 DE 修复 4 给 `load_selection` 增加了可选 `meta=None` 参数（见 §3 修复 4）。默认值 None、返回值不变、截断/排序/过滤行逐字未动——交易语义仍零改动。

### 主张 B（单次读盘，hash/留档/解析同源）：**证实**

- V1.3：`rebalance_daily.py:327-336` 单次 `open(sel_path,"rb").read()` → sha256（`:330`）→ asof 留档（`:331`，留档字节即 `_raw_sel`）→ decode（`:332`）→ raw 计数（`:333`）；`load_selection(sel_path, eff_top, text=_sel_text, ...)`（`:339`）传入 text，函数内 `if text is None` 才自行读盘（`:170-172`）——**确认没有二次 open**。全文件 `load_selection` 仅 `:339` 一个调用点。
- G2/ENS：`rebalance_g2.py` / `rebalance_ens.py` 的 `load_g2_selection` / `load_ens_selection` 内一次 `open(csv_path,"rb").read()` → sha → 留档 → decode → 循环解析（单函数内闭环）；两文件中 `_g2_top` 相关路径仅出现在 `_latest_avail_candidate`（只 listdir 不读内容）与该 load 函数，无第二读盘点。
- op 的 sha8 运行时实证（24383fb1）与 asof 留档文件在盘事实我独立复核过：`data/candidates_asof/` 下 `20260929_24383fb1_20260929_selection_full.csv`（6792 字节）存在，`data/rebalance_g2/candidates_asof/` 下有 20260928/20260929 的 g2_top10 留档（且 09-28/09-29 各有两个不同 sha8 共存，证明「内容不同则共存、绝不覆盖」机制实际生效）。

### 主张 C（F7 恢复链已加固）：**部分成立**——C.1~C.4 证实，**C.5 属实且已修**

1. **finally 覆盖性**：逐路径核对三个 compare/rerun 脚本——所有 `return` 早退（`rc != 0` 分支）都在 `try` 内部（`compare_g2_daily.py:224/:331/:359`、`compare_v13_daily.py:227/:254`、`test_g2_rerun.py:149`），finally 必然执行；所有「备份之后、try 之前」的代码只有 `_log`（若 `_log` 抛错，此时复跑未发生、原件未被改写，无需恢复）；SKIP/降级前置 return 都在备份之前。**证实，无绕过路径**。
2. **per-item 恢复隔离**：`compare_v13_daily.py:89-99` 逐条 `try/except` 已补齐，单个失败 `_log` 告警不中断。**证实**。
3. **`_safe_print` 兜底**：GBK 控制台下 `print` 含 `\ufffd` 文本抛 `UnicodeEncodeError` → except 分支 `text.encode(enc,"replace").decode(enc)` 降级重打（`compare_g2_daily.py:122-130`、`compare_v13_daily.py:102-110`、`test_g2_rerun.py:71-79`，三处同款）。encode 产物必为该编码合法字节，decode 回打不会再抛。**证实**。
4. **ENS 分支写 G2 主目录**：`deploy_predict_g2.py:308-322` 无 ensemble 分支、无条件写 G2 主目录 csv+md，与 op 判断一致；ENS 臂备份清单（`compare_g2_daily.py:220-223`）已含 `g2_csv_baseline` + `g2_md_baseline`。**证实，清单对候选文件而言完整**。但见下条与 §4-1——「完整」只对 csv/md 成立，追加型 live 日志不在清单内。
5. **多臂备份覆盖（op 提请重点核对项）**：**缺陷属实，定级 P2（低概率 × 高危害），已修**。
   - 场景推演：`main()` G2 臂在 `finally` 恢复 `baseline`+`md_baseline`（`compare_g2_daily.py:364-365`）→ 若恢复失败（文件锁/权限，per-item except 只告警不中断），盘上留复跑产物 → 随后 `_ens_compare` 对**同一路径**再 `_backup_baseline`，`copy2` 把**污染内容**覆盖进 `.compare_base`（`compare_g2_daily.py:222-223`，ENS 臂）→ ENS 臂 finally 从被污染的备份"恢复"→ **污染被固化为"原件"，最后一份好副本永久丢失**（无 G2 臂污染时该文件本可手动从 `.compare_base` 抢救）。
   - 触发概率低（需 G2 臂恢复先失败），但一旦发生不可逆且次日实盘可能吃到污染数据——这正是 F7 要消灭的灾难。op 只提问未定级未修，本 DE 定级 P2 并修复（§3 修复 2）。
   - 附带核对：除该场景外，同进程内 G2 臂恢复成功后 `.compare_base` 内容 == 当前盘上内容，ENS 臂二次备份是纯冗余；first-wins 去重不影响任何正常路径语义（跨进程仍每次新鲜备份，夜间正品被合法重写后再次运行仍能正确备份新原件）。

### 主张 D（7 字段口径）：**部分成立**——G2/ENS 正确，**V1.3 的 candidate_rows 口径偏差，已修**

- 规格口径（任务书 §四-D）：`candidate_rows` = **过滤后**候选数。
- G2/ENS：`"candidate_rows": len(pool)`（`rebalance_g2.py:423`、`rebalance_ens.py:386`），pool 未截断（"不截断，买入侧自行排除持仓取最高"）→ len(pool) 即过滤后候选数。**正确**。
- V1.3：`"candidate_rows": len(target)`（修复前 `:497`/`:638`）。`target = load_selection(...)` 返回 `stocks[:top_n]`（`rebalance_daily.py:186`）——**len(target) 是「过滤 + top_n 截断后」的数**。当过红线票数 > eff_top 时（selection_full 是 review_full 全量清单，常态可达 5+ 只过线），candidate_rows ≠ 过滤后候选数，与规格、与 G2/ENS 双重不一致；且 `candidate_rows_raw`（过滤前）与 `candidate_rows`（截断后）之间**过滤后计数信息整体丢失**。op 报告 Q3 写"candidate_rows=过滤后（取 len(target)）"是自相矛盾的表述——「取 len(target)」为真，「=过滤后」为假。
- 全仓 grep 确认 `candidate_rows` 无任何下游代码消费者（仅 3 个 rebalance 脚本自身写入；`research_external/TSP_repo` 命中为无关外部仓库），修正字段值零风险。已修（§3 修复 4）。

---

## 3. 阶段 2：本次 DE 修复清单（每处：位置 → 改前 → 改后 → 为什么必须改）

### 修复 1（rebalance_daily.py:160-186）——主张 D 口径缺陷

- **改前**：`def load_selection(csv_path, top_n, text=None)`，`return stocks[:top_n]`，过滤后计数未传出。
- **改后**：签名增可选 `meta=None`；排序后、截断前新增 `if meta is not None: meta["candidate_rows_filtered"] = len(stocks)`（`:184`），截断行逐字未动。
- **为什么必须改**：candidate_rows 与规格「过滤后候选数」及 G2/ENS 口径不符（主张 D 裁定），属任务书授权的「溯源字段」类改动；过滤后计数在改动前无任何字段承载，审计信息缺失。

### 修复 2（rebalance_daily.py:339 / :502 / :643）——同上缺陷的调用点与写点

- **改前**：`target = load_selection(sel_path, eff_top, text=_sel_text)`；两处 plan json `"candidate_rows": len(target)`。
- **改后**：调用点传 `meta=cand_meta`；两处写点改 `"candidate_rows": cand_meta["candidate_rows_filtered"]`（dryrun `:502`、live `:643`）。
- **为什么必须改**：承接修复 1 的回填值。直接下标访问（非 `.get`）保证未来若有绕过 meta 回填的路径会响亮 KeyError 而非静默写错值，符合本仓「宁响勿静」纪律。plan json 中的 `"target"` 列表仍为截断后实际消费集，消费口径不受影响。

### 修复 3（compare_g2_daily.py:78-95）——主张 C.5 多臂备份覆盖缺陷

- **改前**：`_backup_baseline` 每次调用无条件 `copy2` 覆盖 `.compare_base`；G2 臂恢复失败时 ENS 臂会把复跑产物固化为"原件"。
- **改后**：新增模块级 `_BACKUP_SEEN = {}`（`:78`），函数入口 `if path in _BACKUP_SEEN: return _BACKUP_SEEN[path]`（`:88-89`），首备后记录（`:94`）——**同进程同路径首次备份优先（first-wins）**，ENS 臂复用 G2 臂的好备份。
- **为什么必须改**：消除「G2 臂恢复失败 → ENS 臂二次备份污染固化 → 好原件永久丢失」链路（§2-C.5 推演）。语义核对：G2 臂恢复成功时 `.compare_base` == 盘上内容，跳过重备纯冗余消除；恢复失败时保留好备份使 ENS 臂恢复反而能**修复**污染；红线空仓日（existed=False）缓存值与重查值一致；跨进程不受影响（每次运行 `_BACKUP_SEEN` 重新为空，夜间正品合法重写后仍能备份新原件）。属任务书授权的「备份-恢复链」类改动。

### 修复 4（compare_g2_daily.py:113-116）——恢复静默跳过告警（op 遗留 P2 收紧）

- **改前**：`_restore_baseline` 中 `existed=True` 但 `.compare_base` 缺失时静默跳过（op 报告遗留 P2："如需收紧可加 _log 告警"）。
- **改后**：else 分支新增 `!! [%s] %s 原件存在过但备份 %s 缺失，无法恢复——盘上是复跑产物，审计时注意` 告警日志。
- **为什么必须改**：该分支一旦触发即「盘上留复跑产物且无任何日志痕迹」，与 `compare_v13_daily.py` 的行为（copy2 抛 FileNotFoundError 被 per-item except 接住并告警）不一致；一行成本把静默数据污染变成响亮告警。

### 修复 5（test_g2_rerun.py:59-61）——同款静默跳过告警

- **改前**：`_restore_baseline` 同 compare_g2_daily 旧版，`existed=True` 但 `.rerun_base` 缺失时静默跳过。
- **改后**：同款 `!! [F7] ... 原件存在过但备份 ... 缺失，无法恢复` print 告警。
- **为什么必须改**：与修复 4 同根因；test_g2_rerun 是周六回归，静默污染历史候选后连日志都没有，排障无从下手。

### 修复 6（rebalance_g2.py / rebalance_ens.py）——删除未使用的 `import shutil`

- **改前**：两文件 `import shutil`（本次修复轮引入，全文件无任何 `shutil.` 引用——grep 确认仅 import 行一条命中）。
- **改后**：删除。
- **为什么必须改**：本轮修复引入的死代码，会误导后续读者以为文件里存在备份/拷贝逻辑；stdlib import 无副作用，删除零风险。（compare 三个脚本里 `shutil` 有真实使用，保留。）

### 修复 7（compare_g2_daily.py:224）——删除死变量

- **改前**：`ens_existed = items[0][1]`，赋值后全函数无引用（旧代码遗留，重构后失去消费者）。
- **改后**：删除该行。
- **为什么必须改**：死赋值是本轮 diff 引入的噪音，且变量名暗示"有消费者"，误导审阅。零行为影响。

**未改动**：`compare_v13_daily.py` 本次零改动（其 `_restore_baseline` 已通过异常路径天然告警，其余核验全部通过）；`rebalance_g2.py` / `rebalance_ens.py` 除删 import 外零改动。

### py_compile 结果（唯一允许的执行）

解释器：`C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe`（PATH 上的 `python` 是 WindowsApps 存根，exit 9009 不可用）：

```
daily=0  g2=0  ens=0  cmpg2=0  rerun=0  cmpv13(未改对照)=0
```

5 个改动文件 + 1 个未改对照全部语法通过。

---

## 4. op 漏掉的问题（DE 独立发现）

### 4-1（P2，建议但我没改）：复跑对追加型 live 日志的污染不在 F7 覆盖内

- **位置/证据**：`deploy_predict_g2.py:330`（`_append_live_dedup`，G2 主臂与 ENS 臂复跑都会执行）追加 `data/real/paper_forward_live.csv`；`:426-432` ENS 臂追加 `data/real/paper_forward_ens.csv`。`_append_live_dedup` 去重用 `keep="last"`（`:55`）。三个复跑脚本的备份清单只含候选 csv+md，**这两个追加型日志完全不在 F7 备份-恢复链内**。
- **为什么是问题**：PASS 日复跑与正品逐位一致 → (date, code) 去重后幂等无污染；但**差异日**（如 F2 新浪兜底）复跑行会被追加/覆盖：同 (date, code) 不同值时 `keep="last"` 直接**覆写夜间正品的 total_new/prob**，不同 code 则留下幻影行——前向统计（paper_forward_downgrade 按 ARMS TOP_N 过滤）会被污染。test_g2_rerun 周六重放最新交易日同理（且周六重放更易因 F2 当日值变化产生差异行）。
- **危害**：纸面前向收益统计失真，且无告警无留痕。
- **为什么没改**：①该文件是**追加型生产日志**而非「历史候选文件」，F7 目标语句（任务书 §三）未覆盖它；②纳入备份-恢复意味着恢复窗口内任何并发合法追加会被回滚丢弃，需要 op 对「复跑追加算不算合法记录」做语义拍板；③正常 PASS 日无污染，触发面=差异日。**交 op 裁决**：若判污染，最小修法是把两个日志文件加入三个复跑脚本的 items 备份清单（`_backup_baseline`/`_restore_baseline` 语义对 append 文件同样成立）。

### 4-2（P3，记录不改）：`.compare_base` / `.rerun_base` 备份文件永不清理

- 三个脚本恢复后均不删除备份文件，每个 runDay 累积 2-3 个小文件。可接受（等价于额外审计副本，也是 C.5 场景下的人工抢救来源），仅记录。

### 4-3（P2 观察项，与 op 一致）：候选读取失败静默 return、退出码 0

- op 报告 Q11 的 P2 定级我复核后**同意**：`rebalance_daily.py` read_error 分支 print + return，`main()` 无 `sys.exit`，定时任务层面视为成功。改成非零退出会影响上游任务链判定（是否重试、是否告警），超出本次授权范围，不改，留 op 拍板。

---

## 5. §五三项的独立判断

### 5-1 零候选日无 plan json：**op 判断站得住**

- 早退点 `rebalance_daily.py:340-344`（`当日清单过红线的票为 0` 且非 T0 → return）确实先于两个 plan 写点（dryrun `:511-513`、live `:653-655`，修复后行号）。
- `no_order_reason` / `candidates_over_redline` / `redline_empty_target_early_return` 全仓 grep：**仅**命中 `data/rebalance_20260929.json`（数据文件）与文档，任何 `.py`（含 versions/ 归档外的全部脚本）都不含这些键名 → 该 json 不可能由当前代码路径产出，TRAE 会话 inline 补写的定性成立。
- 最强证据链完好：asof 留档 `data/candidates_asof/20260929_24383fb1_20260929_selection_full.csv` 在盘（我独立确认存在，6792 字节）。「零候选日本就无计划可写，强塞空摘要制造噪音」的不修理由我同意。

### 5-2 compare_g2_daily 静默跳过 P2 定级：**定级对，但「实际难触发」的表述只对了一半——已顺手修掉**

- op 说"备份与原件同目录同生命周期，实际难以触发"。单次运行内确实近乎不可触发（copy2 成功 ⇒ 备份必在，恢复必能找到）。但 op 提示的三条路径里「上一轮崩溃残留 + 人工清理备份」是现实可碰的：上一轮复跑崩溃留下污染的候选文件，人手工清 `.compare_base`（看着像垃圾文件），下一轮 `existed=True` 但备份缺失 → 静默跳过 → 污染继续且无痕。
- 定级维持 P2 正确；但既然修复成本=一行告警（§3 修复 4/5），就没有理由留一个静默数据污染分支。

### 5-3 test_g2_rerun 非 pytest：**与源码一致，证实**

- `test_g2_rerun.py` 全文无 `def test_*`、无 `assert`；`_check_csv`（`:80-108`）靠 print + `_push` + 布尔返回表达成败，`main` 靠 `sys.exit(main())` 的 0/1 表达（`:155-156`）。§8.6 的 12/12 合成测试只覆盖 `compare_v13_daily.py`，「不得外推为 F7 整体验证」的纪律表述与事实相符。

---

## 6. 与 op 的分歧汇总（不含附和）

1. **Q3（主张 D）**：op 标 PASS 是**错的**——V1.3 `candidate_rows=len(target)` 是截断后口径，与规格「过滤后」不符（§2-D）。已修。
2. **C.5 多臂备份覆盖**：op 只把它列为"请重点核对"未下结论——**缺陷属实**，P2，已修（§2-C.5 / §3 修复 3）。
3. **遗留 P2 静默跳过**：op 说"实际难触发"且"如需收紧可加告警"——我认为该分支一旦触发即无痕污染，一行告警成本下没有理由不收紧。已修（§5-2）。
4. **op 未察觉**：paper_forward 追加日志污染（§4-1，P2 建议未改）；未用 `import shutil` ×2、死变量 `ens_existed`（P3，已清）。
5. 其余（主张 A/B、C.1-C.4、§五全部三项、问题 #1/#3/#4/#5 的定级与处置）：**逐条验完，未发现反例**。

---

## 7. 建议但我没改（汇总，待 op 拍板）

| # | 事项 | 定级 | 不改理由 |
|---|---|---|---|
| 1 | paper_forward_live.csv / paper_forward_ens.csv 追加污染（§4-1） | P2 | 追加型生产日志超出 F7「历史候选文件」目标语句；纳入恢复链的并发回滚风险与「复跑追加是否合法记录」需 op 语义拍板 |
| 2 | read_error 静默 return 退出码 0（§4-3） | P2 | 改退出码牵动上游定时任务链的重试/告警判定，超授权范围 |
| 3 | 零候选日不写最小摘要 json（§5-1） | — | 同意 op：无计划可写，asof 字节证据已兜底；强塞空摘要制造噪音 |
| 4 | `.compare_base`/`.rerun_base` 永不清理（§4-2） | P3 | 充当额外审计副本 + 人工抢救来源，累积量小 |

---

## 8. 最终 diff（仅 6 个授权文件，git diff 实录）

统计：`6 files changed, 522 insertions(+), 123 deletions(-)`（含 op 与更早 DE 的全部未提交改动 + 本轮 DE 增量）。本轮 DE 净增量 = 修复 1-7，合计 +21/−4 行左右，全部落在溯源字段与备份-恢复链两类。

完整 diff 见下（与 `git diff -- <6路径>` 逐字节一致）：

```diff
diff --git a/data/schedules/compare_g2_daily.py b/data/schedules/compare_g2_daily.py
index 3c9fe1e..4784343 100644
--- a/data/schedules/compare_g2_daily.py
+++ b/data/schedules/compare_g2_daily.py
@@ -75,6 +75,64 @@ def _load_csv(path):
     return pd.read_csv(path, encoding="utf-8-sig")
 
 
+_BACKUP_SEEN = {}   # 同进程内已备份路径 → 当时原件是否存在（F7 复核补丁 2026-09-29）
+
+
+def _backup_baseline(path, tag):
+    """复跑前备份原件 → <path>.compare_base；返回备份前原件是否存在。
+    （F7 修复配套，2026-09-28）
+    同进程内同路径只备份一次（2026-09-29 DE 复核补丁）：main 的 G2 臂与 _ens_compare 的
+    ENS 臂会对 G2 主目录同一 csv/md 各备份一次；若 G2 臂恢复失败（文件锁等，per-item
+    try/except 只告警不中断），盘上留的是复跑产物，ENS 臂二次 copy2 会把它当原件固化进
+    .compare_base —— 最后一份好原件被覆盖，污染永久化。首次备份优先（first-wins）杜绝该场景。"""
+    if path in _BACKUP_SEEN:
+        return _BACKUP_SEEN[path]
+    existed = os.path.exists(path)
+    if existed:
+        shutil.copy2(path, path + ".compare_base")
+        _log("[%s] 备份基准 %s -> %s" % (tag, os.path.basename(path), os.path.basename(path) + ".compare_base"))
+    _BACKUP_SEEN[path] = existed
+    return existed
+
+
+def _restore_baseline(items, tag):
+    """F7 修复（2026-09-28，候选可审计性）：复跑会用同名路径重写历史候选，
+    对比完成后必须恢复原件，否则盘上留下的是复跑产物而非夜间正品 → 历史候选
+    不可审计（实证：20260924_g2_top10.csv 于 2026-09-28 16:48 被 --date 补跑覆写，
+    内容由 002617/002569 变为 603272/003029）。
+    items: [(path, 复跑前原件是否存在), ...]
+    - 原件存在过 → 从 .compare_base 恢复
+    - 原件本不存在（红线空仓日）而复跑新建 → 删除复跑产物，保持无候选状态
+    恢复失败只告警不阻断（对比结论已得出），日志标注审计注意。"""
+    for path, existed in items:
+        try:
+            backup = path + ".compare_base"
+            if existed:
+                if os.path.exists(backup):
+                    shutil.copy2(backup, path)
+                    _log("[%s] 恢复原件 %s（复跑产物不落盘）" % (tag, os.path.basename(path)))
+                else:
+                    _log("!! [%s] %s 原件存在过但备份 %s 缺失，无法恢复——盘上是复跑产物，审计时注意"
+                         % (tag, os.path.basename(path), os.path.basename(backup)))
+            else:
+                if os.path.exists(path):
+                    os.remove(path)
+                    _log("[%s] 移除复跑产物 %s（当日原为红线空仓，保持无候选状态）" % (tag, os.path.basename(path)))
+        except Exception as e:
+            _log("!! [%s] 恢复 %s 失败（盘上可能留有复跑产物，审计时注意）: %s" % (tag, os.path.basename(path), e))
+
+
+def _safe_print(text):
+    """重放 stdout 按 utf-8/replace 解码后可能含 \\ufffd，在 GBK 控制台 print 会
+    UnicodeEncodeError（2026-09-29 E2E 实测）——降级为可编码字符再打印，绝不让
+    打印崩溃中断「重放→恢复」链路。"""
+    try:
+        print(text)
+    except UnicodeEncodeError:
+        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
+        print(text.encode(enc, "replace").decode(enc))
+
+
 def _replay(date_str):
     cmd = [PY, DEPLOY, "--date", date_str, "--top", "10"]
     r = subprocess.run(cmd, cwd=P16, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
@@ -152,25 +210,35 @@ def _ens_compare(run_day, no_send):
     """融合(ENS)复跑对比（T-20260920-001 扩展）：与 G2 同留档快照，独立重放并分级。
     返回退出码（0=PASS 含新浪兜底一致/记录差异 1=error 2=warn）。"""
     ens_baseline = os.path.join(ENS_SELECT_DIR, "%s_g2_top10.csv" % run_day)
-    ens_backup = ens_baseline + ".compare_base"
-
-    if not os.path.exists(ens_baseline):
-        _log("[ENS] 当日无融合基准候选（红线空仓路径），重放确认")
-    else:
-        shutil.copy2(ens_baseline, ens_backup)
-        _log("[ENS] 备份融合基准 %s -> %s" % (os.path.basename(ens_baseline), os.path.basename(ens_backup)))
+    # F7 补修（2026-09-28 首跑实测发现）：ENS 重放（--ensemble --ens-live）除 g2_ens/ 外
+    # 还会重写 G2 主目录的同名 csv+md（deploy L309/L320 无 ensemble 分支），
+    # 必须一并备份/恢复，否则 ENS 复跑漂移会污染 G2 正品且无恢复。
+    g2_csv_baseline = os.path.join(SELECT_DIR, "%s_g2_top10.csv" % run_day)
+    g2_md_baseline = os.path.join(SELECT_DIR, "%s_g2_selection.md" % run_day)
+
+    _log("[ENS] 当日无融合基准候选（红线空仓路径），重放确认" if not os.path.exists(ens_baseline)
+         else "[ENS] 备份融合基准")
+    items = [(ens_baseline, _backup_baseline(ens_baseline, "ENS")),
+             (g2_csv_baseline, _backup_baseline(g2_csv_baseline, "ENS")),
+             (g2_md_baseline, _backup_baseline(g2_md_baseline, "ENS"))]
 
     _log("[ENS] 重放 deploy_predict_g2.py --date %s --top 10 --ensemble --ens-live" % run_day)
-    rc, out = _replay_ens(run_day)
-    print(out[-2500:])
-    if rc != 0:
-        _push("G2/ENS 每日复跑 FAIL", "融合复跑退出码 %d（运行日 %s）—— FAIL-LOUD 或运行时异常" % (rc, run_day),
-              "error", no_send)
-        _log("!! 融合复跑退出码 %d（exit=1）" % rc)
-        return 1
+    # F7 修复加固（2026-09-29 复核）：原实现逐 return 内联 _restore_baseline，
+    # 一旦 _replay_ens/_load_csv/print 抛异常（GBK \ufffd 打印崩溃最典型），
+    # 恢复永不执行 → ENS 复跑产物永久留在 G2 主目录（正品被污染）。
+    try:
+        rc, out = _replay_ens(run_day)
+        _safe_print(out[-2500:])
+        if rc != 0:
+            _push("G2/ENS 每日复跑 FAIL", "融合复跑退出码 %d（运行日 %s）—— FAIL-LOUD 或运行时异常" % (rc, run_day),
+                  "error", no_send)
+            _log("!! 融合复跑退出码 %d（exit=1）" % rc)
+            return 1
 
-    base = _load_csv(ens_backup)   # 原始融合基准（已备份）
-    rep = _load_csv(ens_baseline)  # 复跑融合产物（已重写同名路径）
+        base = _load_csv(ens_baseline + ".compare_base")   # 原始融合基准（已备份）
+        rep = _load_csv(ens_baseline)                      # 复跑融合产物（已重写同名路径）
+    finally:
+        _restore_baseline(items, "ENS")   # F7 修复：对比后恢复原件（含 G2 主目录 csv+md）
     sev, msg = _cmp_ens(base, rep)
     if sev == "pass":
         _log("[ENS] PASS：%s（exit=0）" % msg)
@@ -239,7 +307,7 @@ def main():
     run_day = args.date or time.strftime("%Y%m%d")
     snap = os.path.join(LIVE_DIR, "snapshot_g2_%s.parquet" % run_day)
     baseline = os.path.join(SELECT_DIR, "%s_g2_top10.csv" % run_day)
-    base_backup = baseline + ".compare_base"
+    md_baseline = os.path.join(SELECT_DIR, "%s_g2_selection.md" % run_day)   # 复跑同样会重写 md（F7）
     ens_baseline = os.path.join(ENS_SELECT_DIR, "%s_g2_top10.csv" % run_day)
 
     _log("==== G2+ENS 每日选股复跑对比 运行日=%s 开始 ====" % run_day)
@@ -253,13 +321,21 @@ def main():
     if not os.path.exists(snap):
         _log("[DEGRADE] 留档快照 %s 不存在 → 用当前快照重放，仅健康校验（单源结论，未经留档对比）"
              % os.path.basename(snap))
-        rc, out = _replay(run_day)
-        print(out[-2000:])
-        if rc != 0:
-            _push("G2 每日复跑 FAIL", "复跑退出码 %d（降级路径，运行日 %s）" % (rc, run_day), "error", args.no_send)
-            _log("!! 降级复跑退出码 %d" % rc)
-            return 1
-        ok = _health_check(run_day, args.no_send)
+        # F7 修复：降级路径的重放同样会覆写当日候选，先备份、校验后恢复原件
+        deg_items = [(baseline, _backup_baseline(baseline, "DEGRADE")),
+                     (md_baseline, _backup_baseline(md_baseline, "DEGRADE"))]
+        # F7 修复加固（2026-09-29 复核）：原实现逐 return 内联 _restore_baseline，
+        # 异常出口（GBK 打印崩溃/健康校验抛错）会跳过恢复 → 复跑产物留在盘上。
+        try:
+            rc, out = _replay(run_day)
+            _safe_print(out[-2000:])
+            if rc != 0:
+                _push("G2 每日复跑 FAIL", "复跑退出码 %d（降级路径，运行日 %s）" % (rc, run_day), "error", args.no_send)
+                _log("!! 降级复跑退出码 %d" % rc)
+                return 1
+            ok = _health_check(run_day, args.no_send)
+        finally:
+            _restore_baseline(deg_items, "DEGRADE")
         if not ok:
             _log("!! 降级健康校验不通过（exit=1）")
             return 1
@@ -271,25 +347,27 @@ def main():
         return 2
 
     # ---- 1) 备份基准（复跑会重写同名路径） ----
-    if os.path.exists(baseline):
-        shutil.copy2(baseline, base_backup)
-        _log("[1/4] 备份基准 %s -> %s" % (os.path.basename(baseline), os.path.basename(base_backup)))
-    else:
+    base_items = [(baseline, _backup_baseline(baseline, "1/4")),
+                  (md_baseline, _backup_baseline(md_baseline, "1/4"))]
+    if not base_items[0][1]:
         _log("[1/4] 当日无基准候选文件（红线空仓路径），跳过备份")
 
-    # ---- 2) 重放 ----
+    # ---- 2) 重放（F7 修复加固：try/finally 兜底恢复，任何异常/早退都不留复跑产物） ----
     _log("[2/4] 重放 deploy_predict_g2.py --date %s --top 10" % run_day)
-    rc, out = _replay(run_day)
-    print(out[-3000:])
-    if rc != 0:
-        _push("G2 每日复跑 FAIL", "复跑退出码 %d（运行日 %s）—— FAIL-LOUD 或运行时异常" % (rc, run_day),
-              "error", args.no_send)
-        _log("!! 复跑退出码 %d（exit=1）" % rc)
-        return 1
+    try:
+        rc, out = _replay(run_day)
+        _safe_print(out[-3000:])
+        if rc != 0:
+            _push("G2 每日复跑 FAIL", "复跑退出码 %d（运行日 %s）—— FAIL-LOUD 或运行时异常" % (rc, run_day),
+                  "error", args.no_send)
+            _log("!! 复跑退出码 %d（exit=1）" % rc)
+            return 1
 
-    # ---- 3) 读取双方产物对比 ----
-    base = _load_csv(base_backup)   # 原始基准（已备份）
-    rep = _load_csv(baseline)       # 复跑产物（已重写同名路径）
+        # ---- 3) 读取双方产物对比（原件由 finally 恢复，复跑产物不留在盘上） ----
+        base = _load_csv(baseline + ".compare_base")   # 原始基准（已备份）
+        rep = _load_csv(baseline)                      # 复跑产物（已重写同名路径）
+    finally:
+        _restore_baseline(base_items, "F7")
     src = _f2_source(out)
     _log("[3/4] 复跑 F2 主源=%s" % src)
 
diff --git a/data/schedules/compare_v13_daily.py b/data/schedules/compare_v13_daily.py
index e8319f6..1ae04d0 100644
--- a/data/schedules/compare_v13_daily.py
+++ b/data/schedules/compare_v13_daily.py
@@ -69,6 +69,47 @@ def _load_csv(path):
     return pd.read_csv(path, encoding="utf-8-sig")
 
 
+def _backup_baseline(path, tag):
+    """F7 修复（T-20260928-002，与 compare_g2_daily 同款）：重放会重写同名路径，
+    先备份原件；返回原件是否存在的布尔值。"""
+    if os.path.exists(path):
+        shutil.copy2(path, path + ".compare_base")
+        _log("[%s] 备份基准 %s -> %s" % (tag, os.path.basename(path),
+                                         os.path.basename(path) + ".compare_base"))
+        return True
+    _log("[%s] 原件不存在（红线空仓等）：%s" % (tag, os.path.basename(path)))
+    return False
+
+
+def _restore_baseline(items, tag):
+    """F7 修复：复跑对比后恢复原件（复跑产物不落盘）；原件本不存在而复跑新建则删除
+    （恢复红线空仓的「无候选」状态）。幂等：重复调用安全。
+    逐条 try/except（2026-09-29 复核补齐，与 compare_g2_daily 同款）：单个文件恢复
+    失败不得中断循环，否则后续文件永久留复跑产物；也不得掩盖 finally 外的业务异常。"""
+    for path, existed in items:
+        try:
+            if existed:
+                shutil.copy2(path + ".compare_base", path)
+                _log("[%s] 恢复原件 %s（复跑产物不落盘）" % (tag, os.path.basename(path)))
+            elif os.path.exists(path):
+                os.remove(path)
+                _log("[%s] 移除复跑产物 %s（原件为红线空仓，恢复无候选状态）" % (tag, os.path.basename(path)))
+        except Exception as e:
+            _log("!! [%s] 恢复 %s 失败（盘上可能留有复跑产物，审计时注意）: %s"
+                 % (tag, os.path.basename(path), e))
+
+
+def _safe_print(text):
+    """重放 stdout 按 utf-8/replace 解码后可能含 \ufffd，在 GBK 控制台 print 会
+    UnicodeEncodeError（2026-09-29 E2E 实测）——降级为可编码字符再打印，绝不让
+    打印崩溃中断「重放→恢复」链路。"""
+    try:
+        print(text)
+    except UnicodeEncodeError:
+        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
+        print(text.encode(enc, "replace").decode(enc))
+
+
 def _snap_latest_day():
     """当前 latest_features.parquet 最新交易日（YYYYMMDD），读失败返回 None。"""
     try:
@@ -152,6 +193,8 @@ def main():
     snap = os.path.join(LIVE_DIR, "snapshot_v13_%s.parquet" % run_day)
     baseline = os.path.join(SELECT_DIR, "%s_model_top10.csv" % run_day)
     base_backup = baseline + ".compare_base"
+    # 复跑（deploy_predict）除 csv 外还会重写同名 md（F7，与 G2 的 selection.md 同款）
+    md_baseline = os.path.join(SELECT_DIR, "%s_selection_dual.md" % run_day)
 
     _log("==== V1.3 每日选股复跑对比 运行日=%s 开始 ====" % run_day)
 
@@ -171,13 +214,20 @@ def main():
             return 2
         _log("[DEGRADE] 留档快照 %s 不存在 → 用当前快照重放，仅健康校验（单源结论，未经留档对比）"
              % os.path.basename(snap))
-        rc, out = _replay(run_day)
-        print(out[-2000:])
-        if rc != 0:
-            _push("V1.3 每日复跑 FAIL", "复跑退出码 %d（降级路径，运行日 %s）" % (rc, run_day), "error", args.no_send)
-            _log("!! 降级复跑退出码 %d" % rc)
-            return 1
-        ok = _health_check(run_day, args.no_send)
+        # F7 修复：降级路径的重放同样会覆写当日候选（csv+md），先备份；
+        # finally 兜底恢复——重放/校验/打印任何一步异常都不留复跑产物在盘上
+        deg_items = [(baseline, _backup_baseline(baseline, "DEGRADE")),
+                     (md_baseline, _backup_baseline(md_baseline, "DEGRADE"))]
+        try:
+            rc, out = _replay(run_day)
+            _safe_print(out[-2000:])
+            if rc != 0:
+                _push("V1.3 每日复跑 FAIL", "复跑退出码 %d（降级路径，运行日 %s）" % (rc, run_day), "error", args.no_send)
+                _log("!! 降级复跑退出码 %d" % rc)
+                return 1
+            ok = _health_check(run_day, args.no_send)
+        finally:
+            _restore_baseline(deg_items, "DEGRADE")
         if not ok:
             _log("!! 降级健康校验不通过（exit=1）")
             return 1
@@ -187,26 +237,28 @@ def main():
         _log("[DEGRADE] 健康校验 PASS（exit=2，warn：留档缺失）")
         return 2
 
-    # ---- 1) 备份基准（复跑会重写同名路径） ----
-    if os.path.exists(baseline):
-        shutil.copy2(baseline, base_backup)
-        _log("[1/4] 备份基准 %s -> %s" % (os.path.basename(baseline), os.path.basename(base_backup)))
-    else:
+    # ---- 1) 备份基准（复跑会重写同名路径，csv+md 两文件） ----
+    items = [(baseline, _backup_baseline(baseline, "1/4")),
+             (md_baseline, _backup_baseline(md_baseline, "1/4"))]
+    if not items[0][1]:
         _log("[1/4] 当日无基准候选文件（红线空仓路径），跳过备份")
 
-    # ---- 2) 重放 ----
-    _log("[2/4] 重放 deploy_predict.py --date %s --model v3 --top-k 10" % run_day)
-    rc, out = _replay(run_day)
-    print(out[-3000:])
-    if rc != 0:
-        _push("V1.3 每日复跑 FAIL", "复跑退出码 %d（运行日 %s）" % (rc, run_day), "error", args.no_send)
-        _log("!! 复跑退出码 %d（exit=1）" % rc)
-        return 1
-
-    # ---- 3) 读取双方产物对比 ----
-    base = _load_csv(base_backup)   # 原始基准（已备份）
-    rep = _load_csv(baseline)       # 复跑产物（已重写同名路径）
-    sev, msg = _cmp(base, rep)
+    # ---- 2) 重放 + 3) 对比（F7 修复：finally 兜底恢复，任何异常/早退都不留复跑产物） ----
+    try:
+        _log("[2/4] 重放 deploy_predict.py --date %s --model v3 --top-k 10" % run_day)
+        rc, out = _replay(run_day)
+        _safe_print(out[-3000:])
+        if rc != 0:
+            _push("V1.3 每日复跑 FAIL", "复跑退出码 %d（运行日 %s）" % (rc, run_day), "error", args.no_send)
+            _log("!! 复跑退出码 %d（exit=1）" % rc)
+            return 1
+
+        # ---- 3) 读取双方产物对比（原件由 finally 恢复，复跑产物不留在盘上） ----
+        base = _load_csv(base_backup)   # 原始基准（已备份）
+        rep = _load_csv(baseline)       # 复跑产物（已重写同名路径）
+        sev, msg = _cmp(base, rep)
+    finally:
+        _restore_baseline(items, "F7")
     if sev == "pass":
         _log("[4/4] PASS：%s（exit=0）" % msg)
         print("[PASS] %s" % msg)
diff --git a/data/schedules/test_g2_rerun.py b/data/schedules/test_g2_rerun.py
index eb70396..584ef31 100644
--- a/data/schedules/test_g2_rerun.py
+++ b/data/schedules/test_g2_rerun.py
@@ -18,6 +18,7 @@
 """
 import argparse
 import os
+import shutil
 import subprocess
 import sys
 
@@ -34,6 +35,49 @@ def _push(title, body, level, no_send):
     subprocess.run(cmd, cwd=P16, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
 
 
+def _backup_baseline(path):
+    """重放前备份原件 → <path>.rerun_base；返回备份前原件是否存在。（F7 修复配套，2026-09-28）"""
+    existed = os.path.exists(path)
+    if existed:
+        shutil.copy2(path, path + ".rerun_base")
+        print("[F7] 备份原件 %s -> %s" % (os.path.basename(path), os.path.basename(path) + ".rerun_base"))
+    return existed
+
+
+def _restore_baseline(items):
+    """F7 修复（2026-09-28，候选可审计性）：重放会用同名路径覆写历史候选（本周六 09:10 定时任务
+    默认重放快照最新交易日），校验完成后必须恢复原件，否则历史候选被回归测试污染、不可审计。
+    - 原件存在过 → 从 .rerun_base 恢复
+    - 原件本不存在（红线空仓日）而重放新建 → 删除重放产物"""
+    for path, existed in items:
+        try:
+            backup = path + ".rerun_base"
+            if existed:
+                if os.path.exists(backup):
+                    shutil.copy2(backup, path)
+                    print("[F7] 恢复原件 %s（重放产物不落盘）" % os.path.basename(path))
+                else:
+                    print("!! [F7] %s 原件存在过但备份 %s 缺失，无法恢复——盘上是重放产物，审计时注意"
+                          % (os.path.basename(path), os.path.basename(backup)))
+            else:
+                if os.path.exists(path):
+                    os.remove(path)
+                    print("[F7] 移除重放产物 %s（当日原为红线空仓，保持无候选状态）" % os.path.basename(path))
+        except Exception as e:
+            print("!! [F7] 恢复 %s 失败（盘上可能留有重放产物，审计时注意）: %s" % (os.path.basename(path), e))
+
+
+def _safe_print(text):
+    """重放 stdout 按 utf-8/replace 解码后可能含 \ufffd，在 GBK 控制台 print 会
+    UnicodeEncodeError（2026-09-29 E2E 实测）——降级为可编码字符再打印，绝不让
+    打印崩溃中断「重放→恢复」链路。"""
+    try:
+        print(text)
+    except UnicodeEncodeError:
+        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
+        print(text.encode(enc, "replace").decode(enc))
+
+
 def _check_csv(path, no_send):
     import pandas as pd
     if not os.path.exists(path):
@@ -77,16 +121,7 @@ def main():
         cmd += ["--date", args.date]
     cmd += ["--top", str(args.top)]
 
-    print("== 重放 deploy_predict_g2.py %s ..." % (args.date or "(快照最新日)"))
-    r = subprocess.run(cmd, cwd=P16, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
-    log = r.stdout.decode("utf-8", "replace")
-    print(log[-3000:])
-    if r.returncode != 0:
-        # FAIL-LOUD 中止（防线D）或运行时异常
-        _push("G2 快照回放回归 FAIL", "重放退出码 %d（%s）" % (r.returncode, (args.date or "快照最新日")), "error", args.no_send)
-        return 1
-
-    # 落盘 CSV 路径（日期缺省时取快照最新交易日）
+    # 日期先于重放解析（F7 修复需要重放前备份原件；原实现重放后才定位产物路径，来不及备份）
     if args.date:
         date = args.date.replace("-", "")
     else:
@@ -95,7 +130,27 @@ def main():
         day = pd.read_parquet(snap)
         date = str(pd.to_datetime(day["trade_date"]).max().date()).replace("-", "")
     csv = os.path.join(P16, "data", "selections", "g2", "%s_g2_top%d.csv" % (date, args.top))
-    return 0 if _check_csv(csv, args.no_send) else 1
+    md = os.path.join(P16, "data", "selections", "g2", "%s_g2_selection.md" % date)
+    base_items = [(csv, _backup_baseline(csv)), (md, _backup_baseline(md))]
+
+    print("== 重放 deploy_predict_g2.py %s ..." % (args.date or "(快照最新日)"))
+    # F7 修复加固（2026-09-29 复核）：原实现逐 return 路径内联 _restore_baseline，
+    # 一旦 _replay/_check_csv 抛异常（典型：GBK 控制台 print 重放 stdout 触发
+    # UnicodeEncodeError），恢复永不执行 → 重放产物留在盘上污染历史候选。
+    # 改为 try/finally 兜底，与 compare_g2_daily.py / compare_v13_daily.py 同款。
+    try:
+        r = subprocess.run(cmd, cwd=P16, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
+        log = r.stdout.decode("utf-8", "replace")
+        _safe_print(log[-3000:])
+        if r.returncode != 0:
+            # FAIL-LOUD 中止（防线D）或运行时异常
+            _push("G2 快照回放回归 FAIL", "重放退出码 %d（%s）" % (r.returncode, (args.date or "快照最新日")), "error", args.no_send)
+            return 1
+
+        ok = _check_csv(csv, args.no_send)   # 校验基于重放产物，校验完由 finally 恢复原件
+    finally:
+        _restore_baseline(base_items)
+    return 0 if ok else 1
 
 
 if __name__ == "__main__":
diff --git "a/projects/Project_16_LightGBM\350\202\241\347\245\250\345\244\247\345\270\210/rebalance_daily.py" "b/projects/Project_16_LightGBM\350\202\241\347\245\250\345\244\247\345\270\210/rebalance_daily.py"
index 2d34b35..1132c08 100644
--- "a/projects/Project_16_LightGBM\350\202\241\347\245\250\345\244\247\345\270\210/rebalance_daily.py"
+++ "b/projects/Project_16_LightGBM\350\202\241\347\245\250\345\244\247\345\270\210/rebalance_daily.py"
@@ -29,6 +29,8 @@
 """
 import argparse
 import csv
+import hashlib
+import io
 import json
 import os
 import sys
@@ -118,22 +120,68 @@ def _trading_days_between(d0, d1):
     return n
 
 
-def load_selection(csv_path, top_n):
-    """读完整版清单，返回 total>=REDLINE 按 total 降序的前 top_n 只。"""
+def _candidate_meta(date, csv_path, loader):
+    """候选溯源 meta（2026-09-28 候选可审计性修复①+②，缺陷B）。
+
+    V1.3 的 rebalance json 曾有 selection_file/candidates_file 字段（20260901/0902），
+    09-03 起随脚本改版丢失（DE 排查 F5）；本次修回并升级为完整溯源（sha256 + asof 留档）。
+    V1.3 候选为当日早晨生成当日消费（无跨日覆盖），溯源主要防「同日复核重跑/TRAE 任务重写」
+    导致的事后失真。
+    loader: exact=当日清单精确匹配 / none=清单缺失或为空。
+    """
+    return {
+        "candidate_file": csv_path,        # 实际加载的清单文件路径
+        "candidate_loader": loader,
+        "candidate_data_date": date,       # V1.3 清单以运行日命名，数据日=运行日
+        "candidate_rows_raw": 0,           # CSV 原始行数（红线过滤前）
+        "candidate_sha256": None,          # 内容哈希（核心：防事后覆写失真）
+        "candidate_asof": None,            # asof 只读留档路径（读取点字节快照）
+    }
+
+
+def _archive_candidate_asof(date, meta, raw):
+    """asof 只读留档（修复③之 3d 读取侧变体）：把本次实际读取的清单字节写入
+    data/candidates_asof/<date>_<sha8>_<原名>。文件名含内容哈希 → 天然幂等，
+    内容不同则共存，绝不覆盖已有留档。失败只打日志，绝不阻断换仓。"""
+    try:
+        adir = os.path.join(os.path.dirname(C.TRADE_LOG), "candidates_asof")
+        os.makedirs(adir, exist_ok=True)
+        sha8 = (meta.get("candidate_sha256") or "nasha")[:8]
+        base = os.path.basename(meta.get("candidate_file") or "selection_full.csv")
+        adst = os.path.join(adir, "%s_%s_%s" % (date, sha8, base))
+        if not os.path.exists(adst):
+            with open(adst, "wb") as f:
+                f.write(raw)
+        meta["candidate_asof"] = adst
+    except Exception as e:
+        print(f"  [asof留档] 失败（不阻断换仓）: {e}")
+
+
+def load_selection(csv_path, top_n, text=None, meta=None):
+    """读完整版清单，返回 total>=REDLINE 按 total 降序的前 top_n 只。
+    text 参数（2026-09-28 溯源修复）：传入已读取的清单文本做单次读盘解析，
+    保证 sha/内容/asof 留档与实际解析三者同源；缺省时自行读盘（向后兼容）。
+    meta 参数（2026-09-29 DE 复核修正）：可选 dict，回填 candidate_rows_filtered =
+    红线过滤后、top_n 截断前的候选数——修复 candidate_rows 误记 len(target)（截断后）
+    导致口径与规格「过滤后候选数」及 G2/ENS 不一致的缺陷；不影响返回值。"""
+    if text is None:
+        with open(csv_path, encoding="utf-8-sig") as f:
+            text = f.read()
     stocks = []
-    with open(csv_path, encoding="utf-8-sig") as f:
-        for row in csv.DictReader(f):
-            code = (row.get("ts_code") or "").strip()
-            if not code:
-                continue
-            try:
-                total = float(row.get("total", 0) or 0)
-            except ValueError:
-                total = 0.0
-            if total >= REDLINE:
-                stocks.append({"code": code, "name": row.get("name", ""),
-                               "total": total, "prob": float(row.get("model_prob", 0) or 0)})
+    for row in csv.DictReader(io.StringIO(text)):
+        code = (row.get("ts_code") or "").strip()
+        if not code:
+            continue
+        try:
+            total = float(row.get("total", 0) or 0)
+        except ValueError:
+            total = 0.0
+        if total >= REDLINE:
+            stocks.append({"code": code, "name": row.get("name", ""),
+                           "total": total, "prob": float(row.get("model_prob", 0) or 0)})
     stocks.sort(key=lambda s: s["total"], reverse=True)
+    if meta is not None:
+        meta["candidate_rows_filtered"] = len(stocks)   # 过滤后、截断前（2026-09-29 DE 复核修正口径）
     return stocks[:top_n]
 
 
@@ -270,10 +318,25 @@ def main():
     eff_top = args.top if args.top < 2 else TOP_N
 
     sel_path = os.path.join(C.TRADE_LOG.rsplit("qmt_trade_log.csv", 1)[0], "selections", f"{args.date}_selection_full.csv")
+    # ---- 候选溯源（2026-09-28 缺陷B修复①+②）：单次读盘 + 读取点即时 SHA256 + asof 留档 ----
+    # 保证 sha/内容/留档与实际解析三者同源（R2），事后清单被覆写也能复现实盘输入。
+    cand_meta = _candidate_meta(args.date, sel_path, "exact")
     if not os.path.exists(sel_path):
+        cand_meta["candidate_loader"] = "none"
         print(f"!! 找不到当日清单: {sel_path}\n   先运行 review_full 生成完整版清单")
         return
-    target = load_selection(sel_path, eff_top)
+    try:
+        with open(sel_path, "rb") as f:
+            _raw_sel = f.read()
+        cand_meta["candidate_sha256"] = hashlib.sha256(_raw_sel).hexdigest()
+        _archive_candidate_asof(args.date, cand_meta, _raw_sel)
+        _sel_text = _raw_sel.decode("utf-8-sig")
+        cand_meta["candidate_rows_raw"] = sum(1 for _ in csv.DictReader(io.StringIO(_sel_text)))
+    except Exception as e:
+        cand_meta["candidate_loader"] = "read_error"
+        print(f"!! 当日清单读取失败: {e}")
+        return
+    target = load_selection(sel_path, eff_top, text=_sel_text, meta=cand_meta)
     if not target:
         if args.top == 0:
             # T-20260904-003 修复：T=0（沪深300≤-1.5% 大盘破位）→ 清仓信号，
@@ -430,7 +493,16 @@ def main():
                 "strategy_capital": round(capital, 2), "target_value_each": round(target_value, 2),
                 "tier": args.top, "eff_top": eff_top,  # tier=T档位(0/1/2)；eff_top=归一后实际持仓上限
                 "sell": sell_plan, "buy": [o for o in exec_buys if o.get("vol")],
-                "trim": trim_orders, "hold": hold_plan, "locked": locked}
+                "trim": trim_orders, "hold": hold_plan, "locked": locked,
+                # ---- 候选溯源（2026-09-28 修复①+②）----
+                "selection_file": sel_path,             # 修回 09-01/0902 曾有的溯源字段（DE 排查 F5）
+                "candidate_file": cand_meta["candidate_file"],
+                "candidate_loader": cand_meta["candidate_loader"],
+                "candidate_data_date": cand_meta["candidate_data_date"],
+                "candidate_rows": cand_meta["candidate_rows_filtered"],
+                "candidate_rows_raw": cand_meta["candidate_rows_raw"],
+                "candidate_sha256": cand_meta["candidate_sha256"],
+                "candidate_asof": cand_meta["candidate_asof"]}
         out = os.path.join(os.path.dirname(C.TRADE_LOG), f"rebalance_{args.date}.dryrun.json")
         with open(out, "w", encoding="utf-8") as f:
             json.dump(plan, f, ensure_ascii=False, indent=2, default=str)
@@ -563,6 +635,15 @@ def main():
         "sell": sell_plan, "buy": [o for o in exec_buys if o.get("vol")],
         "trim": trim_orders, "hold": hold_plan, "locked": locked,
         "guard_note": guard_note,
+        # ---- 候选溯源（2026-09-28 修复①+②）----
+        "selection_file": sel_path,             # 修回 09-01/0902 曾有的溯源字段（DE 排查 F5）
+        "candidate_file": cand_meta["candidate_file"],
+        "candidate_loader": cand_meta["candidate_loader"],
+        "candidate_data_date": cand_meta["candidate_data_date"],
+        "candidate_rows": cand_meta["candidate_rows_filtered"],
+        "candidate_rows_raw": cand_meta["candidate_rows_raw"],
+        "candidate_sha256": cand_meta["candidate_sha256"],
+        "candidate_asof": cand_meta["candidate_asof"],
     }
     out = os.path.join(os.path.dirname(C.TRADE_LOG), f"rebalance_{args.date}.json")
     with open(out, "w", encoding="utf-8") as f:
diff --git "a/projects/Project_16_LightGBM\350\202\241\347\245\250\345\244\247\345\270\210/rebalance_ens.py" "b/projects/Project_16_LightGBM\350\202\241\347\245\250\345\244\247\345\270\210/rebalance_ens.py"
index f8b1ae3..fe23c12 100644
--- "a/projects/Project_16_LightGBM\350\202\241\347\245\250\345\244\247\345\270\210/rebalance_ens.py"
+++ "b/projects/Project_16_LightGBM\350\202\241\347\245\250\345\244\247\345\270\210/rebalance_ens.py"
@@ -19,6 +19,8 @@
 import argparse
 import csv
 import datetime
+import hashlib
+import io
 import json
 import os
 import sys
@@ -225,35 +227,92 @@ def _latest_avail_candidate(date, top_n):
     return path, "运行日候选缺失，回退到最新数据日 %s 的候选" % d
 
 
+def _candidate_meta(date, csv_path, loader):
+    """候选溯源 meta（2026-09-28 候选可审计性修复①，缺陷B；与 G2 同机制同缺陷）。
+
+    背景：融合候选与 G2 共用夜跑副本机制（g2_candidates_night.ps1 L102-110），
+    副本次日会被正品覆写（实证：g2_ens/20260921_g2_top10.csv mtime=09-21 20:11:39），
+    事后文件不可考。本 meta 让每次换仓记录都能回答
+    「当时到底读了哪个文件、内容是什么、是否被事后改动」。
+    loader: exact=运行日精确匹配 / fallback=回退最新数据日 / none=无可用候选。
+    """
+    meta = {
+        "candidate_file": csv_path,        # 实际加载的候选文件路径（None=无候选）
+        "candidate_loader": loader,
+        "candidate_data_date": None,       # 候选文件名日期（数据日口径）
+        "candidate_rows_raw": 0,           # CSV 原始行数（红线过滤前）
+        "candidate_sha256": None,          # 内容哈希（核心：防事后覆写失真）
+        "candidate_asof": None,            # asof 只读留档路径（读取点字节快照）
+    }
+    if csv_path:
+        b = os.path.basename(csv_path)
+        if b[:8].isdigit():
+            meta["candidate_data_date"] = b[:8]
+    return meta
+
+
+def _archive_candidate_asof(date, meta, raw):
+    """asof 只读留档（修复③之 3d 读取侧变体）：把本次实际读取的候选字节写入
+    data/rebalance_g2_ens/candidates_asof/<date>_<sha8>_<原名>。文件名含内容哈希 → 天然幂等，
+    内容不同则共存，绝不覆盖已有留档。失败只打日志，绝不阻断换仓。"""
+    try:
+        adir = os.path.join(G.DATA_DIR, "rebalance_g2_ens", "candidates_asof")
+        os.makedirs(adir, exist_ok=True)
+        sha8 = (meta.get("candidate_sha256") or "nasha")[:8]
+        base = os.path.basename(meta.get("candidate_file") or "candidate.csv")
+        adst = os.path.join(adir, "%s_%s_%s" % (date, sha8, base))
+        if not os.path.exists(adst):
+            with open(adst, "wb") as f:
+                f.write(raw)
+        meta["candidate_asof"] = adst
+    except Exception as e:
+        print("  [asof留档] 失败（不阻断换仓）: %s" % e)
+
+
 def load_ens_selection(date, top_n):
     """读融合选股 CSV → 候选池（total>=REDLINE，按 total 降序；不截断，买入侧自行排除持仓取最高）。
 
     先按运行日 <date> 精确匹配；缺失则回退最新数据日候选（见 _latest_avail_candidate）。
     绝不回退 G2/V1.3 的选股文件（融合/G2 隔离红线）。
+    返回 (picks, note, meta)：
+    - meta 为候选溯源（2026-09-28 缺陷B修复①）：单次读盘 + 读取点即时 SHA256 + asof 留档，
+      保证 sha/内容/留档三者同源一致（R2），事后候选被覆写也能复现实盘输入。
     """
     csv_path = os.path.join(G.ENS_SELECT_DIR, "%s_g2_top%d.csv" % (date, top_n))
     note = "融合候选池 %d 只（红线 %.0f）" % (top_n, G.REDLINE)
+    loader = "exact"
     if not os.path.exists(csv_path):
         csv_path, note = _latest_avail_candidate(date, top_n)
+        loader = "fallback"
         if csv_path is None:
-            return [], note
+            return [], note, _candidate_meta(date, None, "none")
+    meta = _candidate_meta(date, csv_path, loader)
     picks = []
-    with open(csv_path, encoding="utf-8-sig") as f:
-        for row in csv.DictReader(f):
-            code = (row.get("ts_code") or "").strip()
-            if not code:
-                continue
-            try:
-                total = float(row.get("total_new", row.get("total", 0) or 0))
-            except ValueError:
-                total = 0.0
-            if total < G.REDLINE:
-                continue
-            picks.append({"code": code, "total": total})
+    try:
+        with open(csv_path, "rb") as f:
+            raw = f.read()
+        meta["candidate_sha256"] = hashlib.sha256(raw).hexdigest()
+        _archive_candidate_asof(date, meta, raw)
+        text = raw.decode("utf-8-sig")
+    except Exception as e:
+        meta["candidate_loader"] = "read_error"
+        return [], "融合候选读取失败(%s)" % e, meta
+    for row in csv.DictReader(io.StringIO(text)):
+        meta["candidate_rows_raw"] += 1
+        code = (row.get("ts_code") or "").strip()
+        if not code:
+            continue
+        try:
+            total = float(row.get("total_new", row.get("total", 0) or 0))
+        except ValueError:
+            total = 0.0
+        if total < G.REDLINE:
+            continue
+        picks.append({"code": code, "total": total})
     if not picks:
-        return [], "融合选股无 total>=%.0f 的票（宁缺毋滥）" % G.REDLINE
+        return [], "融合选股无 total>=%.0f 的票（宁缺毋滥）" % G.REDLINE, meta
     picks.sort(key=lambda p: p["total"], reverse=True)
-    return picks, "融合候选池 %d 只（红线 %.0f）" % (len(picks), G.REDLINE)
+    return picks, "融合候选池 %d 只（红线 %.0f）" % (len(picks), G.REDLINE), meta
 
 
 def load_ens_ledger(date):
@@ -314,12 +373,20 @@ def build_plan(date, capital):
     - 卖出：持仓满 HOLD_DAYS 到期 → SELL（不看是否在候选池内；止损/止盈由桥内风控处理）
     - 买入：持仓数 < TOP_N 时，从候选池（Top10）选 total_new 最高、不在持仓、过红线的补足（while len(hold) < TOP）
     返回 (orders, plan_dict)。"""
-    pool, note = load_ens_selection(date, G.SELECT_TOP)   # 候选池（Top10）
+    pool, note, cand_meta = load_ens_selection(date, G.SELECT_TOP)   # 候选池（Top10）+ 溯源 meta
     ledger = load_ens_ledger(date)
     hold_dates = load_hold_dates()   # {code: "YYYYMMDD"} 持仓建仓日
     plan = {
         "date": date, "account_id": G.ACCOUNT_ID, "capital": round(capital, 2),
         "pool": pool, "ledger": ledger, "note": note,
+        # ---- 候选溯源（2026-09-28 缺陷B修复①：事后候选被覆写也能复现实盘输入）----
+        "candidate_file": cand_meta["candidate_file"],
+        "candidate_loader": cand_meta["candidate_loader"],
+        "candidate_data_date": cand_meta["candidate_data_date"],
+        "candidate_rows": len(pool),                # 红线过滤后候选数
+        "candidate_rows_raw": cand_meta["candidate_rows_raw"],
+        "candidate_sha256": cand_meta["candidate_sha256"],
+        "candidate_asof": cand_meta["candidate_asof"],
         "hold_days": G.HOLD_DAYS, "hold_dates": hold_dates,
         "sells": [], "buys": [], "skips": [],
     }
diff --git "a/projects/Project_16_LightGBM\350\202\241\347\245\250\345\244\247\345\270\210/rebalance_g2.py" "b/projects/Project_16_LightGBM\350\202\241\347\245\250\345\244\247\345\270\210/rebalance_g2.py"
index ee0f603..ea141f9 100644
--- "a/projects/Project_16_LightGBM\350\202\241\347\245\250\345\244\247\345\270\210/rebalance_g2.py"
+++ "b/projects/Project_16_LightGBM\350\202\241\347\245\250\345\244\247\345\270\210/rebalance_g2.py"
@@ -18,6 +18,8 @@
 import argparse
 import csv
 import datetime
+import hashlib
+import io
 import json
 import os
 import sys
@@ -263,35 +265,91 @@ def _latest_avail_candidate(date, top_n):
     return path, "运行日候选缺失，回退到最新数据日 %s 的候选" % d
 
 
+def _candidate_meta(date, csv_path, loader):
+    """候选溯源 meta（2026-09-28 候选可审计性修复①，缺陷B）。
+
+    背景：g2 候选命名空间有多个写手（夜跑正品/次日副本/TRAE 09:25 重算/复跑对比），
+    事后文件可能被覆写（r1 误判即由此产生）。本 meta 让每次换仓记录都能回答
+    「当时到底读了哪个文件、内容是什么、是否被事后改动」。
+    loader: exact=运行日精确匹配 / fallback=回退最新数据日 / none=无可用候选。
+    """
+    meta = {
+        "candidate_file": csv_path,        # 实际加载的候选文件路径（None=无候选）
+        "candidate_loader": loader,
+        "candidate_data_date": None,       # 候选文件名日期（数据日口径）
+        "candidate_rows_raw": 0,           # CSV 原始行数（红线过滤前）
+        "candidate_sha256": None,          # 内容哈希（核心：防事后覆写失真）
+        "candidate_asof": None,            # asof 只读留档路径（读取点字节快照）
+    }
+    if csv_path:
+        b = os.path.basename(csv_path)
+        if b[:8].isdigit():
+            meta["candidate_data_date"] = b[:8]
+    return meta
+
+
+def _archive_candidate_asof(date, meta, raw):
+    """asof 只读留档（修复③之 3d 读取侧变体）：把本次实际读取的候选字节写入
+    data/rebalance_g2/candidates_asof/<date>_<sha8>_<原名>。文件名含内容哈希 → 天然幂等，
+    内容不同则共存，绝不覆盖已有留档。失败只打日志，绝不阻断换仓。"""
+    try:
+        adir = os.path.join(G.DATA_DIR, "rebalance_g2", "candidates_asof")
+        os.makedirs(adir, exist_ok=True)
+        sha8 = (meta.get("candidate_sha256") or "nasha")[:8]
+        base = os.path.basename(meta.get("candidate_file") or "candidate.csv")
+        adst = os.path.join(adir, "%s_%s_%s" % (date, sha8, base))
+        if not os.path.exists(adst):
+            with open(adst, "wb") as f:
+                f.write(raw)
+        meta["candidate_asof"] = adst
+    except Exception as e:
+        print("  [asof留档] 失败（不阻断换仓）: %s" % e)
+
+
 def load_g2_selection(date, top_n):
     """读 g2 选股 CSV → 候选池（total>=REDLINE，按 total 降序；不截断，买入侧自行排除持仓取最高）。
 
     先按运行日 <date> 精确匹配；缺失则回退最新数据日候选（见 _latest_avail_candidate）。
     绝不回退 V1.3 的 selection_full.csv（G2/V1.3 隔离红线）。
+    返回 (picks, note, meta)：
+    - meta 为候选溯源（2026-09-28 缺陷B修复①）：单次读盘 + 读取点即时 SHA256 + asof 留档，
+      保证 sha/内容/留档三者同源一致（R2），事后候选被覆写也能复现实盘输入。
     """
     csv_path = os.path.join(G.G2_SELECT_DIR, "%s_g2_top%d.csv" % (date, top_n))
     note = "g2 候选池 %d 只（红线 %.0f）" % (top_n, G.REDLINE)
+    loader = "exact"
     if not os.path.exists(csv_path):
         csv_path, note = _latest_avail_candidate(date, top_n)
+        loader = "fallback"
         if csv_path is None:
-            return [], note
+            return [], note, _candidate_meta(date, None, "none")
+    meta = _candidate_meta(date, csv_path, loader)
     picks = []
-    with open(csv_path, encoding="utf-8-sig") as f:
-        for row in csv.DictReader(f):
-            code = (row.get("ts_code") or "").strip()
-            if not code:
-                continue
-            try:
-                total = float(row.get("total_new", row.get("total", 0) or 0))
-            except ValueError:
-                total = 0.0
-            if total < G.REDLINE:
-                continue
-            picks.append({"code": code, "total": total})
+    try:
+        with open(csv_path, "rb") as f:
+            raw = f.read()
+        meta["candidate_sha256"] = hashlib.sha256(raw).hexdigest()
+        _archive_candidate_asof(date, meta, raw)
+        text = raw.decode("utf-8-sig")
+    except Exception as e:
+        meta["candidate_loader"] = "read_error"
+        return [], "g2 候选读取失败(%s)" % e, meta
+    for row in csv.DictReader(io.StringIO(text)):
+        meta["candidate_rows_raw"] += 1
+        code = (row.get("ts_code") or "").strip()
+        if not code:
+            continue
+        try:
+            total = float(row.get("total_new", row.get("total", 0) or 0))
+        except ValueError:
+            total = 0.0
+        if total < G.REDLINE:
+            continue
+        picks.append({"code": code, "total": total})
     if not picks:
-        return [], "g2 选股无 total>=%.0f 的票（宁缺毋滥）" % G.REDLINE
+        return [], "g2 选股无 total>=%.0f 的票（宁缺毋滥）" % G.REDLINE, meta
     picks.sort(key=lambda p: p["total"], reverse=True)
-    return picks, "g2 候选池 %d 只（红线 %.0f）" % (len(picks), G.REDLINE)
+    return picks, "g2 候选池 %d 只（红线 %.0f）" % (len(picks), G.REDLINE), meta
 
 
 def load_g2_ledger(date):
@@ -352,12 +410,20 @@ def build_plan(date, capital):
     - 卖出：持仓满 HOLD_DAYS 到期 → SELL（不看是否在候选池内；止损/止盈由桥内风控处理）
     - 买入：持仓数 < TOP_N 时，从候选池（Top10）选 total_new 最高、不在持仓、过红线的补足（while len(hold) < TOP）
     返回 (orders, plan_dict)。"""
-    pool, note = load_g2_selection(date, G.SELECT_TOP)   # 候选池（Top10）
+    pool, note, cand_meta = load_g2_selection(date, G.SELECT_TOP)   # 候选池（Top10）+ 溯源 meta
     ledger = load_g2_ledger(date)
     hold_dates = load_hold_dates()   # {code: "YYYYMMDD"} 持仓建仓日
     plan = {
         "date": date, "account_id": G.ACCOUNT_ID, "capital": round(capital, 2),
         "pool": pool, "ledger": ledger, "note": note,
+        # ---- 候选溯源（2026-09-28 缺陷B修复①：事后候选被覆写也能复现实盘输入）----
+        "candidate_file": cand_meta["candidate_file"],
+        "candidate_loader": cand_meta["candidate_loader"],
+        "candidate_data_date": cand_meta["candidate_data_date"],
+        "candidate_rows": len(pool),                # 红线过滤后候选数
+        "candidate_rows_raw": cand_meta["candidate_rows_raw"],
+        "candidate_sha256": cand_meta["candidate_sha256"],
+        "candidate_asof": cand_meta["candidate_asof"],
         "hold_days": G.HOLD_DAYS, "hold_dates": hold_dates,
         "sells": [], "buys": [], "skips": [],
     }

```
