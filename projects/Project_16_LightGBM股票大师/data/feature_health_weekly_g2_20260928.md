# G2 特征级 IC/覆盖率周报（43 特征，P0-2 补盲）

> 生成：2026-09-28 17:01:19
> 面板：G2 训练面板口径（feature_panel_v3 + _enh asof + g2 增强）（43 特征）| 近 4 周横截面 IC + 缺失率

## 判定规则

- **ALERT**：近4周|IC|<0.01 且 缺失率>5%
- **WATCH**：近4周|IC|<0.01 或 缺失率>5%

> IC 目标 = 面板 fwd_ret（v3 口径）；enh 慢变量为逐行 asof（T-20260910-106 口径）；
> lhb_net/lhb_count/rc_num 缺失按训练口径 fillna(0) 后统计（事件缺失=0 为有效值，断源看下方新鲜度表）。

## 特征健康表

| 特征 | 分组 | 近4周IC(abs均值) | 缺失率 | 判定 |
|---|---|---|---|---|
| vol_ratio_5_20 | v3基础 | 0.0644 | 0.00% | **OK** |
| amount_ma5 | v3基础 | 0.0997 | 0.00% | **OK** |
| volume_ratio | v3基础 | 0.0444 | 0.00% | **OK** |
| above_ma60 | v3基础 | 0.0464 | 0.00% | **OK** |
| rsi6 | v3基础 | 0.0369 | 0.00% | **OK** |
| above_ma20 | v3基础 | 0.0640 | 0.00% | **OK** |
| turn_ma5 | v3基础 | 0.1579 | 0.00% | **OK** |
| vol20 | v3基础 | 0.1670 | 0.00% | **OK** |
| mom_20 | v3基础 | 0.0604 | 0.00% | **OK** |
| rel_mom_20 | v3基础 | 0.0604 | 0.00% | **OK** |
| mom_10 | v3基础 | 0.0841 | 0.00% | **OK** |
| mom_5 | v3基础 | 0.0138 | 0.00% | **OK** |
| dist_250_low | v3基础 | 0.0969 | 0.00% | **OK** |
| mom_60 | v3基础 | 0.0969 | 0.00% | **OK** |
| macd_hist | v3基础 | 0.0708 | 0.00% | **OK** |
| pb | v3基础 | 0.1450 | 0.00% | **OK** |
| dv_ttm | v3基础 | 0.1393 | 0.00% | **OK** |
| sc_is_unlock | v3基础 | 0.0175 | 0.00% | **OK** |
| pe_ttm | v3基础 | 0.1193 | 0.00% | **OK** |
| fin_ocf_to_profit | v3基础 | 0.0293 | 12.08% | **WATCH** |
| sc_days_since | v3基础 | 0.0353 | 0.00% | **OK** |
| fin_dt_netprofit_yoy | v3基础 | 0.0368 | 0.25% | **OK** |
| fc_days_since | v3基础 | 0.0608 | 0.00% | **OK** |
| pos_250 | v3基础 | 0.1005 | 0.00% | **OK** |
| fin_netprofit_yoy | v3基础 | 0.0363 | 0.00% | **OK** |
| log_mv | v3基础 | 0.0994 | 0.00% | **OK** |
| fc_force | v3基础 | 0.0406 | 0.00% | **OK** |
| dv_year_sum | enh慢变量 | 0.0395 | 0.00% | **OK** |
| ex_days_since | enh慢变量 | 0.0409 | 0.00% | **OK** |
| ex_yoy | enh慢变量 | 0.0163 | 0.04% | **OK** |
| fc_pchange | enh慢变量 | 0.0428 | 0.00% | **OK** |
| industry_mom20 | enh慢变量 | 0.0547 | 0.00% | **OK** |
| turnover_rank | enh慢变量 | 0.1567 | 0.00% | **OK** |
| lhb_net | g2增强 | 0.0231 | 0.00% | **OK** |
| lhb_count | g2增强 | 0.0078 | 0.00% | **WATCH** |
| north_chg | g2增强 | — | 100.00%（断源） | **WATCH** |
| rc_rating | g2增强 | 0.1411 | 99.78%（断源） | **WATCH** |
| rc_num | g2增强 | 0.0025 | 0.00% | **WATCH** |
| mf_main_net | g2增强 | 0.0360 | 25.07% | **WATCH** |
| mf_elg_net | g2增强 | 0.0362 | 25.07% | **WATCH** |
| mf_main_ratio | g2增强 | 0.0182 | 25.07% | **WATCH** |
| ind_pct_ths | g2增强 | 0.0464 | 75.00% | **WATCH** |
| ind_net_ths | g2增强 | 0.0458 | 75.00% | **WATCH** |

## 汇总

- **ALERT（0）**：无
- **WATCH（10）**：fin_ocf_to_profit, lhb_count, north_chg, rc_rating, rc_num, mf_main_net, mf_elg_net, mf_main_ratio, ind_pct_ths, ind_net_ths

## G2_EXTRA 来源新鲜度（外部表 max(trade_date) vs 面板最新日）

| 来源表 | 覆盖特征 | 源最新日 | 落后天数 |
|---|---|---|---|
| lhb/top_list.parquet | lhb_net, lhb_count | 2026-09-22 | 6 |
| northbound/hk_hold_full.parquet | north_chg | 2026-09-22 | 6 |
| research/report_rc_daily.parquet | rc_rating, rc_num | 2026-09-11 | 17（⚠️ >7 天，训练面板同口径落后，特征在 asof/精确 merge 下退化为 NaN/0） |
| moneyflow/moneyflow.parquet | mf_main_net, mf_elg_net, mf_main_ratio | 2026-09-24 | 4 |
| board/ths_daily.parquet | ind_pct_ths, ind_net_ths | 2026-09-11 | 17（⚠️ >7 天，训练面板同口径落后，特征在 asof/精确 merge 下退化为 NaN/0） |
| board_fundflow/moneyflow_ind_ths.parquet | ind_pct_ths, ind_net_ths | 2026-09-11 | 17（⚠️ >7 天，训练面板同口径落后，特征在 asof/精确 merge 下退化为 NaN/0） |

> 注：北向/研报/行业表在 train_g2 中为**精确日期 merge**（非 asof），源停更后特征直接 NaN——
> 与训练面板行为一致，属数据源天花板（PROJECT_MEMORY 2026-09-12「数据天花板」节），非本脚本 bug。

> 仅监控告警，特征去留需人工裁决；告警特征在下一轮重训前建议核查数据源/口径。