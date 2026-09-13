# DE 意见书 P0 全系 + P1-1 执行报告（T-20260913-001 续）

- 日期：2026-09-13 晚
- 任务来源：`data/de_opinion_auto_iteration_20260913.md` ⑥ 行动清单（P0-2 / P0-3 / P0-4 / P1-1）
- 执行范围声明：严格按任务白名单操作——修改 `feature_health_weekly.py`、新建 `paper_quarterly_review.py`、产出下列 data/ 报告；**未修改** rebalance_ens.py / rebalance_g2.py / run_scheduled.ps1 / deploy_predict_g2.py / 任何配置 / 任何模型；无 git 操作；无训练/回测/下单；验证手段 = py_compile + 只读首跑。

---

## 任务一（P0-2）：特征健康周报补 G2 43 特征覆盖 ✅

**改动**（`feature_health_weekly.py`，V1.3 27 特征功能保留并回归验证通过）：
- 新增 `--panel {v3,g2}` 参数（默认 v3，行为不变）；
- G2 模式构建训练口径监控视图（`build_g2_view`）：27 v3 基础窗口读 + 6 enh 慢变量 merge_asof（镜像 train_g2 L77-94，T-20260910-106 口径）+ 10 G2 增强窗口化构建（镜像 train_g2 L96-148，lhb_net/lhb_count/rc_num fillna(0) 与训练同口径）；
- 窗口读 `_read_window`（pyarrow 谓词下推，60 自然日，base 124,570 行）；来源新鲜度表 `_source_freshness`（6 张外部表 max(trade_date) vs 面板最新日）；
- 断源注记：缺失率 ≥99.5% 加「（断源）」，**不改 ALERT/WATCH 判定**（规则与 V1.3 完全一致：ALERT=IC趋零∧缺失>5%，WATCH=二者之一）；
- 产出文件名 `feature_health_weekly_g2_<date>.json/.md`，G2 报告增加分组列（v3基础/enh慢变量/g2增强）与新鲜度表。

**首跑结果**（data/feature_health_weekly_g2_20260913.md，43 特征）：**ALERT 0 | WATCH 5**：
| 特征 | 发现 | 定性 |
|---|---|---|
| **north_chg** | 缺失 100%（断源） | 北向源停更 08-07（落后 35 天），精确 merge 下特征全 NaN——真断源信号 |
| **ind_pct_ths / ind_net_ths** | 缺失 86.79% / 75.00% | 同花顺行业源停更 08-21（落后 21 天） |
| **rc_rating** | 缺失 99.12% | 研报源停更 08-28（落后 14 天）+ 事件稀疏结构性缺失 |
| fin_ocf_to_profit | 缺失 13.34% | 与 V1.3 首跑同发现（已知 WATCH） |

6 个 enh 慢变量首次纳入监控（历史退化点 asof 漂移正是 enh 列），首跑全部 OK（industry_mom20 IC 0.0765、turnover_rank 0.1270）。
**执行中修复**：① research 表日期列为 report_date 且 pandas index 恢复问题（`_read_window` 加 datecol 参数 + reset_index 兜底，同修 hk/ths/moneyflow 同类 index 恢复）；② 常数列 corrwith 的 numpy RuntimeWarning 噪音（局部 suppress）；③ 报告头重复计数。
**验证**：py_compile PASS；G2 首跑 PASS（见上）；**V1.3 模式回归 PASS**（WATCH 1 = fin_ocf_to_profit，与改前一致）；新鲜度表 6 源数值与人工核实逐一相符（mf 0 天 / rc 14 / lhb 21 / ths 21 / board_fundflow 21 / north 35）。

## 任务二（P0-3）：v3_enh 模型迭代链路核实 ✅（只读核实+报告）

**产物**：data/de_v3enh_chain_verify_20260913.md。核心结论：
- **v3_enh 无任何重训/promote 链路**：模型冻结 2026-08-23（21 天，1373 树/33 特征），全库无写入者、调度无挂链（run_scheduled.ps1 L222 只重训 V1.3、L292 只重训 G2；train_optuna `--model-tag _enh` 有能力但无调用方）；
- 消费者清单落实：ENS 生产融合（deploy_predict_g2.py:37 硬编码）、纸面 AB 臂、V1.4 候选门禁 live 基准（gate_strategy_layer.py:136）、4 处研究回测；
- 面板侧已有日更 writer 而**模型侧静态** → mild train-serving skew（industry_mom20 近似口径 corr 0.983~0.9966，已知成文）；
- **附带发现**：features_v3_enh.json 的 feature_cols 与 dropped 有 4 个交集（fc_pchange/dv_year_sum/ex_yoy/ex_days_since），meta 自相矛盾（低危，只登记）；
- 两方案对比：**A 补链周更**（代价高 + ENS 纸面 0 笔到期无切换资格）vs **B 显式拍板冻结为半静态融合锚**（监控兜底已由 P0-2 覆盖 33 特征、下行保护已有降级闸）；**建议 B**，升 A 触发条件写死（季评连续两季最末等）。**裁决归诚哥**。

## 任务三（P0-4）：ENS 对齐 G2 三项差异设计文档 ✅（只设计不改码）

**产物**：data/ens_alignment_design_20260913.md。三项（每项含现状证据/目标/改法/风险/dry-run 验证/独立验收结论）：
1. **预算基数未扣 kept_invest**（rebalance_ens.py:384 vs G2 已修 rebalance_g2.py:443-452）：部分补仓场景总敞口超资金池约 40%，**唯一正在产生实际偏差项**，3 行级改动，可独立验收；
2. **无大盘门控**（ENS 买入段 vs G2 rebalance_g2.py:409-424 三档门控）：同账户三策略唯一无刹车；改法含 config 增 4 常量 + import 复用 load_hs300_pct/_calc_tier + 纸面口径分叉声明；
3. **hold_dates 缺失 fail-open 即卖**（rebalance_ens.py:328-335 vs G2 fail-safe rebalance_g2.py:367-373）：误清仓风险，1 处分支改写，风险最低。
**实施批次建议**：批次 1 = 差异一+三（下批优先）；批次 2 = 差异二；每批走 dry-run 三件套 + 人工拍板 + 部署后 reconcile 核对（实盘保守红线）。

## 任务四（P1-1）：三臂纸面季评脚本 + 首跑 ✅

**新建**：`paper_quarterly_review.py`（Py3.6 兼容：无 f-string/dict[str]/:=；复用 paper_forward_downgrade 的 ARMS/load_open/load_bench/_calendar，与降级闸同口径同源）。
- 季度分组（信号日归自然季）、单季 n≥30 才排名、bootstrap 95% 区间**仅参考**、双季最末且差 >0.0005 才提议（全历史重算，自包含）；
- 臂间重合率 TOP2+TOP10 两口径（Jaccard）+ 非独立性警告（G2/ENS 共享模型）；
- 报告内写死制度声明：**只提议、不动资金分配**（capital_allocation.yaml 硬流程）。

**首跑结果**（data/quarterly_review_20260913.md）：如实标注样本不足——G2 n=8（超额 -0.02423，与降级闸实跑**逐一相符**，交叉验证通过）、V1.3/ENS n=0；无提议（预期，三臂均运行不足一季）；重合率表：G2×ENS TOP2 Jaccard 0.11、G2×V1.3 0.06、V1.3×ENS 0.00（当前重叠低，独立证据价值尚可）。
**验证**：py_compile PASS；--check 预览路径可用；%% 格式化笔误 2 处已修并重跑。

---

## 验证汇总

| 项 | 手段 | 结果 |
|---|---|---|
| feature_health_weekly.py | py_compile + G2 首跑 + V1.3 回归 | PASS / PASS / PASS（WATCH 数与改前一致） |
| paper_quarterly_review.py | py_compile + 首跑 + 数字对拍降级闸 | PASS / PASS / G2 n=8 超额 -0.02423 一致 |
| P0-3 / P0-4 报告 | 引用行号全部实读复核（grep+read） | deploy_predict_g2.py:37、gate_strategy_layer.py:136、rebalance_ens.py:328-335/384、rebalance_g2.py:367-373/409-424/443-452、g2_config.py:95-98 均确认 |
| 时间戳证据 | Get-Item 实测 | 模型 08-23 20:47:39 / 面板+meta 09-12 22:43:30 |

## 遗留（本批白名单外，需另行拍板/执行）

1. **挂调度**：feature_health_weekly `--panel g2` 与 paper_quarterly_review 均未挂 run_scheduled.ps1 / paper_forward_daily.ps1（白名单禁改 .ps1）——建议前者随 retrain 前置与 v3 周报同点位、后者随 16:45 管道尾或季度手跑；
2. **P0-4 批次实施**：按设计文档批次 1（差异一+三）执行代码修复 + dry-run 验收（改 rebalance_ens.py，需新任务授权）；
3. **v3_enh 处置拍板**：方案 A/B 待诚哥裁决（建议 B）；
4. **features_v3_enh.json dropped 交集**：4 个特征删除一行级修复，随下批顺带；
5. **PROJECT_MEMORY.md / 全局控制台.md / repowiki 登记**：本批白名单未含这三个文件，任务记录待另行写入（防「记忆层热、知识库冷」断层）；
6. 意见书 P1-2/P1-3/P1-4（前向底座统一 / M2 显著优通道 / 回滚真实 trial 走通）未在本批范围，仍待办。

*引用行号以 2026-09-13 晚工作区状态为准。*
