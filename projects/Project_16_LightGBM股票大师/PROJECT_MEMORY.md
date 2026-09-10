# Project\_16 项目记忆（自 trae\_workspace 迁移）

> 本文件由 `D:\trae_workspace` 工作区的项目记忆 `project_memory.md`（2026-08-25 迁移至 QuantLab）整理而来，用于在 QuantLab 工作区继续研发本项目时无缝继承历史上下文。
>
> **优先级说明**：本文件是历史研发记忆，**如与** **`D:\QuantLab\AGENTS.md`、`config/trading_config.yaml`** **或最新代码冲突，一律以 AGENTS.md / 配置 / 代码为准**。
>
> **账号分工（2026-08-25 用户确认，双账号并存、各跑各的）**：`67014907`（旧号，miniQMT/xtquant 委托，跑本项目 Project\_16 策略，`qmt_config.py` 的 `ACCOUNT_ID`）；`70180771`（新号，纯 QMT 端跑 ATR低波/价值小盘V2 等策略）。本文件的 `67014907` 绑定记录有效，非停用账号。
>
> 完整对话级记忆（按日期的 `session_memory_*.jsonl`）归档于 `D:\QuantLab\archive\_from_trae_workspace_20260825\memory_projects\trae_workspace\`。

## 项目背景

用户的主要工作是 **A 股量化交易策略开发与实盘运维**，核心资产位于 `D:\QuantLab`（研究开发中枢）、`D:\QMT_STRATEGIES`（生产工程）与 `F:\天翼云盘同步盘\Obsidian\量化知识库`（Obsidian 知识库）。

完整的主工作概况（工作区布局、实盘状态、研究焦点、已实证结论、方法论红线、文档速查）已沉淀在用户级记忆 `user_profile.md`，涉及本项目话题时先读该文件。

## 本项目工作区约定

本会话工作目录为 `d:\trae_workspace`（迁移前），用于承载通用任务产物：

* `D:\trae_workspace\scripts` — 程序代码、脚本

* `D:\trae_workspace\results` — 最终交付结果

* `D:\trae_workspace\temp` — 临时文件、中间产物

* `D:\trae_workspace\data` — 数据文件

量化相关任务一律以 `D:\QuantLab` / `D:\QMT_STRATEGIES` 为准，不要使用 C 盘临时目录。

## 常用入口（进入量化话题先读）

1. `D:\QuantLab\全局控制台.md` — 当前任务与项目状态
2. `D:\QuantLab\AGENTS.md` — 开发规则与红线
3. `D:\QuantLab\研究总览与路线图.md` — 全局（过去研究/踩坑/现在/未来）
4. `D:\QuantLab\全局复利与踩坑日志.md` — 踩坑库

## 硬约束

* 选股必须遵循年内涨幅>50%一票否决的规则

* 换手率<0.5%的股票直接排除

* 必须有确证性催化才能入选S级股票

* 9:30\~9:45为绝对禁止买入时段，主力诱多诱空高发期

* 14:50\~15:00禁止买入，收盘前买卖盘失真，次日不确定性高

* 株冶集团（600961）跌破-5%止损线28.64元时需无条件清仓

* 有研新材（600206）跌破-10%回撤线52元时需清仓剩余部分，当前先减仓50%

* 云南锗业（002428）跌破止损线98.67元时需止损

* 美新科技（301588）跌破-7%止损线30.03元时需立即清仓

* 模型必须每季度滚动重训，不能一训到底

* 短线预测中，财务质量因子（ROE/毛利率等）对次日涨跌基本无效，应剔除

* 策略持仓参与9:45评分对比，不在Top 2的卖出换新票

* 非策略持仓需在开盘后（9:31）立即清仓，不等待9:45

* 清仓脚本必须使用`--auto-keep`参数，自动保留策略持仓

* 9:45换仓任务仅处理策略持仓，与开盘清仓任务互不干扰

* 策略资金池固定为10万本金，收益滚动，亏损不补资金

* 委托守护机制：委托后60秒查成交，未成交则撤单并按最新价重试，最多5次，涨跌停跳过

* 盯盘任务需使用QMT客户端内置策略脚本，通过`handlebar`每tick回调触发，脱离Windows定时任务

* 资金流数据接入优先选择Parquet格式，存放于`E:/astock/moneyflow/`，需开发`moneyflow_ingest.py`归一化入库

* v4面板构建需新增6-8个资金流特征，包括主力净流入占比、5/20日滚动和、超大单占比等，替代现有量比代理

* 超配减仓逻辑需按目标股数对齐，目标股数=目标市值÷现价（向下取整到整手），T+1锁定时今日不动作

* 买入资金分配需按策略资金池×95%÷目标数等权分配，已持有的不足部分补仓

* 多数据源优先级（2026-08-27 更新，详见「2026-08-27/28 会话沉淀」数据源现状）：每日 agent 任务以 MCP 数据源为主（mcp\_tdx / iFind / 悟道 / 腾讯 / 东财），脚本直连（xtdata / 新浪 / 东财HTTP / 同花顺881）为无人值守回退

* 盯盘任务需处理UnicodeEncodeError，强制stdout/stderr用UTF-8编码，控制台打印替换emoji为ASCII

* 300684.SZ需在T+1可卖后自动减仓至目标市值约4.8万

* 601865.SH卖出未成交需在收盘后核查委托明细

* 002237.SZ需补仓至目标股数3200股

* 委托守护需接入`order_guard.py`，覆盖卖出(PK出局)、减仓(超配)、买入/补仓操作

* 风控策略需复刻止损-7%、止盈+15%、移动止盈8%规则，持仓成本从`qmt_trade_log.csv`同步

* v4面板重训需复用R2全量重建流程，研发电脑约1小时，对比v3\_enh是否接近+0.3%判定线

* 资金流数据周更新，无法替代盘中TDX实时通道，仅用于回测/离线因子

* 大盘资金流可作为风控二次确认，叠加全市场主力净流出信号

* 行业/概念板块资金流可用于F5评分，替换相对动量代理

* 策略持仓变动后需同步更新`HOLDINGS`，可读取本地`qmt_trade_log.csv`

* QMT策略需绑定账号`67014907`，使用`passorder`原生下单，启用`order_callback`/`deal_callback`回报（**双账号并存（见本文件头部账号分工说明）**：67014907 = 本 Project\_16 绑定账号；70180771 = 新号跑 ATR/V2 等纯 QMT 策略，两者各跑各的、禁止混用，引用错号会废单）

* 非交易时段需注意QMT客户端登录状态，确保盯盘任务正常运行

* 修复后的盯盘任务需在13:30/14:00/14:30验证触发情况

* 资金池基准为`strategy_capital.json`的`capital`字段，缺失回退10万

* 持仓用volume而非can\_use，避免T+1锁定持仓被过滤

* dry-run需与live逻辑一致，预览每只票动作（补仓/减仓/达标）

* 定时任务配置需更新多数据源优先级和资金池基准

* 策略脚本需添加注释记录修复内容（如bug3超配减仓逻辑）

* 委托守护需在飞书汇报包含结果状态（FILLED/LIMIT\_SKIP等）

* 资金流数据接入需先核对样品字段，确认质量与格式

* 新增`data_sources.py`模块统一封装多数据源获取逻辑

* `review_full.py`需导入`data_sources`模块，处理数据缺失回退

* `rebalance_daily.py`需修复资金分配逻辑，按目标持仓数等权分配

* `qmt_monitor.py`需替换emoji为ASCII，修复控制台编码错误

* `order_guard.py`需实现委托守护流程，处理撤单重试和涨跌停判断

* `risk_guard.py`需实现QMT内置风控策略，包含init/handlebar/order\_callback/deal\_callback

* `TODO_PENDING.md`需记录QMT内置风控策略和v4资金流面板待办任务

## 工程惯例

* 项目结构包含README.md、factor\_framework.md、reviews、selections和results目录

* 选股结果需记录排名、股票名称、代码、评分、等级、核心逻辑和3必选全中情况

* 盘后验证需填写results目录下的验证模板，验证4个假设后优化因子框架

* 盯盘日志文件需包含股票数据表、checklist逐项检查结果、判定结果及原因

* 写文件必须使用"临时文件+os.replace原子替换"，严禁直接open截断原文件

* QMT源文件编码为GBK，部署后需重拷覆盖密文并重启

* 定时任务按cron触发，独立于开发会话状态

* 开盘实时复核任务（9:45）取评分≥58分的前2名作为换仓目标

* 换仓逻辑：先卖持仓中不在目标Top 2的票（仅卖今日可卖部分），再买入目标中未持有的票（等权，预算=可用资金95%）

* 大盘风控开关：沪深300跌>1.5%时空仓（T=0），-1.5%\~-1.0%时半仓（T=1），>-1.0%时正常T=2

* miniQMT客户端必须在开市前启动并登录，否则交易通道连接失败

## 经验教训

* 零催化股的成功率可能低于50%，需在选股时重点关注催化因素

* 位置过跌的股票可能拖累整体表现，需谨慎选择

* 高开>+3%的股票应放弃买入，存在高位利好后追高风险

* 开盘15分钟内放巨量杀跌（量比>5+跌>-3%）的股票应放弃，表明有资金抢跑

* 创业板次新股票一旦破位，流动性枯竭速度快，越拖越难出

* 短线均值回归效应显著：放量、涨幅高、RSI超买、站上均线的股票次日更容易回落

* 公开的现成量化策略很难直接赚钱，重要的是开发与校验策略的能力

* koa-connect wrapper caused ctx leaks, so native rewrite is required

* 开发会话在9:25-9:45期间应避免修改`review_full.py`/`rebalance_daily.py`等关键脚本，防止任务读取未完成版本

* 环境变量中的占位符/过期值会导致API调用失败，需检查并更新真实有效密钥

* 资金池使用账户全量资金会导致仓位极度失衡，需严格按策略资金池分配

* 超配减仓按市值差额取整会导致剩余市值仍超配，需按目标股数对齐

* 盯盘任务因控制台编码错误导致崩溃，需强制UTF-8编码并替换emoji

* 定时快照盯盘可能错过止损/止盈时机，需改为连续盯盘或提高快照频率

* 多数据源未配置优先级会导致数据获取失败，需明确优先级并自动回退（8/27 起 MCP 数据源为主、脚本直连回退，见「数据源现状」）

* 委托未成交未及时处理会导致持仓不符预期，需实现委托守护机制

* 策略持仓成本未同步会导致风控触发错误，需从交易日志同步成本数据

* 资金流数据周更新无法满足日内实时需求，需结合实时API补充

* QMT策略未处理涨跌停会导致无效委托，需在下单前判断并跳过

* T+1锁定持仓未考虑会导致重复买入或无法减仓，需准确获取可卖数量

## 2026-08-27/28 会话沉淀（P0 修复 + 资金池口径 + 风控规则证伪）

> 本节为 2026-08-27\~28 对账审计后的收口沉淀；对应看板 `T-20260827-004` / `T-20260828-001`。

### 新硬约束 / 规则（已落地代码）

* **资金池硬校验**（`rebalance_daily.py`）：买入单笔 ≤ 资金池×95%÷N、当日累计 ≤ 资金池×95%，超限拒绝并打 `POOL_BLOCK` 报警（fail-loud），杜绝 8/24 用账户全量资金下单的 bug 复现。

* **资金池口径（策略真实）**：`strategy_capital.py` / `reconcile_trades.py` 用 `EXCLUDE_CODES={"300684.SZ"}`（8/24 超买标的）剔除其盈亏，资金池 = 初始 10万 + 策略真实已实现 + 策略真实持仓浮盈（不滚入超买利润）。每日对账报告双口径并列（全账户 vs 策略资金池）。

* **持仓成本权威源** = 成交记录 FIFO 含费均价（`qmt_monitor.py`/`strategy_capital.py` 已统一）；**QMT** **`open_price`** **对个别票异常不可信**（300684 曾显示 1002.97 / -40.92），只能作兜底。

* **每日盘后对账**：`reconcile_trades.py` 已并入 `run_scheduled.ps1` daily（16:30），持仓不一致或异常时退出码非 0 并告警；成交记录缺漏用 `score=RECON_BACKFILL` 补记。

* **持仓单票上限** = 资金池×95%÷N（当前 N=2，单票约 47k）。

* **定时任务必须指向** **`D:\QuantLab\projects\Project_16_LightGBM股票大师\run_scheduled.ps1`**：9 个 Quant\_\* 任务已于 2026-08-28 从残留旧路径 `D:\trae_workspace\...` 改指向新路径；迁移后勿再引用旧副本。

* **本策略为 N=10（约两周）换仓周期，非每日调仓**：无 09:35 定时调仓任务；换仓由人工确认后执行（`rebalance_daily.py --live` 或先出方案批准再下）。

### 经验教训（本次新增）

* **8/24 超买 939万 = 资金分配红线**：账户全量资金买入 300684（远超 10万 资金池）。账面 +47万 几乎 100% 来自该单票运气（中际旭创入股+液冷+中报催化），**剔除超买后策略真实选股收益≈0（+0.26%）** → 收益核算必须剥离超买/异常仓位，看真实策略口径。

* **成交记录必须完整**：8/25 有 7,600 股 300684 卖出未入账（QMT `yesterday_volume` 锚点推导锁定），导致 FIFO 对账持仓虚高 → 每次成交即写 `qmt_trade_log.csv`，缺漏及时补记。

* **未成交挂单不得当成交记录**：601865 8/24 @10.55 未成交被记成成交，reconcile 异常检测（策略代码卖出>买入→报警）捕获；历史误记需移除或标注。

* **收盘后撤单不生效**：8/28 撤 300684 全卖 700 挂单（order 1082173605）多次返回 0 但状态仍"已报"，只能在次日开盘处理；**收盘后不挂次日可能需取消的单**，误挂用"补反向单"对冲净持仓。

* **项目迁移后必须同步更新定时任务路径**，否则模拟盘持续跑旧代码/旧数据（旧副本成交记录停在 8/26）。

* **风控规则"连续N日跑输大盘+主力持续净流出→减仓"回测证伪**（2020-2026，746万行，N/M∈{2,3,5}）：触发组 vs 基线前向收益差极小且方向不稳；对"近期强势（近似持仓）"子集触发组 fwd5/10 反而略好于基线，按此减仓会卖飞而非避损；分年 delta 符号不一致无稳定效应。**结论：不加入正式风控**（正式版未动）；留观察模式 `research/trim_rule_paper.py`（只记录不卖出）。这再次印证"走势+资金"型信号无独立 alpha（与 P12/P17/P18 互证）。产物：`research/trim_rule_backtest.py` + `research/results/trim_rule_backtest_report.md`。

* **feature\_panel\_v3 停更（8/14→8/27 修复，T-20260828-003）**：V1.1 deploy\_predict 的模型分曾冻结在 8/14 特征（8/26-8/28 选股同票 model\_prob 完全相同）。**根因**：周更 retrain 只跑 `build_features_v2.py`（写 v2 面板），v3 面板靠 WORKFLOW\_DEPLOY.md 一行**手工**切片命令，不在自动化里→8/20 后停更；且主库 E:/astock daily 只到 8/21（增量 8/22-8/28 在主库外）。**修复**：新增 `refresh_panel_v3.py`（合并主库+增量→重建 v2→切片 v3，慢变量前向填充），`run_scheduled.ps1` retrain 改调它（周一 17:00 自动刷新 v3）。**教训：任何"生产文件靠手工命令生成"的环节都要固化进定时任务，否则必然停更；选股文件里同票 model\_prob 跨日完全一致=面板/特征冻结的判据。** 附注：8/28 盘中不完整未纳入；模型未重训（v3 面板变了，重训另议）。

### 前向验证（进行中）

* `build_g2_daily.py`：V2.0 g2 43 特征当日化（量价 21 + 基础 14 + g2 资金/龙虎榜/研报/北向/板块 10），输出 `data_live/g2_latest_features.parquet`。

* `paper_forward_daily` 定时（工作日 16:45）累积 V2.0 g2 前向候选至 `paper_forward_live.csv`；样本 N>30（约 2-3 个月）后再判（超额>0 / 胜率>55%）。

* **2026-08-28 管道修复（T-20260828-002）**：前向样本曾停更在 8/25，根因=8/26-8/27 的 16:30 数据更新任务仍跑旧路径 `xtdata_update.py`，新路径 `data_live/incremental_daily.parquet` 停在 8/25（旧路径已到 8/27）→ g2 管道只读到 8/25 重复生成候选。已修：新路径增量更新到 8/28 + `paper_forward_live.csv` 去重 + 回补 8/26/8/27 两日样本；现 9 个交易日（8/17-8/27）。**教训：迁移后 16:30 数据更新任务必须同时切新路径，否则增量数据新旧分家、前向样本停更。** 今日起 16:30（xtdata\_update）+ 16:45（paper\_forward）均已走新路径。

* 8/26 该定时曾失败一次（0x800710E0，23:05 触发，调度环境一次性问题）；8/27 起 LastTaskResult=0。

* **2026-09-04 二次管道修复（T-20260904-009）：backfill 连续 4 天回填 0 笔却 exit 0，前向验证实际有效样本 N=0**：

  * **根因（数据源分裂）**：`paper_forward.py` 的 backfill 只依赖 `DC.read_main_daily`（MAIN_DAILY + Updatedata **人工周更**目录），**主库停在 8/28**；而 g2 选股侧（`build_g2_daily.py`/`deploy_predict_g2.py`）用的是 `data_live/incremental_daily.parquet`（每日自动更新，已到 9/4）。结果：**候选每天照常产生、收益一笔也算不出**——63 笔候选回填 0 笔（45 笔因主库无晚于候选日的交易日、18 笔因持有期窗口越界），且日志照打 `[...] OK`，连续 4 天无人察觉。此前 PROJECT_MEMORY 记的"9 个交易日样本"是**候选数**而非**已回填数**，属记账口径误导。
  * **教训（与 8/28 那次同源，必须固化）**：**任何"两个数据源各管一段"的管道，一旦生成侧与核算侧不同源，必然出现"能出信号、无法验证"的死局**；判据是「候选日期最大值 > 收益数据源最新日」，应作为每日硬断言。
  * **已修**：①`paper_forward.py` 新增 `load_open_panel()`：主库 + `data_live/incremental_daily.parquet` 合并（增量优先），并打印「主库最新 / 增量最新 / 合并后」三行便于一眼定位；②数据源停更（合并后最新交易日距今 >5 自然日）**exit 2**，`paper_forward_daily.ps1` 捕获并转发，使计划任务 LastTaskResult 非 0 → fail-loud；③写回前按 (date,code) 去重（此前 9/4 出现 2 行重复，会重复计权）；④已回填的 ret 不因数据源抖动回退；⑤新增 `forward_stats.py`（**只读**）按「同窗口全市场等权」逐笔配对算超额（与回测 `market_daily` 横截面等权同源），输出 TOP2（实盘口径）/ 全部两档，报告 `data/real/forward_stats_YYYYMMDD.md`，已接入 16:45 定时。
  * **修复后首批结果（8 笔 = 8/17~8/20 各 2 只，N=10 open→open）**：绝对 **+1.110%** vs 同窗口全市场等权 **+1.643%** → **超额 -0.533%，t=-0.24，超额胜率 62.5%**。逐笔：8/17 −7.73%/+2.04%、8/18 −6.02%/−8.82%、8/19 +3.08%/+3.14%、8/20 +8.75%/+1.29%（超额口径）。**同日两票高度同向**（8/19 两只 +6.35%/+6.41%），ANOVA 粗估组内相关 ICC ρ≈0.68 → **有效独立观测约 4.8 个，远小于 8 笔**（ρ 估计仅基于 4 组配对，误差极大，仅取数量级）。**结论：样本不足，不作任何判定；G2 不得据此 promote 或加仓。** 约 **9/15** 达 30 笔（TOP2 口径，2 笔/交易日）；若按 30 个独立观测计约需至 **10 月中**。
  * **次生缺陷（T-20260904-010，待拍板）**：`deploy_predict_g2.py` 把 Top10 `picks` 全量写 live，而实盘 `g2_config.py:30 TOP_N=2` → 9/2 起候选口径由 2 只/天变 10 只/天（**中途变更且无人察觉**）。已加 `rank` 列（1..N，按 total_new 降序），`forward_stats.py` 双轨输出；待拍板主判定用 TOP2 子集（对齐实盘、累积慢 5 倍）还是 Top10 全集。

### 评分卡参数寻优（2026-08-28，T-20260828-004）

* **背景**：用户质疑 F1-F6 权重与 F2/F5/F6 阶梯阈值"拍脑门定"。核查确认：LightGBM 超参（train\_optuna.py Optuna）、红线 58→60/N=5/10、止损止盈均做过寻优，但 **SC\_WEIGHTS 与阶梯阈值从未寻优**（继承 Project\_15 手工值）。

* **产物**：`optimize_scorecard.py`（复用 scan\_rotate\_cost\_real 可执行引擎，walk-forward IS 2024-07~~2025-12 / OOS 2026-01~~08，固定红线58/TOP2/N10/滑点0.1%）+ `data/real/scorecard_optim_report.md`。

* **结论（稳健性检查为准）**：①权重寻优**稳健有效**（IS 前10 在 OOS 10/10 超基线，均值 +0.18% 日超额 vs 基线 +0.05%）；②F5/F6 阈值寻优**稳健有效**（10/10）；③**F2 资金阶梯阈值寻优是负优化（OOS 0/10 超基线）→ 生产默认 F2 阈值保持不动**。生产默认权重 F1=0.25 偏优 F1/F4、压制 F5 确实不优。

### V1.x 卖出规则变更（2026-09-05，T-20260904-001 已实施；版本归因 2026-09-06 修正：PK_OUT 非 V1.3 引入）

* **版本归因澄清（2026-09-06）**：PK_OUT 从 **V1.0 起（git 2634f5d，2026-08-22）就存在于 `rebalance_daily.py`**，是 V1.x 系列长期存在的规则；V1.3（2026-08-31）仅模型换版（6694树/27特征，VERSIONS.md 登记「评分卡/双轨/红线/TOP 均不变」），未改卖出规则。WB 复盘标题「V1.3 的卖出条件比回测多 PK_OUT」属归因不精确，实为「V1.x 自 V1.0 起就存在」。ablate 报告/回测引擎注释已同步修正。
* **PK_OUT（掉出 top2 即卖）已从 `rebalance_daily.py` 删除**，改为「满 HOLD_DAYS=5 交易日到期才卖」的到期制（ablation 证明 PK_OUT 在 N=3/5/10 各吃掉 −0.11/−0.23/−0.26pp 日超额，且把平均持有压到 ~1.2 日）。实现：sell_plan 仅对「掉出 top2 且 `_trading_days_between(上次BUY日,今日)>=HOLD_DAYS`」的持仓生成（reason=`MATURE`，remark=`planA_mature`）；仍在 top2 的持仓无论持有多久都保留（避免同日卖买抖动）；无买入记录者视为已满期。
* **重要边界**：删 PK_OUT 仅止血（回测 OFF 组日超额仍 −0.26%~−0.003%，全负/近零），非盈利动作；V1.x 真实 viability 仍以前向实盘样本 + G2 干净跑完一轮 N=10 为准。改动**未提交**（`rebalance_daily.py.bak_20260905_094409` 为实施前备份），待下个交易日实盘复盘确认后再 commit。
* **定时任务同步（2026-09-06 核对+修正）**：①09:45 开盘实时复核任务（15868a74）调用 `rebalance_daily.py --date --top T --live`，**不传 --hold-days → 自动用脚本默认 HOLD_DAYS=5**，无需改参数；message 已补充「卖出=到期制 MATURE（PK_OUT 已删）」说明，防执行 agent 按旧逻辑理解。②10:05 兜底 `rebalance_daily_guard.ps1` 标记2 原用 `SELL reason in (PK_OUT,TRIM_OVR,POOL_ADJ)` 识别换仓成交——**删 PK_OUT 后新卖出 reason=MATURE 未被识别，兜底会误判「09:45 未执行」→ 10:05 重复换仓**，已改为 `(MATURE,TRIM_OVR,POOL_ADJ,PK_OUT)`（保留历史兼容）。③其余任务（09:15候选/09:25集合竞价/11:35午休/15:40盘后/夜间检修/G2换仓对账）均不依赖卖出 reason 语义，无需改；G2 侧 HOLD_DAYS=10 独立不受影响。
* 防复发：不要再给 V1.x 加「排名掉出即卖」类日频翻转规则；任何新卖出规则须先跑 `ablate_pkout.py` 开关对照（PK_OUT ablation 模板）确认 ≤0 拖累。

* **推荐组合（研究结论，未落盘）**：权重 F1=0.17/F2=0.26/F3=0.22/F4=0.07/F5=0.17/F6=0.12；F5 阈值 >4/1.5/0.5/-1；F6 阈值 12-25/3-40；F2 保持默认。推荐组合 OOS 日超额 +0.22%（基线 +0.05%）、回撤 -18.5%（基线 -20.8%）。

* **落盘纪律**：本次未修改 review\_full.py / deploy\_predict.py / scorecard\_real.py；若采纳需人工确认，且先以推荐组合跑前向纸面验证 N>30 对照后再落盘（OOS 仅 7.5 个月样本有限）。

### 面板重建 + 模型 Promote（2026-08-28，T-20260828-005）

* **面板**：feature\_panel\_v3.parquet 重建至 **2026-08-27**（4,776,117 行/4306 只）；增量到 8/28（盘中不完整未纳入，今晚 16:30 刷新后入下轮）。

* **Promote 已执行（登记 V1.2）**：候选 `lgb_model_v3_retrain_20260828.txt`（2837树/27特征，新面板训练）→ 正式 `lgb_model_v3.txt`（`promote_model.py` 校验 27 特征一致 + 备份旧模型 `versions/models/lgb_model_v3_pre_promote_20260828_143151.txt` 3676树 + 重设只读）。

* **决策依据**：①正式模型训练于旧面板(8/14 冻结版)，生产现喂新面板(8/27)，存在训练/推理分布不一致——必须 promote 新面板训练的模型；②新面板测试集(2024-07\~2026-08-27, 149.7万行)横向对比：候选 test IC **0.0394 > 正式 0.0380**、ICIR 持平 0.376、acc 0.738 > 0.736，无性能回退。

* **冒烟验证**：deploy\_predict 加载 OK，8/27 面板 Top3=600272/300785/300996（选股已随新面板/新模型更新，非旧 300684/000737）。

* **教训固化**：面板重建后**必须同步重训 + promote 模型**，否则"新面板喂旧模型"=训练/推理分布不一致，选股分不可信（与 8/14 停更同源：模型与数据必须同版联动）。

* **自动检查已落地（T-20260828-005）**：新增 `verify_model_panel_sync.py`（读 `data/model_panel_binding.json`，比较面板最新日 vs 正式模型绑定日，不一致 exit 1 fail-loud）；`promote_model.py` 提升时自动写绑定记录（模型 mtime/树数/面板日期/promote\_time）；`run_scheduled.ps1` 的 daily（盘后）与 retrain（周更后）模式已接线——daily 若发现"面板已更新但正式模型未 promote"当场告警，retrain 后提示"请 promote 今日候选"。正/反向测试均通过（一致 exit 0 / 旧绑定 8/14 时 exit 1）。

### 数据源现状（2026-08-27 更新，MCP 为主 + 脚本直连为辅）

* **每日 agent 任务（9:25/9:45/11:35/15:40）已切 MCP 数据源为主**（实证：`data/holdings_daily_20260827.md` / `holdings_daily_20260828.md` / `selections/20260827_selection_full.md`）：

  * **mcp\_tdx**（通达信）＝基础行情；**iFind**（同花顺）＝主力资金（日频 `get_stock_performance`）；**悟道 mcp\_wudao**＝公告/龙虎榜/涨停梯队/热榜/题材/盘中主力/指数；**腾讯财经 API**＝量比/换手/PE/PB/涨跌停价；**东财 em**＝龙虎榜全自动（datacenter）。

* **无人值守脚本管道（16:30 daily/盯盘/重训）走脚本直连回退**：miniQMT xtdata（每日增量 OHLCV，`update_meta.json` source 实证）＋ 新浪资金流（g2 F2）＋ 东财 datacenter/reportapi（龙虎榜/研报）＋ 同花顺881成分自算（F5）。

* `data_sources.py` 的 `SOURCE_PRIORITY`（miniqmt→tencent→tdx）仅剩 V1.1 `review_full.py` 盘中兜底在用；注意与旧硬约束「miniQMT→TDX→腾讯」顺序有出入，以代码为准。

* 其余已配置 MCP（TuShare / 盈米 / 东财Choice mx-ds / agent-earth / earnings-interpretation 等）按研究/临时任务按需调用，未必进每日管道。

* 实测不可用源（2026-08-25）：东财 push2/push2his 资金流、腾讯 ff\_ 主力资金、同花顺板块实时接口（401 需登录）、东财 emappdata 人气榜，勿再引用。

### 数据源稳定性台账（2026-08-31，selection\_full 来源标注 + tdx\_review errors 实证）

**每日实际生效组合（08-24\~08-31）**：08-24 TDX 字段异常(F3=0) → 08-25 `tencent+em`（TDX 掉线）→ 08-26 `mcp_tdx+tencent+em` → 08-27 `mcp_tdx+tencent+ifind+em` → 08-28 `tdx_quotes+mx`（TDX 工具名漂移）→ 08-31 `tencent+ifind`（**mcp\_tdx 503 不可达**，tdx\_review errors 实锤）。

**稳定性结论**：①**腾讯 API = 最稳**（5 日全勤零失败，08-31 TDX 挂掉时独挑基础行情大梁）；②**TDX = 最不稳**（6 日 3 日异常/掉线，08-30 恢复一天 08-31 又 503；工具名 mcp\_tdx↔tdx\_quotes 漂移）；③**iFind = 扩展指标最稳第二顺位**（08-31 独立扛起主力净流入/板块涨幅/公告催化，主力资金为前一日口径）；④东财 em/mx 稳定但非主力依赖。

**v5 优化已落地（2026-08-31，TraeWork 9:25/9:45 任务指令 +** **`MCP数据源配置说明书.md`** **第一章 v5 + 第十节编排）**：按职责分组——①基础行情锚定腾讯（最稳），mcp\_tdx 增强覆盖；②扩展指标 F2/F3/F5 固定 mcp\_tdx→iFind→东财mx→东财curl；③冗余位 full-link/独立TDX/miniQMT 不动。TDX 系不可达绝不阻塞，腾讯+iFind 兜底。大盘风控指数源优先腾讯 API 沪深300，次选 `tdx_get_index_quote("000300")`。

### 候选预生成方案（2026-08-31 落地，与 9:25 解耦）

* **背景**：08-31 9:25 任务因大模型(LLM)环节卡住未跑完，9:45 被迫现场 deploy\_predict 重算（09:46→09:52→09:55 全流程约 10 分钟）。`deploy_predict.py` 是确定性本地推理（LightGBM 模型 + 面板特征），不依赖行情/LLM，完全可提前跑。

* **落地**：新增 TraeWork 任务「候选预生成(项目16)」09:15（幂等：面板最新日 `D_model_top10.csv` 已存在则跳过），跑 `deploy_predict.py --model v3 --top-k 10`；9:45 任务检查点改为优先用预生成候选，缺失才现场 deploy 兜底（历史行为不变）。

* **正确性论证**：候选集（top10）由 combo=0.6×模型分+0.4×面板评分卡决定（全离线，9:25 与 9:45 不变）；候选内最终排名由 9:45 review\_full 实时 F2/F5（权重 30%）复核重排——**预筛缓存"候选是谁"，实时复核"谁先谁后"**，价格波动由第二层兜住。

* **F3 催化缓存（2026-08-31 追加）**：9:25 任务 3.5 步读 v3 候选 top10 采集 F3（新闻/公告）写 `data/cache/review_<date>.json`；`review_full._read_catalyst_cache` 实时缺失时兜底（显式 0 不覆盖，来源标 `+cache`，dry-run 验证补 1 只排名不变）。**F2/F5 不缓存**：F5 板块涨幅分档敏感（跨档差 3 分=top 内相邻换位），9:25 与 9:45 差异 0.5-2%（若用 iFind 昨日口径则 2-4% 不可忽略）；F2 主力资金分钟级必须实时。

* **F2 资金源修复（2026-08-31，T-20260831-003，实盘失真案例）**：300456 虚高 TOP1——8/31 TDX 503 降级 iFind 前一日口径，用「8/28 主力净流入 +5.26 亿」打出 F2=10（量比 6.42），实际东财 8/28 = **-2.79 亿**、8/31 当日 = **-1.25 亿**，方向完全相反；真实 F2 应 1 分、总分 86→68、跌出 TOP3。**教训**：iFind 主力资金字段与东财口径严重不一致且滞后一日，TDX 缺席时不可作为 F2 主源。**修复**：①任务指令 v6——F2 主力资金**当日口径** mcp\_tdx→悟道 capital\_flow（东财当日四档）→东财mx→东财curl，iFind 仅兜底且必须写 `main_net_inflow_date`；②`review_full._valid_main_inflow` 时效校验——复核数据带日期戳且非当日 → F2 按缺失 5 分，绝不用旧数据打高分（dry-run 10 只全拦截验证）。9:25/9:45 指令 + `MCP数据源配置说明书.md` v6 同步。

* **新浪智研 sina-finance 接入（2026-08-31，T-20260831-004）**：token 实测有效（API 直连 + MCP streamable-http 双通），75 工具，**收费源**。**实测速度 \~60-80ms**（行情 globalStockQuoteRealtime / 新闻 stockNewsSearch / 估值 cnStockValuationDetail 三接口多轮采样，亚 100ms 顶级档，接近 mcp\_tdx 40ms 且预期更稳）。**A 股无个股主力资金流接口**（仅港股/美股有），**不参与 F2**。定位 **F3 催化 / F5 板块第二顺位**（mcp\_tdx 之后）：F3 用 `stockNewsSearch`（个股新闻实测质量高：当日新闻+概念+预警），F5 用 `cnMarketStrongSectors/cnStockLianBC/cnVirtualSectorRanking`，另覆盖估值 `cnStockValuationDetail`/北向 `cnStockConnectHoldings`/融资融券 `cnStockTradingMarginList`/行情 `globalStockQuoteRealtime`。已接入项目 `.mcp.json`（**含 token，已 git rm --cached + gitignore，未 commit**）。9:25/9:45 指令 + 配置说明书 v6 已把新浪列为 F3/F5 第二顺位。API 直连：`https://mcp.finance.sina.com.cn/api-call/<code>?params` + header `X-Auth-Token`。

* **盘前关键指标交叉验证（2026-08-31，T-20260831-005）**：9:25 任务 3.6 步双源对拍 F2 资金/行情/F5 板块写 `data/cache/crosscheck_<date>.json`；`review_full._read_crosscheck` 对不一致候选打 `[交叉验证]` 预警 + selection\_full 标注（只预警不阻塞），dry-run 验证 300456 触发 F2 资金预警正确；单元测试 `research/tests/test_crosscheck.py` 16/16 PASS。

* **每日刷新面板落地（2026-09-01，T-20260901-001）**：此前面板只在周更重训时更新（refresh\_panel\_v3 供 run\_scheduled.ps1 retrain 模式），周中面板落后（9/1 仍用 8/28，8/31 增量已入库未合并）。已改：① `run_scheduled.ps1` daily 模式在 merge\_live\_features 后、deploy\_predict 前加 `refresh_panel_v3.py`（每天增量入库后刷新面板到最新交易日，约 10 分钟，次日 09:15 候选自动用最新数据）；② `verify_model_panel_sync.py` 面板>绑定差异放宽到 **≤7 自然日**不告警（每日刷新正常领先，周更 promote 归零），超过才告警要求重训。**dtype bug 修复**：pandas 2.2 日期键 `merge_asof` 报 `datetime64[ns] vs [us]` 不匹配——`build_features_v2.py` 4 处日期键统一 `astype("datetime64[ns]")`（df trade\_date / fin ann\_date / event\_merge ev\_key / dv ann\_date）。验证：面板刷新到 8/31 成功，verify exit=0 一致。注意：模型 V1.3 训练于 8/28 面板、每日喂新面板为 1 天增量差异可接受，周更 promote 对齐。

* **悟道改直连客户端（2026-09-03，T-20260903-001，复盘** **`悟道方案复盘_20260903.html`** **落地修正）**：**排查结论**——① 报告推断的 `/api/openclaw` REST 路径实测 **404 不存在**（报告未实测的推断值）；② 悟道真实端点为 **`https://stock.quicktiny.cn/api/mcp`（JSON-RPC POST + Bearer key）**，实测 tools/list 65 工具、market\_overview/intraday\_main\_flow/dragon\_tiger 全部调通；③ **key 盘中可用**（13:12/15:05 均调通 intraday\_main\_flow，排除 FREE\_TIER\_MARKET\_OPEN\_RESTRICTED 盘中受限根因）；④ **9-02/9-03 连挂真根因 = 项目级** **`.mcp.json`** **未含 mcp\_wudao**（定时任务环境加载不到全局 MCP 插件 → 调用"不可达"），data\_source\_keys.json 旧登记"插件未安装"已过时。**修复**——① 新增 `scripts/wudao_client.py`（urllib 零依赖直连 /api/mcp，CLI `python scripts/wudao_client.py <tool> '<json_args>'`，失败 exit!=0 打 \[WUDAO-ERR]）；② 项目 `.mcp.json` 加 `wudao`（streamable-http + Bearer，已 gitignore）；③ `config/data_source_keys.json` 悟道条目更新 url+status；④ 9:25/9:45/11:35/15:40 四任务指令悟道调用全部改为 `scripts/wudao_client.py` 直连。**验证**：客户端 market\_overview（上涨1846/下跌3570/强度43）、intraday\_main\_flow（香农芯创 +4.92亿 开盘红）、dragon\_tiger（50条）、news\_hotlist 均实测通过，链路健康。**教训**：定时任务（Schedule）环境与交互环境 MCP 配置可能不同，远程数据源一律写 Python 直连客户端（如 caihui/wudao），勿依赖客户端插件层。

* **F3/F5/基础行情加悟道 + 熔断路由（2026-09-03，T-20260903-002）**：用户拍板——①悟道加入 F3/F5/基础行情/大盘指数链做备选源：F3 用 `official_announcements`/`research_reports`/`cls_news`（公告/研报/财联社），F5 用 `theme_intraday_capital`/`sector_analysis`（题材资金/板块分析），基础行情用 `stock_rank`/`valuation_snapshot`，大盘指数用 `index_market`；②新增**熔断路由** **`scripts/data_source_router.py`**——各源不稳定（TDX 6日3挂/悟道插件连挂/iFind 口径存疑），熔断器让故障源快速短路：取数前 `check <源>`（OPEN 跳过/PROBE·OK 才调用）+ 调用后 `record <源> ok|fail [ms]`，连续失败 ≥3 次熔断 300s、熔断期过半开探测、成功立即恢复，状态持久化 `data/cache/datasource_health.json`（多任务共用）。CLI 测试通过（fail×3→OPEN→record ok→恢复，其它源不受影响）。9:25/9:45/11:35/15:40 四任务取数规则已固化（v8，F3/F5/基础行情加悟道备选 + 全链熔断）；`MCP数据源配置说明书.md` 升 v8（扩展指标组 ③④⑤⑥ 完整链 + ⑦ 熔断路由 + 编排表/职责表更新）。

* **夜间检修任务落地（2026-09-03，T-20260903-003）**：用户拍板——每日 01:00 检修确保次日策略可运行/数据最新/面板已更新，全模块覆盖（A-G）。已建：①`scripts/nightly_check.py`（A 数据完整性\[增量/主源/财务PIT] / B 面板模型同步\[面板落后自动 refresh\_panel\_v3 重刷] / C 调度任务健康 / D 数据源连通性+熔断恢复\[腾讯/悟道探活 record ok] / E QMT进程/桥心跳/账本戳/对账 / F 磁盘/旧文件清理/脚本冒烟 / G 报告+告警；`--fix` 自动修复，`data/cache/nightly_check_<date>.md` 报告）；②Schedule「夜间检修(项目16)」e05e526a 01:00 工作日（核对次日 6 任务 Active + 检修报告 + 飞书告警 + 盘前晨报，不阻塞次日 9:15）。**首次 dry-run 即抓到真实问题**：B1 面板最新日 9/2 ≠ 最近交易日 9/3（当天 16:30 refresh 未到位）→ 正是检修价值；A4 财务 PIT 8/21 为季报披露制正常。A4 修复：income ann\_date 混合格式需 `pd.to_datetime(errors='coerce')`。

* **Tushare moneyflow 升级为 F2 权威主源（2026-09-03，T-20260903-004）**：用户充 Tushare 会员后评估 WB 交付（`P16_Tushare主数据源升级说明.md` + `scripts/refresh_tushare_moneyflow.py`）——**采用**。核心：①**单位坑已实测修复**：Tushare `moneyflow` 原生 amount=万元（600519.SH 9-02 RAW `buy_lg_amount=81094.1` 实测），parquet 同万元，**禁止 ÷1e4**（初版误除导致偏小 1e4 倍，已归档 bad 版+回滚 .bak+重刷）；②增量刷新正确（读最新→次日刷到 T-1、分页 5000、keep=last 去重、.bak 备份原子写）；③`deploy_predict_g2.py` F2 自适应（快照新鲜→Tushare 主源、滞后→回退新浪兜底）已实码（L100-130）；④token 已填（data_source_keys.json ACTIVE）；⑤moneyflow.parquet 已刷到 **9/2**（消除 8/21 滞后 11 天）；⑥**补建缺失的 19:30 刷新任务**（WB 文档声称 268d2dd9 实际不存在 → 新建「Tushare刷新」c63cff58 19:30 工作日）；⑦夜间检修 nightly_check 加 A3 moneyflow 新鲜度检查、任务核对清单 6→7 个；⑧配置说明书 v8 总表+扩展指标组标注 Tushare F2 权威主源（**与评分卡实时 F2 区分**：Tushare T-1 日频做模型特征层 mf_main_net 等 43 特征，评分卡 F2 9:45 当日仍用东财/悟道）。**经验**：信任交付前必须核实"声称的定时任务/产物是否真实存在"（268d2dd9 不存在即例）。

* **待验证（09-01）**：09:15 出 model\_top10.csv → 09:25 写 F3 缓存 → 09:45 跳过 deploy 直接复核+下单。

## 2026-08-31 会话沉淀（V2.0 大QMT 文件桥迁移 + 代码格式 P0 坑）

### hy4 审计修复（2026-09-01 凌晨，依据 `results/V2.0收益真实性评估报告_20260831.md`）

> 用户授权对合理部分落地。当前生产基线已演进为 **V1.3**（27特征/6694树，8/31 夜 promote）；大QMT 信号层用 **g2 43特征/1964树**（deploy\_predict\_g2.py 确认）。

**已落地**：

* **P1-1 去重**：`deploy_predict_g2.py` / `paper_forward.py` 的 live 追加改为「读现有→concat→按(date,code)去重→原子写回」（`_append_live_dedup`），幂等防重入；`paper_forward_live.csv` 已清理（26→20 行，8/28 重复 4 次移除）。

* **P1-1 前向口径**：`paper_forward.py --backfill` 新增 N 日 open→open 收益回填（entry=选股日次日 open，exit=买入后第 N 交易日 open），对齐回测 N=10 alpha 来源；数据只到 8/28 时最早可回填 9/1 到期（8/17 候选），随数据到位自动累积；每次按当前 hold 幂等重算。判据沿用：N>30 + 剔极值仍正 + 折算 t>2。

* **P1-3 卖出侧可执行性过滤**：`scan_rotate_cost_real.py` 卖出侧补一字跌停过滤（open<=down\_limit 卖不掉→推迟）+ 停牌/退市不冻结（连续 60 天无价按 50% 损失假设清仓），计数器 SELL\_SKIP\_DOWN/SELL\_DELIST 输出；买入侧已有 `_executable`。**注意：此改动会略微降低历史回测收益（更保守），重跑结果见** **`data/real/scan_rotate_cost_real_report*.md`** **对照**。

* **P2-2 口径统一**：`ANNUAL_RESULT.md` 表头"交易数 516"改为"交易日数 516 / 独立轮次 \~47(N10)/\~86(N5) / 成交笔数 102/166"。

* **P0-1/P0-3 表述**：`周一实盘运行检查清单_20260831.md` 184 行明确"33特征版已弃用仅备份"；`VERSIONS.md` 年化 75.88% 加降级警示（回测口径/测试期寻优/非上线）。

* **模型路径修复**：`paper_forward.py` ASCII 路径过期（trae-cn/work 已迁 `D:/QuantLab/models`），已修正。

**分析产物**：`audit_stats_walkforward.py`（P1-2 显著性 + P1-4 walk-forward，输出 `data/real/audit_stats_walkforward_20260901.md`）。

**审计 P0-2 结论**：33 特征 V2.0-live 已弃用、大QMT 直接用 g2 43特征，无需补测被弃用模型；真正要验证的是 **g2 模型本身**（75.88% 未过多重检验校正）——见 P1-2/P1-4 分析。

**前向验证自动执行恢复（2026-09-01）**：核查发现 `paper_forward_daily` 计划任务**缺失**（8/28 16:45 最后成功运行后断更——`g2_pipeline_daily.log` 停在 `[20260828_164501] OK`，Windows 计划任务与 TraeWork 自动化均无此任务）。已恢复：①`paper_forward_daily.ps1` 补第 3 步 `paper_forward.py --backfill --hold 10`（回填 N=10 open→open 收益，审计 P1-1）；②重建计划任务 `paper_forward_daily`（周一\~五 16:45，Interactive only + Administrator，与 quant\_daily\_update 同模式；16:30 数据更新后 15 分钟跑，确保增量就绪）。backfill 在 TRAE vm python 下验证通过。**前向验证完整链路**：16:30 quant\_daily\_update（更新增量数据）→ 16:45 paper\_forward\_daily（build\_g2\_daily 快照 → deploy\_predict\_g2 记录候选 → backfill 回填到期收益）。教训：PROJECT\_MEMORY 声称的定时任务需与 Windows 任务计划实况对拍，任务可能随环境变动消失（8/28 后管道静默断更未被发现）。

## 2026-08-31 会话沉淀（V2.0 大QMT 文件桥迁移 + 代码格式 P0 坑）

### 迁移背景（T-20260831-001/002）

* V2.0 从 miniQMT（67014907）迁移到大QMT（70180771）文件桥架构：外部信号层（Python 3.10）写 `D:/QMT_POOL/g2_bridge/cmd/`，内置执行器（大QMT Python 3.6）轮询读取 → 委托 → 反查 → 状态回写 `state/`。seq 幂等 + pending 状态机（300s/3 次重试）+ 三风控规则（止损7%/止盈15%/追盈8%）迁内置。

* 产物 `build/strategy_p16_g2_bridge.py`（GBK 单文件，`broker/qmt_order.py` 之外的独立桥实现），构建脚本 `build_p16_g2.py`（py3.6 语法扫描 + BUILD\_TAG 替换 + 首行强制 `# coding=gbk`）。

### 坑1【P0】passorder 代码格式（T-20260831-002，本次核心）

* **现象**：小单验证 passorder `ret=0` 但委托不进通道（界面无记录 / 反查无单 / `XtTradeData` 0 字节 / PENDING-NO-ORDER 空转 300s 重试全废单）。

* **根因**：`_to_qmt_code` 把 `600522.SH` 翻转成 `SH.600522`；QMT 主日志实锤 `orderCode:600522SH 不合法!` → 静默废单。ATR/Project\_10 均用数字在前 `601985.SH`/`600000.SH` 实盘成交正常。

* **修复（3 处，BUILD\_TAG=20260831-151459）**：①`_to_qmt_code` 原样返回 `600522.SH`；②`_norm_code` 改 `split('.')[0]` 取 6 位数字（原 `[-1]` 误取交易所后缀，反查必失配）；③风控纳管/持仓快照裸码补 `.SH`/`.SZ` 后缀。

* **教训固化（已入** **`QMT避坑指南.md`** **第七章 +** **`broker/QMT委托买卖防坑指南.md`** **坑5）**：

  1. QMT 全链路代码格式统一 `600522.SH`（数字在前）——passorder/反查/持仓/选股一致；
  2. `passorder ret=0` 只代表异步受理，**必须查 QMT 主日志** **`XtClient_<date>.log`** **确认** **`CTradeClient::order`** **才代表真进通道**；`parserParam` + 「下单代码不合法」= 静默废单；
  3. 排查废单看主日志（msg service/parserParam 段），不是 FormulaOutput 策略日志；
  4. 反查归一化取码用 `split('.')[0]`，禁止 `[-1]`（取到交易所后缀）。

### 坑2【P1】构建脚本首行编码

* `build_p16_g2.py` 原只校验产物首行 `# coding=gbk` 但源码首行是 `# coding=utf-8` → 构建必失败。已改为写出前正则替换首行为 `# coding=gbk`。

### 遗留待办（09-01 周二开盘）

* 部署 151459 产物 → 全新小单验证主链路（读指令→下单→反查→成交→回写→对账），确认 QMT 主日志出现 `CTradeClient::order`；

* 小单实盘验证三件套（不易成交挂撤 / 正常成交 / 对账一致）；

* 灰度切换（小资金并行观察 → 全量），旧 miniQMT 保留 ≥1 个月作回滚通道。

## 2026-09-01 上午小单调试（大QMT D:\QMT交易端模拟，构建 151459→094726→100845→101406）

> 上午小单实盘（模拟端 70180771）调试主链路，发现并修复 4 个问题，全部在 `strategy/strategy_p16_g2_bridge_src.py` 源码 + `build_p16_g2.py` 重建。

### 关键结论（QMT 环境差异，D:\QMT交易端模拟 与 ATR 环境 D:\国金QMT交易端模拟 不同）

* **① 代码格式修复验证 PASS**：09:37:56 主日志 `CTradeClient::order ... 601988 op:18 prz:6.61`（数字在前），8/31 的 `orderCode:600522SH 不合法` 根因确认修复。**验证方法：看 QMT 主日志** **`parserParam`** **段（无「不合法」）+** **`CTradeClient::order`** **出现 = 委托真进通道；`passorder ret=0`** **只是异步受理不算成功。**

* **② 真实委托号字段 =** **`m_strOrderSysID`（不是 m\_strOrderID/m\_nOrderID）**：DIAG-ORDER 一次性诊断打印实锤——D:\QMT交易端模拟 的订单对象字段集只有 `m_strOrderSysID`（如 '2882'）、`m_strOrderRef`（'44569...'）、`m_nOrderStatus`（'56'）等；`m_strOrderID`/`m_nOrderID`/`m_strSysid` 不存在。`_extract_order_id` 多字段兜底遍历（m\_strOrderID/m\_nOrderID/m\_strOrderSysID/m\_strSysid/m\_nOrderRef/m\_strUserOrderId）。

* **③ status 56 = 已成（全部成交）**，而 `m_nDealVolume` 读 0（字段不存在 → int('')=0）。成交判定必须「status==56 或 成交量>=目标」双触发，`_extract_deal_volume` 多字段（m\_nDealVolume/m\_nTradedVolume/m\_nCumDealVolume/m\_nDealVol/m\_nTradeVolume/m\_nVolTraded）+ status56 回退目标量。

* **④ prType=5（最新价）会忽略传入 price，按最新价成交**：parserParam 显示 modelPrice:6.3，但 CTradeClient::order 实际 prz:6.6（最新价），下单 0.1s 即成交。→ **桥无法用低限价造"不可成交单"测撤单**；撤单测的可行路径=同 tick 内 orders+cancel 一起写，让桥同一 handlebar 内"下单→撤单"。

* **⑤ 撤单时序竞态**：模拟盘成交极快（0.1-7min 不定），撤单指令到达时订单可能已成交 → 撤单前必须反查当前状态，已成交按 FILLED（CANCEL-TOO-LATE）收尾，绝不写假 CANCELED；撤单发出后进 `cancel_requested` 确认态（`_handle_cancel_confirm`：已成→FILLED / 已撤53,54→CANCELED / 废单55,57→REJECTED / 60s 未确认→强制 CANCELED，外部以 positions 兜底）。

### 2026-09-01 下午·第五轮（BUILD 101406→130532，撤单链路最终验证 PASS + 崩溃根因修复）

* **⑥ 撤单 order\_id 必须 int（不是 str）**：`[CANCEL-ERR] order_id=2882: Python argument types` 实锤——QMT passorder 撤单第6参绑定期望 int，`m_strOrderSysID` 是 str → 传 str 抛类型错误、撤单静默失败（QMT 日志无 opType:24）。修复 `_cancel_order_by_id` 先 `int(str(order_id))` 转换。

* **⑦ 真实成交量字段 =** **`m_nVolumeTraded`**（DIAG 实锤 =100；m\_nDealVolume 不存在读 0），已加入 `_extract_deal_volume` 候选列表。

* **⑧ 策略崩溃根因（12:31 PermissionError）**：handlebar `not in_session` 分支直接 `_write_heartbeat` **无 try/except**；心跳 os.replace 遇外部读文件锁（agent 监控 Get-Content 轮询与原子改名撞车）抛 WinError 5 **未捕获 → 策略 Python 崩溃 → 下午 handlebar 停触发**。修复：`_atomic_write_json` 遇 OSError 重试 5 次×0.2s + `not in_session` 心跳 try/except。**教训：①QMT 策略任何写文件路径都要 try/except 兜底，文件锁（尤其外部进程读）会让 os.replace 偶发 WinError 5；②外部监控读桥文件必须低冲突（FileShare.ReadWrite 或降低频率），禁止高频 Get-Content 轮询与桥原子写撞车。**

* **⑨ 撤单链路端到端 PASS（130532 下午实证）**：BUY 600028 100\@5.54 + 同 tick 撤单 → QMT 主日志 **opType:24 真实撤单** + 持仓 600028 保持 100（订单未成交）+ fills 正确 CANCELED（走 cancel\_requested→60s 未确认强制，因已撤订单从反查列表消失）→ **无孤儿**。模拟盘策略恢复健康（心跳实时、无 PermissionError 复发）。

### 2026-09-01 下午·第六轮（BUILD 131620，撤单格式实锤 + 竞态修复定稿）

* **⑩ 程序化撤单格式错误实锤**：13:07:44 撤单被 QMT 拒 `[msg service] 函数: passorder, 下单数量/金额/比例为0`——**passorder(24,...) 第7参（股数）传 0 被 D:\QMT交易端模拟 拒绝**，撤单静默不生效 → 订单 4（600028 100\@5.53 sys:4006）仍挂通道，**用户手动撤单解除（stat 54 cancelvol 100）**。修复：`_cancel_order_by_id` 第7参传原订单股数 `int(vol)`（131620）。**教训：撤单 passorder 第7参必须传原订单股数（>0），ATR 环境（D:\国金QMT交易端模拟）传 0 可行、本环境不可——两 QMT 撤单格式不同。**

* **⑪ 撤单竞态修复验证 PASS（131620）**：订单 5（BUY 600028 100\@5.54）+同 tick 撤单 → 订单已成交（模拟盘 prType=5 秒成）→ 桥正确写 **FILLED（filled before cancel）** 而非假 CANCELED（`_process_cancels` 撤前先反查，已成交按 FILLED 收尾）。

* **⑫ 确认逻辑 fail-loud 定稿（131620）**：`_handle_cancel_confirm`——订单仍活跃（2/48/49/50）→ **保留 pending + 90s 重发最多2次**（绝不假写 CANCELED，外部可预警）；查不到订单 → 写 **UNCONFIRMED** 由 positions 兜底（绝不写假 CANCELED）。杜绝"桥以为撤了、单还挂着"的假确认。

### 2026-09-01 下午·第七轮（BUILD 140324，官方撤单 cancel() 突破——参考 QMTDoc\QMT撤单重委托方案调研.md）

* **⑬【核心突破】passorder opType 枚举没有撤单值（24=股票卖出）**：之前一直用 `passorder(24,...)` 假装撤单，QMT 根本不认——把第6参 order\_id 当**价格**解析（parserParam modelPrice:4.456e18）、把撤单当**卖出单**校验（用户亲见"策略信息显示卖出不是撤单"）→ 拒「下单数量/金额/比例为0」。**正确撤单 = 官方** **`cancel(orderId, accountId, accountType, C)`** + **`can_cancel_order(orderId, accountId, accountType)`** **预检**。

* **⑭ cancel() 的 orderId =** **`m_strOrderSysID`（柜台合同号）**（如 4312/4760）：`cancel(4312, '70180771', 'stock', C)` 返回 True=指令送达柜台，**返回 True 不等于已撤**，需确认态轮询终态（status 54=已撤）。**两次端到端实证（order7/order8 均 CANCELED status=54）**。

* **⑮ 状态码标准表（调研文档）**：活跃={48未报,49待报,50已报,51已报待撤,52部成待撤,55部成}；终态={53部撤,54已撤,56全成,57废单}。**55=部成是活跃态**，之前误当终态（ACTIVE\_SKIP\_STATUS/CONFIRM\_DEAD\_STATUS 原含 55 是 bug）→ 修正为排除 (53,54,57)。

* **⑯ prtype=11 指定价可造"不可成交单"测撤单**（132942 新增 prtype 支持）：BUY 600028 @5.30/5.20（低于市价 5.5）挂单 stat 2 不成交 → 撤单实测。**之前误判"桥无法造不可成交单"（⑨ 结论被推翻）——prType=5 忽略价格但 prType=11 指定价生效**。

* **⑰ 已知小瑕疵已修（BUILD 142420 实证）**：`_execute_order` 下单后反查捕获 sysid（m\_strOrderSysID，与 `_check_pending_orders` 一致）+ `_process_cancels` 反查补录兜底 → **首次撤单即用柜台合同号成功**（order9：`cancel(5079)` 返回 True → status=54 立即确认，无 CANCEL-NOT-CANCELABLE、无 90s 重试；对比 order8 首次用 ref 被拒→90s 重试）。

* **最终结论：程序化撤单已通（cancel() + 柜台合同号 m\_strOrderSysID），模拟端不再需要界面手动撤单。**

### 教训

* **孤儿仓 = 撤单失效 + 限价单残留成交的完整后果链**：空 order\_id 撤单假写 CANCELED → 真单在通道 → 价格回落成交 → 桥不知情成孤儿。修复后撤单先反查 + 确认态，杜绝假记录。

* **QMT 不同安装（D:\QMT交易端模拟 vs D:\国金QMT交易端模拟）对象字段/状态码可能不同**：ATR 模板的字段假设不能直接套用，必须 DIAG 打印实际字段再适配（`_g_diag_printed` 一次性诊断）。

* **构建每次都要 py3.6 语法 + GBK + 无 MOCK 验证**，产物 BUILD\_TAG 必须与心跳对得上（心跳 build\_tag 是部署生效的判据）。

### 当前持仓（待明日清，T+1 锁定）

* **600028 200 股（@5.6005 含费）+ 601988 200 股（@6.691 含费）+ 601398 100 股（@8.2243 含费，撤单测试意外成交仓）**，约 3254 元，模拟盘；`cmd/positions_cfg_20260901.json` 已写成本锚，risk 纳管中；今日不可卖（can\_use=0），**09-02 开盘后清仓**。**清仓指令脚本已备**：`clear_orphans_20260902.py`（备用，未运行不触发；09-02 开盘后跑 `miniqmt venv python clear_orphans_20260902.py` 即写 3 条 SELL 到 `cmd/orders_20260902.json`，幂等防重，验证「先卖后买+卖出成交回写+对账」闭环）。

* fills 记录 P16\_20260901\_0001/0003=CANCELED vol0（历史假记录，实为已成交孤儿）需在外部对账时注意剔除；0004/0007/0008=CANCELED（撤单链路测试，均无成交，非孤儿）。

## 2026-09-01 晚·G2 基础设置搭建（代码级全套 + 配置补齐收官，与 V1.3 完全隔离）

* **① G2 独立配置**：`g2_config.py`（账号 70180771/桥路径/资金池/参数），**绝不 import V1.3 qmt\_config**；独立资金池 `D:/QMT_POOL/g2_bridge/g2_strategy_capital.json`（初始 10 万，account\_id 戳）。

* **② G2 脚本**：`rebalance_g2.py`（每日换仓，先卖后买，只认 G2 账本 positions\_cfg+fills FIFO，T+1 锁定自动跳过卖出，dry-run 默认；20260901 验证 8 只过红线/BUY top2/3 只孤儿 T+1 skip）；`reconcile_g2.py`（日终对账，持仓差额/孤儿预警，验证捕获 600028 超额 100 股）；`G2_RUNBOOK.md` 运行手册。

* **③ 隔离硬约束**：账号 70180771 vs 67014907、资金池/候选/持仓归属独立、G2 绝不调 qmt\_trader、绝不纳管他人持仓。

* **④ 配置补齐（2026-09-01）**：G2 计划任务已建（Paused 未启用）：`30344e79` G2换仓 09:50 + `4cf3db92` G2日终对账 15:45（工作日，与 V1.3 09:45/15:40 错开 5 分钟）；`capital_allocation.yaml` 双镜像登记 `g2_bridge` 10 万/2 只，`check_capital_allocation.py` 退出码 0 PASS（4 策略共 40 万 ≤ 账户 1000 万）。

* **⑤ 上线（2026-09-01 晚诚哥拍板 09-02 正式上线）**：G2 计划任务已 resume（Active）：`30344e79` 换仓 09:50 + `4cf3db92` 对账 15:45，09-02 首日自动运行；换仓任务加「当日候选缺失→中止」保护；候选管道已由 09:25 任务（b0254f11 Active）覆盖；孤儿仓 500 股由 09:50 换仓自动卖出（非目标持仓），不再单独跑 clear\_orphans（避免双重卖出）；上线后观察 ≥1 交易日稳定再切（旧 miniQMT 67014907 保留 ≥1 月回滚）。

* **⑥ 飞书推送升级（2026-09-01 晚）**：新增 `push_review_card.py`（TOP10 打分明细卡，4 项拍板：TOP10 全量/双表\[速览6列+六因子8列]/Top5 重点卡/09:25 预估版）。丰富版(09:50)读 `selection_full.csv`（F1-F6 实时+当日主力资金+量比+板块+催化）；预估版(09:25)读 `model_top10.csv`(SC\_F1-F6 模型预估) + `crosscheck_<date>.json`(当日资金/现价/板块) + `review_<date>.json`(F3 催化)，缺失显示"—"绝不编造。09-01 真实数据双模式验证通过（10 只/模式），丰富版测试卡真发飞书 bot 成功。任务接线：`30344e79` 加第6步推丰富版、`b0254f11` 加第6步推预估版、`15868a74`（V1.3 09:45 复核）加第6步推丰富版。用法：`python push_review_card.py --date <YYYYMMDD> [--estimate] [--summary "大盘|动作"] [--no-send]`。

* **⑦ 持仓卡片升级（2026-09-01 晚）**：新增 `push_holdings_card.py`（持仓卡片：摘要\[总浮盈亏/资金池/已实现] + 逐股卡\[现价/涨跌/浮盈亏/主力/量比/板块/建议/预警] + 汇总表）。任务写 `data/cache/holdings_<date>.json`（schema 见模块头部）→ `--type midday|close` 推送。09-01 真实持仓测试卡真发飞书成功。接线：`095bbe1b`（11:35 午休）加第5步推 midday、`9a41d7f7`（15:40 盘后）加第5步推 close。用法：`python push_holdings_card.py --date <YYYYMMDD> --type midday|close [--no-send]`。

* **⑧ 推送三批优化全量实施（2026-09-01 晚，诚哥拍板）**：第一批决策缺口：`push_fill_card.py`（成交回报卡，g2=G2桥fills/v13=rebalance json 双源，成交明细+未成交）＋ `push_past_review_card.py`（昨日推荐复盘，读昨日 selection\_full+g2\_top2 + 今日 past\_quotes\_<date>.json，算 top10/top2 今日表现 vs 大盘 HS300）＋ `push_review_card.py` 加 `--action`（📌操作建议置顶：买入/卖出/仓位）＋ `--holdings`（持仓→目标换仓对比）。第二批体验：Top3 重点卡加"查看行情"按钮（open\_url 东财）、降级/交叉验证⚠️标注上移个股行、数据口径＋数据截至时间戳明确。第三批：`push_alert_card.py`（管道健康告警：候选缺失/桥未存活/对账异常主动推）＋ `push_review_card.py` Top3 精简（4-10 仅表内）＋ 09:50 G2 改推成交回报卡替代重复 TOP10 卡（⑧合并，TOP10 卡由 V1.3 09:45 推）＋ `push_daily_summary_card.py`（15:45 盘后总览，V1.3 持仓 + G2 成交/对账合一）。任务接线：`b0254f11` 加第7步昨日复盘、`15868a74` 加第7步成交回报+候选缺失告警、`30344e79` 改第6步成交回报+告警、`4cf3db92` 加第5步盘后总览+对账异常告警。全部模块 py\_compile 通过、09-01 真实数据构建通过，成交回报/持仓卡真发飞书成功。

## 2026-09-02 ·G2 首日实盘 P0：status=55 误当废单 → 双倍建仓（600262 3000→6400 / 300964 800→1000）

> 触发：G2 首日 09-02 手动 rebalance --live 后，fills 只回 2 笔（600262 3000/300964 800），但 positions 显示 600262=6400、300964=1000（多 3400/200 股）。QMT 委托记录 6 笔全同价（15.77/57.03），恰为初始 2 笔 + 4 笔废单重试（600262 2200/1000/200 + 300964 200）。
> **排查关键**：QMT 端策略日志（`D:\QMT交易端模拟\userdata\log\XtClient_FormulaOutput_20260902.log`）完整还原 `[REJECTED-RETRY-1/2/3]` 时序；桥 seq=1 只发 2 笔、fills 只回 2 笔 → 6 笔全为桥自身 `_handle_rejected_retry` 所为，**非绕桥、非第二下单者**。

* **根因（桥状态机）**：`_check_pending_orders` 1b 分支 `if status == 55` 把「部成活跃态」当「废单」→ `_handle_rejected_retry` **不撤原单**直接 `_do_passorder` 重报剩余量。55 在模拟端=原单仍在挂单继续成交（DIAG 实锤 3000 单 `m_nVolumeTraded=400/m_nVolumeTotal=2600/status=55`）→ 原单继续成交到 3000 + 重报单（2200/1000/200）也全成交 = 6400。300964 同理 800+200=1000。注释自己都写"55 部成是活跃态不能算死"（CONFIRM\_DEAD\_STATUS=53,54,57），但 1b 却 `status==55` 当废单，自相矛盾。

* **修复（BUILD\_TAG 20260902-161814，源文件** **`strategy/strategy_p16_g2_bridge_src.py`）**：

  1. 1b 分支 `status == 55` → `status == 57`（55 部成走 1a/1d 等原单自然成交满；57 真废单才走重试）。
  2. `_handle_rejected_retry` 加重报前死透确认（对齐 `_handle_timeout_retry` 纪律）：短轮询反查原单（4×0.5s），原单已全成交 → FILLED 收尾绝不重报；原单仍活跃 → 延后 60s 复查（`REJECTED-UNCONFIRMED`）绝不重报；死透（53/54/57 或查不到）才重报 remaining。

* **验证**：`build_p16_g2.py` 重建 exit 0（py\_compile + Py3.6 禁用语法扫描 + GBK 写出 + BUILD\_TAG 替换）；产物验证 MOCK=0/f-string=0/walrus=0/`status==55` 残留=0/`status==57` 生效=1/`# coding=gbk` 头/BUILD\_TAG=20260902-161814。

* **遗留（09-02 当日，T+1 锁定无法撤销/卖出）**：600262 实持 6400（超额 3400）、300964 实持 1000（超额 200），bridge fills 账本仍只记 3000/800 —— **账本与实际持仓不一致**，对账/换仓时必须按 positions 实际持仓核对，明日换仓按目标差额自然处理。

* **部署要求（硬）**：QMT 端 `python/STRATEGY_P16_G2_BRIDGE.py` 是 QMT 加密密文（MiFBOec 头），**无法脚本覆盖**，必须在 QMT 界面重新加载 `build/strategy_p16_g2_bridge.py`（BUILD\_TAG=20260902-161814），以心跳 `build_tag` 为部署生效判据（PROJECT\_MEMORY L437 红线）。

* **教训**：① 状态码语义（55 部成 vs 57 废单）在模拟端必须 DIAG 实锤，不能按注释/直觉写分支；② 废单/超时重报前必须先确认原单死透（不撤原单就重报 = 双倍建仓），此纪律已对齐 timeout\_retry；③ 账本 fills 与 QMT 实际持仓可能脱钩，positions 是唯一真相（AGENTS「账户 position 唯一真相」红线）。

## 2026-09-02 ·G2 实盘与回测口径对齐：N=10 持有期（用户拍板「回测有意义的前提是实盘对齐」）

> 背景：回测/前向（`scan_rotate_cost_real.py` N=10 卖出条件 `(i-buy_i)>=N+1`、`paper_forward.py --hold 10`）为「买入后持有 10 个交易日」，
> 但实盘 `rebalance_g2.py` 原逻辑「每日对齐 top2、掉出即卖」——两者不一致，用户指出「不然回测的意义在哪」。

* **实盘对齐改动（rebalance\_g2.py + g2\_config.py + deploy\_predict\_g2.py）**：

  1. `g2_config.HOLD_DAYS = 10`（对齐回测 N=10）+ `SELECT_TOP = 10`（候选池大小）。
  2. `deploy_predict_g2.py --top 10`（默认改 10）产出 `_g2_top10.csv`，对齐回测 TOP10 候选池，不再只产 top2。
  3. `rebalance_g2.py` 卖出对齐回测 simulate 真实语义：**持仓满 10 交易日到期 → SELL**（不看是否在候选池内；止损/止盈桥内风控不动）。未满 10 日 → 不卖（`[SKIP] 持有未满10日`）。
  4. `rebalance_g2.py` 买入：**仅当持仓数 < TOP\_N(2)** 时从 Top10 池选 total\_new 最高、不在持仓、过红线的补足（对齐回测 `while len(hold) < TOP`，避免持仓膨胀超 2 只）。
  5. 持仓建仓日持久化：`data/rebalance_g2/g2_hold_dates.json`（`{code:"YYYYMMDD"}`），`_update_hold_dates` 新BUY记当日/SELL清仓移除/保护仓保留，live 落盘、dry-run 预览。
  6. 交易日计数：`is_trade_day.is_trade_day()` 逐日判断（主库日历快照滞后到 8/20 也 OK——未覆盖日期走「默认交易日+节假日表」fallback，9/25-27 中秋、10/1-7 国庆正确排除）。**坑：日历文件里日期带** **`T00:00:00`** **后缀，不能裸** **`in cal`** **集合匹配，必须走 is\_trade\_day()**。
  7. 消费方同步改读 Top10：`premarket_g2_check.py`（盘前核对）、`push_past_review_card.py`（复盘卡，G2 部分改读 `g2/` 子目录 top10，顺带修了原读顶层 `data/selections/` 过期副本的路径缺陷）；docstring 清理（push\_alert\_card/rebalance\_g2/g2\_config/paper\_forward\_daily.ps1/周一检查清单）。

* **首次初始化（已做）**：600262/300964 建仓日=20260902；9/3 起持有未满 10 日 → 不卖不买，持仓保持 2 只。

* **验证记录**：未满10日不卖 ✅（9/2建仓→9/11=7日不卖 / 9/16=10日可卖）；满10日到期 SELL+从 Top10 池补足 ✅；持仓不膨胀 ✅；建仓日持久化 ✅；Top10 候选生成（真实 deploy 产 10 只全过红线）+ 消费方改读 ✅；py\_compile 通过 ✅。

* **注意**：`qmt_bridge_client.build_orders_from_g2`（top\_k=2 参数化）未被 G2 主流程调用，保持不动；VERSIONS.md L229 印证 N=10 为回测最优换仓周期（约 2 周）。

## 2026-09-02 收工总结（今日已做 + 待完成）

### ✅ 今日已做（G2 首日实盘 + 两项 P0 级修复）

1. **实盘实时链路修复（build\_g2\_daily.py，P0）**：增量库列名 `volume` vs `RAW_COLS` 期望 `vol` 不匹配 → `vol_ratio_5_20` 等量价特征实盘全 NaN（train-serving skew）。加 `volume→vol` rename 修复，缺失率 100%→0.21%，候选分数恢复正常（600262 0.6117→0.6268 / 300964 0.5890→0.6107）。
2. **G2 首日实盘执行**：手动 `rebalance_g2.py --live` 写桥（seq=1，600262 3000\@15.77 + 300964 800\@57.03），QMT 10:07 全部成交。
3. **双倍建仓 P0 定位+修复（bridge 状态机）**：`status==55`（部成活跃态）被误当废单 → 不撤原单直接重报 → 600262 3000→6400 / 300964 800→1000。修复：1b 分支 55→57 + `_handle_rejected_retry` 加死透确认（BUILD\_TAG 20260902-161814）。详见上方「G2 首日实盘 P0」节。
4. **实盘与回测口径对齐（N=10 + Top10 候选池）**：`rebalance_g2.py` 改为回测 simulate 真实语义——卖出=持仓满 10 交易日到期（逐票滚动，不看是否在池内，止损/止盈桥内处理）；买入=持仓<2 时从 Top10 池选 total\_new 最高补足。`deploy_predict_g2 --top 10` 产 Top10 池。持仓建仓日持久化 `g2_hold_dates.json`。详见上方「N=10 持有期」节。
5. **消费方同步**：premarket\_g2\_check / push\_past\_review\_card（顺带修顶层过期副本路径缺陷）/ 各 docstring 全部改读 Top10。
6. **新增工具**：`premarket_g2_check.py`（盘前核对：心跳 build\_tag + Top10 候选 + 超额持仓三项）。
7. **positions\_cfg 成本锚自动化（T-20260902-005，P1）**：新增 `gen_positions_cfg_g2.py`（G2 持仓 code ∩ 账户持仓 avg\_price 含费成本 → 写 `cmd/positions_cfg_<date>.json`），集成 rebalance --live（换仓后生成）+ reconcile（对账前刷新校准），空仓/无持仓安全跳过、幂等。已补 9/2 缺口（600262 6400\@15.7841 / 300964 1000\@57.0989）。
8. **F6 估值补字段（T-20260902-006，P0，9/3 早盘前）**：增量日估值滞后 11 天（fill\_cols 用主库 8/21 旧值）→ 改为「增量日逐日用主库每股慢变量 × 当日真实 close 反推」：pe\_ttm/pb/dv\_ttm/circ\_mv/turnover\_rate 全部反映当日价格，口径交叉验证误差 0.00%。重建快照 + 重生成候选（Top10 仍含 300475/002641）。**V1.1 merge\_live\_features.py 同款问题仅标注不动（非实盘）**。
9. **G2 自动化梳理（T-20260902-007）**：**TRAE 自动化面板已有** `30344e79`（G2换仓 09:50）+ `4cf3db92`（G2日终对账 15:45），均 Active（`30344e79`/`4cf3db92` 是 TRAE 任务 ID，非 Windows 计划任务）。曾误建 Windows 计划任务 `Quant_G2_Rebalance`/`Quant_G2_Reconcile` 与 TRAE 重复（双跑会重复下单），**已删 Windows 任务 + ps1 脚本**，只保留 TRAE。代码优化保留（TRAE 任务同样生效）：`rebalance_g2.py` 桥存活读最新心跳（修复误判）+ `qmt_bridge_client.notify_feishu`（rebalance/reconcile 复用）+ reconcile 飞书通知。**教训**：查定时任务先看 TRAE 面板（Schedule list）再查 Windows schtasks。
10. **文档**：G2\_RUNBOOK（第四/六/九节更新为 TRAE 任务）、PROJECT\_MEMORY（今日 P0×3 + 收工总结）、全局控制台看板（T-20260902-001\~007）已更新。

### ⏳ 待完成 / 明日（9/3）清单

1. **【用户·必须手动】开盘前 QMT 界面重载** **`build/strategy_p16_g2_bridge.py`（BUILD\_TAG=20260902-161814）**——当前 QMT 端心跳仍是旧版 `20260901-142420`，不重载则双倍建仓 P0 修复不生效。重载后核对心跳 `build_tag` 变 161814。
2. **【Agent】9/3 盘前**：`miniqmt venv python premarket_g2_check.py --date 20260902`（三项核对）。
3. **【Agent】9/3 换仓**：`rebalance_g2.py --date 20260902` dry-run（600262/300964 持有未满 10 日 → 预期不卖不买，持仓保持 2 只）→ 确认后 --live。
4. **【Agent】9/3 盘中**：盯心跳/委托，确认不再出现 `[REJECTED-RETRY]`（P0 修复验证点）。
5. **【Agent】9/3 15:45 对账**：`reconcile_g2.py --date 20260902`，账本 vs positions 差额核对。
6. **【遗留·超额持仓】9/2 实持 600262=6400 / 300964=1000**（账本只记 3000/800），已 T+1 锁定；按 N=10 逻辑满 10 日（约 9/16）到期卖出时按 positions 实际持仓数量卖出，对账/换仓以 positions 为唯一真相。
7. **【观察】N=10 逐票滚动首轮验证**：9/16 前后验证"满10日到期 SELL + 从 Top10 池补足"实际执行正确。
8. **【数据】交易日历快照滞后到 8/20**：is\_trade\_day 已 fallback 处理（默认交易日+节假日表），主库周更后自然恢复，无需人工干预。
9. **【可选·未做】`qmt_bridge_client.build_orders_from_g2`** **仍 top\_k=2**（未被 G2 主流程调用，保持不动；如需统一可后续对齐）。

## 2026-09-07 会话沉淀（出场规则 ablation 独立验证 + ATR2.0 前向验证挂观察）

> 看板 T-20260907-001。事件源：WB 评估文档 `data/real/exit_ablation_20260907_thr58-60.md`（追盈止损出场规则横向对照，红线58/60 × N5/10 × 8 模式）。

### 独立验证结论（脚本 verify_exit_ablation.py，只读复算 + 三重统计口径）

1. **报告数字 100% 复现**：28 组配对 t 的 delta_pp/t 全零偏差；口径自检（红线58 fixed 偏差 -0.012/+0.024pp）、出场原因构成逐行一致。报告无计算错误。
2. **重叠观测假设被实测证伪**：配对日差序列（mode−fixed）AC1 仅 -0.19~+0.10，Newey-West(lag10) 修正后 t 几乎不变（个别反而增大：60/N=10 ATR2.0 1.90→2.21、58/N=5 ma5 -1.98→-2.66）。原因：两组合大部分日子持仓相同，差异序列近似白噪声。→ 报告的 t 未被高估。
3. **逐笔 Welch 口径（独立观测近似）把显著性进一步压低**：28 组逐笔 |t| 最大仅 1.63（ma5 负向）；ATR2.0(60/N=10) 逐笔 t=1.18（每笔差 +1.63pp，n=108 vs 105）。→ "无任何出场规则显著优于 fixed"的结论成立且比报告更强。
4. **回撤结论的边界**：live_trail 降回撤只在 N=10 两档明显（58: -31.6%→-21.0%；60: -30.8%→-20.5%）；58/N=5 档 Calmar 反而恶化（-0.31→-0.67）。回撤差异同样未做显著性检验，仅 516 日单条路径。
5. **纪律结论**：28 组无一显著 → 不改任何实盘出场参数；唯一可确认排除项=时间止损/跌破MA5（提前斩仓类，全档大额负向）；ATR2.0(60/N=10) 作为唯一"正方向稳定未达显著"候选 → 挂前向验证观察，不落盘。

### 落地：出场规则前向验证管道（paper_forward_exit.py）

- **新增只读脚本** `paper_forward_exit.py`：对 `paper_forward_live.csv` rank≤2（实盘口径，g2_config.TOP_N=2）候选，逐笔模拟 none/fixed/live_trail/atr20（可用 --rules 加 atr25/atr30/time/ma5），规则逻辑与 `scan_rotate_cost_real.simulate(exec_ok=True)` 分支逐行对齐（峰值用"截至昨日"判定再并入今日 high、一字跌停顺延、T+1 次日可卖、ATR 建仓日快照、MATURE 优先）。持仓 N=10 open→open。
- **口径自检 `--selfcheck` 8/8 精确复现 backfill**（差 ~1e-16，机器精度）→ 入场索引/交易日历/open 面板与 backfill 完全一致。
- **已接入 `paper_forward_daily.ps1` 步骤5**（16:45 每日，fail-loud：exit≠0 → EXIT-RULE-ALERT 且任务 exit 2）。
- **产物**：`data/real/paper_forward_exit_live.csv`（逐笔明细）+ `paper_forward_exit_<date>.md`（判定报告：N≥30 且 atr20 vs live_trail 逐笔配对 t>2 才建议拍板落盘）。
- **当前基线**：8 笔到期样本（8/17~8/20 入场）：fixed/live_trail 均值 +2.171%、ATR2.0 +1.147%、none +1.110%；n<30 不作判定。按 2 笔/交易日需再约 11 个交易日达标。
- **与现有 G2 前向管道的关系**：G2 前向（forward_stats）验证的是"选股 alpha（持有10日不动）"，出场规则前向验证的是"同一批入场下哪种出场规则更好"——两者互补、互不干扰，共享同一候选源。

### 追盈语义 bug 修复（2026-09-07，T-20260907-002，用户拍板"激活+8%+保本底线 + 四处同步"）

- **问题（601579 实锤）**：601579 当天 10:17 买入（23.96×1900，V1.3 67014907）即触发「高点回撤8%」追盈信号，T+1 卖不掉；且 peak 只需 >成本(任意微盈) 即追踪，回撤 8% 触发时可能是亏损卖出——**"追盈=追跌"**。
- **量化**（analyze_trail_semantics.py，红线60/N=10，647 笔同入场）：现语义 TRAIL 132 次/**64% 亏损**、TRAIL 均值 **-1.10%**；激活+8% 后 TRAIL 74 次/35% 亏损、均值 **+1.79%**，整体均值 +2.575%→+2.676%、胜率 +1.7pp（修复不损收益）。
- **修复内容（4+1 处同步）**：
  1. `qmt_config.py` 加 `TRAILING_ACTIVATE_PCT=0.08`；
  2. `qmt_monitor.py evaluate()` 加 `sellable_vol` 参数（can_use=0 不评估不并峰）+ 激活阈值 + 保本底线 `line=max(cost, high×0.92)`；
  3. G2 桥 `strategy_p16_g2_bridge_src.py` 同改（can_use 检查前移到 peak 更新前），`build/strategy_p16_g2_bridge.py` 重建 **BUILD_TAG=20260907-151202**；
  4. `scan_rotate_cost_real.py`：`TRAIL_ACTIVATE_PCT`（env BT_TRAIL_ACTIVATE 默认 0.08）+ 峰值 T+1 卫生（`(i-buy_i)>=2` 才并入当日 high）+ live_trail 分支激活/保本；
  5. `paper_forward_exit.py` 同改（前向基线同步）。
- **修复后 ablation（exit_ablation_20260907 重跑，报告已标注语义修复版）**：live_trail 红线58/N=10 日超额 +0.003%→**+0.147%**、回撤 -31.6%→**-18.9%**、Calmar 1.96；红线60/N=10 +0.015%→**+0.132%**、胜率 62.2%、Calmar 1.60；**fixed 基线四档与修复前逐行一致（无污染）**；ATR2.0(60/N=10) 旧 +0.199/t1.90 降至 +0.071/t0.75（旧优势部分源自峰值 bug，T-20260907-001 的前向对比仍有效但动机弱化）。逐笔 Welch：live_trail 60/N=10 +1.03pp/笔 t=0.79（不显著）。
- **待办（部署）**：①G2 桥 QMT 端重载 151202 产物（核对心跳 build_tag）；②V1.3 monitor 重启盯盘任务即可（无构建）；③次日 16:45 paper_forward_exit 自动按新语义跑。
- **遗留说明**：ATR 模式的 ATR_TRAIL（`peak>o_buy`）未加激活阈值（候选规则、波动率自适应），本次只修实盘在跑的 live_trail；如需一致可后续单列。

### V1.3 超买事故修复（2026-09-07，T-20260907-003，用户拍板）

- **事故**：09-07（到期制新代码首个实盘日）原有 300413+003005（09-04 买入）一笔未卖，又买 601999(6600@6.9)+601579(1900@23.96)，持仓 2→4 只，总占用 ~17.6 万 ≈ 资金池 95,897 的 **1.84 倍**（突破本金）。
- **根因（两层叠加）**：①卖出=到期制（T-20260904-001）→ 未满 N=5 的持仓保留，持仓不回落（原 PK_OUT 掉出即卖已删）；②买入无持仓数上限 + P0 校验只算"当日新增买入额"（POOL_BUY_CAP=资金池×0.95），不算"现有持仓市值" → 新票叠加、突破本金。
- **修复（rebalance_daily.py）**：①**持仓数上限**：买入名额 `slots = TOP_N − 卖出后仍持仓数`，新增买入（held_vol=0）按 total 降序取前 slots 只，补仓（已持有）不受限——对齐回测 simulate「持仓始终 ≤ TOP」；②**资金存量校验（校验3）**：保留持仓市值（未卖持仓 vol×现价，取价失败用成本兜底）+已买+本单 ≤ 资金池×deploy_pct×1.02，超限 POOL_BLOCK；③展示/落盘/live json 统一用 exec_buys。guard 幂等标记2 已能识别 BUY 成交，未改（0 卖出日由 json executed_live 标记兜底）。
- **验证**：py_compile OK；四场景逻辑测试（事故态 slots=0 → 0 新增；全到期卖2补2；卖1留1补1；target 已持有时拦截新票）；今日 dry-run 跑通（当前 4 只全持有 → 0 新增）。
- **生效**：rebalance_daily.py 为普通脚本（09:45 TRAE 任务 + 10:05 guard 直接调用），次日自动生效，无需构建/QMT 重载。
- **遗留**：今日 4 只存量不自动减，按到期制逐只满 N=5 自然消化（300413/003005 最早约 09-10 可卖）；如需人工回 2 只另行拍板。**纪律教训**：改卖出规则（删 PK_OUT/改到期制）时必须同步检查买入侧护栏（持仓数上限/资金存量），否则首日即暴露叠加超买。

### V1.3 QMT 内置风控裁剪（2026-09-07，T-20260907-004）

- **背景**：用户要求 V1.3 止盈止损从外部半小时快照（qmt_monitor 定时 13:30/14:00/14:30）升级为 QMT 内置 tick 级；G2 桥（70180771）已验证该路线（handlebar + get_full_tick + 三规则 + pending 状态机）。
- **已交付**：
  1. `strategy/strategy_p16_v13_risk_src.py`（1018 行，裁剪自 `strategy_p16_g2_bridge_src.py`）：
     - **保留**：止损-7%/止盈+15%/**修复版追盈**（TRAIL_ACTIVATE_PCT=0.08 激活阈值+保本底线+T+1 can_use<=0 不评估不并峰）、账户持仓全量纳管（防孤儿）、`_do_passorder`/`_lookup_order`/pending 状态机（300s/3次重试/废单死透确认/防双倍）、`risk_sold` 防重复、心跳+peak_v13 持久化、外部成本表读取；
     - **删除**：文件桥指令（orders/cancel cmd、seq、归档）、持仓/资产快照导出、换仓逻辑；ACCOUNT_ID=**67014907**、目录 `D:/QMT_POOL/p16_v13_risk`（与 g2_bridge 隔离）、remark=`P16V13风控`、文件前缀 `_v13_`。
  2. `build_p16_v13_risk.py`：Py3.6 语法扫描 + **MOCK 残留扫描**（AGENTS 红线）+ GBK + BUILD_TAG；产物 `build/strategy_p16_v13_risk.py` **BUILD_TAG=20260907-194446**（42369B，首行 # coding=gbk，账号 67014907，f-string 0）。
  3. `gen_positions_cfg_v13.py`：外部成本锚（qmt_trade_log.csv FIFO 含费成本 → `cmd/positions_cfg_v13_<date>.json`，因 QMT open_price 不可信 T-20260827-002）；试跑 20260907 输出 4 只持仓成本正确（003005 19.1048/300413 21.9955/601579 23.9662/601999 6.9018）。
- **待办（部署）**：①确认 67014907 有可加载内置策略的 QMT 客户端；②QMT 端加载 194446 产物（触发设 tick 级/高频），核对心跳 build_tag；③每日 09:00 定时跑 gen_positions_cfg_v13.py；④与 qmt_monitor 并行观察→稳定后停（防双进程重复卖出）；⑤插针：当前"触发即卖"（对齐 G2 桥），如需连续N tick 确认机制后续增强；⑥fills（state/fills_v13_<date>.json）回写 qmt_trade_log 的 reconcile 对接待建（对齐 G2 桥 qmt_bridge_client 消费模式）。
- **未 commit（等指示）**。**经验**：G2 桥风控模块是可直接复用的 tick 级风控资产，新策略做止盈止损优先裁剪它而非重写。

### 止盈线/追盈优先级敏感性回测（2026-09-08，T-20260908-001）

- **触发**：601999（出版传媒）09-08 13:30 在 +15.07%（成本 6.90 → 7.94）触发固定止盈卖出，当日后续涨停收 8.02——用户疑虑"止盈 15% 卖飞强势票，要不要重新评估"。iFinD 确认：09-07 买 6.90/收 7.29（+5.65%），09-08 最高 8.02 涨停（+10.01%），13:30 卖 7.94 确实不是最高点（少赚 ~1%/528元）。
- **回测**：`run_tp_sensitivity.py`（import 引擎+改模块属性，build_per_day 一次 + 18 组 simulate，约 5 分钟）矩阵 = 止盈线 15/20/25/30 × 纯追盈(TP=99%) × 追盈优先(TRAIL_FIRST)，红线 58/60、N=10、滑点 0.1%，与 20260907 ablation 同口径。
- **结论（数据）**：
  1. **现状 live_TP15（固定止盈15%+追盈8%双通道）两档绝对最优**：红线60 超额 +0.132%/日、回撤 -20.9%、Calmar 1.60、胜率 62.2%；红线58 +0.147%/-18.9%/1.96/57.4%。
  2. **止盈线上移单调负优化**：TP 20/25/30 → Calmar 红线60 0.91→0.40→0.42、红线58 0.30→0.26→0.32；TP 触发从 13 次降到 2~6 次。
  3. **追盈优先（trail_first）也负优化**：Calmar 红线60 1.60→0.66、红线58 1.96→0.83；TP 从 13 次消失（→1~2 次），回撤 -20.9%→-28.1%。
  4. **纯追盈** Calmar 0.50/0.68，同样差。
- **机制认知**：现状 TP 锁利 13 次 + TRAIL 吃趋势 13~17 次 = **双通道配合**，缺一不可；601999 是 13 次 TP 之一，统计上每次锁 +15% 是 Calmar 净贡献。"卖飞"是锁利通道的固有成本，单笔 n=1 不足以推翻。
- **引擎改动**：`scan_rotate_cost_real.py` 加 `BT_TRAIL_FIRST` 开关（默认关闭=原行为，验证与昨日基线逐位一致零影响）+ exec 循环打印出场原因（EXIT_REASON_CNT 每组前清空）；`run_tp_sensitivity.py` 驱动可复用（未来参数敏感性直接改 GROUPS）。
- **决策**：维持现状，不改任何出场参数。报告 `results/止盈追盈优先级敏感性_20260908.md`。**未 commit（等指示）**。

### G2 桥/V1.3 孤儿持仓自校准（2026-09-08，T-20260908-002）

- **发现**（用户部署 70180771 G2 桥 151202 后检查）：`D:/QMT_POOL/g2_bridge/state/peak.json` 两持仓（001266/300475）峰值全为 **0.0**。源码证据：peak=0.0 只出现在孤儿纳管路径（`_check_risk_signals` L963 `{"cost":0.0,...,"peak":0.0}`），而 L985-987 `if cost<=0: continue` 直接跳过风控评估 → **止损/止盈/追盈对孤儿持仓全部失效**。
- **根因（部署时序缺陷）**：成本表 `positions_cfg_<date>.json` 由外部 15:47 生成（盘后），而 QMT 内置策略**只在 init 读一次**成本表。当日盘中部署 → init 早于成本表生成 → 持仓走孤儿纳管（cost=0）→ 全天风控静默失效。
- **修复**：`strategy_p16_g2_bridge_src.py` + `strategy_p16_v13_risk_src.py` 各加 `_reload_positions_cfg(date)`——`_check_risk_signals` 纳管后检测存在 cost<=0 孤儿时，重读当日成本表填充 cost/peak（**30s 节流**，避免每 tick 读文件），并打印 `[P16G2/V13][CFG-RELOAD] xxx 孤儿校准 cost=.. vol=..`。全局加 `_g_last_cfg_load` 节流时间戳。
- **验证**：py_compile OK；逻辑测试 PASS（真实成本表：001266→34.0766、300475→170.1475 且 peak 初始化为成本；不在表内的 600000 不误校；<30s 第二次调用不重读=节流生效）。**注意 import 测试时须把 `strategy/` 子目录加入 sys.path**（源码在 strategy/ 下，模块名 strategy_p16_g2_bridge_src）。
- **产物重建**：G2 桥 `build/strategy_p16_g2_bridge.py` **BUILD_TAG=20260908-212526**（58088B）、V1.3 `build/strategy_p16_v13_risk.py` **BUILD_TAG=20260908-212526**（44274B），均 GBK 头、MOCK 0。**G2 桥需 QMT 端重载 212526 替换 151202**。
- **部署验收**（重要）：重载后核对 `D:/QMT_POOL/g2_bridge/state/peak.json` 峰值显示**成本值（非 0）**，且心跳日志出现 `[CFG-RELOAD]` 或 peak 值正常即生效；若仍为 0 说明成本表未生成或账号不匹配。
- **根治建议**：外部成本表生成改**每日 09:00 盘前**（当前 15:47 盘后生成，任何"当天盘中部署"都会踩时序坑）。

### V1.3 风控卖出接入 order_guard 委托守护（2026-09-09，T-20260909-001，方案A）

- **事故**（09-09 09:45 实盘）：qmt_monitor 触发 SELL_STOP 003005/300413，300413 全天近跌停（-9.46%），**卖单滞留挂单队列**；qmt_monitor 裸下单拿到 order_id>0 即 `sold.add` + `append_trade_rows` 记成交 → **挂单被误记成交、无撤单重挂**。10:30/11:00 盯盘显示 `300413 T+1锁定(可卖0)`——实为挂单占用可卖额度被误判 T+1。
- **根因**：V1.3 风控卖出（`qmt_monitor.py _sell`）是**裸 `order_stock` 下单即断连**，从未接入 `order_guard`；撤单重挂只存在于换仓路径（rebalance_daily/qmt_clear 用 order_guard）、G2 桥（pending 状态机）和已交付但**从未部署**的 V1.3 内置 tick 风控（`strategy_p16_v13_risk_src.py`，`QMT_POOL/p16_v13_risk/` 下无 state/ 心跳 = QMT 端未加载）。
- **修复（qmt_monitor.py，方案A）**：
  1. `_connect_trader()`：--auto-sell 时建立一条交易连接供 order_guard 复用（失败则本次仅预警不下单）；
  2. `_sell(trader, account, ...)` → `order_guard.order_with_guard`（下单→轮询60s→未成撤单→更新价格重试→最多5次→涨跌停跳过）；
  3. **记账修复**：仅 `r["traded_vol"]>0`（已确认实际成交）才写 qmt_trade_log；FILLED 全成→sold.add；部分成交（CANCELED_TIMEOUT traded>0）按真实量记账但不 sold.add（剩余下轮继续评估）；LIMIT_SKIP/REJECTED 0 成交 → **绝不记账**（防假成交）；
  4. 退出时统一 `trader.stop()`（--once/行情失败 break 均覆盖）。
- **验证**：py_compile OK；新增 `research/tests/test_monitor_sell.py` **19/19 PASS**（FILLED 记2000+sold / PARTIAL 记800不sold / LIMIT_SKIP 0成交绝不记账 / REJECTED 0成交不记账）。
- **待办（用户）**：①查 300413 挂单今日收盘是否已撤（若滞留明日开盘优先处理）；003005 已从持仓消失=大概率成交。②本改动生效=重启 qmt_monitor 定时任务即可（脚本直跑，无构建）。③中长期仍建议部署 T-20260907-004 的 V1.3 内置 tick 风控（更实时+自带 pending），并行观察后停 qmt_monitor。
- **未 commit（等指示）**。

### QMT 内置风控部署报错修复 + 300413 挂单处置（2026-09-09，T-20260909-002）

- **问题1（部署报错）**：用户部署昨晚 V1.3 内置风控（BUILD 20260908-212526）到 QMT，日志持续刷 `[P16V13][HANDLEBAR-ERR-HEART] FileNotFoundError: 'D:/QMT_POOL/p16_v13_risk\state\heart_v13_*.json.tmp'`。**根因**：`_atomic_write_json` 写 `state/` 前未确保目录存在，而 `p16_v13_risk/state/` 从未建过（对比 g2_bridge 有 state/）——部署时漏建目录 + 代码无自愈。
- **修复**：`strategy_p16_v13_risk_src.py` 与 `strategy_p16_g2_bridge_src.py` 的 `_atomic_write_json` 写前 `os.makedirs(dirname, exist_ok=True)`（自愈，一处修心跳/fills/peak 全部受益）；重建产物 V1.3 `build/strategy_p16_v13_risk.py` **BUILD_TAG=20260909-142712**、G2 桥 `build/strategy_p16_g2_bridge.py` **BUILD_TAG=20260909-143205**（均 GBK 头、无 MOCK）。
- **问题2（成本表缺失）**：今日内置风控 INIT `positions=0`——`cmd/positions_cfg_v13_20260909.json` 未生成（T-20260907-004 待办③"每日 09:00 定时跑 gen_positions_cfg_v13.py"尚未落地，cmd/ 下只有 20260907）。**已补生成**今日成本表（300413@21.9955 / 601579@23.9662）。
- **问题3（300413 双挂单）**：QMT 端 300413 出现两笔委托：①qmt_monitor 09:45 挂单 order=1090573035（status=50 未成交）→ **已程序撤单（status=54）**；②内置风控 14:34 SELL_STOP 挂单（passorder 反查 order_id=0，price=18.6 近跌停）→ **14:34:39 实际成交 deal=2000**（fills_v13 记 FILLED，QMT vol 归 0）。最终 300413 **已全部清仓**，无滞留挂单。
- **账本修复**：qmt_trade_log.csv ①删 3 条假记录（qmt_monitor 挂单未成交 1090573035 + 测试污染 123/456——test_monitor_sell.py 初版无 mock 误写盘，已修复测试并验证不再写盘）；②补记内置风控真实成交（14:34:39 SELL 2000@18.6 order=235）。**FIFO 复核与 QMT 账户一致**：601579=1900、300413=0、003005=0。备份 `.bak_20260909_cleanup`。
- **验证**：QMT 日志 14:28 后 HEART 错误消失；14:34:39 `[FILLED]` 正常。py_compile OK；test_monitor_sell.py 19/19 PASS（重跑确认 csv 行数不变）。
- **用户需做**：QMT 端重载 V1.3 内置风控新产物 142712（含 makedirs 自愈）；补建"每日 09:00 gen_positions_cfg_v13"定时任务（否则次日 INIT positions=0 风控空转）。
- **未 commit（等指示）**。
- **编号冲突提醒**：本条目与下方"重训失败完整复查"均标 T-20260909-002（各自独立记录，内容不同），后续引用请带标题区分。

### 外部定时风控降级为只读（2026-09-09，T-20260909-003）

- **背景**：miniqmt V1.3 内置 tick 风控（strategy_p16_v13_risk.py，BUILD 142712）已部署到 QMT 内置运行并验证（14:34 SELL_STOP 实际成交、心跳正常）。外部 6 个 Windows 计划任务（Quant_Monitor_0945/1030/1100/1330/1400/1430，均跑 run_scheduled.ps1 -Mode monitor）原带 `--auto-sell` 真实下单 → **与内置风控双跑=重复卖出风险**（2026-09-09 300413 双挂单教训：外部 09:45 挂单 + 内置 14:34 成交）。
- **决策（用户拍板：改只读监控）**：内置风控已完整覆盖外部功能（三规则/全量纳管/pending 状态机/峰值持久化，且 tick 级更实时），外部降为"报告+预警、不自动下单"的独立兜底视角，观察 1-2 个交易日内置稳定后再彻底停。
- **执行**：`run_scheduled.ps1` monitor 分支默认去掉 `--auto-sell`（只读），新增 `-AutoSell` 显式开关才恢复外部自动卖出；文件头用法注释同步。6 个计划任务参数未动（都只传 -Mode monitor → 自动降为只读）。PowerShell 语法校验 OK。qmt_monitor.py 只读路径已确认安全（trader=None 不连交易通道、auto_sell 块完全跳过、不写账本）。
- **待观察**：明日起看 6 个 monitor 日志 `[只读监控]` 字样 + 无自动卖出动作；内置风控连续稳定 1-2 交易日后彻底停外部（删除计划任务或改 Paused）。
- **未 commit（等指示）**。



## 2026-09-08 · G2-V1.0 上线：位置过滤 + 低开校验（阶段1 P0 落地）

- **依据**：三步验证（回测对照 红线60-full 日均超额 +0.125%/胜率61.7%/回撤-21.1% vs Base +0.015%/54.3%/-30.8%；实盘21笔回放 命中规则组-0.53% vs 未命中+2.11%；面板147万样本分层 高动量/高位/放量组胜率低4-5pp）。
- **T0** 新增 position_filter.py：R1追高(pre20>30%且量比>1.5)/R2高位(距20日高点>-2%且非温和放量突破)/R3低流动(5日均额<1亿)；lite=仅R2、full=R1+R2+R3；asof 语义取每股信号日特征；PF_DISABLE=1 一键回滚。
- **T1** deploy_predict_g2.py Step5 红线后接入过滤（G2 用 full）：实测 09-07 候选剔除 603551/002677/300836/301525 四只低流动票。
- **T3** 
ebalance_g2.py 买入循环低开校验（gap_guard）：极端低开<=-5%跳过（003005 型）、低开-5~-3%需量比>=2放量承接放行（601999 型）；单测两案例 PASS。
- **T7** g2_config.py/qmt_config.py 参数：POSFILTER_MODE（G2=full / V1.3=lite 预留）、GAP_HARD_PCT=-5、GAP_OPEN_PCT=-3、GAP_VR=2.0。
- **版本**：VERSIONS.md 登记 G2-V1.0；归档 versions/G2-V1.0_20260908/；改动前备份 *.bak_20260908；paper_forward_live.csv 重跑覆盖已从 git+原始记录修复。
- **待办（阶段2）**：T2/T4 V1.3 接入 lite+低开校验、T5 回测引擎 BT_POSFILTER 开关、T6 纸面管线开过滤 → 前向纸面累积 30 笔判定。


## 2026-09-08 · V1.4 上线：V1.3 链路位置过滤（lite）+ 低开校验（阶段2 P1 落地）

- **T2** `review_full.py` 输出清单前接入 lite 过滤（V1.3 红线58 仅 R2 高位剔除；full 在 58 下验证过严 +0.010% 不启用）。实测 09-08 真实候选 10 只剔除 601999（09-08 涨停+10% 创新高、pre20+33%，正确拦下高位追入）。
- **T4** `rebalance_daily.py` LIVE 买入循环低开校验（复用 position_filter.gap_guard：极端低开<=-5%跳过、-5~-3%需放量承接放行）；`fetch_ohlc`/`recent_avg_vol` 提为 position_filter 公共函数（腾讯行情实测正常）。
- **T5** `scan_rotate_cost_real.py` 加 BT_POSFILTER 开关（默认 off 官方报告不变）：复现 58-lite +0.157%/59.6%/-15.9%（与验证完全一致）、60-full +0.104%/62.5%/-25.7%（vs Base +0.015%）。
- **T6** `paper_forward.py` --replay 加过滤开关（BT_POSFILTER），回放与实盘候选同口径。
- **修复记录**：position_filter 数据窗口 2026→2023-06（回测期特征）、merge_asof 键精度统一 ns（datetime64[s] vs [ns] 冲突）、apply_rules 返回单值（scan_rotate/paper_forward 解包错误）。
- **版本**：VERSIONS.md 登记 V1.4（与 G2-V1.0 同日，共用 position_filter，分口径 G2=full/V1.3=lite）；归档 versions/V1.4_20260908/；备份 *.bak_20260908b。
- **待办（阶段3 观察）**：前向纸面（live 已带过滤）累积 30 笔判定；参数微调走 PF_* 环境变量。


## 2026-09-08 深夜 · 【重要修正】G2 位置过滤回退：g2_strong_real 口径验证过滤有害

- **触发**：用户质疑"G2 年化不如 V1.4 / 之前测的 70% 哪去了"。复核发现此前 vf_sim/T5 的 60-full（+0.125%/+0.104%）用的是 scan_rotate_cost_real 默认 v3_enh 模型套红线60，**并非 G2 模型**。
- **g2_strong_real 口径重跑**（红线60/N=10/0.1%滑点/可执行，BT_PANEL=feature_panel_v3_enh2_n3_bt、BT_MODEL=lgb_model_v3_g2_strong_real_20260825_1964t）：
  - Base（官方复现）：**+0.149%**/胜率55.9%/盈亏比1.97/回撤-16.3%/102笔 → 年化约 +45%
  - lite：+0.096%/55.6%/1.75/-16.3%/99笔（无增益）
  - full：**-0.108%**/48.8%/1.11/-29.0%/86笔（有害）
- **归因**：g2 模型（N3 对齐标签+真实 F2/F5）选出的强势票位置本身健康（09-07/09-08 候选均零剔除），过滤剔掉的是 g2 的盈利交易；v3_enh 候选含较多高位/追高票故 lite 有效（58-lite +0.157%）。
- **决策**：G2 默认【不过滤】（deploy_predict_g2 与 g2_config.POSFILTER_MODE 默认 ""，仅显式 PF_MODE_G2 启用）；低开校验保留（执行保护）；V1.3 链路 lite 保留。
- **年化澄清**：官方可复现 G2 = +0.149%日超额 → 年化约 45%；「75.88%」为更早流传乐观口径，V2.0收益真实性评估报告判定不可直接采信（归因链断裂/回测期=测试集/寻优未校正/样本外转负/实盘-1.49%），且 +0.149% 本就对应 45% 而非 75.88%。
- **教训**：过滤规则适用性强依赖模型，必须用各策略自己的模型+真实面板复核，不可跨模型推广（险些让 G2 上线有害配置）。
- **已同步**：VERSIONS.md G2-V1.0/V1.4 行修正、归档 versions/G2-V1.0_20260908 与 V1.4_20260908 同步最终版代码、变更说明 HTML v2。


## 2026-09-09 · G2 回退至 08-25 模型（T-20260909-001，09-07 周更判定失败）

- **触发**：用户要求复核 G2 43特征效果时发现 G2 live 指针已切到 09-07 周更版（3281树，train_g2.py --promote 正规写入 09-07 17:51）。
- **回测证据**（红线60/N=10/0.1%滑点/可执行/真实评分卡）：09-07 版仅 **+0.039%**/胜率52.6%，08-25 版（1964树）**+0.149%**/55.9%——日均超额缩水 74%，判定 09-07 周更失败。
- **连带发现**：V1.3 正式模型（lgb_model_v3.txt）也已是 09-07 周更版（27特征，未登记 VERSIONS），真实评分卡回测 Base **-0.045%**、lite **-0.002%**——09-07 周更对两策略均为失败重训（待决策回退或切 v3_enh）。
- **执行**：① `data/g2_live_model.json` 改写指向 08-25 模型/meta（旧值备份 .bak_20260909，note 标注回退原因）；② 用回退模型重算 09-08 候选（Top10 新旧差异 6 只，新候选含 300475 持仓，备份 selections/g2/_bak_20260909）；③ 09-09 早盘 rebalance 将使用回退模型候选；④ paper_forward_live.csv 重算前已备份 .bak_20260909。
- **验证**：g2_live() 返回 08-25；deploy_predict_g2 --date 20260908 重跑成功（F2 Tushare 主源+新浪兜底 100/100）。
- **待办**：V1.3 模型决策（v3_enh 33特征切换 or 回退 08-31 版 or 维持 09-07 版）；09-07 重训管线系统性复盘。


## 2026-09-09 · 重训失败完整复查（T-20260909-002，5 项结论）

- **① 门禁口径 IC（整体 Spearman, fwd_ret3, 同窗口 2024-07~2026-08-14）**：08-25=0.08157 > 09-07=0.07814。09-07 实为"更差"却被 promote——门禁比较的是历史不同滚动窗口记录，且整体 IC 微降即放行，**窗口不可比 + 只看全局 IC 是门禁盲区**。
- **② 交易级归因（简化轮动 TOP2/红线60/N10/open→open/成本0.1%x2）**：两模型 104 笔 vs 104 笔，Top2 重合仅 37 笔(35%)；总超额差 +0.44 全来自独有选股——08-25 独有 67 笔均值 +1.18%，09-07 独有 67 笔均值 +0.53%。**09-07 退化是系统性的（高分尾部排序质量下滑），非个别交易驱动**；整体 IC 是全局度量，掩盖尾部退化。
- **③ 同面板 33特征 vs 43特征 A/B（同一 enh2_n3_bt、同测试期）**：v3_enh(33特征) 对普通收益 IC=0.0317（>43特征的 0.0181）；g2(43特征) 对 N3 对齐标签 IC=0.0279（>33特征的 0.0183）。**两个模型针对不同收益口径各有优势，不能简单互相取代**；谁适合作 V1.4 模型需按目标口径 + 前向纸面判定。
- **④ 训练窗口/泄漏检查**：train 2020-01~2023-06 固定，valid 2023-07~2024-06，test 2024-07~最新（滚动）——无时间重叠泄漏；但**回测期(2024-07~2026-08-14) ⊆ 模型 test 集（双重使用，V2.0 报告已指出）**；train 集特征值随源数据刷新漂移（BASE_PANEL 每日刷新+龙虎榜/北向/研报后补），树量 1964→3281 反映训练非确定性，特征漂移疑为尾部退化来源之一（待深查）。
- **⑤ 门禁升级建议**：a) 候选 IC 必须与 live 在**同一测试窗口**重算对比；b) 增加**尾部质量指标**（prob>=60 分区 IC / Top2 模拟收益），不能只看全局 IC；c) 增加**可执行回测超额门槛**（N10/成本口径 候选>=live）；d) promote 后设**观察期回滚**（5 交易日劣于旧模型自动回退）；e) 修复指针 test_ic 基线（已补 08-25=0.08157）。
- **执行**：G2 已回退 08-25 模型（T-20260909-001）；指针 test_ic 修正为 0.08157；09-08 候选已用回退模型重算。


## 20260909 · 三任务收尾（T-20260909-003/004/005）

- **T-20260909-003 门禁升级（G1-G4）**：train_g2.py 门禁重写为 strict（默认）——G1 候选与 live 同 test 窗口重算对比（修复滚动窗口基数不可比）；G2 尾部 IC(prob>=60) 对比（尾部样本不足自动跳过）；G3 TOP2 可执行模拟收益对比（open→open/成本0.1%x2，build_panel 新增 open10_ret 列）；G4 观察期回滚——promote 写入 trial 字段（10 交易日），配套 rollback_check_g2.py（只读检查/--apply 执行回退，meta 按模型文件名推断）。旧门禁保留 --gate lenient。live 指针 test_ic 已修正为 08-25 实测 0.08157。
- **T-20260909-004 纸面 A/B 上线**：deploy_predict_g2 新增 --ab（默认关），同一快照/评分卡/红线用 v3_enh(33特征) 额外出候选，写 paper_forward_ab_v3enh.csv（幂等）。首日(09-08)：G2 与 v3_enh 候选 10 只仅 1 只重合(300475)，分歧巨大，需前向数据判定。
- **T-20260909-005 V1.4 模型决策证据**：同面板(enh2_n3_bt)/同测试期/同可执行模拟对比——整体IC：G2 08-25=0.0816 > v3_enh=0.0543；尾部IC(>=60)：0.115/0.108 接近；**TOP2 可执行模拟：v3_enh +0.0298 > G2 08-25 +0.0140（约2倍）**。V1.4 采用 v3_enh(33特征) 有可执行口径支撑；但 v3_enh 在 v3_sc 面板(close→close 口径)与 enh2_n3_bt 面板(测试期 2026-08-14)结果差异大，生产切换前需纸面 30 笔证据。决策路径：先 A/B 纸面（今日起）→ 30 笔后定模型 → 再动生产（deploy_predict 需支持 33特征）。
- **连带**：特征漂移深查结论——27 基础特征训练期(2020-01~2023-06) 207 万行零漂移；enh 慢变量(build_panel tail(1) 最新asof)随训练日期漂移且与回测面板(enh2_n3_bt 逐行asof)口径不一致，为 09-07 重训尾部退化诱因之一（待按逐行 asof 重构 build_panel）。


## 2026-09-09 · 融合验证结论修正（T-20260909-006，简化模拟误导教训）

- **背景**：简化逐日净值模拟（无止损止盈/无涨跌停可执行过滤）曾显示 rank 融合(v3_enh+G2,w=0.5) 年化 +73.8% 远超 G2 单独 +32.9%，触发"强强联合"验证。
- **官方引擎口径（N=10/0.1%滑点/可执行/止损-7%止盈+15%/红线60）w 扫描**：
  - w=0.3: +0.153% 胜率53.3% 盈亏比1.83 回撤-27.7%
  - w=0.5: +0.098% 胜率57.3% 盈亏比1.57 回撤-21.7%
  - w=0.7: +0.029% 胜率52.5% 盈亏比1.55 回撤-25.9%
  - G2 单独: +0.149% 胜率55.9% 盈亏比1.97 回撤-16.3%
- **结论修正**：官方口径下融合**无实质优势**——最优 w=0.3 仅 +0.004pp（可忽略）且回撤 -16.3%→-27.7%、胜率下降、盈亏比 1.97→1.83。**融合不采用上线，G2 单独（+0.149%）保持**。
- **教训（重要）**：简化模拟（无止损/止盈、无可执行过滤）系统性高估"稳健融合类"、低估"高盈亏比动量类"（止损止盈是后者的放大器）。**任何收益结论必须用官方引擎口径复核**；简化模拟仅可用于方向提示。
- **落地保留**：引擎 BT_ENSEMBLE 融合开关（默认关）、deploy_predict_g2 --ensemble 纸面臂（已实现，继续低成本累积作为旁证，预期大概率不优于 G2）、paper_forward_ab_stats.py 三臂统计（累积中）。纸面三臂最终以官方口径+前向数据为裁判。


## 2026-09-09 · 简化模拟 vs 官方引擎差异归因（T-20260909-007，G2 vs 融合 结论反转解剖）

- **背景**：融合(w=0.5)在简化净值模拟 +0.22%/日（≈+74%年化）远超 G2（+0.11%/日），官方引擎却相反（fixed：G2 +0.149% > 融合 +0.098%）。质疑是否引擎 bug。
- **隔离实验（官方引擎 EXIT=none 纯到期）**：
  - G2 单独 EXIT=none = +0.100%（vs 简化模拟 G2 ≈+0.11%/日，**对账吻合 → 引擎无 bug**）
  - 融合 EXIT=none = -0.024%（vs 简化模拟融合 +0.22%/日，**仍差 0.24pp**）
- **归因（两因素）**：
  1. **止损止盈（部分）**：fixed 下 G2 +0.149%（从 +0.100 提升 +0.049pp）、融合 +0.098%（从 -0.024 提升 +0.122pp）——止损对 G2 增益是融合的 4 倍（G2 高盈亏比票在止损框架下优势放大）。
  2. **选股机制（主因）**：官方引擎选股由**评分卡 total_new 主导**（prob Top100 池 → Top10 → 评分卡红线 → 按评分卡取 TOP2），模型分只决定"进不进池"；简化模拟由**模型融合分直接选 Top2**。融合改变的 Top100 池在评分卡框架下更差，且融合对最终持仓的影响力被评分卡稀释。
- **教训升级**：简化模拟缺失"评分卡选股层"与"止损止盈"，两类差异都会导致结论反转；**简化模拟仅可用于方向粗筛，收益结论一律以官方引擎（scan_rotate_cost_real，EXIT=fixed）为准**。已登记 VERSIONS 口径基准：官方 G2 = +0.149%（红线60/N10/0.1%滑点/可执行）。


## 2026-09-09 · 配置扫描：G2 vs 融合 各自最优对比（T-20260909-008，用户方法论修正）

- **背景**：用户指出"用同一套回测配置对比两个不同取向的策略 = 配置应试"，要求各自找最优配置再比。
- **扫描矩阵（官方引擎，红线60/TOP2/滑点0.1% 默认，测试期 2024-07~2026-08）**：
  | 出场 | G2-N5 | G2-N10 | G2-N15 | 融合-N5 | 融合-N10 | 融合-N15 |
  |---|---|---|---|---|---|---|
  | fixed | +0.095% | +0.149% | +0.206% | +0.040% | +0.098% | -0.013% |
  | live_trail | +0.126% | +0.119% | **+0.213%** | +0.048% | **+0.211%** | +0.128% |
  | none | — | +0.100% | — | — | -0.024% | — |
- **敏感性（live_trail）**：红线 55/58/60/65——G2-N15: +0.129/+0.206/+0.213/-0.060；融合-N10: +0.183/+0.206/+0.211/+0.111。TOP2/TOP3——G2-N15: +0.213/+0.138；融合-N10: +0.211/+0.131。
- **结论**：
  1. **各自最优几乎打平**：G2-live_trail-N15-红线60-TOP2 = +0.213%（盈亏比2.70）vs 融合-live_trail-N10-红线60-TOP2 = +0.211%（盈亏比1.75）。融合非"全方位落后"，而是**配置敏感**（live_trail 强、fixed 弱；G2 对出场规则不敏感）。
  2. **最优配置稳健**：红线 58-60 平台（+0.20%），TOP2 > TOP3，非尖峰；红线 65 双崩（G2 -0.060%）。
  3. **融合在红线65 更抗跌**（+0.111% vs -0.060%）——融合池质量对高红线更稳健，这是融合的独特价值点。
  4. **实盘优化点**：G2 实盘当前 N10-fixed（+0.149%），扫描最优 N15-live_trail（+0.213%）；融合若启用需 live_trail-N10。
- **方法论固化**：多策略对比必须"各自最优配置 + 稳健性检查（邻域平台非尖峰）+ 前向验证"，禁止单配置应试。
- **候选上线配置**（待前向验证 + 实盘出场规则对齐）：G2 = live_trail/N15/红线60/TOP2；融合 = live_trail/N10/红线60/TOP2。
- 纸面三臂按各自最优持有期回填（ab_stats v2：G2=N15、v3_enh/融合=N10）。


## 2026-09-09 · 网格化扫描：V1.4/G2/融合 全配置寻优（T-20260909-009，用户"不放过任何可能性"）

- **背景**：用户要求结合开发经历，对 V1.4/G2/融合 在不同配置下做网格化寻优——持有时间、持仓只数、止盈止损策略全维度跑，同时强调防过拟合、数据真实。
- **S1 粗扫（官方引擎，滑点0.1%/可执行/止损-7%止盈+15% 基准，测试期 2024-07~2026-08）**：
  - **V1.4（v3_enh+lite，红线58）完整 N×EXIT 矩阵**：N5 fixed -0.003%/lt -0.030%；N10 fixed **+0.157%**/lt +0.114%；N15 fixed +0.087%/lt +0.114%；N20 fixed +0.002%/lt +0.094%。**N10-fixed 为峰（+0.157%），长短两端衰减，V1.4 是短中持有型**。
  - **G2（红线60）补 N20**：fixed +0.027%/lt +0.022%（远逊 N15-lt +0.213%，长持有端 G2 衰减）。
  - **融合（w=0.5，红线60）补 N20**：fixed +0.088%/lt +0.141%（N10-lt +0.211% 仍最优）。
- **S2 组合微调（各策略最优持有期处扫 TOP/红线邻域）**：
  - V1.4-N10-fixed：红线55 +0.009%/58 +0.157%/60 +0.063%/65 +0.077%（58 峰）；TOP1 -0.003%/TOP2 +0.157%/TOP3 +0.142%（TOP2 峰）。
  - G2-N15-lt：TOP1 **+0.245%**（38笔）/TOP2 +0.213%/TOP3 +0.138%——TOP1 表面更高但样本仅38笔。
  - 融合-N10-lt：TOP1 **+0.290%**（55笔）/TOP2 +0.211%/TOP3 +0.131%——TOP1 表面更高但样本仅55笔。
- **S3 防过拟合（时间分段稳健性，前段2024-07~2025-06 / 后段2025-07~2026-08）**：
  - **TOP2 全稳健**：G2-N15-lt 前+0.095%/后+0.353%；融合-N10-lt 前+0.186%/后+0.130%；V1.4-N10-f 前+0.225%/后+0.156%。**三策略 TOP2 前后段超额均正**。
  - **G2-TOP1 证伪**：前段 **-0.016%**/后段+0.536%（前段转负）→ TOP1 高超额是过拟合尖峰，**不可上线**。
  - **融合-TOP1 存疑**：前+0.358%/后+0.122% 均正但每段仅28笔，样本不足，列为观察不主推。
- **结论（防过拟合后的最佳推荐）**：
  1. **G2 = live_trail/N15/红线60/TOP2（+0.213%/日，盈亏比2.70，回撤-16.7%，前后段+0.095/+0.353）**——胜率/赔率均衡，时间分段最稳。
  2. **融合 = live_trail/N10/红线60/TOP2（+0.211%/日，盈亏比1.75，回撤-17.2%，前后段+0.186/+0.130）**——与 G2 打平，高胜率低赔率型。
  3. **V1.4 = fixed/N10/红线58/TOP2+lite（+0.157%/日，盈亏比1.67，回撤-15.9%，前后段+0.225/+0.156）**——回撤最小，但超额低于前两者。
  4. **TOP1 全部不采用**（G2 前段转负证伪；融合样本不足）——宁可低一档超额也要稳健，防过拟合红线。
- **过程教训**：第一批 V1.4 网格曾遗漏 BT_POSFILTER=lite（误报 +0.029%），复跑修正为 +0.157%——**网格批次必须显式声明位置过滤开关**，否则 V1.4 官方口径失真。
- **候选落地**：G2 维持 live_trail/N15/红线60/TOP2 待前向验证；融合/V1.4 已具备可比回测基准，最终以纸面前向三臂（G2=N15、v3_enh/融合=N10）为裁判。


## 2026-09-10 · 各策略最优配置年化折算 + 纸面前向首批到期（T-20260910-001）

- **年化折算（官方口径，日超额复利 250 交易日，锚点 +0.149%→45.1% 已验证）**：
  - G2 live_trail/N15/红线60/TOP2：+0.213%/日 → **年化约 70.2%**
  - 融合 live_trail/N10/红线60/TOP2：+0.211%/日 → **年化约 69.4%**
  - V1.4 fixed/N10/红线58/TOP2+lite：+0.157%/日 → **年化约 48.0%**
  - 注：此为官方引擎回测口径复利年化，仍受测试集双重使用局限（T-20260909-001），最终以上线后实盘为准。
- **纸面前向三臂首批到期（交易日到期口径修正后）**：
  - 修复：paper_forward_ab_stats v2 用自然日 hold*2+2 判断到期 + ashare_trade_dates.txt 日历陈旧（仅到 08-20）→ 误判 0 到期；改用 data_live/merged_daily_full.parquet（最新 09-09）+ 交易日到期口径。
  - **G2(live,N15) 首批 4 笔到期：均值 -0.0715%、胜率 25%**——300201 两笔 -13.4%/-12.8% 重创（08-17/08-18 信号），002846 +1.9%、603155 -4.3%。样本外开局不佳，与回测乐观口径形成反差，**持续观察，暂不据此改配置**（样本仅 4 笔）。
  - v3_enh(N10) 候选 20 笔、融合(N10) 候选 2 笔均未到期（信号自 09-08 起，N10 需到 09-22+ 才到期）。
  - 待办：merged_daily_full 需每日刷新（refresh_panel_v3 或增量合并），否则纸面收益回填滞后。
