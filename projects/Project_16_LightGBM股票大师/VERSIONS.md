# 策略版本管理（Version Registry）

> 目的：每次上线的策略按版本号归档并保留完整资产（模型/面板/脚本/配置/选股记录），任何版本可回滚复现。新版本不行时，回退到上一个可用版本。
> 命名规则：`V<数字>_<上线日期YYYYMMDD>`，数字单调递增。
> 关联：模型文件 `D:/QuantLab/models/`、面板 `data/feature_panel*.parquet`、选股记录 `data/selections/`。

---

## 版本登记总表

| 版本号 | 上线日期 | 模型 | 面板 | 评分卡红线 | 关键配置 | 状态 | 说明 |
|---|---|---|---|---|---|---|---|
| **V1** | 2026-08-21 | `lgb_model_v3.txt`（8/19 版） | `feature_panel_v3.parquet`（8/14 版） | **65** | TOP=2 / 红线65 / 双轨(模型0.6+评分卡0.4) / F2F3F5 用 TDX 实时复核 | ✅ 实盘运行中 | 首次实操版本，含实时资金/新闻/板块复核 |
| **V2** | 待定 | `lgb_model_v3_enh.txt`（1373树） | `feature_panel_v3_enh.parquet` | 58 | F3补列+ind_mom20+换手分位 | ⏳ 候选 | 可执行口径转正 +0.056%，未达 +0.3% 门槛，暂不上线 |

---

## V1（2026-08-21 实操版）

### 资产快照清单

| 资产 | 路径 | 保留状态 |
|---|---|---|
| 模型 `lgb_model_v3.txt`（8/19 版，1962树） | `D:/QuantLab/models/lgb_model_v3.txt` | ❌ **8/19 版未找到**（8/23 被重训覆盖；F盘找到的是 484 树小样本测试版，非原版） |
| 模型 `lgb_model.txt`（v1，2754树） | `versions/models/lgb_model_v1_0819_2754t.txt` | ✅ **真原版已归档** |
| 模型 `lgb_model_v2.txt`（v2，2576树） | `versions/models/lgb_model_v2_0819_2576t.txt` | ✅ **真原版已归档** |
| 面板 `feature_panel_v3.parquet`（8/14 版） | `versions/V1_20260821/feature_panel_v3_unadj.parquet` | ✅ 已归档（未复权版） |
| 选股清单 8/21 | `data/selections/20260821_selection_full.csv/md` | ✅ 保留 |
| 选股清单 8/20 | `data/selections/20260820_selection_full.csv/md` | ✅ 保留 |
| 持仓报告 8/21 | `data/holdings_report_20260821.md` | ✅ 保留 |
| 再平衡记录 8/21 | `data/rebalance_20260821.json` | ✅ 保留 |
| 评分脚本 | `deploy_predict.py`（2634f5d 版） | ✅ git 已存 |
| 评分卡 | `review_full.py` / `deploy_predict.py` F1-F6 | ✅ git 已存 |
| 当日快照 | `data_live/latest_features.parquet` | ✅ 保留（8/20 数据） |

### V1 配置要点（已核实）

- **确认（用户 2026-08-23）**：8.21 实操 TOP10 就是用 **V1 版 + 65 分红线** 选出来的；**后来重训练把红线改成了 58**。
- 模型：`lgb_model_v3.txt`（8/19 生成的 v3，1962 树，**非当前 8/23 重训版**）
- 面板：`feature_panel_v3.parquet`（未复权版，数据到 2026-08-14）
- 评分卡红线：**65 分**（`--score-threshold 65`，实操时从默认 58 调高；重训练后改回 58）
- 综合分：`combo = 0.6×model_prob + 0.4×(score_total/100)`（**源码确认双轨**：`deploy_predict.py` 注释"双轨综合：模型概率×0.6 + 评分卡总分/100×0.4"，`--model-w` 默认 0.6；当天清单 `20260821_selection_full.md` 标题即"双轨选股"，用户记忆一致）
- 数据口径：F1/F4/F6 用面板代理分；**F2/F3/F5 用 TDX 实时复核**（主力净流入/新闻催化/行业涨幅）
- 持仓：TOP=2 等权满仓、止损 -7% / 止盈 +15% / 移动止盈 8%
- 8/21 选股 top10：恒邦股份/朗特智能/福莱特/音飞储存/北方铜业/内蒙新华/海伦哲/坤泰股份/耐普矿机/粤电力A
- 备注：2634f5d 版注释写红线 ≥70，8.21 实操清单标注红线 65，实操代码应晚于 2634f5d（65 为调后值，与用户确认一致）

### V1 复刻结论（2026-08-24 核实）

**用面板代理分无法复现 V1 实盘选股**：
- 8.21 实操的 F2/F3/F5（权重合计 50%）由**通达信实时数据**驱动（板块涨幅/主力净流入/新闻催化），这些数据决定 total 与排名；
- 面板代理分（量比/相对动量/事件）与实时评分是两套不同体系，导致复刻选股与 8.21 清单交集为 0；
- 因此：**找到 1962 树原版 v3 模型只是必要条件，非充分条件**；没有 8.21 当天通达信实时数据快照，无法还原当日 total 与 TOP10；
- 唯一可靠复刻数据源：`data/selections/20260821_selection_full.csv`（已保存当日 F1-F6/model_prob/total/main_net_inflow/industry_pct/catalyst_note 实时评分结果）。

### 版本保留规范（自 V2 起强制执行）

**归档目录结构**（`versions/`）：
- `versions/V<数字>_<日期>/`：每个版本一个目录，含模型/面板/选股清单/报告
- `versions/models/`：模型保留池。当前含：v1 原版(2754树)、v2 原版(2576树)、复权v3(1437树)、v3_enh(1373树)、v3_enh2、v3_f3、v3小样本测试版(484树)。命名带日期+树数防混淆。

每个版本上线时归档：
1. `lgb_model_v<版本>.txt` 模型副本 → `versions/models/`（**必须保留，防重训覆盖**）
2. `feature_panel_v<版本>.parquet` + `features_v<版本>.json` 面板与特征定义 → 版本目录
3. 选股清单（上线日 + 后 3 日）与持仓报告 → 版本目录
4. 本次改动涉及的 `.py` 脚本（或 git commit hash）→ 在 `VERSIONS.md` 记录 hash
5. 配置快照（红线/TOP/N/权重/滑点等）→ `VERSIONS.md` 登记表
6. 回测结论（可执行口径日超额/胜率/回撤，来源报告文件名）→ `VERSIONS.md` 登记表

**V1 教训**：8/19 版 `lgb_model_v3.txt`（1962树）因未做模型保留，8/23 重训时被直接覆盖；从 F 盘找回的 v3 是 484 树小样本测试版（服务器曾用 `--limit 300 --n-trials 2` 重训覆盖），非原版。v1/v2 原版已在本机模型池确认保留。自 V2 起任何重训前必须先复制旧模型到 `versions/models/`。

---

## V2（候选，未上线）

- 模型：`lgb_model_v3_enh.txt`（1373 树）
- 面板：`feature_panel_v3_enh.parquet`（27 特征 + F3 三列 + ind_mom20 + turnover_rank）
- 可执行 open→open 0.1% 滑点 N=5：日均超额 **+0.056%**、胜率 55.1%、回撤 -26.8%
- 判定：未达 +0.3% 门槛，暂缓上线；待补 F2 真实资金流或参数重扫后再评
- 详见：`补数据测试报告_20260823.md`

---

*仅供个人量化研究使用，不构成投资建议，市场有风险。*
