# V1.1 · 版本配置单

> 版本号：V1.1
> 上线日期：2026-08-25（待开盘）
> 变更类型：**小版本参数修订**（同 V1.0 体系，仅红线调整）
> 变更内容：评分卡红线 **65 → 58**

---

## 变更依据

- 8/21 审计（`data/audit_strategy_20260821.md` 缺陷 2）：红线 65 过严，全市场 + 模型前100池两个口径均显示 65 胜率/超额最差，**样本外回测 17% 空仓**；
- 红线 58 为 **0 空仓天、胜率 55%、盈亏比 1.46**（参考 `scan_threshold_pool_report.md`）；
- 该改进 8/21 审计时已定案，本次正式以 V1.1 版本启用。

## 配置（与 V1.0 唯一差异）

| 项 | V1.0 | V1.1 |
|---|---|---|
| 评分卡红线 | 65 | **58** |
| 模型 | lgb_model_v3.txt（8/19 版，1962树） | 同左（注：该模型已被 8/23 重训覆盖，实际运行时用当前 `D:/QuantLab/models/lgb_model_v3.txt`） |
| 面板 | feature_panel_v3.parquet（8/14 版） | 同左 |
| 综合分 | combo = 0.6×模型分 + 0.4×(评分卡/100) | 同左 |
| F2/F3/F5 | TDX 实时复核 | 同左 |
| 持仓 | TOP=2 / 止损-7% / 止盈+15% / 移动止盈8% | 同左 |

## 代码确认

- `deploy_predict.py` 默认 `--score-threshold 58.0`（无需改代码）；
- `run_scheduled.ps1` 盘后链路：`deploy_predict.py --model v3 --top-k 10`（无显式 threshold → 用默认 58）。

## 明日开盘运行说明

- 盘后链路（16:30）`Quant_Daily_Update` 自动跑 `xtdata_update → merge → deploy_predict`；
- 开盘选股（9:45 前）以 58 红线输出候选 → `review_full.py` + TDX 实时复核 → 出完整清单；
- 若需在开盘前手动触发，可执行：
  ```powershell
  python deploy_predict.py --model v3 --top-k 10 --score-threshold 58
  ```

## 回滚

如需回退到 65 红线：`python deploy_predict.py --model v3 --top-k 10 --score-threshold 65`（V1.0 配置）。
