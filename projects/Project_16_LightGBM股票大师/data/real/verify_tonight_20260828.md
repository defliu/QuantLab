# 定时任务产出核验（自愈） · 2026-08-28 · 12:11:26
等待到 1652（16843s）...

## A. Quant_Daily_Update (16:30)
- incremental_daily.parquet 最新日: 2026-08-28 ✅
- reconcile 报告: 存在 ✅
  - 对账: 持仓一致/无异常 ✅

## B. paper_forward_daily (16:45)
- paper_forward_live.csv 含 2026-08-28: ✅
- g2 selections 20260828: 存在 ✅
- g2 日志尾部（含今日 OK/FAILED 判断）:
        F2 实时覆盖 100/100 只（新浪当日主力净额，元口径）| 未命中回退快照周更(×1e4)
    [5/5] 红线 + Top2 ...
      ts_code   prob  total_new  SC_F1  SC_F2  SC_F3  SC_F4  SC_F5  SC_F6
    300005.SZ 0.6439       70.5    8.0    6.0    9.0    7.0    5.0    5.0
    001336.SZ 0.5949       65.0    8.0    6.0    4.0   10.0    5.0    5.0
    [6/6] 保存结果 + 追加 live 日志 ...
        CSV: D:\QuantLab\projects\Project_16_LightGBM股票大师\data\selections\g2\20260828_g2_top2.csv
        MD : D:\QuantLab\projects\Project_16_LightGBM股票大师\data\selections\g2\20260828_g2_selection.md
        live log: D:\QuantLab\projects\Project_16_LightGBM股票大师\data\real\paper_forward_live.csv
    [20260828_164501] OK

## D. 定时任务最近结果
- Quant_Daily_Update:
    LastRunTime    : 2026/8/28 16:30:00
    LastTaskResult : 0
- paper_forward_daily:
    LastRunTime    : 2026/8/28 16:45:00
    LastTaskResult : 0