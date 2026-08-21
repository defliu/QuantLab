# 股票大师 · 工作流与部署手册（v1.0）

> 项目：Project_16 LightGBM 股票大师
> 更新：2026-08-21
> 目标：TraeWork 选股 → miniQMT 执行 → 盯盘，全链路可部署、可复用
> ⚠️ **接手必读**：交易链路所有踩坑与避坑规则见 `QMT避坑指南.md`（QMT 持仓判断/成交记录/委托状态/飞书推送/账户纪律）；完整知识库见 Obsidian `60_工程知识库`。

---

## 一、系统概览

```
┌──────────────────────────────────────────────────────────────┐
│  数据层                                                        │
│   E:/astock 主库（周更权威快照，只读）                           │
│   data_live/ 临时增量库（miniQMT xtdata 每日拉取，独立）          │
└──────────────────────────┬───────────────────────────────────┘
                           │ merge_live_features.py（合并视图，主库不写）
┌──────────────────────────▼───────────────────────────────────┐
│  模型层（LightGBM v3，27 特征）                                 │
│   特征面板 feature_panel_v3.parquet → lgb_model_v3.txt         │
└──────────────────────────┬───────────────────────────────────┘
                           │ deploy_predict.py（全市场打分 → TopN 候选）
┌──────────────────────────▼───────────────────────────────────┐
│  双轨选股                                                     │
│   模型分排序 × F1-F6 评分卡红线 → 候选                          │
│   review_full.py + TDX MCP 实时复核（F2资金/F3催化/F5板块）      │
└──────────────────────────┬───────────────────────────────────┘
                           │ qmt_trader.py（miniQMT 委托）
┌──────────────────────────▼───────────────────────────────────┐
│  执行与盯盘                                                    │
│   qmt_trader.py 下单 → qmt_monitor.py 止损/止盈/移动止盈预警     │
└──────────────────────────────────────────────────────────────┘
```

## 二、目录结构

```
Project_16_LightGBM股票大师/
├── build_features.py        # 阶段0：v1 量价特征面板
├── build_features_v2.py     # 阶段4：v2 量价+财务+事件特征面板
├── train_baseline.py        # 阶段1：基线训练 + IC 评估
├── train_optuna.py          # 阶段2：OPTUNA 调参（--panel v1/v2/v3）
├── deploy_predict.py        # 阶段3：双轨选股（--model v1/v2/v3）
├── review_full.py           # 阶段3b：完整版实时复核（读 tdx_review.json）
├── factor_ic_monitor.py     # 阶段5：因子 IC 月度监控 + 回灌
├── scan_topk.py             # 持仓数量扫描回测（Top1~6 对比，定最优持仓数）
├── backtest_dual.py         # 双轨选股样本外回测
├── rolling_eval.py          # 季度滚动训练评估
├── xtdata_update.py         # 增量行情拉取 → data_live（主库不写）
├── merge_live_features.py   # 合并视图 → 最新日特征快照
├── qmt_config.py            # QMT 配置（路径/账号/风控参数）
├── qmt_trader.py            # miniQMT 委托下单（--equal 均分/dry-run/live）
├── qmt_monitor.py           # 盯盘：止损/止盈/移动止盈预警+自动卖出+飞书推送
├── qmt_clear.py             # 清仓工具（--keep 保留 / --live 实盘）
├── qmt_mini_test.py         # 全链路最小测试单
├── strategy_capital.py      # 策略资金池计算（初始10万+已实现盈亏+持仓浮盈）
├── data_config.py           # 数据/模型/输出路径集中配置（部署只改此文件）
├── run_scheduled.ps1        # 定时任务调度器（daily/monitor/retrain/factor）
├── QMT避坑指南.md           # 交易链路踩坑与避坑规则（接手必读）
├── data/                    # 特征面板/模型报告/选股清单
│   ├── feature_panel*.parquet
│   ├── features*.json       # 特征集定义（v1/v2/v3）
│   ├── selections/          # 每日选股结果（dual/full）
│   └── *report.json/md      # 各类研究报告
├── data_live/               # 临时增量库（独立，可重建）
│   ├── incremental_daily.parquet
│   ├── latest_features.parquet
│   └── update_meta.json
└── WORKFLOW_DEPLOY.md       # 本手册
```

## 三、环境与依赖

- Python 3.10（TraeWork 环境）
- 包：`lightgbm pyarrow scikit-learn scipy optuna optuna-integration`
- miniQMT 客户端：`E:\国金QMT交易端模拟`（必须运行并登录，xtdata/xttrader 依赖）
- xtquant 路径：`E:\国金QMT交易端模拟\bin.x64\Lib\site-packages`（qmt_*.py 已自动 append）
- 数据：`E:/astock`（主库，周更）、`D:/QuantLab/models`（模型输出）
- 交易账号：qmt_config.py 的 ACCOUNT_ID（当前 67014907 测试账号，模拟盘）

## 四、数据管线（主库只读原则）

```
E:/astock（周更快照） ──只读──┐
                              ├─→ merge_live_features → latest_features → 模型预测
data_live（每日增量）─────────┘   （主库永不修改，临时库可重建/丢弃）
```

- `xtdata_update.py`：拉主库最后日后到今天的增量日线（需 miniQMT 在线，先 download_history_data 再读取）
- `merge_live_features.py`：合并主库+增量 → 重算最新日量价特征（真实）+ 换手/量比/估值（主库近似）+ 财务/事件（面板 asof）
- 局限：增量只有 OHLCV/成交额，换手/量比/估值字段用主库最后值近似；候选可再用 TDX 实时精确化

## 五、每日工作流（命令序列）

### 盘后（15:30 后，主库周更日则先更新主库）
```bash
# 1. 拉当日增量到临时库（需 miniQMT 在线）
python xtdata_update.py

# 2. 合并生成最新日特征快照
python merge_live_features.py --date <最新交易日>

# 3. 模型选股 → 次日 TopN 候选
python deploy_predict.py --model v3 --top-k 10
```

### 开盘后（9:30+，资金流已产生）
```bash
# 4. TDX 实时复核（agent 通过 TDX MCP 采集资金/新闻/板块 → data/tdx_review.json）
python review_full.py --candidates data/selections/<最新>_model_top10.csv
```

### 委托与盯盘
```bash
# 5. 买入（策略资金池，--equal 均分，默认 dry-run，--live 需显式）
python strategy_capital.py                                  # 计算当前策略资金池（初始10万+已实现+浮盈）
python qmt_trader.py --plan data/selections/<最新>_selection_full.csv --total <资金池> --equal
python qmt_trader.py --plan ... --total <资金池> --equal --live   # 真实买入

# 6. 盯盘（止损-7%/止盈+15%/移动止盈8%，持仓优先 QMT 实时查询）
python qmt_monitor.py --once --auto-sell                   # 单次检查+触发自动卖出
python qmt_monitor.py --positions "603969.SH:7.10:1000"    # 手动指定持仓

# 7. 全链路验证（最小测试单）
python qmt_mini_test.py --code 603969.SH --vol 100
```

### 周期维护
```bash
# 主库周更后：重建面板 + 重训（约 1 小时）
python build_features_v2.py
# 生成 v3 面板（从 v2 选列）
python -c "import json,pandas as pd; df=pd.read_parquet('data/feature_panel_v2.parquet'); m=json.load(open('data/features_v3.json')); df[m['feature_cols']+['trade_date','ts_code','label','fwd_ret']].to_parquet('data/feature_panel_v3.parquet')"
python train_optuna.py --panel v3 --n-trials 20

# 月度因子监控 + 回灌
python factor_ic_monitor.py
```

## 六、脚本速查表

| 脚本 | 作用 | 关键参数 | 依赖 | 产物 |
|---|---|---|---|---|
| xtdata_update.py | 增量行情拉取 | --start/--end/--limit | miniQMT 在线 | data_live/incremental_daily.parquet |
| merge_live_features.py | 合并视图→最新特征 | --date | 主库+临时库 | data_live/latest_features.parquet |
| deploy_predict.py | 双轨选股 TopN | --model v1/v2/v3, --top-k | 面板+模型 | data/selections/*_model_topN.csv |
| review_full.py | 完整版实时复核 | --candidates, --review | tdx_review.json | data/selections/*_selection_full.* |
| strategy_capital.py | 策略资金池计算 | 无 | 成交记录+QMT | data/strategy_capital.json |
| qmt_trader.py | 委托下单 | --plan, --total, --equal, --live | miniQMT 在线 | 成交记录 csv |
| qmt_monitor.py | 盯盘预警+自动卖出 | --once, --auto-sell | miniQMT 在线 | qmt_signal.json |
| qmt_clear.py | 清仓 | --keep, --live | miniQMT 在线 | 成交记录 csv |
| qmt_mini_test.py | 最小测试单 | --code/--vol | miniQMT 在线 | 验证+记录 |
| factor_ic_monitor.py | 因子IC监控 | --panel/--meta | 面板 | factor_ic_report.* |
| scan_topk.py | 持仓数量扫描回测 | 无（可改 TOP_KS） | 面板+模型 | scan_topk_report.json/md |

## 七、模型与关键产物

- 模型：`D:/QuantLab/models/lgb_model_v3.txt`（27 特征，最优）
- 特征集：`data/features_v3.json`（剔除 11 个失效因子后的 27 特征）
- 面板：`data/feature_panel_v3.parquet`（475 万行 × 27 特征，2019-2026）
- 回测：`data/backtest_dual_report.json`（胜率 54%、盈亏比 1.38、日均超额 0.53%）
- 滚动：`data/rolling_eval_report.json`（ICIR 1.40、95% 季度正 IC）
- 因子健康：`data/factor_ic_report.md`

## 八、部署到服务器的注意事项

1. **miniQMT 必须在目标机运行登录**：xtdata/xttrader 走本地进程间通信，服务器需装国金 QMT 客户端并保持登录
2. **路径配置**：修改 `qmt_config.py` 的 QMT_PATH/USERDATA/ACCOUNT_ID 指向服务器实际路径/账号
3. **数据挂载**：E:/astock 主库与 data_live 需在服务器可访问（可拷贝或挂载）；模型目录 D:/QuantLab/models 同理
4. **Python 环境**：服务器装 Python 3.10 + 依赖；xtquant 用 QMT 自带 site-packages（cp310 的 pyd）
5. **TDX 采集**：review_full 的 tdx_review.json 由 agent 通过 TDX MCP 填充（服务器会话需 TDX 授权）
6. **定时任务**：部署后可在服务器配 cron/计划任务，或在 TraeWork 配 Schedule（见下节）

## 九、交易策略与仓位规则（已启用）

- **策略资金池**：账户约 1000 万，但策略只用 `START_CAPITAL=100000`（10 万）建仓；资金池 = 初始 + 已实现盈亏（成交记录 FIFO 配对）+ 策略持仓浮盈（QMT 查询，查不到保守按 0）。`strategy_capital.py` 计算并写 `data/strategy_capital.json`，收益滚动进池子用于加仓。
- **买入（方案A·每日换仓，2026-08-21 上线）**：持仓永远等于当日评分最高的 top T（大盘正常 T=2 / 降半仓 T=1 / 破位 T=0 空仓）。9:45 复核生成当日完整版清单 → `rebalance_daily.py --date <日> --top <T> --live` 自动换仓：卖"不在目标 top 内且今日可卖"的持仓（T+1 锁定不卖）、买"目标内未持有"的票（等权）。依据：含真实成本回测 `data/scan_rotate_cost_report.md`（每日换仓超额最优 +0.32%~+0.68%）、`data/scan_newsc_report.md`。不设轮动阈值、不设一票否决；价格风控（止损/止盈/移动止盈）由六档盯盘负责。
- **F6 估值口径（审计修复 2026-08-21）**：F6 优先用实时 PE（`tdx_review.json` 的 `pe_ttm`），PE<0 或 PE>100 得 1 分；**不设一票否决**（新体系回测 `data/scan_newsc_report.md` 证明：否决 PE 极端票会降低胜率/盈亏比/超额且放大回撤，模型选出的高 PE/亏损票多为正贡献）；无实时 PE 时回退面板 `SC_F6`。
- **仓位**：常态总仓 95%（留 5% 交易缓冲）、2 只均分（每只约资金池/2 ≈ 4.75 万）、单票上限 50%；**已有持仓抵扣**（买入只补到目标数，避免叠加超配）。
- **大盘风控**（沪深300 当日涨跌）：`<= -1.5%` 空仓不买；`-1.5% ~ -1.0%` 降半仓（目标持仓降为 1、金额 50%）；`> -1.0%` 正常满仓（目标 2 只）。
- **盯盘风控**：止损 -7% / 止盈 +15% / 移动止盈 8%（高点回撤），`qmt_monitor --auto-sell` 触发即自动卖出；持仓优先 QMT 实时查询（`query_stock_positions`），非交易时段查询可能为空时回退本地成交记录。

## 十、定时任务（已配置，2026-08-21）

> **交易日防护（2026-08-21 新增，所有任务的第一道闸）**：A股交易时间已锁死。所有 TraeWork 任务 message 第一步必须运行 `python is_trade_day.py`（在项目目录）判断今天是否交易日，非交易日（周末/法定节假日）直接结束、不执行任何操作；`run_scheduled.ps1` 同样内置该判断（所有 Windows 计划任务模式，非交易日跳过）。判断逻辑：周末必休 → 主库真实交易日历（`data/ashare_trade_dates.txt`）→ 2026 法定节假日休市表（元旦/春节/清明/五一/端午/中秋/国庆，来源：沪深北交易所休市安排公告）。**每年 1 月需更新 `is_trade_day.py` 的节假日表。**
> 明日 2026-08-22（周六）休市，所有任务不会触发。

### TraeWork Schedule（agent 任务，需 TDX MCP / 飞书 bot）

| 任务 | cron | 内容 |
|---|---|---|
| 集合竞价增强预判(项目16) | 25 9 * * 1-5 | 竞价行情+隔夜新闻+板块+资金 → 持仓预警等级+候选强弱，飞书推摘要 |
| 开盘实时复核(项目16) | 45 9 * * 1-5 | 大盘风控定 T → 方案A每日换仓（`rebalance_daily.py --live`，卖被PK+买新晋top，自动下单），飞书推结果 |
| 午休持仓报告(项目16) | 35 11 * * 1-5 | 逐股持仓详细情况+分析（行情/资金/新闻/浮盈亏/操作建议）→ 报告+飞书推摘要 |
| 盘后持仓复盘(项目16) | 40 15 * * 1-5 | 全天持仓复盘（当日表现/浮盈亏/资金/新闻/次日建议）→ 报告+飞书推摘要 |

### Windows 计划任务（纯脚本，走 run_scheduled.ps1）

| 任务 | 时点 | 内容 |
|---|---|---|
| Quant_Monitor_0945 / 1030 / 1100 / 1330 / 1400 / 1430 | 交易日 | qmt_monitor --once --auto-sell（六档盯盘，触发自动卖出+飞书） |
| Quant_Daily_Update | 交易日 16:30 | xtdata_update → merge → deploy_predict（盘后增量+选股） |
| Quant_Weekly_Retrain | 周一 17:00 | build_features_v2 → train_optuna --panel v3（周更重训） |
| Quant_Monthly_Factor | 每月1日 20:00 | factor_ic_monitor（因子监控） |

> run_scheduled.ps1 -Mode daily|monitor|retrain|factor；日志在 data/schedules/。

## 十一、飞书推送机制（已验证连通）

- **通道**：`lark-cli im +messages-send`，**bot 身份**，私聊发送给用户 `ou_76deaecde50e10576f8fdc8ba954a7b0`（刘诚，已测试连通）。不要用 user 身份、不要发到其他 open_id（会 cross-app 报错）。
- **关键环境变量处理（必须）**：调用 lark-cli 前，在其进程环境里**移除 `LARKSUITE_CLI_APP_ID` 和 `LARKSUITE_CLI_USER_ACCESS_TOKEN`**，并设置 `LARKSUITE_CLI_STRICT_MODE=off`。原因：外部注入的 app 只有 user token 且 strict-mode=user 会挡 bot；移除后 lark-cli 回退到 config.json 里的 Trae app（cli_aa0fbe282c399cef，有 bot 凭据）。
- **lark-cli 路径**：`C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\1.0.4\bin\lark-cli.exe`
- **推送内容约定**：9:25 预判每次推摘要（持仓等级+候选强弱，强预警重点标出）；9:45 推买入结果（大盘结论+买入清单/未买原因）；盯盘脚本 `qmt_monitor.notify_feishu()` 触发信号时推送（无信号推送"持仓正常"心跳）。
- **优先级与容错（必须遵守）**：飞书推送**永远放在任务所有主步骤之后**执行，优先级最低。推送失败/异常时**仅记录**（日志/汇报中说明），**绝不因推送失败中断、重试或阻塞主流程**——交易、报告等主任务完成即为成功。脚本层 `notify_feishu()` 已 try-except 容错，失败只打印不影响主循环。
- 脚本内实现见 `qmt_monitor.py` 的 `notify_feishu()`（已内置环境变量处理）。

## 十二、常见问题

- **miniQMT 未登录/离线**：xtdata/xttrader 连接失败 → 需人工登录客户端后重跑
- **xtdata 拉不到增量**：先 `download_history_data`（脚本已内置）；检查区间是否含交易日
- **主库滞后**：快照到 8/14，用 data_live 增量 + merge 得到最新特征
- **盘前实时数据为空**：资金流/量比/板块要 9:30 开盘后才有，盘前只能查新闻
- **TDX 查询无结果**：如实记录"无数据"，禁止编造（金融数据红线）
- **飞书 bot 推送失败或发错人**：必须移除 `LARKSUITE_CLI_APP_ID`/`LARKSUITE_CLI_USER_ACCESS_TOKEN` 并设 `LARKSUITE_CLI_STRICT_MODE=off`，用 bot 身份发 `ou_76deaecde50e10576f8fdc8ba954a7b0`（见第十一节）
- **持仓查询非交易时段返回空**：`query_stock_positions` 不稳定，盯盘自动回退本地成交记录；`strategy_capital.py` 浮盈按 0 保守计（已内置重试）
- **清仓记录价格 0 污染盈亏**：市价清仓 SELL 价格可能为 0，`strategy_capital.py` 已跳过 price<=0 的记录
- **QMT 持仓列表残留（孤儿持仓，已实测）**：当天卖出的票，QMT 持仓列表仍会显示该股但 `volume=0`（次日才清除）。**判断持仓必须以持仓数量 >0 为准，vol=0 即已卖出**，绝不能只看"列表里有"就当作持仓。`qmt_monitor.py`/`strategy_capital.py`/`qmt_clear.py` 均已按 volume>0 过滤；卖出后需在 `qmt_trade_log.csv` 补 SELL 记录（否则"已有持仓数/已实现盈亏"会误判）。知识库参考《QMT孤儿持仓与撤单不拉黑》。

---
*仅供个人量化研究使用，不构成投资建议。市场有风险。*
