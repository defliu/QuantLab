# 定时任务迁移执行记录（WorkBuddy 侧）

> 执行时间：2026-09-23 04:2x
> 依据：`定时任务迁移可行性评估_WorkBuddy_20260923.md`（用户选定：除 Windows 已覆盖 5 条外全建；下单三条采取"提前触发"方案）
> 结果：**已创建 8 条**，其中 5 条 ACTIVE、3 条 PAUSED（待 TRAE 侧停用后再点亮）

> ⚠️ **时效性更正（2026-10-09 核实，本文件是历史记录、不是现状）**：
> 1. **本文件描述的是「旧 WorkBuddy home」`C:\Users\Administrator\.workbuddy` 的 2026-09-23 状态**。当前会话所在的 WorkBuddy home 是 `C:\Users\Administrator\.workbuddy-ai`，其自动化库中**没有任何 Project_16 任务**（仅 1 条无关的「飞书消息桥接：处理并回复」）。
> 2. 只读直查旧 home 的 `automations` 表：**8 条 P16 任务仍登记在册，但已全部为 `PAUSED` 且 `next_run_at = None`（不再被调度）、`last_run_at = None`（从未运行）**；另有「模拟账号盘后监控」1 条已软删除。即上面「5 条 ACTIVE」**已不成立**。
> 3. 因此**当前不存在双调度器重复下单风险**；下文 §二「点亮顺序」仅在将来真要迁移时才需遵守。
> 4. 结论：以「当前 WorkBuddy home（`.workbuddy-ai`）无任何 P16 任务」为现状口径。

---

## 一、已创建的 8 条自动化

| # | 名称（WorkBuddy 面板） | 触发 | 状态 | automation id | 工作目录 |
|---|---|---|---|---|---|
| ① | P16 开盘实时复核 (09:38)｜真实下单 | 工作日 09:38 | **PAUSED** | `500481f9-456c-4189-846a-5adc3ff6d9db` | Project_16 |
| ② | P16 候选预生成 (09:15) | 工作日 09:15 | ACTIVE | `7ecb5673-2a58-43fb-a5f4-bd9c8a71d67a` | Project_16 |
| ③ | P16 集合竞价增强预判 (09:25) | 工作日 09:25 | ACTIVE | `4efe8cb3-dab3-468e-829c-2fa3fcb9dbb0` | Project_16 |
| ④ | P16 G2换仓 (09:45)｜真实下单 | 工作日 09:45 | **PAUSED** | `203c60c3-ec58-430d-9ec6-7457cd6ce1e2` | Project_16 |
| ⑤ | P16 融合换仓 (09:47)｜真实下单 | 工作日 09:47 | **PAUSED** | `01f00001-c9a3-4b60-b9fe-99a83aae00a5` | Project_16 |
| ⑧ | P16 G2日终对账 (15:45) | 工作日 15:45 | ACTIVE | `32688176-1791-4c8c-8463-f36f7b576334` | Project_16 |
| ⑨ | P16 融合日终对账 (15:50) | 工作日 15:50 | ACTIVE | `663fed5a-acc8-4abd-acb1-b233d9babdba` | Project_16 |
| ⑭ | 模拟账号盘后监控 (15:05) | 工作日 15:05 | ACTIVE | `cfba1674-1254-4aa5-a08e-3cc2959f929f` | D:\Workspace |

排程时点已回读验证（`nextRunAt` 换算本地时间）：

```
09:15 / 09:25 / 15:05 / 15:45 / 15:50  均落在 2026-09-23（周三）
```

**未创建（按评估结论排除）**：#6 午休报告、#7 盘后复盘、#10 Tushare刷新、#11 夜间检修、#13 成本锚——Windows 计划任务已覆盖。
**未创建（阻塞）**：#12 夸克行情每周检查——缺两个分享链接，且 `quarkclouddrive` skill 未安装到 WorkBuddy。

---

## 二、下单三条的时点调整（"提前触发"方案）

| 任务 | 原 TRAE 时间 | WorkBuddy 新时间 | 对应 guard 时点 | 落盘余量（按原 ~12 分钟耗时估） |
|---|---|---|---|---|
| ① 开盘实时复核 | 09:45 | **09:38** | 10:05（V13 guard） | 约 15 分钟 |
| ④ G2换仓 | 09:50 | **09:45** | 10:07（G2 guard） | 约 17 分钟 |
| ⑤ 融合换仓 | 09:52 | **09:47** | 10:08（ENS guard） | 约 16 分钟 |

**三条 PAUSED 状态的原因**：TRAE 侧对应任务当前仍在正常运行（2026-09-22 实测三项均按时完成），若现在同时点亮 WorkBuddy 版本，同一交易日会出现**两次真实下单**。

### 点亮顺序（务必按序）

1. 先在 WorkBuddy 面板对 3 条 PAUSED 任务点「立即执行」验证一次 message 中的脚本链路可跑通（**注意：这也会真实下单，请选非交易时段只验证 dry-run 部分，或在下一个交易日开盘前先确认**）；
2. 确认无误后，**暂停 TRAE Agent 面板中对应的 09:45 / 09:50 / 09:52 三条任务**；
3. 再把这 3 条 WorkBuddy 任务切为 ACTIVE；
4. 次日开盘观察：① guard 日志应出现「标记已存在 → 已执行，跳过」；② 飞书只收到一份成交回报卡。

> ⚠️ 第 2 步在当前环境**无法自动完成**（TRAE Agent 面板是独立应用），需你手动操作。

---

## 三、ACTIVE 的 5 条：暂存的双重推送（可接受，属验证成本）

②③⑧⑨ 在 TRAE 侧同样有对应任务且仍在运行，因此短期内会出现**重复执行**：

| 任务 | 重复行为 | 危害 |
|---|---|---|
| ② 候选预生成 09:15 | 幂等（`D_model_top10.csv` 已存在即退出），无推送 | 无 |
| ③ 集合竞价 09:25 | 各推一次飞书摘要 | 重复推送，无数据危害 |
| ⑧ G2日终对账 15:45 | 各推一次总览卡 | 重复推送，无数据危害 |
| ⑨ 融合日终对账 15:50 | 各推一次总览卡 | 重复推送，无数据危害 |
| ⑭ 模拟账号监控 15:05 | TRAE 侧该任务为 PAUSED | 无 |

这 5 条均为**只读**任务，不产生下单。重复推送是验证 WorkBuddy 侧飞书通道与数据源映射是否真的可用的必要成本——**验证通过后再停 TRAE 侧**。

---

## 四、为适配 WorkBuddy 环境所做的 message 改造

原文档的 message 直接照搬会大面积失败，创建时已做三处改造（每条 message 内已内联说明）：

**1. Python 解释器改为绝对路径**
原 message 用裸 `python`，而本环境 PATH 上的 `python` 不可靠（本机实测 WorkBuddy shell 环境 PATH 缺失外部命令）。统一改为：
```
C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe
```
实测该解释器含 numpy / pandas / lightgbm / pyarrow / xtquant（Python 3.11.15）；TRAE 内置解释器（3.10.11）**缺 xtquant**，不能用于换仓类任务。

**2. 数据源降级链重写**（TRAE 专有插件在 WorkBuddy 不存在）

| 原 message 引用 | WorkBuddy 处置 |
|---|---|
| `mcp_tdx` / `full-link 内置 TDX` / `mcp_plugin_TDX_tdx` | 统一为 **通达信 MCP（tdx-connector）**，唯一 TDX 通道 |
| `sina-finance` | 不存在 → 改用 **mx-ds-mcp** 或 **westock-mcp** |
| `iFind` | 不存在 → 改用 **wind-finance** |
| `东财mx` | 保留（mx-ds-mcp 已连接） |
| `悟道直连 wudao_client.py` | **保留**——它是本地 HTTP 直连脚本，不依赖 TRAE 插件 |
| `东财curl`、腾讯 API(qt.gtimg.cn) | 保留 |

**3. 飞书 lark-cli 路径**
原文档写 `...\lark\1.0.4\bin\lark-cli.exe` **不存在**；代码侧（`qmt_config.py` / `g2_config.py` / `g2_ens_config.py` / `qmt_card.py`）**早已是 1.0.5**，即代码没问题、文档过期。改造后策略：**优先调用项目内 `push_*.py` 脚本**（其路径由代码固化，不会随文档漂移）；仅保留直连说明时给出 1.0.5 正确路径。

**4. 新增【时序红线】段**（仅三条下单任务）
把"必须在 guard 时点前落盘"写成显式硬约束，并规定：临近 guard 时点宁可不换仓也不迟到下单；绝不伪造 `executed_live` 标记；已下单但落盘失败须立即告警并提示人工创建锁文件。

---

## 五、验证记录

| 检查项 | 结果 |
|---|---|
| 8 条自动化创建并回读 | ✅ 名称 / rrule / cwds / status 全部正确 |
| 排程时点换算 | ✅ 全部落在 2026-09-23（周三），时区为本地北京时间 |
| 解释器依赖完备性 | ✅ miniqmt venv：numpy/pandas/lightgbm/pyarrow/xtquant 全 OK |
| 工作目录可进入 | ✅ `D:\QuantLab\projects\Project_16_LightGBM股票大师`、`D:\Workspace` 均存在 |
| 冒烟测试 `is_trade_day.py` | ✅ 退出码 0，输出「2026-09-23 A股交易日: 是」 |
| 被调用脚本存在性 | ✅ 18 个项目脚本全部存在（create 前已逐个校验） |

---

## 六、后续待办

- [x] **收尾：更新 `全局控制台.md` 任务看板**（2026-09-23 已登记 T-20260923-001/002/003）。
- [x] **收尾：更新 `.qoder/repowiki/`**（2026-09-23 已写入 `部署与运维/环境搭建与部署配置.md`）。
- [ ] **今日（09-23）观察**：09:15 / 09:25 是否按时触发；15:05 / 15:45 / 15:50 是否按时触发；飞书是否收到 WorkBuddy 发来的推送（标题带 `[WB]` 前缀即为 WorkBuddy 侧）。
- [ ] 确认 ③ 的取数降级链在本环境实测可用（这是改造幅度最大的一条，首次运行最可能部分失败）。
- [ ] 连续观察 ≥3 个交易日后，逐个暂停 TRAE 侧对应任务；三条下单任务的顺序严格按 §二。
- [ ] 提供 #12 夸克的两个分享链接 + 决定是否移植 `quarkclouddrive` skill（移植需先过 Skills 安全审计）。
- [ ] 复核 `Quant_P16_NightlyCheck_0100` 于 2026-09-22 01:00 退出码 1 的原因。

---

## 七、当日追加实施（2026-09-23 上午）

### 7.1 lark-cli 路径加固（T-20260923-002）

**动机**：`LARK_CLI` 硬编码在 TRAE 插件目录下（`...\.trae-cn\plugins\trae-remote-official\lark\1.0.5\bin\lark-cli.exe`），TRAE 一旦升级或卸载，**全部**飞书推送立即中断。该风险已实际发生过一次（版本 1.0.4 → 1.0.5 迁移，文档未同步）。

| 步骤 | 动作 | 结果 |
|---|---|---|
| 1 | 复制 exe 至独立目录 `C:\Users\Administrator\bin\lark-cli.exe` | 49,432,064 字节 |
| 2 | MD5 一致性校验 | ✅ `ff86b854d819ea9195306ff0a8a98063`（与源文件相同） |
| 3 | 中性目录独立运行 | ✅ `lark-cli version 1.0.94`，退出码 0 |
| 4 | 新路径直连真实推送 | ✅ `ok:true` / identity=bot / message_id 返回 |
| 5 | 三处配置改为「主路径 + 自动回退」 | ✅ `qmt_config.py` / `g2_config.py` / `g2_ens_config.py` |
| 6 | 回读解析结果 | ✅ 三处均解析为 `C:\Users\Administrator\bin\lark-cli.exe`，`os.path.exists=True` |

配置写法（三处一致）：

```python
_LARK_CLI_MAIN = r"C:\Users\Administrator\bin\lark-cli.exe"
_LARK_CLI_FALLBACK = r"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\1.0.5\bin\lark-cli.exe"
LARK_CLI = _LARK_CLI_MAIN if os.path.exists(_LARK_CLI_MAIN) else _LARK_CLI_FALLBACK
```

旧 TRAE 目录**保留未删**，作为兜底。`versions/G2-V1.0_20260908/` 与 `archive/` 下的历史快照**刻意未改**（保留历史原貌）。

### 7.2 推送来源标记 `LARK_SRC_TAG`（T-20260923-003）

**动机**：WorkBuddy 与 TRAE 两侧共用**同一 bot 应用（`cli_aade2524ddbd9bd6`）同一私聊**，重复推送在收件侧完全无法区分来源，导致「WorkBuddy 通道到底通没通」无法验证。

**实现**（单点出口 + 三处文本通道）：

| 文件 | 改动 |
|---|---|
| `qmt_card.py` | 新增 `src_tag()` / `apply_src_tag()`；在推送单点出口 `send_lark_card()` 对 `header.title` 打前缀 |
| `qmt_monitor.py` | `notify_feishu()` 文本推送前缀 |
| `qmt_bridge_client.py` | `notify_feishu()` 文本推送前缀（G2 换仓/对账链路） |
| `qmt_bridge_client_ens.py` | `notify_feishu()` 文本推送前缀（融合换仓/对账链路） |

设计要点：
- **未设 `LARK_SRC_TAG` 时返回空串 → 卡片与文本逐字节不变**，TRAE / Windows 侧零影响（这是可安全上线的前提）。
- 打标**幂等**：标题已含标记则不重复加；空标题、非 dict 输入均安全跳过。
- 仅作用于 `header.title`，不触碰 `subtitle` / 卡片体 / 按钮。

**验证记录**：

| 检查项 | 结果 |
|---|---|
| 4 个改动文件 `py_compile` | ✅ 全部通过 |
| 未设变量 | ✅ 标题保持原样（`G2 对账异常`） |
| 设 `LARK_SRC_TAG=[WB]` | ✅ 标题变为 `[WB] G2 对账异常` |
| 幂等性 | ✅ `[WB] 已带` 不重复加 |
| 空标题 / 非 dict 边界 | ✅ 无异常 |
| 端到端真实推送 | ✅ `[SEND] 成功 → alert(info)`，卡片标题带 `[WB]` 前缀 |
| 7 条自动化 prompt 更新 | ✅ 已追加「必须设 `LARK_SRC_TAG=[WB]`」条款 |

> 说明：`push_alert_card.py --no-send` 打印的是**打标前**的原始卡片（该分支在调用 `send_lark_card` 之前即 return），因此不能用它验证标记；验证须走函数级调用或真实推送。

### 7.3 当日两次真实推送说明

- `[路径加固验证] lark-cli 已迁移至 ...` —— 验证新路径可用（text 消息，无前缀，直连 CLI）。
- `[WB] 来源标记验证` —— 验证标记生效 + 项目脚本走新路径（interactive 卡片，带前缀）。

两条均为验证用途，非交易信号。

---
