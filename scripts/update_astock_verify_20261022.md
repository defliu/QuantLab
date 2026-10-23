# D:/astock 数据更新验证报告（E 盘离线资产合并）

- 执行时间：2026-10-22 23:18:37
- 执行模式：APPLY（备份+合并+写回+验证）
- 脚本：`D:\QuantLab\scripts\update_astock_from_e.py`
- E 源：`E:\temp\行情数据\1.2parquet格式股票数据`（子目录镜像布局：五类子目录根下直接放表）
- D 目标：`D:\astock\`（唯一权威数据源，AGENTS 硬规则）
- 备份后缀：`.bak_20261022`（与原文件同目录）
- 合并规则：
  - daily/stock_daily：E 全量版不备份、直接原子替换（2026-09-14 制度）；
  - finance 8 表：**2026-10-22 起改为「备份 + E 版整体替换」**——同一 (code, end_date, ann_date) 下存在多条内容不同的版本记录（修订稿/更正公告），按主键合并会把 676049 行坍缩到 377047 行、丢一半历史，故不做主键合并；
  - lhb / research / board / board_fundflow / basic：按主键去重合并，D 独有行保留 + E 全量行，冲突键以 E 为准（E 较新）；只增不删。
- 更新顺序：daily → finance 8 表 → lhb → research → board → board_fundflow → basic；异常即停，后续表不动。

## 结果总表

| # | 表 | D 行数 | E 行数 | 合并后行数 | D独有键 | E新增键 | 冲突键(取E) | 日期范围(前→后) | 备份 | 耗时(s) | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | daily/stock_daily | 14723532 | 14762505 | 14762505 | 0 | 38973 | 14723532 | 2009-01-05 00:00:00~2026-09-30 00:00:00 → 2009-01-05 00:00:00~2026-10-16 00:00:00 | 无（主行情规定不备份，直接全量迁移） | 40.2 | OK |
| 2 | finance/balancesheet | 445365 | 445775 | 445775 | 0 | 410 | 445365 | 19901231~20260910 → 19901231~20260910 | balancesheet.parquet.bak_20261022 | 9.2 | OK |
| 3 | finance/cashflow | 387698 | 388204 | 388204 | 0 | 506 | 387698 | 19990130~20260910 → 19990130~20260910 | cashflow.parquet.bak_20261022 | 6.8 | OK |
| 4 | finance/dividend | 169345 | 169355 | 169355 | 0 | 10 | 169345 | 19940101~20260910 → 19940101~20260910 | dividend.parquet.bak_20261022 | 0.3 | OK |
| 5 | finance/express | 29598 | 29604 | 29604 | 0 | 6 | 29598 | 20050108~20260825 → 20050108~20260825 | express.parquet.bak_20261022 | 0.2 | OK |
| 6 | finance/fina_indicator | 675628 | 676049 | 676049 | 0 | 421 | 675628 | 19901231~20260910 → 19901231~20260910 | fina_indicator.parquet.bak_20261022 | 18.2 | OK |
| 7 | finance/forecast | 149107 | 149199 | 149199 | 0 | 92 | 149107 | 19990108~20260824 → 19990108~20260824 | forecast.parquet.bak_20261022 | 1.5 | OK |
| 8 | finance/income | 419276 | 419683 | 419683 | 0 | 407 | 419276 | 19950105~20260910 → 19950105~20260910 | income.parquet.bak_20261022 | 5.4 | OK |
| 9 | finance/share_change_event | 53892 | 53892 | 53892 | 0 | 0 | 53892 | 20090106~20260821 → 20090106~20260821 | share_change_event.parquet.bak_20261022 | 0.3 | OK |
| 10 | lhb/top_list | 230808 | 230741 | 230808 | 67 | 0 | 230741 | 2005-01-19 00:00:00~2026-10-19 00:00:00 → 2005-01-19 00:00:00~2026-10-19 00:00:00 | top_list.parquet.bak_20261022 | 2.7 | OK |
| 11 | research/report_rc_daily | 936742 | 937505 | 930233 | 0 | 332 | 929901 | 2006-01-01 00:00:00~2026-09-30 00:00:00 → 2006-01-01 00:00:00~2026-10-16 00:00:00 | report_rc_daily.parquet.bak_20261022 | 21.0 | OK |
| 12 | board/ths_daily | 3023802 | 3036127 | 3036127 | 0 | 12325 | 3023802 | 2007-08-01 00:00:00~2026-09-30 00:00:00 → 2007-08-01 00:00:00~2026-10-16 00:00:00 | ths_daily.parquet.bak_20261022 | 7.4 | OK |
| 13 | board_fundflow/moneyflow_ind_ths | 44640 | 45270 | 45270 | 0 | 630 | 44640 | 2024-09-10 00:00:00~2026-09-30 00:00:00 → 2024-09-10 00:00:00~2026-10-16 00:00:00 | moneyflow_ind_ths.parquet.bak_20261022 | 0.1 | OK |
| 14 | basic/stock_basic | 5922 | 5925 | 5925 | 0 | 3 | 5922 | NA~NA → 1990-12-01 00:00:00~2026-10-16 00:00:00 | stock_basic.parquet.bak_20261022 | 0.1 | OK |

## 每表详情

### 1. daily/stock_daily

- D 目标：`D:\astock\daily\stock_daily.parquet`（14723532 行）
- E 源：`E:\temp\行情数据\1.2parquet格式股票数据\1.parquet格式个股行情数据\stock_daily.parquet`（14762505 行）
- 主键：trade_date+ts_code
- D 日期范围：2009-01-05 00:00:00 ~ 2026-09-30 00:00:00；E 日期范围：2009-01-05 00:00:00 ~ 2026-10-16 00:00:00；合并后：2009-01-05 00:00:00 ~ 2026-10-16 00:00:00
- 键统计：D独有键 0 / E新增键 38973 / 冲突键(以E为准) 14723532 / D内部重复 0 / E内部重复 0 / 主键含NaN行 0
- 列对齐：D独有列=无；E独有列=无；合并后列数=33
- 合并后行数：14762505（写后复核=14762505）
- 备份：`无（主行情规定不备份，直接全量迁移）`（0.0s）；写回 29.5s；验证 2.7s；总耗时 40.2s
- 状态：OK

### 2. finance/balancesheet

- D 目标：`D:\astock\finance\balancesheet.parquet`（445365 行）
- E 源：`E:\temp\行情数据\1.2parquet格式股票数据\财务数据parquet格式（已清洗去重）\balancesheet.parquet`（445775 行）
- 主键：ts_code+end_date+report_type+ann_date
- D 日期范围：19901231 ~ 20260910；E 日期范围：19901231 ~ 20260910；合并后：19901231 ~ 20260910
- 键统计：D独有键 0 / E新增键 410 / 冲突键(以E为准) 445365 / D内部重复 0 / E内部重复 0 / 主键含NaN行 0
- 列对齐：D独有列=无；E独有列=无；合并后列数=158
- 合并后行数：445775（写后复核=445775）
- 备份：`balancesheet.parquet.bak_20261022`（0.1s）；写回 3.2s；验证 0.3s；总耗时 9.2s
- 状态：OK

### 3. finance/cashflow

- D 目标：`D:\astock\finance\cashflow.parquet`（387698 行）
- E 源：`E:\temp\行情数据\1.2parquet格式股票数据\财务数据parquet格式（已清洗去重）\cashflow.parquet`（388204 行）
- 主键：ts_code+end_date+report_type+ann_date
- D 日期范围：19990130 ~ 20260910；E 日期范围：19990130 ~ 20260910；合并后：19990130 ~ 20260910
- 键统计：D独有键 0 / E新增键 506 / 冲突键(以E为准) 387698 / D内部重复 0 / E内部重复 0 / 主键含NaN行 0
- 列对齐：D独有列=无；E独有列=无；合并后列数=97
- 合并后行数：388204（写后复核=388204）
- 备份：`cashflow.parquet.bak_20261022`（0.1s）；写回 2.5s；验证 0.2s；总耗时 6.8s
- 状态：OK

### 4. finance/dividend

- D 目标：`D:\astock\finance\dividend.parquet`（169345 行）
- E 源：`E:\temp\行情数据\1.2parquet格式股票数据\财务数据parquet格式（已清洗去重）\dividend.parquet`（169355 行）
- 主键：ts_code+end_date+ann_date+div_proc
- D 日期范围：19940101 ~ 20260910；E 日期范围：19940101 ~ 20260910；合并后：19940101 ~ 20260910
- 键统计：D独有键 0 / E新增键 10 / 冲突键(以E为准) 169345 / D内部重复 0 / E内部重复 0 / 主键含NaN行 0
- 列对齐：D独有列=无；E独有列=无；合并后列数=14
- 合并后行数：169355（写后复核=169355）
- 备份：`dividend.parquet.bak_20261022`（0.0s）；写回 0.1s；验证 0.0s；总耗时 0.3s
- 状态：OK

### 5. finance/express

- D 目标：`D:\astock\finance\express.parquet`（29598 行）
- E 源：`E:\temp\行情数据\1.2parquet格式股票数据\财务数据parquet格式（已清洗去重）\express.parquet`（29604 行）
- 主键：ts_code+end_date+ann_date
- D 日期范围：20050108 ~ 20260825；E 日期范围：20050108 ~ 20260825；合并后：20050108 ~ 20260825
- 键统计：D独有键 0 / E新增键 6 / 冲突键(以E为准) 29598 / D内部重复 0 / E内部重复 0 / 主键含NaN行 0
- 列对齐：D独有列=无；E独有列=无；合并后列数=17
- 合并后行数：29604（写后复核=29604）
- 备份：`express.parquet.bak_20261022`（0.0s）；写回 0.0s；验证 0.0s；总耗时 0.2s
- 状态：OK

### 6. finance/fina_indicator

- D 目标：`D:\astock\finance\fina_indicator.parquet`（675628 行）
- E 源：`E:\temp\行情数据\1.2parquet格式股票数据\财务数据parquet格式（已清洗去重）\fina_indicator.parquet`（676049 行）
- 主键：ts_code+end_date+ann_date
- D 日期范围：19901231 ~ 20260910；E 日期范围：19901231 ~ 20260910；合并后：19901231 ~ 20260910
- 键统计：D独有键 0 / E新增键 421 / 冲突键(以E为准) 675628 / D内部重复 0 / E内部重复 0 / 主键含NaN行 0
- 列对齐：D独有列=无；E独有列=无；合并后列数=167
- 合并后行数：676049（写后复核=676049）
- 备份：`fina_indicator.parquet.bak_20261022`（0.2s）；写回 6.0s；验证 0.5s；总耗时 18.2s
- 状态：OK

### 7. finance/forecast

- D 目标：`D:\astock\finance\forecast.parquet`（149107 行）
- E 源：`E:\temp\行情数据\1.2parquet格式股票数据\财务数据parquet格式（已清洗去重）\forecast.parquet`（149199 行）
- 主键：ts_code+end_date+ann_date+type
- D 日期范围：19990108 ~ 20260824；E 日期范围：19990108 ~ 20260824；合并后：19990108 ~ 20260824
- 键统计：D独有键 0 / E新增键 92 / 冲突键(以E为准) 149107 / D内部重复 0 / E内部重复 0 / 主键含NaN行 0
- 列对齐：D独有列=无；E独有列=无；合并后列数=13
- 合并后行数：149199（写后复核=149199）
- 备份：`forecast.parquet.bak_20261022`（0.0s）；写回 0.3s；验证 0.1s；总耗时 1.5s
- 状态：OK

### 8. finance/income

- D 目标：`D:\astock\finance\income.parquet`（419276 行）
- E 源：`E:\temp\行情数据\1.2parquet格式股票数据\财务数据parquet格式（已清洗去重）\income.parquet`（419683 行）
- 主键：ts_code+end_date+report_type+ann_date
- D 日期范围：19950105 ~ 20260910；E 日期范围：19950105 ~ 20260910；合并后：19950105 ~ 20260910
- 键统计：D独有键 0 / E新增键 407 / 冲突键(以E为准) 419276 / D内部重复 0 / E内部重复 0 / 主键含NaN行 0
- 列对齐：D独有列=无；E独有列=无；合并后列数=94
- 合并后行数：419683（写后复核=419683）
- 备份：`income.parquet.bak_20261022`（0.0s）；写回 1.7s；验证 0.2s；总耗时 5.4s
- 状态：OK

### 9. finance/share_change_event

- D 目标：`D:\astock\finance\share_change_event.parquet`（53892 行）
- E 源：`E:\temp\行情数据\1.2parquet格式股票数据\财务数据parquet格式（已清洗去重）\share_change_event.parquet`（53892 行）
- 主键：ts_code+change_date+change_reason
- D 日期范围：20090106 ~ 20260821；E 日期范围：20090106 ~ 20260821；合并后：20090106 ~ 20260821
- 键统计：D独有键 0 / E新增键 0 / 冲突键(以E为准) 53892 / D内部重复 0 / E内部重复 0 / 主键含NaN行 0
- 列对齐：D独有列=无；E独有列=无；合并后列数=9
- 合并后行数：53892（写后复核=53892）
- 备份：`share_change_event.parquet.bak_20261022`（0.0s）；写回 0.1s；验证 0.0s；总耗时 0.3s
- 状态：OK

### 10. lhb/top_list

- D 目标：`D:\astock\lhb\top_list.parquet`（230808 行）
- E 源：`E:\temp\行情数据\1.2parquet格式股票数据\parquet格式打板专题数据\top_list.parquet`（230741 行）
- 主键：trade_date+ts_code
- D 日期范围：2005-01-19 00:00:00 ~ 2026-10-19 00:00:00；E 日期范围：2005-01-19 00:00:00 ~ 2026-10-16 00:00:00；合并后：2005-01-19 00:00:00 ~ 2026-10-19 00:00:00
- 键统计：D独有键 67 / E新增键 0 / 冲突键(以E为准) 230741 / D内部重复 0 / E内部重复 0 / 主键含NaN行 0
- 列对齐：D独有列=无；E独有列=无；合并后列数=15
- 合并后行数：230808（写后复核=230808）
- 备份：`top_list.parquet.bak_20261022`（0.0s）；写回 0.4s；验证 0.1s；总耗时 2.7s
- 状态：OK

### 11. research/report_rc_daily

- D 目标：`D:\astock\research\report_rc_daily.parquet`（936742 行）
- E 源：`E:\temp\行情数据\1.2parquet格式股票数据\parquet格式特色数据\report_rc_daily.parquet`（937505 行）
- 主键：ts_code+report_date+org_name+author_name+report_title
- D 日期范围：2006-01-01 00:00:00 ~ 2026-09-30 00:00:00；E 日期范围：2006-01-01 00:00:00 ~ 2026-10-16 00:00:00；合并后：2006-01-01 00:00:00 ~ 2026-10-16 00:00:00
- 键统计：D独有键 0 / E新增键 332 / 冲突键(以E为准) 929901 / D内部重复 6841 / E内部重复 7272 / 主键含NaN行 94
- 列对齐：D独有列=无；E独有列=无；合并后列数=21
- 合并后行数：930233（写后复核=930233）
- 备份：`report_rc_daily.parquet.bak_20261022`（0.0s）；写回 1.4s；验证 2.1s；总耗时 21.0s
- 状态：OK

### 12. board/ths_daily

- D 目标：`D:\astock\board\ths_daily.parquet`（3023802 行）
- E 源：`E:\temp\行情数据\1.2parquet格式股票数据\parquet格式打板专题数据\ths_daily.parquet`（3036127 行）
- 主键：trade_date+ts_code
- D 日期范围：2007-08-01 00:00:00 ~ 2026-09-30 00:00:00；E 日期范围：2007-08-01 00:00:00 ~ 2026-10-16 00:00:00；合并后：2007-08-01 00:00:00 ~ 2026-10-16 00:00:00
- 键统计：D独有键 0 / E新增键 12325 / 冲突键(以E为准) 3023802 / D内部重复 0 / E内部重复 0 / 主键含NaN行 0
- 列对齐：D独有列=无；E独有列=无；合并后列数=15
- 合并后行数：3036127（写后复核=3036127）
- 备份：`ths_daily.parquet.bak_20261022`（0.1s）；写回 2.3s；验证 0.8s；总耗时 7.4s
- 状态：OK

### 13. board_fundflow/moneyflow_ind_ths

- D 目标：`D:\astock\board_fundflow\moneyflow_ind_ths.parquet`（44640 行）
- E 源：`E:\temp\行情数据\1.2parquet格式股票数据\parquet格式资金流向数据\moneyflow_ind_ths.parquet`（45270 行）
- 主键：trade_date+ts_code
- D 日期范围：2024-09-10 00:00:00 ~ 2026-09-30 00:00:00；E 日期范围：2024-09-10 00:00:00 ~ 2026-10-16 00:00:00；合并后：2024-09-10 00:00:00 ~ 2026-10-16 00:00:00
- 键统计：D独有键 0 / E新增键 630 / 冲突键(以E为准) 44640 / D内部重复 0 / E内部重复 0 / 主键含NaN行 0
- 列对齐：D独有列=无；E独有列=无；合并后列数=12
- 合并后行数：45270（写后复核=45270）
- 备份：`moneyflow_ind_ths.parquet.bak_20261022`（0.0s）；写回 0.0s；验证 0.0s；总耗时 0.1s
- 状态：OK

### 14. basic/stock_basic

- D 目标：`D:\astock\basic\stock_basic.parquet`（5922 行）
- E 源：`E:\temp\行情数据\1.2parquet格式股票数据\1.parquet格式个股行情数据\stock_basic_data.parquet`（5925 行）
- 主键：ts_code
- D 日期范围：NA ~ NA；E 日期范围：1990-12-01 00:00:00 ~ 2026-10-16 00:00:00；合并后：1990-12-01 00:00:00 ~ 2026-10-16 00:00:00
- 键统计：D独有键 0 / E新增键 3 / 冲突键(以E为准) 5922 / D内部重复 0 / E内部重复 0 / 主键含NaN行 0
- 列对齐：D独有列=无；E独有列=无；合并后列数=17
- 合并后行数：5925（写后复核=5925）
- 备份：`stock_basic.parquet.bak_20261022`（0.0s）；写回 0.0s；验证 0.0s；总耗时 0.1s
- 状态：OK

## 口径修正与说明

1. **basic 的 fill_source 列**：任务盘点写「E 无 D 独有的 fill_source 列」，实测相反——`fill_source` 存在于 **E 版**（`stock_basic_data.parquet` 5904 行），**D 版**（5892 行）没有该列。合并按「D 列序为基准 + E 独有列追加」的通用规则处理：合并后保留 E 的 `fill_source` 列，D 独有股票（不在 E 中）该列为空。本报告以实际文件为准。
2. **finance 主键**：D 版 8 张财务表 parquet 均无显式索引（RangeIndex），按 Tushare 口径定义主键（见脚本 `FIN_KEYS`），冲突以 E 为准，PIT 字段（ann_date/f_ann_date）原样保留。
3. **research 主键**：按任务指定的 5 列（ts_code+report_date+org_name+author_name+report_title），index 中的 quarter 列不参与去重。
4. **未触及**：northbound / cb / etf / min_* / index / chip / auction / benchmark / index_weight / Updatedata 均未改动。

## 遗留

- 回滚方式：任一表如需回滚，用同目录 `.bak_20261022` 备份覆盖回原名即可（备份为 D 原版原样拷贝）。
- 建议后续：下游 reader（astock_reader 等）跑一次冒烟回测确认新数据可读；新数据（08-24~09-11）已经过本脚本「行数/日期范围/index/主键唯一性」四项验证。
