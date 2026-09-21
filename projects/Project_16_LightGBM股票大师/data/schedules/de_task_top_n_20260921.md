# DE 任务书：P16 三策略持仓只数 TOP 最优性论证网格（2026-09-21）

## 背景与目的

诚哥要求论证 P16 三个策略（V1.3 / G2 / 融合）各自「持仓只数」设几只最优。
实盘三策略目前均为 TOP_N=2（等权，10 万虚拟子账户）。回测引擎 `scan_rotate_cost_real.py`
的持仓只数参数为环境变量 `BT_TOP`（默认 2）。本任务用官方引擎做 3 臂 × 7 档 TOP 网格，
输出每臂每档的年化/夏普/最大回撤/换手率/胜率/交易数，据此判定每臂最优持仓只数。

## 允许读取（只读，禁止修改）

- `D:\QuantLab\projects\Project_16_LightGBM股票大师\scan_rotate_cost_real.py`
- `D:\QuantLab\projects\Project_16_LightGBM股票大师\data_config.py`
- `D:\QuantLab\projects\Project_16_LightGBM股票大师\data\feature_panel_v3_enh2_n3_bt.parquet`
- `D:\QuantLab\projects\Project_16_LightGBM股票大师\data\features_v3_enh.json`
- `D:\QuantLab\projects\Project_16_LightGBM股票大师\data\features_v3_g2_strong_real_20260825.json`
- `D:\QuantLab\projects\Project_16_LightGBM股票大师\results\策略优化审计_20260910.md`（参考历史口径）
- `D:\QuantLab\models\lgb_model_v3_enh.txt`
- `D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260825_1964t.txt`

## 运行环境

- Python：`C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe`（运行前确认存在）
- 工作目录：`D:\QuantLab\projects\Project_16_LightGBM股票大师`

## 网格设计（21 组 = 3 臂 × 7 档 TOP）

固定口径（官方基线）：
- `BT_THRESHOLD=60`、`BT_STOP=-0.07`、`BT_TP=0.15`、`BT_SLIPS=0.001`、`BT_N_LIST=10`、`BT_EXIT=fixed`
- 区间用引擎默认（`BT_START`/`BT_END` 不设，2024-07-01 ~ 2026-08-14）
- TOP 档位：`BT_TOP` ∈ {2, 3, 4, 5, 6, 8, 10}

三臂（仅面板/模型/融合不同，其余相同）：
- V1.3 臂：`BT_PANEL=data\feature_panel_v3_enh2_n3_bt.parquet` + `BT_MODEL=D:\QuantLab\models\lgb_model_v3_enh.txt` + `BT_META=data\features_v3_enh.json`
- G2 臂：同面板 + `BT_MODEL=D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260825_1964t.txt` + `BT_META=data\features_v3_g2_strong_real_20260825.json`
- 融合臂：G2 臂配置 + `BT_ENSEMBLE=0.5` + `BT_MODEL2=D:\QuantLab\models\lgb_model_v3_enh.txt` + `BT_META2=data\features_v3_enh.json`

每组命令模板（PowerShell）：
```powershell
$env:BT_PANEL="data\feature_panel_v3_enh2_n3_bt.parquet"
$env:BT_MODEL="D:\QuantLab\models\lgb_model_v3_enh.txt"   # 按臂切换
$env:BT_META="data\features_v3_enh.json"                  # 按臂切换
$env:BT_TOP="<n>"
$env:BT_THRESHOLD="60"; $env:BT_STOP="-0.07"; $env:BT_TP="0.15"; $env:BT_SLIPS="0.001"; $env:BT_N_LIST="10"; $env:BT_EXIT="fixed"
python scan_rotate_cost_real.py
```

注意：每组跑完必须清理/重设环境变量再跑下一组，防止串扰。
建议把整个网格写成临时脚本 `data\schedules\grid_top_n_20260921_run.py`（仅允许创建这一个临时脚本）
用 `subprocess` + 每组合并 env 干净执行，便于一次跑完 21 组并逐组捕获 stdout。
引擎输出里提取每组的指标（年化/夏普/最大回撤/换手率/胜率/交易数/日均超额），写入 CSV。

## 输出（必须写，禁止写其他位置）

1. `D:\QuantLab\projects\Project_16_LightGBM股票大师\results\grid_top_n_20260921.csv`
   —— 21 组原始指标，列：arm, top, annual_ret, sharpe, max_dd, turnover, win_rate, n_trades, daily_excess。
   每行可溯源到对应组命令。
2. `D:\QuantLab\projects\Project_16_LightGBM股票大师\results\持仓只数最优论证_20260921.md`
   —— 论证报告：
   - 三臂各自 7 档 TOP 的完整指标表；
   - 每臂最优 TOP 结论（说明依据：年化/夏普/回撤/换手综合，防过拟合）；
   - 集中度与容量联动分析（单票上限=资金池×95%/TOP，10 万子账户下 TOP=2/3/4/5/6/8/10 的单票金额、100 股最小单位约束）；
   - 风控联动（-7% 止损尾部暴露随 TOP 摊薄、单日最大换手 30% 约束、持有期 N=10 与 TOP 的交互）；
   - 过拟合提示（2024-07~2026-08 单一路径，建议分年观察；TOP 差异若在 1pp 内属噪声）；
   - 与实盘 TOP_N=2 现状的差距与上调建议（若适用）。

## 禁止

- 修改只读清单外的任何文件；禁止 git add/commit/push；
- 禁止改动 config/*.yaml、qmt_config、g2_config.py、g2_ens_config.py、rebalance_*.py、deploy_*.py；
- 禁止改动模型/面板/特征文件；禁止创建除上述两输出 + 临时网格脚本外的文件；
- 报告数字必须与 CSV 完全一致，不得夸大或编造。
