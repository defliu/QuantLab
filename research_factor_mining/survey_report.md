# 因子挖掘数据普查报告

- 生成时间: 2026-09-16 11:35:38
- 口径: 覆盖/质量仅作候选设计前置，不涉及回测结论


## 1. 主库 daily（stock_daily.parquet）

- 行数: 14656796 | 日期: 2009-01-05 ~ 2026-09-11 | 股票数: 5839
- 列: ['open', 'high', 'low', 'close', 'pre_close', 'change', 'pct_chg', 'vol', 'amount', 'up_limit', 'down_limit', 'turnover_rate', 'turnover_rate_f', 'volume_ratio', 'pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm', 'dv_ratio', 'dv_ttm', 'total_share', 'float_share', 'free_share', 'total_mv', 'circ_mv', 'adj_factor', 'suspend_timing', 'suspend_type', 'is_st', 'listed_days']
- 训练段2021-23 覆盖日期 2021-01-04 ~ 2023-12-29, 行数 3491863
- 样本外2024-26 覆盖日期 2024-01-02 ~ 2026-09-11, 行数 3537398

## 2. 方向 A：另类数据三源


### 北向持仓 hk_hold_full
- 路径: `D:\astock\northbound\hk_hold_full.parquet`
- 行数: 5744542 | 列: ['code', 'trade_date', 'ts_code', 'name', 'vol', 'ratio', 'exchange']
- 日期: 2016-06-29 ~ 2026-09-11 | 唯一交易日 2406
  - 训练段2021-23 覆盖: 2021-01-04 ~ 2023-12-29, 2307691 行
  - 样本外2024-26 覆盖: 2024-01-02 ~ 2026-09-11, 1045263 行
- 覆盖股票数: 5635 | 字段: ratio(持股比例)/vol(持股数) → 可构造持股比例变动

### 龙虎榜 top_list
- 路径: `D:\astock\lhb\top_list.parquet`
- 行数: 229547 | 列: ['name', 'close', 'pct_change', 'turnover_rate', 'amount', 'l_sell', 'l_buy', 'l_amount', 'net_amount', 'net_rate', 'amount_rate', 'float_values', 'reason']
- 日期: 2005-01-19 ~ 2026-09-11 | 唯一交易日 5086
  - 训练段2021-23 覆盖: 2021-01-04 ~ 2023-12-29, 42108 行
  - 样本外2024-26 覆盖: 2024-01-02 ~ 2026-09-11, 47252 行
- 覆盖股票数: 6552 | 字段: net_amount/net_rate/l_amount/l_sell → 可构造席位净买入

### 研报 report_rc_daily
- 路径: `D:\astock\research\report_rc_daily.parquet`
- 行数: 929295 | 列: ['name', 'report_type', 'classify', 'op_rt', 'op_pr', 'tp', 'np', 'eps', 'pe', 'rd', 'roe', 'ev_ebitda', 'rating', 'max_price', 'min_price']
- 覆盖股票数: 7367 | 字段: rating/eps/tp/np → 可构造覆盖数/评级变动

## 3. 方向 B：财务 8 表（PIT 锚 ann_date/f_ann_date）


### balancesheet.parquet
- 行数: 319515 | 列数: 158 | PIT锚: ann_date=True f_ann_date=True
- 报告期: 2005-03-31 ~ 2026-06-30
- ann_date 覆盖率: 94.8% (302852/319515)
- 业务字段样例(前18): ['end_type', 'total_share', 'cap_rese', 'undistr_porfit', 'surplus_rese', 'special_rese', 'money_cap', 'trad_asset', 'notes_receiv', 'accounts_receiv', 'oth_receiv', 'prepayment', 'div_receiv', 'int_receiv', 'inventories', 'amor_exp', 'nca_within_1y', 'sett_rsrv']

### cashflow.parquet
- 行数: 316738 | 列数: 97 | PIT锚: ann_date=True f_ann_date=True
- 报告期: 2005-03-31 ~ 2026-06-30
- ann_date 覆盖率: 94.7% (299925/316738)
- 业务字段样例(前18): ['end_type', 'net_profit', 'finan_exp', 'c_fr_sale_sg', 'recp_tax_rends', 'n_depos_incr_fi', 'n_incr_loans_cb', 'n_inc_borr_oth_fi', 'prem_fr_orig_contr', 'n_incr_insured_dep', 'n_reinsur_prem', 'n_incr_disp_tfa', 'ifc_cash_incr', 'n_incr_disp_faas', 'n_incr_loans_oth_bank', 'n_cap_incr_repur', 'c_fr_oth_operate_a', 'c_inf_fr_operate_a']

### dividend.parquet
- 行数: 169067 | 列数: 14 | PIT锚: ann_date=True f_ann_date=False
- 报告期: 1990-12-31 ~ 2026-07-23
- ann_date 覆盖率: 97.8% (165362/169067)
- 业务字段样例(前18): ['div_proc', 'stk_div', 'stk_bo_rate', 'stk_co_rate', 'cash_div', 'cash_div_tax', 'record_date', 'ex_date', 'pay_date', 'div_listdate', 'imp_ann_date']

### express.parquet
- 行数: 29506 | 列数: 17 | PIT锚: ann_date=True f_ann_date=False
- 报告期: 2005-03-31 ~ 2026-06-30
- ann_date 覆盖率: 100.0% (29501/29506)
- 业务字段样例(前18): ['revenue', 'operate_profit', 'total_profit', 'n_income', 'total_assets', 'total_hldr_eqy_exc_min_int', 'diluted_eps', 'diluted_roe', 'yoy_net_profit', 'bps', 'open_net_assets', 'open_bps', 'perf_summary']

### fina_indicator.parquet
- 行数: 354187 | 列数: 167 | PIT锚: ann_date=True f_ann_date=False
- 报告期: 2005-03-31 ~ 2026-06-30
- ann_date 覆盖率: 95.2% (337125/354187)
- 业务字段样例(前18): ['eps', 'dt_eps', 'total_revenue_ps', 'revenue_ps', 'capital_rese_ps', 'surplus_rese_ps', 'undist_profit_ps', 'extra_item', 'profit_dedt', 'gross_margin', 'current_ratio', 'quick_ratio', 'cash_ratio', 'invturn_days', 'arturn_days', 'inv_turn', 'ar_turn', 'ca_turn']

### forecast.parquet
- 行数: 122540 | 列数: 13 | PIT锚: ann_date=True f_ann_date=False
- 报告期: 2005-03-31 ~ 2026-06-30
- ann_date 覆盖率: 100.0% (122506/122540)
- 业务字段样例(前18): ['type', 'p_change_min', 'p_change_max', 'net_profit_min', 'net_profit_max', 'last_parent_net', 'first_ann_date', 'summary', 'change_reason']

### income.parquet
- 行数: 329557 | 列数: 94 | PIT锚: ann_date=True f_ann_date=True
- 报告期: 2005-03-31 ~ 2026-06-30
- ann_date 覆盖率: 94.9% (312711/329557)
- 业务字段样例(前18): ['end_type', 'basic_eps', 'diluted_eps', 'total_revenue', 'revenue', 'int_income', 'prem_earned', 'comm_income', 'n_commis_income', 'n_oth_income', 'n_oth_b_income', 'prem_income', 'out_prem', 'une_prem_reser', 'reins_income', 'n_sec_tb_income', 'n_sec_uw_income', 'n_asset_mg_income']

### share_change_event.parquet
- 行数: 53892 | 列数: 9 | PIT锚: ann_date=False f_ann_date=False
- 业务字段样例(前18): ['change_date', 'change_reason', 'pre_shares', 'post_shares', 'pre_total_share_wan', 'post_total_share_wan', 'source', 'source_unit']

---
总用时 13.9s