# -*- coding: utf-8 -*-
"""验收：真实快照上验证修复后 F2/F5 回退生效（死代码已解除）。"""
import numpy as np
import pandas as pd

SNAP = r"D:\QuantLab\projects\Project_16_LightGBM股票大师\data_live\g2_latest_features.parquet"

day = pd.read_parquet(SNAP)
day["trade_date"] = pd.to_datetime(day["trade_date"])
target = day["trade_date"].max()
day = day[day["trade_date"] == target].copy()
pre = day.copy()

print(f"快照日 {target.date()} 股票数 {len(pre)}")
print(f"列存在性: mf_main_net={ 'mf_main_net' in pre.columns }, ind_pct_ths={ 'ind_pct_ths' in pre.columns }, "
      f"main_net={ 'main_net' in pre.columns }, industry_pct={ 'industry_pct' in pre.columns }, volume_ratio={ 'volume_ratio' in pre.columns }")

# ---- 修复后逻辑（与 deploy_predict_g2.py 现 L113-121 一致）----
if "main_net" not in pre.columns and "mf_main_net" in pre.columns:
    pre["main_net"] = pre["mf_main_net"] * 1e4
if "industry_pct" not in pre.columns and "ind_pct_ths" in pre.columns:
    pre["industry_pct"] = pre["ind_pct_ths"]
for c in ["main_net", "industry_pct", "volume_ratio"]:
    if c not in pre.columns:
        pre[c] = np.nan
# ---------------------------------------------------------

mn_nonnull = pre["main_net"].notna().sum()
ip_nonnull = pre["industry_pct"].notna().sum()
mn_from_mf = (pre["main_net"] == pre["mf_main_net"] * 1e4).sum() if "mf_main_net" in pre.columns else 0
print(f"[修复后] main_net 非空 {mn_nonnull}/{len(pre)}，其中 = mf_main_net*1e4 的 {mn_from_mf}")
print(f"[修复后] industry_pct 非空 {ip_nonnull}/{len(pre)}")
print(f"[修复后] main_net 非 NaN 样本: {pre['main_net'].dropna().head(3).round(0).tolist()}")
print(f"[修复后] industry_pct 非 NaN 样本: {pre['industry_pct'].dropna().head(5).round(3).tolist()}")
verdict = "PASS" if mn_nonnull > 0 and ip_nonnull > 0 and mn_from_mf == mn_nonnull else "FAIL"
print(f"==> {verdict}")
