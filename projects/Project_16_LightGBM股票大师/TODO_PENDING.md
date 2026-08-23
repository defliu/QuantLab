# 待执行清单

> 本清单记录暂时搁置、需要在合适时机执行的任务。每项含背景与执行方式，任何会话接手均可直接执行。

---

## [进行中] 审计修复（2026-08-22 第三方审计，用户批准开工）

> 开工依据：`TODO_PENDING.md` + `第三方审计报告_20260822.md` + `第三方审计验证报告_20260822.md`（均在本目录）。
> 审计结论：**NO-GO** —— 现有回测证据不足（P0-1 可执行口径下日均超额转负）。按下方修复路线执行，逐项完成后更新状态。
>
> **2026-08-22 用户决策**：重训放研发电脑（内存充足）跑，跑完同步到服务器。服务器端接收脚本已建好：`D:\quant_server\app\sync_from_dev.ps1`（用法见文件头注释）。任何会话接手：等用户同步后运行该脚本归位+校验，再继续 R1 重跑。
>
> **2026-08-23 用户决策**：QMT自动交易工作流加入备用池，当前继续按原工作流运行。工作流命名与完整方案见 `WORKFLOW_REGISTRY.md` / `QMT_AUTO_TRADE_WORKFLOW.md`。
>
> **2026-08-23 本机重跑（研发电脑，正式全市场面板）**：R1 可执行口径正式重跑、R2 复权重训已完成，见下方"已落地成果"。补数据测试发现"回测缺评分信号"，v3_enh 定为当前最优基线（可执行 0.1% 滑点 +0.056%）。

### 修复路线（来自审计报告第六节）

| # | 优先级 | 任务 | 状态 | 说明 |
|---|---|---|---|---|
| R1 | **P0 立即** | 回测改 open→open 口径 + 一字板/停牌过滤，重跑 `scan_rotate_cost` | ✅ **已完成**（2026-08-23 本机正式面板重跑） | 代码已改（--exec/--naive 双口径）；**本机全量重跑时发现并修复 2 个 bug**（见"已落地成果"）。可执行口径结果：未复权模型最优 N=3 0.1%滑点 **-0.108%**（NO-GO），详见 `data/scan_rotate_cost_report.md` |
| R2 | P1 短期 | 复权重建面板（close×adj_factor）并重训 v1/v2/v3 | ✅ **已完成**（2026-08-23 本机） | `build_features_v2.py` 复权版全量重建 475 万行 → 重生成 v3 → 重训。复权有效性验证：新旧标签差异>1% 占 0.17%，最大 131.7%（除权日假跳空消除）。复权后可执行口径最优 N=5 0.1%滑点 **-0.023%**（仍 NO-GO） |
| R3 | P1 短期 | 成交回报回填（query_stock_trades 回填真实成交价/量） | ⏳ 未开始 | qmt_trader/rebalance_daily 日志补状态字段 |
| R4 | P1 短期 | 移动止盈峰值跨日持久化（peak_state.json） | ⏳ 未开始 | qmt_monitor peak 不再每进程重置 |
| R5 | P1 中期 | 特征筛选去泄漏（只用训练期选特征） | ⏳ 未开始 | P1-3 经实证降级为 P2，影响极小但应修 |
| R6 | P1 中期 | 参数选择去数据窥探（预留 2026 纯净验证期） | ⏳ 未开始 | 红线58/TOP2/N=1 需按可执行口径重扫 |
| R7 | P2 持续 | 盯盘回退逻辑修复（load_positions_from_log 扣 SELL） | ⏳ 未开始 | 复用 rebalance_daily 净额逻辑 |
| R8 | P2 持续 | 报告产物自动校验（NaN/信号衰减标记） | ⏳ 未开始 | backtest_dual annual_return NaN 等问题 |

### 已落地成果（2026-08-23 本机重跑）

**R1 修复的 2 个 bug（服务器版脚本）**
1. **持仓收益取价绑定 TOP10 快照**：原用每日选股快照的 `open_next` 盯市，持仓跌出 TOP10 即冻结为 0，导致胜率虚降到 9.7%、回撤 -65%~-95%。改用全市场 `open_map` 逐日盯市后胜率恢复 50.6%。
2. **持有期 N 未生效**：可执行口径卖出条件写死 N=1 的 `>=2`，导致 N=1/3/5/10 结果全相同。改为 `>= N+1` 后各持有期正确分化。

**R2 复权重训**：`build_features_v2.py` 复权版全量重建 v2 → 重生成 v3 → `train_optuna.py --panel v3` 重训（test IC 0.044）。复权有效性：除权日假跳空标签被消除（新旧 fwd_ret 差异最大 131.7%）。

**补数据测试（用户发现"回测缺评分信号"）**：核对 F1-F6 后发现——
- F3 三列（`ex_days_since`/`fc_pchange`/`dv_year_sum`）在 v3 精简面板**缺失**，回测中 F3 退化为二元信号；
- F2/F5 用的是离线代理（量比/相对动量），非真实资金流/板块涨幅；
- 补数据测试结论：F3 单独补入最优 +0.016%（微正）；F3+ind_mom20+换手分位（v3_enh）最优 N=5 0.1% 滑点 **+0.056%**（当前最优基线）；再堆 6 个板块动量特征（v3_enh2）反而崩坏（top2 次日 +0.12%，动量反转+特征冗余）。

**关键洞察（消融实证）**：板块动量是**反转因子**（IC 全为负：ind_mom20 -0.026 / rel_mom_20 -0.052），不是延续因子；单板块特征（ind_mom20）中性无害，堆叠高度相关板块特征会导致模型追高反转偏置（树数骤降到 31）。

### 当前最优基线（2026-08-23 定版）
- **v3_enh**（27 原特征 + F3 三列 + ind_mom20 + turnover_rank，1373 树）
- 面板 `data/feature_panel_v3_enh.parquet` / meta `features_v3_enh.json` / 模型 `D:/QuantLab/models/lgb_model_v3_enh.txt`
- 可执行 open→open 0.1% 滑点 N=5 日均超额 **+0.056%**、胜率 55.1%、回撤 -26.8%
- 判定门槛 +0.3% **未达到**，但方向已从"必然亏"转为"可执行正超额"；后续靠补 F2 真实资金流 + 参数重扫（R6）继续提升

### 注意
- 验证报告确认 P1-3 技术性泄漏存在但实证影响可忽略（训练期与全期 ICIR 判定一致），降级为 P2。
- 审计验证脚本（audit_bias.py/verify_executable.py 等）不在 GitHub 仓库，需重建。

---

## [待定] D:\tmp 数据源归档（用户决策：先放着）

- 状态：**用户暂不处理**（2026-08-22 决定"先放着吧"）
- 背景：用户从原开发机下载了完整数据源到 `D:\tmp`，用于完善服务器数据源、供未来其它策略复用。

### D:\tmp 现有内容（已核验）

| 内容 | 位置 | 规模 |
|---|---|---|
| 日线主库 | `D:\tmp\行情数据更新至2026.8.14\stock_daily.parquet` | 1.24GB，1454 万行，5820 只，到 2026-08-14 |
| 分钟线(1min) | `D:\tmp\行情数据更新至2026.8.14\stock_1min\` | 68.3GB |
| 分钟线(5min) | `...\stock_5min\` | 17.9GB |
| 分钟线(15min) | `...\stock_15min\` | 7.3GB |
| 分钟线(30min) | `...\stock_30min\` | 4.2GB |
| 分钟线(60min) | `...\stock_60min\` | 2.5GB |
| 财务数据 | `D:\tmp\财务数据更新至2026.8.21\`（8 张表） | 0.56GB，到 2026-08-21 |

> 分钟线合计约 99GB（2009-2026 全历史，格式：ts_code+OHLCV+adj_factor，MultiIndex 时间）。

### 后续可选动作（任何会话接手可执行）
- 日线/财务与现有主库一致，无需重复迁移；如需覆盖校验可对比行数与末日期。
- 分钟线按需迁入 `D:\quant_server\astock\minute\`（D 盘当前空闲约 44GB，无法全量放入 99GB；建议只迁 60min+30min+15min 约 14GB，或先腾盘）。
- 待办（未完成）：原 `lgb_model_v3.txt` + 2 个 `feature_panel` 正式资产仍需从原开发机补传（见下方条目）。

---

## [待办] 补传 3 个正式资产（模型 + 2 面板）并恢复选股链路

- 状态：**待用户补传**（2026-08-22 建立；用户出门，回原开发机后传输）
- 背景：2026-08-22 在服务器 `D:\quant_server` 验证「周一重训」链路时，用小样本（`--limit 300` + `--n-trials 2`）跑通了 `build_features_v2.py → 生成 v3 面板 → train_optuna.py --panel v3`，但该次运行**覆盖**了服务器上 3 个正式资产，现为测试版，需从原开发机恢复。

### 需补传的 3 个文件

| # | 文件 | 原开发机来源 | 服务器目标位置 | 大小 |
|---|---|---|---|---|
| 1 | `lgb_model_v3.txt` | `D:\QuantLab\models\` | `D:\quant_server\models\lgb_model_v3.txt` | ~5.7 MB（1962 棵树） |
| 2 | `feature_panel_v2.parquet` | `Project_16\data\` | `D:\quant_server\app\data\feature_panel_v2.parquet` | ~694 MB（全市场，475 万行） |
| 3 | `feature_panel_v3.parquet` | `Project_16\data\` | `D:\quant_server\app\data\feature_panel_v3.parquet` | ~684 MB（全市场） |

> 传输方式：局域网共享（此前用 `\\192.168.31.131\...`）或其它方式，传到服务器后告知路径即可。

### 接收后执行步骤（任何会话接手照做）

```powershell
cd D:\quant_server\app
# 1. 校验：模型可加载（应为 1962 棵树）、两个面板可读（行数应为全市场级）
python -c "import lightgbm as lgb; m=lgb.Booster(model_file='D:/quant_server/models/lgb_model_v3.txt'); print('trees', m.num_trees())"
python -c "import pandas as pd; d=pd.read_parquet('D:/quant_server/app/data/feature_panel_v3.parquet'); print('rows', len(d), 'stocks', d['ts_code'].nunique() if 'ts_code' in d.columns else 'n/a')"

# 2. 重新生成当日快照并重跑选股链路（确认恢复正式模型）
python merge_live_features.py --date 2026-08-21
python deploy_predict.py --model v3 --top-k 5
```

### 验收标准
- `lgb_model_v3.txt` 加载后 `num_trees() == 1962`
- `deploy_predict.py --model v3` 输出正常，数据源显示"当日快照"

### 注意事项
- GitHub 上**无**这 3 个文件（`.gitignore` 忽略 `*.parquet`，模型从未提交），只能从原开发机本地补传。
- 服务器上当前 `lgb_model_v3.txt` / `feature_panel_v2.parquet` / `feature_panel_v3.parquet` 为小样本测试版，恢复前请勿再用其出正式选股结论。
- 飞书推送仍 `not_configured`（lark-cli 登录态未配置），属待办事项，不影响选股/盯盘脚本本身。

---

## [已关闭] 核对模拟盘交易费用与真实费率是否一致

- 状态：**已关闭**（2026-08-22，用户决策）
- 关闭原因：模拟盘只是验证流程，**收益结算统一按实盘交易数据（真实费率）**，无需与模拟盘费率对齐。

### 结算口径（已落地）
统一按实盘费率结算（用户 2026 实测）：佣金 万2 双边最低5元 / 印花税 万5 仅卖出 / 过户费 万0.1 仅沪市双边。已配置在 `qmt_config.py`（COMM_RATE/COMM_MIN/STAMP_RATE/TRANS_RATE），并应用到 `strategy_capital.py` 与 `scan_rotate_cost.py`。

---
