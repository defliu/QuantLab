# P0-3 · v3_enh 模型迭代链路核实报告

- 日期：2026-09-13（执行 DE 意见书 `data/de_opinion_auto_iteration_20260913.md` ⑥ P0-3）
- 性质：只读核实 + 本报告写作。未修改任何生产代码、无 git 操作、未训练/回测/下单。
- 核实对象：`D:/QuantLab/models/lgb_model_v3_enh.txt`（33 特征）——ENS 生产融合与 V1.4 候选门禁的「半边」模型。

---

## ① 一句话结论

**v3_enh 无任何重训/promote 链路**：模型文件冻结于 2026-08-23（21 天），全代码库无写入者、调度无挂链；面板侧（feature_panel_v3_enh.parquet）已有日更 writer（refresh_panel_enh.py，T-20260912-001），但**模型侧完全静态**。ENS 的「50% 权重押在冻结模型上」与 V1.4 候选门禁「live 基准 = 冻结模型」均属实。**两个处置方案对比见④，本报告建议方案 B（显式拍板冻结）**，最终裁决归诚哥。

## ② 核实证据（全部可复查）

### 2.1 模型文件本体

| 项 | 值 | 证据 |
|---|---|---|
| 最后写入时间 | **2026-08-23 20:47:39**（21 天前） | `Get-Item` 实测（2026-09-13） |
| 大小 / 树数 | 2,519,317 字节 / 1373 树 | 文件头 `tree_sizes` 段；VERSIONS.md L155「模型 lgb_model_v3_enh.txt（1373树）+ 面板（33特征）」 |
| 特征数 | 33（max_feature_idx=32） | 模型文件头实测；与 `features_v3_enh.json` feature_cols（33 项）配套 |
| 训练来源 | 2026-08-23 通宵研究一次性训练 | PROJECT_MEMORY 2026-08-23 段；`补数据测试报告_20260823.md` |

### 2.2 全代码库无写入者（grep 实证）

对「写入/重训 lgb_model_v3_enh.txt」的全库检索结果：

- **唯一直写能力**：`train_optuna.py:118` `--model-tag` 参数（docstring 明示「如 _enh」）+ `--panel-file/--meta-file`（L116-117）可组合出 `train_optuna.py --panel-file data/feature_panel_v3_enh.parquet --meta-file data/features_v3_enh.json --model-tag _enh` 直写该文件——**但全库无任何调用方带这组参数**。
- **调度链核实**（`run_scheduled.ps1`）：
  - L222：`train_optuna.py --panel-file data/feature_panel_v3.parquet --meta-file data/features_v3.json --n-trials 20 --model-tag $retrainTag` → **只重训 V1.3（27 特征，产出候选后 auto_promote 到 `lgb_model_v3.txt`）**；
  - L292：`train_g2.py --promote` → **只重训 G2（43 特征）**；
  - **无任何一行调度 v3_enh 重训**。daily L116 的 `refresh_panel_enh.py` 只刷面板不训模型。
- 结论：v3_enh 的重训在 08-23 之后**从未发生过，也无自动发生路径**。DE 意见书④-d-2 的怀疑「若 v3_enh 自 08 月研究定稿后从未重训」**属实**。

### 2.3 消费者清单（谁押在这个冻结模型上）

| 消费者 | 位置 | 性质 | 影响面 |
|---|---|---|---|
| **ENS 生产融合**（50% 权重） | `deploy_predict_g2.py:37` `AB_MODEL = "D:/QuantLab/models/lgb_model_v3_enh.txt"`（硬编码路径，非指针）；L252 `--ensemble` 分支加载 | **实盘**（70180771 ENS 桥 10 万子账户） | 模型冻结 = ENS 半边永远不迭代 |
| **纸面 A/B 臂（V1.3 ab_v3enh）** | 同上 L214 `--ab` 分支 | 纸面 | V1.4 决策证据链的数据源 |
| **V1.4 候选门禁 live 基准** | `gate_strategy_layer.py:136-137` `live_model = DC.model_file("_v3_enh")` | 门禁基础设施 | 「候选 ≥ live」的 live 是冻结模型；门禁语义随冻结时间衰减（候选只需不劣于 08-23 的模型） |
| 研究回测 | `scan_rotate_cost_real.py:36`、`optimize_scorecard.py:37`、`research/jq_metrics.py:46,54`、`strategy_cfg_lib.json` L41-42,58-59 | 研究 | 回测口径依赖（可接受，回测本就应冻结口径） |

### 2.4 面板侧 vs 模型侧的半新半旧（train-serving skew 现状）

- 面板：`refresh_panel_enh.py`（唯一 writer）每日 16:45 刷新，当前已到 **2026-09-11**（4,807,456 行；`features_v3_enh.json` refreshed_at 2026-09-12 22:43:30）。
- 模型：训练于 2026-08-23 的**原始口径面板**（industry_mom20 为原始 08-23 vintage 口径）。
- **skew 已被记录且属可控**：现行面板 industry_mom20 为近似口径（corr 0.983~0.9966，`features_v3_enh.json` L62 注记；T-20260912-001 考古结论）。生产推理吃新面板、模型学自旧口径，构成 **mild skew**——已知、成文、逐行一致（非随机噪声）。若重训 v3_enh，此 skew 消除但模型行为改变（新口径进树），**换模型的风险不低于留 skew**。

### 2.5 附带发现：meta 文件自相矛盾（低危，登记待修）

`features_v3_enh.json` 的 `feature_cols`（33 项）与 `dropped`（11 项）**有 4 个交集**：`fc_pchange`、`dv_year_sum`、`ex_yoy`、`ex_days_since` 同时出现在保留列表（L34-37）与剔除列表（L44,48,49,51）。成因推断：dropped 从 `features_v3.json`（27 特征集）原样拷贝，未删去 enh 加回的 4 个事件特征。**实际生效口径以 feature_cols 为准（33 特征，与模型配套无误）**，但任何按 dropped 做差集/审计的下游逻辑会被误导。修复属一行删除操作，建议随下批顺带（本报告只登记不改文件）。

## ③ 「ENS 能自动迭代吗」的最终回答

**只有一半能**：G2 半边随周一 `train_g2 --promote` 全自动滚动；v3_enh 半边是**硬编码静态文件**（deploy_predict_g2.py:37 非指针），冻结 21 天且无迭代路径。V1.4 候选门禁同理押在冻结基准上。DE 意见书②矩阵「模型层 ENS=半边断链」的判断**核实属实**。

## ④ 两个处置方案对比（裁决归诚哥）

### 方案 A：补链——v3_enh 指针化周更

| 维度 | 内容 |
|---|---|
| 做法 | 周一 retrain 增步：`train_optuna --panel-file <enh面板> --meta-file features_v3_enh.json --model-tag _enh_cand_<date>` → 对齐 strict/双门禁（live=v3_enh 当前版）→ promote 指针；`deploy_predict_g2.py` AB_MODEL 改读指针（对齐 G2 的 g2_live 指针机制） |
| 收益 | 三策略模型层能力对称（三边都自动滚动）；消除 2.4 的 mild skew；V1.4 门禁 live 基准恢复时效语义 |
| 代价/风险 | ① **实盘影响**：ENS 桥 10 万真金押在融合上，换 v3_enh = 换半个大脑；纸面前向融合臂当前 **0 笔到期样本**（paper_forward_ens.csv 09-09 起 32 行，无到期），按 P3 制度（≥30 笔纸面证据）**无资格切换**；② **训练口径风险**：新面板 industry_mom20 是近似口径，重训后模型与 08-23 研究结论（V1.4 决策证据 T-20260909-005：v3_enh TOP2 可执行 +0.0298）的连续性断裂，需重跑等价性验证；③ V1.4 门禁 live 基准随之漂移，候选资格语义改变；④ 每周一多一条重训链 = 多一个故障面（对齐 G2 的门禁/回滚/观察期全套需同步建） |
| 工作量 | 高（重训链 + 门禁 + 指针 + 回滚 + 观察期 + 等价性验证，约等于复刻一条 G2 链） |

### 方案 B：显式拍板冻结——「ENS = 半静态融合锚」

| 维度 | 内容 |
|---|---|
| 做法 | ① 拍板文档化：VERSIONS.md 融合行 + 本报告归档「v3_enh 冻结为融合锚，不参与周更」；② 监控兜底已就位：**P0-2 落地的 G2 特征健康周报（feature_health_weekly --panel g2）恰好覆盖 v3_enh 全部 33 特征**（27 v3 基础 + 6 enh 慢变量），冻结模型的输入特征退化现在可见（首跑已见 fin_ocf_to_profit 13.34% 缺失 WATCH）；③ 下行保护已就位：纸面降级闸 ENS 臂（paper_forward_downgrade.py，n≥30 且超额 <-0.0002 → 只卖不买）守住冻结模型失效的下限；④ 加一条**年度人工复查**入口（纸面季评若连续两季 ENS 臂最末 → 触发「锚是否还配 50% 权重」的人工议题） |
| 收益 | 零实盘风险、零新故障面；半静态锚是合法设计（融合的另一半 G2 持续滚动，ENS 整体并非静态——「锚 + 动态」结构常见于指数增强）；把「要不要迭代锚」的决策推迟到**有纸面证据的时点**（季评），而不是现在拍脑袋 |
| 代价/风险 | ① 若 regime 长期漂移，锚持续老化——但降级闸保证失效时刹车，季评保证劣化被看见；② 「V1.4 门禁 live 基准冻结」的语义衰减持续存在（V1.4 尚未上线生产，影响限于候选评估口径，纸面期可接受） |
| 工作量 | 低（文档 + 季评挂钩，监控/降级闸均已有） |

### 建议

**先 B 后 A**：当前 ENS 纸面 0 笔到期、融合上线仅 3 天（09-10），任何换锚动作都没有证据资格；而 B 方案的两个兜底（特征监控 + 降级闸）今天已经落地。**触发升 A 的条件**（写死，防随意）：纸面季评 ENS 臂连续两个季度排名最末且与最优差超阈值（P1-1 季评的提议条件），或 G2 周更持续 PASS 而 ENS 纸面臂显著劣于 G2 臂（同源证据，见季评臂间重合率解读）。届时再按 A 的完整清单补链，且必须先攒 30 笔纸面前向证据。

## ⑤ 红线对照

- 本报告未触碰实盘配置/资金分配（AGENTS 资金分配红线）；
- 「先纸面后实盘」（P3 制度）：方案 A 被明确约束在 30 笔纸面证据之后；
- 季评只提议不动资金（DE 意见书④-b 风险条）：本报告的升 A 触发条件同样只产生「人工议题」，不自动执行。

---

*引用行号以 2026-09-13 晚工作区状态为准；模型文件/面板/meta 时间戳均为本机实测。*
