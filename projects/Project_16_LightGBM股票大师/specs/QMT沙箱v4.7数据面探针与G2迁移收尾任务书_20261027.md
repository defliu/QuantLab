# 任务书：QMT 沙箱 v4.7 数据面探针 + G2 迁移收尾

- **日期**：2026-10-27
- **委托**：诚哥（经 Trae 复核 Qoder 复审报告后拍板，交由 Qoder 执行）
- **上游输入**：`results/QMT沙箱探针报告复审与miniQMT影响_20261027.md`（Qoder 自己的复审，§六建议已获采纳为本任务书 T1–T5）
- **本文件是唯一入口**：按 §一 顺序读完即可开工，不必再全库探索

---

## 〇、一句话任务

**用 v4.7 定向探针回答「P16 能否在国金客户端内自给自足产出信号」（数据面生死项），同时完成 G2 迁移 67014907 的快照重做与全部工程收尾。**

---

## 一、必读文件（按此顺序，全部在 D:\QuantLab 下）

| # | 文件 | 作用 |
|---|---|---|
| 1 | `projects/Project_16_LightGBM股票大师/results/QMT沙箱探针报告复审与miniQMT影响_20261027.md` | 任务源头（Qoder 复审，§二 7 项事实 + §六 建议） |
| 2 | `projects/Project_16_LightGBM股票大师/specs/QMT沙箱能力全量探针研究报告_20261026.md` | 权威研究报告（v4.6 已收口：行情 API 可用，55 OK / 25 FAIL） |
| 3 | `broker/qmt_sandbox_probe.py` | v4.6 探针源码（PROBE_TAG `20261027-03`），v4.7 以它为基底改 |
| 4 | `AGENTS.md` 第 120–141 行 | 沙箱红线 + 探针编写红线（v4.7 必须遵守，见本文件 §三 摘要） |
| 5 | `D:\国金QMT交易端模拟\python\_PyContextInfo.py` | C 上下文 API 真签名（行号：`get_market_data_ex` 181 / `set_universe` 69 / `get_full_tick` 293） |
| 6 | `.qoder/repowiki/knowledge/zh/.../Project_16 LightGBM 股票大师.../特殊配置与命令.md` | Qoder 自家知识卡（探针块 317–369 行有红线全文） |

---

## 二、任务清单（按优先级，T1/T2 并行可做）

### T1（P0·生死项）：v4.7 数据面定向探针

**为什么**：v4.6 只证明了 OHLCV 可取。P16 的 21 特征需要 PE/PB/流通市值/财务/板块，这些 API 在 `C.dir()` 里存在但**从未实调**。「客户端内自给自足」成立与否由本项决定，不由 HTTP POST 决定。

**要做的**：

1. 基于 `broker/qmt_sandbox_probe.py` 出 **v4.7 定向探针**（建议 `SEGMENT='DATA'` 或独立文件 `broker/qmt_sandbox_probe_v47_data.py`，PROBE_TAG `20261027-04`），新增一段 `[FIN]`，实调以下 C 上下文 API（先读 `_PyContextInfo.py` 确认签名，**全部用关键字传参**，吸取 v4.5 签名陷阱教训）：
   - `get_finance` / `get_financial_data`（财务字段）
   - `get_turnover_rate`（换手）
   - `get_float_caps`（流通市值）
   - `get_stock_list_in_sector`（板块成分，先取如 `沪深A股` 类板块）
   - `get_instrument_detail`（如存在，个股静态信息）
2. 样本：**20 只股票 × 最近 3 天**（从 D:/astock 挑含沪深两市的混合样本，建议直接用 P16 当期持仓/候选里的 20 只代码）。
3. 每个打印：API 名 + 股票 + 返回关键字段（如 pe/pb/流通市值/换手），**对照 D:/astock 同日数据算偏差**（对齐逻辑放沙箱外做：探针只负责把原始返回打印全，比对脚本在 QuantLab 内跑，不要在探针里读 CSV——沙箱内文件 IO 全拒）。
4. `get_market_data_ex` 补一组 `front_ratio`（前复权）与 D:/astock 复权价对齐——验证复权口径。

**验收**：每个 API 有实调输出（存在性 + 字段清单 + 20 只样本值）；产出对齐报告（偏差表 + 结论：哪些字段可用/口径一致/缺失需外挂）。

**已知风险**：若某 API 返回空/None，区分「沙箱拦截」vs「需要下载财务数据包」vs「字段名/参数错」三种可能，在报告里分别标注，不要一律判死刑。

### T2（快赢）：G2 迁移 67014907 的 L3 快照重做

**为什么**：今早 08:52:27 国金客户端 Formula 日志 `[P16G2I][INIT-SNAP-ERR] 快照账号戳 70180771 != 策略 67014907` → G2 内置版已转被动。账号戳不重做，G2 在新客户端永远不动。

**生成链（已核实，不要手改中间产物）**：

```
gen_g2_signal_snapshots.py（生成器）
  → strategy/_g2_signal_snapshots.py（ACCOUNT_STAMP="70180771"，SNAPSHOT_DATE="20261021"，头部标注「自动生成，禁止手改」）
  → build_p16_g2_selfcontained.py（构建脚本，第 29 行引用上面文件）
  → build/strategy_p16_g2_selfcontained.py（产物，第 205 行 ACCOUNT_STAMP，第 3619–3620 行 init 校验不等即 raise）
```

**要做的**：按 67014907 账户重生成快照（走生成器，改它的输入参数而非手改文件）；若 67014907 当前**空仓**（复审引证：最后已知状态 10-23 为零持仓、现金 10,081,509.64），**显式空仓起步**可能是更干净的选择——生成空快照 + 账号戳 67014907 + 当日 SNAPSHOT_DATE，两种方案都摆出来让诚哥选。重建后产物 BUILD_TAG 更新，交诚哥人工粘贴到国金客户端。

**验收**：新 build 在国金客户端跑起来 `INIT-SNAP` 无 ERR、`passive=False`、账号戳校验通过。

**同类问题**：ENS（`build/strategy_p16_ens_selfcontained.py`）与 V13（`build/strategy_p16_v13_risk.py:62`，`EXTERNAL_REMARK_BUY="planA_new_top"`）有相同账号戳/外部买单 remark 依赖——**只登记不施工**（ENS 去留本身未拍板；V13 的 `_late_bind_whitelist` 在 miniQMT 消失后永远绑不到票，属架构缺口，写入报告待拍板）。

### T3（收尾·必做）：工程收尾四件套

1. **撤探针**：提醒诚哥从国金客户端删除 QMT_SANDBOX_PROBE 策略（探针会随 bar 反复跑，Formula 日志 10-26 已 230 MB，10-27 至 11:24 已 18.7 MB）。客户端内策略文件加密读不到，删除只能诚哥在客户端 UI 手工做。
2. **删残留库**：`D:\QMT_POOL\` 下 **6 个**（不是复审写的 4 个）：`_probe4_1793009783 / _probe4_1793010229 / _probe4_1793011856 / _probe4_1793012280 / _probe4_1793068101 / _probe4_1793070440`（各 8192 字节，沙箱内 `os.remove` 被 `Foribdden fileIO` 拒，必须在客户端外删；保留 `_probe_persist.db`——它是跨重启证据）。
3. **git 提交**：报告（untracked）+ `AGENTS.md`（modified）+ `broker/qmt_sandbox_probe.py`（modified，git 里只有 v2/v3）+ 本任务书 + 复审报告，一次收口。提交前让诚哥确认。
4. **看板登记**：`全局控制台.md` T-20261026-001 行按实际结果更新（若 T1/T2 完成，登记结论）。

### T4（治理）：AGENTS 红线修订条款

AGENTS.md 第 127 行「**策略进程零外部交互**」（方案B 监控设计）与研究报告 §五 参考架构（HTTP 拉数据/HTTP POST 告警/subprocess）存在张力。**不改红线原文，补修订条款**——仿第 124 行 2026-10-19 xtquant 解禁先例的格式，写明：探针已实证的通道（网络 GET、subprocess stdout）在「沙箱内文件 IO 不可用」前提下作为过渡期例外允许评估，新策略默认仍走自闭环，例外须显式登记。措辞交诚哥过目。

### T5（可选·需单独拍板）：交易面实盘探针

实盘模式最小链路 `get_current_time → passorder 1 手 → get_trade_detail_data 反查 → cancel`。**涉及真实委托（模拟账户 67014907），未获诚哥明确拍板前不得执行**，只可先做「非交易时段 API 存在性」只读探测。

---

## 三、探针编写红线（v4.7 必须遵守，违者探针必挂或结论作废）

1. **目标客户端**：`D:\国金QMT交易端模拟`（账号 67014907，v2.1.33.0）。**内嵌 Python 3.6.8**，不随客户端版本升级而变。
2. **语法禁令**：禁 f-string、海象、match、新式类型注解；文件头 `# coding=gbk`；**输出字符串纯 ASCII**（中文进 GBK 策略文件会乱码/被拒）。
3. **主通道**：`print` + 每行 `sys.stdout.flush()`。日志落 `D:\国金QMT交易端模拟\userdata\log\XtClient_FormulaOutput_YYYYMMDD.log`，沙箱外读它做判定。
4. **签名陷阱（v4.5 教训）**：`C.get_market_data_ex` **首位参数是 `fields`**，股票列表必须 `stock_code=` 关键字传，位置传参会绑错位 → 返回空 dict 且无异常。所有 C API 先读 `_PyContextInfo.py` 确认签名再用关键字。
5. **高危隔离**：任何可能挂死的调用（文件 IO、tempfile 类）放 daemon 线程 + 超时兜底，超时记 HUNG 不吞整段。
6. **文件 IO 全拒**：`open/os.open/pathlib/makedirs` 一律 `PermissionError: Foribdden fileIO`（拼写就是单 b 双 d）；`os.remove` 也拒 → 测试库会残留，探针里别假装删掉了。
7. **`set_universe` 非必需**（返回 None，生产 G2 不调用）；`save_py_key/get_py_key` 是全局函数非 C 方法，get 返回 `(value, rc, msg)` 三元组，仅小数据（1MB 起静默丢）。
8. **部署方式**：写好后由诚哥**人工粘贴**到客户端策略编辑器运行（磁盘策略文件加密，无法脚本部署）；跑完提醒删除。
9. **上下文差异声明**：探针跑在模型交易/回测模式；实盘模式沙箱强度是否一致未知，报告里所有结论须带此限定。

---

## 四、纪律与交付

- **不动生产**：除 T2 的 build 产物（交诚哥粘贴）外，不改任何在跑策略；不下单（T5 除外且需拍板）；不 force push。
- **口径同步**：T1/T2 完成后，四处文档口径一致——研究报告（specs/）+ `AGENTS.md` + `.qoder/repowiki` 对应知识卡 + `全局控制台.md`。**不编造未跑的结果**（先例：v4.4 编号跳过就在版本表如实注明，不许虚构历史）。
- **报告落点**：v4.7 数据面对齐结论写入 `specs/QMT沙箱能力全量探针研究报告_20261026.md` 新章节（或续篇文件），G2 快照重做单独一节。
- **完成标志**：T1 对齐报告 + T2 新 build 验证通过 + T3 四件套 + T4 条款草稿，全部提交 git。

---

*任务书完。执行顺序建议：先 T2（半小时级快赢，解除 G2 被动）→ T1（探针写好交诚哥粘贴）→ T3/T4 随后收尾。*
