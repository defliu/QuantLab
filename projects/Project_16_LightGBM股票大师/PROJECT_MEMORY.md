# Project\_16 项目记忆（自 trae\_workspace 迁移）

> 本文件由 `D:\trae_workspace` 工作区的项目记忆 `project_memory.md`（2026-08-25 迁移至 QuantLab）整理而来，用于在 QuantLab 工作区继续研发本项目时无缝继承历史上下文。
>
> **优先级说明**：本文件是历史研发记忆，**如与** **`D:\QuantLab\AGENTS.md`、`config/trading_config.yaml`** **或最新代码冲突，一律以 AGENTS.md / 配置 / 代码为准**。
>
> **账号分工（2026-08-25 用户确认，双账号并存、各跑各的）**：`67014907`（旧号，miniQMT/xtquant 委托，跑本项目 Project\_16 策略，`qmt_config.py` 的 `ACCOUNT_ID`）；`70180771`（新号，纯 QMT 端跑 ATR低波/价值小盘V2 等策略）。本文件的 `67014907` 绑定记录有效，非停用账号。
>
> 🚫 **`70180771` 未开通 miniQMT（xtquant）（2026-10-22 用户确认 + 实测）**：外部 Python `connect()` 返回 0 但 `subscribe()` 返回 **-1**、`query_stock_asset()` 返回 **None**、持仓/委托/成交全空。**不是链路故障**，是该号无 miniQMT 权限。禁止再用 external xtquant 连它做连通性排查；该号策略只走 QMT 内置引擎 + 文件桥。
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

### 2026-09-29 开盘复核：0 下单 + early return 抑制到期卖出（P0，**已于 2026-10-02 修复 T-20261002-001**）

* **今日结果**：交易日放行；D=20260928（面板最新日）；`D_model_top10.csv` 别名陈旧(0924)已备份 `.bak_stale_20260924` 并同步为当日候选；F2/F3/F5 取数 10/10 落 `data/tdx_review.json`；`market_tier.py --json` → **tier=2**（沪深300 -0.19%）→ eff_top=5；`rebalance_daily.py --date 20260929 --top 2 --live` **已调用一次**，因「过红线(>58)的票为 0」在**成熟度判定之前** return → **0 买入 0 卖出 0 委托**，资金池 89,247.77 元未动用。只读反查 miniQMT 确认：今日成交记录新增 0 行、`traework_rebalance` 委托 0 笔（无重复/半单风险）。
* **P0 流程缺陷（✅ 已修 T-20261002-001，2026-10-02）**：`rebalance_daily.py` 的 zero-target early return 把**买入拦截与到期卖出一起短路**。今日实证 2 只持仓已满 `HOLD_DAYS=5` 且不在候选清单内，按到期制本应 `reason=MATURE`/`remark=planA_mature` 卖出却被抑制：603049.SH(900股,9日)、603237.SH(2800股,8日)。**修复**：early return 改为置 `buy_allowed=False`，继续走持仓/成熟度判定；`slots` 计算后新增买入闸门把 `exec_buys` 压到 0，`sell_plan`/`trim_orders` 完全不动（与既有「纸面降级闸」同范式）。回归测试 `research/tests/test_rebalance_sell_priority.py`（S1~S6 共 18 项，含 T=0 清仓不回归、到期制保留、target 内不误卖），配套 `test_order_guard_backfill.py` 19 项全绿。
* **两只票实际已正常卖出（事后核实，2026-10-02）**：`qmt_trade_log.csv` L123/L129 显示 603049.SH(900股@43.89) 与 603237.SH(2800股@15.2418) 均于 **2026-09-30 10:08/10:09 按 `MATURE` 成交**（09-30 清单 7 只过红线 → 走正常路径，`rebalance_20260930.json` sell 段完整）。即滞留仅 1 个交易日、未演变为无限期，属流程缺陷而非资金损失。603237.SH 当时 `guard_note=CANCELED_TIMEOUT(重试10次)` 但实为全成交，已由只读对账补记（`manual_reconciliation` 区块 + L129 账本行）。
* **教训（固化进 AGENTS 认知）**：**买入拦截与卖出执行必须是两条独立通道**。任何「无候选 → 不买」的短路都不得放在持仓成熟度判定之前。同类隐患排查口诀：在 `rebalance_daily.py` 里凡是 early `return`，先问它是否落在「持仓读取 → 成熟度 → sell_plan → 委托」链路之上。
* **脚本副作用（历史，修复前）**：旧版 zero-target early return 位于 live artifact 写入之前 → 2026-09-29 当日**没有**生成生产 `rebalance_<date>.json`；旧 TRAE prompt 随后要求 agent 补写同名文件，造成非生产字段污染（`rebalance_20260929.json` 的 `executed_live=false` + `live_invoked=true`），亦使 `.de_bak.json` 备份与污染文件完全相同、无法用于恢复；候选输入只能靠 `data/candidates_asof/` 旁路文件还原。
* **脚本副作用（现状，T-20261002-001 修复后）**：红线空仓日 live 路径照常写 `rebalance_<date>.json`（含完整 `candidate_*` 溯源字段）；dry-run 独立写 `rebalance_<date>.dryrun.json` 防覆盖 live 记录；**本脚本不写 `.md`**。若 agent 侧另需留档，走 `rebalance_<date>.trae_note.json` 旁路，不得写生产 plan JSON。
* **飞书推送教训**：任务书硬编码的 `lark\1.0.4\bin\lark-cli.exe` 在本机**不存在**，统一出口 `lark_push.resolve_cli()` 实际解析到 `C:\Users\Administrator\bin\lark-cli.exe`（告警卡与纯文本摘要均经此路成功送达）。`config bind` 需用户确认身份策略，未擅自绑定。**step5 私聊文本应直接用 `lark_push.py --file <txt> --user-id <open_id> --as bot`**，勿自己拼 lark-cli 命令行。
* **路径腐化实测（2026-10-02 现场核实）**：`1.0.4` 目录确实已不存在，插件已升级到 `1.0.5`（`...\trae-remote-official\lark\1.0.5\bin\lark-cli.exe`）——**再次印证「硬编码带版本号的插件路径必然随升级腐化」**。权威出口 `C:\Users\Administrator\bin\lark-cli.exe` 存在=True；`lark_push.py --check` 自检输出 `[自检] lark-cli: ...（来源=probe）存在=True`，可自动探活。
* **TRAE 任务 15868a74 prompt 现场回读（2026-10-02，以 Schedule get 为唯一准绳）**：✅ 已生效 = step4 只读禁写 plan json、step4b 溯源核验、step4c 账实不符 6 步对账协议、备注分流 `.trae_note.json`、方案 B 产物隔离。❌ 仍残留两处过时：①step4 末尾仍写「红线空仓/候选缺失时脚本在写文件之前早退」——该 early return 已由 T-20261002-001 删除，live 路径任何非致命情况都应写出 plan json，旧文案会让 agent 把异常误判为正常而漏报；②step5 仍钉死 `1.0.4` 路径（见上条）。**本次 Schedule update 第 4 次仍在用户确认弹窗阶段等待 10 分钟后被自动跳过，更新后立即回读确认两处旧文案均未改动 → 未生效，不得据回执声称成功**（历史状态，该两处残留已于第 8 次修订落地，见下条「✅ 已关闭」）。影响可控：step5 失效仅致飞书推送失败（prompt 已规定「推送失败仅记录，绝不阻塞主流程」），不危及下单；step4 旧文案风险为异常漏报，「禁止重复下单」与 4c「严禁重下」条款仍在。
* **【重要·机制结论 + ✅ 已关闭】Schedule update 必须有人守弹窗；第 8 次成功落地（2026-10-02）**：**失败史（保留作教训）**——第 4、5 次先 `Schedule get` 取**现场完整 prompt 原文**、基于原文做三处最小修订（step3 增补卖出优先级红线 / step4 改为带 4c 例外的只读规则并新增 step4a 只读诊断顺序 / step5 改走 `python lark_push.py`），**不凭记忆改**，发起 update 后**均停在确认弹窗等待 10 分钟被自动跳过**；每次失败后立即 `Schedule get` 回读逐字比对（zero-target 旧句仍在、`1.0.4` 路径仍在、无 step 4a），**不存在部分写入**。连同 09-29 的 3 次，**跨会话累计前 7 次失败点完全相同 → 判定为机制约束（update 必须经用户在弹窗内显式确认，超时整体跳过），而非文案问题**。**第 8 次做法**：先问用户是否方便守着弹窗，用户答「立即执行」后再发起 → **当场成功**（`Schedule '开盘实时复核(项目16)' updated successfully.`）。**✅ 验收（独立 `Schedule get` 回读，不采信 update 回执）8/8 通过**：zero-target 旧句消失 / `1.0.4` 硬编码可执行路径消失 / step 4a 存在 / `python lark_push.py` 存在 / step 3 卖出优先级红线存在 / step 4 只读规则含 4c 白名单前置例外 / step 4b·4c + 方案 B + 模型降级条款完整 / cron `45 9 * * MON,TUE,WED,THU,FRI`、Active、执行次数 24、next run `2026-10-05T01:46:00+00:00` 均未变。**据此关闭 T-20261002-002**。**教训（取代原「第一次超时即改走人工路径」）**：发起需确认弹窗的工具动作前，**先问用户是否在场守着弹窗**，不要在无人值守时连发重试空耗；**TRAE 侧修改由 agent 直接 `Schedule update` 完成，用户只需在弹窗点确认**——需用户手工粘贴的**只有 QMT/miniQMT 客户端的策略编辑器部署**（自绘 UI，粘贴是设计内人工动作），两者不可混为一谈。**修订后必须 `Schedule get` 独立回读才可声称成功**（「文档说已改」≠「任务实际已改」）。存档：完整 prompt 落 `specs/TRAE任务_prompt全文_待粘贴_20261002.txt`（已生效存档，文件名「待粘贴」为历史遗留）、现场记录 `data/rebalance_20260929.trae_note.json` 的 `verification_20261002_get` 区块、详见 `specs/TRAE任务修改说明_0945开盘实时复核_20260930.md` §十。
* **10-02 当日核实（2026-10-02，法定节假日休市）**：`is_trade_day.py` 输出「2026-10-02 A股交易日: 否（法定节假日休市）」，故 09:45 任务（01:47:41Z 启动）第一步即正常退出，**当日无 `rebalance_20261002.json` 属预期而非缺陷**。另注：`rebalance_20260929.dryrun.json` 的 mtime 为 10-02 10:01，但其 `date` 字段是 `20260929`、`hold` 是当前 QMT 持仓——系**历史日期 dry-run 重放**（用当前持仓重放不会复现历史卖单），非当日产物；且 dry-run 写独立文件不覆盖 live json，无污染。**版本时序留痕**：`rebalance_daily.cpython-310.pyc` 编译于 01:00:12，而修复后源码 mtime 为 10:11:32 → 该次运行加载的是**修复前版本**，但因休市未进入换仓逻辑，无实际影响。下一交易日（10-05 起，节后）才是修复后代码的首次真实运行验证窗口。
* **数据源当日实证**：`mcp_tdx` list tools failed（已降级）；东财 F3 旧接口 404 → 换 `search-api-web.eastmoney.com/search/jsonp` 后 10/10；东财扩展接口 `RemoteDisconnected` → 量比回退腾讯 f[49] 补齐 10/10；悟道盘中熔断 → F5 双源对拍未完成（清单 10/10 带警示）；F2 主源 Tushare T-1 本地 parquet 正常（刷新至 2026-09-28）。
* **流程纪律**：候选别名文件**非空≠新鲜**——必须校验 `trade_date` 与面板最新日一致再用，否则会用 3 日前的候选下单（今日实证 0924 vs 0928）。

### 同源缺陷全库审计 + 飞书配置漂移订正（2026-10-02，T-20261002-003）

* **背景**：继 T-20261002-001（P0 zero-target 短路卖出）修完后，对全库做「还有没修的地方」的定向审计，目标是不复现同一类缺陷。
* **G2 / 融合（ENS）Python 源码审计结论=干净，无需改动**：`rebalance_g2.py` 与 `rebalance_ens.py` 的 `build_plan()` 均是「先独立从 ledger 生成到期/调出 SELL → 再按 `n_slots > 0 and pool and deploy_pct > 0` 决定买入」，**`pool=[]` 不会阻断卖出**，也没有候选为空时的裸 `return`。→ **同源缺陷不在源码，在调度层**。
* **真正的 P0 在两个 Active TRAE 任务的 prompt（已修，T-20261002-003）**：`G2换仓(项目16大QMT)`=30344e79（cron `50 9 * * MON,TUE,WED,THU,FRI`，23 次）、`融合换仓(项目16大QMT)`=051bc2d9（cron `52 9 * * MON,TUE,WED,THU,FRI`，16 次）。两处 prompt 各有 2 个缺陷：
  1. **候选文件名错**：写「勿因找不到 `<今日>_g2_top2.csv` 而误判」，但 `g2_config.SELECT_TOP=10` / `g2_ens_config.SELECT_TOP=10`，实际产物是 `<今日>_g2_top10.csv`；且把 `TOP_N`（G2=3 / ENS=5，目标持仓数）与候选数 10 混为一谈 → 已改为 top10 口径并显式区分两个概念。
  2. **候选异常连带中止卖出（P0，与 001 同源）**：原句「若输出候选缺失/数据日落后/宁缺毋滥类报错 → 推告警后**直接中止并报告**」会在候选为空时把 SELL 一起拦掉 → 已改为「此类字样只说明本日**不新增买入**，绝不说明不卖出」「**卖出照常执行**的判定依据 = dry-run 输出中存在 SELL 记录」「只允许在 ①崩溃 traceback ②桥不存活 ③plan json 缺失/损坏 三种情形中止」，并在 live 步骤补「即使本日 0 ，只要有 SELL 也必须 `--live` 提交，否则到期卖出会被滞留」。
* **教训（机制级，取代一切 prompt 文案修补的临时说法）**：**「不用过期候选交易」这条约束，由「空候选池→不新增买入」天然保证；不需要、也不允许用「连卖出一起停」来落实它。** 买入拦截与卖出执行是两条独立通道，调度 prompt 必须显式写明三段语义：①候选异常的准确含义（只影响买入）②卖出照常的判定依据③允许中止的**穷举清单**（不含业务性字样）。凡是 prompt 里出现「遇 X 情况直接中止」而 X 属于**业务语义**（而非基础设施故障），就要怀疑它是否误伤了卖出链路。
* **飞书配置漂移订正（3 份现行文档 + 3 个死脚本）**：①`open_id` 唯一事实源 = `qmt_config.py`/`g2_config.py`/`g2_ens_config.py` 的 `FEISHU_OPEN_ID` = **`ou_bd13444d8ea53c28249c669f43f3eeff`**（bot `cli_aade2524ddbd9bd6` 下刘诚，2026-08-29 实测可私聊）；旧值 `ou_76deaecde50e10576f8fdc8ba954a7b0` 属 **`cli_aa0fbe282c399cef` 旧应用**，跨应用发会报 `open_id cross app`，已废弃。②bot appId 亦漂移：文档曾写回退 `cli_aa0fbe282c399cef`，**实读 `~/.lark-cli/config.json` 证伪——该文件只注册了 `cli_aade2524ddbd9bd6` 一个应用**，按旧 appId 排查会走错方向。③lark-cli 路径一律改为稳定出口 `C:\Users\Administrator\bin\lark-cli.exe` + 统一包装器 `lark_push.py`（`--check` 自检），**禁止硬编码 `...\plugins\...\lark\1.0.x\...` 版本目录**（1.0.4 → 1.0.5 已实证腐化两次）。
* **死脚本处置（不删，加废弃头）**：`push_lark.ps1`、`push_lark.bat`、`scripts/push_auction_summary.ps1` 全库无调用方且硬编码 1.0.4 版本目录，误调用必然失败 → 保留文件（历史推送身份验证记录 + 失败史证据），在文件头加「[已废弃 2026-10-02] + 正确替代命令 + 保留原因」，正文原路径原样留作证据。含中文的 `.ps1` 重写时用 `UTF8Encoding(true)` 保住 BOM，并用 PowerShell AST Parser 校验语法 → `bom=True parseErrors=0`。
* **已排除的伪问题（省下次重复排查）**：①`rebalance_20260930.json` 与 `.scriptorig` 的差异是**人工对账区块**（`manual_reconciliation`），属有效事故证据，**不是污染**；②`.trae_note.json` 是会话级旁路备注，绝不可写进生产 plan JSON（`rebalance_20260929.json` 曾因此被 `executed_live=false` + `live_invoked=true` 污染）；③`__pycache__/*.pyc` 已被 `.gitignore` 覆盖，非现行缺陷。
* **双调度器核实（AGENTS 双调度器红线）**：用户确认 **WorkBuddy 侧当前没有任何任务、且未介入交易体系** → 现存换仓调度唯一 owner = TRAE（30344e79 / 051bc2d9），`D:/QMT_POOL/` 产物**不存在双写**。此结论有时效性，未来若在 WorkBuddy 侧新建任务必须先互查。
* **未执行的动作（本轮刻意不做）**：未运行任何 live 换仓、未触发 QMT 委托、未重复提交 603049.SH/603237.SH 卖单、未 `git commit`、未用飞书消息做无谓的实盘验证；验收一律以独立 `Schedule get` 回读为准，不采信 update 回执。
* **终检补漏（第二轮 grep + 实读码，抓到 3 处活文档/脚本残留）**：
  1. `G2_RUNBOOK.md` 第 27 行仍把现行候选写 `data/selections/g2/<date>_g2_top2.csv`，而同文件第 129 行已写明现行产物是 top10 → **活文档自相矛盾**，已订正为 `_g2_top10.csv`。
  2. `QMT避坑指南.md` 一条排障说明仍以旧 open_id `ou_76de...` 为操作对象（"不要用 user 身份发 `ou_76de...`"）→ 会引导读者继续用废弃 ID，已改为 `ou_bd134...`。
  3. **一次性高风险脚本 `g2_correction_exec.py` 只加注释不够，已加运行时 fail-closed 门禁**（详见下条）。
* **【重要·纠正我自己的错误结论】`g2_correction_exec.py` 的废弃注释首版写「直接跑必 `[ABORT]`」是错的，实读码后订正**：①`deploy_predict_g2.py` 第 317 行按 `f"{date_str}_g2_top{args.top}.csv"` 拼名，`--top 2` **仍会产出** `_g2_top2.csv`，不会因文件名缺失而中止；②`data/selections/g2/` 下**留有 25 个历史 `_g2_top2.csv`**（最新 `20260922`），若重跑 `deploy_predict_g2` 异常但 rc=0，第 121 行 `sorted(...)[-1]` 会**静默取到一个月前的陈旧候选**；③`SELL_HINTS` 守卫**不构成保护**——`_known_volumes()` 读不到桥持仓时 `return out or fallback` **回退硬编码 6400/1000 股**，卖单照常生成；④第 10 步会覆写 `g2_hold_dates`（以 `R.load_g2_ledger` 为 active 基集）→ **污染现行 G2 账本**。真实风险是「误用陈旧候选 + 按硬编码数量真实写大 QMT 桥」，不是「跑一下就中止」。
  - **处置（双层门禁，注释拦不住误操作，只有运行时守卫能）**：`g2_correction_exec.py` 与包装脚本 `g2_correction_20260904.ps1` 均在入口检查 `G2_CORRECTION_EXEC_ALLOW=1`，未设则打印原因清单并 `return/exit 2`；仅复现 T-20260903-007 事故证据时显式开启。实测两侧门禁均生效（`exitcode=2`，未进入业务流程）。**未删除脚本**（T-20260903-007 完整证据链，含 dry-run 记录与决策依据）。
  - **已实测确认 `g2_correction_20260904.ps1` 未注册 schtasks**（查 `Quant*` 任务清单只有 DE_G2_Compare / G2Candidates_Night / G2RerunCompare_Daily / G2Rerun_Weekly / G2_Rebalance_Guard 五个，均不调本脚本）→ 排除「次日 09:43 自动写桥」风险。
  - **BOM 教训再次实证（T-20260903-015）**：重写含中文的 `g2_correction_20260904.ps1` 时写入工具**丢了 UTF-8 BOM** → `powershell.exe` 按 GBK 解码 → 路径 `Project_16_LightGBM股票大师` 变乱码 `鑲＄エ澶у笀` → 字符串引号被吞 → `The string is missing the terminator: "` 解析崩溃。**修复方式：`[System.IO.File]::WriteAllText($f, $text, [System.Text.UTF8Encoding]::new($true))` 补 BOM**，复测 `BOM=True parseErrors=0`。**每次编辑含中文 .ps1 后必须校验 BOM** 这条规则，本轮再次被真实验证。
* **本轮验证记录（可复现）**：解释器 `C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe`（3.11.15；该 shell 无 `python` 命令，直接调 `python` 会 exit 9009）。`py_compile g2_correction_exec.py` OK；`pytest research/tests/test_rebalance_sell_priority.py research/tests/test_order_guard_backfill.py -q` → **11 passed**（唯一 warning 是 QMT 端 numexpr 的 `distutils Version classes deprecated`，与本轮改动无关）；`Schedule get` 独立回读 30344e79 / 051bc2d9 → 修订后 prompt 全文逐条命中 `_g2_top10.csv`、候选异常只禁买、有 SELL 必须 `--live`、三类允许中止条件、`python lark_push.py`，cron/executions/next run 未变。

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


## 2026-09-20 · F2/F5 死代码事故（T-20260919-002）修复 + 三道防线落地

> 对应看板 `T-20260919-002`；DE 扫描报告 `data/schedules/de_r8_scripts/f5fix_scan/scan_report.md`、修复报告 `.../f5fix_fix/fix_report.md`。

- **事故**：`deploy_predict_g2.py` L112-119 先 `for c in [...]: if c not in pre.columns: pre[c]=np.nan` 无条件建列，后置 `if "main_net" not in pre.columns and ...` 永假 → **F2 恒新浪兜底、F5 恒 5.0**，实盘 2026-08-17~09-18 共 39 天评分卡坍缩无人发现（每日 top1 可被翻转）。
- **教训（三类死代码高危模式）**：
  1. **P1 建列后判**：先填 NaN 建列再 `if col not in df.columns` → 判断永假。修法=先真回退再 NaN 兜底（判断移到填 NaN 前）。
  2. **P2 passorder 返回值当订单号**：`order_id = passorder(...)` 后 `if order_id:` 永假（异步返回 0/None），正确判 `ret == 0`。qmt_builder mfic 模板 L204/L332/L357 仍含此死代码（三重引号字符串内，AST 不可见需文本层扫描，未部署实盘待修）。
  3. **死代码藏身模板字符串**：AST 看不到，必须正则文本层兜底（`scan_deadcode.py` find_p2_text_hits）。
- **三道防线（已落地+定时任务）**：
  - **A 静态扫描** `D:\QuantLab\scripts\scan_deadcode.py`（P1 建列后判/P2 passorder 真值，AST+文本双层）：接入 `build_p16_g2.py`/`build_p16_ens.py`/`build_p16_v13_risk.py` 构建前置（命中即中止构建）+ schtasks `Quant_P16_DeadcodeScan_Weekly`（周六 09:00）。默认扫 P16 全链路+broker+qmt_builder+data/schedules+versions 存档。
  - **B 数据健康监控** `D:\QuantLab\scripts\check_scorecard_health.py`：每日 20:15（schtasks `Quant_P16_ScorecardHealth_Daily`），SC_F2/SC_F5 nunique<2 → error 飞书告警，快照非空率/新鲜度/live 日志 7 日检查；退出码 0=PASS/1=error/2=warn。**注意（2026-09-24 T-20260924-001 修正旧说法「F3/F6 恒值是常态」）**：SC_F1 恒值=分档桶效应（设计内，warn）；**SC_F6 恒 5.0=MAIN_DAILY 滞后 est 全 NaN（数据源失效，须查主库 max）**；**SC_F3 恒 4.0=候选不在 enh 面板宇宙（覆盖断裂，非分档同档）**；warn 级不升 error，但 F6/F3 恒值必须按上述两查法定性，勿再当「常态」忽略。（09-25 方案 A+C 已修：F6/F3 恒值再现=修复回归，除两查外加查 est 自检日志与 `_fill_f3_events` 是否触发。**另：任务 LastResult 经 `powershell -Command` 包装 exit 2(warn)→1，与 exit 1(error) 不可区分；且假日 `latest_top10_csv` 会选中 +1 交易日副本（文件名最大）而非当日产物，两者均可造成「假 error」——判定必须复跑 `--no-send` 看输出或看飞书卡级别。**）
  - **C 快照回放回归** `D:\QuantLab\data\schedules\test_g2_rerun.py`：周六 09:10（schtasks `Quant_P16_G2Rerun_Weekly`），重放 deploy_predict_g2 主流程 + 独立校验 CSV 健康（列齐全/SC_F2/F5 非坍缩/候选非空）。
  - **D FAIL-LOUD**：deploy_predict_g2.py 主/--ab/--ensemble 三处断言，`SC_F2/SC_F5 nunique<=1 or main_net 全 NaN` 即 `raise SystemExit` 中止落盘（宁可空仓不选错），随 20:05 候选自动执行零额外调度。
- **坏样本归档**：`data/selections/g2/20260921_g2_top10.csv`、`20260921_g2_top2.csv`（修复前坏代码生成，SC_F5 恒 5.0）已归档 `.bak_bad_deadcode`，防 B 误报/防 rebalance 消费。
- **存量命中待处理**：versions/ 存档 3 文件 P1×12 条（回滚/复制即复活死代码）+ qmt_builder mfic 模板 P2×3（L204/L332/L357，未部署实盘，P0 模式）。
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


## 2026-09-10 · 面板/数据源/周训练机制答疑（T-20260910-002，用户三问）

- **面板回退答疑（2a）**：回退的是**模型指针**（g2_live_model.json 指向 08-25），不是面板。实测三类面板日期：
  - 回测面板 `feature_panel_v3_enh2_n3_bt.parquet`（G2）/ `feature_panel_v3_sc.parquet`（V1.4）：均**冻结 2019-01 ~ 2026-08-14**（= 回测测试期终点，回测验证必须冻结，防止结果随数据更新漂移、不可复现）。
  - 实盘选股面板 `feature_panel_v3.parquet`（V1.x 基础）：**每日刷新，最新 2026-09-09**。
  - 实盘 G2 特征 `data_live/g2_latest_features.parquet`：**每日刷新，最新 2026-09-09**。
  - **结论**：面板/特征数据从未"回退停更"，回退只影响模型权重；实盘每日用最新数据 + 08-25 模型权重推理，数据是新的、权重是回退的。模型训练/回测必须用冻结面板（否则对比无意义），推理用每日面板（否则选股滞后），两套并存是设计如此。
- **版本数据区别（2b）**：
  - **G2（V2.0 体系）**：43 特征 = 27 v3 基础 + 6 enh 慢变量(asof) + 10 g2 增强（lhb_net/count、north_chg、rc_rating/num、mf_main_net/elg/main_ratio、ind_pct_ths/ind_net_ths，来自 D:/astock 权威源）。评分卡 F2 用真实主力净额、F5 真实板块涨幅、F6 实时 PE/换手。面板 `enh2_n3_bt`（43 特征含 N3 标签）。实盘特征 `g2_latest_features.parquet`。
  - **V1.3/V1.4（V1.x 体系）**：27 特征（v3 基础），评分卡同框架。V1.4 = V1.3 + lite 位置过滤 + 低开校验。面板 `feature_panel_v3.parquet`（每日刷新）。
  - **融合**：不是独立面板/数据，而是 G2 面板上 rank 融合两模型概率（w=0.5 G2×v3_enh），引擎 BT_ENSEMBLE 开关实现（默认关）。
- **数据源依赖（3a）**：实时数据健康（data/cache/datasource_health.json，09-10）：腾讯/腾讯指数/东财 curl 正常；**悟道熔断中（最近失败 09-10 09:34）、mcp_tdx 熔断中（09-10 10:40）**；tushare/eastmoney_mx/ifind 上次成功 09-09。评分卡 F2 主源 Tushare 快照→新浪当日兜底；F5 增量库当日自算。风险点：若增量库/腾讯断供，F5 行业涨幅与 open 行情会滞后；北向 north_chg 受监管无日频，只能前向填充（已记录 T-20260825）。实盘选股偏差主要来自「模型权重回退 + 数据新鲜度 + 实时因子覆盖」，需用纸面前向持续校验（当前 G2 首批 4 笔 -7.15%）。
- **周训练达不到预期根因（3b，T-20260910-002 核心）**：
  - **直接证据**：09-07 周更旧门禁 G0-G6 全 PASS 放行（G3 仅要求 IC 不退步 0.9×、G5 仅分位方向），`train_g2.py` 旧 lenient 门禁也只比整体 IC（0.07942>0.03 放行）→ 09-07 模型被自动上线；09-09 升级严格门禁（同窗口 IC + 尾部 IC + Top2 可执行模拟）才判定其失败（+0.039% < 08-25 的 +0.149%）。**周训"发现不了问题"不是训练本身的错，是门禁太宽松 + 指标只看整体 IC 掩盖尾部退化**。
  - **方法论层面**：这两天的优化（位置过滤、live_trail 追盈、融合、网格寻优防过拟合）**几乎都不是"重训能学到的"**——它们是特征工程（过滤规则）、交易执行（出场规则）、策略组合（融合）、参数选择（N/红线/TOP）层面的东西，LightGBM 重训只在"给定特征→更新权重"的空间内搜索，**训练目标（IC/分位单调）与实盘收益（胜率/盈亏比/回撤）不是同一个目标函数**，所以重训天然找不到这些增益。
  - **机器学习本身非缺陷**：模型的定位是"特征到次日收益的映射"，位置/出场/组合是"如何使用模型输出"的决策层，后者必须由规则/回测/人来找，不能指望重训自动发现。正确的自动化路径是：重训只管模型参数（配严格门禁防退化），策略层优化（过滤/出场/融合/参数）走离线网格寻优 + 前向验证（本轮已做）。
  - **落地建议**：a) 周更门禁已升级 G1-G4 strict（同窗口 IC/尾部 IC/Top2 模拟/观测期回滚），防止"越训越差"再发生；b) 把「网格寻优结论」固化为候选配置库（G2=N15-live_trail 等），重训产出新模型后自动用候选库回测对比；c) 数据源熔断需监控告警（悟道/TDX 已熔断，腾讯正常）。


## 2026-09-10 · 策略层门禁自动化链路落地（T-20260910-003）

- **动机**：T-20260910-002 分工方案落地——模型层周训门禁只保证"预测质量不退步"（IC/尾部IC），但 IC 与实盘收益隔着评分卡选股/过滤/出场/组合（09-07 模型反例：IC 0.079 过关、策略回测仅 +0.039%），需在策略层加一道"直接比钱"的门禁。
- **新增资产**：
  - `data/strategy_cfg_lib.json`：候选配置库，登记三策略各自最优配置（G2=live_trail/N15/红线60/TOP2、融合=live_trail/N10/红线60/TOP2、V1.4=fixed/N10/红线58/TOP2+lite）+ 面板/模型来源 + 历史最优与时间分段结论。
  - `gate_strategy_layer.py`：策略层回测门禁 CLI。在指定策略最优配置下，用官方引擎分别回测 live 与候选模型，比较 daily_excess（候选 >= live - 0.02pp 容差即 PASS，exit 0；FAIL exit 2；运行错误 exit 1）。G2/融合支持 `--rollback`（门禁失败自动回退 live 指针到 train_g2 写入的 trial.prev_model）；`--live/--live-meta` 可指定对比基准（V1.3 用 promote 前旧正式模型）。
  - `run_scheduled.ps1` retrain 分支接入：G2 在 train_g2 --promote 成功后跑策略层门禁（FAIL 自动回滚）；V1.3 在 auto_promote 成功后跑 V1.4 配置对比（FAIL 回滚旧正式模型，用 pre_retrain 备份）。
- **验证（全部冒烟通过）**：
  - G2 PASS：候选=08-25 live，excess +0.213% vs +0.213% 判定 PASS（口径与历史最优一致）。
  - G2 FAIL：候选=09-07 模型，excess +0.03% vs +0.213% 判定 FAIL 拒绝上线（复现"周更坏模型被拦"）。
  - V1.4 PASS：候选=当前 27 特征正式模型，excess 一致判定 PASS（v3_sc 面板 27 特征推理正常）。
- **分工闭环**：模型层 = train_g2 --promote G1-G4 strict + auto_promote G0-G6；策略层 = gate_strategy_layer（候选配置库口径，直接比实盘收益）；最终裁判 = 纸面前向三臂累积。周训"越训越差"从此被两道门禁拦住（模型层防 IC 退化，策略层防收益退化）。


## 2026-09-10 · 面板数据同步核查 + 日历滞后修复（T-20260910-004）

- **核查结论：面板数据已同步最新（最近完整收盘日 09-09）**，实测各文件日期覆盖：
  - `feature_panel_v3.parquet`（V1.x 实盘面板）：**2019-01-10 ~ 2026-09-09**（每日 16:30 daily 链路经 refresh_panel_v3 刷新，09-09 16:45 落盘）
  - `g2_latest_features.parquet` / `latest_features.parquet`：**2026-09-09**（G2/V1.x 实盘特征，09-09 更新）
  - `merged_daily_full.parquet`：**~ 2026-09-09**（主库+增量合并，16:40 落盘）；`incremental_daily.parquet`：**2026-08-24 ~ 09-09**（xtdata_update 增量）
  - 回测面板 `feature_panel_v3_sc` / `feature_panel_v3_enh2_n3_bt`：冻结 **2019 ~ 2026-08-14**（设计如此，回测可复现，非滞后）
  - 主库 `D:/astock/daily/stock_daily.parquet`：周更滞后停在 **08-21**（设计如此，靠增量补每日）
- **发现并修复隐患：交易日历 `data/ashare_trade_dates.txt` 停在 08-20**（滞后 14 交易日）。根因：日历由主库周更快照生成，主库停更 + 无主动重建。影响：`is_trade_day`（被 `g2_correction_exec.py` 使用）依赖兜底（工作日非节假日即交易日）判断，命中率下降。
- **修复（双保险）**：
  1. `is_trade_day.py` `load_calendar()` 增加陈旧检测：日历末日距今 >7 自然日自动用主库+增量重建（`_gen_calendar`）。
  2. `refresh_panel_v3.py` 末尾固化每日日历重建（daily 链路必跑，增量已合并）。实测重建后日历末日 08-20 → **09-09**（4297 日）。
- **验证**：两文件语法 OK、import 链无循环、`is_trade_day.py` 实测触发陈旧重建成功。
- **结论**：实盘数据链路（增量→合并→面板→特征→选股）每日闭环正常；日历滞后隐患已修复，后续每日自动维护。


## 2026-09-10 · 全面排查：发现 3 个 bug 已修 + 遗留清单（T-20260910-005）

- **排查范围**：这两天新改动代码质量（gate 段变量/备份路径）、纸面三臂调度、G4 回滚调度、数据源告警、主库节奏、定时任务产物一致性。
- **bug 1（严重，已修）：V1.3 策略层门禁永远跳过**——run_scheduled.ps1 gate 段 `$oldFormal` 用重新 Get-Date 的时间戳拼备份文件名，与 retrain 开头实际备份（1.5-2 小时前）必然不同 → Test-Path 恒 False。修复：改为 Get-ChildItem 找最新 `lgb_model_v3_pre_retrain_*.txt`。若未发现，周一 V1.3 策略层门禁将静默失效。
- **bug 2（严重，已修）：纸面三臂断流**——g2_candidates_night.ps1 跑 deploy_predict_g2 未带 `--ab --ensemble`，v3_enh/融合臂自 09-09 手动跑后无新信号（csv 停在 09-09）。修复：top10 调用补上开关（Top10 全集记录，统计时筛 rank<=2），今晚 20:05 起每日累积。
- **bug 3（已修）：rollback_check_g2 无调度 + 退出码语义缺失**——G4 观测期回滚纯手动脚本，永不触发；且"建议回退"时 return 0 无法被调度感知。修复：a) worse 未 --apply 时 return 2；b) 挂进 run_scheduled.ps1 daily 尾部只读检查（exit 2 → 日志告警，人工确认后 --apply）。验证：当前无 trial 字段 exit 0 正常。
- **补数：astock_kit 09-09 缺失**——Quant_AstockKit_Update 计划任务 09-09 19:14 返回 0x800710E0（任务条件拒绝，产物目录缺 20260909；龙虎榜/行业/研报等评分卡数据源）。已手动 `--date 2026-09-09` 补拉。今天 17:30 任务会自动再跑（需观察是否再次 0x800710E0，若是则检查任务条件设置：交流电源/唤醒执行）。
- **确认无问题项**：$g2LivePointer/$proj 变量定义链 OK；train_g2 promote 确写 trial.prev_model（G2 回滚依赖成立）；g2_candidates_night 语法 OK；run_scheduled PS 语法 OK。
- **遗留清单（未修，按优先级）**：
  1. nightly_check.py 体检脚本无调度（数据源探活/熔断盘点/面板/QMT 全链路检查纯手动）——建议挂每日盘后（如 20:30）。
  2. 主库 stock_daily.parquet 停 08-21——设计如此（数据包手动导入节奏，update_astock.py），增量库兜底每日数据；面板合并已含最新 09-09。注意主库不宜再拖太久（增量库会越滚越大，merge 耗时上升）。
  3. build_panel 逐行 asof 重构（enh 慢变量特征漂移，T-20260909-002 遗留）。
  4. G2 实盘 N15-live_trail 切换（待纸面前向 30 笔验证）。
  5. 数据源熔断告警推送（nightly_check 有盘点无推送；悟道/TDX 当前熔断中）。


## 2026-09-10 · 夜间检修自动化落地（T-20260910-006，遗留清单#1 完成）

- **落地**：`nightly_check_task.ps1`（包装：Get-GoodPy 同口径 Python 探测 + -u 无缓冲 + 日志 data/schedules/nightly_check_task.log）+ 计划任务 `Quant_P16_NightlyCheck_0100`（每日 01:00，StartWhenAvailable + 电池条件全放开 + 2h 上限）。
- **踩坑（PS5.1 经典坑）**：包装脚本首版设 `$ErrorActionPreference = "Stop"`——Native 命令(python) stderr 经重定向会触发 ErrorRecord，Stop 策略**静默终止**脚本（17:17 首跑症状：日志只有开始行、报告未更新、无任何错误输出）。修复：去掉 Stop（与 run_scheduled.ps1 同口径），并加 `-u` 无缓冲防块缓冲丢日志。
- **验证（17:22 完整跑通）**：23 项检查 FAIL=0 / WARN=1（C1"次日任务Active"设计上交检修指令核对）；exit 码语义正确（FAIL>0 → exit 1 → LastTaskResult 可观察）。附：本次检修顺带确认面板已刷到 09-10（16:30 daily 正常）、悟道熔断恢复 PASS、QMT 进程存活。
- **语义**：每日 01:00 自动体检 A-G（数据/面板/模型/数据源/QMT/环境/报告）+ --fix 自动修复（面板重刷/增量重下/熔断恢复/QMT_POOL 清理），FAIL 时 LastTaskResult=1；--push-alert 未启用（飞书推送由检修指令按报告执行）。
- **遗留清单更新**：原 #1（nightly_check 无调度）完成；剩主库节奏/asof 重构/N15 切换/熔断推送 4 项。


## 2026-09-10 · TW 策略优化审计：数字全复现，结论=回测口径成立（T-20260910-007）

- **背景**：诚哥布置「审审 TW 这两天做的策略优化」（G2/V1.4/融合 网格寻优，T-20260909-009）。审计报告：`results/策略优化审计_20260910.md`。
- **独立复算（官方引擎原样重跑，非引用 TW 输出）全部精确命中**：G2 live_trail/N15/红线60/TOP2 = +0.213%（55.1%/2.70/-16.7%/78笔）；融合 live_trail/N10/红线60/TOP2 = +0.211%（61.3%/1.75/-17.2%/106笔）；V1.4 fixed/N10/红线58/TOP2+lite = +0.157%（59.6%/1.67/-15.9%/99笔）；G2 时间分段 前+0.095%/后+0.353%。strategy_cfg_lib.json 登记配置与实际 env 逐项对照一致（各臂 posfilter 显式声明 ✓）。
- **引擎审读通过**：T+1 卫生（买入日不并当日 high 入峰值）、一字板双侧、停牌/退市处理、live_trail 保本底线语义、成本模型、posfilter 真实生效（V1.4 lite 臂跳过 67 笔）。
- **根本性保留——测试集双重使用**：G2 模型 08-25 / v3_enh 08-23 训练，测试期 2024-07~2026-08-14 **100% 在训练窗内**，三配置数字系统性偏乐观；S3 分段不能缓解（模型两段都见过）。唯一干净裁判 = 纸面前向三臂（G2 首批 4 笔 -0.0715%/日正是样本外投影）。
- **次级风险**：V1.4 红线 58 独峰（55/58/60/65 = +0.009/+0.157/+0.063/+0.077）过拟合嫌疑最重；G2 N20 断崖（+0.027%）N15 恰在格点取峰；融合与 G2 同面板同池，三臂前向非独立对照；基准 fwd_ret=close→close 与策略 open→open 窗位差（历史口径沿袭，年化 70.2% 等折算双重虚高，禁止对外引用）。
- **TOP1 弃用裁决审计通过**：G2-TOP1 前段 -0.016% 证伪、融合-TOP1 每段 28 笔样本不足——裁决合理。
- **建议（与 103/104 拍板一致）**：纸面裁判标准看「样本外超额是否 >0」而非「是否达到 +0.2%」；V1.4 与 G2/融合间抉择优先信后两者。


## 2026-09-10 · V13 成本锚调度归属拍板：双调度器消除（T-20260910-008）

- **发现**：TW 侧定时任务「成本锚生成(项目16V1.3)」(eda9b0c3) 与 QuantLab schtasks `Quant_P16_CfgPremarket_0900` 双写 `positions_cfg_v13_<date>.json`——且**两边都是 09-10 当天注册、均从未运行**，明早 09-11 09:00 将是两边首次同时触发（撞车恰好发生在首次，归因最难）。
- **归因澄清**：TW 任务 09-10 09:27 创建、Executions=0；历史 cmd/ 落盘时间 19:49/14:33/18:33 全是旧机制（收盘后手动/补跑）产物，09-08 断档 = T-20260908-002 要根治的原缺陷，均非调度失效。
- **拍板（方案 A）**：TW 任务已暂停，用户 2026-09-10 拍板**彻底删除**（待 TW 执行；删除确认后此条闭环）——**V13+G2 成本锚唯一 owner = QuantLab `Quant_P16_CfgPremarket_0900`**（09:00 MON-FRI，串跑 gen_positions_cfg_g2.py + gen_positions_cfg_v13.py，日志 data/schedules/gen_positions_cfg_premarket.log + exit 码，已加 StartWhenAvailable + 电池条件放开）。
- **边界**：TW 侧即使将来重建调度也只许管 V13（67014907），不得写 G2（70180771）半边；P1 文案问题（QMT 进程体检多余/自指校验无意义/静默与通知矛盾）随任务退役无需修，若复活按审计清单 3~7 条修正。
- **教训**：双端（QuantLab schtasks + TW 定时任务）都能碰同一产物文件时，**建调度前必须互查对方已注册的任务清单**，避免同日双注册；本次靠落盘时间戳取证才未误判"TW 调度失效"。

## 2026-09-11 · G2 升级 N15-live_trail + 融合(ensemble) 上线（T-20260910-109）

- **拍板（2026-09-10 诚哥）**：①G2 实盘配置由 N10-fixed 升级到网格最优 N15-live_trail（+0.213%）；②融合(ensemble) 现在直接上线（纸面前向 0 笔即上线，**覆盖防过拟合 30 笔裁判红线**，T-20260910-103/104 让步）；③融合走 70180771 虚拟子账户，初始 10 万。
- **G2 升级**：g2_config.HOLD_DAYS 10→15。审计（策略优化审计_20260910.md）已确认桥出场逻辑（STOP→TP→TRAIL + 激活8%+保本底线）与回测 live_trail（TRAIL_FIRST=0）语义一致 → **桥无需重建**（仍 184724），仅 rebalance 持有期变 15 日；现有持仓自动延至 15 交易日。
- **融合管道（全新独立资产，与 G2 零耦合）**：选股 deploy_predict_g2 --ensemble --ens-live（v3_enh+G2 rank 融合 w=0.5 红线60，候选 top10 by ens → 买入 top2 by total_new，16:45 管道并入）；g2_ens_config.py/qmt_bridge_client_ens.py/
ebalance_ens.py/
econcile_ens.py/gen_positions_cfg_ens.py/uild_p16_ens.py/strategy_p16_ens_bridge_src.py 七件套；独立目录 D:/QMT_POOL/p16_ensemble_bridge + 独立资金池 ens_strategy_capital.json（10万）+ 独立账本 g2_ens_hold_dates.json。桥产物 BUILD **20260910-233536**（GBK/Py3.6/无MOCK 已验）。
- **同账户双桥隔离（关键设计）**：G2 与融合同一账号 70180771、同一大QMT 客户端并跑。隔离 = 双桥都「纳管全账户持仓」但 _check_risk_signals 只评估自家成本锚（cost>0）的票；对方票 cost=0 跳过。叠加「融合换仓跳过 g2_hold_dates 已有票」的重叠规避。**已知残余风险：G2 侧无反向互斥**（G2 候选若撞融合持仓会双买）——观察期日终对账盯同票双账本，用户确认后才给 G2 加对称互斥。
- **部署与验证**：09-11 00:09 模拟模式误启 7s 手动停止 → 00:10 交易模式重启；QMT 日志 [P16ENS][BUILD] BUILD_TAG=20260910-233536 account=70180771 + [P16ENS][INIT] positions=0 无报错；heart_20260911.json 心跳正常、peak.json 空。外部层 dry-run 全通（融合选股落盘 / rebalance_ens 重叠规避+TOP2 / reconcile_ens 飞书通知）；资金分配表双份 +10万，check_capital_allocation.py exit 0。
- **调度**：融合换仓 TW 任务 051bc2d9 **09:52**（09-10 由 09:50 后移 2 分钟避 G2 同刻下单）+ 融合日终对账 18c0c8d2 15:45；盘前成本锚 ps1 增第三腿 gen_positions_cfg_ens.py（UTF-8 BOM 已校验）；push_fill_card.py 增 --source ens。
- **观察期（≥5 交易日）**：09:00 三腿 exit=0 → 09:52 融合换仓首跑 → 盘中心跳/peak → 15:45 对账盯 orphan/差额/同票双账本；G2 桥不受影响（仍 184724 心跳正常）。
- **未提交 git**：融合七件套 + g2_config 改动 + 3 处 ps1/py 修改 + 资金分配表/看板/VERSIONS/知识库，待用户确认后提交。
## 2026-09-11 · 「5% 触价换票」两步实证：证伪结题（T-20260911-001）

- **结论**：「单票 3~5 交易日内触价 +5% 就换票」不可行。**「换票/出场规则不产生 alpha」清单 +1（触价止盈版）**，与 PK_OUT/减仓/持有期软化/止盈线上移/追盈优先同族。维持现行出场规则：V1.3 = live_TP15 / G2 = N15-live_trail。
- **第一步（逐笔，`scan_mfe_tp5.py`，报告 results/MFE触价统计_TP5_20260911.md）**：候选池无触价概率 edge——top10 vs 全市场基准提升仅 +0.1~+0.8pp（红线>=60 最高 +2.8pp），触价率主要由波动驱动（基准 Q1→Q5 11%→39% 单调）；逐笔唯一正收益区=红线子集 touch_stop（>=58 H5 +0.59%/笔）但 **2026 YTD 全转负**（H3 -0.44%/H5 -0.51%）且触价率跑输同年基准。
- **第二步（组合层，`run_tp5_rotation.py`，报告 results/TP5短持有期轮动回测_20260911.md）**：N=3/5 共 12 组全部 ≤+0.024% 日超额，**N=3 配对 t 4/6 组显著为负**（p=0.002~0.048），与 T-20260904-001「持有期越短越差」单调性第三次互证；touch_TP5_N10 +0.104% 不及基线 live_TP15_N10 +0.132%（配对 t p=0.76 不显著，thr58 下 2024 转负 -0.124%）——胜率 68.5% 的提升被盈亏比 0.85 的截断抵消。逐笔表面收益来自高换手重叠暴露+无资金约束，组合层不可复制。
- **副产品（仅备查，禁止凭此改实盘）**：fixed_TP5_N10（开盘+5% 固定止盈）thr60 +0.130% 与基线统计打平（p=0.98）且分年更均衡（24/25/26 = +0.07/+0.11/+0.21 vs 基线 -0.08/+0.25/+0.11），但 thr58 弱于基线；将来纸面前向若加臂可作候选。
- **引擎增量**：`scan_rotate_cost_real.py` 新增 `touch_tp` 出场模式（-7% 开盘止损 + 盘中高点≥成本×(1+TP) 触价卖，跳空按开盘成交，T+1 锁 T+2 起可卖），默认关闭；官方口径零影响由 4/4 基线锚点精确复现验证（fixed/live_TP15_N10 × thr58/60 全 PASS）。
- **方法论收获**：配对 t 检验已固化进 run_tp5_rotation.py（各组日收益 vs 同红线基线 ttest_rel），后续出场规则扫描复用该模板；首达过程直觉（换票规则零期望）+ 组合层资金约束复核缺一不可。
- **单源声明**：gpsj 备用源不可用（No module named 'duckdb'），结论仅基于 astock，未经备用源交叉验证。

## 2026-09-12 · train_g2 enh 慢变量 asof 漂移修复 + 学习恢复实证（T-20260910-106）

- **根因坐实**：`train_g2.py` 把 6 个 enh 慢变量按「每股最新值」广播到全部历史行——08-25 原版训练（overnight_opt 直接读 enh 面板=逐行 as-of）与实盘（build_g2_daily asof）都是逐行口径，唯独周更重训脚本偏了 → 09-07 周更越训越差（TOP2 +0.039% vs live +0.149%）被网格寻优发现、回滚至 08-25（T-20260909-001/002）。
- **修复**：`pd.merge_asof(on="trade_date", by="ts_code", direction="backward")` 逐行取值；附带修复 `--limit` 冒烟隐性 bug（head(N) 全落 2019 年 → 训练窗 mask 全空必挂，改跨期随机抽样 sample(random_state=0)）。
- **三层验证**：①面板级：2 万行抽样与 enh 面板直查 **0 不一致**；2021 年 100% 行值改变（000001.SZ turnover_rank 唯一值 1→243=漂移面实证）；面板末日 backward 命中 99.62%（余 0.38% 为末日无数据的停牌/退市股=正确语义）；②冒烟链路通（strict 门禁正确 FAIL 5 万行样本且不提升）；③**全量重训 20260912_1905t（不 promote）：同窗 strict 门禁全 PASS——IC 0.1503 vs live 0.1273、尾部IC 0.1730 vs 0.1711、TOP2 +0.0174(n=362) vs +0.0117(n=351)，分位收益单调（Q1 -0.0018→Q5 +0.0072）**。同一数据同一脚本唯一变量=asof 修复 → 学习恢复实证。
- **生效路径**：live 指针未动（仍 08-25 回滚版）；09-14（周一）17:00 quant_weekly_retrain 经「备份→train_g2 --promote（strict）→gate_strategy_layer 策略层复核（FAIL 自动回滚）」双门禁自然生效。
- **新发现（T-20260912-004）**：feature_panel_v3_enh.parquet **全库无 writer**（08-25 一次性构建，冻结 2026-08-14），refresh_panel_v3 只刷 27 特征基础面板；训练与实盘共用同一过期面板故口径一致（不构成 train-serving skew），但 6 个慢变量绝对新鲜度滞后近月且随时间恶化，需补刷新链路。
- **其余 tail(1) 用法核查**：build_g2_daily / merge_live_features / refresh_panel_v3 均为单日推理/增量刷新场景（取「截至今日」值=正确语义），非训练侧漂移；versions/ 下为历史快照不动。
- 产物：候选模型 `models/lgb_model_v3_g2_strong_real_20260912_1905t.txt`（strict PASS 但未 promote，留作 09-14 对照）+ 日志 `data/real/train_g2_asoffix_20260912.log`。未 commit。

## 2026-09-12 · enh 面板刷新链路落地 + build_features_v2 NaT 溢出根因修复（T-20260912-004）

- **口径考古（原 builder 脚本遗失，git 各提交仅消费端）**：33 特征 = 27 基础 + 6 enh。实证（3+5 抽样日）：4 事件特征（ex_days_since/fc_pchange/dv_year_sum 事件段 + ex_yoy）与 turnover_rank（turnover 全市场 rank pct）与现行 `build_features_v2.py` 链路 **100% 复现**；**industry_mom20 原始口径不可精确恢复**——原 stock_basic 08-23 vintage 随研发电脑遗失（本机 08-28 23:47 更新过）。最佳近似 = stock_basic.industry 当前映射 + 全市场**未复权** close 20 日动量按 (trade_date, industry) 等权均值，corr 0.983~0.9966、组内 diff 恒定（纯聚合层差异）；881 同花顺行业指数口径 corr 仅 0.79 被排除。
- **根因 bug（build_features_v2.py 两处，已修）**：`NaT.astype('timedelta64[D]').astype('float64')` = **-9.2e18 而非 NaN**（numpy NaT 转 float 的语义坑），`np.isnan` 拦不住 → int32 溢出 -2147483648。污染 ex/fc/sc_days_since 全期 3.73%/0.30%/6.09% 行（merge_asof 无匹配 = 首事件晚于面板日的股票）。**08-23 面板诞生即有**（冻结面板同样污染，live 训练/推理同源一致 → 不构成 skew）；尾部干净（ex 07-20 后 0 行、sc 近 30 日仅 15 行）→ 实盘影响 ≈0。修复：`td[np.isnat(td_raw)] = np.nan` 再 astype；迁移验证：全部变更行与迁移行一一对应（-2^31→9999 / NaN→0），零意外变更。
- **writer 落地**：`refresh_panel_enh.py`（唯一 writer）——v2 面板切片（27+4 事件）+ turnover_rank（主库区原生值 / 增量库 vol+float_share 反推，增量库仅 OHLCV）+ industry_mom20（近似口径）→ 校验门禁（v2 新鲜度≤7 天、NaN≤2%、days_since 负值回归=0、重叠期末 3 日 5 特征复现≥99%、industry_mom20 corr≥0.90、行数≥90%）→ 备份 → 覆盖 → meta 更新（features_v3_enh.json 含 refreshed_at/口径注记）。**失败保留旧面板 exit 1（fail-loud）**。
- **设计决策：全量重建而非冻结历史+追加**——4 事件特征/turnover_rank 确定性复现，industry_mom20 vintage 偏差全期均匀且原映射不可恢复；全量重建单 writer、自愈（stock_basic 更新后全期一致重聚合）、与 v2/v3 面板周重建机制同构。
- **挂链**：`run_scheduled.ps1` daily 16:45（refresh_panel_v3 后、deploy_predict 前，失败仅告警不阻塞 V1.3——V1.3 读 _v3 基础面板不消费 enh）+ retrain 周一 17:00（train_g2 前，失败告警不跳过）。含中文 .ps1 编辑后 BOM 校验完好（EF BB BF）。
- **生产面板已刷**：feature_panel_v3_enh.parquet → 2026-09-11（4,807,456 行，2019-01-10 起），与 v3 基础面板网格完全一致；备份 .bak_20260912_224322。冒烟：train_g2 --limit 50000 读新面板通过（enh 行级覆盖率 0.9911，strict FAIL=小样本正确行为）。
- **数据天花板（刷新链的固有约束）**：astock finance forecast/express max ann_date 2026-07-29、dividend/fina_indicator/share_change 2026-08-21；ths_daily 到 08-21；主库日线到 08-21、增量库 08-24 起仅 OHLCV。事件特征靠 asof 语义自然衰减（无新事件=旧值持续，语义正确）；industry_mom20 用主库+增量 close 无天花板。
- **生效路径**：09-14 周更重训自然吃 09-11 数据（与 asof 修复候选 20260912_1905t 同链双门禁）；live 模型（08-25）serve 消费经 build_g2_daily asof 读 enh 面板 → 修复后慢变量恢复到 09-11。验证脚本 `scripts/_tmp_*.py` ×7 留存（口径复现/变体扫描/迁移验证）。未 commit。

## 2026-09-13 · ENS 资金池滚动补齐（T-20260913-001 P0，对齐 G2 reconcile_g2._roll_g2_capital）

- **根因**：
econcile_ens.py 无资金池滚动逻辑，ns_strategy_capital.json 停在 09-10 初始 10 万（de_recheck B-5 观察项）——已实盘建仓 600409/300138 后买入预算失真（浮亏时仍按 10 万配仓），随盈亏累积偏差扩大。
- **修复（reconcile_ens.py）**：补三个函数——_all_fills()（遍历 state 目录所有 fills_<date>.json 跨日累计）、_realized_pnl_from_fills()（FIFO 配对已实现盈亏，与 positions_from_fills 同口径）、_float_pnl_now()（最新 positions_cfg 成本含费 + fetch_price 现价）；_roll_ens_capital(date) = START_CAPITAL + realized + float_pnl，account_id 戳原子写 save_ens_capital（<=0 跳过）。main() 挂 ⑤ 资金池段：capital_prev 先读再滚，md 报告新增"资金池"节，飞书通知带资金池。
- **验证**：①单测 5/5 PASS（沙箱临时目录：FIFO 650/浮盈 50/滚动 100700/幂等二次滚动一致/空仓回退 10 万）；②端到端 
econcile_ens.py --date 20260913：资金池 **100000→98469.21**（已实现 0 + 浮盈 -1531），positions_cfg FALLBACK 回退 0911 持仓（周日无当日快照属正常），md/飞书均带资金池。
- **生效路径**：TW 对账任务 18c0c8d2（15:45 日终对账）直接调 reconcile_ens.py → 自动生效，无需改调度。
- **⚠️ 测试坑**：
ead_fills/read_positions 用的是 qmt_bridge_client_ens 模块级 STATE_DIR/CMD_DIR 常量（不是 g2_ens_config.G 的），沙箱测试必须同时 patch CL.STATE_DIR/CL.CMD_DIR 才不污染真实 QMT_POOL。
- **⚠️ 周日跑对账会误报"账本比账户多 N 股"**（
ead_positions(date) 无当日快照返回空 → 账户侧=0）：交易日 15:45 有当日 positions 快照不会误报，非修复引入。
- **相关**：三策略自我迭代讨论纪要与 DE 稿见 data/三策略自我迭代_讨论纪要_20260913.md、data/de_self_iteration_discuss_20260913.md（DE 稿"心跳告警未注册"结论已过时——09-12 已补注册，待 09-14 盘中真实触发核对）。

## 2026-09-13 · 纸面自动降级闸落地（T-20260913-001 P1-8）

- **动机**：体系"防模型退化"强（双门禁+回滚）、"防策略整体失效"弱——样本外纸面前向持续为负时无自动刹车。设计文档 data/纸面自动降级闸_设计_20260913.md。
- **新脚本 paper_forward_downgrade.py**：三臂（G2 live=N15 / V1.3 ab_v3enh=N10 / ENS ens=N10）取 rank<=2 已到期样本（到期=信号日+hold+1 交易日<=最新，merged_daily_full 日历）；超额=个股 open→open 复权收益（load_open）− 同窗口全市场等权基准（load_bench，与 forward_stats.market_ret 同源）；**触发=n>=30 且 超额均值<0 且 <-0.0002**（幅度阈值防微负抖动；t 值仅报告不作硬门槛——A股日频 30 笔样本 t 天然难显著）。
- **CLI**：默认=评估+新触发自动写标记+飞书；--check=只读不写（dry-run）；--unfreeze <G2|V1.3|ENS>=人工解除；--force-freeze <策略> --reason=人工强制。标记文件 data/real/paper_forward_downgrade.json（frozen/since/n/mean_excess/note）。
- **rebalance 接入（只卖不买）**：rebalance_g2.py（G2）/rebalance_ens.py（ENS）在 build_plan 买入段入口 
_slots=0 + plan["downgrade_frozen"]；rebalance_daily.py（V1.3）在 exec_buys 计算后清空。**卖出/减仓/锁定/桥内 QMT 风控全保留**。消费方用 paper_forward_downgrade.is_frozen(key)（try/except 包裹，import 失败降级为不冻结）。
- **挂调度**：paper_forward_daily.ps1 16:45 管道尾步骤6（fail-loud：exit 非 0 → DOWNGRADE-ALERT 日志 + 管道 exit 2）。
- **验证**：单测 8/8 PASS（check 不写标记/48 笔显著负触发/写标记+alerts/is_frozen/unfreeze/微负 -8e-6 不触发/force-freeze）；真实数据三臂均不触发（G2 n=8 未达 30，超额 -0.02423）；端到端 G2 冻结 dry-run 实证「冻结加仓 0 买入、持仓保留照常」。
- **踩坑**：①load_bench/load_open 必须过滤 open<=0 行（停牌/异常，否则 bench=inf 污染超额）；②ARMS 存相对文件名、运行时 join REAL（否则测试覆盖 D.REAL 无效）；③--check 语义=完全不写标记（曾 bug：check 也 save_mark 导致 dry-run 污染真实标记）。
- **后续**：09-14 16:45 管道首跑自然生效；解除必须人工 --unfreeze（不自动恢复，防呆）。

## 2026-09-13 · 自我迭代机制批量落地（T-20260913-001 全部执行，诚哥"所有任务都完成"）

### 1. C2 G2 反向互斥对称化
- g2_config.py 加 ENS_HOLD_DATES_FILE（指向 rebalance_g2_ens/g2_ens_hold_dates.json）；
ebalance_g2.py 买入段候选池排除 ENS 账本持仓（对齐 rebalance_ens 的 G2 单向规避）。实测 dry-run「跳过 ENS 持仓 2 只: 300138.SZ,600409.SH」，G2 候选 Top1 与 ENS 无冲突正常买入。

### 2. F1 特征级 IC/覆盖率周报
- 新脚本 eature_health_weekly.py：V1.3 面板（feature_panel_v3.parquet 27 特征）近 4 周逐日横截面 Spearman IC + 缺失率；ALERT=近4周|IC|<0.01 且缺失率>5%，WATCH=二者之一；--check 只读；挂 retrain 前置（面板刷新前跑=用上周期数据评估）。**首跑发现真信号：fin_ocf_to_profit 缺失率 13.34% WATCH**（经营现金流/净利润比缺失率偏高，待人工裁决）。**踩坑：面板 index 是 [None] 扁平表（列含 trade_date/ts_code/fwd_ret），不是 MultiIndex**。

### 3. F2 enh 面板新鲜度硬门禁
- 新脚本 check_enh_freshness.py：enh 面板末日 vs 最近交易日（merged_daily_full，trade_date 在 index），>7 天 exit 2。挂 run_scheduled retrain：$skipG2 阻断 train_g2（补 if 守卫 + else 日志）。实测 enh 面板落后 0 天 PASS。

### 4. M3 观察期回滚条件自动化
- 
ollback_check_g2.py 加 --auto：深度劣化（前向 < 基线 − AUTO_THRESHOLD=0.0005）自动回退 prev_model + 飞书；轻度劣化仍 exit 2 人工；_do_rollback 抽取复用（--apply 也走它）。单测 9/9 PASS（深度自动回退+meta推断/轻度人工/通过观察期）。**踩坑：trading_days_since 默认参数绑定了 import 时旧路径，改函数内读全局 LIVE_CSV**。挂 daily 尾部 
ollback_check_g2.py --auto。

### 5. E6 双调度器一致性巡检
- 新文件 data/tw_schedule_snapshot.json（TW 侧 13 任务快照，agent 会话手动更新，标记 conflict_with 任务）；
ightly_check.py check_e 加 E6：读快照，conflict 任务仍 Active → FAIL。全检 27 项 PASS（含 E6）。**TW 侧 eda9b0c3（成本锚生成V1.3）仍 Paused 未删——T-20260910-008 拍板"彻底删除"待 TW 执行，E6 已监控**。

### 6. P1 配置漂移复查
- 新脚本 config_drift_recheck.py：promote 后对历史最优 N 的 3 个邻域点（N±5 × 保持 EXIT，每点约8-10分钟）用官方引擎重扫，与 strategy_cfg_lib 对比，**只登记不改实盘**（data/config_drift_<策略>_<date>.md/.json）。挂 retrain 末尾：仅当 $g2New（本轮 promote 成功）才跑。后台验证中。

### 7. P3 纸面优先制度固化
- repowiki 网格寻优与防过拟合方法论.md 新增第七节：任何配置改动先纸面≥30笔；降级闸/漂移复查只登记；回滚自动化保守边界；KPI=样本外纸面前向超额（回测年化不作 KPI）。

### 09-14 周一待观察（自然生效）
周更双门禁（asof 修复候选 20260912_1905t）+ 心跳告警盘中真实触发 + 降级闸 16:45 管道首跑 + enh 硬门禁/特征周报/配置漂移复查首跑 + G2 互斥首日无冲突。

## 2026-09-13 · DE 独立审查自我迭代批次 + P0/P1/P2 修复（T-20260913-001 收尾）

- **DE 审查**（报告 data/de_audit_self_iteration_20260913.md）：9 项机制 7 PASS + 1 FAIL（⑥自动回滚列名死链）+ 横切 BOM 丢失。TRAE 逐项独立复核全部确认属实后修复复验。
- **P0-1（真 bug）**：
ollback_check_g2.py 读 	rade_date/wd_ret 列，真实 paper_forward_live.csv schema 是 date/
et——trial 存在时必 exit 1，自动回退/轻度告警永不可达。**教训：MOCK 单测用 mock schema 自证自测=假阴性（AGENTS「MOCK 测不到真实 schema」的又一实例）**。修复：列名对齐 + 单测改用真实 schema（11/11 PASS）。
- **P0-2（违反硬规则）**：run_scheduled.ps1/paper_forward_daily.ps1 被编辑工具改写丢 UTF-8 BOM（首字节 35,32 无 EF BB BF），PS5.1 中文乱码；09-14 周训前置。已补 BOM（EF BB BF 复验 OK）。**教训：每次编辑含中文 .ps1 后必须复验 BOM（AGENTS T-20260903-015 硬规则再次命中，PROJECT_MEMORY 09-12 声称「BOM 完好」已失效——写入者是不同会话）**。
- **P1-1**：paper_forward_downgrade.py docstring 承诺「推送失败 exit 2」但 main 恒 return 0 → 调度 DOWNGRADE-ALERT 分支不可达。修复：推送失败 return 2（mock 验证）。
- **P1-2**：
ollback_check_g2.py --auto 回退失败静默 return 0 → 假阳性「已自动回退 OK」。修复：失败 return 2 + 飞书告警。
- **P2-1（规格偏离）**：讨论纪要 M3 要求「连续 3 日」深度劣化才自动回退，实现为单次。修复：
ollback_streak.json 计数，连续 3 交易日才触发；方法论第七节同步（对齐）。
- **P2-6**：E6 快照新鲜度未校验（手动快照过期→假 PASS）。修复：generated_at >7 天 WARN。
- **P2-13**：_do_rollback 指针写入非原子。修复：tmp+os.replace。
- **剩余 P2 记录待办**（DE 报告③节）：V1.3 臂 N10 vs 实盘 N5 口径；互斥读文件 fail-open 告警；漂移 argmax 无容差；gate 回滚后漂移复查用被拒候选；ENS 预算基数未扣保留持仓（预存分歧）；回滚 CSV 回填 --hold 10 vs N15。
- **DE 设计建议（采纳记录）**：统一前向评估数据底座（共享 schema）；动作失败与触发同级 fail-loud；调度退出码矩阵文档化；gate/rollback 改指针后重读；BOM 校验自动化。

## 2026-09-13 晚 · P2 技术债批量清理（T-20260913-001 收尾，DE 审查③节）

逐项处理 DE 审查报告的 P2 项（P2-1/6/13 已在上轮修复）：
- **P2-2 已修**：gate FAIL 回滚后漂移复查重读 live 指针（），live ≠ 候选则跳过（复查被拒候选无意义）。
- **P2-3 已修**：check_enh_freshness exit 1（数据缺失 fail-safe 放行）与 exit 0（新鲜）在 ps1 日志区分，不再误导「视为新鲜」。
- **P2-4 已修**：is_frozen 文件存在但损坏 → 打印告警（保留 fail-open 但可见，防静默解冻）。
- **P2-5 已修**：①rebalance_g2 ENS 互斥读文件缺失/失败 → 告警（双买风险回归可见）；②rebalance_ens _update_hold_dates 补陈旧条目清理（对齐 G2 版，防 ENS 陈旧条目让 G2 过度排除）。
- **P2-7 已修**：config_drift_recheck 漂移判定加 DRIFT_MARGIN=0.0005（历史最优与扫描最优 excess 差>边际才判漂移，防微差噪声误记）。
- **P2-8 已注**：降级闸 ARMS 口径注记（V1.3 臂 N10=候选口径 vs 实盘 N5 到期制，有意为之与 ab_stats 同源）。
- **P2-9 已注**：rollback_check_g2 docstring 注记（paper_forward_live ret 为 N10 回填 vs G2 live N15，已知近似，待统一前向底座按臂分 hold）。
- **P2-10 已注**：tw_schedule_snapshot owner_boundary 澄清「TW 触发 QuantLab 脚本 ≠ 双写，仅直接写同一产物才冲突」。
- **P2-11 已修**：feature_health_weekly --check 严格只读（json/md 均不落盘）。
- **P2-12 已列待办**（非本批引入，涉口径一致，不碰实盘）：ENS 预算基数未扣保留持仓投入（G2 P1-4 修复未同步）、ENS 无大盘门控、ENS hold_dates 缺失 fail-open 即卖（G2 fail-safe 跳过）——列入下批对齐清单。
- **P2-14 接受**：资金池 realized 不含费 vs positions_cfg 成本含费，G2/ENS 同口径继承近似，两桥一致可接受。
- **P2-15 已注**：设计文档澄清标记文件每日幂等重写（非仅新触发才写）。

## 2026-09-13 深夜 · DE 执行 P0 全系+P1-1 + TRAE 交叉验证 + 挂调度（T-20260913-001 续，诚哥"让DE执行，我看汇报"）

- **P0-2 G2 特征健康补盲**（feature_health_weekly.py --panel g2，43 特征）：首跑 ALERT0/WATCH5。**重大生产发现：G2_EXTRA 6 源断更**——北向 08-07（35天）/龙虎榜+同花顺行业 08-21（21天）/研报 08-28（14天），moneyflow 新鲜。G2 live 已在"北向/研报/行业特征缺失"下运行（与 AGENTS 主源买断 08-21 一致，但北向更早）。**教训：主数据源买断范围要定期核对增量库实际覆盖表，G2 增强特征依赖的外部表是增量维护盲区**。已挂 retrain 前置与 v3 同点位。
- **P0-3 v3_enh 核实**（de_v3enh_chain_verify_20260913.md）：模型冻结 08-23 无重训链路（run_scheduled 只训 V1.3/G2；train_optuna --model-tag _enh 有能力无调用方）；ENS 仅半自动。**方案 A 补链 vs B 显式拍板冻结，DE 建议 B**（监控已兜底 33 特征+降级闸），升 A 触发条件写死（季评连续两季最末）。附带发现 features_v3_enh.json dropped/feature_cols 4 交集（低危一行修复）。
- **P0-4 ENS 对齐设计**（ens_alignment_design_20260913.md）：三项差异均含改法/风险/dry-run。预算未扣 kept_invest（rebalance_ens.py:384 vs G2:446-452）敞口超池约 40% 唯一实际偏差；无大盘门控（对照 G2:409-424）；hold_dates fail-open 即卖（对照 G2:367-373 事故教训）。建议批次1=差异一+三，涉及实盘须拍板后实施。
- **P1-1 三臂纸面季评**（paper_quarterly_review.py）：复用降级闸同口径，双季最末才提议退役/降配、只提议不动资金、臂间重合率防同源误读。首跑 G2 n=8 超额-0.02423 与降级闸一致；样本不足如实标注。已挂 paper_forward_daily.ps1 16:45 管道尾（QUARTERLY-ALERT 不阻断主链）。
- **TRAE 交叉验证**（诚哥要求）：全部属实——①白名单合规（DE 未碰 rebalance/ps1/模型）；②G2_EXTRA 断源数据源独立复核（Get-Item+read 确认北向 08-07/lhb+ths 08-21/研报 08-28）；③v3_enh 冻结 08-23 复核属实；④季评 n=8 数字与降级闸一致；⑤PS1 语法+BOM 复验通过。
- **遗留待拍板**：①v3_enh 方案 A/B ②P0-4 差异一+三实施（改 rebalance_ens，dry-run+拍板）③features_v3_enh.json dropped 交集一行修复 ④P1-2/3/4（前向底座/M2/回滚真实trial）。09-14 周一看板待观察清单不变。

## 2026-09-19 · R8 回测 vs 历史回测差异归因（T-20260919-001，历史日超额口径证伪）

- **诚哥问题**：R8 评估报告（V14 K2 +6.9% / G2 K2 +26.8% / ENS K2 +25.8% 绝对年化）与历史回测（G2 日超额 +0.213% / V1.4 +0.157% / V2.0「年化 75.88%」）差距怎么这么大？
- **归因**（TRAE 钉死事实 + DE 引擎级消融 + 独立复算核对，报告 `data/schedules/de_r8_scripts/DE_R8差异归因_20260919.md`）：~0.224pp/日差距 **100% 来自两引擎候选构造顺序相反**——历史引擎 `scan_rotate_cost_real.py`「模型 prob top10 → 买入时才筛红线」（**实盘从未执行的选股规则**，日均候选 3.38 只）vs DE/实盘引擎 `de_r8_common.py`「top100 → 先筛红线 → prob top10 → 总分排序买入」（日均 9.5 只）；**相同候选下两引擎记账逐日一致（残差 0.0000pp）**。叠加测试集双重使用（测试窗 100% 落在训练窗内）系统性偏乐观。
- **教训（本文件必读）**：①**回测报告的数字必须先核对「选股规则是否与实盘信号链一致」再引用**——历史 +0.213%/日衡量的是从未上线的策略变体，不可作为上线依据；②**实盘预期锚定 R8 绝对年化口径**（G2 K2 ≈+27%，超额≈0，等权基准本身年化 +27~30%）；③纸面前向才是最终裁判（G2 首批 4 笔 −0.0715%/日、胜率 25% 已示警）；④「年化折算」类数字一律禁止对外引用（审计报告已自标注）。
- **真实可改进点（新发现）**：prob top10 票均笔 +3.05%/胜率 63.3% vs 深挖票（prob 11~100）均笔 +0.38%/胜率 44.2%——「先红线再选 + 总分排序」用弱信号（评分卡总分）稀释了强信号（模型 prob）。买入排序改「prob 优先」待回测+样本外验证（防过拟合）。
- **遗留待诚哥拍板**：①买入排序 prob vs 总分（新增，先验证）②TOP_N 上调 ③买入时刻微调 ④ENS_FUSION 26% 欠配率影子盘验证。
- **看板**：T-20260919-001 已闭环；知识卡 `P16 概述.md` 已同步（顶部新段落 + 旧 +0.213%/+0.211% 引用标注证伪）。
- **DE 独立核查（2026-09-19，诚哥「你让de也核查一下」；报告 `data/schedules/de_r8_scripts/de_r8_candidate_check.md`，任务书 `de_r8_candidate_check_task.txt`）**：TRAE 四条主张全成立（行号微调：红线 L159、picks L178）；**核心新增**：实盘红线后 top10 键 = `total_new`（deploy_predict_g2.py L178），DE 引擎 = `prob`（de_r8_common.py L161-162）——「DE 引擎=实盘信号链」修正为「同族非相同」。**516 天量化反转**：变体 A（prob top10）年化 +26.8%/超额≈0 vs **变体 B（total_new top10）年化 +39.4%/超额 +0.045pp/日**——**「实盘稀释更重」不成立（收益意义上），实盘口径反而更高**；候选集 453/516 天不同、成交重叠仅 15/76；B−A=+0.04pp/日≈引擎间差距 1/3。「R8 数字更接近实盘预期」方向维持；**顺带支持实盘现行 total_new 键，无需为对齐 DE 改排序**（此前「prob 优先可改进点」待重新评估）。**P1 新发现（看板 T-20260919-002）**：实盘 F5 恒中性 5.0 死代码——L112-114 先填 NaN 使列必然存在 → L117/L119 回退分支永假（mf_main_net/ind_pct_ths 永不执行）；多日实盘 CSV SC_F5 全=5.0；实盘评分卡与回测面板存在 **F2/F5 train-serving skew**，每日 top1 可被翻转（09-18 实盘 top1=rank12 600712 vs 快照口径 rank60 300062）；`--ab`/`--ensemble` 分支同模式。改法：L117/L119 判断移到 L112 填 NaN 循环前，**落地待拍板**（改后需模拟端核对 SC_F5 出现真实值）。

## 2026-09-20 · G2 每日盘后选股复跑对比机制（T-20260920-001，诚哥「每天复跑一遍，双重保障」3 点全同意拍板落地）

- **背景**：2026-08-17~09-18 F2/F5 死代码事故 39 天无人发现——A/B/C/D 四防线都查「事后产物」，抓不到「当日运行时执行偏差」（当日主源失效被新浪兜底 / 数据扰动 / 代码回退时，落盘产物仍健康、后续防线不报）。
- **机制（对比口径分级）**：
  ①**留档**：`g2_candidates_night.ps1`（20:05）deploy 全部跑完后 `Copy-Item g2_latest_features.parquet → data_live/snapshot_g2_<runDay>.parquet`（次日 build_g2_daily 会重建覆盖最新快照，不提前留档则复跑无当日输入可对比；留档失败置 snapFail 并入 exit）。
  ②**复跑对比**：`data/schedules/compare_g2_daily.py`（20:10）备份基准 `_g2_top10.csv.compare_base` → 用留档快照重放 `deploy_predict_g2.py --date <runDay> --top 10`（cwd=P16）→ 对比 ts_code 集合/排序 + SC_F2/SC_F5 逐位。
  ③**分级告警**：Tushare 主源（确定性口径）不一致 = **error 告警**（当日运行时主源失效/数据扰动/代码回退）；新浪当日兜底（盘中实时盘后不可复现）= **仅记录差异不告警**（deploy stdout 打印 `F2 主源=Tushare每日快照(主源)` / `新浪当日(兜底,Tushare滞后)` 可自动区分）；无留档快照 = 降级用当前快照仅健康校验（单源结论 + warn，快照缺失本身提示留档步骤故障）。**通知口径（2026-09-20 诚哥拍板「失败成功都通知」）**：PASS/新浪兜底差异推 info 成功蓝卡，error 推红卡，warn 推橙卡；SKIP（非交易日）不推避免噪音，降级路径已推 warn 不重复。
  ④**调度**：schtasks `Quant_P16_G2RerunCompare_Daily` 周一~五 20:10（紧随 20:05 候选、赶在次日快照覆盖前）。退出码 0=PASS / 1=error / 2=warn。
- **验证**：20260918 历史复跑全流程 PASS——F2 主源=Tushare，10 只 ts_code 排序 / SC_F2 / SC_F5 逐位一致，exit=0（当前快照最新日恰=20260918，Copy 为 snapshot_g2_20260918.parquet 模拟留档）。复跑对 paper_forward_live.csv 幂等（按 (date,code) 去重，不产生重复行）。
- **注意**：`de_g2_compare_daily.ps1`（10:05 dry-run 换仓核验 rebalance_g2）与本机制不同，勿混淆勿删。**2026-10-15 推送优化（T-20261015-003，诚哥「我要在飞书看到内容」）**：原推送只有一行报告路径；现在 prompt 强制 DE 报告以「## 飞书推送摘要」节收尾（核验结论/卖出/买入/跳过/风险 5 行表格），脚本末尾抽取该节正文推飞书（抽取失败回退推路径），不再只有路径。同型「仅推路径」问题存在于 `de_rebalance_guard_daily.ps1`（G2/融合/V13 三任务），待确认是否同法优化。
- **看板**：T-20260920-001；repowiki `QMT架构集成.md` 故障排查表已追加「运行时执行偏差盲区」行。
- **2026-09-20 晚间扩展 ENS/V1.3 覆盖（诚哥「只是g2吗，融合的，v1.3的没有吗」）**：
  - **V1.3 留档**：`g2_candidates_night.ps1`（20:05）追加 `Copy-Item latest_features.parquet → data_live/snapshot_v13_<runDay>.parquet`（latest_features 为 16:30 merge 产物，夜间脚本不改动；留档失败置 snapFail13 并入 exit）。
  - **ENS 融合臂**：`compare_g2_daily.py` 追加 `_replay_ens()`（重放 `deploy_predict_g2.py --date <runDay> --top 10 --ensemble --ens-live`）+ `_cmp_ens()`（ts_code 集合/排序 + total_new 逐位）+ `_ens_compare()`（备份 `g2_ens/<runDay>_g2_top10.csv` → 重放 → 按 `_f2_source` 分级，Tushare 不一致=error / 新浪仅记录 / 未知=warn）；退出码 `rc = max(rc_g2, rc_ens)`。
  - **V1.3 复跑对比**：新建 `data/schedules/compare_v13_daily.py`（镜像 G2 模式：`_replay` 用 `deploy_predict.py --date YYYY-MM-DD --model v3 --top-k 10`；对比 ts_code 集合/排序 + model_prob/score_total/SC_F1~F6/combo 共 9 列逐位；**V1.3 纯离线确定性，任一不一致一律 error**；DEGRADE 无留档快照时先查当前 latest_features 最新日==run_day，非当日 warn+exit 2 无法重放校验）。调度 schtasks `Quant_P16_V13RerunCompare_Daily` 周一~五 20:12（注册成功，PowerShell /TR 引号用单引号包裹 + 内部 `''` 转义）。
  - **验证抓到 3 个真实问题（20260918 历史 --no-send + 修复版重生成）**：①**ENS 臂 FAIL-LOUD KeyError bug（已修）**：deploy_predict_g2.py 融合分支只合并 `total_new_real`，FAIL-LOUD 检查引用未合并的 `SC_F2/SC_F5` → `KeyError: 'SC_F2'` 必崩（09-20 加防线 D 时引入，夜间 20:05 尚未触发）；已补 `for f in ("F1".."F6"): en_pool[f"SC_{f}"] = en_sc[f].values`。②**AB 臂同模式 KeyError（重生成时抓到，已修）**：--ab 分支 L238 同样只合并 total_new，夜间链路 `--ab --ensemble --ens-live` 连跑时 AB 先崩（20260918 重生成实测 exit 1）；同法补 6 列 SC_* 合并。③**09-21（周一）ENS 候选仅 1 只（605098.SH）**：09-18 夜间死代码时代「融合无候选通过红线 60 → 空仓」不落盘 → `g2_ens/20260918` 残留 09-17 的 1 只副本，09-18 夜间把它复制为周一 09-21 候选 → 周一 rebalance_ens 将消费仅 1 只的候选。
  - **09-21 ENS 候选处理（已按推荐方案执行，备份 `.bak_repair_20260920`）**：修复版 `deploy_predict_g2.py --date 20260918 --top 10 --pool 100 --ab --ensemble --ens-live` 重生成（G2/AB/ENS 各 10 只，F2 主源=Tushare 确定性），复制 `g2_ens/20260918_g2_top10.csv` → `g2_ens/20260921_g2_top10.csv`（trade_date 改写 20260921）→ 周一 rebalance_ens 消费完整 10 只池。
  - **验证结果**：V1.3 PASS（10 只 model_prob/score_total/SC_F1~6/combo 逐位一致）；G2 PASS；ENS 重放修复后 10 只但基准异常（1 只）→ 标 ERROR——机制正确识别出历史坏产物。

## 2026-09-21 · P16 三策略持仓只数（TOP_N）最优性论证（T-20260921-001，DE 网格 + TRAE 独立审核交叉验证）

- **背景**：诚哥「现在你和De论证的p16项目三个策略持有几只股票是最优的」。09-19 已有 R8 论证（live_trail 实盘口径，`DE_R8_持仓数与买入时刻评估_20260918.md` + `R8_独立复算审核报告_20260919.md` 独立复算 6 点逐位对齐），今日 DE 网格为 **fixed 官方口径交叉验证**（避免重复劳动，定位为第二口径确认）。
- **网格（2026-09-21，DE 产出）**：3 臂 × 7 档 TOP∈{2,3,4,5,6,8,10}=21 组，官方引擎 `scan_rotate_cost_real.py` 原样调用，BT_THRESHOLD=60、N=10、SLIPS=0.001、EXIT=fixed、2024-07-01~2026-08-14（516 交易日），初始资金 10 万（与实盘子账户一致）。产物 `results/grid_top_n_20260921.csv` + `results/持仓只数最优论证_20260921.md`；G2 TOP2 日均超额 +0.149% 与官方锚点精确一致 ✓。**锚点复现校验通过**。
- **结论（fixed 口径）**：V1.3→TOP6（夏普 1.76 全档最高、回撤 -15.40% 最浅、2026 段唯一为正 +2.9%；TOP2 回撤 -36.87% 严重超 15% 红线）；G2→TOP3（TOP2 保留可接受；TOP≥4 断崖 -28pp，头部 alpha 集中在 2~3 只）；融合→TOP6（夏普 1.78、回撤 -17.49% 最浅、胜率最高、2024 段由负转正）。三臂 TOP2 均非风险调整最优。
- **两口径交叉结论**：fixed（官方引擎）与 live_trail（R8 实盘口径）**一致证实 K=2 非最优**。绝对年化两口径差异巨大（G2 TOP2 fixed 79.16% vs R8 live_trail 26.8%），归因出场规则 TRAIL/N(10 vs 15)/红线(60 vs 58)，DE 报告自标注「禁止引用绝对收益」（测试集双重使用系统性偏乐观），仅作档间相对比较。
- **建议（待诚哥拍板）**：G2→3（优先级最高）、V1.3→4~5、融合→5；公共稳健 K=4~5。**升级实盘须先纸面前向验证 30 笔以上**（裁判：样本外超额>0，与 T-20260910-103/104 流程一致）；仅改 TOP_N 参数不动 `capital_allocation.yaml` 额度（10 万/策略不变，Σ=50 万约束不受影响）。
- **联动约束提醒**：①ENS 融合红线内日均仅 9.9 只，K=5 时 26% 交易日欠配（R8 发现，需影子盘验证）；②单日最大换手 30% 全局红线与 N=10 轮动结构性冲突（最差日 60~100%），任何 TOP 档无法满足，需另行拍板豁免口径；③融合/G2 同面板同源持仓高度重合，上调后重叠度变化观察两周。
- **看板**：T-20260921-001；repowiki `QMT架构集成.md` 已同步（复跑对比行 + 持仓数论证行）。
- **✅ 执行落地（2026-09-21 晚间，诚哥拍板「把三个策略都改了，让de来执行，你交叉验证签收」）**：DE 执行 6 文件改动 + TRAE 独立交叉验证签收。**A 组 config**：`g2_config.py:30 TOP_N=2→3`、`g2_ens_config.py:32 TOP_N=2→5`、`rebalance_daily.py:46 TOP_N=2→5`（V1.3 实盘 `--top` 目标持仓=5；T=0 清仓/降半仓语义不变）。**B 组纸面口径**：`paper_forward.py:163` rank<=2→3、`paper_forward_exit.py:231-232/407` 分支条件 + default 2→3（**只改 L232 不改 L231 分支条件会落入 else 变 Top10 全集口径**）、`forward_stats.py:107` rank<=2→3。**TRAE 验证**：git diff 逐条核对 6 文件与 DE 报告一致、miniqmt venv py_compile 6/6、无越权改动（未动 versions/、capital_allocation.yaml、未 commit）。**TRAE 补修 ps1（DE 未授权改 ps1 的盲区，4 个均补 UTF-8 BOM + PowerShell 语法 OK）**：`paper_forward_daily.ps1:35 --top 2→3`（否则覆盖 default=3 落入 else 分支）；`de_rebalance_guard_daily.ps1:60 --top 2→5` + L61/L74/L87 核验文案（V1.3/ENS/G2 各自 top5/top5/top3）；`g2_candidates_night.ps1:22 --top 2→3` + L6/L31/L70/L24 文案（夜间 top3 候选 + 次日副本）；`de_g2_compare_daily.ps1:35 top2→top3` 文案。**资金校验**：`check_capital_allocation.py` PASS（EXIT=0，10 万/策略不变）。**遗留待拍板**：①三臂共用脚本 `paper_forward_downgrade.py:154`/`paper_forward_ab_stats.py:93`/`paper_quarterly_review.py:70` 单一 rank<=2 阈值不再对齐任一臂（3/5/5 分化）；②`deploy_predict_g2.py:209` 过时注释 TOP_N=2；③升级实盘前纸面前向验证 30 笔门槛 / ENS 欠配 26% 影子盘 / 单日换手 30% 豁免口径。


## 2026-09-22 · 夜间检修 B2/B5/D1 三项修复（T-20260922-001，01:00 nightly_check FAIL=3 复跑 FAIL=0）

- **B2 模型-面板绑定不一致（P0 级）**：09-21 周更 auto_promote 上线 6617 树候选 → 18:41 策略层门禁 FAIL（excess +0.0008 < live +0.0011）→ run_scheduled.ps1 回滚了正式模型文件但**未回滚绑定记录** → verify 报「模型被改动但未登记」。**根因=回滚链路只管模型文件不管绑定，绑定唯一真相源被留在「新模型」状态**。修复：①绑定回滚至 09-14 版（4151 树/mtime 精确一致，git fb75a96 原版 + 修复备注），verify_model_panel_sync.py exit=0；②promote_model.py 提升前自动备份绑定（model_panel_binding.json.bak_<stamp>）；③run_scheduled.ps1 门禁 FAIL 回滚分支同步回滚绑定（BOM EF BB BF 复验完好）。
- **B5 enh 面板滞后 + 刷新校验失败（P1 级）**：09-14 E 盘离线包全量替换主库（daily 08-21→09-11）后，旧面板末日 09-09~11 的 turnover_rank 由「增量反推（float_share=08-21 vintage ffill）」变「主库原生」，换手率微差 → 精确复现 0% 触发门禁拦截。**实测每日 Spearman 0.997+，定性=数据源 vintage 升级而非口径 bug**。修复：refresh_panel_enh.py 门禁分级——4 事件特征仍要求 ≥99% 精确复现；turnover_rank <99% 时查 Spearman，≥0.98 判「vintage 升级」放行、<0.98 仍 FAIL。实跑重刷 enh 面板至 **2026-09-21**（旧面板备份 .bak_20260922_023037），check_enh_freshness.py 落后 0 天 PASS。
- **D1 悟道探活盘前时序伪失败（P2 级）**：01:00 无 date 参数请求「当日」市场概要，上游当日概要未就绪 → 恒 FAIL → fail_count 日增 → 熔断常开，09-04 起每日靠人工 record ok 收尾。修复：nightly_check.py D1 探活显式 date=<最近已收盘交易日>（与人工复核同口径）；02:25 实测 date=2026-09-21 成功（强度 84）→ record ok → 熔断清零。
- **端到端验证**：全量 nightly_check.py（无 --fix）复跑 **FAIL=0 WARN=1 exit=0**；01:00 原报告备份 data/cache/nightly_check_20260922.md.bak_0100_run。
- **教训**：①回滚链路必须覆盖该资产登记的全部真相源（模型文件+绑定+指针），只回滚文件=半吊子回滚；②数据 vintage 升级（反推值→原生值）会表现为「精确复现崩、秩相关仍极高」——门禁应区分「口径漂移」与「源升级」，否则数据升级日必误拦；③探活类检查请求的资源必须是「该时刻必然存在」的（T-1 而非 T 日）；④**PowerShell 双引号 here-string 里 markdown 反引号是转义符**（`n=`换行/`r=CR/`t=Tab，写文件必腐蚀，本文 09-13 段 1031/1035 行同源损伤），写含反引号文本一律用单引号 here-string @'...'@。
- **遗留待拍板**：①E6 TW 调度快照过期（09-13）未修，需 Schedule list 刷新——**✅ 已闭环（09-22 07:30 刷新+renderer 日志交叉验证，见下节）**；②09-21 G2 周更重训被 enh 陈旧阻断未跑成，本周是否人工补跑 train_g2 --promote 待拍板——**✅ 已闭环（09-22 07:3x 补跑，候选被 strict 门禁 G3 拒绝，live 维持 09-15 版，见下节）**；③绑定 gap 设计内预警：面板(09-21)超前绑定(09-14)，09-23 起 verify 将告警「模型待周更 promote」——09-21 候选被双门禁拒绝属设计行为，下周一重训过门禁后自愈，**不要为此放宽阈值**。

## 2026-09-22 · 夜间检修遗留两项落地：G2 周更补跑 + E6 快照刷新（07:25~07:55 会话）

- **G2 周更补跑（09-21 被 enh 陈旧阻断的补跑，用户拍板执行）**：链路复刻 run_scheduled retrain 分支的 G2 段。①check_enh_freshness PASS（B5 修复后面板 2026-09-21，落后 0 天）；②promote 前 live 备份 `models/bak/lgb_model_v3_g2_strong_real_pre_retrain_20260922_073000.txt`（MD5 与源一致；retrain 分支的 PS ConvertFrom-Json 因指针 JSON 含 `Infinity` 解析失败会跳过备份——09-21 实证，本次手工 Copy-Item 绕过）；③`train_g2.py --promote`（TRAE python）：候选 `0922_1705t`（43 特征，test IC 0.07958，指标 ic=0.14846/tail=0.17425/**top2=+0.0135**）vs live 0915_1522t（ic=0.14523/tail=0.17342/**top2=+0.0155**）同窗对比 → **G3 top2_ret FAIL，门禁整体 FAIL，live 指针未动**（维持 09-15 版至下周一）。设计内 fail-loud，不放宽阈值不强制上线。日志 `data/real/train_g2_rerun_20260922.log`。
- **在线增量未重跑（有意跳过）**：09-21 retrain 已跑（lhb→09-21、北向→09-18 达标；report_rc 被限频属 best-effort 设计内）；rc 断源由特征健康周报 WATCH 兜底，且 09-15 live 训练时 rc 同样只到 09-11，G1 同窗对比公平。策略层门禁与 config_drift_recheck 因无 promote 按设计跳过。
- **P1 新发现（待拍板修复，本会话未改）**：`run_scheduled.ps1` L356 G2 策略层门禁命令不传 `--live` → `gate_strategy_layer.py` 缺省读当前 live 指针，而 train_g2 --promote 已把指针指向新候选 → **候选自己比自己，diff=0 恒 PASS，--rollback 分支死代码**（融合分支同病；V1.4 分支显式传 pre_retrain 备份 --live 不受影响）。修法=仿 V1.4 传 `--live <pre_retrain 备份>` 或 gate 缺省改读 trial.prev_model。意味着：历次 G2 promote 后的策略层门禁可能均未真检验（需回溯 09-15 版 promote 是否人工跑过带 --live 的门禁）。
- **E6 TW 快照刷新**：本会话无 TRAE Schedule list 直连能力，改用 **TRAE renderer 日志触发证据重建**（`TRAE SOLO CN/logs/*/renderer*.log` 的 scheduled_task_triggered 事件）：10 Active 任务 09-15~09-22 每交易日全有触发、3 Paused（eda9b0c3/c63cff58/f43df15a）零触发、快照外新任务 **5f2bd77c 夸克行情数据每周更新检查**（09-13 后新建，观测 09-21T03:00 定时触发与 cron `0 3 * * MON` 吻合）。与 07:30 并发会话写入的快照内容**完全一致**——快照已被刷新，遵守双写红线不重复写，本会话独立交叉验证签收。方法可复用：无 TRAE 面板时用 renderer 日志重建清单+状态。
- **端到端验证**：nightly_check.py（无 --fix）复跑 **FAIL=0 WARN=0 exit=0**；E6 PASS（快照 14 项新鲜，无 Active 冲突）；B2 模型-面板绑定一致维持。全程无 commit、无下单、无复盘-下单混淆（仅训练/门禁/检查类脚本）。

## 2026-09-23 · B2「模型-面板同步」反复事故彻底修复（T-20260923-005，授权「全权处理」，交易时段未跑重训）

- **根因链（结构性循环，已复发多次）**：策略层门禁每次拒绝候选 → 模型回滚到上周版本、绑定回滚 → 面板继续日刷 → gap>7 → B2 每天 FAIL，直到下次周更成功。09-21 回滚未滚绑定的 P0 已在 T-20260922-001 修；09-23 gap=8 天 >7 此前必 FAIL——告警系统把「设计内等待」误报为「反复事故」。
- **彻底修复五项**：
  1. `verify_model_panel_sync.py` 新增 `gate_rejected` 感知——gap>7 但门禁拒绝在期望重训窗口内（`next_retrain>=今天`）→ **WARN/exit 0**（设计内等待）；超期未 promote → 恢复 FAIL（真·周更失败）；树数/mtime 不一致仍硬 FAIL（回滚链路 bug 不放过）。
  2. `run_scheduled.ps1` V1.3 **策略层门禁 BEFORE auto_promote**——旧序 promote→策略层→FAIL回滚=每次拒绝留半吊子回滚窗口；新序策略层先跑（candidate vs pre_retrain 备份），PASS 才进模型层；FAIL=从未 promote，只写 `gate_rejected{at,layer,candidate,reason,next_retrain=下周一}`，无需回滚任何文件。门禁运行异常/缺文件 = fail-closed 拒绝 promote。
  3. `run_scheduled.ps1` G2 门禁补传 `--live $g2LiveBak --live-meta $g2OldMeta`（修复 P1：promote 后指针==候选 → 自比较恒 PASS，`--rollback` 死代码；promote 前同时捕获 old meta 路径）。
  4. `gate_strategy_layer.py` G2/融合缺省 live 解析防御——candidate==当前指针时回退 `trial.prev_model`，无可用回退则 exit 1 拒绝自比较。
  5. `scripts/nightly_check.py` B2 解析输出含 `WARN` → 状态记 WARN 而非 FAIL（`write_report` 已支持 WARN 分类，FAIL 才告警）。
- **`promote_model.py`** 成功 promote 重写全新 binding dict → `gate_rejected` 自动清除（无需显式删）。
- **回溯补状态**：当前绑定已写入 09-21 18:41:26 策略层拒绝记录（`next_retrain=2026-09-28`），立即生效。
- **验证（全部 PASS，未跑训练）**：`verify_model_panel_sync.py` exit=0 输出 WARN；`nightly_check.check_b` 复跑 **FAIL=0 WARN=1**（B2=WARN）；4 文件 py_compile exit=0；`run_scheduled.ps1` BOM `EF BB BF` 完好 + Parser `PARSE_OK`；binding `gate_rejected.next_retrain=2026-09-28`。
- **边界**：仅改门禁/校验/调度层，未动训练、模型文件、实盘下单；下次周更 **09-28（周一）17:00** 首次验证新序（策略层 BEFORE promote）。
- **看板**：T-20260923-005；repowiki `QMT架构集成.md` 新增「B2 反复 FAIL」行已同步；`WORKFLOW_REGISTRY.md` 四节已更新两条路径门禁顺序与 G2 --live。

### 09-23 周训补跑实录（10:07–12:31，新序首次实战）+ 两尾巴修复

- **V1.3 路径（策略层 BEFORE promote 新序首跑成功）**：面板刷新到 09-22 → train_optuna 候选 `lgb_model_v3_retrain_20260923.txt`（4151→候选，test IC 0.04554/ICIR 0.3605）→ 策略层门禁 **FAIL**（candidate excess **-0.0004** vs live **+0.0011**，差 -0.0015 > 容差 0.0002）→ **直接写 `gate_rejected(next_retrain=2026-09-28)` 并跳过 auto_promote，正式模型零改动**——旧序 promote→回滚 的半吊子窗口不复存在，新序实证成立。
- **G2 路径**：train_g2 候选 `0923_2576t`（ic=0.07821/tail=0.1174/top2=+0.0156）vs live `0915_1522t`（ic=0.07905/tail=0.1257/top2=+0.0269）同窗 → **strict 三项全 FAIL → live 不变**（维持 09-15 版）。在线增量 lhb/北向 OK、report_rc 超限（10次/日额度，不阻断）。
- **尾巴1 BOM 崩溃（本轮暴露）**：`verify_model_panel_sync.py --warn-only` 读 binding 抛 `Unexpected UTF-8 BOM` exit=1——根因是 `run_scheduled.ps1` 的 `Set-Content -Encoding UTF8` 写 binding **带 BOM**（`EF BB BF`），Python `encoding="utf-8"` 读炸。**修复**：`verify_model_panel_sync.py`、`auto_promote.py` 读 binding 改 `encoding="utf-8-sig"`（BOM 有无都能读）。py_compile OK。
- **尾巴2 G2 指针误报（本轮暴露，09-21 已知未修）**：日志 `G2 live 指针缺失，跳过备份`——根因 `g2_live_model.json` 的 `quantile_fwd_ret["5"]: Infinity`（Python json 非标准写入）→ PowerShell `ConvertFrom-Json` 抛异常被 `catch{}` 吞 → `$g2Cur=null` → promote 前备份被误跳过 + `$g2OldMeta` 为空 → 下轮 G2 策略层门禁缺 `--live` 会重演自比较。**修复**：`run_scheduled.ps1` 改用正则提取 `model_path`/`meta_path`（不依赖 JSON 解析器接受 Infinity），已实测提取到 09-15 live 且文件存在。BOM `EF BB BF` + PARSE_OK 复验。
- **验证**：`verify_model_panel_sync.py --warn-only` exit=0 输出 WARN（gate_rejected=09-23 11:17:50）；`nightly_check.check_b` **FAIL=0 WARN=1**；binding 经 utf-8-sig 正确解析含 `gate_rejected`；ps1 BOM 完好。
- **边界**：正式模型仍 4151 树/09-14 版；G2 live 仍 0915_1522t；候选文件已留档待 09-28 复训对比。未动实盘下单。

### 09-21 周训执行核实（T-20260923-006，防误判为任务失败）

- **结论（三处独立佐证）**：`quant_weekly_retrain` 09-21 **LastRun=17:00:00 LastResult=0**（任务本身成功）；日志 `retrain_20260921_170003.log` 220 行完整（17:00 启动→19:42 结束）；正式模型 mtime=09-14、MD5 与 `models/bak/lgb_model_v3_pre_retrain_20260921_170116.txt` **完全一致**（回滚成功）。
- **V1.3 链**：train_optuna 产候选 6617 树（18:07:39）→ 模型层 G0~G6 全 PASS、auto_promote 短暂上线（18:07:41）→ **策略层门禁 FAIL**（excess +0.0008 < live +0.0011，差 -0.0003 > 容差 +0.0002）→ 18:41:26 **回滚正式模型至 09-14 版**。**正式模型未更新 = 门禁有意拒绝差候选（设计内 fail-loud），不是调度没跑、不是任务执行失败**。
- **G2 链同日**：`refresh_panel_enh` 校验失败（turnover_rank 复现率 0%，vintage 漂移）→ `check_enh_freshness` 落后 10 天 > 7 → **硬门禁阻断 G2 重训**（日志 L68/L218），G2 未训练属数据新鲜度阻断，亦非任务故障；09-22 人工补跑也被 strict 门禁拒（见 T-20260922-001）。
- **伴生 P0（当时暴露、次日已修）**：回滚只还原模型文件**未回滚绑定** → verify 报「模型被改动但未登记」→ T-20260922-001 修（绑定回滚 + promote 前备份 + 回滚分支同步写绑定）。
- **防误判记录落点**：①`results/周训执行核实报告_20260921.md`（完整时间线+判定）；②`VERSIONS.md` 0921 上线记录已加「已回滚、非任务失败」批注；③binding note 已注明「09-21候选策略层门禁FAIL已回滚」。**后续任何人见「正式模型仍 09-14 / gap>7」，先看本核实报告与 gate_rejected，勿直接定性为周训任务故障**。

## 2026-09-23 T档 vs TOP_N 语义澄清（T-20260923-007）
- 诚哥问「持仓仍2只、自动化仍判TOP2」：**TOP2=T档位不是持仓只数**；eff_top 已归一 TOP_N=5。
- 落盘：market_tier.py semantics/建议行 + rebalance_daily.py dry/live JSON 记 tier+eff_top + WorkBuddy 配置明细 §三-1 澄清。
- 持仓仍2只=09-23 过红线0票宁缺毋滥 + 09-21/22 受旧 --top bug（09-22晚已修）。
- 规则：读 tier/top=2 先查同 JSON eff_top 与 TOP_N，勿当持仓只数。

## 2026-09-24 G2 评分卡 SC_F1/F3/F6 nunique=1 排查（T-20260924-001）
- 对象：`data/selections/g2/20260924_g2_top10.csv`（09-23 快照、trade_date=20260924）。
- **SC_F6=数据源失效（实锤）**：`MAIN_DAILY` max=2026-09-11 滞后 12 天 → `deploy_predict_g2` est reindex 全 NaN → `review_full.score_f6` nan→5.0 恒值；`scorecard_real.compute_real_scorecard` est 非 None 即整列用 est、**不按行回退快照 pe_ttm/turn_ma5**（快照 pe 67% 非空、turn_ma5 全有，本可兜底）。Updatedata 最新仅 `8.31-9.4`，主库 09-11 后无增量包。
- **SC_F3=面板宇宙覆盖断裂（非面板内事件源坏）**：`feature_panel_v3_enh` 09-23 仅 **2791 只**（v2 dropna BASE_FEATURES 收缩，universe 5488/stock_basic 5904），快照全市场 5839 → `build_g2_daily` map 后 **52.2% 事件列 NaN**；**top10 全部 0/10 不在面板** → `_score_f3` 加成条件全 False → 恒 4.0。面板内事件列 0% NaN；finance 源 forecast/express max=20260729、dividend=20260821 滞后是次因。
- **SC_F1=分档桶效应（非数据源）**：pos_250 健康（快照 nunique=5287、nan 7.7%），top10 全落 [0.448,0.660] ⊂ <0.72 档 → `_score_f1` 恒 8.0。
- **F2/F5 主源健康**（night log：Tushare 96/100 + sina 4/100，FAIL-LOUD 未触发）；重放 top10 与 CSV 代码序列完全一致。
- 修复待拍板：A. scorecard_real est 全/行 NaN 回退快照 pe/turn（最小、只救 F6）；B. 催刷主库 Updatedata（治本，需 E 盘包）；C. F3 面板宇宙扩覆盖或 build_g2_daily 面板外 fail-safe（改选股口径需重验）；D. F1 不修。
- 规则：SC_F6 恒 5.0 先查 MAIN_DAILY max 与 est reindex；SC_F3 恒 4.0 先查候选是否在 enh 面板宇宙内。
- **✅ 方案 A 已执行（2026-09-25 诚哥拍板「动手修」，改动落 `deploy_predict_g2.py` 而非 `scorecard_real`）**——完整报告：`results/SC_F6数据源失效排查与修复报告_20260925.md`：
  - **落点选择**：`scorecard_real.compute_real_scorecard` 被 `scan_rotate_cost_real`/`optimize_scorecard` 回测共用（口径须冻结），故改在 live 侧 `deploy_predict_g2.py`——est 构建后加**命中率自检**：`est["pe_ttm"]` 非空率 < 50% 视为主库滞后快照日 → `est=None`，让 scorecard_real 走**设计内**的快照 `pe_ttm`/`turn_ma5` 兜底（`build_g2_daily` 已按 EPS 反推 T 日口径，G2_RUNBOOK 验证中位误差 0.00%）；`--ab`/`--ensemble` 臂的 `ab_est`/`en_est` 同判定（`est is None` 时直接 None）。主库新鲜（命中 ≥50%）时行为不变，回测零影响。
  - **只读验证（2026-09-25 快照，miniQMT venv，未写生产文件）**：est 命中率 0.0% → 修复前 Top100 SC_F6 恒 {5.0:100}；修复后 nunique=5，dist={1.0:14, 4.0:8, 5.0:55, 7.0:17, 10.0:6}，**均值仍 5.0 无系统性偏移**（红线穿越无方向性影响）；py_compile PASS。
  - **生效时点**：09-25（周五）18:14 改毕，当晚 20:05 `g2_candidates_night` 首跑修复版；此前 09-24 夜间产物仍是旧口径（SC_F6=5.0），属预期。20:15 `check_scorecard_health` 的 SC_F6 warn 应消失（除非快照 pe/turn 也断）。
  - **遗留**：①方案 B（主库停 09-11，需 E 盘离线包人工催刷）未做——兜底已让管线对主库滞后免疫，B 非紧急；②~~方案 C 未做~~ 已于同日执行（见下）；③修复版首夜（09-25）核对（中秋休市 target 停 09-24，产物=覆盖 `20260924`+复制 `20260928`）：日志 `[F6] 主库 est 命中率 0% → est=None` + `[F3] 面板外事件 fail-safe` 两行、CSV SC_F6/SC_F3 均非恒值（干跑预演 n=7 过红线，空仓则 CSV 项顺延）、20:15 health check 双 warn 消失。
  - **✅ 方案 C 已执行（2026-09-25 诚哥拍板「F3 的问题也修了」，落 `deploy_predict_g2.py` 池副本而非 build_g2_daily 快照）**：`_fill_f3_events()`（L61）在三处 `compute_real_scorecard` 前（主臂 L251 verbose / AB L338 / ENS L388）对四列（fc_days/ex_days/fc_pchange/dv_year_sum）NaN 行按 `build_features_v2` 同源 PIT 口径重算（ann_date<=T backward、90 天事件窗、365 天分红窗回溯至多 30 条）；**只填 predict 之后的评分池副本 → 模型输入/输出零变化**（四列同时是 g2 43/AB 33 模型特征，改快照会动 Top100 构成不可归因）；源异常保持 NaN 中性不阻断。验证：py_compile PASS；重放 Top100 F3 nunique **1→4**（{4:60,5:7,7:25,9:8}）、红线≥60 穿越 **0→7**、rank corr 0.8398、prob 逐位不变、PIT 抽查 fc 3/3+dv 2/2 精确；端到端干跑（输出重定向临时目录、生产零接触）主臂 7 只过线 + AB/ENS 全通过。报告七/八章：`results/SC_F6数据源失效排查与修复报告_20260925.md`。
  - **✅ 首跑核对（2026-09-25 20:05~20:23 实录，报告九章）**：夜跑 `[F6] est=None` + `[F3] fail-safe 100/100` 双行出现、top10/top3/AB/ENS/ENSLIVE 全 exit=0；产物 `20260924` n=7 与干跑逐位一致（F3=3 档/F6=2 档，两死维度解除）；20:10 compare PASS（**空仓一致性路径**：按运行日键控、假日两侧均空，逐位验证由干跑对生产完成）。**附带发现**：①假日 `g2_candidates_night.ps1` 次日副本按 `$runDay` 找源必跳过（产物按 target 命名）→ 0928 四副本停旧口径，已按脚本语义手工补生成 4 文件（夜志 `[MANUAL] 20:22:59` 留痕），health 复跑仅剩 F1 设计 warn；**ps1 根因（源改按快照 target 日定位）待拍板**。②health `latest_top10_csv` 取文件名最大者→假日查的是次日副本非当日产物（周中无碍），**改 mtime 选文件待拍板**。③**schtasks LastResult 经 `powershell -Command` 包装会把 python exit 2(warn) 显示成 1（与 exit 1 error 不可区分，实证 `cmd /c exit 2` 包装后=1）——判定健康勿信 LastResult，复跑 `--no-send` 或看飞书卡级别**。

## 2026-09-25 训练切分加 embargo（T-20260925-001）
- 起因：评估外部文章《会自己进化的ETF自动交易系统》（其用 Purged K-Fold + 5日 embargo）时发现 P16 全项目 **无 embargo / 无 purge**——固定三段与季度滚动切分首尾相接（train 2023-06-30 → valid 2023-07-01）。
- **泄漏机理**：G2 标签 N3 = T+1 开盘→T+4 开盘（跨 4 个交易日的价格点），训练窗末尾若干行的标签窗口落进验证期 → 训练样本标签含验证期行情信息 → valid IC 被轻微抬高，early stopping / 超参搜索对着「掺水考官」调参。
- **修复（三脚本同口径；审计勘误：原记「各 +18 行」不实，git numstat 实测 train_baseline +24/-1、train_g2 +36/-4、train_optuna +28/-3——含 17 行 `train_cutoff` 函数 + 各调用点/日志/报告字段改造）**：新增 `train_cutoff(dates, hi, embargo)` —— 训练上界 = hi 及之前允许的最大交易日**往前数 embargo 个交易日**（按真实交易日历取数，不用 DateOffset 天数，避开非交易日误差）；`embargo=0` 时严格回到旧口径。
  - `train_g2.py`：`EMBARGO_DAYS = 4`（= N3 标签跨度 4 个价格点），固定三段 train 上界 2023-06-30 → **2023-06-26**。
  - `train_baseline.py`：`--embargo-days` 默认 1（标签 horizon=1 日），可命令行覆盖。
  - `train_optuna.py`：`--embargo-days` 默认 1，**季度滚动折同步生效**（`m_r` 由 `date < fs` 改为 `date <= train_cutoff(fs, args.embargo_days + 1)`；**审计 T-20260925-001 P1 修复（09-25）**：fs 是 valid 首日而非训练日，需额外跨过 fs 本身，故 embargo+1 = fs 前 2 个交易日，修后 21 折全部干净、逐折 cut 写入 `report["rolling"].fold_cuts` 并打日志）；optuna_report.json 的 `splits.train` 改记实际训练上界 + 新增 `embargo_days` 字段，报告可审计。
- **实测（feature_panel_v3，1863 交易日 2019-01-10~2026-09-24）**：剔除 2023-06-27/28/29/30 四个交易日，train 2,075,966 → 2,066,366 行（-0.462%）；末样本 2023-06-26 的 N3 标签窗口末点 = **2023-06-30**（审计勘误：原记 06-29 是 +3 口径之误，06-26 后续 4 个交易日为 06-27/28/29/30）= 训练期最后交易日 < 2023-07-01，**已完全落在 valid 期之前，边界泄漏消除——embargo=4 为精确最小值，末点恰压训练期最后交易日、一点不宽松**。rolling 折 fs=2022-01-01 → 训练上界 2021-12-27 系 **embargo=4 口径**的数字（审计勘误：此语境是 train_optuna 默认 embargo=1，滚动折经 P1 修复按 embargo+1=2 回缩，实测训练上界 **2021-12-29**，末样本标签日 2021-12-30 < fs ✓）。
- **边界健壮性（已单测）**：embargo 超过可用交易日数时钳到最小不抛错；空序列 / 上界之前无交易日返回 None，调用点用 `or pd.Timestamp(...)` 兜底回旧上界。
- **踩坑**：`train_cutoff` 首版直接用 `hi` 比较 Timestamp，三个调用点传的都是字符串（如 `"2023-06-30"`）→ `Timestamp <= str` 抛 TypeError。已在函数入口 `hi = pd.Timestamp(hi)` 归一，单测用真实调用形态验证通过。
- **影响定性**：改动幅度小（污染样本 ~0.5%），**不会改变收益结论**；价值在口径干净 + 按 Purged K-Fold 标准审计可过关。真正的大头仍是「回测期 ⊆ test 集双重使用」（见 09-09 ⑤ 与 T-20260909-001），embargo 只是顺手补的小缝。
- 验证：三脚本 ast.parse 语法通过（审计勘误：原记 395/186/286 各多计 1 行，实测 **394/185/285**；P1 整改后 train_optuna 增至 292 行）+ `train_cutoff` 单测 + 真实面板边界打印全 PASS（一次性脚本已删，不留仓库）。
- **未动**：`overnight_20260825.py` / `overnight_opt_20260825.py` / `versions/V20260909_*/train_g2.py` 仍为旧口径——它们是历史归档/一次性研究脚本，不属生产重训路径；若日后要复现 G2 生产训练，用 `train_g2.py` 即已带 embargo。


## 2026-09-28 · 候选可审计性修复落地（三策略溯源 + asof 留档 + F7 历史覆写修复，授权「新旧一起修」）

**背景**：G2 桥账本 -4.01% vs 纸面前向 +2.945%(t=3.21) 背离的根因定位（r2 报告）判定机制设计正确、缺陷在审计可观测性——历史候选文件可被 5+1 个写手事后改写且无任何留痕（实证：`20260924_g2_top10.csv` 于 09-28 16:48 内容被改，002617/002569 → 603272/003029）。

**修复内容**（方案 `specs/候选可审计性修复方案_20260928.md` §8，D1-D5 决议见该文档）：
- `rebalance_g2.py` / `rebalance_ens.py`：plan 新增 7 溯源字段（`candidate_file/loader/data_date/rows/rows_raw/sha256/asof`）；`load_g2_selection` 单次读盘（bytes→sha→留档→StringIO），读取点把原始字节落 `candidates_asof/<date>_<sha8>_<basename>` 只读留档（sha 命名幂等、内容不同共存，天然展示文件漂移）。
- `rebalance_daily.py`（V1.3）：修回 0903 起丢失的 `selection_file` + 同套 7 字段（留档 `data/candidates_asof/`）。
- `compare_g2_daily.py`：新增 `_backup_baseline`/`_restore_baseline`，主流程/DEGRADE/`_ens_compare` 全路径备份→复跑→恢复（原件不存在则删复跑新建）——**复跑对比能力保留，盘上永远留原件**。
- `test_g2_rerun.py`（方案外新发现的第 6 写手）：重放前备份 `.rerun_base`、校验/失败后恢复。
- 未动：`g2_candidates_night.ps1`（机制正确）、`deploy_predict_g2.py`、一切持仓/账本/下单路径。

**验证**：py_compile 5 文件全过；G2/ENS/V1.3 dry-run 全字段落地（V1.3 用 20260922 有票日验证 `rows=2 过滤后 / rows_raw=10 原始` 口径区分，且 sha 复算 picks 与当日实盘记录一致=该日文件未被污染的旁证）；compare F7 合成测试 3/3、rerun 2/2；15 个 json 消费者全部 `.get()` 按名取值，新增字段零影响（方案 §7.2 R1）。

**残留缺口（运维注意）**：
1. **直接手动 `deploy_predict_g2 --date <历史日>` 仍是裸写手**——补跑历史日期必须走 `compare_g2_daily.py`（自带备份恢复）或手动先备份。
2. V1.3 的 TRAE 09:45 任务 inline 写 json——**09-29 已实证坐实**：当日 `rebalance_20260929.json`（10:10:05）无 7 字段且含全项目任何脚本都没有的 `live_invoked` 键=agent 手写铁证（当日系红线空仓日，脚本早退未产 json，故为「补写」而非「覆写」；正常交易日同一行为即真覆写——旧 prompt step 4「保存换仓结果到 data/rebalance_<date>.md/.json」字面指示 agent 写该文件）；读取侧 asof 已兜底（10:02:16 落 `20260929_24383fb1_selection_full.csv`，live 所读文件内容有档）。**修改说明已生成（09-29）：`specs/TRAE任务修改说明_0945开盘实时复核_20260929.md`**——step 4 改只读核对 + 禁写 rebalance json + 备注/零单事实走独立 `rebalance_<date>.trae_note.json` + 4b 缺 `candidate_sha256` 告警，待诚哥转 TRAE 执行，下一交易日按文件第四节验收。
3. V1.3 当日 0 票早退不写 json（保留原行为护 10:05 guard 幂等标记）。
4. 09-24 历史文件不回改：现内容=修复版重放产物（与 09-28 实盘读入一致），09-24 早真实读入由 `.compare_base`（002617/002569）保全。
5. D5（纸面统计用副本内容重算）暂不做——先积累带 sha 的候选 + 留档，有可验证输入后再做，那是解开背离定论的最后一块拼图。
6. F8（source 定位读快照 trade_date.max）待 10-09（国庆前最后交易日）首次实测。
7. **ENS 盲点补修（09-28 20:10 首跑实测发现并当晚修复）**：ENS 重放（`--ensemble --ens-live`）会**同时重写 G2 主目录**同名 csv+md（deploy L309/L320 无 ensemble 短路），`_ens_compare` 原只备份 ENS 侧 → 已扩为三文件备份/恢复。今晚因重放确定性未污染（逐字节核对一致）。**教训：跨目录写入的复跑工具，备份清单必须覆盖重放命令的全部写入目标，不能只按对比对象配置。**
8. **compare_v13_daily.py 同款 F7 修复（09-29 晨，定时任务盘点发现）**：V13 每日复跑对比（工作日 20:12）**只备份从不恢复**，且污染的是**次日实盘活输入**（V1.3 候选 16:47 生成 → 次日 09:15 预生成幂等跳过 + 09:45 复核直接消费同一文件）而非纯历史文件；DEGRADE 路径完全无备份；备份清单漏 `<D>_selection_dual.md`（重写 csv+md 两文件）。已修：helpers + **try/finally 兜底恢复**（E2E 实测教训：重放 stdout 含 `\ufffd` 在 GBK 控制台 print 崩溃，崩点恰在重放后恢复前——恢复不 finally 兜底必留 F7 缺口）+ `_safe_print` 乱码降级。验证：合成测试 12/12 + E2E `--date 20260928` exit=0（恢复日志行出现、盘上 csv 与 16:47 原件 sha 一致）。09-28 晚旧代码覆写的 20260928 文件已于 09-29 晨从 `.compare_base` 恢复（恢复前抢存副本 hash 一致 + 昨晚 compare PASS = 实盘消费无实害）；09-21/22/23 历史按先例不回改。**教训：恢复动作必须放在 finally 里，try 块内任何一步（包括打印）抛异常都不能跳过恢复；复跑工具的 stdout 打印一律用安全降级，子进程输出编码与父进程控制台编码不一致（GBK↔utf-8）是常态。**

## 2026-09-28 · 候选口径纪律（诚哥追问「第二天拉实时 F1-F6 还有必要吗」后立）

**大原则（硬规则）**：任何「T+1 实时数据进打分」的路径，必须先过纸面前向或回测验证，否则不进买入链路。实时数据的合法位置只有两个：①预警/预判报告（给人看，不回灌打分）；②夜跑失败时的兜底重算（且兜底必须仍是 T 日口径——G2 现状即如此：moneyflow parquet 19:30 已刷 T 日数据，mf_fresh=True 走主源，--date 缺省=快照最新日=T 日）。

**依据（回测口径代码实证，scan_rotate_cost_real.py）**：T 日收盘截面打分（模型特征 + F1-F6 全部 T 日日频值，est=daily.reindex([(T,code)])）→ T+1 开盘成交（open_next=shift(-1)，一字板/停牌过滤）。夜跑 20:05 与此逐点同口径；盘中实时数据盘后不可复现（compare_g2_daily「新浪兜底差异不可复现」分支为证），进打分=未回测 skew + 审计毒药。r2 旁证：背离根因之一即「rank 排序有效性不足」，弱环节上叠加实时重排是加噪声。

**三路径定性**：A 打分/买入——G2 已是「夜跑为正、实时仅缺失兜底」，正确；B 兜底重算——保留（价值在重算不在换数据）；C 集合竞价预判报告——保留（每天唯一人工风险扫描），但严禁回灌打分。

**唯一待裁决存量**：V1.3 的 09:45 实时复核（F2/F5 30% 权重重排候选内排名，直接产出 rebalance 读的 selection_full）——全系统唯一「实时数据喂买入」的存量路径，V1.x 时代设计，从未回测验证（回测无盘中 F2）。**裁决方法**：用 paper_forward 基础设施对比「09:15 预生成原始排序前 N」vs「09:45 重排后前 N」的前向收益——无显著正贡献则砍掉回归纯 T 日口径，有贡献则正名为「已验证增强」并在 rebalance json 记录重排数据源。裁决前不动它（没坏先不拆，但标注为未验证 skew）。G2 上线时未带此机制=已隐式选纯口径。

## 2026-09-29 · G2 部署选股序对齐引擎（T-20260929-005 方案1，诚哥拍板）

**起因**：XVAL5（T-20260929-001）工作包 C 判定「需要改代码」——引擎红线作用池 = Top10(prob)，部署旧序红线作用池 = Top100，成交票重合仅 6/102（Jaccard 0.031）；0825 已验证配置反事实：部署旧序把日均超额 +0.1495% → −0.0293%（−17.9bp/日）、MDD −16.3% → −30.8%，6 个阈值档全负。二选一拍板 → 诚哥选**方案1（改部署端，约 3 行）**。

**改动**：`deploy_predict_g2.py` L277-290——红线过滤前插 `pre = pre.nlargest(10, "prob").copy()`，注释写明引擎引用（`scan_rotate_cost_real.py` L221/L223 + simulate L247）与 XVAL5 C 包反事实数字；AB/ENS 分支不动。py_compile 通过。

**验证（op 本地，516 交易日 × `research/xval5_c_top100_0915.parquet`）**：新部署序==引擎序 **516/516**；相对旧序 515/516 日选票变化；**空仓日 1→66（12.8%）**。

**空仓日行为（新口径下的常态，勿当故障）**：Top10 池无过线者 → deploy 早退不写 `<date>_g2_top10.csv` → `rebalance_g2` 回退校验宁缺毋滥（候选数据日须==增量库最新日，否则池空）→ **不买入、到期卖出照常**（`build_plan` 无空池早退，卖出先于买入），与引擎「无候选不买入」一致。副作用：`detect_data_stale` 当日候选缺失告警在这些日子出现属预期（有回退文件即正常）。

**剩余漂移（维持已拍板现状，未随本次对齐）**：TOP_N=3 vs 引擎 2、HOLD_DAYS=15+live_trail vs 引擎 N=10 fixed、大盘 T 档门控 vs 引擎无门控；G2 臂回测结论仍按 XVAL5 报告 §5.4 标注口径差异（红线作用池项已解除）。**首个生效点**：次日 09:25 候选生成。

## 2026-09-30 · V1.3 口径对齐方案B Phase 1 纸面并行落地（T-20260929-006，诚哥拍板「可以，执行吧」）

**起因**：调研定论=V1.3 实盘买的是回测从未验证的变体（池子已验证，但最终买谁由回测中不存在的 F3 新闻分 20% 权重 + T 日盘中 F5 决定，每日置换名单 43.3%，top5 重合仅 2.83/5）。报告 `specs/V13盘中重排裁决与执行架构研究_20260929.md`。诚哥拍板方案 B：删盘中重排、T-1 晚定名单、次日 09:31-09:35 纯脚本执行；先纸面并行 2-4 周，门禁达标才 live-switch。

**Phase 1 交付（当日落地，生产链零改动）**：
- SPEC：`specs/V13口径对齐方案B落地_SPEC_20260930.md`（口径锚点=prob Top10 池→scorecard_real→红线 58→total Top5；诚实偏差 4 条；Phase 2 门禁：≥15 执行交易日、new−old 日均配对差 ≥ −3bp/日、胜率差 ≤10pp）。
- `build_v13_final.py`（20:20 任务 `Quant_P16_V13_FinalList_2020`）：F2 新浪主源（与回测 fund_flow_real 同源家族）→ Tushare moneyflow 兜底（万元×1e4）→ 全缺失 fail-loud；F5 = incr 真实 pct_chg（close/preClose−1）× stock_basic.industry 等权（同 build_panel_v3_sc）；F6 = daily_basic（20:05 刷）→ est=None 回退面板；volume_ratio = incr volume / 主库前 5 日 vol 均值（回测用预计算列，现算为诚实偏差）。产物 `<T>_final_list.csv` + `.meta.json`（模型/快照 sha256 + 三源 max_date + f2_source_counts）。
- `v13_paper_parallel.py`（20:30 任务 `Quant_P16_V13_PaperParallel_2030`）：old 臂=`<T>_selection_full.csv` total≥58 top5；new 臂=D=max(final_list 日<T) 的 redline top5（跨假期自动配对，09-30 → 10-09）；两臂 T 开盘入场/第 5 交易日开盘退出/记 ret_1d；**均不加 PF（信号对比纯度，PF 是执行层正交件）**；停牌/无量 unfillable 剔除；幂等 log（按 exec_date+arm+ts_code 去重）；报告自动展示门禁指标；数据停更 exit 2。

**踩坑与修复（写脚本当天踩掉）**：①est 对齐 bug——先挂列再 sort，否则 est.values 排序后错位；②`pool["score_total"]` 幽灵列——代理总分须另算 `proxy_score_total`；③moneyflow.parquet trade_date 是索引列，`read_parquet(columns=[...])` 取不到 → reset_index 后再取；④**PS 5.1 `*>>` 重定向写文件是 UTF-16LE**（与 Add-Content UTF-8 混成一个文件全乱码），且 python 管道输出编码随宿主上下文漂移——修复=包装 ps1 里 `$env:PYTHONIOENCODING="utf-8"` + `cmd.exe /c "... 1>>log 2>&1"` 原始字节追加（零转码，任何上下文可读）；⑤ps1 含中文路径必带 UTF-8 BOM，编辑后必须复验（edit 工具会保留，但每次都要查）。

**首跑验证（2026-09-30）**：final_list 过线 1/10（002111.SZ total 59.0），F2 新浪 10/10，F6=daily_basic 真实口径（20:05 刷新后生效，名次内分数如实变化），代理分交叉自检 F1/F3/F4 vs model_top10 PASS（重叠 3 只）；纸面 log 首日 old 臂 5 只（entry 已填）+ new 臂 NOFILE 哨兵（首日设计内）；幂等重跑 log 不变。compare_g2_daily（G2=0/ENS=0）与 compare_v13_daily（PASS）当晚双双通过。

**下一步（自动，无需人工）**：每晚 20:20/20:30 累积；10-09 首笔跨国庆配对；≥15 执行交易日（约 10 月下旬）报告出门禁判据 → Phase 2 live-switch 另启拍板（届时 rebalance 加 --final-list 候选源 + 09:32 新任务 + TRAE 09:45 停用，回滚=去掉 --final-list）。方案 C（尾盘 proxy 重排）已挂路线图 P3，仅门禁失败才启动。

## 2026-09-30 · 603237.SH 账实不符对账事件 + TRAE 09:45 对账协议固化（T-20260930-001，诚哥四项拍板）

**事件（09:45 会话，新 step 4 prompt 首跑日）**：live 换仓后只读核查发现 `guard_note` 把 603237.SH 卖单记 `CANCELED_TIMEOUT`，实际全部成交 2800 股。**根因（未修 bug）**：`order_guard.py:251` 重试耗尽返回 `ok=False`（ok 语义=「重试走完且确认全成」≠「有成交」）+ `rebalance_daily.py:533` 记账门槛 `ok and traded_vol>0` → 真实成交漏记 → 持仓虚留 2800 股。现场处置闭环：账本补记（第一笔缺账号戳+日期残缺 → 10:30:10 触发账号戳 fail-safe 备份 `.bak_acct_nostamp_20260930_103010` → 修正后 128 行全带 67014907 戳 verify PASS）+ plan json 顶层追加 `manual_reconciliation` 区块（`.scriptorig` 备份、原字段未删改）+ 40 项 crosscheck 全 PASS + 报告 `data/rebalance_20260930.md`；全程未重下。**待修→已修（当日）**：order_guard/rebalance_daily 记账逻辑已修复（见下节），看板 T-20260930-001 全闭环。

**重要状态订正（两度反转，以 TRAE 回执为准）**：09-29 的 step 4 修改**第一轮从未真正写入任务**——09-30 00:21 落盘的 trae_note_20260929 虽声称「已改为」，但 TRAE 09-30 晚执行回执（§8.1）回读任务 15868a74 发现 message 仍是 09-03 旧版 step 4；09-30 晨 plan json 干净（22 字段全、`candidate_sha256` 非 null、未覆写）属**会话侧自觉遵守**，非 prompt 约束生效。**两轮改动已于 09-30 晚由 TRAE 按「累积」口径一次性写入并回读校验全过**（step 4 只读版 + 4b + 4c 对账协议 + trae_note 异常日必写 + 方案B 产物隔离；cron 未动、Active、执行次数 22 未变、其他任务未碰），回执落 `specs/TRAE任务修改说明_0945开盘实时复核_20260930.md` §八。**教训**：「文档层声称已改」≠「任务侧实际已改」——TRAE 任务类改动必须以**改后回读回执**为完成判据（本次 TRAE 回执做法正确，固化为惯例）；行为级验收待下一交易日按说明 §六清单核对，看板 T-20260928-002 残留⑥已按此订正回写。

**TRAE 协议固化（诚哥四项拍板 2026-09-30）**：新说明 `specs/TRAE任务修改说明_0945开盘实时复核_20260930.md`，在已执行的第一轮基础上增补 3 处——①新增 step 4c「账实不符人工对账协议」= 固化今晨实践（只读核查先行→trae_note 落证据→`.scriptorig` 备份→仅顶层追加 manual_reconciliation 区块→账本补记必须 8 列齐全带戳带完整时间戳→严禁重下→推告警）；②step 4 的 trae_note 从可选改「异常日必写」（账实不符/降级/清单缺失/红线零单/重试超限必写，正常日可不写）；③message 末尾加「方案B 纸面并行产物隔离」条款（`<T>_final_list.*` / `v13_paper_parallel_*` 禁写禁改禁移动，本任务照常只消费 `<T>_selection_full.csv` 系列）。**坑位记录**：账本补记缺账号戳/日期残缺会触发 fail-safe 备份（本次实际发生），4c 第 4 条「8 列齐全 + 完整时间戳」就是防它，不可省略。

## 2026-09-30 · order_guard 撤单窗口成交补记 + 双重委托防护修复（T-20260930-001 待办①，诚哥授权「把改修的都修了」）

**三 bug 同源定性**（603237.SH 事故形态的完整解构）：①`order_guard.py` 死透确认循环内 `pass` 漏记撤单竞争窗口成交；②cancel==0 撤单受理后无回查，「受理→重挂」间隙成交漏记；③活跃态(50/51/52)超时落入底部 continue **盲重挂**（原单仍活=双重委托；且该路径 remaining 未扣减会超量重挂）。三者共同产出「真实成交但 traded_vol=0/ok=False」或「双倍委托」，再叠加消费方 `ok and traded_vol>0` 双重门槛 → 漏记账本。

**修复（最小改动，未动下单/重试核心逻辑与 action 字符串语义）**：`order_guard.py` 6 处——活跃态 50/51/52 加入 L203 撤单分支集合（+对应 print）；死透确认窗口补记 `traded_vol` 累加/remaining 扣减 + remaining≤0 返 FILLED；cancel==0 新增回查补记块；`ok: traded>0` 误用改 `total_traded>0`（原漏前几轮累计）；未知状态兜底从盲 continue 改「按实际成交量放弃」（绝不双重委托）；docstring 厘清 ok 语义=「有已确认成交」。消费方门槛去 `ok` 化：`rebalance_daily.py` 卖/减/买 3 处 + `qmt_clear.py` 1 处改 `traded_vol > 0` 单条件（真实确认成交必入账本）；`qmt_monitor.py` 运行时本已正确（traded_vol>0 记账、ok 只管 sold.add），仅修 docstring。

**验证**：新建回归测试 `research/tests/test_order_guard_backfill.py`（FakeTrader mock 带 cancel_script/query_transitions/auto_fill_from/reject_from 钩子；stub xtquant 防连真客户端；提速 patch `wait_order_fill.__defaults__=(1,)`+POLL_INTERVAL=0.05+sleep 压缩）——S1 事故复现（撤单失败→死透确认窗口全成 2800 必返 FILLED/traded_vol=2800，修复前 ok=False/traded_vol=0）、S2 撤单成功窗口补记+重挂只挂净剩余 1000（防双倍）、S3 耗尽部分成交 traded_vol=1000 不丢、S4 撤单失败原单活跃绝不重挂（placements==1）、S5 记账门槛源码断言（4 文件无 `ok and traded_vol` 残留）——**19/19 PASS**；复跑 `test_monitor_sell.py` 19/19；py_compile 5 文件过。**保留**：4c 对账协议仍为兜底；`versions/V1.4_20260908` 冻结归档不改。**行为级验收**：下个交易日（预计 10-09）按说明 §六核对修复后记账正确性。

## 2026-10-01 · ENS 执行时点实验立项落地（T-20261001-002，诚哥拍板「动 ENS 留 G2」）

**实验**：ENS 桥触发 09:52→09:31（目标成交 ~09:35，避开 09:30-09:32 最宽价差），G2 09:50 锁死（前向验证期不动变量），15 个执行交易日 fill-vs-open 对照。基线（`results/fill_vs_open_baseline_20261001.md`）：ENS n=14 mean|dev|=**154.9bps**、G2 n=17 **167.6bps**（控制臂）；判定口径预注册锁定（降幅≥25% 且单侧 p<0.1，`specs/ENS执行时点实验预注册_20261001.md`）。

**本轮核实的代码事实（后续接手必读）**：
1. **`rebalance_ens.py` 本体无幂等**——`write_orders` 是 `next_seq` + 原子**覆写**同一路径 `cmd/orders_<date>.json`；二次 `--live` = 新 seq 覆写 = 内置桥按新订单重复执行（同账户双重下单）。幂等只存在于 guard 脚本层（orders json / plan written=true / lock 三标记）。**闭合手段 = TRAE 任务 message 加 step 0 前置检查**（`specs/TRAE任务修改说明_ENS执行时点前移_20261001.md` §三），脚本层内建幂等另立项。
2. **硬时序规则**：guard 触发时点必须晚于 TRAE 完成时点（现状 09:52→~10:02 完成→10:08 guard，6 分钟余量）。**反向操作（先提前 guard）会让 guard 抢跑变主执行 + TRAE 后写 = 双重下单**。故 TRAE 回执核验前 op 不动任何 Windows 时点；中间态（TRAE 09:31 + guard 10:08）安全。回执后联动：guard 10:08→09:48、DE 核验 10:12→09:52。
3. **两桥互斥是文件级双向的**：ENS 跳 G2 账本（`rebalance_ens.py` L444-455）+ G2 反向跳 ENS 账本（`rebalance_g2.py` L504-519，T-20260913-001 C2）。ENS 提前后互斥仍闭合且**消除现状竞态**（G2 09:53 写 hold_dates vs ENS 09:52-09:54 读）；代价 = 撞票优先权翻转（ENS 先得、G2 让位）。3 周实盘零撞票实证，影响面小。
4. **ENS 无大盘门控**（`rebalance_ens.py` L462 注释明示），此前会话记录的「T 档门控读取前移 17 分钟」副作用**不成立**——以代码为准。
5. fills 格式：`<bridge>/state/fills_<date>.json`（code/action/vol/price/ts/status），open 对照用主库+增量面板（未复权，与 QMT 成交价同口径）。

**观测链（已落地）**：`research/fill_vs_open_track.py` 全量重算（幂等无追加漂移，首跑数字与基线审计一致交叉验证过）+ schtasks `Quant_P16_FillVsOpen_Track`（每日 20:45，S4U，回读核验过）；guard 兜底日（lock 存在）自动标记并从实验臂主样本排除。**坑**：lookup 键口径必须统一去连字符（`20260929` vs `2026-09-29` 混用会全量 open 缺失，首跑已踩已修）。

**生效与回退**：2026-10-09 起算；回退 = TRAE cron 改回 `52 9` + schtasks 改回 10:08/10:12，单日可完成无代码回滚。**遗留**：TRAE 侧改动待诚哥转交 + 回执（§六清单）；回执后 op 执行 Windows 侧联动（修改说明 §七）。

## 2026-10-02 · 非交易日暂停优化（诚哥「非交易日，相关推送和任务就可以暂停」，T-20261002-003 后续）

**需求定性**：不是「永久 Paused」，而是**运行时 fail-closed 门禁**。TRAE cron 只能表达 `MON-FRI`，无法表达法定节假日——10-01~10-07 国庆休市期间调度器仍会唤醒任务（实测 10-02 换仓任务确实被唤醒）。若改 `Paused` 则交易日也不跑，等于全停。故方案 = 调度器照常唤醒 + **prompt 第一条**跑交易日检查，非交易日立即结束。

**统一事实源**：`is_trade_day.py`（项目根，131 行）。`python is_trade_day.py --json`，退出码 0=交易日 / 1=非交易日。判定顺序 = 周末 → 主库交易日历 → `HOLIDAYS` 硬编码表 → 兜底。实测 10-01/10-02/10-05 exit=1「法定节假日休市」，10-08 exit=0。

**门禁六条铁律（后续所有交易类任务 prompt 统一遵守）**：①必须是第一条，不得跳过、不得提前做任何动作；②非交易日只输出 `[SKIP-NON-TRADING-DAY] <日期> <reason>` 一行后结束；③禁止事项 = 不跑业务脚本 / 不做桥检查 / 不生成或修改任何产物 / 不访问行情 / 不推飞书（含告警、成交卡、模型降级卡）/ 不触发 QMT 或文件桥；④**非交易日不交易是设计内行为，不是异常**，不告警不人工介入，不得以「昨天有到期持仓该卖」绕过；⑤交易日通过门禁后**卖出优先原则照旧**，候选异常只拦买入；⑥prompt 内统一写 `python`（TRAE 定时任务环境可调用，历史 20+ 次执行为证；仅 Agent 交互终端需绝对路径）。

**已生效 8/8（Agent 改完 + 逐个独立 `Schedule get` 回读验收通过；Status/cron/executions/next run 全部未变，仅 prompt 正文更新）**：

| ID | 名称 | cron | executions | 批次 |
|---|---|---|---:|---|
| `30344e79` | G2换仓 | `50 9 * * MON-FRI` | 23 | 第一批 |
| `4cf3db92` | G2日终对账 | `45 15 * * MON-FRI` | 23 | 第一批 |
| `18c0c8d2` | 融合日终对账 | `50 15 * * MON-FRI` | 16 | 第一批 |
| `051bc2d9` | 融合换仓 | `52 9 * * MON-FRI` | 16 | 第二批 |
| `e05e526a` | 夜间检修 | `0 1 * * MON-FRI` | 21 | 第二批（守卫判**次日**） |
| `b0254f11` | 集合竞价 | `25 9 * * MON-FRI` | 25 | 第二批（`--top 10`） |
| `9a41d7f7` | 盘后复盘 | `40 15 * * MON-FRI` | 25 | 第二批（绝对路径门禁） |
| `095bbe1b` | 午休报告 | `35 11 * * MON-FRI` | 25 | 第二批（绝对路径门禁） |

**第二批 5 个的落地经过（诚哥「那 5 个我没确认的重来，我盯着」）**：首轮 `Schedule update` 卡在确认弹窗满 10 分钟被整体跳过（`051bc2d9`、`9a41d7f7` 各一次超时），每次事后独立 `Schedule get` 回读确认 prompt 逐字未变、Status/cron/executions/next run 均未动 → 判定未写入、无误更新、无重复触发；**用户在场守弹窗后重发即当场成功**，5/5 全部通过独立回读验收。**教训订正（取代「超时即改走人工粘贴」的旧退路）**：超时只说明「需要用户点确认」，不代表写入失败——**先回读判定真未写入，若用户在场则直接重发**，比落可粘贴全文改人工处理更快更可靠；无人值守连发重试才是真浪费（T-20261002-002 已 8 次验证：前 7 次无人值守全败、第 8 次守弹窗当场成功）。**口径区分（勿误读）**：`051bc2d9` / `e05e526a` 首轮「未生效」的**仅是本轮非交易日门禁**——T-20261002-003 的核心修订（`_g2_top10.csv` 口径 + 候选异常只禁买 + SELL 独立生成 + 有 SELL 必须 `--live`）当时已生效并验收，两件事状态独立、切勿混为一谈。

**全量 A 股 Active 任务门禁覆盖（`Schedule list` 实测 13 个任务）**：**10 个 A 股 Active** = 上表 8 个强门禁 + `3c7fdd1e` 候选预生成（`15 9`，24，已有基础守卫）+ `15868a74` 开盘实时复核（`45 9`，24，已有基础守卫）；另有 1 个非 A 股 Active `5f2bd77c` 夸克网盘（**不需要**交易日守卫）、2 个 Paused（`eda9b0c3` 成本锚、`c63cff58` Tushare 刷新，当前不运行、不纳入验收）。**口径订正**：此前口头表述的「9 个 A 股 Active」是**计数错误，实为 10 个**。

**同批抓出的两个真缺陷（已在第二批 prompt 中一并修掉并回读确认生效）**：
1. **`e05e526a` 夜间检修守卫方向错**：01:00 的语义是「为**次日**开盘做准备」，旧 prompt 却判断「今天」。周五 01:00 旧逻辑判断「今天=周五=交易日」→ 不跳过 → **为周六做检修（周六不开市，白跑）**；节前最后一个交易日的 01:00 同理为休市日做检修。改为 `is_trade_day.py <次日YYYYMMDD>`，跨月跨年按真实日历推算，禁止字符串拼接，跳过标签改 `[SKIP-NEXT-DAY-NON-TRADING]`。同时修 lark 1.0.4 腐化路径（改走 `python lark_push.py` / 稳定出口 `C:\Users\Administrator\bin\lark-cli.exe`；插件版本目录仅作为「禁止硬编码」的反例出现，**终检不能只做字符串命中、须判语义位置**）。
2. **`b0254f11` 集合竞价两处 `--top 2` 断链**：旧 prompt 走 `--threshold 60 --top 2 --pool 100` 生成 `_g2_top2.csv`，而消费端 `rebalance_g2.py` 用 `pattern="_g2_top%d.csv" % top_n` 且传 `SELECT_TOP=10` → **换仓读不到，且目录内 25 个历史 top2 文件会成陈旧候选误用源**。已改为 `--top 10`，两处执行命令均已回读确认；修订稿中保留的 `--top 2` 字样是「严禁使用」的反例。**残留清理项（未做，不影响运行）**：`deploy_predict_g2.py` 顶部帮助文本仍有 `[--top 2]` 旧示例，属文档/帮助文本层面，下次触碰该文件时可顺手订正。

**脚本层守卫 ≠ prompt 层守卫（易误解点）**：`de_report_daily.ps1` **已有**交易日守卫（非交易日记日志后 `exit 0`），但脚本返回 0 + 报告文件不存在时，**模型会把「预期跳过」误判为 P0 异常**并补发告警/模型降级卡。故 `9a41d7f7` / `095bbe1b` 仍须在 prompt 层提前退出；这两个任务工作目录是 `D:\QuantLab` 而脚本在项目子目录，门禁必须用**绝对路径** `python "D:\QuantLab\projects\Project_16_LightGBM股票大师\is_trade_day.py" --json`。

**已知限制（需每年维护）**：`is_trade_day.py` 的 `HOLIDAYS` **仅硬编码 2026 年**（含 10-01~10-07）。兜底方向是「工作日且非节假日→交易日」，故次年 1 月法定节假日会**误跑**（不是误停），不涉资金安全但会产生无谓推送。**每年 12 月前必须补下一年度交易所休市安排**。

**教训（调度层通用）**：①`Schedule update` 的成功回执不可信，**必须独立 `Schedule get` 回读**核对 status/cron/executions/prompt 全文；②**发起需确认弹窗的动作前先问用户是否在场守弹窗**（本项目已两次实证：T-20261002-002 守弹窗当场成功 / 本轮 5 个任务守弹窗 5/5 成功），无人值守连发重试纯属浪费；③**超时 ≠ 写入失败**——超时只说明没人点确认，先回读判定真未写入，若用户在场直接重发即可，**不必改走「人工粘贴」退路**（该退路是用户确实无法守弹窗时的备案）；④`python` 是否在 PATH **取决于运行环境**——Agent 交互终端无 `python`（exit 9009）但 TRAE 定时任务有，不要把终端结论写进 prompt。

## 2026-10-02 · 成交通道全链路审计（16 文件）+ premarket BUILD_TAG 漂移修复（T-20261002-004）

**背景**：T-20260930-001 修复落地后工作区积累成交通道文件改动，诚哥要求「3 条都执行」= 认可范围判定 + 补完剩余文件/Py3.6 兼容性/测试有效性审计 + 清理 DE 卡死证据。DE 审计委派**卡死失败**（见下「DE 委派事故」），审计由 op 独立完成。

> **文件数与 diff 统计口径订正（2026-10-03 复核）**：本章节早期版本写「10 文件 / 13 文件 / `+1929/-413`」，属**统计口径漂移**——旧口径既未覆盖全部六个桥文件，也未含测试与邻接改动。**权威数字以 Git 实测为准：16 个文件，`+3033/-793`**（前 15 文件 `+2720/-788`，`test_order_guard_backfill.py` `+313/-5`）。下表已按 16 文件重列，**引用本项目历史结论时请用新口径**。

### 一、逐文件裁决（全部保留，无一建议回滚）

| 文件 | 改动 | 裁决 | 依据 |
|---|---|---|---|
| `order_guard.py` | +258 | 保留 | 成交事实优先；均价不完整返 `None` 而非部分均价 |
| `rebalance_daily.py` | +63 | 保留 | 3 通道门槛统一；SELL 不被候选阻断 |
| `reconcile_trades.py` | +91 | 保留 | 零价 SELL 扣 FIFO 成本不计盈亏，账务自洽 |
| `qmt_monitor.py` | +35 | 保留 | 告警/卖出职责完整，未被候选异常压制 |
| `qmt_clear.py` | +18 | 保留 | 撤单超时/重挂耗尽不丢已成交量 |
| `strategy_capital.py` | +17 | 保留 | FIFO 语义与 reconcile 一致 |
| 三桥 src + build（6 文件） | +1474 | 保留 | src↔build 归一化后仅首行编码头不同（`# coding=utf-8` → `# coding=gbk`，QMT 红线要求的转换），除该行外逻辑 IN_SYNC |
| `research/tests/test_order_guard_backfill.py` | +313 | 保留 | 68 项断言，源码级校验非空壳 |
| `premarket_g2_check.py` | +30 | 保留（本轮修复） | 见第二节 |
| `g2_correction_exec.py` / `.ps1` | +38 | 保留 | 无现行调用方，废弃守卫优于删除 |
| **合计 16 文件** | **+3033/-793** | **保留 16 / 回滚 0** | 权威数字以 Git 实测为准 |

### 二、本轮唯一代码修复：premarket_g2_check.py 的 BUILD_TAG 漂移

**缺陷**：`EXPECTED_BUILD_TAG` 硬编码 `"20260902-161814"`，而 `build_p16_g2.py` 每次重建都自动盖新时间戳 → 盘前核对**永久误报「未切换新版」**，把人工核对信号退化成噪声（与 lark-cli 1.0.4 硬编码路径腐化同类）。
**修复**：新增 `_read_local_build_tag()`，从 `build/strategy_p16_g2_bridge.py` **动态读取**（GBK 解码，因 QMT 红线产物非 UTF-8）；读不到（产物缺失/被删）时 `check_heartbeat()` 显式告警并返 False，**不静默通过**。原「半成品」状态（函数已插入但缺 `import re`、`check_heartbeat` 仍引用已删变量）本轮补齐。
**验证**：`py_compile` OK；`_read_local_build_tag()` 实测返 `20261002-193332`。

### 三、op 自身两处审计错误（方法论教训，比结论更重要）

1. **f-string 正则判定法有缺陷**：首轮用正则 `f["']` 判定，复核时在 6 个桥文件上**误报 `fstr=6`**。改用 `ast.JoinedStr` 节点判定（唯一可靠方式）后**全部为 0**——**结论没变，方法错了**。正则会把 `for attr in (...)` 的 `f"`、`"SELL_STOP", "` 都算进去。**教训：语法级判定必须走 AST，正则只做粗筛**；`build_p16_g2.py` 的禁用语法扫描同理，其正则存在同类假阳/假阴风险。
2. **函数归属判错**：曾称 `_resolve_fill_avg` / `_adverse_slippage`「不存在」。实际存在于**三个 QMT 桥内部**（自包含实现，因 QMT 单文件不能 import 外部模块），不在 `order_guard.py`。**结论对、理由错**。

**由此查出一处设计口径差异（非缺陷）**：
```text
桥内：fill_avg → order_obj → ref_price → unresolved   （四级）
外部：fill_avg → ref_price → unresolved               （三级）
```
桥内多一级 `order_obj` 兜底（桥拿不到 `traded_price`），且 `strategy_p16_g2_bridge_src.py:508-519` 明确要求 `price 恒 > 0` 才可记账，**比外部侧更严**。合理，勿误判为不一致。

### 四、验证证据

```text
测试：10 文件 TOTAL PASS=618  FAIL=0  全部 exit 0
  test_order_guard_backfill 68 / test_zero_price_sell_fifo 31 / test_rebalance_sell_priority 18
  test_monitor_sell 19 / test_g2_deal_fill_slippage 62 / test_ens_deal_fill_slippage 62
  test_v13_deal_fill_slippage 87 / test_g2_state_machine 108 / test_ens_state_machine 108
  test_v13_state_machine 55
Py3.6 红线（ast 判定）：6 桥文件 fstring/walrus/pep604/genann/match 全 0
构建一致性（2026-10-03 全量重算）：g2 src=1773/build=1773、v13 1365/1365、ens 1763/1763，
  行数完全相等；完整解码逐行比较每对**恰 2 处**差异 = L1 编码头（`# coding=utf-8`→`# coding=gbk`，
  QMT 红线强制）+ BUILD_TAG 行（src 占位时间戳 → 本地构建时间戳，构建器自动注入）。
  归一化这两处后 normalized_diff_lines=0、difflib ratio=1.000000 → **逻辑完全同步，字节不一致
  属设计转换，非缺陷**。**三层判据：①原始字节不一致 ②完整解码每对 2 处 ③归一化后完全一致**；
  旧口径「1613/1253/1603、DIFF_LINES=1、唯一差异=编码头」三处均已作废（行数与差异数都错）
外部脚本 f-string 不构成红线：order_guard(38)/rebalance_daily(46)/reconcile_trades(19)
  /strategy_capital(4)/qmt_clear(10)/qmt_monitor(36) 均未被内联进 QMT 产物
```
测试用 `xtquant` stub，**不连真实 QMT、不产生真实委托**。

### 五、唯一真实阻塞项：QMT 部署滞后（非代码 bug）

```text
本地构建  build/strategy_p16_g2_bridge.py  BUILD_TAG = 20261002-193332
QMT 实际   heart_20261002.json  build_tag    = 20260928-113106   ← 落后 7 天
```
`premarket_g2_check.py` 现返回 `RET=False`，这是**真实部署滞后信号**。按 QMT 红线须人工粘贴部署，op 不能自动完成。**注意区分**：这是「改了没部署」，**不是**「改了没重建」——src/build 侧已 IN_SYNC。

### 六、DE 委派事故归档（承接 `_de_audit_brief_20261002.md`）

DE 运行约 25 分钟，**无 `SCOPE_ACK`、无报告正文、CPU 长时间几乎不增长**，已终止全部 `deveco` 进程、**未重派**，零信息量输出 `_de_audit_out_20261002.json` 已删除，任务书改为归档记录。
**原任务书 Q1 前提已被推翻**：曾把 `build/...py 的 BUILD_TAG`（本地构建版本）与 `heart_*.json 的 build_tag`（QMT 实际运行版本）混为一谈，据此断言「build 产物 tag 仍是 20260928-113106」——**该表述错误**，真实问题见第五节。
**委派纪律补记（补 AGENTS.md「两类委派事故」第三条）**：③**任务书里的事实前提必须由 op 用原始数据先验证**，不能把未核实的判断写进任务书让 DE 去「确认」——DE 会基于错误前提产出自洽但答非所问的报告，且污染后续所有引用。本项目已两次实证（本次 Q1 前提错误 / 上次 Project_21 自行重界定范围），**共同点是任务书本身有缺陷，而非 DE 能力问题**。

### 七、遗留

- **QMT 部署**：3 个桥产物待人工粘贴，逐一核对日志 `BUILD_TAG`（本节第五节）。**这是本轮唯一真实阻塞**。
- **Git**：未提交。工作区混有并行任务产物（astock 升级审计、ENS 换仓、v13 纸面并行），**建议分批提交**，只提本轮 16 文件。
- **repowiki 知识库**：P16 两张卡（架构设计 / 特殊配置与命令）+ 知识库导航已补齐（2026-10-03）。
- **残留下游**：`deploy_predict_g2.py` 顶部帮助文本仍有 `[--top 2]` 旧示例（文档层面，不影响运行），下次触碰该文件时顺手订正。

## 2026-10-03 · corr_diag 夜间批次独立审计（T-20261003-001，补录+关闭）

**补录背景**：10-02 23:10 ~ 10-03 02:34 夜间会话产出 corr_diag（同批次选股相关性诊断）22 文件（core/io/report/runner 四模块 + 三测试套件 + 三策略 20260930 批次报告 + 8 个只读探针），**当时未记看板/记忆（违反硬规则），本条即补录**。审计报告：`results/corr_diag夜间批次独立审计_20261003.md`。

**定性**：诊断工具而非策略优化，**不构成生产交易闸门**（全项目 grep 零生产引用；core/io 零写操作；探针输出走 TEMP）。用户所说「TW 三策略优化」的另一半——白天成交通道批次——已由 T-20261002-004 关闭。

**审计结论 PASS**，关键验证（独立复算脚本 `research/audit_corr_diag_20261003.py`，不 import corr_diag）：
- 19/19 复算全吻合：三策略候选清单（v13 10/g2 9/ens 7）逐一匹配；主库末日 20260911；ens 21 对 60 日相关全表 ±5e-4（max 002181.SZ/920237.BJ @0.4754）；申万截面 2026-08-14 独立重建行业映射 7/7（含北交所 920237 正确归类）、HHI=0.1837。
- 测试亲跑：core 183 / io 153 / report 100 全 PASS（io 夜间回执 140 是旧版本快照，以亲跑为准）。

**唯一 P1（已当场修复）——报告产物滞后于定稿代码**：报告 00:57 生成后 core/io 02:31 又改（新增风格快照滞后 ≤45 天、风格覆盖率 ≥50% 两检查），未重新生成 → 盘上 v13 风格维度误显示 OK。已用定稿代码原地重生成三份报告（v13 风格修正为 WARN），重生成后 19/19 PASS + 再生成幂等（六文件逐字节一致）。

**P2 记录**：①corr_diag 只读主库未合并 `data_live/incremental_daily`（口径弱于 fill_vs_open 的合并口径，报告已 WARN 披露）；②风格成分快照停在 20260731（滞后批次 61 天，数据源层面待离线包升级）。**P3**：`_rep_io.txt` 回执未刷新；探针脚本留盘待清理。

**教训（产物-代码同步）**：**改了生成器必须立刻重跑产物**——报告类产物与代码不同步时，「盘上结论」会替错误代码背书（本次 v13 风格 OK 实为旧代码结论）；审计「报告 vs 代码版本」先比对时间戳再信内容。
- **DE 复核任务书**：`research/_de_audit_brief_20261003.md`（写死 16 文件绝对路径 + `SCOPE_ACK` 要求），供外部 agent 独立复核本轮结论。

## 2026-10-03 · 成交通道 16 文件 DE 独立审计关闭 + P1/P2 修复（T-20261003-002）

**DE 独立审计**（报告 `results/DE审计_成交通道16文件_20261003.md`，复算脚本 `research/_de_audit_compare_20261003.py`，全程只读、不采信主 agent 结论）：Q1 三桥 src→build 归一化 **diff=0**（仅编码头+BUILD_TAG 两行，g2/v13/ens 差异行 [1,25]/[1,22]/[1,24]）；Q4 成交价链路全核验（**桥内 deal 查询函数实名 `_query_deals`**，任务书误写 `query_stock_trade_stats`——查桥源码时按实名找）；Q5 六桥 AST 全零+GBK/CSV/passorder 红线全过（**静态层面，非 3.6.8 运行证明**）；Q6 亲跑 15 测试全绿（PASS=618 与主 agent 逐文件一致）；Q7 修正脚本守卫在 main 首语句无绕过；Q8 账号戳 fail-safe 齐全。与主 agent 分歧：order_guard numstat 实测 **+255/-3**（主报告误写 +258）。

**审计新发现并已修复两项（代码注释沿用任务书编号 T-20261003-001）**：
1. **P1 `rebalance_daily.py` 清单早退残留**：「清单缺失/读取失败」两个裸 `return` 仍压在持仓→成熟度→sell_plan 之上（zero-target 修复未覆盖）→ 已改为 `_sel_text=None → target=[]` 走既有 `if not target:` 分支（T≥1 只禁买 / T=0 清仓照常）。**踩坑：`_candidate_meta` 不预填 `candidate_rows_filtered`**（仅 `load_selection` 回填），清单缺失路径到 plan 落盘会 KeyError——修复显式补 `candidate_rows_filtered=0`。
2. **P2 `premarket_g2_check.py` 退出码**：main() 恒 `return 0` → 改 `0 if all_ok else 1`；全库 grep 无自动调用方依赖退出码（仅 RUNBOOK 人工），安全。

**回归测试增量**：`test_rebalance_sell_priority.py` 新增 S7/S8（清单缺失·读取失败→MATURE 卖出照常）/S9（清单缺失+T=0 全清）/S10（源码绊线：清单分支无裸 return），32/32 PASS；order_guard 68/68（S5/A8 源码断言未破坏）；全通道套件复跑全绿。

**硬约束（粘贴窗口）**：**P1/P2 修复只动外部脚本，不动桥 src → 三桥无需重建，粘贴 T-20261002-004-B 照常**，核对 tag g2=20261002-193332 / v13=20261002-193451 / ens=20261002-200314。P3 两项（桥内 1 处 `datetime.now()` 相对年龄测量、`build_p16_g2.py` BUILD_TAG count=1 弱校验）**观察不修**——后者刻意留到下轮构建脚本加固批次，避免粘贴窗口引入新构建变量。

**经验**：审计「卖出优先」类红线时，光看修复 diff 不够——**要把 main() 里全部 early return 逐个对照「持仓→成熟度→sell_plan」链路位置**（T-20261002-001 修了 zero-target 分支，同文件还有两条别的早退路径压在链路上）；DE 静态 AST 检查的边界要在报告里显式声明（≠3.6.8 运行证明）。

## 2026-10-03 · 三桥构建脚本 BUILD_TAG 校验加固（T-20261003-003）

**DE 审计 P3 第一项**，在三桥粘贴完成（-B）后即时落地——只动构建脚本、不碰桥 src、不跑真构建，不影响已部署三 tag。

**加固（三脚本统一）**：①BUILD_TAG 赋值**恰 1 处**硬断言（原 `count=1` 弱校验会静默漏替第二处赋值——审计 Q1「静默不一致」追问项；0 处/2 处/单引号形态均 `[BUILD_TAG-REJECT]` 拒绝且不写产物）；②GBK 写出后**回读复核**（首行 `# coding=gbk`+新 tag+行数守恒，防截断/未刷新）；③g2/ens 补齐 v13 已有的 MOCK/pytest/unittest/assert 残留扫描（红线前移到构建期；三 src 实测零命中，无行为变化）。

**验证**：`research/tests/test_build_p16_hardening.py` **40/40 PASS**。关键手法：importlib 加载构建脚本 + 把模块级 `OUT` 重定向到临时目录 → **全链路 build() 验证而不触发真重建**；末尾 sha256 断言生产产物逐字节不变（粘贴窗口红线）。拒绝路径断言「SystemExit 且产物未写出」。

**铁律**：三桥部署完成 ≠ 构建链可以随便动——**改构建脚本可以随时做（不影响已部署 tag），跑真构建才会作废部署**（产物盖新时间戳 → 与 QMT 心跳不一致 → 须再粘贴）。桥内 `datetime.now`（P3 第二项）因此维持观察：改它必须重建+再粘贴，零功能收益，攒到下次桥 src 实质改动一并带上。

## 2026-10-03 · 部署比例 95%→98%（T-20261003-005 研究 + T-20261003-006 执行）

**口径澄清（易错）**：回测从来不是满仓——官方引擎 `scan_rotate_cost_real.py` 部署硬编码 `budget = cash*0.95/n`（文档「满仓2只」只是 T=2 档位称呼），与实盘 `RESERVE_CASH_PCT=0.05` 刻意同口径。**回答年化口径时区分三层**：实盘资金池 = 初始 10 万 + 已实现（FIFO）+ 浮盈（复利执行、亏损缩池不补钱）；回测年化 = 复利几何折算（(1+总收益)^(252/n)，已验算 ANNUAL_RESULT 25.42% 与 walkforward 44.0% 吻合）；季评超额 = 单笔 open→open 口径（非组合复利）。老脚本 `analyze_sell_timing.py` 用 /100000 固定本金是单利历史口径。

**三档对照（T-20261003-005，op 亲跑）**：引擎 L395 改 `BT_DEPLOY_PCT` env（默认 0.95 行为不变）+ `research/deploy_pct_compare.py`。结果（2024-07~2026-08，复利年化）：G2 110.1→113.6→116.7%（98 档 +3.5pp/100 档 +6.6pp）；融合 109.6→112.0→113.1%（+2.4/+3.5pp）；V1.4 82.6→83.8→84.4%（+1.2/+1.8pp）；回撤代价 +0.3~0.6pp、夏普不动、三档交易数/胜率一致。产物 `research/results/deploy_pct_compare_20261003.json` + `_report.md`。

**诚哥拍板（2026-10-03）**：收到 **98%**，不推 100%。**2% 的用途要能算出来**：P2-14 卖出费盲区（realized 永不扣卖出费，每轮 ~0.036% 池，一年累积 ~1.2-1.7%）+ 换仓日时序错配；不预留则名义满仓实际是真实权益的 101-102%，**悄悄突破 10 万 capital_base 额度且 `check_capital_allocation.py` 查不到**（只查声明值不查实际敞口）。**废单论在本双账号不成立**（两账户可用资金 ~1000 万，V1.3 日报实测 10,009,004 元）——2% 保的是额度纪律+净值口径真实，不是订单存活。

**执行（T-20261003-006）**：`g2_config.DEPLOY_PCT` 0.95→0.98（RESERVE_CASH_PCT 0.05→0.02）、`g2_ens_config.RESERVE_CASH_PCT` 0.05→0.02、`qmt_config.RESERVE_CASH_PCT` 0.05→0.02；T=1 半仓 0.50 不动；注释/文档/知识库四处同步；py_compile 7 文件过 + 配置回读 0.98×3 + `test_rebalance_sell_priority.py` 32/32 PASS。**桥产物不引用这些常量（全项目验证），无需重建粘贴，10-08 节后首个换仓日自动生效**。**回测官方基线保持 0.95 不动**（对照锚点，研究需对齐实盘时用 BT_DEPLOY_PCT=0.98）。

**遗留**：P2-14 根因修法（reconcile 写回 realized 扣卖出费，fee-aware）排进下一轮资金池维护——修完 98% 可评估收到 99%+；按 AGENTS 红线属资金池写回逻辑改动，需补测试、三边（V1.3/G2/ENS）同口径。

## 2026-10-04 · 近 5 年回测详测（T-20261004-005，三策略 2021-08~2026-08 双档 IS/VALID/OOS 分段）

- **背景**：诚哥要求跑三策略近 5 年回测、详细数据指标落盘。**runner** `research/backtest_5y_20261004.py`（复用 jq_metrics 官方三配置，一次 build 双档 simulate；窗口 2021-08-16~2026-08-14；argv 支持策略/档位过滤分次跑——V1.4 带 lite posfilter 很慢必须单独跑+3600s 超时）。报告 `results/近5年回测_三策略_20261004.md`；三份全指标 json `research/results/backtest_5y_20261004_{G2,融合,V1.4}.json`（逐日净值/回撤/月度/分段）。
- **训练切分约定（方法论底线，引用前必查）**：全项目统一 train 2020-01~2023-06 / valid 2023-07~2024-06 / test 2024-07+（`train_g2.py` L13、`train_optuna.py` L148-150）→ **5 年窗口必含 IS/VALID 段，全窗合计数字不可冒充样本外成绩**；官方 OOS 口径=2024-07 起。
- **核心数字（deploy=0.98 现行口径，全窗 1210 交易日）**：G2 总收益 +236.9%/年化 28.8%/夏普 0.90/回撤 -46.8%；融合 +455.7%/42.9%/1.19/-41.5%（全窗综合最优）；V1.4 +349.2%/36.7%/1.02/-49.7%；基准（全市场等权）年化 9.6%/回撤 -29.9%。分年：G2 2022 -21.9%、2026YTD(至8-14) +98.7%；融合 2025 +161.3%；V1.4 2024 +52.0%。
- **三条硬结论**：①**IS 段并未虚高**——G2 训练样本内 -7.7%（年化 -4.3%）、融合 +45.1%、V1.4 +18.7% → LightGBM 450 万行学不出记忆化，「in-sample 通胀」担忧实测不成立；真实画像=**模型只适应 2024-07 之后的 regime**（OOS 段年化 G2 100.1%/融合 107.9%/V1.4 90.4%）。②**压力段全军覆没**——VALID 段（2023H2~2024H1，含 2024-02 微盘流动性危机）G2 -11.9%/融合 -14.4%/V1.4 +1.3%，段内回撤 -35~-42%，**策略对小微盘流动性危机无防御力**；回测引擎无分层风控/无大盘门控=与实盘桥口径的最大缺口。③**融合是唯一 2022/2023 连续为正的策略**（+13.7%/+11.8%），双模型 rank 融合的稳健性在长窗口获实证，与 ENS 桥实金配置互为印证。
- **回撤警示（后续验收必修课）**：三策略全窗回撤 -41.5%~-49.7%，全部远超组合 15% 红线；若走正式验收，组合级回撤控制（大盘门控扩展至融合/V1.4 或组合回撤熔断）须另立项——不改现网。
- **口径披露**：open→open 可执行成本口径（跳空/一字剔除 34-86 笔）；**单源（仅 astock）未经 gpsj 备用源交叉验证**；北向因子 2025 起 99% NaN、rc_rating 全期 97%（LightGBM 原生处理，报告已披露）。0.95 vs 0.98 差异 +0.5~1pp 年化，与 T-20261003-005 三档对照方向一致。
- **正确性校验 + 方法论澄清（分段 ≠ 独立重跑）**：OOS 段年化（G2 0.95 = 97.1%）**低于**官方独立跑（110.1%）——已实证归因：用同一脚本代码路径按官方窗口 2024-07-01 **冷启动**重跑 G2 0.95，总收益 357.41%/年化 110.12%/夏普 2.278/回撤 -16.71%/交易 78/跳空 37，与 T-20261003-005 锚点**六项逐位一致**（`research/results/backtest_5y_check_oos_freshstart_20261004.json`）→ 脚本无 bug，差距纯属**连续跑路径依赖**（2024-07 边界携带 6 月建仓持仓、live_trail 锚与轮动相位不同，TOP2 集中策略对入场时点敏感，-13pp 年化）。**规则：引用 OOS 成绩以官方冷启动独立跑为准；连续长窗的分段数字只用于同一资金曲线内的时代对比，两套数字不可混引。**
- **踩坑**：①seg_metrics 里字符串日期与 `strat.index` 的 Timestamp 直接比较抛 TypeError（pandas 列比较会自动转型、标量比较不会）→ 必须 `pd.Timestamp(s)`；②PowerShell 内嵌 python -c 带中文/`%` 格式串会被解析器打碎 → 长提取逻辑一律写临时 .py 文件再跑；③**PowerShell 双引号 -c 字符串里 markdown 反引号是转义符（`` `r ``=CR/`` `e ``=ESC，T-20260922-001 教训④同源，本日两次命中）**——凡含反引号的文本经 `python -c "..."` 写入文件必被吃字符+断行（本次 repowiki 两处损坏，已修复）；**规则：写含反引号/中文的文件内容一律走 write 工具或临时 .py 文件，禁止 PowerShell -c 直传**。

## 2026-10-05 · 磁盘大扫除（释放 ~24GB，诚哥批准 A+B+C）

- **删除（data/ 根级）**：8/23-25 实验面板群 15 个 ≈12.3GB（v3.1/v3.1b/v3.2/v3.3/v3.4/v3.4_N3/N5/t5、enh2、enh2_n3、enh2_n3_bt_42/43、enh2_n3_proxy_bt、sc_real、f3）+ 复权迁移前旧备份 3 个 ≈2.0GB（v2_unadj_bak/v3_adj_bak_20260824/v3_unadj_bak）+ enh 轮转 bak 9 个 ≈6.5GB（保留最近 2 个：bak_20260929/0930）。
- **删除（versions/）**：panels_bak_20260828（1.4GB）、V1.0_20260821（685MB）、V2.0-candidate_20260824（743MB）整目录（含 11 个已入库小文件 selection/报告，git 历史可找回）；**models/ 2165MB 模型资产保留**。
- **删除（全局）**：browse/ 未跟踪原始转储 557 文件 ≈354MB（crawl/ + jsonl；**19 个已入库审计结论文件保留**）、根目录 `_*.txt` 55 个、scripts 未跟踪回执 `_*.txt`、research/tests `_rep_*` 回执、`__pycache__` 114 个目录。
- **保留的活跃/冻结面板（后续引用以此为准）**：`feature_panel_v2/v3/v3_enh.parquet`（9-30 每日刷新）、`feature_panel_v3.4_latest.parquet`（V2.0 面板资产，已回滚但留作大QMT 改造依据）、`feature_panel_v3_sc.parquet`（V1.4 回测冻结面板）、`feature_panel_v3_enh2_n3_bt.parquet`（G2/融合回测冻结面板）、`data_live/merged_daily_full`、versions/models。
- **注意**：v3.2 已删 → `build_panel_v3_sc.py` 无法原样重建 v3_sc（其依赖 v3.2 的 main_net_v32 列），但 v3_sc 已冻结在盘，回测研究不受影响；enh2_n3（非 bt）已删 → G2 训练面板不可复原，模型在 versions/models，重训练请走现行 build_features_v2 → enh 链。
- **教训（shell 转义）**：`python -c` 里 `line.replace('/','\\\\')` 在 PowerShell 双引号串里实得 `\\`（双反斜杠）→ 与 relpath 单反斜杠永不匹配 → `git ls-files` 跟踪集判定全失效，误删 19 个已入库 browse 结论文件 + `_run_capture.py`（均已 `git restore` 恢复）。**判定 git 跟踪集必须写临时 .py 文件跑，禁止 -c 内嵌转义**。

## 2026-10-09 · 止损方向审计实盘口径交叉验证（T-20261009-003，结论修订「分池存疑」）

- **一句话**：止损审计（55 笔 G2 rank≤3 纸面队列，T-20261009-002 复算一致）的「止损方向错了」结论，经实盘 fills 交叉验证后修订为「**分池存疑**」——G2 池纸面+实盘同向指止损伤害（001266：止损 −6.96%，T+5 反弹 +7.0% ≈成本，多亏 ≈6.6pp）；V1.x/ENS 池实盘 4/4 止损正确（603021 N10 同窗续跌 −16.6%）。**改止损参数前先跑调和检验**（8 笔实盘止损 × 本臂到期反事实，`讨论室/P16三策略止损方向审计_20261009.md` §七.5）。
- **结构性认知（以后引用止损审计必带三条）**：①审计队列与实盘止损 **trade 级零重叠**（V1.x 票不在 G2 模型列表；ENS 票在 top14 但 rank 5-8 样本外）——G2 池结论不得外推到 V1.x/ENS 池；②出场时钟分臂——**G2 N15（`g2_config.py` L35，09-10 拍板）/ ENS N10（`g2_ens_config.py` L37）/ V1.x N5（`rebalance_daily.py` L50）**，N10 纸面结论外推到 N5 臂会高估止损后反弹收益；③止损执行口径分臂——桥 tick 贴线（穿线 ≤0.45pp，审计 §四.2「实盘 whipsaw 更严重」预测在桥臂**不成立**），V1.x 穿线 4.5~8.4pp（300413 14:34 以低于线 9% 成交，机制待查：缺口/跌停/检查频率）。
- **工程缺口（A 层补齐项）**：V1.x `qmt_trade_log.csv` 与 `sold_forward_live.csv` 均无 reason 列 → 601999（+15.1% 一日止盈，6.90×1.15=7.94 精确命中 TP 线）曾被误归止损，只能按价格反推。**reason 字段 = 止损研究的先决基础设施**。
- **数据坑**：300413 的 10-02 +108% 行是假成交残留（T-20261008-012），前向复核必须剔除；300413 09-09 止损为 2,000 股整批（09-04 @21.99 成本），实现 −15.4%。
- **有效样本量的诚实计数**：ENS 三笔止损（603021/688450/688651）同为 09-22 批次买入 = 一次批次暴露，7 周 9 笔风险出场实际 ≈6 个独立事件。
- **样本后续看**：688450（10-08）/ 002510、688651（10-09）三笔 STOP 的 T+3 下周出——审计 §四④ regime 依赖（止损伤害集中在均值回归段）的续样本检验。

## 2026-10-09 · V1.x 选股断流修复（T-20261009-006：fail-closed 日期门禁 + 断流飞书告警）

- **一句话**：10-09 V1.x 零交易的根因 = 09:45 任务写/沿用旧 `data/tdx_review.json`（date 停在 20260930，假期后未更新）→ `review_full.py` 无条件采信该日期把清单写成错日文件名（20260930_selection_full.csv）→ `rebalance_daily.py --live` 找当日清单落空、candidate_rows=0 静默空仓（marker executed_live=true 表面成功）→ 10:05 guard 见标记已存在直接跳过（补跑条件=缺标记，非缺候选）。上游无恙：10-08 16:30 daily 链正常产出 20261008_model_top10.csv、面板 00:49 已刷新；10-08 候选 0 是独立的 F3 缺失过滤问题（优化方案 V-3 单独跟踪）。
- **修复（fail-closed + fail-loud 双层，代码+测试+事故复现三重验证全过）**：①`review_full.py` 加 `--date` 参数 + `_resolve_rdate()`——复核 JSON 日期非今日且未显式传 `--date` 即 **exit 2 拒写错日清单**（复盘/补跑显式传 `--date` 合法）；`guard_backfill_v13.py` 调 review_full 时补传 `--date`（保 `--allow-restamp` 复现场景）。②`rebalance_daily.py` 新增 `_alert_selection_problem()`——清单缺失/读失败即时飞书告警（lazy import qmt_bridge_client.notify_feishu，best-effort 不影响交易路径）+ plan json 落 `degraded` 字段（selection_missing / selection_read_error），T-20261003-001「卖出照常」语义不变；dryrun 与 live 两处 plan 白名单各补 degraded。③告警在 dry-run 与 live 都触发（一次事故可能两条消息，可接受）。
- **测试基线**：`test_rebalance_sell_priority.py` 32→**42 PASS**（S11 缺失→告警+degraded / S12 读失败→告警 / S13 正常清单防误报）；新建 `test_review_rdate_guard.py` **5/5**（UTF-8 与 GBK 双编码各跑一次）。事故复现：真实旧 JSON → review_full exit 2 拒写、旧档未触碰；rebalance dry-run → 飞书真阳性 + degraded=selection_missing 落档。
- **编码坑（桥外脚本同样适用）**：⚠ 等 Unicode 符号在 GBK 控制台 `UnicodeEncodeError` 直接 crash，已改 `[WARN]`——**控制台输出一律 ASCII 安全字符**。环境再确认：PATH 上 python 是 WindowsApps stub（静默无输出），必须用 miniqmt venv python；控制台先 `$env:PYTHONIOENCODING="utf-8"`。
- **运维红线（新增）**：guard 补跑条件是「缺标记」救不了「标记在但 0 候选」——修复后 09:45 任务再写错日期，review_full 会当场 exit 2 报错给 agent + rebalance 飞书告警暴露断流，双保险。TRAE 09:45 任务 prompt 应增补「tdx_review.json 的 date 必须为当日」条款（Schedule update 需诚哥守弹窗，发起前先问是否在场，L273 第 8 次成功经验）。
- **周一 10-12 开盘前核对清单**：①今晚 16:30 daily 应产 `20261009_model_top10.csv`；②09:45 产物文件名必须是 `20261012_selection_full.csv`（不是 20261009_*）；③guard 首跑看 `rebalance_daily_guard_20261012_*.log` 含新 in-flight 扫描；④G2 空仓是否门控零买、ENS 688117 T+1 解冻后表现。

## 2026-10-10 · reason 字段工程落地（T-20261009-007，方案一：不动账本结构）

- **一句话**：止损审计 §六.1 说「账本无 reason 致 601999 止盈出场被误判为止损」——实测修正：qmt_trade_log 的 **score 列对 SELL 行已事实承载归因**（09-02 起全部有：SELL_STOP×13/MATURE×9/PK_OUT×11/601999=SELL_TAKE_PROFIT 等；63 行无归因全是 2026-08 旧约定前历史行），误判真正根因是 **sold_forward_live.csv 无 reason 列**。诚哥拍板**方案一**：不动账本 8 列结构（TRAE 4c-4 人工对账协议写明「8 列 / score/原因」，加第 9 列=迁移 135 行+改 5 写入方+prompt 再改一轮，弃方案二）。
- **落地三件**：①`track_sold_forward.py`——台账加 reason 列（账本源 `_reason_from_score`：score 非数值=归因、数值/0.0 如实留空**禁止猜测**；桥 fills 源=fill 自带 reason 戳透传）+ `drop_known_fake()` 剔除假成交残留（KNOWN_FAKE_SELLS 四元组 code/date/vol/acct）+ 报告表加归因列；②写入侧堵漏：`qmt_clear.py` 清仓 SELL 行 score 0.0→`CLEAR_ALL`（原唯一仍写无归因 SELL 的写入方）；③`qmt_config.py` TRADE_LOG_FIELDS 注释固化「score/原因」双语义约定+写入侧硬约束+63 行历史缺口披露。
- **设计要点（防再踩）**：KNOWN_FAKE 匹配键**必须含 acct**——首版三元组在 R5 测试实测抓出会误杀「同日同量异账号」真行；累计台账是幂等追加，**源头清除不回滚已落盘的派生行**（300413 10-02 +108% 假成交：fills 源已按 T-20261008-012 清掉，但台账行残留），必须显式 KNOWN_FAKE 剔除，这是唯一清除途径。
- **测试与核验**：新建 `research/tests/test_track_sold_forward_reason.py` **25/25**（R1-R6）；卖出优先级回归 42/42；真实重建 120 SELL→剔 1 假→119 行，601999=SELL_TAKE_PROFIT ✓、10-02 假行清零 ✓、reason 分布与账本/桥逐项对账吻合（SELL_STOP 13 账本+5 桥=18、v13 与账本重复行 dedup 折叠）；台账备份 `.bak_reason_20261010`。
- **遗留（P2 记录不做）**：①2026-08 63 行归因不可考、如实留空；②桥 rotation 卖出 reason="deal confirmed"（非 SELL_MATURE 语义），B 层止损研究需区分「到期 vs 风险」出场时须改桥 fills 写 reason（涉桥重建+人工粘贴）；③已同步 repowiki P16 架构卡（2026-10-10 增量节）。

## 2026-10-10 · ENS 大盘门控落地（T-20261009-008 / E-4：三链补齐最后一块刹车）

- **一句话**：ENS（融合）是同账户三链（V1.x/G2/ENS）中唯一无大盘门控的——大盘急跌日 G2 停买、V1.x 有门控、ENS 照常满额建仓（10-09 实证：V1.x tier=1 半仓档时 ENS 照买 688117）。本次按 `data/ens_alignment_design_20260913.md` 差异二（2026-09-13 已设计未落地）补齐。
- **落点拍板**：买入订单生成在 `rebalance_ens.py`（Python 侧），桥只执行——**无需桥重建、无需人工粘贴**。周一 10-12 起 10:05 ENS 换仓自动带门控。
- **实现（口径同源 G2）**：`import rebalance_g2 as RBG` 复用 `load_hs300_pct`/`_calc_tier` + g2_config 的阈值/部署比例（G2 调阈值/换数据源同步生效；测试注入环境变量同为 `G2_HS300_PCT`，无 ENS_ 前缀）。tier 块插**降级闸后**无条件执行（plan 恒记 `hs300_pct`/`tier`/`deploy_pct`）；买入闸加 `deploy_pct>0`（T=0 整段不执行：不买、**不超配减仓**，对齐 G2——kept 不被动减仓）；`target_value = capital×deploy_pct/TOP_N`，T=2 时 DEPLOY_PCT(0.98) ≡ 1−RESERVE_CASH_PCT(2%)，**行为零变化**（新旧公式逐分一致有测试）。
- **语义要点（易误解）**：①T=1 半仓时 **kept 超配减仓同按半仓目标**（G2 同口径，单一 target_value）——半仓日会把超配持仓修剪到半仓目标，这是对齐设计而非 bug；②恰 −1.0% 判满仓（`<` 严格小于，DE 体检 P2-6 口径）；③数据缺失 fail-safe T=1 半仓（降速不裸奔）。
- **测试**：新建 `research/tests/test_rebalance_ens_tier_gate.py` **33/33**（R1-R9，含边界/双闸共存/先卖后买序）；回归 42/42 + ENS 状态机 108/108。真实 dry-run 注入 −2.0 → 零订单 + plan 落档 tier=0 ✓。
- **风险声明（季评必带）**：ENS 网格寻优（+0.211% 候选）**未含门控**——纸面 ens 臂自此与实盘口径分叉（实盘多了门控），属防御性偏离，方向保守；P1-1 季评报告需带此注记。
- **踩坑提醒**：rebalance_ens.py 的 dry-run **会推飞书 NOOP 消息**（`_notify_feishu` 在 NOOP 分支无 dry-run/live 区分，预存行为）——周六测试已推 1 条无害「今日无买卖」给诚哥；后续测试注入跑 dry-run 前要有此预期，勿频繁跑。

## 2026-10-10 · C 层慢门控立项（T-20261009-009：设计+回测方案交付，不部署）

- **一句话**：三臂补慢尺度仓位门控（防「温水煮青蛙 regime 下移」，与 E-4 快门控互补）。交付物 `讨论室/C层慢门控立项与回测方案_20261010.md`；立项动机如实写「**换回撤**非提收益」（P10 MA200 反例内嵌：−29.7%→−15.8% 换年化 −4.4pp）。
- **核心设计决策**：①主候选 R0=沪深300 63 交易日回看跌≤−8%→降仓 30%，hysteresis −4% 恢复；②**双指数对照（vs 中证1000/2000）**——小盘池配大盘指数会漏防 2024 式微盘崩塌，「选对温度计」可能比阈值重要；③与 E-4 独立触发、乘法叠加，各自可归因；④预注册判据走 **P21 证伪线模式**（falsification yaml 只读判定，GO/NO-GO），六判据含 V 反段踏空 ≤3pp 与触发次数充分性（≥8 次），判伪即归档。
- **隔离纪律**：不与 B1/B2 混跑（受控变量全锁实盘值，单变量）；不占 B1 本周窗口（C 层回测排 B1 网格出结果后）；不部署（接入点届时在三个 rebalance Python 侧，与 E-4 同构无需桥重建）。
- **后续 C-1~C-4 清单**（指数数据 Day 0 检查→预注册锁定→回测判定→报告拍板）在文档 §六；纯立项无部署，repowiki 知识卡待 C-4 部署时再同步。

## 2026-10-12 · ST 股剔除三层防线（T-20261012-003：G2 实盘买入 \*ST华鹏实锤触发，诚哥拍板存量次日强卖）

- **触发**：G2 臂当日 10:09 实盘买入 \*ST华鹏 603021.SH（7600 股 @4.17，当日候选池仅此一只）；ENS 当日候选含 600180.SH=ST（幸未买）且存量持仓 000610.SZ=ST。**根因**：候选链特征快照（g2_latest_features.parquet / incremental_daily.parquet）均无 is_st 列，deploy_predict*.py / review_full.py 无 ST 过滤 → ST 股畅通无阻直达实盘。
- **诚哥拍板（question 工具）**：存量两只 ST（G2 603021 / ENS 000610）**次日开盘强制卖出，不等持有期**。
- **三层防线（全 Python 侧，零桥重建零粘贴）**：
  - **L1 候选生成端根治**：`position_filter.py` 新增 `load_st_codes()`（主库 daily is_st 最新数据日快照 + `_ST_CACHE` 进程内缓存，失败返 None fail-open）、`fetch_name()`（腾讯实时简称）、`is_st_name()`（'ST' in name）、`st_guard(code, name=None)→(skip,reason)`；`deploy_predict_g2.py`（G2 主臂+ENS 臂）、`deploy_predict.py`（V1.x）、`review_full.py`（V1.x selection_full）候选输出前批量剔除。
  - **L2 执行端买入拦截**：`rebalance_g2.py`/`rebalance_ens.py`/`rebalance_daily.py` 买入循环 `PF.st_guard`（主库快照+腾讯实时名称双层，`_fetch_ohlc`/`PF.fetch_ohlc` 补 name 免二次 HTTP；两层皆失 fail-open 与 gap_guard 同哲学）。
  - **L3 存量强制卖出**：G2/ENS 卖出循环与 rebalance_daily 成熟度循环对 ST 持仓**不等 HOLD_DAYS 直接 SELL**（reason="ST风险警示剔除...(强制卖出)"，rebalance_daily remark=`planA_st_exit`）；T+1 锁定可卖 0 则 skip 顺延次日（st_guard 每日重判）。
- **验证**：新专项 `research/tests/test_st_exclusion.py` **34/34 PASS**（S1-S7 PF 单元 + S8-S11 G2/ENS build_plan 集成：强卖/顺延/拦截/次位照常买）；回归全绿：tier_gate 33/33（run_case 补 PF.st_guard 桩防撞真实网络）+ sell_priority 42/42 + track_sold_forward_reason 25/25 + rdate_guard 5/5 + crosscheck 16/16 + ens/g2/v13 状态机 108+108+55；py_compile 7/7；真实烟囱 5/5（真实主库快照 asof 2026-09-30 共 201 只 ST：603021/600180/000610 全拦截、002762/000001 放行）。
- **生效路径**：今晚 20:05 g2_candidates_night / 16:30 daily 链夜跑起新候选已无 ST；明日 10:05 G2/ENS 换仓自动执行 603021/000610 强制卖出；V1.x 明日 09:45 review_full 链同步生效。
- **已知边界**：主库 is_st 快照随供应商包滞后约 2 周，新戴帽由腾讯实时名称层兜底（双层互补）；`rebalance_daily.py` main() 强依赖 xtquant 无法离线单测，由 PF 单元+同构评审覆盖；ST SELL 未成交时 hold_dates 已删 → 走「建仓日缺失 fail-safe」既有路径（与正常到期卖出同风险，桥重试兜底）。
- **登记**：看板 T-20261012-003（全局控制台.md）+ repowiki P16 知识卡已同步。

## 2026-10-12 · TRAE 换仓任务幂等前置检查补强（T-20261012-005：P0-1/P0-2）

- **缘起**：TRAE 侧 13 条定时任务全量 prompt 排查（P0×2/P1×3/P2×2），本轮只修 P0-1、P0-2（换仓类任务的「二次跑」防线），P1/P2 待诚哥后续拍板。
- **根因（关键机制发现）**：`rebalance_daily.py` 的 LIVE 互斥锁是**进程级瞬态锁**——`_acquire_live_lock()` 用 `os.O_CREAT|O_EXCL` 原子创建 `data/rebalance_live.lock`（内容 `pid\t日期`），`_release_live_lock()` 在进程退出时（L973）删除锁。因此它**只防并发、不防顺序重跑**：兜底任务先跑完释放锁后，TRAE 任务后启动可再次抢锁重跑。→ 坐实「TRAE prompt 的 step-0 幂等前置检查是唯一能防 TRAE 会话二次跑的有效拦截层」。
- **危险交错（10-12 实证）**：Windows 10:05 兜底任务（`rebalance_daily_guard.ps1`）与 TRAE 09:45 任务**都跑 `rebalance_daily.py --live`**；TRAE 迟到过 10:05 即自动踩上双 live 交错（当日账：兜底 10:05:41 写 plan，TRAE 10:06:21 才启动）。
- **幂等三层结构（为何必须补 prompt 层）**：① 脚本本体无幂等（`rebalance_daily.py` 的 `executed_live` 只写不查，全文仅 3 处引用无读取检查）；② guard ps1 三标记只防 guard 自己；③ TRAE prompt step-0 前置检查是唯一能防 TRAE 会话二次跑的层。
- **修复（三任务 `Schedule update`，均为 prompt 层，代码/脚本零改动）**：
  - **P0-1 `15868a74`（开盘实时复核(项目16)）**：一次 update 修两缺陷——(a) 补**完整版交易日门禁**（`【第零步之一·强制交易日门禁】`= 非交易日禁止事项硬约束 + 防绕过条款，此前为简版）；(b) 新增 `【第零步之二·换仓幂等前置检查】`**两标记**（标记1 `data/rebalance_<今日YYYYMMDD>.json` 存在且 `executed_live=true`；标记2 `data/schedules/rebalance_daily_guard_<今日YYYYMMDD>.lock` 存在）→ 命中则**跳过 step3 `--live`**，仅只读核对 + 推说明卡结束。
  - **P0-2 `30344e79`（G2换仓(项目16大QMT)）**：补 `【第零步之一·强制交易日门禁】` + `【第零步之二·换仓幂等前置检查】`**三标记**（标记1 `D:/QMT_POOL/g2_bridge/cmd/orders_<今日YYYYMMDD>*`；标记2 `data/rebalance_g2/rebalance_g2_<今日YYYYMMDD>.json` 存在且 `written=true`；标记3 `data/schedules/rebalance_g2_guard_<今日YYYYMMDD>.lock` 存在）→ 命中 `[SKIP]` 全部换仓步骤。
  - **P0-2 `051bc2d9`（融合换仓(项目16大QMT)）**：现有 step-0 的 orders 原名检查**同坑顺手修**——三项（`orders_<今日>*` 通配 / `rebalance_g2_ens_<今日>.json` written=true / `rebalance_ens_guard_<今日>.lock`）。
- **关键坑（`_done_` 改名）**：桥处理后 `orders_<date>.json` 改名为 `orders_<date>_done_<seq>.json`（bridge src L216），幂等检查**只查原名会在桥处理后约 30 秒漏检** → 三处 orders 检查**必须用 `orders_<今日>*` 通配**（G2 与 ENS 均按此修正）。
- **dry-run 不构成幂等标记**：dry-run 写 `rebalance_<date>.dryrun.json`（rebalance_daily L761）或 `rebalance_g2_<date>.dryrun.json`（rebalance_g2 L707-708，`suffix = "" if plan.get("written") else ".dryrun"`），不写 `executed_live`，**不作为幂等标记**（故标记必须锚定正式 plan 的 `executed_live`/`written` 字段）。
- **生效与复核**：三任务 `Schedule get` 逐字核对 prompt 确认落地；cron（`45 9`/`50 9`/`31 9` 均 `* * MON,TUE,WED,THU,FRI`）与 Status（均 Active）未变；Next run 均为 2026-10-13 → **自次日换仓起生效**。
- **未决（本轮范围外，P1/P2 待拍板）**：① `e05e526a`（夜间检修）时点回填 09:31/15:50 + 两个 Paused 副本处置（建议删除优于补条款）+ 清理「成本锚09:00」引用；② `b0254f11` 步骤2 改条件执行（仅当步骤1 未跑 `deploy_predict_g2` 时执行）；③ `3c7fdd1e` 暂不动。
- **登记**：看板 T-20261012-005（全局控制台.md）；纯 prompt 层变更、无代码/脚本/路径改动，repowiki 知识卡不适用（`_done_` 改名与 LIVE 瞬态锁语义已在本卡记录）。

## 2026-10-12 · TRAE 定时任务瘦身与 prompt 时点回填（T-20261012-006）

- **缘起**：承接 T-20261012-005 的「未决（P1/P2 待拍板）」清单，诚哥拍板「**未启用的定时任务就删除，剩余的工作都完成**」。
- **删除 2 个长期 Paused 冗余任务（用户明确授权）**：`eda9b0c3`（成本锚生成 09:00）、`c63cff58`（Tushare 刷新 19:30）。**删除前已实测替代者为 Windows 计划任务**（`\Quant_P16_CfgPremarket_0900`、`\Quant_Tushare_Moneyflow_Refresh` 均在位）→ 功能无真空；对 `nightly_check.py` E6 无副作用（E6 仅在 `conflict_with` 标记任务 Active 时 FAIL，删除 Paused 项只会减少观测面）。
- **更新 `e05e526a`（夜间检修(项目16)）prompt**：第 2 步改为「**按执行时点排序**核对次日定时任务 Active 状态」并回填正确时点——**融合换仓 09:31**（旧值误记 09:52）、**融合日终对账 15:50**（旧值误记 15:45）；移除两个已删任务引用；另加说明段（声明成本锚/Tushare 刷新已由 Windows 任务承接、不在本清单内，若发现其残留 Active 属异常）。cron `0 1 * * MON,TUE,WED,THU,FRI` 与 Active 状态未变。
- **更新 `b0254f11`（集合竞价增强预判(项目16)）prompt**：步骤 2 改**条件执行**——先确认步骤 1 的 G2 候选（`data/selections/g2/` 下 D_g2_top10.csv）已就绪则**仅核对、不得再跑 `deploy_predict_g2.py`**（免覆盖、免浪费算力）；仅当该文件缺失/为空（步骤 1 中断未产出）才补齐。保留 `--top 10` 口径提醒（严禁止 `--top 2`）。cron `25 9 * * MON,TUE,WED,THU,FRI` 与 Active 未变。
- **快照刷新**：`data/tw_schedule_snapshot.json` → `generated_at` 2026-10-12 23:01:02，tasks 由 14 项收敛为 **11 项（全 Active）**（移除 eda9b0c3/c63cff58/f43df15a），修正 051bc2d9 cron `52 9`→`31 9`，`owner_boundary.note` 补「2026-10-12 起不再有 conflict_with 标记的任务」（E6 快照一致性不破）。
- **规格镜像同步**：`specs/02_e05e526a_夜间检修_prompt全文.txt`、`specs/02_b0254f11_集合竞价_prompt全文.txt` 已与线上 prompt 逐字一致；`specs/TRAE任务_prompt全文_待粘贴_20261002.md` 更新「13 任务」→ **11 全 Active**。
- **`3c7fdd1e`（候选预生成）按上轮结论保持原状不动**。
- **登记**：本卡 + 全局控制台 T-20261012-006 + repowiki P16 知识卡。
- **性质**：**纯 prompt / 调度层变更，代码与脚本零改动**；无 commit 动作（工作区）。

## 2026-10-13 · 周更重训「假成功」诊断 + optuna 回归修复 + 补跑（T-20261013-001）

- **缘起**：诚哥问「周更训练是不是滞后了」→ 诊断 Project_16 周更重训（Windows 计划任务 `\quant_weekly_retrain`，每周一 17:00）。
- **诊断结论（调度准时但「假成功」）**：任务 Last Run 2026/10/12 17:00、Next 2026/10/19 17:00、Last Result 0，**调度层面正常**；但 `run_scheduled.ps1` 捕获子步骤失败后仍打印 `[retrain] 完成` 并 exit 0 → **`Last Result=0` 不代表重训成功**。实证：V1.3 正式模型自 **2026-09-14** 起未更新（滞后近一个月）；10-12 那次更彻底——`retrain_20261012_170001.log` 中 `train_optuna` 因 `ModuleNotFoundError: No module named 'optuna'` 崩溃（exit=1），候选未产出。
- **根因（T-20261012-001 的连带回归）**：T-20261012-001 把 `Get-GoodPy` 首选解释器切到 miniqmt venv（该 venv 实测可用但**未预装 optuna**），而原 TRAE SOLO fallback（10-08 成功那次所用）已损坏（缺 `encodings`）→ 训练腿崩溃。**教训**：切换 Python 解释器首选时必须同步核对目标 venv 的**全部**依赖（不止 numpy/lightgbm，还有 optuna 等训练专用包）。
- **修复（方案1）**：`pip install optuna optuna-integration` 装入 miniqmt venv → **optuna 5.0.0 + optuna-integration 5.0.0**（连带 alembic 1.20.0 / sqlalchemy 2.1.4 / Mako 1.4.3 / MarkupSafe 3.0.4 / colorlog 6.12.0）；smoke test 通过（`SMOKE OK best= 0.5 lgbm= 3.3.5 optuna= 5.0.0`，exit=0）。
- **补跑（方案2）**：`run_scheduled.ps1 -Mode retrain`，2026-10-13 06:07:28 启动 → 07:37:54 结束（日志 `data/schedules/retrain_20261013_060728.log` L221 `[retrain] 完成`）。
- **补跑结果（逐步骤）**：feature_health_weekly PASS（v3 ALERT 0/WATCH 1；g2 ALERT 0/WATCH 5）→ refresh_panel_v3 exit=0（合并日线 14,741,079 行 | 最新 2026-10-12；v3 切片 27/27；日历重建末日 2026-10-12）→ refresh_panel_enh exit=0（4,849,185 行；6 项 enh 复现率全 100%）→ check_enh_freshness exit=0（落后 0 自然日）→ **train_optuna exit=0（成功越过 `import optuna`，仅 FutureWarning：`optuna.integration.lightgbm` 已 v4.9 弃用、v6.0 移除），最优验证 IC 0.08975 / best_iteration 3704 / 准确率 0.5216 / test IC 0.04374 / 日均 IC 0.04318 / ICIR 0.3592，候选 `D:/QuantLab/models/lgb_model_v3_retrain_20261013.txt`** → **策略层门禁 FAIL（exit=2）**：live（`lgb_model_v3_pre_retrain_20260928_170119.txt`，门禁取最新 pre_retrain 备份）excess=+0.0011（胜率52.9%/盈亏比1.82/回撤-27.6%/交易102）vs 候选 excess=-0.0002（胜率54.2%/盈亏比1.26/回撤-33.4%/交易96），差 -0.0013 > 容差 0.0002 → 拒绝上线，写 gate_rejected（next_retrain=2026-10-19）→ verify_model_panel_sync WARN（设计内等待：正式模型 09-14 版 vs 面板超前 28 天）→ auto_promote 跳过 → 在线增量 lhb/report_rc（Tushare 限频）/北向三表 ERROR + daily_basic NO_NEW_DATA（均 best-effort 不阻断）→ **G2 腿 train_g2.py 候选 `lgb_model_v3_g2_strong_real_20261013_1514t.txt`（best_iteration=1514，test IC=0.08197），strict 门禁 ic=PASS / tail_ic=FAIL / top2_ret=PASS => FAIL** → G2 live 保持。
- **结论**：补跑达成核心目标（optuna 修复生效、全链路跑通、候选正常产出），两腿候选均被门禁**按设计**拒绝，**正式模型保持 2026-09-14 版**（与 10-08 同款保守行为）。
- **遗留观察（未修）**：optuna 5.0 触发 `optuna.integration.lightgbm` FutureWarning，v6.0 将移除该路径，届时需改用 `optuna_integration.lightgbm`（当前不影响运行）。
- **登记**：本卡 + 全局控制台 T-20261013-001 + repowiki P16 知识卡。
- **性质**：本轮 optuna 修复属 **venv 层改动（无仓库文件变化）**；T-20261012-001/-003/-006 批次改动**仍未 commit**（工作区）。
- **关联**：T-20261012-001（解释器切换引入的连带回归，本源）、T-20261012-003（同批 ST 剔除）。


## 2026-10-18 · 评分卡层消融：内产版 18.8% vs 官方 113.6% 的 6 倍差距拆解（T-20261018-001）

- **动机**：诚哥追问 B21 内产版（沙箱特征 + 简化红线 prob≥0.60）年化 +18.8% 与官方口径 G2 +110.1%/+113.6% 差 6 倍，差在哪没拆过。
- **方法**：官方引擎 `scan_rotate_cost_real.py`（可执行 open→open）2×2 消融 = **模型档（0825_1964t / 0915_1522t 现役）× 评分卡层（ON / OFF）**。`scorecard_OFF` 把 per_day 帧内 `total_new` 换成 `prob*100`（帧内含 prob 列，**引擎零改动**），等价沙箱内产版简化红线语义。窗口 2024-07-01~2026-08-14 / N15 / live_trail / 红线60 / TOP2 / 滑点0.1% / deploy0.95 / 单源 astock。
- **校验档 PASS**：0825+评分卡ON = 总收益 357.41% / 年化 110.12% / 夏普 2.278 / 回撤 -16.71% / 78 笔，与官方锚点**逐项一致**。
- **结果**（总收益/年化/夏普/回撤/交易）：**0825+ON 357.41%/110.12%/2.28/-16.7%/78**；0825+OFF 43.86%/19.43%/0.70/-34.8%/74；**0915+ON 191.82%/68.71%/1.84/-19.6%/81**；0915+OFF 72.09%/30.36%/1.00/-22.0%/75。
- **分解**：**评分卡层 = 最大单一因素**（0825 -90.7pp / 0915 -38.4pp）；**模型档（有评分卡）0915 比 0825 低 41.4pp**（无评分卡时反高 +10.9pp）→ 评分卡增益随模型差异极大，不可当常数外推。风险面：拿掉评分卡回撤 -16.7%→-34.8%、盈亏比 2.70→1.17（评分卡同时供收益与风控）；执行跳过 37→81。
- **结论**：①内产版 18.8% 与官方 113.6% 的差距主体 = **沙箱砍掉评分卡层（F2/F5/F6 不可内移）**，非模型退化；②**待核查新发现（未改任何生产配置）**：现役 0915 模型官方口径仅 +68.71%，低于 0825 的 +110.12% 达 41.4pp，暗示 09-15 换版在官方口径下可能是降级（与 09-10 门禁记录 08-25=+0.213%/日 方向一致）——是否回退属策略变更，须单独实证 + 拍板；③无评分卡口径（19.43%/30.36%）与 B21 报告 18.79% 同量级 → 两 harness 互证；④**附注不一致**：B21 报告「现役43f 臂」6.59% 低于本消融同模型同红线 30.36% 约 24pp，原因未定位（TOP3+tier+gap+窗口差异），建议后续核查该 harness。
- **产物**：报告 `results/评分卡层消融_20261018.md`｜脚本 `research/ablation_scorecard_20261018.py`｜指标 `research/results/ablation_scorecard_20261018.json`｜日志 `data/real/ablation_scorecard_20261018.log`。
- **登记**：本卡 + 全局控制台 T-20261018-001 + engram。**引擎零改动、未 commit**。
- **关联**：T-20261017-002（B21 内产版策略级回测，本消融的上游疑问）、G2/ENS 信号内产版重训验收（评分卡不可内移的拍板背景）、2026-10-15 架构红线（沙箱唯一合法模式）。


## 2026-10-18 · 独立模型回退实证：0825_1964t vs 0915_1522t（三轴，T-20261018-002）

- **缘起**：承接 T-20261018-001 的新发现（现役 0915 官方口径 +68.71% < 0825 +110.12%，差 41.4pp），但该结论只来自单窗口单配置 → 诚哥指令做独立实证。**预注册判定**：主口径（N15/TOP2/d95）下 0825 在**全部窗口**都不低于 0915 且实盘对齐口径（TOP3/d98）同向 = 「0915 为降级」；否则「证据不足」。
- **轴 A 模型层（官方门禁 harness，同面板同窗 test 2024-07+，0825 首次测）**：0825 ic 0.08101 / tail_ic 0.11592 / top2_ret +0.01411(n=106) vs 0915 **0.08111 / 0.12267 / +0.01975(n=105，+40%）→ 0915 三项**全面不劣于** 0825，**模型层无降级**。
- **轴 B 策略层（官方引擎评分卡链）**：主口径全窗 0825 **+110.12%**(2.28/-16.7%/78笔) vs 0915 +68.71%(1.84/-19.6%/81笔) = +41.4pp；分窗 2024H2 +80.96 vs **+83.86**（0915 反超）/ 2025 +96.97 vs +93.68 / 2026 至 08-14 **+198.76 vs +132.72**（+66pp）→ **差距集中于 2026 小样本段**；实盘对齐口径 TOP3/d98：0825 **+76.30%**(1.97/-16.2%) vs 0915 +46.11%(1.47/-23.7%)，+30.2pp 同向。
- **轴 C 前向纸面（弱轴）**：0825 年代（08-18~09-14，52 笔全平）live_trail 均 **+0.72%**/胜率 42.3% vs 0915 年代（09-15~10-08，20 笔平）**-5.07%**/15.0%。
- **轴 D 池诊断**：过线候选相当（3.82 vs 3.86 只/日），0825 Top2(total_new) 日均 fwd_ret **+19bps** vs 0915 +11bps；Top10(prob) 池 +0.0015 vs +0.0011 → 差异在**池被评分卡加工后的选择质量**（-8bps/日 ≈ 复利 41pp 量级）。
- **判定：「证据不足」**（2024H2 反超，预注册条件未满足）。正确画像 = **模型层 0915 持平略优 + 评分卡链层全窗 0825 明显优且差距集中于 2026**（模型层与策略层背离）。
- **三条硬限制**：①功效不足（分窗 22~36 笔、2026 段 66pp 由 22 vs 23 笔决定，P12 单票运气教训）；②非同训练数据对照（0825 用 08-25 vintage 面板、0915 用 09 月刷新面板）；③test 窗选择偏差（周更门禁用 2024-07+ 筛模型，同窗再比收益有自我确认风险）。
- **建议（未改生产）**：①不动 live 指针、不回退；②**立纸面双轨 A/B**（复用 `deploy_predict_g2.py --ab` 基建，同快照/评分卡/红线每日同跑 0825+0915，累积 15~20 交易日 forward mean_excess，判定线「候选 ≥ 现役 −0.02pp」）；③可选：2026 段逐笔 Welch t 检验 + 剔除单票影响。
- **产物**：报告 `results/模型回退实证_0825_vs_0915_20261018.md`｜脚本 `research/model_rollback_0825_vs_0915_20261018.py`（--axis model|strategy|forward）｜json `research/results/model_rollback_0825_vs_0915_20261018.json`。**引擎/门禁零改动、未 commit**。
- **登记**：本卡 + 全局控制台 T-20261018-002 + repowiki + engram。**关联**：T-20261018-001（纠正其"0915 疑似降级"初判）、周更门禁 harness（T-20260910-003/T-20261013-001）。


## 2026-10-18 · G4 观察期回滚闸修复 + 0915 回退裁决（T-20261018-003）

- **现象**：G2 live（0915_1522t）自带 10 交易日观察期 trial，但 `rollback_check_g2.py --auto` 每天只打「样本不足（<3 笔），暂不判定」，外层 `run_scheduled.ps1` 统一打印「…OK」→ 观察期回滚保护**空转**（纸面降级闸同样吃不到样本）。
- **根因（三层，实测）**：①`paper_forward_daily.ps1` 把 `$py` **硬编码**为已损坏的 TRAE SOLO 内置解释器（`init_fs_encoding ... No module named 'encodings'`）——它是 **T-20261012-001 漏修的第 4 个脚本**；②16:45 纸面管道里**每个步骤都直接崩**（日志该致命错 **40 次**），ps1 在全部步骤跑完后 exit 2 → `paper_forward.py --backfill` **压根没产出**（**非**「覆写」亦**非**「静默空产」；候选行仍涨到 10-12 是 20:05 的 `g2_candidates_night` 腿在追加）；③`ret` 停在 09-16 → G4 永远样本不足。日志证据：`BUILD-ALERT build_g2_daily 异常退出 (exit=1)` 出现在 **09-28 与 10-12~10-16**，与「最后成功回填 10-09」吻合（内置解释器损坏是**间歇性**的，09-30~10-09 又能跑通）。数据源 `merged_daily_full`/`incremental_daily` 均新鲜到 10-16 → **非行情断更**。
- **修复（4 项）**：①ps1 改用与 `run_scheduled.ps1` 同口径的 `Get-GoodPy`（miniqmt venv 首选、实测 `import numpy, lightgbm` 通过、再 PATH、再内置；全崩 `PY-ALERT` exit 1）；②`rollback_check_g2.py`：**次级样本源兜底**（主源 <3 笔时读 `paper_forward_exit_live.csv` 的 `live_trail_ret`，rank≤3）+ **宽限期 fail-loud**（观察期满再宽限 10 交易日仍无样本 → `[ALERT]` + `exit 3`）+ 输出 `VERDICT=` 机读标记；③`run_scheduled.ps1` G4 分支加 exit 3 ALERT、exit 0 不再笼统打 OK；④两 ps1 **补回 UTF-8 BOM**（编辑剥 BOM 教训第三次命中）。
- **验证**：新单测 `research/tests/test_rollback_check_g2.py` **22/22 PASS**；`py_compile` OK；两 ps1 **PS 5.1 BOM=True / PARSE_ERRORS=0** 且抽出解析块实跑选中 miniqmt venv；**回填恢复 133/225 → 198/224 笔**（备份 `.bak_20261018`）。
- **G4 真实裁决（只读，未 --auto/--apply）**：观察期候选 67 笔前向均值 **-7.75%** vs 基线 +0.95% → **`VERDICT=deep_worse`（深度劣化 8.71pp >> 0.05pp）**，`rollback_streak.json` 记 `streak=1/3`。**⏳ 待诚哥拍板**：若后续两个交易日续判 deep_worse，约 10-21 `--auto` 会**自动回退 live→0825_1964t** 并推飞书（设计内行为）。选项：A 放行（推荐，与 T-20261018-002 策略层结论同向）/ B 先暂停等纸面 A/B / C 立即人工 `--apply`。
- **产物**：报告 `results/G4观察期闸修复与0915回退裁决_20261018.md`、单测、看板 T-20261018-003。**未动 live 指针、未 commit**。**同型遗留**：`start_card_webhook.ps1` 仍硬编码同一损坏解释器。
- **关联**：T-20261018-002（上游实证）、T-20261012-001（解释器缺陷本源，漏修第 4 脚本）、T-20260913-001 M3（G4 自动化落地）、DE 2026-09-29 §4-1（追加污染 P2，仍未定位，但不解释本次断档）。


## 2026-10-19 · B21 干净对比收尾：生产评分卡 sc-mode 双口径稳健劣于红线/B21 + tier 口径分叉发现（T-20261019-003）

- **做了什么**：把生产评分卡语义（deploy_predict_g2：top100→评分卡红线60→total_new 排序；F2=元/mf_main_net×1e4、F5=ind_pct_ths、F6=est pe_ttm/turnover 池级命中<50% 回退）完整移植进 `backtest_b21_strategy.py`（`_sc_scores()` 向量化 np.select；`sel_mode=sc/sc_wan`；`--sc` 5 配置含第 5 臂「万元退化 F2 对照」`mn_scale=1.0` 直传元阈值=官方引擎行为；fail-loud 万元单位自校验：退化档 1/8/10 必为 0 否则 SystemExit）。退化 F 分布 2:58.1%/5:2.1%/6:39.8% 精确复现官方 {2:60.2%,5:2.2%,6:37.6%}。
- **⚠️ tier 口径混杂发现（决定性）**：并行审计 T-20261019-002 修复 #18 改了本脚本 tier 门控（T+1 开盘跳空→T 日自身涨跌），落点在旧基线跑数后、首次 sc 跑数前 → 跨口径混杂。定位方法=**tier_cnt 指纹**（旧 [7,16,525] vs 新 [39,26,483]）。全量双口径重跑，tier=off 锚点两口径同值 +18.20% 自洽（唯一变量=tier）。新增 `--tier-caliber {t0_close,t1_open}` + 输出分流（t1_open 仅 json：`bt_b21_result_t1open.json`/`bt_b21_sc_result_t1open.json`，不覆盖报告）。
- **双口径核心数字（slip0.1%，G2 臂，2024-07~2026-09）**：t0_close——B21 +5.84% / 现役43f 红线 +12.26% / 生产 SC60 **-3.87%** / 退化F2 -5.12%；t1_open——B21 +18.79% / 现役 +6.59% / SC60 +4.79% / 退化F2 +4.67%。
- **双口径稳健结论**：①生产评分卡均劣于同口径 prob 红线（评分卡层 -16.1pp/-1.8pp）且均劣于 B21（-9.7pp/-14.0pp）→「沙箱解除后原方案不更优」对 tier 稳健；②官方消融 +38.4pp 双口径×双 F2 语义均不可复现（F2 语义差仅 -1.3pp/-0.1pp）→ 主因=官方 harness，T-20261018-001「红线降级主因」假设**证伪**；③**B21 vs 现役相对强弱随 tier 口径反转**（gap +12.2pp vs -6.4pp）——t1_open 是 live「09:50 看当日跳空+09:50 市价成交」的忠实同刻镜像（非严格前视），t0_close（审计默认）保守但偏离 live 语义，**取舍待诚哥拍板**。
- **工程坑（新）**：①write_report 硬编码结论在新数字下自相矛盾 → 改符号自适应（内产版优劣/滑点/ tier 贡献/红线甜点四条）；②**--all 再生成会冲掉报告手工段** → 手工内容（口径边界/历史对照表/差异主因）全部下沉 write_report 源码（静态带日期，regeneration-safe）；③%-格式串里嵌套三元 `%s` 实参字符串中的 `%%` 不被格式化（渲染成双百分号）——内层字符串直接写单 `%`；④PowerShell 控制台 GBK 乱码但 UTF-8 文件正常，勿按控制台字面修文件。
- **产物**：`results/B21_干净对比_生产评分卡口径_20261018.md`（双口径）、`results/B21信号内产版_策略级回测报告_20261017.md`（双口径重建）、json×4（含 tier_caliber 字段）、备份×3（`*.tier_old_20261019.bak.json`/`*.run1_20261019.bak.json`/`*.md.pre_tierfix_20261019.bak`）。确定性：sc 三轮全同、--all 两轮全同、F 分布六轮全同。
- **遗留**：未 commit；tier 口径拍板；SC58 t1_open +14.78% 反常高值仅记录；周一 17:00 周更（净化面板）后需同口径复跑验证。
- **关联**：T-20261018-001（消融，本任务证伪其主因假设）、T-20261019-002（审计 #18 即 tier 变更源）、T-20261017-004（B21 回测本体）。




## 2026-10-20 · P16 体检 + 自闭环监控层对齐（T-20261020-003，发现 2 个真缺陷）

- **任务**：诚哥「给 p16 项目做个体检，发现问题直接修复」。范围=夜间体检 33 项 + 23 个单测套件 + QMT 实机日志交叉核对。
- **【P0 真缺陷①】G2/ENS 今日盘中全程停跑，且监控层看不见**：`D:\QMT交易端模拟` 上 `STRATEGY_P16_G2_BRIDGE.py` / `STRATEGY_P16_ENS_BRIDGE.py` 于 **08:52:30 被 `python formula destructed` 卸载**（同批 `QMT_SANDBOX_PROBE.py` 抛 `KeyboardInterrupt:策略被手动停止`），此后至收盘**零输出**。直接后果：10:07 G2 guard、10:08 ENS guard 双双 `[ABORT] 桥心跳异常，拒绝写单` → **当日 G2 买入（000960.SZ 1200股@25.27）与 ENS 两笔到期卖出（002841.SZ / 600691.SH）全部未执行**。**监控为何没报**：①`nightly_check.py` E2 用 48h 阈值 → 停跑 4h 仍判 PASS；②`check_bridge_heartbeat.py` 虽在 12:00 抓到了，但它读的是**文件心跳**（heart_20261016.json，83h 前），告警文字误导为「桥心跳超时」而非「策略已停」。**⚠️ 后续更正（T-20261020-005 实测）**：G2/ENS **并非全天停跑**——13:44:42 已重新加载并心跳至 15:00:03，真实影响窗口 08:52~13:44（覆盖 09:50/09:52 换仓时点，故当日买卖仍未执行，但 13:44 后风控已恢复在岗）。**修复**：`nightly_check.py` 新增 **E2b 盘中存活检查**——交易日且过 10:00 时，当日最新 `[HEART]` 落在 09:30 之前或当天无心跳 → **FAIL**（区分「停跑」与「心跳旧」）；`_log_heart` 加 `latest_trade_date` 参数支持「锁当日」严格判定。**实测**：修复后体检正确报 `E2b 盘中存活 G2/ENS FAIL 最新心跳停在 08:51`、V13 PASS（11:29）。**⚠️ 未修复项（需人工）**：策略本身仍停着，**必须重启 QMT 客户端并重载 G2/ENS 策略**（沙箱/策略进程不可由外部脚本拉起）。
- **【P0 根因②】心跳通道自闭环化后，外部判定全断**：G2/ENS/V13 自 2026-10-15~16 起「自闭环零文件」，`heart_<date>.json` **永久停写**，唯一通道是 print `[P16xx][HEART]` → `XtClient_FormulaOutput_<date>.log`。但外部 5 处仍在读文件心跳：`qmt_bridge_client.read_heart` / `qmt_bridge_client_ens.read_heart` / `check_bridge_heartbeat.py` / `nightly_check.py` E2 / `rebalance_g2|ens` 的 `is_bridge_alive`。**⚠️ 关键坑（不是「文件不存在才回退」）**：10-16 的旧 `heart_*.json` **一直在盘上**，`if heart: return heart` 会直接返回 83h 前的死心跳，把日志里的新鲜心跳完全掩盖 → 回退形同虚设。**正确做法 = 两源都读、按 `last_heartbeat` 字符串取较新者**。**修复**：`qmt_bridge_client.py` 新增 `read_heart_from_log()`（解析 `[P16G2|P16ENS|P16V13][HEART]` 行 → 同构 dict，含 `source="formula_log"`）+ `QMT_PATH/QMT_PATH_ALT` 双客户端扫描；`read_heart`（G2/ENS 两客户端）改为双源取最新；`is_bridge_alive` msg 增 `source=` 便于诊断。
- **【P1 真缺陷③】G2/ENS 成交记录彻底消失**：`_write_fill` 末尾只调 `_add_or_update_fill` → `_atomic_write_json`，而后者在自闭环版是**空壳 `return`**（沙箱禁 open）→ **G2/ENS 自 10-16 起所有成交记录无任何落点**（V13 早有 `[P16V13][FILL]`，G2/ENS 漏了）。影响：`reconcile_g2/ens`（读 fills json 恒 0 条）、`push_fill_card.py`、`track_sold_forward`、`analyze_sell_timing` 全部断供；且因当日无成交未能暴露。**修复**：两 src 的 `_write_fill` 在 `_add_or_update_fill` 前补 `[P16G2|P16ENS][FILL]` print，**字段/格式与 V13 逐字对齐**（date sid code action vol px src status ref slip gap retry oids | reason），外部解析器可复用同一正则。**实测**：单测跑通输出 `[P16G2][FILL] 20261020 s1 300413.SZ SELL vol=1000 px=9.4000 src=fill_avg status=FILLED ref=9.500 slip=1.05% gap=0.00% retry=0 oids=1001 | stop_loss`。
- **【噪声清理】C1 白名单过期**：`nightly_check.py` `EXPECTED_TASKS` 仍列 `Quant_Monitor_0945`，该任务及同族 6 个已于 10-17 经诚哥拍板删除（T-20261017-001）→ 10-17~10-20 每天固定 FAIL 1 项。已移出白名单（保留注释说明来龙去脉）。**修复后 C1 PASS**。
- **【陈旧单测批量修复，5 个套件 / 原 6 红 → 全绿】**：根因统一为「源码自闭环化、测试仍按文件桥语义断言」。①`test_v13_state_machine` / `test_v13_deal_fill_slippage`：`_add_or_update_fill` 已删除 → 改为包装 `_write_fill` 捕获返回 rec（**只包一次**，重复 reset 嵌套叠加会让一次调用追加多条、断言假失败）；②`test_g2|ens_state_machine` T19 改为断言 print 心跳字段（`[P16G2][HEART]` 含 date/pending/positions/risk_sold/risk_count/fills/slippage/passive + 60s 节流 + `[HOLD]` 明细）；T20 从「heart json 恢复 pending/alert_count」改写为「账户即账本：当日 order 反查重建 risk_sold/RSK 水位，pending 不从文件恢复」（旧语义已废止，保留必红）；T21 前置显式 `_g_passive=False`（前一用例的桩 init 因缺 `get_market_data_ex` 转被动会早退）；③`test_build_p16_hardening`：行数守恒断言未考虑 `# __VENDOR__` 注入 → 改为「含 vendor 则产物行数 > 源码行数，无 vendor 则严格相等」（V13 仍严格）；④`test_corr_diag_io` H9/H12/H14：H9 北交所 `.BJ` 不在申万一级映射（数据源事实，改判「沪深 100% 覆盖」）；H12/H14 候选 <`min_candidates=5` 时核心走 `_skip` 提前 return、`section_status` 未构建（改判 `sections._skip.status` / `overall==FAIL and date.status==FAIL`）。
- **验证**：**23/23 单测套件 rc=0**（含 build 40/40、G2 状态机 100/100、ENS 状态机 100/100、V13 状态机 55/55、V13 fill 87/87、corr_diag_io 153/153）；`check_py36_compat` exit 0；两产物 `check_build_bridge_artifact` 全 PASS（首行 `# coding=gbk`、GBK 可编解码、BUILD_TAG 恰 3 处、AST 可解析）；夜间体检 **FAIL 由 1→2（新增 E2b 真告警）→ 准确反映 G2/ENS 停跑**，WARN 仅剩 B2（门禁拒绝后设计内等待，非故障）。
- **产物**：`build/strategy_p16_g2_bridge.py` BUILD_TAG=**20261020-122656**（4183 行/544032 B）、`build/strategy_p16_ens_bridge.py` BUILD_TAG=**20261020-122659**（4187 行/544548 B）。源码 `strategy/strategy_p16_{g2,ens}_bridge_src.py` 各补 1 处 FILL print。
- **⏳ 待人工**：①**重启 QMT 客户端 + 重载 G2/ENS 策略**（今日盘中仍停跑，新 build 需粘贴部署，BUILD_TAG 核对 20261020-1226xx）；②新 build 首次实跑需核对日志出现 `[P16G2][FILL]` 与 `[P16G2][HEART]` 且 `passive=False`；③B2 WARN 待 10-26 周更自动恢复。
- **关联**：T-20261015-004（沙箱自闭环改造，本任务即其监控层收尾）、T-20261017-001（Quant_Monitor 六任务删除，C1 白名单源）、T-20261018-003（G4 观察期闸，同为「监控层没跟上架构变更」形态）。

## 2026-10-20 · G2/ENS 自闭环版改名（T-20261020-005，三层全改：文件名 / 日志前缀 / ORDER_REMARK）

- **任务**：诚哥要求「把 QMT 内执行的策略换个名称，区别原有可外部读写的策略」。选定三层全量改名，命名风格 `_selfcontained`（语义直白=自闭环零文件，与旧 `_bridge` 形成对照）。
- **⚠️ 事实更正（推翻本日 T-20261020-003 的部分表述）**：G2/ENS **并非全天停跑**——08:52:30 被卸载后，**13:44:42 已重新加载并持续心跳到 15:00:03**（当日 G2 心跳 75 条、ENS 75 条，首条 08:51:03、13:44:42 起恢复）。真实影响窗口 = **08:52~13:44（约 4h52m）**，覆盖 09:50/09:52 换仓时点 → 当日 G2 买入与 ENS 两笔到期卖出仍未执行，但 **13:44 后风控（止损/止盈/追盈）已恢复在岗**。T-20261020-003 里「此后至收盘零输出」应以此条为准。恢复动作非本 agent 所为（疑似人工重载）。
- **改名清单（四层，全部已落地）**：
  1. **文件名**：`strategy/strategy_p16_g2_bridge_src.py` → `strategy_p16_g2_selfcontained_src.py`；ENS 同理。构建脚本 `build_p16_g2.py` → `build_p16_g2_selfcontained.py`；ENS 同理。产物 `build/strategy_p16_g2_bridge.py` → `build/strategy_p16_g2_selfcontained.py`；ENS 同理。
  2. **日志前缀**：`[P16G2]` → `[P16G2I]`、`[P16ENS]` → `[P16ENSI]`（I = 信号内产 internal）。源码各 103 处全换，产物零旧前缀残留。
  3. **ORDER_REMARK**：`P16G2桥` → `P16G2I桥`、`P16ENS桥` → `P16ENSI桥`。**新增旧值兼容层**：`ORDER_REMARK_LEGACY = ("P16G2桥",)` + `ORDER_REMARK_ALL = (ORDER_REMARK,) + ORDER_REMARK_LEGACY`，两处反查点（`_lookup_order` 排序键、`_rebuild_state` risk_sold 重建）改用 `any(rk in remark for rk in ORDER_REMARK_ALL)`——**下单只写新值，反查认新旧两值**，防同日重启把改名前的未成交委托当「非本策略」→ risk_sold 漏项 → 重复卖出。
  4. **旧产物归档**：`build/strategy_p16_{g2,ens}_bridge.py` → `build/_deprecated_bridge/`（`git mv`，留证可查；`build/` 下只剩新产物，防人工粘贴拿错）。
- **⚠️ 关键陷阱（本次踩到并解决）**：`gen_ens_bridge_src.py` 是**由 G2 源码生成 ENS 源码**的生成器，其 `PREFIX_REPL` 原为 `[("P16G2","P16ENS")]`。若只改源码不改生成器，下次重生成会把命名**回退**。且前缀替换**顺序敏感**——必须先换长串 `P16G2I` 再换裸 `P16G2`，否则 `P16G2I桥` 会被裸规则先命中产生错位。已改为 `[("P16G2I","P16ENSI"), ("P16G2","P16ENS")]` 并把 `P16G2I` 加入防呆残留检查；实跑生成器验证产出 103 处 `P16ENSI`、零 `P16G2` 残留、参数正确（SIGNAL_HM=09:31 / TOP_N=5 / HOLD_DAYS=10）。
- **下游同步（7 处，全部改为「认新旧两前缀」而非只认新值）**：`qmt_bridge_client.py`（`_STRATEGY_LOG_TAG="P16G2I"` + `_STRATEGY_LOG_TAG_LEGACY` + `read_heart_from_log` 正则 `(?:P16G2I|P16G2|P16ENSI|P16ENS|P16V13)`）、`qmt_bridge_client_ens.py`（tag="P16ENSI"）、`check_bridge_heartbeat.py`（`LOG_TAGS` + `_tags` 双值）、`scripts/nightly_check.py`（`HEART_LOG_TAGS` + `_log_heart` 双值）、`qmt_account_metrics.py`（`REMARK_MAP` 新值排在旧值**之前**——子串匹配下 `P16G2I桥` 也含 `P16G2`，旧值在前会误判；`_RE_TAG` 加新前缀；`_LOG_TAG_NORM` 新旧都归 G2I/ENSI）、`premarket_g2_check.py`（产物路径）、两个历史审计脚本路径。**为什么必须双认**：改名前的历史日志/成交记录里仍是旧前缀，只认新值会让当日改名前的数据全部查不到。
- **验证**：**23/23 单测套件 rc=0**（含 build hardening 40/40、G2 状态机 100/100、ENS 100/100、V13 55/55、V13 fill 87/87、corr_diag_io 153/153）；`check_py36_compat` exit 0；两新产物 `check_build_bridge_artifact` 全 PASS；`premarket_g2_check._read_local_build_tag()` 正确读到 `20261020-232204`；**实机日志双前缀兼容实测通过**——`read_heart_from_log('20261020', tag='P16G2I')` 成功解析出**旧前缀** `[P16G2]` 的 14:59:36 心跳；`qmt_account_metrics._attr_by_remark` 对 `P16G2I桥`/`P16G2桥`/`P16ENSI桥`/`P16ENS桥` 四种输入均正确归属；夜间体检 FAIL=0、E2b 三策略全 PASS。
- **产物**：`build/strategy_p16_g2_selfcontained.py` BUILD_TAG=**20261020-232204**（4189 行/544644 B）、`build/strategy_p16_ens_selfcontained.py` BUILD_TAG=**20261020-232205**（4193 行/545162 B）。
- **⏳ 待人工**：①**新产物需重新粘贴部署**（QMT 客户端当前跑的仍是 `20261017-235317/235319` 旧名旧前缀版），粘贴后核对 BUILD_TAG 为 20261020-2322xx；②QMT 客户端策略编辑器里的**策略名**需一并改为新名（`STRATEGY_P16_G2_BRIDGE` → 建议 `P16_G2_SELFCLOSED`，ENS 同理），否则客户端侧仍是旧名，与仓库产物名不对应；③改名后首次实跑需核对日志出现 `[P16G2I][HEART]`/`[P16G2I][FILL]` 且 `passive=False`。
- **遗留**：未提交 git；`ablate_exits.py`/`backtest_b21_strategy.py` 等历史分析脚本的 docstring 仍提及旧名（属历史叙述，不影响运行，未改）。
- **关联**：T-20261020-003（本日体检，本条更正其「全天停跑」表述）、T-20261015-004（自闭环改造）、T-20261019-001（G2I/ENSI 统计标签首次引入）。

## 2026-10-21 · G2 内置版上线检查 + L3 快照幽灵持仓修复（T-20261021-001）

- **任务**：诚哥「粘贴了 strategy_p16_g2_selfcontained，ENS 先搁置（QMT 政策不明朗），先跑 G2 内置版看看，检查一下」。
- **✅ 新版部署核实（实机）**：`STRATEGY_P16_G2_SELFCONTAINED.py` 于 **21:36:30 加载成功**，`BUILD_TAG=20261020-232204`（与本地产物一致）、`mode=自闭环信号内产(零文件)`、`passive=False`、`positions=0`、`whitelist=3`；`continueRun` 回放 55189 根历史 bar 无异常。旧版 `STRATEGY_P16_G2_BRIDGE.py` 已于 **20:53:48 `stop success` + `python formula destructed`** 正常卸载，新旧未并存。日志前缀已切 `[P16G2I]`，旧前缀零残留。
- **✅ 心跳监控链路实测通**：`read_heart()` 正确解析 `[P16G2I][HEART]`（`source=formula_log`，21:36:30），`is_bridge_alive` → `True`；`nightly_check` E2/E2b 均 PASS（G2 21:36）。**改名后的双前缀兼容在真实新版日志上验证成立**。
- **✅ ENS 搁置确认**：`STRATEGY_P16_ENS_BRIDGE.py` 20:53:48 已 `destructed`，21:36 后零输出，符合诚哥「先搁置」。ENS 账本 `g2_ens_hold_dates.json` 仍有 4 只陈旧持仓（600691.SH/002841.SZ/002762.SZ/688758.SH，updated_at 10-15），**按指示未动**，恢复 ENS 前必须先清理。
- **🚨【P1 真缺陷，已修】L3 快照幽灵持仓 → 实际部署仅 32.7%**：新版首次加载时 `[P16G2I][INIT] 白名单(3只) ∩ 账户position 交集为空`，但 L3 快照 `HOLD_DATES = {'000607.SZ':'20260929', '000960.SZ':'20261016'}` 仍带两只**幽灵持仓**。**危害链**：信号逻辑 `kept = set(_g_hold_dates) - sold`，而卖出循环只遍历 `acct`（账户实际持仓，当前为空）→ **幽灵票永远卖不掉、永远留在 kept** → `n_slots = SIGNAL_TOP_N(3) - len(kept)(2) = 1` → 只能买 1 只，**实际部署 32.7% 而非 98%**。若未发现，明日 09:50 信号会以 1/3 仓位建仓，且 `000960.SZ` 还被算作「持有中」参与超配减仓判定。
- **根因（两层）**：①`g2_hold_dates.json` 停在 **10-16 09:52**，从未随实际持仓收敛；②`000607.SZ`/`000960.SZ` 在 `[HOLD]` 日志里**从未出现在实际持仓**（10-19~10-21 实际只有 605098.SH，10-21 13:10 止损卖出后清空）→ **陈旧账本条目污染**。**⚠️ 更正（2026-10-21 22:xx 复核）**：本条初稿称这两只是「退市票（10-01 起无行情）」**有误**——当时误用了 `D:/astock` 主库（该库最新只到 **2026-09-30**，所有票都停在 9-30，不能用于判退市）；查 `data_live/merged_daily_full`（最新 10-21）**四只票（000607/000960/301202/605098）均有正常行情到 10-21**，均未退市。`D:/QMT_POOL/delist_info.csv` 也不是退市名单——它是全市场 5922 只的清单（list_status 全空）。**清空 hold_dates 的处置仍然正确**（这两只确实不在实际持仓），但归因改为「账本未跟随实际持仓」，非「退市」。
- **修复（3 步）**：①备份 `g2_hold_dates.json.bak_stale_20261021`，清空 `hold_dates={}` 并写 note 说明来龙去脉（账户 10-21 13:10 止损卖出 605098.SH 后实际空仓）；②重跑 `gen_g2_signal_snapshots.py --arm g2` → `hold_dates=0`、`merged last=2026-10-21`、`capital=95246`、`SNAP_N=4863`；③重建产物 **BUILD_TAG=20261021-214054**（4188 行/544587 B），校验 `SNAPSHOT_DATE=20261021`、`HOLD_DATES` 空、`CAPITAL_SNAP=95245.82`、新前缀 102 处/旧前缀 0、`ORDER_REMARK=P16G2I桥`。**干跑验证**：`kept=∅ → n_slots=3 → 可部署 98.0%`（修复前 32.7%）。
- **✅ 旧版今日实际战绩（供对照）**：10-21 旧版 `STRATEGY_P16_G2_BRIDGE.py`（BUILD 20261017-235317）运行正常——11:25 加载、`SIG-EXPIRED`（过 09:58 信号点，未换仓）、**13:10:18 触发 `[P16G2][RISK] SELL_STOP` 卖出 605098.SH 500股@52.45（跌破止损位 52.46）并 `[FILLED] deal=500`**。内置风控在岗有效。
- **验证**：23/23 单测套件 rc=0；`check_py36_compat` exit 0；`check_build_bridge_artifact` 全 PASS；`nightly_check` FAIL=0。
- **⏳ 待人工（关键）**：①**新产物 BUILD_TAG=20261021-214054 需重新粘贴部署**（当前运行的是 20261020-232204，快照仍是 10-16 幽灵持仓版）；②粘贴后确认日志 `[P16G2I][INIT] L3快照 date=20261021` + `白名单合并 ... = 3 只`（不再有「交集为空」WARN）+ `positions=0`；③QMT 客户端策略名建议同步改为 `P16_G2_SELFCLOSED`。
- **⚠️ 遗留风险（未修，需拍板）**：`G2_CODES = ("605098.SH", "000607.SZ")` 里 **`000607.SZ` 是退市票**，硬编码在白名单里会一直占一个名额（不影响下单，因候选池来自行情宇宙不会选中它，但 `whitelist=3` 显示虚高）。建议下次重建时改为 `("605098.SH",)` 或换成当前实际持仓。
- **关联**：T-20261020-005（改名）、T-20261020-003（心跳监控层对齐）、T-20261015-004（自闭环改造）。

## 2026-10-21 晚 · 301202 纳管 G2（T-20261021-001 续，诚哥「真实持仓是 301202 1000股，其他都清空了」）

- **触发**：诚哥告知真实持仓 = **301202.SZ 1000 股**，其余已清空。核查发现该票**不在 `G2_CODES` 白名单**（原为 `("605098.SH","000607.SZ")`）→ 按 `_rebuild_state` 的「白名单 ∩ 账户position = 纳管集」逻辑，**301202 属白名单外持仓，G2 绝不触碰 → 零风控覆盖（止损/止盈/追盈全部不触发）**。这是资金安全红线级缺口。
- **持仓事实（QMT 客户端日志实证，xtquant 外部直连 CONNECT_RC=-1 不可用）**：从 `D:\QMT交易端模拟\userdata\log\XtClient_2026102*.log` 的 `[LOGIN] initPosition` 行解析：
  - `stockCode:301202, totalAmt:1000, enableAmount:1000, costPrice:29.4512, costBalance:29431`；
  - 10-20 08:52 lastPrice=30.10（浮盈 648.45）、10-21 11:25 lastPrice=31.45（浮盈 1997.77）、10-21 20:53 lastPrice=31.08（浮盈 1627.96）。
  - **建仓日推断 = 20261019**：成本 29.4512 落在 10-19 当日区间 [29.19, 30.40] 内（10-20 最低 29.49 已高于成本，10-16 最高 31.02/最低 29.55 区间不含该价但 10-19 唯一精确命中）。
- **⚠️ 重要更正（推翻本日早前判断）**：此前把 `000607.SZ`/`000960.SZ` 判为「退市票（10-01 起无行情）」**是错的**，根因是误用了 `D:/astock` 主库——该库最新只到 **2026-09-30**（所有票都停在 9-30），**不能用来判退市**；且 `D:/QMT_POOL/delist_info.csv` 也不是退市名单，是全市场 5922 只的清单（`list_status` 全空）。改查 `data_live/merged_daily_full`（最新 **10-21**）确认：**000607 / 000960 / 301202 / 605098 四只全部有正常行情到 10-21，均未退市**。清空旧 `hold_dates` 的处置本身正确（那两只确实不在实际持仓），但归因应为「账本未跟随实际持仓」而非「退市」。
- **修复（3 处）**：
  1. `g2_hold_dates.json`：`{"301202.SZ": "20261019"}`（移除 000607/000960），note 留痕建仓日推断依据 + 605098 已于 10-21 13:10 止损卖出；
  2. 源码 `G2_CODES = ("301202.SZ",)`（原 `("605098.SH","000607.SZ")`，三只全已不在持仓），加注释说明「白名单外持仓 = 零风控覆盖，持仓变动后必须同步此常量 + 重建产物」；
  3. 重跑快照（`hold_dates=1`、`last=2026-10-21`、`capital=95246`）+ 重建产物 **BUILD_TAG=20261021-224356**（4191 行/544907 B）。
- **仓位分配干跑（纳入后）**：`kept={301202.SZ}` → `n_slots = 3-1 = 2`；目标每仓 31114 元；301202 当前 1000 股 × 31.08 = 31080 元 ≈ 目标（diff=0，**不触发超配减仓**）；总部署 = 31080 + 2×31114 = 93307 = **98.0%**（符合设计）。301202 `is_st=0`（非 ST），满 15 日约 **2026-11-07** 到期卖出。
- **验证**：23/23 单测套件 rc=0；`check_py36_compat` exit 0；`check_build_bridge_artifact` 全 PASS；产物常量核对 `G2_CODES=("301202.SZ",)` / `HOLD_DATES={"301202.SZ":"20261019"}` / `SNAPSHOT_DATE=20261021` / `CAPITAL_SNAP=95245.82` / 新前缀 102 处旧前缀 0。
- **⏳ 待人工（关键）**：**BUILD_TAG=20261021-224356 需重新粘贴部署**。当前运行的是 20261020-232204（白名单还是 `605098.SH/000607.SZ`、L3 为空）→ **不粘贴则 301202 仍无止损保护**。粘贴后核对：`[P16G2I][INIT] L3快照 date=20261021` + `白名单合并 ... = 1 只` + `白名单∩position 纳管 1/1 只`（不应再出现「交集为空」WARN）+ `[P16G2I][HOLD] n=1 301202.SZ=1000@29.451`。
- **遗留**：①ENS 仍搁置，其账本 `g2_ens_hold_dates.json` 仍有 4 只陈旧持仓（600691/002841/002762/688758），恢复 ENS 前必须先清理；②`data_live/merged_daily_full` 已到 10-21 但 `D:/astock` 主库仍停 9-30，两者口径差异需在下次数据更新时对齐（回测/研究用主库会缺 10 月行情）；③未提交 git。
- **关联**：T-20261021-001（本日前半段：G2 上线检查 + 幽灵持仓修复，本条更正其退市误判）、T-20261020-005（改名）、T-20261015-004（自闭环改造）。

## 2026-10-25 · G2 持有期 N15 vs N10 独立审计与拍板（T-20261025-001）

- **任务**：复核 2026-10-08 B1 的“G2 N15→N10”建议，判断当前是否应改参；只读审计，未改 `g2_config.py`、QMT 配置、资金池、账本、换仓或下单逻辑。
- **拍板结果（A 方案，2026-10-25）**：**维持 `HOLD_DAYS=15`，不执行 B1；B2 止损后复入继续不推进。** 准确含义是“没有合格证据支持切换到 N10”，不是“N15 已被证明优于 N10”。已拍板，生产参数未改（原值即 N15）。
- **P0 数据边界**：`paper_forward_live.csv` **不是生产纸面线**。原始 238 行、按 `(date, code)` 去重后 234 行，`hold={10:222, NaN:12}`；9 月多日为 Top10 全候选（11~16 只），首个 `top10` 文件为 09-02。该文件只能作历史候选诊断，不能代表生产 TOP2/TOP3 组合路径。
- **生产候选复算**：按 `<date>_g2_top10.csv` → `total_new>=60` → `total_new` 降序 → 09-21 及以前 TOP2、09-22 起 TOP3。全窗 57 笔/28 个信号日，N15−N10 原始毛收益 `-0.692pp / t=-0.62`、市场调整 `+0.814pp / t=+0.97`；严格 top10 33 笔/16 个信号日，原始 `-1.825pp / t=-1.51`、市场调整 `+0.611pp / t=+0.61`。方向不稳健且统计不显著。
- **B1 核心证据失效**：10-08 公共窗 `N10−N8` 原报告逐笔 `t=4.76`（独立复算 4.72）；信号日聚合降为 `+0.795pp / t=+0.97`，HAC5 `t=+1.16`，MBB L5/L10 区间均跨 0。应表述为“原 B1 核心逐笔显著性失效”，不写成“B1 自始无任何合格证据”。
- **5 年对照边界**：11 个半年段仅作描述性路径对照，非干净 OOS；N10 胜 5/N15 胜 6，全段配对 `t=-0.83`；train `+2.58pp/t=+0.27`、valid `-2.28pp`（仅 2 段，未检验）、test `-13.04pp/t=-1.15`、test 剔 2026H1 `-1.75pp/t=-1.10`。末段实际只到 2026-08-14，6/11 段属于 train/valid。
- **参数选择风险**：N15 是历史网格峰点（N10/N15/N20 = `+0.149%/+0.213%/+0.027%`，N20 断崖），且历史测试期 100% 落在 live 模型训练窗内；test 又被模型版本、参数和锚点选择反复使用，不能把本轮 5 年表当作独立确认。
- **方法论固化**：参数裁决只使用生产 TOP_N 候选路径；主统计单位为信号日；原始毛收益与市场调整双列；同时报告 HAC5 与 MBB L5/L10/L15，并预先固定主块长；达到约定独立信号日数前只观察，不得自动改参。旧“连续两月日级 t<-2 自动解锁”作废。
- **产物**：`results/G2持有期N15vsN10_二次复算_20261025.md`、`..._二次审计_20261025.md`、`..._综合审计_20261025.md`、`..._裁决_20261025.md`；首次审计 `..._审计_20261025.md` 保留不覆盖；脚本 `research/g2_hold_n10n15_second_audit_20261025.py`。
- **数据边界**：全部结论单源 astock，未经 gpsj 备用源交叉验证。**关联**：T-20261008 B1、`P16纸面持有期与止损诊断_20261008.md`、`策略优化审计_20260910.md`、全局控制台 T-20261025-001。

## 2026-10-27 · G2 迁号快照故障修复 + 沙箱 v4.7 数据面探针（T-20261027-001 / T-20261026-001）

- **故障**：G2 内置信号内产版自 10-23 起每天 init 抛 `RuntimeError: 快照账号戳 70180771 != 策略 67014907` → fail-safe 转被动（`passive=True whitelist=0 positions=0`），10-23/26/27 三个交易日整策略不评估不下单；两台客户端同病。
- **根因**：10-22 拍板 g2_bridge 由 70180771 迁 67014907；10-23 重建产物时**只改 `ACCOUNT_ID`，没重生成 L3 期初账本快照**，产物内 `ACCOUNT_STAMP` 仍是 70180771。`init()` 的戳校验按设计 fail-safe。
- **修复**：`gen_g2_signal_snapshots.py --arm g2` → `build_p16_g2_selfcontained.py`（禁止手改中间产物）。新产物 `BUILD_TAG=20261027-132907`，549,296 B / 4,315 行；`SNAPSHOT_DATE=20261023`（= merged 数据真实最后交易日）、`ACCOUNT_STAMP=67014907`、`HOLD_DATES={}`、`OPPONENT_HOLDS=()`、`CAPITAL_SNAP=93501.0`、`SNAP_N=4863`；blob 解码 133,825 B 精确消费（4863×27+4+280×9），4863 键无重复。
- **教训①（迁号三件套）**：账号迁移必须同时改「产物 `ACCOUNT_ID`」「重生成快照（`ACCOUNT_STAMP` 随 `ACCOUNT_ID`）」「账本账号戳」，少一件就把策略打成被动；构建期建议加 `ACCOUNT_STAMP == ACCOUNT_ID` 硬断言（**未加，待拍板**）。
- **教训②（幽灵持仓）**：旧快照 `HOLD_DATES={"301202.SZ":"20261019"}` + `OPPONENT_HOLDS` 4 只是 70180771 时代的幽灵持仓——**即便戳改对也会错**（少 1 个买入位 + 误触发重叠规避）。迁号后必须重生成，不能只改头部常量。
- **教训③（双端重复下单）**：同一份产物同时粘在 `D:\QMT交易端模拟` 与 `D:\国金QMT交易端模拟`，两台都连 67014907。修复后**只粘国金一台**，另一台先卸，否则同信号下两次单。
- **v4.7 数据面探针（2026-10-27 14:11 已实跑定稿）**：`broker/qmt_sandbox_probe_v47_data.py`（PROBE_TAG 20261027-04）在国金客户端跑完（161 条 `[P47]` / 160 call），比对报告 `results/QMT_v47数据面对齐_20261027.md`，人工判定在研究报告 §10.10。**结论**：①可用——`get_market_data_ex`（none/front_ratio 均可，**日期到 20261027，比本地 astock 新 2 个交易日**；volume=手/amount=元）、`get_stock_list_in_sector`（8/8，只给成分无行业）；②**股本不可信**——`get_instrument_detail`/`get_float_caps`/`get_total_share` 同源，20 只中仅 2 只（600036/600519）在 1% 内、**18 只 FloatVolume 偏差 1.6%~100%**（2 只返 0），`get_total_share` 是 A 股股本不含 H 股，**`get_float_caps` 是流通股本(股)不是市值**；③不可用——`get_finance`（C context 无此方法）、`get_turnover_rate`（空表）/`get_turn_over_rate`（nan）；④可用但无数据——`get_financial_data` 7 字段全 `438×1` 全 nan → 判「本地财务数据包未下载」；⑤复权未判定（窗口内无除权票）。**推论**：`float_share` 污染 `turn_ma5`/`turnover_rank`/`log_mv` → 维持 `SIGNAL_ENABLED=False` 的第二条实证理由。
- **数据断流根因（2026-10-27，顺带查明）**：本地 `incremental_daily`/`merged_daily_full` 停在 10-23 **不是上游无数据**，是外部 xtquant 行情服务连不上——10-26 16:30 的 `xtdata_update.py` 先卡 3h24m，`download_history_data` **0/5854**，`get_market_data_ex` 抛 `无法连接行情服务！`，但上层只打「无增量更新，跳过 merge/deploy」并 **exit 0**（静默）。同日 QMT **内置**上下文能取到 10-26/10-27 行情。**恢复前 G2 候选一律视为 10-23 陈旧，不得据此开仓**。详见全局控制台 T-20261027-003。
- **待拍板**：①资金基线 93,501（承接 realized -6,499）vs 100,000；②是否加构建硬断言；③保留哪台客户端（建议国金）；④T4 红线修订条款草案（`specs/AGENTS红线修订条款草案_20261027.md`）。
- **关联**：全局控制台 T-20261027-001 / T-20261026-001；`specs/QMT沙箱能力全量探针研究报告_20261026.md` §十 §十一；repowiki P16 卡「特殊配置与命令」。

## 2026-10-27 · 21f 纸面影子臂上线（T-20261027-004）

- **目的**：给「21f（内置 B21）vs 43f（生产模型）」补前向证据。此前只有回测，且策略级结论随 tier 口径（t0_close / t1_open）反转。
- **实现**：`paper_shadow_b21.py` —— 特征复用 `vendor/g2_features_qmt.assemble()`（与 QMT 内置引擎同一份代码），模型复用 `vendor/g2_tree_model_B21_k1500.py`；池 = universe(enabled) ∩ is_st==0 ∩ 剔 688/4/8；prob≥0.60 → top10 → 取 3；T+1 开盘买入（一字板跳过、低开守卫 ≤-5% 跳、(-5%,-3%] 需量比≥3.3）；收益 T+1 开盘 → +5/10/15 日开盘（毛）。
- **只读**：不碰交易/账本/plan/state；产物只有 `data/real/paper_shadow_b21_{picks,days}.csv`、`paper_shadow_b21_<date>.md`、`paper_shadow_b21.log`。
- **首次回填（asof=20261023）**：99 笔 / 41 个活跃日 + 3 个空日。ret5 +0.39%/57%(n=63)、ret10 +1.52%/63%(n=52)、ret15 +1.93%/60%(n=42)。同引擎对照 43f live 臂：ret15 +2.33%(n=37) vs +2.17%(n=26)，**名字重合 0/66**（两臂买完全不同的票）。
- **教训①（is_st 缺值口径）**：2026-09-14/18/24 `is_st` 全表缺值、09-23 缺 1290 行。训练侧 `build_features_v2.py` 用 `df[df["is_st"]==0]`，NaN 比较为 False → **直接丢弃**。影子必须照做（否则过滤帧多出上千只、横截面 rank 变化 → prob 整体漂移，实测 top prob 0.8288→0.8491、过线数 389→752）。`--st-ffill` 只作研究对照开关。
- **教训②（forward 语义）**：`source=forward` 只在「信号日 == 运行日」时记；数据陈旧补跑一律记 backfill，不冒充前向。当前本地行情停在 10-23 → forward 0 笔。
- **教训③（tier 数据源）**：`D:/astock/index/index_daily/000300.SH.parquet` 止于 20260930，之后 tier 记 -1（不静默当 T1）。要补需另找指数增量源。
- **调度**：`paper_forward_daily.ps1` 第 8 步（16:55），失败只写 `SHADOW-ALERT` 不阻断主链。
- **裁决门槛**：累计 60 个独立信号日再谈显著性；且影子与 live 同时变模型与信号层，不能把差异全归因于特征数。
- **关联**：全局控制台 T-20261027-004；repowiki P16 卡「特殊配置与命令」；报告 `data/real/paper_shadow_b21_20261027.md`。

## 2026-10-28 · QMT 零介入部署（T-20261028-001，推翻「必须人工粘贴」旧结论）

- **痛点**：诚哥「代码生成了，QMT 内还得我手动粘贴进去再执行，你再读日志，再调试，我再粘贴」——要求全程零介入。
- **机制实证（本案最重要的一条）**：客户端启动时按**固定路径** `<QMT>\python\<模型名>.py` 读文件执行（日志 `reqid = 0<path>_<id>`）；**明文 GBK .py 可直接加载**，客户端**不回写加密**（md5 全周期一致，含 `Stop-Process -Force` 强杀后仍一致）。⇒ 2026-09-10「复制到 python\ 无效、必须粘贴」的结论**只对「随便放个文件名」成立**，对**已注册模型名覆盖文件**不成立。
- **硬限制**：模型名必须已存在于客户端二进制 `indexConfig/configFormula`。只写 `config/indexUserConfig.xml` + `indexConfig/formulainitstepsinof` **不够**（客户端打 `configFormula not exist, continue ... utfName:X` 后静默跳过，只剩 `auto run timeout`、零 Python 输出）→ **每上新一个策略名，仍需 UI 建一次模型空壳，之后就永久免粘贴**。
- **两个坑**：①启动必须带 `reboot` 参数，否则停在 `Proxy client not logged`，一个策略都不跑；②`auto run timeout` / `loadFile [X] parse error` 是每次重启都有的良性噪声，**唯一判据是日志出现 `BUILD_TAG=<期望值>`**。
- **沙箱边界**：`Foribdden FileIO` 拦的是**运行中的策略**读写文件，**不拦客户端自己的加载器**读 `python\*.py`——两条道要分清。
- **工具**：`projects/Project_16_LightGBM股票大师/qmt_autodeploy.py`（校验 GBK/无 BOM/首行/含 BUILD_TAG → 备份到项目 `_deploy_bak/`（已 gitignore）→ 写入 → 优雅关窗 12s 后强杀 → `reboot` 重启 → 从日志**当前偏移**轮询 BUILD_TAG → PASS/FAIL）。关闭只按 `ExecutablePath` 前缀命中 `--qmt-dir`，**绝不误伤 `D:\国金QMT交易端模拟`**。退出码 0/2/3/4/5/6。
- **端到端 PASS（15:50）**：`[DEPLOY] [OK] PASS: 客户端已加载 STRATEGY_P16_G2_SELFCONTAINED，BUILD_TAG=20261027-132907`，运行日志 `passive=True`（70180771 上账号戳不符 + 白名单空，双被动 → 零委托）。
- **⚠️ 事故教训（写进 AGENTS）**：回滚期间误删了 70180771 上那份加密原始产物（734,366 B / build 20261023-105529）及其备份，用仓库 build 产物（549,296 B / 20261027-132907）补回=**恢复即升级**。硬规则：**覆盖 `python\*.py` 前必须备份到客户端目录之外，覆盖后绝不删除备份**。
- **未做**：生产端 `D:\国金QMT交易端模拟`（67014907 实盘执行端）未接入，仍跑手工粘贴的 10-27 版；接入需先拍板该客户端能否按同一节奏重启。
- **关联**：全局控制台 T-20261028-001；`specs/QMT零介入部署方案_20261028.md`；commit `1bd33f6`；repowiki P16 卡「特殊配置与命令」与部署运维文档。

## 2026-10-28 · 夜间全自动闭环上线（T-20261028-002，零介入闭环收尾）

- **背景**：T-20261028-001 只解决"部署"这一环，构建/重启/回读仍是人按序列跑；诚哥「动手干吧」授权把整条链脚本化并挂进 21:10 夜间窗口。
- **组成**：①`qmt_nightly.py`（`scan` / `loop` 子命令：调 build → 调 qmt_autodeploy → 双客户端日志对照体检 → 落 md/json 报告 + 退出码）；②`qmt_nightly_deploy.ps1`（UTF-8 BOM，控制台编码统一，60 天报告清理）；③计划任务 `Quant_P16_QMT_NightlyDeploy`（每日 21:10，**S4U**，`New-ScheduledTaskAction` 对象注册，回读 `Actions.Arguments` 中文路径正确）；④报告落 `results/qmt_nightly/YYYYmmdd-HHMMSS.md|json`（已 gitignore）。
- **时间窗选择**：20:54 客户端自重启之后、21:20 夸克备份之前；避开 20:05 G2Candidates / 20:10-20:30 体检 / 20:45 fill-vs-open。
- **三个真 bug（都只在「走 ps1」时暴露，单独跑 python 全绿）**：
  1. **`start_client()` 假失败**：单实例交接「先起一个→自查已存在→自杀→再起」导致 90s 内 PID 抖动；改为 90s 轮询 + 连续稳定 8s + `exclude` 排除刚杀的 PID 才算稳定。
  2. **`ps_run()` 编码错配（最隐蔽）**：ps1 为写日志把 `[Console]::OutputEncoding` 设为 UTF-8，**同控制台**产出的子 PowerShell 也跟着吐 UTF-8，而 python 侧一律按 GBK 解 → 中文路径 `D:\QMT交易端模拟` 解成乱码 → `ExecutablePath.startswith(qmt_dir)` 失败 → `list_client_pids()` 返回空（杀不到也认不到，报「全程未见进程」而客户端其实起来了，且旧进程根本没被关）。修法：子进程显式 `[Console]::OutputEncoding=UTF8` + 解码 utf-8→gbk 双兜底。**教训：`[Console]::OutputEncoding` 是控制台级状态，会沿进程树继承——"单独跑过没问题"不能作为这类问题的验收依据。**
  3. **ps1 结论回显乱码**：编码还原写在第二次 `& $py` 之前 → `latest.json -> verdict=FAIL（重删除失败）`。修法：还原移到**所有** python 调用之后。
- **演练语义修正**：`-Rehearsal` 改用 `--deploy-dry-run`（只校验/构建），**不能用 `--no-restart`**——后者会把新产物写进客户端 `python\` 却不重启加载，制造"盘上 TAG ≠ 运行中代码"的失真态。
- **验证证据**：手工跑 `PS_EXIT=0` / `BUILD_TAG=20261028-161624`；S4U 会话 0 跑 `LastTaskResult=0` / `BUILD_TAG=20261028-161755`；同期体检 `P16_V13_RISK 20261023-110053`(57 条) + `STRATEGY_P16_G2_SELFCONTAINED 20261027-132907`(246 条) 双 passive=True、0/0 被动记录 → **生产端 `D:\国金QMT交易端模拟` 全程零触碰**。
- **边界（未放宽）**：只重启 `--qmt-dir` 指定客户端（按 ExecutablePath 前缀过滤 PID）；09:00-15:05 时段闸拒绝重启；生产端只读扫描；构建失败不部署。
- **风险留痕**：桌面存在 `XtItClient_x64_国金证券QMT模拟_2.1.33.0.exe` 安装包（10-28 16:09 曾见安装窗口），在 `qmt_dir` 之外故 kill 不会碰它，但若被执行会替换客户端二进制 → 夜间闭环会对着新版本跑，需人确认。
- **遗留**：①生产端（67014907）接入部署待拍板；~~②一次只能部署一个模型（G2 与 ENS 需两次运行）~~ → **已关闭，见下条拍板**；③`--verify-only` 只体检不部署模式未做。
- **✅ 拍板（2026-10-28 诚哥原话「Ens 搁置，不部署」）**：夜间闭环**固定只部署 `STRATEGY_P16_G2_SELFCONTAINED`**，不加 ENS/融合部署腿，不新增第二模型注册位。承接 10-24「先搁置」，这次把"优化冻结"升级为**"部署也排除"**——ENS 既不跑也不上。**重启前置（未变）**：`g2_ens_hold_dates.json` 仍留 4 只陈旧持仓（600691.SH/002841.SZ/002762.SZ/688758.SH，updated_at 10-15），恢复 ENS 前必须先清理，否则 G2 侧重叠规避会误跳过候选票。**注意**：`config/capital_allocation.yaml` 里 `p16_ensemble_bridge` 的 10 万额度**仍占着**（本次未动分配表——拍板只说"不部署"，没说退额度；要退需单独拍板并跑 `check_capital_allocation.py`）。
- **关联**：全局控制台 T-20261028-002；`specs/QMT零介入部署方案_20261028.md` §九；repowiki P16 卡「特殊配置与命令」与部署运维文档。

## 2026-10-28 18:30-19:00 · ❌ 生产端（67014907）强制重启失败事故——客户端卡死需重启 Windows

- **动机**：诚哥「1 吧，这是最新版的 Qmt」= 把零介入部署的目标从沙箱客户端 `D:\QMT交易端模拟`（70180771）切到生产客户端 `D:\国金QMT交易端模拟`（67014907，2.1.33.0）。理由是生产端账号戳与产物一致，部署才有交易意义。
- **动作**：`qmt_autodeploy.py --product build/strategy_p16_g2_selfcontained.py --model STRATEGY_P16_G2_SELFCONTAINED --qmt-dir "D:\国金QMT交易端模拟"`。产物校验/备份/落盘全部 PASS（备份 727,054 B 加密原件 → 项目 `_deploy_bak/STRATEGY_P16_G2_SELFCONTAINED.20261028-183142.py`；另有 `D:\QMT_upgrade_bak_20261028-1840\` 双份）。**这一步的备份是有效的，加密原件未丢**。
- **失败经过**：停 10136（CloseMainWindow 12s 超时→强杀）→ 起 `XtItClient.exe reboot` → 启动器 24648 **在 libcef.dll 崩溃**（`0x80000003`，`debug.log: Invalid file descriptor to ICU data received`）→ 子进程 5880 完成登录（`trade server login success` 18:32:06）后**卡在 `doInitPersist` + `initExtraExpression` FATAL 之后**，不再写日志、不出主窗口、不 AutoRunStrategy → 变 0 线程无窗口进程，`Stop-Process`/`taskkill /F` 一律 `Access is denied`。
- **排查过并排除的**：（a）明文产物不是原因——把加密原件 md5 `9fcf1c19…` 还原回去仍然起不来；（b）不是单实例/另一台客户端占用——杀掉 70180771 客户端后依然失败；（c）不是网络——56001/65000 均通；（d）不是配置损坏——`config/user/root/{UiSettingConfig,configFormula,lua}` 完好（16:08）；（e）不是 CEF 缓存——改名 `data/Cefcache` 无改善；（f）`config/token` 0 字节是常态（另两台同样 0 字节）。
- **失败后的所有启动方式都无效**：桌面会话直起（无声退出 `-1`）、WMI `Win32_Process.Create`、S4U 计划任务（session 0 能登录但 `Proxy client not logged` 后不跑策略）、Interactive 计划任务——**2.1.33.0 的策略自动运行依赖主窗口，无窗口就不 AutoRunStrategy**。
- **恢复手段**：只能重启 Windows 清僵尸。已注册 `QMT_Prod_AutoStart`（AtLogOn +45s，Interactive，`XtItClient.exe reboot`）在登录后自动拉起。**代价：本机未开 autologon，重启后必须人工登录一次 Windows**。
- **★★ 硬教训（写进 AGENTS/记忆）**：
  1. **`D:\国金QMT交易端模拟`（2.1.33.0）禁止被 qmt_autodeploy 强杀重启**。2.1.19.200（沙箱那台）强杀后能干净重起，2.1.33.0 会连带 CEF/UI 初始化一起卡死，现场只能重启 OS。
  2. **明文落盘在 2.1.33.0 上至今未验证成功**（每次都没走到 loadFile 就卡死）→ 不能声称「生产端也能明文部署」。
  3. **夜间闭环（21:10）继续只打 `D:\QMT交易端模拟`（70180771）**，生产端接入必须另设「优雅重启」路径（让客户端自己按 20:54/08:52 自重启节奏加载，而不是我们杀它）。
  4. 生产端恢复前：G2/V13 都在 passive（零委托），本次事故的**交易影响为零**，但监控断档。
- **事故时的现场事实（留档）**：生产 G2 = 加密 `BUILD_TAG=20261027-132907`（已还原）、`passive=True whitelist=0`；V13 = `P16_V13_RISK` 84138 B（10-23 11:09）全天 `[P16V13][PASSIVE] 白名单为空`。即**两台客户端今天本来就都没在下单**。
