# DE 审计任务书：未提交改动批次审核（只读）

> ## 【已归档 · 本任务书的前提假设已被推翻，请勿再执行】
>
> **执行结果**：DE 未能产出任何审计结论。运行约 25 分钟后仅写出 1 条 `step_finish`
> （output 254 token，其余全为 cache read，无 SCOPE_ACK、无报告正文），CPU 在约 7 分钟
> 内仅增长约 5 秒，低于盯梢阈值 → 判定卡死，已终止全部 `deveco` 进程，**未重派**。
> 本文件转为**归档记录**，不是待办任务。预期中的 `results/DE审计_成交通道改动_20261002.md`
> **从未生成**，请勿去找。
>
> **Q1 前提已被独立核实推翻（2026-10-02，任务书第 40/67-71 行）**：任务书把两个
> 不同语义的 tag 混为一谈——
> - `build/strategy_p16_g2_bridge.py` 的 `BUILD_TAG = 20261002-193332` = **本地构建版本**
> - `heart_20261002.json` 的 `build_tag = 20260928-113106` = **QMT 客户端实际运行版本**
>
> 因此「build 产物 tag 仍是 20260928-113106」这一表述本身是错的：该值从未存在于 build
> 产物中，而在心跳文件里。真实情况是**构建侧完全同步**（g2/v13/ens 三对 src↔build 逐行
> 比对 DIFF_LINES=0，唯一差异为设计内的 BUILD_TAG 时间戳），但 **QMT 端运行的仍是
> 20260928-113106 旧版**——即「改了没部署」，而非「改了没重建」。这两件事必须分开，
> 详见下文 Q1 更正。
>
> **审计已由主 agent 独立完成**（未依赖 DE 结论）。下述问题清单保留原文仅为存档。

- 任务编号：T-20261002-005（审计类，非修改类）
- 委派人：主 agent（TRAE）
- 执行方：DE（DevEco Code CLI）
- 日期：2026-10-02
- 任务性质：**纯只读审计。不允许修改任何业务文件，不允许 git commit。**

---

## 一、开工前必须先回报（硬性要求）

在读任何代码之前，你必须**先输出一行**并等待确认：

```
SCOPE_ACK: 我理解本次审计范围为下列 10 个文件的未提交改动，共 N 行新增/删除。
```

如果你判断的范围与此清单不符，**立即停下并说明分歧**，不要自行扩大或缩小范围。

---

## 二、审计范围（写死的文件清单，共 10 个）

以下为 `git diff HEAD` 中与本批次相关的文件。**只审这 10 个**，其余工作区文件（根目录大量 `_*.txt` 探针产物、`scripts/_audit_*.py`、`scripts/_probe_*.py`、`data/rebalance_*`、`data/reconcile_*`、`research/tests/*` 等）**不属于本批次，不要审、不要提**。

| # | 相对路径 | 最后修改时间 | 改动量 |
|---|---|---|---|
| 1 | `projects/Project_16_LightGBM股票大师/rebalance_daily.py` | 10-02 17:37 | +63 |
| 2 | `projects/Project_16_LightGBM股票大师/qmt_monitor.py` | 10-02 17:38 | +35 |
| 3 | `projects/Project_16_LightGBM股票大师/research/tests/test_order_guard_backfill.py` | 10-02 17:47 | — |
| 4 | `projects/Project_16_LightGBM股票大师/order_guard.py` | 10-02 17:49 | +258 |
| 5 | `projects/Project_16_LightGBM股票大师/qmt_clear.py` | 10-02 17:50 | +18 |
| 6 | `projects/Project_16_LightGBM股票大师/strategy_capital.py` | 10-02 17:59 | +17 |
| 7 | `projects/Project_16_LightGBM股票大师/reconcile_trades.py` | 10-02 18:03 | +91 |
| 8 | `projects/Project_16_LightGBM股票大师/strategy/strategy_p16_v13_risk_src.py` | 10-02 18:53 | +471 |
| 9 | `projects/Project_16_LightGBM股票大师/build/strategy_p16_v13_risk.py` | 10-02 19:02 | +475 |
| 10 | `projects/Project_16_LightGBM股票大师/strategy/strategy_p16_g2_bridge_src.py` | 10-02 19:16 | +485 |

**注意文件 9 vs 文件 10 的时间顺序**：build 产物（19:02）早于 G2 桥源码（19:16）。这是本次审计的核心疑点，见问题 Q1。

查看差异的命令（注意必须加 `-c core.quotepath=false`，否则中文路径被八进制转义）：

```powershell
git -C D:\QuantLab -c core.quotepath=false diff HEAD -- "projects/Project_16_LightGBM股票大师/order_guard.py"
```

---

## 三、这批改动的自述意图（来自主 agent 的初步观察，**需你独立验证，不要照抄**）

改动主题疑似为：**成交通道 / 滑点告警 / 成交价解析 / 持仓对账**。

`order_guard.py` 新增了疑似以下函数（需你确认是否真实存在、是否合理）：

- `_resolve_fill_avg`
- `_adverse_slippage`
- `_slippage_alert`
- `_query_deals`

主 agent **无法确认这批改动是否经过用户批准**，因此需要你给出独立技术判断：它是修 bug 的合理产物，还是高风险乱改。

---

## 四、必须回答的问题（逐条作答，引用具体文件:行号）

### Q1（最高优先级）G2 桥构建不一致

已核实的事实：
- `strategy/strategy_p16_g2_bridge_src.py` 最后修改 `10-02 19:16`
- 对应构建产物 `build/strategy_p16_g2_bridge.py` 的 `BUILD_TAG` 仍为 `20260928-113106`
- 该 build 产物在 `git status` 中**没有** modified 标记

请回答：
1. 用 `Grep` 确认 `build/strategy_p16_g2_bridge.py` 中实际的 `BUILD_TAG` 值（用文件读取工具，不要只靠 git）。
2. 对比 V1.3 侧：`strategy/strategy_p16_v13_risk_src.py`（18:53）→ `build/strategy_p16_v13_risk.py`（19:02，BUILD_TAG 应为 `20261002-190249`），确认 V1.3 侧源与产物是否同步。
3. 结论：G2 桥是否处于「源码已改但从未重新构建」状态？这意味着如果现在人工粘贴部署到 QMT，部署的是 09-28 的旧逻辑、而 19:16 的新逻辑根本没生效——请确认这个推断是否成立。
4. G2 桥源码 19:16 那次改动内容是什么？是否完整、是否处于半成品状态（写了一半）？

### Q2 改动是否引入 P0 级实盘风险

重点检查（这是实盘成交链路，改错的代价是真金白银）：
- `order_guard.py` 的新增逻辑是否会**误拦正常成交**（fail-open 还是 fail-closed）？
- 滑点告警阈值是多少？会不会大量误报导致告警疲劳？
- `_resolve_fill_avg` 解析成交价时，对「未成交/部分成交/撤单」状态的处理是否正确？有没有把 None 或 0 当成成交价算进均价？
- `reconcile_trades.py` 的对账逻辑改动，会不会重演 2026-08-14 那种「误判未成交 → 撤销持仓 → 尾盘重复建仓」事故？
- `strategy_capital.py` 的资金额度校验改动，会不会突破「Σ capital_base ≤ 账户总资产」硬约束？
- `qmt_clear.py` / `qmt_monitor.py` 有没有动 `passorder` 调用方式？（红线：禁止裸调 passorder，必须走 `broker/qmt_order.py`）
- 改动是否引入 Python 3.6 不兼容语法？（QMT 是 3.6.8：不能用 `dict[str,...]`、`str | None`、`:=`、`match/case`、f-string）

### Q3 测试覆盖是否真的有效

`test_order_guard_backfill.py` 有没有**真实断言**？还是只有 print 没有 assert（假测试）？新增的滑点/成交价逻辑有没有被测试覆盖到？

### Q4 是否值得保留

给出明确结论：这 10 个文件的改动
- 应保留 / 应回滚 / 需修正后保留（分类列出，注明理由）
- 如果发现 P0 缺陷，指出**具体是哪几行**

---

## 五、硬性约束（违反即作废）

1. **只读**。不许修改任何 `.py` / `.md` / `.ps1` / `.yaml` 业务文件。
2. **禁止 git commit / add / checkout / stash / reset**。
3. **禁止运行任何交易相关脚本**：`rebalance_*.py`、`reconcile_*.py`、`deploy_predict_*.py`、`qmt_*.py`、`order_guard.py` 一律**不许执行**，只能读源码。
4. 唯一允许写入的文件是**第六节指定的报告文件**。
5. 不要重跑全量测试套件（耗时且可能写文件）；如需验证逻辑，只允许 `python -m py_compile <file>` 这类无副作用检查，且**必须用绝对路径的解释器**：
   ```
   C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe
   ```
   （`python` 不在 PATH，会 exit 9009）
6. 不要 git commit，不要改 git 配置。

---

## 六、报告输出

把结论写入**唯一**这个文件：

```
D:\QuantLab\projects\Project_16_LightGBM股票大师\results\DE审计_成交通道改动_20261002.md
```

报告结构：

```markdown
# DE 审计报告：成交通道改动批次

## 0. 范围确认
（回报 SCOPE_ACK，并列出实际审了的文件清单）

## 1. 这批改动想解决什么问题
（从 diff 反推真实意图，不要复述任务书）

## 2. Q1 答案：G2 桥构建不一致
## 3. Q2 答案：P0 风险逐项判定
## 4. Q3 答案：测试有效性
## 5. Q4 答案：保留/回滚建议（分类表格）
## 6. 我发现但任务书没提到的问题（如有）

## 附：证据索引
（每条结论对应的 文件:行号）
```

---

## 七、给 DE 的提醒（来自本项目历史教训）

- 本项目曾发生过 DE **自行重新界定审计范围**（被要求复核 P16，结果跑去审完全无关的 P21 并输出错误结论）。**不要重复这个错误。**
- 本项目曾发生过 DE 报告**复述已过时文档结论**。所有判断以**当前磁盘上的代码**为准，不要引用记忆里的旧结论。
- **不要编造**。读不到、看不出、拿不准的，直接写「无法确认」。一份诚实标注了 3 处「无法确认」的报告，远比一份全都有结论但有错错的报告有价值。