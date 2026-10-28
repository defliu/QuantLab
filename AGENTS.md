# QuantLab Agent 开发指南

本文件适用于 `D:\QuantLab` 下的全部文件。参与本项目开发时，必须优先遵守以下规则。

## 项目定位

- 本项目是 A 股量化研究与实盘框架，覆盖因子研究、回测验证、风控、miniQMT 实盘交易的完整链路。
- `data/` 是数据读取层，采用鸭子类型接口设计（astock parquet / DuckDB 双引擎），财务数据必须 PIT 安全。
- `factors/` 是因子库，因子基类提供 winsorize/zscore/rank_normalize 预处理，引擎负责注册、计算与 IC 评估。
- `strategy/` 是策略库（当前策略逻辑在各项目目录下），未来统一收归此处。
- `broker/` 是券商接口层，含 QMT 单文件策略生成器（`qmt_builder.py`），桥接模块化研究代码与 QMT 单文件需求。
- `projects/` 是策略项目隔离目录，每个策略独立回测、独立配置、独立结果。
- `specs/` 是需求与设计文档目录，可作为需求、设计和验收依据。
- 实盘策略工程资产已收拢至本仓库（评分买入 + 分层卖出风控 + QMT 单文件构建）；`Project_01\research\multi_factor_ic\` 是其 vendored 副本（自包含），QMT 生产部署位为各项目 `build/` 子目录（如 `Project_10\build\strategy_v2.py`、`Project_01\build\strategy_mfic.py`）。

## 知识中心（Knowledge Center）

> 接手本项目或开展任何新任务前，**先查知识中心**：`.qoder/repowiki/` 是本项目的知识沉淀，任何人都可以通过它快速建立全局认知，再进入代码细节。

- **位置**：`.qoder/repowiki/`（Qoder repowiki 自动生成与维护，内容随代码演化）
- **入口**：先读 `zh/content/知识库导航.md`（全库地图），再读 `zh/content/快速开始.md`
- **知识卡**：`knowledge/zh/` 为模块级知识卡（每张固定五篇：概述 / 架构设计 / 技术栈 / 编码规范 / 特殊配置与命令），索引为 `knowledge/zh/_index.yaml`。已覆盖：根项目、数据读取层、因子库与因子引擎、回测引擎、QMT Broker 适配器、策略库、配置集、MCP 服务进程、记忆存储、因子研究与策略审计脚本集，以及 **风控叠加层 `risk/`、LightGBM 模型资产库 `models/`、需求与设计规格 `specs/`、回测框架验证套件 `projects/verification/`、候选策略研究 `research_study/`、Project_04（已淘汰）**；策略项目集 `projects/` 下 Project_01~18 与 ATR 低波各有独立卡
- **内容文档**：`zh/content/` 为体系化文档，含快速开始、术语表、项目概述与系统架构设计（数据层 / 因子框架）、**API 参考（数据层 / 因子与回测 / 配置与券商接口）**、回测引擎核心、**因子研究系统**、实盘交易系统、**风控与资金管理**、**数据源与基础设施**、策略系统、验证与测试、部署与运维、**项目管理**、**最佳实践与案例**
- **使用方式**：人工可直接阅读 Markdown；AI agent 可通过知识检索能力按主题检索，或直接读 `知识库导航.md` + `快速开始.md` 起步
- **维护**：新增模块时在 `knowledge/zh/` 建目录（五张卡 + `_module.yaml`）并在 `_index.yaml` 登记；新增主题文档放 `zh/content/<主题>/` 并在 `知识库导航.md` 登记
- **优先级**：本文件（AGENTS.md）与代码是权威源；知识中心若与代码/本文件冲突，以代码和本文件为准，并更新知识中心或标注差异
- **维护**：开发中如需补充知识，直接在对应模块知识卡或内容文档下编辑；索引文件由 repowiki 自动导出
- **知识库同步收尾（硬规则，2026-09-10 立）**：任务收尾时若涉及 **新模块 / 新定时任务 / 新机制（门禁/回退/部署方式）/ 路径或环境变更 / 策略状态变更**，必须同步更新 `.qoder/repowiki/` 对应知识卡或内容文档（与任务看板硬规则同等地位，不更新=工作没做完）。注意：repowiki 只自动导出 `_index.yaml` 索引，**知识卡正文不会自动沉淀，只能靠收尾时人工写入**——只记 PROJECT_MEMORY 不记 repowiki 会造成「记忆层热、知识库冷」断层（2026-09-10 实测：知识库停在 09-06，四天产出全缺失，含会误导实盘操作的旧机制描述）。
- **全局研究视图**：`研究总览与路线图.md` 一眼看清"以前研究过什么 / 踩过什么坑 / 现在研究什么 / 未来研究什么"；旧项目史料在 `archive/legacy_qmt_strategies/`。
- **未完工作看板（硬规则，2026-08-09 立）**：`全局控制台.md`「三、当前任务看板」是唯一任务看板（TODO/DOING/DONE/陈旧待裁决）。**开工前先读它确认有哪些未完工作；任务新建/完成/阻塞/拍板必须当场更新它，不更新=工作没做完。** 策略级状态同步更新下方「策略项目状态」表。
- **QMT 实盘下单**：一律复用 `broker/qmt_order.py`（防坑版），禁止裸调 `passorder`；规范与踩坑见 `broker/QMT委托买卖防坑指南.md`。
- **项目级记忆（2026-08-25 自 trae_workspace 迁移）**：各策略项目的历史研发记忆（硬约束/工程惯例/经验教训）见项目目录下 `PROJECT_MEMORY.md`，接手项目先读它再读代码。当前已有：`projects/Project_16_LightGBM股票大师/PROJECT_MEMORY.md`；对话级记忆归档见 `archive/_from_trae_workspace_20260825/memory_projects/trae_workspace/`。

## 核心模块认知

- 数据层统一接口：`data/feed.py` 中的 `DataFeed`，分发到 `astock_reader.py` 或 `duckdb_reader.py`。
- 财务数据 PIT 安全：`data/astock_finance_reader.py` 使用 `ann_date`/`f_ann_date` 防止前视偏差。
- 因子基类：`factors/base.py` 中的 `FactorBase`（ABC），因子引擎：`factors/engine.py` 中的 `FactorEngine`。
- 已实现因子：`factors/vwap_volume_corr.py`（GTJA191 因子：-1 * rank(corr(rank(VWAP), rank(volume), 5))）。
- 主力策略评分：`projects/Project_01_多因子IC小盘Alpha/strategy/scoring.py` 中的 `score()`，五因子加权（BP:27%、反转:22.5%、低波:22.5%、ROE:18%、VWAP:10%）。
- QMT 策略生成器：`broker/qmt_builder.py` 中的 `build_qmt_strategy()` 和 `save_strategy()`，输出 GBK 编码单文件。
- 本地验证适配器：`broker/local_context.py` 中的 `LocalContext`，把 C.* 调用映射到本地 xtdata，各项目的 `local_validate.py` import 使用。
- 实盘 Broker（文档级）：`MiniQMTBrokerPro`、`PaperTradingBroker`，含回调、重连、委托监控、状态持久化。
- 验证框架：`projects/verification/` 下 `engine_tests.py`（B-1~B-8）和 `robustness_tests.py`（D-1~D-7）。
- **飞书互通桥（2026-10-09 立）**：`scripts/feishu_bridge/`（`bridge.py` 收发/去重、`listen.py` 常驻监听、`start_feishu_listen.ps1` 守护），让诚哥在飞书里直接和 WorkBuddy AI 双向对话。**调用 lark-cli 一律经 `bridge.py`**（内部清 `OPENCLAW_CLI`/`HERMES_HOME`，否则报 `not bound`，与 P16 `_env_for_cli` 同款）；接收靠 `im.message.receive_v1` **WebSocket 长连接（无需公网 IP）**；守护任务 `QuantLab_FeishuListen_Boot`/`_Guard`；详见该目录 `README.md` 与 repowiki 知识卡。⚠️ `~/.lark-cli/config.json` 与 P16 推送**共用**，任何 `config init`/新建应用前必须先备份。

## 策略项目状态

| 编号 | 名称 | 年化 | 夏普 | 回撤 | 状态 |
|---|---|---|---|---|---|
| 01 | 多因子IC小盘Alpha | 10.1%（全池真实口径） | 0.41 | -24.4% | **已审计（2026-08-01）：幸存者偏差修复后超额仅BP因子+6.1%/年，2024+转负，不建议实盘** |
| 10 | 价值小盘V2（微调版） | **v2.3状态机口径18.0%**（V2a存档16.2%，含风控15.1%；**实盘对齐口径7.5%**） | 0.58 | -29.7% | **模拟盘候选（验证收尾停滞，2026-10-13 复核）**。2026-08-06 v2.3并入：buffer160降换手+退市排雷，超额全期+244.9%、2024+ +50.1%）；CSV管道+计划任务已完成；模拟盘首跑4个P0异常已于2026-08-09全部定性关闭（重复下单3处fix已落地build 20260809-005102+MOCK18/18，额度冲突校验PASS 20万/1000万）；遗留P1：rollback/恢复持仓时未撤活单（下轮修）；MIN_POSITION_PCT=0.6补仓拍板保留；修复版已部署QMT（E:\国金QMT交易端模拟），**2026-08-14 收盘对账 P0 修复**：reconcile 误判已成交买入为「未成交」→撤销持仓→触发补仓尾盘重复建仓，已对齐 6+2「账户 position 唯一真相」加 position 三态兜底 + 对账后补仓保护（build 20260814-151539，MOCK 29/29）；修复版部署 QMT 待 1 交易日模拟盘复跑核对对账日志（核对清单在项目specs/）**。**2026-08-14 实盘执行 P1 修复**：`_rollback_pending` 回滚前先撤 QMT 活单（防次日意外成交）+ 卖出回滚仍跌停/停牌进暂缓队列（build 20260814-210622，MOCK 36/36）**。**2026-08-14 11条优化方向全量实证**（报告 `Project_10\results\优化方向实证结果_20260814.md`）：①修复回测非确定性 bug（risk_state 污染致年化虚高18%→35.7%）；②18.0%是「双月open状态机+双月才查风控」口径，真实每日风控口径约**7.5%**（虚高主因=风控频率-8.3pp，结算频率仅-2.2pp）；③BP因子IC=0.069/ICIR=1.74/胜率81%强因子；④容量上限约1000-2000万（5000万收益减半）；⑤大盘MA200门控回撤-29.7%→-15.8%（代价年化-4.4pp）；⑥证伪：ATR反权/持有期软化/EP混入均负优化；行业cap无影响。**口径链（引用必带标注）**：状态机18.0%（双月open结算+换仓日风控）→ 对照A 15.8%（逐日close）→ **对照B 7.5%（逐日close+每日风控＝实盘真实预期，2024+超额 -44.4%）**；状态机口径 2024+ +50.1% 不可作实盘预期。**2026-08-23 账号迁移**：70180771（旧号 67014907 已废），账号戳 TAG 20260823-223613。**2026-10-13 E1 修复**：生产 QMT 版补齐回测宇宙筛选（30亿流通市值上限/pe_ttm>0/pb>0/质量排雷bps·roe·profit_dedt>0/ST剔除），生产候选池与回测口径对齐率 100%（827/827），构建 TAG 20261013-055319。**复跑核对未留痕**：`specs/模拟盘复跑核对清单_20260817.md` 全框空、仓库无执行结果；本机 D:/QMT_POOL 无 V2 state/log，「运行中」不成立 |
| 02 | 双均线趋势 | — | — | — | 回测中 |
| 03 | PEAD盈余漂移 | -7.8% | — | — | 不推荐 |
| 04 | ML多因子 | -21.4% | — | — | 已淘汰 |
| 05 | 红利低波 | 3.7% | — | -11.3% | 推荐（防御） |
| 06 | 质量小市值 | 4.1% | — | -20.1% | 可选 |
| 07 | 低换手反转 | -6.9% | — | — | 不推荐 |
| 08 | 指数增强 | 0.4% | — | -8.2% | 防御 |
| 09 | 组合策略 | — | — | — | 组合（70%红利+30%小盘） |
| 11 | ETF轮动（均线择时+动量） | +1.19% | 0.16 | -36.8% | **已淘汰（2026-08-09 定论）：7.6年实证跑输基准（仅2020/2025两年盈利），趋势+动量双腿同族已证伪方法，无绝对动量过滤为致命伤；ETF数据管道（astock etf_xtdata）保留复用** |
| 12 | RPS主升浪 | -60.8% | -0.47 | -63.5% | **已证伪归档（2026-08-14 诚哥拍板）：候选池枯竭→持仓集中→单票运气（2020单独跑+95%实为满仓押中002626），全周期PIT -60.8%，与通宵研究一致（趋势选股不可行）；可复用：`strategy/sector_rps.py`板块RPS信号 + 大盘门控 + `candidate_passed_avg_per_day`必检指标；报告 `projects/Project_12_RPS主升浪/results/RPS主升浪回测研究报告_归档结题.md`** |
| 18 | 买卖点信号工具（飞书需求） | — | — | — | **研究结题（2026-08-27）：4 类买点（箱体突破/杯柄/趋势低吸/二次启动）PIT 信号扫描+前向收益评估，均无稳定预测力（命中率<50%、样本外 2015-2018 全转负、gpsj 交叉验证通过）；增强版「信号+大盘门控+板块过滤」16 组合样本外全部转负仍证伪——门控/板块救不活无 alpha 的形态买点；建议仅作"信号提示+人工决策"工具；报告 `projects/Project_18_买卖点信号工具/results/买卖点策略潜力评估报告.md`、`.../买卖点增强版_门控板块_评估报告.md`** |
| 21 | 日内做T（备选T0） | — | — | — | **已归档结题（2026-09-29 诚哥拍板：彻底归档，P12/P18 模式）**：M1 裁决 NO-GO（2026-09-29）——按 `specs\证伪线预注册锁定_20260928.md` + `config\falsification_criteria.yaml`（2026-09-28 锁定，M1 脚本 `research\m1_decision.py` 只读 yaml 判据）聚合 2019-2024 六年 37,686,767 条信号事件 → **0/6 族过线（要求≥3）、合并六族聚类 t=-22.66、扣全成本年化夏普 -9.43、OOS2024 -28.02bps、池级密度 8.47（唯一过线项）**；直接根因 = 判据1全灭（毛期望最高 F2 仅 +11.87bps < 均成本 24.07bps 的一半；**F1 每 stock-day 成对发出 mom/rev×long/short、组内毛收益精确抵消 → 毛 alpha 恒 0**）。低波振幅带（<2.5%，13.7% 事件）净 -1.96bps 无净 alpha → 挂靠拍板已触发。报告 `projects\Project_21_日内做T\results\M1_decision_report_20260929.md`（含证伪归因 §三之二、预注册有效性、单源「仅 astock 分钟线」、静态池 3055 look-ahead 三警示）。**挂靠拍板（2026-09-29 诚哥）：选项②彻底归档**——不挂靠（ATR 低波 / P16 持仓 / 独立底仓三选项关闭）、不再投入；**Phase 2 永不启动**（重开须换口径重新预注册并经诚哥批准）。扫描产物 6 件 parquet 留盘（`*.parquet` 被 gitignore）。方案 `讨论室\立项与研究方案_20260926.md`；编号 19/20 已被占用 |

## 构建与产物

- QMT 策略构建入口：`broker/qmt_builder.py`。
- 产物为 GBK 编码单文件策略，含 `init(C)` → `handlebar(C)` → `exit(C)` 生命周期。
- **每个 QMT 构建产物必须带代码版本标记**：产物内硬编码一个 `BUILD_TAG`（格式 `YYYYmmdd-HHMMSS`），`build.py` 每次构建时自动替换为时间戳，策略初始化 `init(C)` 与每日日志/日终报告里都应输出该标记，便于部署时核对「本地构建版本 = 模拟盘实际运行版本」。缺版本标记的构建视为未完成。
- 研究回测入口：`main.py`，默认运行 Project_01。
- 修改源模块后，应通过构建脚本重新生成产物，不要手工长期维护构建产物中的重复逻辑。

## QMT 红线

- **两台客户端简称（2026-10-28 诚哥定，此后对话/文档/脚本注释统一用这两个词）**：

  | 简称 | 账号 | 客户端目录 | 版本 | 实际加载的策略 | 权限 |
  |---|---|---|---|---|---|
  | **主号** | `67014907` | `D:\国金QMT交易端模拟` | 2.1.33.0（新） | `P16_V13_RISK` + `STRATEGY_P16_G2_SELFCONTAINED` | **只读扫描；禁止 `qmt_autodeploy` 杀进程重启**（另设优雅重启路径，未拍板前不动） |
  | **沙箱** | `70180771` | `D:\QMT交易端模拟` | 2.1.19.200（旧） | `STRATEGY_P16_G2_SELFCONTAINED`（永远 passive 空转） | **可任意重启**，零介入部署/夜间闭环的验证与日常目标 |

  - 记法：**主号**＝真实账号在跑生产策略的那台（账号老、客户端新）；**沙箱**＝拿来做部署验证、绝不下单的那台（账号新、客户端旧）。**不要**按“新旧”称呼账号或客户端，二者方向相反，必混。
  - 计划任务对应：`QMT_Prod_*` = 主号，`QMT_Sandbox_*` = 沙箱；夜间闭环 `Quant_P16_QMT_NightlyDeploy`（21:10）**只对沙箱**。
  - 凭据在 Windows 凭据管理器：主号 `QMT_PROD_67014907`、沙箱 `QMT_TEST_70180771`（禁写入仓库/文档/日志）。
- 所有 QMT 运行产物必须是 GBK 编码，并且首行必须是 `# coding=gbk`。
- **★★ 2026-10-28 部署方式推翻旧红线：明文文件写入 `<QMT>\python\<模型名>.py` 会被客户端直接加载执行**。旧规则「必须人工经策略编辑器粘贴、python\ 下明文不会被加载」（2026-09-10）**已被实证推翻**——用明文 GBK 探针覆盖已注册模型文件后重启，日志产出 5.6 万行 `[CANARY2]` 输出；且客户端不会把明文重新加密或还原（正常跑完一轮、强杀进程后 md5 均不变）。
  - **默认流程（零介入）**：`build.py` 构建 → `python projects/Project_16_LightGBM股票大师/qmt_autodeploy.py --build <builder> --product <产物> --model <模型名> --qmt-dir <客户端目录>` → 脚本自动校验 GBK/BOM/首行/BUILD_TAG → 备份旧模型到 `_deploy_bak/` → 落盘 → **`CloseMainWindow` 优雅关闭（12s 超时后强杀）** → 以 **`reboot` 参数**重启 `XtItClient.exe` → 轮询 `userdata\log\XtClient_FormulaOutput_<date>.log` 匹配 `BUILD_TAG` → PASS/FAIL。
  - **唯一例外（一次性人工）**：**从未在客户端注册过的新模型名**仍需在界面里创建一次（任意内容的同名策略即可）。注册信息在二进制 `indexConfig\configFormula`（`XTF1`，1.29MB，模型名不以明文编码存放），仅靠往 `config/indexUserConfig.xml` + `indexConfig/formulainitstepsinof` 插入条目**无效**——客户端会打 `configFormula not exist, continue` 后静默跳过，只留一条 `auto run timeout`、零 Python 输出。已注册名字之后所有部署全自动。
  - **必须带 `reboot` 参数启动**：`XtItClient.exe` 不带参数会停在 `Proxy client not logged`，**一个策略都不跑**（10-28 实测）。`auto run timeout` 与 `loadFile [X] parse error` 是每次重启都有的**正常噪声**，不能当失败判据；判定只认 `BUILD_TAG=<期望值>`。
  - **沙箱不拦这条路**：`Foribdden FileIO` 拦的是策略进程内部的 open/read，客户端自身加载器读 `python\*.py` 不经过该拦截——两者必须区分开。
  - **护栏（脚本内置）**：09:00–15:05 拒绝重启（`--force-offhours` 才越过）；只杀 `ExecutablePath` 在 `--qmt-dir` 下的进程（绝不误杀另一台客户端）；只接受无 BOM、GBK、首行 `# coding=gbk`、含 `BUILD_TAG` 的产物。**覆盖前必须备份到客户端目录之外**（10-28 事故：探针覆盖后回滚时误删备份，导致该客户端 G2 加密原件丢失）。
  - **夜间全自动闭环（2026-10-28 立，T-20261028-002）**：`projects/Project_16_LightGBM股票大师/qmt_nightly.py`（`loop`=构建→部署→重启→双客户端日志体检→落报告；`scan`=只体检）+ `qmt_nightly_deploy.ps1`（UTF-8 BOM，控制台编码统一，60 天报告清理），由计划任务 **`Quant_P16_QMT_NightlyDeploy`（每日 21:10，S4U）** 驱动——位置在客户端 20:54 自重启之后、21:20 夸克备份之前。报告 `results/qmt_nightly/<戳>.md|json`（已 gitignore）。**演练用 `-Rehearsal`（= `--deploy-dry-run`，只校验/构建），禁用 `--no-restart`**（会把新产物写进 `python\` 却不加载，造成「盘上 TAG ≠ 运行中代码」失真态）。**只重启 `--qmt-dir` 指定客户端，`D:\国金QMT交易端模拟`（67014907）永远只读扫描**；生产端接入部署待拍板。**闭环只部署 G2 一个模型**（2026-10-28 诚哥拍板「Ens 搁置，不部署」，无 ENS 部署腿）。
  - **客户端启动失败判据（2026-10-28 立，T-20261028-004）**：见到 `initPythonEnv failed:[模块错误] 模块 PythonFormula36.dll 未找到，请确认安装包完整` ⇒ **客户端安装不完整**，`bin.x64` 缺文件（当日实测缺 138 个，含 `PythonFormula36.dll`/`python36.dll`/`pythonw.exe`/`icudtl.dat`/`d3dcompiler_47.dll`）。表层症状依次是 `Invalid file descriptor to ICU data received` → `libcef.dll 0x80000003` → `GPU process isn't usable. Goodbye.` → `initPythonEnv failed`，**与沙箱/权限/账号/网络无关，别再往那些方向查**。修复 = 用 7-Zip 解包官方安装包后 `cp -rn <pkg>\bin.x64\. <QMT>\bin.x64\`（只补缺不覆盖），再 `XtItClient.exe reboot` 重启（**单参数即可**，不需要 `--no-sandbox/--disable-gpu`）。**历史错误结论已推翻**：`D:\国金QMT交易端模拟`（2.1.33.0）强杀后起不来**不是**版本特性，重启 OS 也修不好，真因是磁盘文件缺失。健康判据三件套：主窗口标题含账号 + `FormulaOutput` 有新增行 + `initPythonEnv failed` 零命中。**两台客户端自启**由 `scripts/qmt_autostart.ps1` 承担（`QMT_Prod_BootStart` / `QMT_Prod_LogonStart` / `QMT_Sandbox_BootStart` / `QMT_Sandbox_LogonStart`；S4U 开机 + 交互登录两类触发，已在跑则跳过，带 `reboot`）。凭据存 Windows 凭据管理器（`QMT_PROD_67014907` / `QMT_TEST_70180771`），**禁止写入仓库/文档/日志**。
  - 详细证据与操作手册：`projects/Project_16_LightGBM股票大师/specs/QMT零介入部署方案_20261028.md`（§九 为夜间闭环）。失败方案存档 `deploy_paste_qmt.ps1`（UI 自动化，已被本方案取代）不再使用。
- **含中文的 `.ps1` 脚本（定时任务用）必须带 UTF-8 BOM**：Windows PowerShell 5.1 对无 BOM 文件按 ANSI/GBK 解码 → 中文变乱码 → 字符串引号被吞 → 级联「意外的标记」解析错误；**每次编辑含中文的 .ps1 后必须校验 BOM 仍在**（教训 T-20260903-015：编辑工具改写时丢 BOM，导致 run_scheduled.ps1 在 powershell.exe 下解析崩溃，09-14 周训将依赖此规则）。
- **计划任务注册规范（2026-09-21 立，T-20260921-001/002）**：①注册/重建含中文路径的 schtasks 一律用 `Register-ScheduledTask`/`Set-ScheduledTask` + `New-ScheduledTaskAction` 对象方式，**禁止 XML/schtasks 命令行拼接**（中文路径会 UTF-8→Latin-1 乱码写入任务定义，Intraday_Review 因此静默停摆 3 个交易日）；注册后必须回读 `Actions.Arguments` 核对实际存储值。②LogonType 一律 **S4U**（`New-ScheduledTaskPrincipal -LogonType S4U`），禁止 Interactive（每 30 分钟在桌面弹 PowerShell 窗口）；全部 39 个 Quant* 任务已切换，xtquant 在 S4U session 0 连 miniQMT 实测可用。
- QMT 运行环境按 Python 3.6.8 兼容处理。
- 禁止在 QMT 运行产物或会被合并进产物的代码中使用 Python 3.6 不支持的语法，包括但不限于：
  - `dict[str, ...]`
  - `list[str]`
  - `str | None`
  - `:=`
  - `match/case`
  - f-string
- `passorder()` 是全局函数，不是 `C.passorder()`。
- 账号 ID 必须硬编码，**双账号并存（2026-08-25 用户确认，各跑各的策略，禁止混用）**：
  - `67014907`（旧国金QMT账号，**仍在用**）：① Project_16 LightGBM 策略（V1.3，miniQMT/xtquant 委托，见 `projects/Project_16_LightGBM股票大师/qmt_config.py` 的 `ACCOUNT_ID`）；② Project_10 价值小盘V2（**2026-10 起迁至此号**，QMT 端自包含单文件，账号见 `config/value_smallcap_v2_config.yaml` 的 `account_id='67014907'`）。两策略各有独立 `capital_base` 与持票账本（state 文件内嵌 `account_id`），互不纳管。
  - `70180771`（新国金QMT模拟账号）：纯 QMT 端执行 ATR低波 / 黄氏529 / G2 / 融合 / 全天候等策略，见 `config/*.yaml` 与资金池文件。**此号未开通 miniQMT（xtquant）功能，禁止用 external Python + xtquant 连它**——2026-10-22 实测：`trader.connect()` 返回 0，但 `subscribe()` 返回 -1，`query_stock_asset()` 返回 None，持仓/委托/成交全为空（不是链路故障，是账号未授权 miniQMT 通道）。该号策略一律走 QMT 客户端内置策略引擎 + `D:/QMT_POOL/` 文件桥，不要重复排查或尝试外部 Python 连接。
  - 哪个策略用哪个号必须严格对应；引用错号会废单。此前「67014907 已停用」的说法已推翻，Project_16 的 build/脚本引用 `67014907` 属正常，不是废单。
- **持票账本（holdings/state 文件）必须内嵌 `account_id` 戳**；加载时若 `account_id` 字段缺失或与当前策略 `ACCOUNT_ID` 不匹配，必须自动备份旧档（重命名为 `.bak_acct_<旧戳>_<时间戳>`）并空仓起步（fail-safe），严禁拿错账号的账本交易（T-20260823-004）。
- `C.get_market_data_ex` 缺少财务字段，PE/PB/circ_mv 必须来自预生成 CSV。
- QMT Python 3.6.8 无 pyarrow，不能读 parquet，数据源用 CSV。
- `circ_mv` 单位是万元，30 亿 = 300000（万元单位）。
- 生产版不得混入 mock 或测试代码。
- 涉及真实下单、卖出重试、跌停暂缓队列的改动必须格外保守。

### 实盘执行红线（自实盘工程教训迁移）

- `passorder()` 是异步接口，**正常返回 0/None，不是订单号**；拿真实订单号必须反查 `get_trade_detail_data(acct, 'STOCK', 'order')`。
- 委托后即时反查会撞 QMT 约 100ms 的 order_id 分配延迟 → 必须短轮询（`*_LOOKUP_RETRIES`/`INTERVAL`）等待，反查失败会静默断链：不登记 pending → 成交回写/撤单/市价重试全不触发。
- 订单反查过滤：remark 只能作候选优先级信号，**不能硬过滤**；硬条件只留 code/volume/status/direction/time 五条 AND，唯一候选即使 remark 空也返回。
- 判订单方向用 `m_strOptName`（含中文"买入"/"卖出"），不要用未实测的 `m_nDirection` 等字段。
- 卖出风控评估前必须同步账户全量持仓纳管（孤儿持仓 = 账户有票但持仓文件没记录 → 止损/止盈永不触发）。
- 策略时间必须用 QMT 行情时间（`C.get_current_time()`），**不能读 `datetime.now()`**（设备 CMOS 时钟可能错乱）；相对计时用 `time.time()` 不受影响。
- **真实下单前必须校验「当前是否在 A 股连续竞价时段」**（09:30–11:30 / 13:00–14:57），**收盘后一律拒绝下单**（T-20261023-001，2026-10-23 血的教训）：2026-10-22 **15:18 收盘后**用外部 xtquant 清仓 28 只，QMT 模拟端照样返回 `status=56 / traded_volume=全量`，**看起来全部成交**；但**次日 08:52 账户"复活"了 20 只**（成本价/入场日仍是原值，期间无开盘、无买入委托）→ **收盘后委托的「已成」状态不可信，不等于柜台真实记账**。落地：`projects/Project_10_价值小盘V2/v2_liquidation.py` 新增 `_in_trading_session()` + `--force-offhours` 逃生阀，非时段默认 `exit 2`；所有真实下单脚本（V1.3/G2/ENS 换仓、清仓工具）都应有同类时段闸。
- 收盘（15:00）后 handlebar 不再触发，>15:00 的收盘任务永不执行；"启动时执行一次"的任务放 init 末尾，两者用标志联动防重复。
- QMT 模拟端 `get_trade_detail_data` 只保留当日 deal/order，隔日查不到 → 每日导出 CSV 到 `D:/QMT_POOL/` 是刚需。
- QMT 内置 Python 不保证有第三方包（如 pyyaml），import 必须 try/except fallback；重装/换设备后必看 `XtClient_FormulaOutput_*.log` 确认初始化完成。
- 策略必须自包含：config 读取不得依赖 `__file__`，读不到要有完整 `_DEFAULT_CONFIG` fallback。
- 详细清单见 `全局复利与踩坑日志.md` 的 QMT 相关章节。
- **买入 pending 超时必须重试，不可即刻放弃**：进入 pending 后，超时从 30 秒延长至 300 秒 (5 分钟)，最多重试 3 次（撤单→重新 passorder→更新 pending time/retry→继续跟踪）；3 次重试（约 15 分钟）仍不成交才打印「pending放弃」彻底放弃。禁止出现「30 秒超时即撤单删 pending、永不补单」的写法（2026-08-25 浙能电力案例已证痛点）。

### ★★★ QMT 策略模式长期架构红线（2026-10-15 诚哥拍板；2026-10-19 修订：xtquant 外部通道解禁，最高优先级）

- **拍板原文**：「QMT 以后只能采用内置 Python、且交易信号完全在 QMT 系统内部产生、不与外部环境进行任何交互的策略模式」。
- **⚠️ 2026-10-19 修订（诚哥拍板：先不管红线、恢复 V1.3）——「xtquant 外部进程」解禁**：**存量 P16 V1.3 继续走 miniQMT/xtquant 外部下单**（`projects/Project_16_LightGBM股票大师/qmt_config.py`：`QMT_PATH=D:\国金QMT交易端模拟` / `USERDATA=userdata_mini` / `ACCOUNT_ID=67014907`；换仓 `rebalance_daily.py --live`，10:05 兜底 `rebalance_daily_guard.ps1`）。**依据**：①券商收紧的是 **QMT 内置 Python** 的 `m_nPythonOpFile` / `m_nPythonConnectNet` 两位，**xtquant 外部进程跑在 QMT 之外、不受该开关约束**；②2026-10-19 交易日实测外部会话正常（`onConnected quant session ... connected`、`onQueryStockPositions query positions found 30`、`account [67014907] subscribe`）；③V1.3 换仓管道 10-19 实测健康（10:05 兜底正常执行 live、`executed_live=true`）。**边界（仍禁止）**：①**QMT 内置策略**侧红线不变（内置文件IO 仍被沙箱拒 → 内置策略仍须自闭环零文件）；②新策略**不得**以 xtquant 外部进程为默认架构（优先内置自闭环）；③xtquant 通道**仅限 V1.3 现役策略**的换仓/风控使用，不得据此新增其他外部下单策略。
- **背景**：券商 10-08《内置 Python 策略功能调整》通知 + 服务端 10-13 20:53 收紧 `m_nPythonOpFile`（文件IO）/ `m_nPythonConnectNet`（外部网络）两位权限（灰度提前生效，10-23 全量 deadline）；客服确认「行业普遍趋势、无豁免」。自 10-15 起此为**长期架构约束**（非临时故障应对）——即使未来权限位恢复，新策略也不得再走被关闭的路线。
- **⚠️ 实测校正与终局（10-18 初测 → 10-26 定论）：`m_nPythonConnectNet` 从未生效/下发，网络全通**（回环/`127.0.0.1:58600`/外网 DNS 全 OK，10-26 更扩展到 HTTP/HTTPS/`requests`/`ssl`）；**10-15 那份「两位权限同时收紧」的字段级定论已被推翻**——拦截不在权限位层，而在客户端定制 `python36.dll` 的 C 层（详见上条「机制归因修正」）。**10-23 deadline 已过（2026-10-24 复盘）：本机两台国金客户端（2.1.19.200，deadline 后二进制未升级）策略照常运行——STRATEGY_V2 / P16_V13_RISK / G2 信号内产版均 passive=False、委托正常成交、零 Foribdden**。但行业面在收口（华鑫 10-30 关 miniQMT、国金 8-21 已通知存量 miniQMT 移除、迅投社区 10-23 后部分券商网络全断），**网络路线仍不得作为长期方案**；状态通道优先级 = **sqlite（已验证跨重启，首选）> KV（仅小数据）**。10-18 轮各条旧推测的逐项校正见研究报告 §6.1。
- **全部关闭的路线（禁止再投资/再新增）**：
  1. **文件桥模式**（`cmd/`+`state/` 读写，G2/ENS 现役架构）——策略进程读写任何 D:/QMT_POOL 文件均属「与外部环境交互」，`_read_json`/`_atomic_write_json` 消费 cmd、回写 heart/fills/positions/asset 全部废止；
  2. ~~**xtquant 外部进程**（miniQMT 外部 Python 下单，P16 V1.3 现役架构）——交易信号在 QMT 外部进程产生，违反「信号完全在 QMT 内部产生」~~ **← 2026-10-19 已解禁（见上方修订条款）：P16 V1.3 继续使用此通道，不再属禁止路线；但新策略不得以此为默认架构**；
  3. **申请豁免/白名单**（方案A）——客服已确认无豁免，`m_pythonWhiteList` 字段存在但不可用；
  4. **绕过沙箱**（方案C）——不推荐且违反本红线。
- **QMT 内置策略的唯一合法模式（方案B 自闭环，DE 方案 `specs/QMT沙箱自闭环改造方案_20261015.md`）**：策略=QMT 内置 Python 单文件（部署走 `qmt_autodeploy.py` 自动落盘+重启，2026-10-28 起；新模型名需先注册一次），**信号、风控、执行、状态全部自包含**——行情用 `C.get_market_data_ex`/`C.get_full_tick`（QMT 内部 API，不属外部交互）；状态三层模型 L1 会话内存（全局变量）/ L2 账户即账本（`get_trade_detail_data` position/order/deal，唯一跨重启状态源）/ L3 期初账本快照（源码硬编码字面量，粘贴时更新）；跨重启去重=sid+init 扫当日 order 反查 remark 重建水位；监控=策略 `print` → FormulaOutput 日志 → 沙箱外 schtasks 旁路读日志推飞书（**策略进程零外部交互**，旁路在 QMT 外属独立观察者不违规）。
- **改造顺序（四步全部已完成，2026-10-18 复核）**：V13（纯风控自闭环，S）**✅ build 20261016-020830** → V2（快照内嵌+期初账本，M-L）**✅ build 20261016-075943**（另有 10-16 盘中 P0 对账缺陷修复版 20261016-150143）→ G2/ENS 纯风控版（S）**✅ build 20261016-083453 / 085414** → **G2/ENS 信号内产版（L）✅ 已上线**（原「需重训内置可算特征版 LightGBM」已完成：B21 k=1500 dump 纯 Python 树 + 21 特征内置计算模块；产物 **BUILD_TAG=20261017-235317 / 235319**）。**2026-10-18 23:3x 日志实测**：`d:\QMT交易端模拟` 上 G2 与 ENS 均以 `mode=自闭环信号内产(零文件)` 运行、`passive=False`、`pending=0`、positions=1、零 `Foribdden` ⇒ **信号内产版确已人工粘贴部署并在跑**（比 10-17 看板登记的「⏳待人工粘贴」更新一步）。降级预案（维持风控版/换线性评分卡）未启用；**ENS 臂去留仍待拍板**（T-20261017-004 策略级回测：G2 臂年化 +18.8% vs ENS 臂 +5.1%，建议仅上 G2 臂）。
- **遗留处置**：改造完成前，现役文件桥策略（G2/ENS）在沙箱下转被动属**预期行为**（宁可不交易也不违规交互）；`D:/QMT_POOL/` 下存量 cmd/state 文件仅作历史证据与外部监控旁路数据源，策略侧不再读写。

### QMT 文件 IO 沙箱红线（Foribdden FileIO，2026-10-15 立）

- **现象**：QMT 定制 `python36.dll` 在客户端处于受限模式时，策略 Python 打开**已存在**文件（读或写）一律抛 `PermissionError: Foribdden FileIO`（读回为 `ForBidden File Read`；**日志实际拼写为单 b 双 d「Foribdden」，非 Foribidden**，字符串内置于 `bin.x64\python36.dll`）；**新建文件也不可靠**（10-18 上午可写、同日下午起即被拒，读回一律被拒）——见下条 10-20 复测。
- **⚠️ 2026-10-20 探针复测（`d:\QMT交易端模拟` 70180771，08:52:30 跑 `QMT_SANDBOX_PROBE.py` 20261018-01，被客户端重载 `KeyboardInterrupt` 中断）**：`makedirs` / `listdir` / `stat` **OK**；**读任何文件一律 FAIL**（读 `D:/QMT_POOL` 已有文件、读 `C:/Windows/win.ini` 全 `PermissionError: Foribdden FileIO`）；**新建文件+写也 FAIL**；TCP 段 **回环自测 OK + 连 `127.0.0.1:58600` OK**（网络位当天仍未下发；外网 DNS 那项在跑之前被中断，当日未复测）。**结论：内置策略的文件 IO 一律不可依赖（读必拒、写时开时拒）→ 任何文件桥在沙箱内无解**；唯一不占文件位也不占网络位的候选通道仍是 `save_py_key`/`get_py_key`（走券商后台服务），但 **v2 探针（20261018-03，7 项 KV 深测）至今未人工粘贴运行 → KV 生死未定**，未验证前不得当作既定方案。
- **★★★ 2026-10-26 沙箱能力矩阵已摸清，2026-10-27 行情 API 补测收口（探针 v4.6 `20261027-03`，AUTO 全 9 段 **55 OK / 25 FAIL**，多轮一致）——细节全部下沉到研究报告，此处只留结论**：
  - **权威报告（唯一事实源）**：`projects/Project_16_LightGBM股票大师/specs/QMT沙箱能力全量探针研究报告_20261026.md`（能力矩阵 / 踩坑清单 / 跨重启证据 / `C` API 清单 / 复现方法 / 遗留事项）。**引用任何探针数据前先读它，勿凭记忆引用。**
  - **✅ 可用通道（按推荐优先级）**：①**网络全开**（回环/`127.0.0.1:58600`/外网 DNS/HTTP/HTTPS/`requests 2.24.0`/`ssl` 全通）；②**子进程全开**（`subprocess.run`/`os.system`/`os.popen` 均 OK）；③**sqlite 文件库**（走自身 C 层 IO，**绕过 `Forbidden fileIO`，已验跨进程重启持久**，唯一可承担中量状态持久化的通道）；④**KV `save_py_key`/`get_py_key`**（全局函数非 `C` 方法；返回值 `(value, rc, msg)` 首元素才是值；**仅限小数据，1MB 起静默丢**）；⑤win32 层（`winreg`/`ctypes`）；⑥第三方库丰富（requests/numpy/pandas/scipy/sklearn/yaml/zmq/redis/talib，缺 pyarrow/websocket/pymysql/h5py）；⑦**内置行情 API（2026-10-27 11:07 实证收口）**——`get_market_data_ex` + `get_full_tick` 实调均返回真实行情（000001.SZ rows=5 last_close=11.49 / 600000.SH rows=5 last_close=9.78，K 线与 tick 交叉一致）→ **「内置自算信号」前提成立**。
  - **⚠️ 数据面 API 分层结论（2026-10-27 14:11 v4.7 实跑，PROBE_TAG `20261027-04`，国金客户端；权威报告 `specs/QMT沙箱能力全量探针研究报告_20261026.md` §10.10）**：
    - **✅ 可用**：`get_market_data_ex`（`none` / `front_ratio` 均可用，**数据日期到 20261027，比本地 astock 新 2 个交易日**；`volume`=手、`amount`=元）；`get_stock_list_in_sector`（8/8：hs_a 5225 / hs300 300 / zz500 500 / sz50 50 / star 618 / chinext 1412；**只给成分，无行业分类**）。
    - **⚠️ 可调但口径不可信**：`get_instrument_detail.FloatVolume/TotalVolume`、`get_total_share`、`get_float_caps` 三者同源——20 只样本中仅 **2 只**（600036.SH 精确 / 600519.SH 0.49%）在 1% 内，**18 只 FloatVolume 偏差 1.6%~100%（603529.SH/688981.SH 返回 0）**；`get_total_share` 口径是 **A 股股本（不含 H 股）**（如 601318 返 108.33 亿 vs astock 181.08 亿）；**`get_float_caps` 返回的是流通股本(股)，不是流通市值**（与 `FloatVolume` 逐只同值，命名陷阱，不得当 `circ_mv` 用）。价格字段（`PreClose`/`TradingDay`）是当日最新，只有股本陈旧。
    - **❌ 不可用**：`get_finance`（`'__main__.ContextInfo' object has no attribute 'get_finance'`——`_PyContextInfo.py` 包装器有、底层 C context 没有）；`get_turnover_rate`（批量返空表 `[0,0]`）；`get_turn_over_rate`（单票返 `nan`）。
    - **⚠️ API 可用但无数据**：`get_financial_data` 7 个字段全部 `438×1`、值全 `nan`（字段名有效、形状正常）→ 归「**本地财务数据包未下载**」，先去客户端补包再重跑，**不判死**。
    - **对 21f 内产引擎的直接影响**：`float_share` → `true_turnover` → 污染 `turn_ma5` / `turnover_rank` / `log_mv` 三个特征 → 即使打开 `SIGNAL_ENABLED` 信号也带偏。**这是维持 `SIGNAL_ENABLED=False` 的第二条实证理由**（第一条：F2/F5/F6 拿不到）。
    - **复权口径本轮未判定**（3 日窗口内无除权票，两边比值都≈1.0，测试无效）；**探针今后一律盘后跑**（本轮 14:11 盘中执行，当日日线是未收盘值，同日两次调用 volume/close 有微小漂移）。
  - **❌ 不可用**：`open`/`os.open`/pathlib/makedirs/rename/remove/append/相对路径全 `Forbidden fileIO`（**只有 `listdir`/`stat` 可用**）；`tempfile.NamedTemporaryFile` **挂死**；`pickle`/`mmap` 随 open 一起拒。
  - **⚠️ 三条落地约束（不阻断使用，2026-10-27 诚哥拍板「先不降级」）**：①**HTTP(S) 断网时须能按内置行情 API 降级**（网络路线不作单点依赖；**行情 API 已实证可用（2026-10-27），降级路径成立**）；②**`subprocess` 已证「能启动+拿到 stdout」，外部进程写文件待补测**；③**sqlite 证据均在 `_isolate()` daemon 线程内取得，主线程直调待补测**——落地要么补测、要么继续用 `_isolate()` 包一层。**已知缺口（行情 API 两项已于 2026-10-27 11:07 实证收口，剩余按优先级）**：实盘上下文交易 API（`passorder`/`get_trade_detail_data`/`get_current_time`）、HTTP POST/飞书 webhook、sqlite 主线程/并发/崩溃恢复。**行情 API 正确姿势**：`get_market_data_ex` 必须用 `stock_code=` 关键字传股票列表（**首位参数是 `fields`**，位置传参会绑错位 → 返回空 dict 且无异常）；`set_universe` 非必需（返回 None，G2 生产不调用）。
  - **⚠️ 运行时版本（2026-10-28 复核订正：国金 2.1.33.0 内置 Python 仍是 3.6；官方 10-16 公告「内置 Python 升到 3.12，2.1.32 及以上版本支持」在国金这版未兑现）——原写「`bin.x64\python36.dll` ver 3.6.8、全盘只有 `PythonFormula36.dll`」的路径是错的**：那是**老客户端 `D:\QMT交易端模拟`（2.1.19.200）**的布局（`python36.dll` 4,170,240B/2022-12-22 + `pythonw.exe`）；**国金 2.1.33.0 的 `bin.x64\`（236 条目）内没有任何 `python*.dll`**，三台客户端也都已不存在 `PythonFormula36.dll`，国金的解释器宿主未定位（pid 10136 模块表无 python dll 却在跑策略）。**3.6 的可靠证据改这四条**：①`python\__pycache__\_PyContextInfo.cpython-36.pyc`（国金，mtime 10-26 09:33:24 = 该客户端进程启动同刻）；②`config\qsci\api\python\` 只有 `Python-2.7.api`/`Python-3.6.api`；③携带 site-packages 的 wheel ABI **全 cp36**（xtquant 的 pyd 只到 cp311、无 cp312）；④stdlib 无 `dataclasses`/`zoneinfo`/`tomllib`、仍有 `lib2to3`。另 10-26 18:50 探针在 2.1.33 上自报 `py=3.6.8`。**⑤内置 xtquant（同一公告的另一半）本机已铺未起**：`bin.x64\Lib\site-packages\xtquant\` 存在，但 `broker.ini` 的 `pythonworker=57500`/`pythonbroker=60500` 均未 LISTENING。→ **Python 3.6 语法禁令继续有效**（禁 f-string / 海象 / match / 新式类型注解），别因「QMT 升级了」就放松。
  - **唯一无保留的结论 = 「文件桥彻底出局」**（可直接据此废弃 `_read_json`/`_atomic_write_json`），不受上述不确定性影响。
  - **探针编写铁律**：不得一次跑全量（单项中止会吞掉整轮结果）→ 必须 `SEGMENT` 分段 + 每行 `sys.stdout.flush()` + 高危项 `_isolate()` 隔离并放最后；`print` 是批量 flush 的，崩溃时缓冲区全丢，故高危项执行前必须先打印 `>> about to run: xxx`。
  - **两台客户端强度不同**（`D:\国金QMT交易端模拟` 比 `D:\QMT交易端模拟` 更严：前者连新建都拒、`open.__module__` 已不可见，后者 `module=io` 且新建可写）→ **任何「某通道可行」的结论必须标注是哪台客户端测的**。
- **⚠️ 机制归因修正（2026-10-26 探针实证，重要）：文件 IO 拦截是「客户端本地 C 层拦截」，不是「服务端下发权限位」**。证据：`XtClient_20261026.log` 全天 **13 处** `doRun` 行均 `m_bAllowOpenFile:1`（`:0` 零处）、4 处 `setPyFileIOAccess nAuthValue : 1`（登录侧也判定为允许），同客户端同日的 `open`/`os.open`/pathlib 却全 `Forbidden fileIO`；网络位亦未收紧。→ ①别再把「等服务端权限位下发/恢复」当恢复路径；②沙箱由**本地客户端进程**决定（重装/升级/重启改变行为，两台客户端强度不同即此故）；③恢复只能重启客户端进程。这也解释了 10-23 deadline 已过而策略仍照常运行（服务端从未对模拟端下发收紧）。详见研究报告 §3.6。
- **⚠️ 判定标准修正（2026-10-24，10-23 deadline 后实证）**：`XtClient_Formula_*.log` 的 `python open file: <path>` 行**不再能单独作为沙箱生效标志**——10-20 起两台客户端每天出现 2-4 条该行（全为引擎加载 site-packages：`pyparsing.py`/`matplotlibrc`，开盘与 20:54 自重启后各一对），同期策略运行完全正常（passive=False、委托成交、无任何 Foribdden）。**可靠判定改为**：①看被代理路径是否为策略自身数据文件；②看策略侧是否实际抛 `Foribdden FileIO`；③看 `[init]`/`[hb]` 持仓是否归零回退默认值。三者同时命中才判受限模式。
- **症状识别**：`strategy_log_*.txt` 当日零写入、`[hb] 持仓=0 现金=<本金默认值>`（state 读失败回退默认）、锁/状态/遥测写读全被拒、`[init]` 持仓=0 但账户实际有票——**恢复方法：重启 QMT 客户端并确认交易账号登录成功后重载策略**（不可只重载策略，客户端进程不死沙箱不解除）。
- **代码红线（所有 QMT 策略 init 必须）**：①IO 预检——锁通过后探测「已存在 state 文件可读 + log 文件可追加」，任一被拒即转被动 fail-safe 并大声打印疑似沙箱提示；②状态载入 IO 失败必须转被动，**严禁「活跃实例 + 状态加载失败 + 持仓=0」盲跑**（会误判全空 → 换仓/补仓全量重买 = 重复下单，10-14 白天旧 build 实证形态）；③读路径异常禁止 `except: pass` 静默吞掉（ENOENT 静默、其余显式打印）；④被动实例日志必须标注真实原因（锁竞争/IO 预检失败/状态载入失败），不得一律打「锁被占用」误导诊断。参照实现：`projects/Project_10_价值小盘V2/strategy_v2.py` 的 `_io_preflight()`（build 20261015-085137）。

### 资金分配红线

- 国金QMT账号 `70180771`（新模拟账号）下多策略共存，每个策略锁定独立「虚拟子账户」本金（`capital_base`），只能动自己 ledger 的票和自己的额度，**绝不抢占他人资金、绝不纳管/卖出他人持仓**。另一账号 `67014907`（旧号，miniQMT/自包含）承载 Project_16 V1.3 与 Project_10 价值小盘V2 两个策略（各有独立 `capital_base` 与持票账本，互不纳管），独立管理。
- **账户总额约束（硬）**：`Σ 各策略 capital_base ≤ 账户实际总资产`。仓库内权威副本 `D:/QuantLab/config/capital_allocation.yaml`；**校验器脚本实际读取的是池副本 `D:/QMT_POOL/config/capital_allocation.yaml`，两副本必须保持一致**（改仓库副本后须同步池副本）；改后必须跑 `D:/QuantLab/scripts/check_capital_allocation.py`（退出码 0 才许部署）。
- 账户 `total_capital` 必须在国金QMT客户端查「总资产」填入；未填只警告不报错，但生产环境必填。
- 当前已锁（唯一事实源 `config/capital_allocation.yaml`）：**atr_lowvol_equalweight（ATR低波等权不杠杆）10 万 / huang529_breakout（黄氏529主升浪）10 万 / g2_bridge（G2桥，P16，账号 67014907）10 万 / p16_ensemble_bridge（融合桥，P16，账号 67014907）10 万 / all_weather（全天候底仓V1）10 万，Σ=50 万 ≤ 账户总资产 1000 万**。**2026-10-22 变更**：①`value_smallcap_v2` 已清仓退出（28 只全成、已实现 −1188.08 元 / −4.36%），从分配表移除；②`g2_bridge` / `p16_ensemble_bridge` 由大QMT 70180771 迁至 miniQMT **67014907**，改为 xtquant 直连下单（70180771 未开通 miniQMT）。`dual_band_6plus2`（主升浪6+2）已于 2026-08-05 淘汰。新增/调额先改分配表再跑校验（退出码 0 才许部署）。
- 共享账户无法物理阻止别策略花掉你的额度，真隔离需开子账户/多模拟账号；当前靠约定保证不超额。
- **人读总表（与校验器同源，已同步至 QuantLab 枢纽）**：`D:/QMT_STRATEGIES/资金分配总表与约束.md`（镜像副本：`D:/QuantLab/资金分配总表与约束.md`）。

## 风控优先级

- 卖出风控优先级高于买入优化。
- 不得为了提高买入频率、评分命中率或回测收益，绕过风控底线。
- 个股止损 8%、组合最大回撤 15%、最长持有 60 天、单日最大换手 30%。
- 涉及清仓、减仓、禁入期、状态持久化、跌停暂缓的逻辑改动，需要补充或更新测试。
- **P16 止损口径例外（2026-09-11 注明）**：Project_16 系（V1.3/G2/融合）回测与实盘统一采用个股止损 **-7%**（`qmt_config.STOP_LOSS_PCT=-0.07`，桥内与回测同口径），比全局底线 8% **更严**，不违反红线；AGENTS 全局「8%」为其他策略/项目的通用底线。G2/融合桥出场顺序 STOP→TP(+15%)→TRAIL(8%激活+保本底线)。

## 配置系统（三级级联）

1. **全局配置** `config/settings.yaml`：项目信息、数据源、回测参数、因子预处理、策略默认股票池、日志。
2. **实盘配置** `config/trading_config.yaml`：账号（70180771，新国金QMT模拟账号，跑 ATR/v2 等纯 QMT 策略）、交易参数、风控阈值、委托管理、调度计划、通知。**Project_16 例外**：账号用 `67014907`（见项目 `qmt_config.py`，miniQMT 委托）。
3. **项目级配置** `projects/Project_XX_*/config/strategy.yaml`：项目独立参数。

## 文件通信

- 策略运行时主要通过 `D:/QMT_POOL/` 交换文件（实盘与 QMT 共用）。
- 常见文件包括：
  - `selected.txt` / `QMTselected.txt`：外部股票池
  - `*_holdings*.txt`：持仓跟踪
  - `*_nav*.txt`：累计盈亏净值
  - `sector_heat.json`：板块热度预计算数据
  - `成交记录_*.txt`：交易记录
  - `*_sell_state_*.json`：卖出状态持久化
  - `strategy_log_*.txt`：每日策略执行日志
- 数据源路径：`D:/astock/`（daily/finance/basic + 分钟线 parquet，唯一权威数据源，2026-08-29 起）。
- QMT 路径：`D:\国金QMT交易端模拟`（本机无 E 盘，2026-08-29 修正）。
- 实盘状态持久化：`data/broker_state.json`。

## 数据层约定

- 所有数据读取器遵循鸭子类型 4 方法接口：`load_window()`、`trading_calendar()`、`coverage()`、`close()`。
- 财务数据必须 PIT 安全：使用 `ann_date`/`f_ann_date` 过滤，禁止使用未来财务数据。
- `filter_func` 模式：市值等过滤条件以 callable 传入评分函数，在函数内部应用（预过滤会降低收益）。
- 数据缓存：`data/cache/` 目录，过期时间 1 天。
- 主数据源 `D:/astock/` 是买断离线资产（2009 起、财务全量、PIT 字段齐）；`adj_factor` 是后复权因子，前复权价 = 原始价 × (adj_factor / 最新 adj_factor)。目录结构：`daily/stock_daily.parquet`（2009-01-05 ~ 2026-09-30，1472 万行 × 33 列，2026-10-05 供应商更新）、`basic/stock_basic.parquet`（5922 只）、`finance/*.parquet`（8 张财务表）、`min_{1,5,15,30,60}min/<ts_code>.parquet`（各 5826 只，含 trade_time）。
- **离线数据升级制度（2026-09-14 立，唯一事实源 `scripts/update_astock_from_e.py`）**：E 盘离线数据包（`E:\temp\行情数据\`）升级 D:/astock 时：
  1. **主行情（daily/stock_daily）**：若有到最新日期的全量版 → **不备份、直接全量替换迁移**（`replace=True`，写临时文件后 `os.replace` 原子替换 = 删除旧文件 + 迁移新全量版）；若只有增量版 → **增量文件放增量目录、不合并进主库**（防口径污染）。
  2. **其他数据（finance/lhb/research/board/board_fundflow/basic）**：可合并（按主键去重，冲突以 E 版为准，只增不删），写前备份为 `<name>.bak_20260913`。
  3. 任何一步异常立即停止，后续表不动；执行后必须写后复核（行数/日期范围）并生成验证报告；**parquet 列选择重写后必须 `replace_schema_metadata(None)` 清洗 pandas 元数据**——`select()` 丢列不清元数据 → 产物被 `pd.read_parquet(path, columns=[...])` 指定列读取即崩（全表读不报错，易漏检），2026-10-05 供应商落库 5 表 P0 已修（根因已补 `D:/astock/_vendor_20261005/` 两个 apply 脚本 3 处 select）。
  4. **在线增量补充（2026-09-14 立，2026-09-26 扩至 daily_basic 日刷，`scripts/refresh_online_incremental.py`）**：离线包未及时下载时，G2 数据源可用 Tushare 在线增量保鲜，TABLES 现含 4 表——lhb/top_list（按 ts_code 去重对齐 D:/astock 口径）、research/report_rc_daily、northbound/hk_hold_full（**保持 RangeIndex 结构**，兼容 `hk["trade_date"]` 列访问）、**daily/daily_basic（2026-09-26 方案A：Tushare 日频估值 pe_ttm/turnover_rate 等 16 列，供 G2 评分卡 F6 当日真实口径；`allow_create=True` 首建模式；消费端 `deploy_predict_g2.py` 优先读 `data_config.DAILY_BASIC`、布局探测失败回退 MAIN_DAILY 快照）**，写前备份 `.bak_online` + 写后复核；**北向 hk_hold 2026 实测已恢复日频**（推翻"2024-08 起季频无当日值"旧结论），不再是断源天花板；ths_daily/moneyflow_ind_ths 需 Tushare 5000 积分档（2000 档无权限），暂靠离线包。接入双挂载：`run_scheduled.ps1` retrain 模式 train_g2 前置全表刷新（best-effort，失败只告警不阻断）+ `g2_candidates_night.ps1` 每晚 20:05 专刷 daily_basic（best-effort，失败不阻断 build_g2_daily，F6 走快照兜底）。moneyflow 已有独立每日 19:30 刷新。
- **备用数据源（回测交叉验证，全局规则）**：`E:/huicexitong/runtime/sj/gpsj.duckdb`（`data/gpsj_reader.py` 鸭子类型 reader，与 astock_reader 同 4 方法接口）。口径与 D:/astock 同源：adj_factor/换手率/市值/成交额/不复权价逐字段一致（已验 2025 全年 ATR MAX5 回测差异 0）。**注意 gpsj `收盘价` 列是前复权价，禁止直接用**，reader 已封装为取 `不复权_*` 列 + `复权因子`；**is_st 已在 reader 层反转归一化**（gpsj 原生 `ST` 列 0=ST/1=正常 与 astock 相反 → reader 输出 astock 口径 1=ST/0=正常，2026-10-05 T-20260827-003，测试 `data/test_gpsj_reader.py`）；全市场覆盖仅 2015-01 起，2015 前非全市场不可用于全市场选股对比。**每个新策略/因子回测定稿后，必须用备用数据源随机抽取 1 个自然年做同区间对比**，两源 CAGR/最大回撤应基本一致（差异 >1pp 需排查数据口径），模板 `research_audit/compare_gpsj_astock_2025.py`。**源不可用时的降级规则（2026-08-29 立，当前即处于此状态）**：先调 `data/gpsj_reader.py` 的 `is_available()`；返回 False 则脚本打印 `[SKIP]` 并 `return 0`，**在报告显著位置标注「单源结论（仅 astock），未经备用源交叉验证」**，既不静默跳过、也不阻断交付。恢复方式：设置环境变量 `GPSJ_DB_PATH` 或改 `gpsj_reader.GPSJ_DB_PATH` 默认路径。
- look-ahead 是回测第一杀手：复权 look-ahead、宇宙选择 look-ahead（静态池套全期）、撮合 look-ahead（盘中取当日 close）都击中过；"盘中 vs 尾盘对照差异 >30pp 且方向反转"是验证 look-ahead 的干净方法。

## 因子层约定

- 新因子必须继承 `FactorBase`，实现 `compute()` 方法。
- 因子预处理顺序：winsorize(1%,99%) → z-score → 方向控制。
- 因子注册到 `FactorEngine` 后可批量计算和 IC 评估。
- 因子权重修改前，需确认 SPEC、研究文档或用户明确要求。

## 策略项目约定

- 每个策略项目独立目录：`projects/Project_XX_名称/`。
- 标准结构：`strategy/`（模块化代码）、`config/`（参数）、`results/`（回测结果）、`specs/`（需求文档）。
- **QMT 构建物自包含**：每个项目的 `build/` 子目录存放 QMT 部署文件（GBK 单文件），不在项目间共享或拷贝到公共目录。开发流程：编辑源码 → `python build.py`（语法检查+GBK转码） → `build/` 下生成部署文件 → **`qmt_autodeploy.py` 自动落盘 + 重启 + 日志核对 BUILD_TAG**（2026-10-28 起，见「QMT 红线」；新模型名需先在客户端注册一次）。
- 新策略项目必须经过回测验证（B 模块）和鲁棒性测试（D 模块）才能进入实盘候选。
- 不推荐的策略（负年化）保留记录但不删除，作为对照参考。

## 验证框架

- 引擎验证（`verification/engine_tests.py`，B-1~B-8）：已知结果校验、成本计算、涨跌停/停牌处理、资金约束、一致性、先卖后买、止损。
- 鲁棒性验证（`verification/robustness_tests.py`，D-1~D-7）：子样本、牛熊分割、压力测试、成本压力、容量、未来函数、幸存者偏差。
- 回测结果必须关注：年化收益、夏普比率、最大回撤、换手率、IC/ICIR。
- 新策略上线前必须通过 B 模块全部测试 + D 模块至少 D-1/D-2/D-6。

## 测试与验证

- 研究环境使用 Python 3.11，QMT 生产必须保持 Python 3.6.8 兼容。
- 优先运行与改动相关的 pytest 测试，再视情况运行更大范围测试。
- **本地 miniQMT 验证（改策略后必跑）**：改完策略逻辑后，用 `broker/local_context.py`（全局共用适配器）连本地 miniQMT 实时数据快速验证，10 秒出结果。流程：
  1. 各项目写 `local_validate.py`，import `broker.local_context` 的 `LocalContext`
  2. 用 miniqmt venv 执行：`C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe local_validate.py`
  3. 验证内容：选股管线通不通、候选数量、排序、fail-open 是否生效
  4. 不能本地验证的（真实下单/涨跌停/持仓管理）再上远程
- QMT 产物构建后，应验证：文件存在、GBK 编码、`# coding=gbk` 文件头、Python 3.6 语法、无 MOCK 残留、无长小数输出。
- MOCK 只测信号逻辑，**测不到 QMT 集成层 BUG**（时序/异步/字段/文件/生命周期，约 8 成 bug 在此）；改代码后除 MOCK+单测外，必须过模拟端验证（部署前跑 1 交易日核对关键日志行）。
- 大量/跨文件修复后必须跑测试并如实汇报失败；测试报告记录 warning 类型与来源分类。
- 任务完成必须明确汇报：完成状态、commit、验证结果、遗留尾巴，区分"本任务相关"与"无关历史遗留"。
- 连接测试：`test_connection.py`（外部 Python）、`test_connection_qmt.py`（QMT 内 Python）。

## 实盘调度

```
09:15  盘前准备：检查连接、下载数据、查询账户、撤销遗留委托
09:35  策略执行：计算因子 → 目标组合 → 先卖后买执行
10:05+ 风控巡检：每 30 分钟 — 止损(-8%)、回撤检查(-15%)
13:05  午后检查：验证持仓
14:50  日终处理：对账、保存状态、发送日报
```

## 定时任务模型降级规范（2026-09-14 立）

- **问题**：TRAE 定时任务（自动化）执行时若默认模型卡顿/超时，agent 会话停摆 → 任务失败。
- **官方机制**：Auto 模式（自动挑选可用内置模型）最接近"自动跳模型"，仅支持内置模型；模型管理面板可禁用不稳定模型防默认选中；定时任务本身不支持"指定多模型备选池"（官方文档确认）。
- **工程兜底（本项目已落地，11 个活跃任务已带降级条款）**：所有定时任务 message 必须包含「模型降级条款」——①任一步工具调用超时/报错先重试 1 次（间隔 10 秒）；②重试仍失败且已完成步骤可独立交付则跳过未开始步骤直接结束，记录 `[MODEL-DEGRADE] 步骤N未执行`；③绝不因模型异常重复执行已完成动作（不重复下单/推告警/写文件/跑脚本）；④结束前用 `push_alert_card.py` 推「模型降级执行」人工核对卡（推送失败仅记录）。
- **核心原则**：关键任务必须"脚本优先"——核心动作（换仓/对账/刷新/生成候选）全部下沉为纯 Python 脚本（不依赖 LLM），agent 只做"调脚本+核对+报告"。模型卡住只影响报告层，不阻塞脚本已完成的业务动作。**真实下单任务额外约束**：模型异常发生在下单前则宁可不买也绝不冒险下单，发生在下单后绝不重复下单，如实记录并告警人工核对。
- **大盘风控 T 档已脚本化（2026-09-14 立，`market_tier.py`）**：开盘实时复核的 T 值判断（沪深300 → 空仓/半仓/正常）从"agent 读行情手工判断"改为确定性脚本——复用 `rebalance_g2.load_hs300_pct()`（腾讯行情实时取价）+ `_calc_tier()`（三档门控），数据缺失自动 fail-safe T=1 半仓。**agent 只读脚本输出的 tier 字段作为 --top 参数，禁止自行用其他行情源重算 T 值**（消除模型能力导致的仓位误判）。
- **新建定时任务必须带降级条款**（复制上方标准条款到 message 末尾）；核心交易类任务必须引用已就绪脚本而非让模型自主推理。

## 与实盘策略工程的关系

- 实盘策略工程资产已收拢至 `D:\QuantLab`（原 `D:/QMT_STRATEGIES`，注意真身在 **D:** 不在 E:），侧重实盘策略构建与分层卖出风控。
- **QMT 部署文件不再拷贝到公共目录**：每个项目的 `build/` 子目录自包含 QMT 部署文件（GBK 单文件）；部署由 `qmt_autodeploy.py` 自动写入 `<QMT>\python\<模型名>.py` 并重启客户端核对 BUILD_TAG（见「QMT 红线」，2026-10-28 起明文落盘有效）。
- 实盘与 QMT 共用 `D:/QMT_POOL/` 文件交换目录（预生成 CSV 数据）。
- `SellStrategyEngine` 是核心风控模块，如需复用应通过明确接口调用，不得绕过。
- 修改收拢后的实盘模块前，需遵守本仓库 AGENTS.md 规则。

## 开发原则

- **Git 提交自动化（2026-10-28 诚哥拍板，取代旧的「git 提交前让诚哥确认」）**：改动完成且自测/验证通过后**直接 `git commit`，不必再等拍板**；收尾汇报给出「已提交 + commit hash + 验证结果 + 遗留尾巴」即可。**仍需事先确认的只剩**：`git push`（本项目惯例是本地攒批，master 领先 origin 十几条属常态）、force push / `git reset --hard` / 删分支 / 删生产文件等不可逆动作、真实下单类动作。
- 优先修根因，避免只改构建产物或做表面补丁。
- 保持最小改动，不顺手重构无关模块。
- 修改策略参数、评分权重、风控阈值前，先确认 SPEC、研究文档或用户明确要求。
- 新因子/新策略必须有 IC 分析和回测验证，不能凭直觉上线。
- 数据 PIT 安全不可妥协，任何涉及财务数据的改动必须验证无前视偏差。
- 如需编辑 QMT 生产产物，必须注意编码；默认应改源文件并重新构建。
- 空模块占位（`strategy/`、`backtest/`、`risk/`、`optimization/`、`dashboard/`、`sentiment/`）为规划中功能，填充前先确认架构设计。

## DE（deveco）委派规范

- **DE = DevEco Code CLI**（本机 `C:\Users\Administrator\AppData\Roaming\npm\deveco.cmd`，v0.1.12 实测可用 2026-09-11；**旧路径 `D:\Program Files\npm-global\` 已不存在，勿再引用**），模型 `deveco/glm-5`，华为 oauth 免费额度（历史 12M tokens 费用 $0）。与 opencode 同架构，工具完整（bash/read/write/edit/task/glob）。
- **用途**: 耗 TOKEN 的重活交给 DE（通读大文件做总结、写分析报告、独立跑脚本、批量重写），opencode 只做审核与落盘。
- **调用**: `deveco run "<任务>" --dangerously-skip-permissions --dir D:\QuantLab --format json`；长会话用 `deveco serve` + `--attach`（⚠️ 本机无 E 盘，`--dir` 必须用 D:\QuantLab）。
- **委派必须写明**: ①允许读取的文件清单 ②输出文件路径 ③禁止修改任何其他文件/禁止 commit/禁止修改类命令。
- **DE 产物必须审核**: DE 结论不保证正确（曾把日期标记 bug 的伪差异归因于持仓延续），opencode 需用原始数据独立验证后再落盘，发现错误要纠正 DE 并重写报告。
- **教训**: DE 报告文本引用旧文档时可能把过时结论当结论输出（audit13 报告直接复述了被证伪的归因），审核时以原始数据为准。
- **【强制】DE 工作必须全程盯梢，不得派完就等**: 这是基本流程。派任务后立即确认进程在跑（`deveco.exe` 存活 + 有无审计子进程），并主动向诚哥报告"DE 在跑/空闲"。长时间任务用轮询盯盘（PowerShell 循环查输出文件落盘 + DE CPU 是否增长）。判定卡死的信号：输出文件长时间不落盘 + CPU 墙钟利用率 <5%（如 11 分钟只涨 ~11s CPU）。一旦 2 次轮询确认卡死 → 立即 KillingDE 进程重派，并改用两步委派（第一步只写脚本贴 diff 审核，通过后第二步只运行），不要等 DE 自己超时。DE 进程 `deveco.exe` 从启动 PID 稳定；若 DE 假跑（时间戳变了内容没变）要识破。
- **【经验教训，2026-09-26 P0 enriched 落盘总结】**：DE 委派有明确的适用边界，别把 op 该亲自干的活也甩给 DE。
  - **委派同题失败 2 次即放弃**（硬规则）：本次 P0 四步——`01_design.md`（DE 起草 OK）→ `02_enriched_pipeline.py`（DE 写一次通过）→ `03_refresh_enriched.py`（**DE 连续 2 次委派失败**：假跑/超时/产物不可用，op 放弃委派亲自 322 行）→ `04_tests.py`（未派 DE，op 亲写 470 行 46 项测试）。**同类任务 DE 委派 2 次仍失败就果断放弃**，别在第 3 次浪费 op 的时间（比委派本身更贵）。
  - **DE 擅长的活**：一次性产出 + 单次审核能收敛——设计文档、核心模块骨架、通读大文件做总结、批量重写、独立跑脚本产报告。**产出路径清晰、断言少、跑测回合 ≤ 2 次** 的活。
  - **DE 不擅长的活（op 亲自干更快）**：①「一次写完+多次跑测+逐步收敛」型——测试套件、CLI 驱动脚本、需要看真实报错逐条 debug 的活（本次 `04_tests.py` 首轮 5 项失败定位+修复就是典型，委派出去反而慢）；②「断言密集 + pandas 精算」的活——触板判定/幂等语义/原子写这些口径细节，需要 op 边跑边核对真实行为，DE 委派容易一次写不完且难以二次收敛；③「需要现场交互」的活——文件读取顺序、临时验证小样本、拍板前的探索。
  - **测试环节的分工铁律**：**测试套件必须 op 亲写或本地跑测**，不因 TOKEN 考量委派 DE——本次 `04_tests.py` 首轮 5 项失败里 1 项是**原断言语义错**（误以为「传入不同 df 应触发 rebuilt」，实际幂等语义是「以盘上为准」，传不同 df 仍 skipped 是正确行为），这种「测试意图 vs 代码实际语义」的分歧只有本地跑测能看到，委派出去只会拖 2 轮。
  - **AGENTS.md 委派条款修订原则**：委派规范不是"越委派越好"，而是"在 DE 的能力边界内最大化 TOKEN 效率"——**DE 成功 1 次 / 失败 2 次 = 该放弃**，别硬扛；同类任务连续失败要立刻把经验写回本节，不要重复踩坑。
  - **【两类委派事故，2026-09-29 Project_16 复核实证，均属"任务书本身有缺陷"而非 DE 能力问题】**：
    1. **DE 自行重新界定范围**——第 1 次委派 DE 复核"今天的大修复"，DE 自己改成去审完全无关的 `Project_21_日内做T`，并输出「今天的修复是 P21 Phase 1 整改」的错误结论。**根因：任务书只给了模糊范围（"今天的大修复"）没给可核验的文件清单**。**纪律：委派复核类任务必须写死文件清单（`git diff --stat` 的 6 个文件逐个列出）+ 要求 DE 先回报「我理解的范围是这 N 个文件」再开工**，否则 DE 会自行选题、产出看似专业实则答非所问的报告，且会污染后续所有引用。
    2. **`.cmd` 传参被截断**——第 2 次委派的任务书（含中文长文本）经 `deveco run "<任务>"` 传参被截断，DE 回报"没收到任务内容"。**纪律：含中文或超过约 200 字符的任务书不可用命令行直传，必须写成文件再让 DE 读**（或 `deveco serve` + `--attach` 走长会话）。
    - **共同教训**：这两类失败**都在开工前可预防**（写死文件清单 + 任务书走文件），属于委派前自检缺位，而非 DE 干不了活。**下次委派复核任务前先过这两条**，别用"失败 2 次就放弃"去掩盖本可避免的委派质量问题——"放弃"只应在任务书写对了仍失败时才成立。

## TW（TraeWORK）协作规范

- **TW = TraeWORK**（Trae 工作区，项目隔离 `--project=Traework`），负责策略研究/优化类工作，交付物落在 `D:\Traework\`（HTML 报告 + 对照清单等）。
- **分工**：TW 做研究和扫描（耗 TOKEN 的网格/对比/报告），QuantLab 侧独立审核后落盘——**TW 结论不保证正确，必须以原始数据独立复算验证**（与 DE 审核条款同级；实例：TW 第一批 V1.4 网格漏 `BT_POSFILTER=lite` 误报 +0.029%，复跑修正 +0.157%）。
- **双调度器红线（T-20260910-008 教训）**：TW 有自己的定时任务功能，与 QuantLab 的 schtasks **可能对同一产物文件双注册**（曾发生：V13 成本锚 09:00 双写者，两边同日注册、次日将同时首跑）。**任何一方新建定时任务前，必须互查对方已注册的任务清单**；当前 `D:/QMT_POOL/` 下所有文件的生成调度唯一 owner = QuantLab schtasks。
- **TW 报告的审计方法**：数字逐项独立复算（不引用其输出）、配置声明与实际 env 对照、训练窗口与测试期重叠检查、时间分段复核；审计报告落 `Project_16/results/`。

## 持久记忆（engram MCP）

- **已接入 `engram` 持久记忆**（MCP server：`engram`，二进制 `C:\Users\Administrator\bin\engram.exe` v1.20.0，DB `~/.engram/engram.db`）。WorkBuddy 与 Trae 双客户端均已注册并验证可用。
- **项目隔离**：WorkBuddy 指向 `--project=QuantLab`（大小写归一为 `quantlab`）；Traework 指向 `--project=Traework`。两工作区记忆互不污染。
- **Memory Protocol（agent 必须遵循）**：
  - 会话开始：先 `mem_current_project` 确认项目，再 `mem_search` 检索相关历史，避免重复调查。
  - 做出决策 / 修 bug / 发现约定后：**立即 `mem_save`**（不要等被问）。
  - 会话结束前：`mem_session_summary` 保存目标 / 发现 / 产出 / 下一步。
  - 上下文压缩后：先 `mem_session_summary` 持久化，再 `mem_context` 恢复。
- **中文检索硬约束（实测已验证）**：engram 底层 FTS5 `unicode61` 对中文连续串整体成一个 token，**纯中文查询必然 miss**。所有记忆条目必须采用「**英文锚点标题 + 中文说明正文**」写法（Tushare / moneyflow / Project_16 / QMT / passorder / IC / LightGBM / ATR 等英文锚点天然充足），检索一律用英文锚点。
- **存储铁律（防 MCP 不可见）**：记忆一律经 **MCP 工具**落库（MCP 进程带 `--project` 覆盖，条目正确绑定项目）。**禁止用 CLI 裸 `engram save`**——CLI 裸存不绑定项目（落到 `project=""`），MCP 的 `quantlab` 作用域会把它排除，导致 WorkBuddy 检索不到。已发生一次（id5 游离）并清理。
- engram 是**增量**层，与 WorkBuddy 三层记忆（云 conversation_search / 用户级 MEMORY.md / 工作区日志）并存，不迁移。选型与验证报告见 `Agent记忆工具选型对比_20260904.md`。
