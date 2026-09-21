# DE 任务执行报告：P16 三策略 TOP_N 实盘参数上调（de_task_top_n_exec_20260921）

> 任务号：de_task_top_n_exec_20260921（T-20260921-003 落地）
> 执行日期：2026-09-22
> 依据任务书：`data/schedules/de_task_top_n_exec_20260921.md`
> 执行者：DE（DevEco Code CLI）

## 一、改动清单（逐条，文件 + 行号 + 旧值 → 新值）

### A 组：实盘核心 TOP_N（3 处）

| # | 文件 | 行 | 旧 | 新 |
|---|---|---|---|---|
| 1 | `g2_config.py` | L30 | `TOP_N = 2` | `TOP_N = 3`（注释更新为「2026-09-21 拍板 TOP_N 2→3，依据《持仓只数最优论证_20260921.md》」） |
| 2 | `g2_ens_config.py` | L32 | `TOP_N = 2` | `TOP_N = 5`（注释同上格式） |
| 3 | `rebalance_daily.py` | L46 | `TOP_N = 2` | `TOP_N = 5`（注释同上格式） |

### B 组：纸面前向统计 rank 口径（3 文件，G2 口径 2→3）

| # | 文件 | 行 | 改动 |
|---|---|---|---|
| 4 | `paper_forward.py` | L115 | 注释「实盘 g2_config.TOP_N=2，统计时按 rank<=2」→「TOP_N=3（2026-09-21 拍板 2→3），按 rank<=3」 |
| 5 | `paper_forward.py` | L163 | `done[done["rank"] <= 2]`（TOP2子集）→ `done[done["rank"] <= 3]`（TOP3子集） |
| 6 | `paper_forward_exit.py` | L7 | 文档字符串 rank<=2 / TOP_N=2 → 3 |
| 7 | `paper_forward_exit.py` | L21、L23 | 用法示例 `--top 2`→`--top 3`；rank<=2 → rank<=3 |
| 8 | `paper_forward_exit.py` | L231-232 | `if top == 2: sub = live[live["rank"] <= 2]` → `if top == 3: sub = live[live["rank"] <= 3]`（**直接关联改动**，见下注） |
| 9 | `paper_forward_exit.py` | L316 | 报告文案「rank<=2 实盘口径，持仓2」→「rank<=3 实盘口径，持仓3」 |
| 10 | `paper_forward_exit.py` | L349 | 进度文案「约 2 笔/交易日，按 rank<=2 …/2」→「约 3 笔/交易日，按 rank<=3 …/3」（预估除数同步 2→3，保持文案自洽） |
| 11 | `paper_forward_exit.py` | L407 | `--top default=2, help="（2=实盘口径）"` → `default=3, help="（3=实盘口径）"` |
| 12 | `forward_stats.py` | L5 | 文档字符串「TOP2（实盘口径，g2_config.TOP_N=2）」→「TOP3（…TOP_N=3）」 |
| 13 | `forward_stats.py` | L107 | `res[res["rank"] <= 2], "TOP2(实盘口径)"` → `res[res["rank"] <= 3], "TOP3(实盘口径)"` |
| 14 | `forward_stats.py` | L151-153 | 进度文案「约 2 笔/交易日，仅 TOP2 口径…/2」→「约 3 笔/交易日，仅 TOP3 口径…/3」 |

> **注（改动 8 说明）**：任务书要求 L232 `rank <= 2` → `rank <= 3` 且 L407 default 2→3。原逻辑为 `if top == 2: 取 rank<=2 子集，else: 取 Top10 全集`。若只改 L232 不改 L231 分支条件，则 default=3 会落入 else 分支变成 Top10 全集，与「实盘口径」语义相反。故 L231 `top == 2` → `top == 3` 是保持逻辑一致的必要直接关联改动（改动量最小，未重构）。

## 二、git diff 摘要

```
 6 files changed, 17 insertions(+), 17 deletions(-)
 forward_stats.py       | 8 ++++----
 g2_config.py           | 2 +-
 g2_ens_config.py       | 2 +-
 paper_forward.py       | 4 ++--
 paper_forward_exit.py  | 16 ++++++++--------
 rebalance_daily.py     | 2 +-
```

关键 diff（A 组）：

```diff
--- a/g2_config.py
-TOP_N = 2                            # 目标持仓数（等权，对齐回测 TOP）
+TOP_N = 3                            # 目标持仓数（等权；2026-09-21 拍板 TOP_N 2→3，依据《持仓只数最优论证_20260921.md》）

--- a/g2_ens_config.py
-TOP_N = 2                            # 目标持仓数（等权，对齐回测 TOP）
+TOP_N = 5                            # 目标持仓数（等权；2026-09-21 拍板 TOP_N 2→5，依据《持仓只数最优论证_20260921.md》）

--- a/rebalance_daily.py
-TOP_N = 2
+TOP_N = 5  # 2026-09-21 拍板 TOP_N 2→5（依据《持仓只数最优论证_20260921.md》）
```

B 组核心 diff（完整 diff 见 `git diff`，此处略同上清单）：`paper_forward_exit.py` L231-232 `if top == 3: sub = live[live["rank"] <= 3]`、L407 `default=3`；`paper_forward.py` L163 `rank <= 3`（TOP3子集）；`forward_stats.py` L107 `rank <= 3`（TOP3 实盘口径）。

## 三、C 组检查结果（只记录，未修改）

### ⚠️ 高优先级：定时任务调用参数脱节风险（任务书未预见）

| 文件 | 位置 | 内容 | 风险与建议 |
|---|---|---|---|
| `paper_forward_daily.ps1` | L9 | 定时任务**显式传参** `paper_forward_exit.py --top 2 --hold 10` | **会覆盖新 default=3**；且因分支条件已改为 `top == 3`，传 `--top 2` 会落入 else 分支 → 出场规则前向验证从「TOP2 子集」**静默变成 Top10 全集口径**，样本累积速度与判定口径全变。**建议 TRAE 拍板后尽快把 ps1 中 `--top 2` 改为 `--top 3`**（本任务无授权改 ps1，未动）。 |

### 实际过滤逻辑硬编码（三臂共用 TOP2 口径，待 TRAE 确认各臂新口径后统一改）

| 文件 | 位置 | 内容 |
|---|---|---|
| `paper_forward_downgrade.py` | **L154（实际逻辑）** | `if ... int(r["rank"]) > 2: continue`（实盘 TOP2 口径过滤，三臂 G2/V1.3/ENS 降级闸共用） |
| `paper_forward_downgrade.py` | L8、L48、L155 | 注释/文案「rank<=2 实盘 TOP2 口径」 |
| `paper_forward_ab_stats.py` | **L93（实际逻辑）** | `r2 = len(df[df["rank"] <= 2])`（rank<=2 统计列） |
| `paper_forward_ab_stats.py` | L69 | 表头「rank<=2笔」 |
| `paper_quarterly_review.py`（任务书未列，全局 grep 发现） | **L70（实际逻辑）** | `if ... int(r["rank"]) > 2: continue`（季度复核，与降级闸同源口径） |
| `paper_quarterly_review.py` | L11、L54、L134、L197、L240、L248 | 注释/文案「实盘 TOP2 口径（rank<=2）」 |

> 说明：三臂 TOP_N 现已分化（G2=3 / ENS=5 / V1.3=5），上述三臂共用脚本的单一大 `rank<=2` 阈值不再对齐任何一臂，需 TRAE 确认是「按臂区分阈值」还是「统一取 min/max」后再改，本任务一律未动。

### 仅注释/历史描述（可不动或后续顺手更新）

| 文件 | 位置 | 内容 |
|---|---|---|
| `deploy_predict_g2.py` | L209-210 | 注释「实盘 g2_config.TOP_N=2，前向统计时 rank<=2 即实盘口径子集」——**注释已过时**（实盘现为 3），建议后续同步；本任务按任务书不改 |
| `g2_candidates_night.ps1` | L6 | 注释「前向统计按 rank<=2」（描述性） |
| `run_scheduled.ps1` | L261/270/332/343 | 「历史最优配置 TOP2」为回测门禁历史锚点描述（网格历史口径），非实盘参数，不需改 |
| `paper_forward.py` | L180（help「当日 top2 候选」）、L183（`--top default=2`）、L235（注释「持仓2：按 total_new 取前2」）、L278（「持仓2只」） | 属 `--replay/--live` 回放候选数参数（CLI 可传参），任务书未列，未改；如需口径统一可后续拍板 |
| `g2_config.py` L35、`g2_ens_config.py` L5/6/31 | HOLD_DAYS/文档字符串中「TOP2」为网格历史锚点描述（live_trail/N15/红线60/TOP2 +0.213%），保留历史事实，未改 |

## 四、全局 grep 复查（排除 versions/、data/、results/）

模式：`TOP_N\s*=\s*2`、`rank.{0,6}(<=|>)\s*2\b`。**`TOP_N = 2` 在生产代码（非 versions/）已零残留**。`rank` 阈值 2 残留即第三节清单（downgrade L154 / ab_stats L93 / quarterly_review L70 三处实际逻辑 + 各注释/ps1 调用参数），均已记录待拍板，无其他遗漏。

## 五、语法检查

```
C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe -m py_compile \
  g2_config.py g2_ens_config.py rebalance_daily.py paper_forward.py paper_forward_exit.py forward_stats.py
→ EXIT=0（Python 3.11.15），6/6 通过
```

（注：系统 PATH 无 python，用 miniqmt venv Python 3.11 执行，仅编译检查，无任何脚本运行副作用。）

## 六、自检确认

- ✅ 未修改 `versions/` 任何文件
- ✅ 未修改 `capital_allocation.yaml`
- ✅ 未修改 `data/real/` 历史预判文档
- ✅ 未 commit / push / 任何 git 写操作（仅只读 `git diff`）
- ✅ 未运行任何真实下单 / 部署 / 构建 / 定时任务脚本
- ✅ 仅修改任务书第三节 A/B 授权的 6 个文件；TOP_N 值严格为 3/5/5，无其他数字
- ✅ rebalance_g2.py / rebalance_ens.py 经任务书确认 `n_slots = G.TOP_N - len(kept)` 自动跟随，无需改动（未动）

## 七、待 TRAE 拍板事项（交接）

1. **`paper_forward_daily.ps1:9` 的 `--top 2` 显式参数**——不处理会导致出场规则前向验证口径静默变为 Top10 全集（见第三节高优先级）。
2. **三臂共用脚本（downgrade / ab_stats / quarterly_review）的 rank<=2 硬编码**——三臂 TOP_N 已分化（3/5/5），单阈值口径如何统一需拍板。
3. `deploy_predict_g2.py` L209-210 过时注释是否顺手更新。
