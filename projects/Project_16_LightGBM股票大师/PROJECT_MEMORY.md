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

### 评分卡参数寻优（2026-08-28，T-20260828-004）

* **背景**：用户质疑 F1-F6 权重与 F2/F5/F6 阶梯阈值"拍脑门定"。核查确认：LightGBM 超参（train\_optuna.py Optuna）、红线 58→60/N=5/10、止损止盈均做过寻优，但 **SC\_WEIGHTS 与阶梯阈值从未寻优**（继承 Project\_15 手工值）。

* **产物**：`optimize_scorecard.py`（复用 scan\_rotate\_cost\_real 可执行引擎，walk-forward IS 2024-07~~2025-12 / OOS 2026-01~~08，固定红线58/TOP2/N10/滑点0.1%）+ `data/real/scorecard_optim_report.md`。

* **结论（稳健性检查为准）**：①权重寻优**稳健有效**（IS 前10 在 OOS 10/10 超基线，均值 +0.18% 日超额 vs 基线 +0.05%）；②F5/F6 阈值寻优**稳健有效**（10/10）；③**F2 资金阶梯阈值寻优是负优化（OOS 0/10 超基线）→ 生产默认 F2 阈值保持不动**。生产默认权重 F1=0.25 偏优 F1/F4、压制 F5 确实不优。

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

