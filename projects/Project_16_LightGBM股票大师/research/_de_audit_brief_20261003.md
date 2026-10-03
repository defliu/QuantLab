# DE 审计任务书：Project_16 成交通道 16 文件（只读独立复核）

- 任务编号：T-20261003-001
- 委派人：主 agent（TRAE）
- 执行方：DE（DevEco Code CLI）
- 日期：2026-10-03
- 任务性质：**纯只读独立审计**
- 前序事故书：`research/_de_audit_brief_20261002.md`（已归档，DE 卡死无结论，**不要沿用其问题清单**）

---

## 〇、开工前必须先输出（硬性）

**你的第一条输出必须是且仅是这一行 `SCOPE_ACK`，然后才允许读任何文件：**

```
SCOPE_ACK: 16 targets + 13 adjacent (A-M) + 1 heartbeat (N) + 1 falsification-report (O), read-only, no-git-commit, scope=Project_16 deal channel
```

若你在读文件过程中发现范围需要变化，**先停手并回报**，不得自行扩大或缩小范围（2026-09-29 事故：DE 自行把复核范围改成完全无关的 Project_21，产出看似专业实则答非所问的报告）。

---

## 一、授权边界（违反即作废结论）

### 允许

- 只读下列文件
- 执行 `git diff` / `git log` / `git status` 等**只读**命令
- 执行 `ast` 解析、`py_compile`、自建只读检查脚本（写到 `research/` 下，`_de_` 前缀）
- 复跑 `research/tests/` 下自建测试

### 禁止（硬红线）

| 禁止项 | 说明 |
|---|---|
| 修改任何业务文件 | 16 个审计目标文件**只读**，发现问题写进报告，不动手改 |
| `git commit` / `git add` / `git stash` / `git checkout` | 本批次尚未 commit，任何 Git 写操作都会污染工作区 |
| `git add -A` | 工作区混有并行任务产物 |
| 执行生产交易脚本 | `rebalance_daily.py` / `rebalance_g2.py` / `rebalance_ens.py` / `qmt_clear.py` **一律带 `--live` 的调用绝对禁止**；只读 `--dry-run` 也须先说明理由 |
| 连接真实 QMT / miniQMT | 不得 import `xtquant` 做实盘连接；测试用 stub |
| 下单 / 撤单 / 改 `D:\QMT_POOL\` | 文件桥与账户状态一律只读 |
| 发飞书消息 | 不得调用 `lark_push.py` / lark-cli |
| 采信主 agent 结论 | 见 §五 |

---

## 二、审计目标：16 个文件（绝对路径，逐个列明）

全部位于 `D:\QuantLab\projects\Project_16_LightGBM股票大师\` 下。

### A 组 · 外部执行层（Python 3.11）— 8 个

```
1.  order_guard.py
2.  rebalance_daily.py
3.  reconcile_trades.py
4.  qmt_monitor.py
5.  qmt_clear.py
6.  strategy_capital.py
7.  premarket_g2_check.py
8.  g2_correction_exec.py
```

### B 组 · QMT 内嵌桥源码（Python 3.6.8 源）— 3 个

```
9.   strategy\strategy_p16_g2_bridge_src.py
10.  strategy\strategy_p16_v13_risk_src.py
11.  strategy\strategy_p16_ens_bridge_src.py
```

### C 组 · QMT 构建产物（GBK 单文件）— 3 个

```
12.  build\strategy_p16_g2_bridge.py
13.  build\strategy_p16_v13_risk.py
14.  build\strategy_p16_ens_bridge.py
```

### D 组 · 测试 + 废弃守卫 — 2 个

```
15.  research\tests\test_order_guard_backfill.py
16.  g2_correction_20260904.ps1
```

---

## 三、邻接验证文件（不属 16 文件，但结论依赖，须读）

```
A.  build_p16_g2.py                        （构建脚本：BUILD_TAG 替换 / GBK 转码 / 3.6 语法扫描）
B.  research\tests\test_zero_price_sell_fifo.py
C.  research\tests\test_rebalance_sell_priority.py
D.  research\tests\test_monitor_sell.py
E.  research\tests\test_g2_deal_fill_slippage.py
F.  research\tests\test_ens_deal_fill_slippage.py
G.  research\tests\test_v13_deal_fill_slippage.py
H.  research\tests\test_g2_state_machine.py
I.  research\tests\test_ens_state_machine.py
J.  research\tests\test_v13_state_machine.py
K.  is_trade_day.py                         （交易日门禁事实源）
L.  rebalance_g2.py                         （G2 换仓：确认卖出段独立于买入拦截）
M.  rebalance_ens.py                        （ENS 换仓：同上）
```

### 运行态证据（只读）

```
N.  D:\QMT_POOL\g2_bridge\state\heart_20261003.json
```

### 主 agent 结论（**待证伪对象，不是事实**）

```
O.  results\成交通道全链路审计报告_20261002.md
```

---

## 四、⚠️ Git 口径陷阱（不读这条会误判主 agent 谎报）

主 agent 文档写的是「**16 文件 / +3033/-793**」。你在项目目录下直接跑 `git diff --stat` 会看到 **27 文件 / +3365/-814**。两者都对，口径不同：

| 范围 | 文件数 | diff | 归属 |
|---|---|---|---|
| 本次审计目标 | **16** | `+3033/-793` | 成交通道修复（T-20261002-004） |
| 飞书身份订正 + 废弃脚本加固 + 文档 | **11** | `+332/-21` | T-20261002-003（**不在本次审计范围**） |
| Project_16 已跟踪改动合计 | 27 | `+3365/-814` | 上述两集合并 |

### 16 文件逐个 numstat（主 agent 实测，供你交叉核对）

```
+375/-130   build\strategy_p16_ens_bridge.py
+369/-129   build\strategy_p16_g2_bridge.py
+360/-121   build\strategy_p16_v13_risk.py
+373/-128   strategy\strategy_p16_ens_bridge_src.py
+368/-128   strategy\strategy_p16_g2_bridge_src.py
+358/-119   strategy\strategy_p16_v13_risk_src.py
+313/-5     research\tests\test_order_guard_backfill.py
+255/-3     order_guard.py
+79/-12     reconcile_trades.py
+57/-6      rebalance_daily.py
+31/-4      qmt_monitor.py
+26/-0      g2_correction_exec.py
+27/-3      premarket_g2_check.py
+17/-1      qmt_clear.py
+13/-4      strategy_capital.py
+12/-0      g2_correction_20260904.ps1
------------------------------------
16 files  +3033/-793
```

### ⚠️ numstat 与 src/build 行数差是两回事（易误判点）

上表 numstat 是**各文件相对 HEAD 的变化量**，**不是** src 与 build 之间的当前行数差。观察到的 `build` 增量比 `src` **多** 2/1/2 行，是因为两者对 HEAD 的基线不同，**不能**据此推断任何行数关系。当前实际行数是**完全相等**的（见 Q1）。

真正应验证的是 Q1 的逐行比对，**不要**用 numstat 增量差代替。

⚠️ **第三行的 27 是 `git diff --stat` 的 tracked 口径，不含未跟踪新文件。** 本批次新建、目前仍是 untracked 的文件（`git status --short` 里 `??`）至少包括：

```
research\_de_audit_brief_20261002.md          （旧任务书，事故归档）
research\_de_audit_brief_20261003.md          （本任务书）
results\成交通道全链路审计报告_20261002.md     （主 agent 报告，§三 O 项）
research\tests\test_zero_price_sell_fifo.py   （邻接 B，以下 8 个测试均为 untracked）
research\tests\test_rebalance_sell_priority.py（邻接 C）
research\tests\test_g2_deal_fill_slippage.py  （邻接 E）
research\tests\test_ens_deal_fill_slippage.py （邻接 F）
research\tests\test_v13_deal_fill_slippage.py （邻接 G）
research\tests\test_g2_state_machine.py       （邻接 H）
research\tests\test_ens_state_machine.py      （邻接 I）
research\tests\test_v13_state_machine.py      （邻接 J）
```

注意：**邻接 D `test_monitor_sell.py` 是已跟踪且本次未改动**（不在 `??` 也不在 `M` 列表内），邻接 A~C、K~M 同理属既有文件。DE 若用 `git diff` 判断「本批次改了什么」，只能看到 16 文件集合 + 11 文件集合，8 个 untracked 测试的**内容变更无法从 git 看出**——这本身就是审计陷阱：请**直接读文件现状**，不要只看 diff。

所以「工作区文件数」远大于 27，**不要**拿 `git status` 的行数去对主 agent 的数字。

11 个范围外文件（供你识别「哪些不属于本审计」，**不要审**）：

```
G2_RUNBOOK.md / PROJECT_MEMORY.md / QMT避坑指南.md / SERVER_DEPLOY.md /
WORKFLOW_DEPLOY.md / data\rebalance_20260929.trae_note.json /
push_lark.bat / push_lark.ps1 / scripts\push_auction_summary.ps1 /
specs\TRAE任务修改说明_0945开盘实时复核_20260930.md / results\v13_paper_parallel_report.md
```

复核命令（注意中文路径需 `core.quotepath=false`）：

```powershell
git -C D:\QuantLab -c core.quotepath=false diff --stat -- "projects/Project_16_LightGBM股票大师"
```

---

## 五、⚠️ 独立性纪律（最重要的一条）

**主 agent 已自行审计过本批次并给出「16/16 全部保留、无一建议回滚」的结论。你必须假定该结论可能有误。**

具体要求：

1. **不要**因为主 agent 写了某机制「已修好」就直接采信 —— 去代码里逐行确认它真的存在且语义正确
2. 若你发现主 agent **结论错误**（含文件归属错误、数字错误、机制描述错误），**必须明确写出并给出行号证据**，不得含糊带过
3. 若你发现主 agent **漏掉了 P0/P1 风险**，直接列出，即使与主 agent 结论冲突
4. 「没发现问题」不是合格结论 —— 必须逐项给出**你实际验证的方式**（读了哪些行、跑了什么、看到什么）
5. 明确区分「代码事实」与「推测」

### 主 agent 已自认的三处错误（供你参考，其余错误请自行发现）

- 曾误报 `order_guard.py` 缺 `_resolve_fill_avg` / `_adverse_slippage`（实际不存在于该文件，存在于三个 QMT 桥内部）
- 早期用 `DIFF_LINES == 0` 判构建一致性（错误判据，会漏掉 `BUILD_TAG` 差异）
- 文档中曾写 `src=1613/1253/1603`、`DIFF_LINES=1`，**行数与差异数均错**（实际 1773/1365/1763，每对恰 2 处差异），已于 2026-10-03 全量重算订正，见 Q1

---

## 六、逐项审计问题

### Q1（最高优先）src ↔ build 三对一致性

对 3 对文件按**各自正确编码完整解码**后逐行比对：

```
strategy\strategy_p16_g2_bridge_src.py   (utf-8)  ↔  build\strategy_p16_g2_bridge.py   (gbk)
strategy\strategy_p16_v13_risk_src.py    (utf-8)  ↔  build\strategy_p16_v13_risk.py    (gbk)
strategy\strategy_p16_ens_bridge_src.py  (utf-8)  ↔  build\strategy_p16_ens_bridge.py  (gbk)
```

主 agent 2026-10-03 实测（**旧口径 1613/1253/1603、DIFF_LINES=1 已作废**）：

```
g2   src=1773  build=1773  len_equal=True  full_decode_diff=2  差异行=[1, 25]
v13  src=1365  build=1365  len_equal=True  full_decode_diff=2  差异行=[1, 22]
ens  src=1763  build=1763  len_equal=True  full_decode_diff=2  差异行=[1, 24]

每对恰好 2 处差异：
  L1        # coding=utf-8   →  # coding=gbk          （QMT 红线强制）
  BUILD_TAG 行  src 占位时间戳  →  本地构建时间戳         （构建器自动替换）

归一化（编码头 + BUILD_TAG 置占位符）后：
  normalized_diff_lines = 0
  difflib.SequenceMatcher ratio = 1.000000, non-equal blocks = 0
```

**请独立复算，并回答**：

- 行数与差异行位置是否与上表一致？（若你算出别的数字，以你的实测为准并指出差异）
- 差异是否**恰为2 处**，且**每一处**都能归因于设计转换（编码头 / `BUILD_TAG`）？是否存在第 3 处未察觉的差异？
- 归一化后是否真的完全一致？建议用两种方法互证（归一化逐行比较 + `difflib.SequenceMatcher`）。
- 按三层判据分别陈述结论：①原始字节 ②完整解码逐行 ③归一化逻辑文本。**不要**用「`DIFF_LINES` 是否为 0」或「字节是否相同」做判据，两者都会误判。
- 构建脚本 `build_p16_g2.py` 是否可能产生**静默不一致**（如替换 `BUILD_TAG` 时误伤正文、编码转换截断、写入未刷新）？请读构建脚本验证替换逻辑的精确性。

### Q2 双 BUILD_TAG 语义（2026-10-02 任务书在此前提错误）

```
build\strategy_p16_g2_bridge.py  的 BUILD_TAG        = 本地构建版本
state\heart_<date>.json          的 build_tag       = QMT 客户端实际运行版本
```

主 agent 实测：本地 `20261002-193332`，QMT 心跳 `20260928-113106`。

**请回答**：

- 读 `heart_20261003.json` 核实 `build_tag` 实际值
- 真实问题是「改了没重建」还是「改了没部署」？
- `premarket_g2_check.py` 新增的 `_read_local_build_tag()`（动态读 build 产物）逻辑是否正确？有没有路径、编码、异常处理缺陷？
- 部署后该检查是否必然返 `RET=True`？若 build 产物被重新构建而 QMT 未更新，会怎样？

### Q3 卖出优先原则是否真正成立

项目硬红线：**卖出风控优先级高于买入优化。**

- `rebalance_daily.py`：zero-target 分支是否已改为置 `buy_allowed=False` 而非 `return`？到期 SELL 是否仍会生成？
- `rebalance_g2.py` / `rebalance_ens.py` 的 `build_plan()`：pool 为空时账本到期 SELL 是否照常生成？
- 候选缺失 / 数据滞后 / 红线空仓 / 宁缺毋滥，是否**只禁买不禁卖**？
- 是否存在**任何** early `return` 仍压在「持仓读取 → 成熟度 → sell_plan → 委托」链路之上？
- 有 SELL 但 0 买入时，是否会正确走 `--live` 提交？

**只允许整链路中止的情形应恰为 3 种**：非零退出码 traceback / 文件桥不存活 / plan JSON 完全缺失或结构损坏。请核对这个收窄是否彻底。

### Q4 成交记账正确性（本批次修复的核心）

逐项核对：

| 机制 | 应有语义 | 检查点 |
|---|---|---|
| 外部三态 | `fill_avg` / `ref_price` / `unresolved` | `order_guard.py` |
| 桥内四级降级 | `fill_avg → order_obj → ref_price → unresolved` | 三个桥；桥内比外部**多一级**是否合理 |
| 成交明细双重过滤 | 必须同时校验 `oid in want` **且** `_norm_code(stock_code) == target` | 桥内 `query_stock_trade_stats` |
| 多轮重挂 | pending 须同时保留 `order_id` **和** `order_ids` 列表 | 只存最后一个会致回补均价偏小 |
| 零价 SELL FIFO | `unresolved` 价 0 时仍扣 FIFO 成本，未知价不算已实现盈亏，价回填后重算 | 相关测试 |
| 入账门槛 | 必须是 `if traded_vol > 0`，**不能**是 `if ok and traded_vol` | `rebalance_daily.py`(3处) + `qmt_clear.py`(1处) |
| 均价不完整 | 必须返回 `None`，不得用部分成交均价冒充全部成交均价 | 桥内 |

**重点追问**：`ok=False` 但 `traded_vol > 0` 的场景，账本是否仍会入账？（这正是 2026-09-30 事故形态：603237.SH 卖单被误记 `CANCELED_TIMEOUT`，实为全成交 2800 股。）

### Q5 Python 3.6.8 兼容性

对 6 个桥文件（3 src + 3 build）做 AST 静态检查，确认**零命中**：

```
fstring (ast.JoinedStr) = 0
walrus (ast.NamedExpr)  = 0
pep604  (X | Y 注解)    = 0
genann   (返回类型注解)  = 0
match    (ast.Match)    = 0
```

并额外检查：

- `dict[str, ...]` / `list[str]` / `tuple[...]` / `set[...]` 下标式注解
- QMT 专用红线：GBK 编码、首行 `# coding=gbk`、无 pyarrow（不得读 parquet）、数据源必须 CSV
- `passorder()` 是全局函数，不得写成 `C.passorder()`
- 不得读 `datetime.now()`（必须用 `C.get_current_time()`）

**必须明确的边界**：AST 静态检查 **≠** 真实 Python 3.6.8 运行验证（当前本机只有 3.11）。请在报告中明确标注你验证到哪一层、哪一层未验证。

### Q6 测试有效性（不要只看计数）

10 个自建测试，主 agent 实测 `PASS=618 / FAIL=0`：

```
test_order_guard_backfill.py        68    test_g2_state_machine.py       108
test_zero_price_sell_fifo.py        31    test_ens_state_machine.py      108
test_rebalance_sell_priority.py     18    test_v13_state_machine.py       55
test_monitor_sell.py                19
test_g2_deal_fill_slippage.py       62    test_ens_deal_fill_slippage.py  62
test_v13_deal_fill_slippage.py      87
```

解释器（Agent 终端无 `python`，必须用绝对路径）：

```
C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe
```

**关键追问**：

- 这些测试**是否真的验证了行为**，还是只做源码字符串断言？（若仅 `inspect.getsource` 匹配代码文本，测试通过 ≠ 逻辑正确 —— 请指出哪些测试属于此类）
- `xtquant` stub 是否**彻底隔离**了真实 QMT？是否存在漏 stub 导致测试可能触碰实盘的可能？
- `test_order_guard_backfill.py` 用自建 runner（`def _check(name, cond, detail="")`），**必须直接执行**、不能用 pytest 启动。请确认这一点，并检查是否还有其他文件同样情况。
- 有无**未被任何测试覆盖**的本批次改动代码路径？
- 复跑结果是否与主 agent 一致？不一致时以你的实测为准并说明差异。

### Q7 废弃脚本 fail-closed

`g2_correction_exec.py` 与 `g2_correction_20260904.ps1` 保留历史材料但运行时禁用：

```python
if os.environ.get("G2_CORRECTION_EXEC_ALLOW") != "1":  → return 2
if ($env:G2_CORRECTION_EXEC_ALLOW -ne "1") { exit 2 }
```

**请回答**：守卫是否在**任何入口路径**（含 import 期、函数调用前、main 分支）都无法绕过？是否存在被 `import` 后直接调用内部函数而跳过守卫的路径？`G2_CORRECTION_EXEC_ALLOW=1` 是否构成生产误执行风险？

### Q8 资金与账号隔离

- `strategy_capital.py`：多策略虚拟子账户 `capital_base` 隔离是否可靠？是否存在越权动用他人额度或纳管他人持仓的路径？
- 持仓账本是否内嵌 `account_id` 戳？不匹配时是否 fail-safe（备份 + 空仓起步）而非带错账本交易？
- 本批次 `strategy_capital.py` 的改动是否引入新风险？

---

## 七、交付物

**唯一交付物**（写文件，禁止污染其他目录）：

```
D:\QuantLab\projects\Project_16_LightGBM股票大师\results\DE审计_成交通道16文件_20261003.md
```

### 报告必须包含

1. **Q1~Q8 逐项结论**，每项标注：`通过` / `有问题` / `无法验证`
2. 每个「有问题」项给出：**文件绝对路径 + 行号 + 问题描述 + 建议修法**（只写建议，不动手改）
3. **16 文件裁决表**：`保留` / `回滚` / `需追加修改`，逐个给一行理由
4. 你**实际执行过的命令与验证方式**清单（证明不是空谈）
5. 明确列出**你未能验证的部分**及原因
6. 与主 agent 结论**不一致**之处（若有），逐条列出并给证据

### 报告纪律

- 不要复述本任务书的问题清单当作「已验证」
- 不要输出「整体看起来没问题」这类无信息量结论
- 数字必须是你自己跑出来的，**禁止引用本任务书或主 agent 报告中的任何数字**作为你的结论

---

## 八、执行纪律（吸取 2026-10-02 事故教训）

1. **必须全程盯梢**：DE 进程启动后确认 `deveco.exe` 存活；长任务轮询检查输出文件是否落盘 + CPU 是否增长
2. **卡死判据**：输出文件长时间不落盘 **且** CPU 墙钟利用率 <5% → 连续 2 次轮询确认后立即 KillingDE 进程，**不重派**（同类任务连续失败 2 次即放弃）
3. **本任务书走文件，不走命令行直传**（含中文长文本经 `.cmd` 传参会被截断）
4. 2026-10-02 那次 DE 运行 25 分钟无 `SCOPE_ACK`、无报告正文、CPU 7 分钟仅增长 5 秒 → 已归档。**若本次同样无 `SCOPE_ACK`，立即回报，不要继续等**

---

## 九、明确未执行声明（供你在报告中核对）

本批次主 agent **明确未执行**以下动作，若你在审计中发现任何一项**实际已发生**，属重大发现，须立即单独标红报告：

- [ ] Git commit / git add / git stash
- [ ] QMT 人工部署或重载
- [ ] 任何真实下单、撤单、换仓
- [ ] 真实 Python 3.6.8 运行验证
- [ ] 修改 `D:\QMT_POOL\` 下任何文件桥状态
- [ ] 发送飞书消息

---

**再次强调**：你的价值在于**独立发现主 agent 的错误**。发现问题不是失职，复述「已修复」才是失职。
