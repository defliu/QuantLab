# 工作流登记与命名约定

> 建立：2026-08-23
> 目的：区分本项目两条工作流，避免混淆；记录近两日（2026-08-22 ~ 08-23）与用户讨论的成果与决策。

---

## 一、工作流命名（用户已确认，务必沿用）

| 工作流名 | 定义 | 当前状态 | 说明 |
|---|---|---|---|
| **原工作流** | 现有 LightGBM 股票大师定时链路：盘后 `xtdata_update → merge_live_features → deploy_predict` 选股 + `run_scheduled.ps1` 定时任务 + 盯盘预警（默认不自动下单） | ✅ **正在运行（当前采用）** | 无人值守但 F2/F3 用离线代理分；盯盘默认仅预警不自动卖出 |
| **QMT自动交易工作流** | 全自动交易链路：QMT 本地粗筛 → 新浪智研（或 TDX）实时复核 F2/F3 → `rebalance_daily --live` 自动换仓 + 六档 `qmt_monitor --auto-sell` 自动止损止盈 | 📥 **备用池（待策略效果验证后启用）** | 用户决策：先按原工作流跑一段时间，若策略效果好，再切换到此工作流 |

> **决策（2026-08-23 用户）**：QMT自动交易工作流先加入备用池，暂不启用。当前继续按原工作流运行。本方案完整细节见 `QMT_AUTO_TRADE_WORKFLOW.md`。

---

## 二、近两日成果与决策记录（2026-08-22 ~ 08-23）

### 1. 部署完成（D:\quant_server）
- 代码、数据、模型已归位；Python 3.10 + 依赖齐备；`data_config.py`/`qmt_config.py` 路径已改本机。
- 完整链路验证通过：数据可读 / 模型加载（v3=1962树）/ 行情通道 / 交易通道(dry-run查持仓) / 选股链路 / 增量链路。
- 无人值守定时任务已建：`quant_daily_update`(交易日16:30)、`quant_weekly_retrain`(周一17:00)、`quant_monthly_factor`(每月1日20:00)、`quant_monitor`(13:00预警版)。
- 修复：LightGBM 4.7 在本机 Windows predict 崩溃 → 降级 4.6.0 解决。
- 修复：`merge_live_features.py` 内存不足 → 改为只读最近 320 交易日。
- 修复：`run_scheduled.ps1` retrain 分支补上"v2→v3 面板生成"一步。
- 增强：`deploy_predict.py` 支持优先用当日快照（`data_live/latest_features.parquet`）预测，自动回退静态面板。

### 2. 硬件评估（8GB 内存机器）
- **周更重训（全量面板重建）在 8GB 上必然崩溃**：实测 concat 阶段触发 `ArrayMemoryError`。训练本身仅需 2-3GB。
- 决策：重训放研发电脑跑，跑完同步（接收脚本 `sync_from_dev.ps1`）。
- 建议：升级 16GB 可让服务器本地重训。

### 3. 审计修复进度（第三方审计，用户批准开工）
- R1（可执行口径回测）：代码完成，待正式面板重跑。
- R2（复权重建面板）：代码完成，已推送 GitHub（commit 见仓库），数据实证除权日收益误差 4.86%→0.03%。待研发电脑重训。
- R3-R8：登记在 `TODO_PENDING.md`。

### 4. 数据源调研结论（关键）
- **QMT 本地 xtdata**：有行情/K线/财务/板块/龙虎榜(本地db，停在2026-07-16)/L2，**无资金流接口、无新闻**。
- **TDX MCP**（远程云服务）：有主力资金流/新闻/公告/研报，免费 1 万积分；但 token 在 Trae 凭据系统，**脚本无法绕过智能体直连**。
- **新浪智研**（zyhub.finance.sina.cn）：免费 API Key 认证可用，覆盖资金趋势/新闻/公告/舆情/两融/北向等；**收费 20 元/1000 次**。用户 Key 已测通认证（余额不足提示，需充值）。
- **Tushare**：`moneyflow` 覆盖 A 股资金流，但用户 Key 非 Tushare 有效 token。

### 5. 决策：原工作流下评分卡数据策略
- F1/F4/F6/F5：QMT 本地实时量价/板块（4 项真实）。
- F2（资金）/F3（新闻）：原工作流继续用**离线代理分**（量比 + 业绩预告/分红事件）。
- 切换 QMT自动交易工作流后，用新浪智研对 Top20 精选做资金+新闻复核（成本约 24 元/月），补齐 F2/F3 实时数据。

### 6. 数据源现状更新（2026-08-27 生效，覆盖 2.4/2.5 旧结论）
- **每日 agent 任务（9:25/9:45/11:35/15:40）已切 MCP 数据源为主**（实证 `data/holdings_daily_20260827.md` / `holdings_daily_20260828.md`）：
  - mcp_tdx（通达信）＝基础行情；iFind（同花顺）＝主力资金（日频）；悟道 mcp_wudao＝公告/龙虎榜/涨停梯队/热榜/题材/盘中主力/指数；腾讯财经 API＝量比/换手/PE/PB/涨跌停价；东财 em＝龙虎榜全自动。
- **无人值守脚本管道（16:30 daily/盯盘/重训）仍走脚本直连**：miniQMT xtdata（每日增量 OHLCV）＋ 新浪资金流（g2 F2）＋ 东财 datacenter/reportapi（龙虎榜/研报）＋ 同花顺881成分自算（F5）。
- 旧结论修正：8/22-8/23「F2/F3 用离线代理分 + 新浪智研备用」已被 8/27 MCP 化取代；新浪智研（zyhub）未充值启用、Tushare Key 未激活，仍按 8/24 清单缺口管理。

---

## 三、切换条件（用户后续可凭此判断）

原工作流 → QMT自动交易工作流 的切换信号（供参考，用户最终拍板）：
1. 原工作流选股在模拟盘验证期内表现符合预期（审计修复 R1/R2 重跑后，可执行口径超额为正）。
2. 新浪智研 Key 充值后复核接口跑通。
3. 用户确认启用自动卖出/自动换仓（真实委托），并接受风险。

切换后仍需保留：QMT 客户端保持登录在线；重训仍走研发电脑同步。

---

## 四、周更重训门禁自动化链路（2026-09-10 建立，T-20260910-002/003）

> 分工方案：**模型层**管"预测质量不退步"（IC/尾部IC），**策略层**管"实盘收益不退步"（官方引擎回测直接比钱），最终裁判为纸面前向三臂累积。
> 背景：09-07 周更坏模型曾被误上线（IC 0.079 过关，策略回测仅 +0.039% < 08-25 的 +0.149%）——IC 涨不等于收益涨，故增设策略层门禁。

### 1. 两条周更路径的门禁流水（周一 17:00 `quant_weekly_retrain`，无人值守）

| 路径 | 模型层门禁 | 策略层门禁（新增） | 失败处置 |
|---|---|---|---|
| **V1.3 周更**（`train_optuna.py` + `auto_promote.py`） | G0~G6：IC下限/不退步/ICIR/分位单调/新面板 | `gate_strategy_layer.py --strategy V1.4 --candidate <新模型> --live <旧正式> --live-meta data/features_v3.json`（fixed/N10/红线58/TOP2+lite 最优配置，面板 v3_sc，27特征可推理） | 拒绝上线；回滚 pre_retrain 备份旧正式模型 |
| **G2 周更**（`train_g2.py --promote`） | G1~G4 strict：同窗口IC/尾部IC/Top2可执行模拟/观测期回滚 | `gate_strategy_layer.py --strategy G2 --candidate <新live> --meta <新meta> --rollback`（live_trail/N15/红线60/TOP2 最优配置） | 拒绝上线；`--rollback` 自动回退 live 指针到 `trial.prev_model` |

### 2. 策略层门禁判定规则

- 候选模型与 live 模型在**同一最优配置**下跑官方引擎回测（`scan_rotate_cost_real.py`，滑点0.1%可执行），比较 `daily_excess`。
- 通过条件：`候选 excess >= live excess - 0.0002`（绝对容差 0.02pp，吸收面板重建噪声）。
- 退出码：0=PASS 放行；2=FAIL 拒绝（`--rollback` 时自动回滚）；1=运行错误（无法评估，拒绝 promote，需人工核查）。

### 3. 关键资产

| 资产 | 说明 |
|---|---|
| `data/strategy_cfg_lib.json` | 候选配置库：三策略各自最优配置 + 面板 + 历史基线（G2=+0.213%/融合=+0.211%/V1.4=+0.157%，均 TOP2 防过拟合口径） |
| `gate_strategy_layer.py` | 策略层回测门禁 CLI（独立进程，复用官方引擎） |
| `run_scheduled.ps1` retrain 分支 | 已接入两条路径的策略层门禁调用 |

### 4. 冒烟验证记录（2026-09-10 全部通过）

| 用例 | 候选 | live | 判定 | 结论 |
|---|---|---|---|---|
| G2-PASS | 08-25 live | 08-25 live | +0.213% vs +0.213% | 放行（口径与历史最优一致） |
| G2-FAIL | 09-07 模型 | 08-25 live | +0.03% vs +0.213% | 拒绝（复现坏模型拦截） |
| V1.4-PASS | 27特征正式 | 同模型 | 一致 | 放行 |

### 5. 门禁升级与回滚说明（无人值守可追溯）

- 模型层门禁升级（G1-G4 strict）与观测期回滚（`rollback_check_g2.py`）见 `VERSIONS.md`「09-09/10 研发沉淀」与 `train_g2.py --gate strict`。
- 每次门禁 FAIL 的日志与原因：G2 走 `data/schedules/retrain_*.log`（含 gate 判定段）；回滚动作写指针 note 字段（`data/g2_live_model.json` 备份 `.bak_gate_*`）。
- **注意**：策略层门禁每次回测约 10 分钟，`quant_weekly_retrain` 总时长相应延长；周一任务窗口 17:00 起预留 40+ 分钟。

### 6. 职责边界（勿混淆）

- 重训（模型层）：找到"给定特征→权重"的最优拟合，周训自动做。
- 策略层优化（过滤/出场/融合/N/红线/TOP）：**重训搜不到**（优化空间与目标函数均不覆盖），只能离线网格寻优 + 前向验证（本轮已完成，结论入 `data/strategy_cfg_lib.json`）。
- 最终裁判：纸面前向三臂累积 30 笔（G2=N15 / v3_enh=N10 / 融合=N10），见 `paper_forward_ab_stats.py` v3。

### 7. 夜间检修自动化（2026-09-10 落地，T-20260910-006）

- 计划任务 `Quant_P16_NightlyCheck_0100`（每日 01:00，入口 `nightly_check_task.ps1`）：A-G 模块体检 + `--fix` 自动修复（面板重刷/增量重下/熔断恢复/QMT_POOL 清理）+ 脚本冒烟 + 卖出规则防回归检查。
- FAIL 时 exit 1（LastTaskResult 可观察）；报告落 `data/cache/nightly_check_<date>.md`；日志 `data/schedules/nightly_check_task.log`。
- 注意：包装脚本**不可设** `$ErrorActionPreference = "Stop"`（PS5.1 下 python stderr 重定向会触发静默终止，T-20260910-006 踩坑记录）。
