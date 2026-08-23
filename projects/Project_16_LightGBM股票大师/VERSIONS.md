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
| 模型 `lgb_model_v3.txt`（8/19 版，1437树） | `D:/QuantLab/models/lgb_model_v3.txt` | ⚠️ **已被 8/23 重训覆盖，无备份，无法恢复** |
| 面板 `feature_panel_v3.parquet`（8/14 版） | `data/feature_panel_v3.parquet` | ⚠️ **已被 8/23 复权重建覆盖，备份见 `_unadj_bak`** |
| 选股清单 8/21 | `data/selections/20260821_selection_full.csv/md` | ✅ 保留 |
| 选股清单 8/20 | `data/selections/20260820_selection_full.csv/md` | ✅ 保留 |
| 持仓报告 8/21 | `data/holdings_report_20260821.md` | ✅ 保留 |
| 再平衡记录 8/21 | `data/rebalance_20260821.json` | ✅ 保留 |
| 评分脚本 | `deploy_predict.py`（2634f5d 版） | ✅ git 已存 |
| 评分卡 | `review_full.py` / `deploy_predict.py` F1-F6 | ✅ git 已存 |
| 当日快照 | `data_live/latest_features.parquet` | ✅ 保留（8/20 数据） |

### V1 配置要点（已核实）

- 模型：`lgb_model_v3.txt`（8/19 22:34 生成的 v3，**非当前 8/23 重训版**）
- 面板：`feature_panel_v3.parquet`（未复权版，数据到 2026-08-14）
- 评分卡红线：**65 分**（`--score-threshold 65`，实操时从默认 58 调高）
- 综合分：`combo = 0.6×model_prob + 0.4×(score_total/100)`
- 数据口径：F1/F4/F6 用面板代理分；**F2/F3/F5 用 TDX 实时复核**（主力净流入/新闻催化/行业涨幅）
- 持仓：TOP=2 等权满仓、止损 -7% / 止盈 +15% / 移动止盈 8%
- 8/21 选股 top10：恒邦股份/朗特智能/福莱特/音飞储存/北方铜业/内蒙新华/海伦哲/坤泰股份/耐普矿机/粤电力A

### 版本保留规范（自 V2 起强制执行）

**归档目录结构**（`versions/`）：
- `versions/V<数字>_<日期>/`：每个版本一个目录，含模型/面板/选股清单/报告
- `versions/models/`：模型保留池，当前 6 个模型副本（V1 8/19 版已被重训覆盖不可恢复，此为教训）

每个版本上线时归档：
1. `lgb_model_v<版本>.txt` 模型副本 → `versions/models/`（**必须保留，防重训覆盖**）
2. `feature_panel_v<版本>.parquet` + `features_v<版本>.json` 面板与特征定义 → 版本目录
3. 选股清单（上线日 + 后 3 日）与持仓报告 → 版本目录
4. 本次改动涉及的 `.py` 脚本（或 git commit hash）→ 在 `VERSIONS.md` 记录 hash
5. 配置快照（红线/TOP/N/权重/滑点等）→ `VERSIONS.md` 登记表
6. 回测结论（可执行口径日超额/胜率/回撤，来源报告文件名）→ `VERSIONS.md` 登记表

**V1 教训**：8/19 版 `lgb_model_v3.txt` 因未做模型保留，8/23 重训时被直接覆盖且无备份，现已无法恢复。自 V2 起任何重训前必须先复制旧模型到 `versions/models/`。

---

## V2（候选，未上线）

- 模型：`lgb_model_v3_enh.txt`（1373 树）
- 面板：`feature_panel_v3_enh.parquet`（27 特征 + F3 三列 + ind_mom20 + turnover_rank）
- 可执行 open→open 0.1% 滑点 N=5：日均超额 **+0.056%**、胜率 55.1%、回撤 -26.8%
- 判定：未达 +0.3% 门槛，暂缓上线；待补 F2 真实资金流或参数重扫后再评
- 详见：`补数据测试报告_20260823.md`

---

*仅供个人量化研究使用，不构成投资建议，市场有风险。*
