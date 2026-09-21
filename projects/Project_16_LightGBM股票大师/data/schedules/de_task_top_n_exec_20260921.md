# DE 任务书：P16 三策略持仓只数（TOP_N）实盘参数上调执行（T-20260921-003 落地方案）

> 任务号：de_task_top_n_exec_20260921
> 日期：2026-09-21
> 背景：诚哥拍板「把三个策略都改了」。依据《持仓只数最优论证_20260921.md》（fixed 官方口径网格）+ 《DE_R8_持仓数与买入时刻评估_20260918.md》（live_trail 口径），两口径交叉一致证实 **K=2 非最优**，本任务按论证报告建议值落地实盘参数。

## 一、目标值（唯一权威，以本表为准）

| 策略 | 配置文件 | 行 | 旧值 | 新值 | 依据 |
|---|---|---|---|---|---|
| G2 | `g2_config.py` | L30 `TOP_N` | 2 | **3** | 论证报告：G2 最优 TOP3（夏普 2.15 全档最高、回撤回 15% 红线内、2026 段 +40.5%；TOP≥4 断崖） |
| 融合 ENS | `g2_ens_config.py` | L32 `TOP_N` | 2 | **5** | 论证报告：融合建议上调至 5（求稳先到 4~5 观察，取 5） |
| V1.3 | `rebalance_daily.py` | L46 `TOP_N` | 2 | **5** | 论证报告：V1.3 建议 4~5（回测最优 TOP6、稳健次选 TOP5，取 5） |

## 二、允许读取的文件清单（只读，用于理解上下文）

- `g2_config.py`、`g2_ens_config.py`、`rebalance_daily.py`
- `rebalance_g2.py`、`rebalance_ens.py`（确认 `n_slots = G.TOP_N - len(kept)` 自动跟随，无需改动）
- `results/持仓只数最优论证_20260921.md`、`results/grid_top_n_20260921.csv`
- `results/DE_R8_持仓数与买入时刻评估_20260918.md`、`results/R8_独立复算审核报告_20260919.md`

## 三、允许修改的文件（仅限以下，且只改 TOP_N 数值与其直接关联的纸面统计口径）

### A. 实盘核心（必须改，3 处）
1. `g2_config.py` L30：`TOP_N = 2` → `TOP_N = 3`（保留原注释说明，可更新注释为「2026-09-21 拍板 TOP_N 2→3」）
2. `g2_ens_config.py` L32：`TOP_N = 2` → `TOP_N = 5`
3. `rebalance_daily.py` L46：`TOP_N = 2` → `TOP_N = 5`

### B. 纸面前向统计口径（必须同步改，否则统计与实盘口径脱节；只改 rank 阈值数字与相关注释中的数字/说明）
- `paper_forward.py`：L163 `rank <= 2`（TOP2子集）→ `rank <= 3`；L114-115 注释中「实盘 g2_config.TOP_N=2」「rank<=2」同步更新为 3
- `paper_forward_exit.py`：L232 `rank <= 2` → `rank <= 3`；L236 打印文案 rank<=2 → rank<=3；L407 `--top default=2` → `default=3`；L7/L21/L316/L349 等注释/文案中的「2」「TOP2」「持仓2」「rank<=2」同步更新为 3（仅文字描述，勿改逻辑）
- `forward_stats.py`：L107 `rank <= 2` → `rank <= 3`；L5 文档字符串「TOP2（实盘口径，g2_config.TOP_N=2）」更新为 3
- `paper_forward_downgrade.py`、`paper_forward_ab_stats.py`：**只检查不改**，若内含 rank<=2 实盘口径阈值，在报告中列出位置，不要修改（待 TRAE 确认是否属 G2 口径）

### C. 只检查不改（报告中报告，禁止修改）
- `deploy_predict_g2.py`（L209 注释提到 TOP_N=2，仅注释，可不动；如需更新注释请列出，但不改）
- `versions/` 目录下所有历史版本快照：**绝对禁止修改**
- `data/real/` 下历史预判文档：禁止修改
- `capital_allocation.yaml`：**禁止修改**（本次只改持仓只数，额度 10 万/策略不变，Σ=50 万 ≤ 1000 万约束不受影响）

## 四、禁止事项（硬约束）

- 禁止 commit / push / 任何 git 写操作
- 禁止修改除「第三节 A/B 列出」之外的一切文件
- 禁止运行任何真实下单 / 部署 / 构建 / 定时任务脚本
- 禁止改动 `scan_rotate_cost_real.py`、`data_config.py`、`broker/`、QMT 桥文件
- 禁止把 `TOP_N` 值改成目标值以外的任何数字

## 五、执行步骤

1. 读取第三节 A 的三个配置文件，确认行号与旧值（用 grep 定位，勿凭记忆）
2. 逐一修改 3 处 TOP_N（A 组）
3. 修改 3 个纸面统计脚本的 rank 口径（B 组）
4. 检查 C 组：`deploy_predict_g2.py`、`paper_forward_downgrade.py`、`paper_forward_ab_stats.py` 是否含其他实盘口径硬编码（rank<=2 / TOP_N=2 / 持仓2），只记录位置不改
5. 全局 grep `TOP_N = 2` 与 `rank <= 2`（排除 `versions/`），确认无遗漏实盘消费点，报告清单
6. 语法检查：`python -m py_compile` 三个 config + 三个 paper_forward 脚本（在当前项目目录用 python 运行，仅编译检查）

## 六、输出

- 报告文件：`data/schedules/de_task_top_n_exec_report_20260921.md`，包含：
  1. 改动清单（文件 + 行号 + 旧值 → 新值，逐条）
  2. `git diff` 摘要（在报告里给出 diff 文本，不要真 git 提交）
  3. C 组检查结果（硬编码残留清单 + 建议）
  4. 语法检查结果
  5. 自检确认：「未修改 versions/、capital_allocation.yaml、未 commit、未跑部署脚本」

## 七、完成后

将报告路径与改动清单摘要反馈给 TRAE（opencode）供交叉验证签收。
