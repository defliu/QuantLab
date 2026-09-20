# 死代码静态扫描报告（T-20260919-002 防线A）

- 扫描日期：2026-09-20 09:02:27
- 扫描文件数：184

## P1 建列后判（高）：先 df[c]=np.nan 建列，后 if 判 not in columns（后者永假）
- `projects\Project_16_LightGBM股票大师\versions\G2-V1.0_20260908\deploy_predict_g2.py:92` 列 `main_net`（建列 L92，判断 L97）
  ```
  if "main_net" not in pre.columns and "mf_main_net" in pre.columns:
  ```
- `projects\Project_16_LightGBM股票大师\versions\G2-V1.0_20260908\deploy_predict_g2.py:92` 列 `industry_pct`（建列 L92，判断 L99）
  ```
  if "industry_pct" not in pre.columns and "ind_pct_ths" in pre.columns:
  ```
- `projects\Project_16_LightGBM股票大师\versions\V20260909_融合验证\deploy_predict_g2.py:101` 列 `main_net`（建列 L101，判断 L106）
  ```
  if "main_net" not in pre.columns and "mf_main_net" in pre.columns:
  ```
- `projects\Project_16_LightGBM股票大师\versions\V20260909_融合验证\deploy_predict_g2.py:101` 列 `industry_pct`（建列 L101，判断 L108）
  ```
  if "industry_pct" not in pre.columns and "ind_pct_ths" in pre.columns:
  ```
- `projects\Project_16_LightGBM股票大师\versions\V20260909_融合验证\deploy_predict_g2.py:215` 列 `main_net`（建列 L215，判断 L218）
  ```
  if "main_net" not in ab_pre.columns and "mf_main_net" in ab_pre.columns:
  ```
- `projects\Project_16_LightGBM股票大师\versions\V20260909_融合验证\deploy_predict_g2.py:215` 列 `industry_pct`（建列 L215，判断 L220）
  ```
  if "industry_pct" not in ab_pre.columns and "ind_pct_ths" in ab_pre.columns:
  ```
- `projects\Project_16_LightGBM股票大师\versions\V20260909_融合验证\deploy_predict_g2.py:258` 列 `main_net`（建列 L258，判断 L261）
  ```
  if "main_net" not in en_pool.columns and "mf_main_net" in en_pool.columns:
  ```
- `projects\Project_16_LightGBM股票大师\versions\V20260909_融合验证\deploy_predict_g2.py:258` 列 `industry_pct`（建列 L258，判断 L263）
  ```
  if "industry_pct" not in en_pool.columns and "ind_pct_ths" in en_pool.columns:
  ```
- `projects\Project_16_LightGBM股票大师\versions\V20260909_门禁升级_纸面AB\deploy_predict_g2.py:98` 列 `main_net`（建列 L98，判断 L103）
  ```
  if "main_net" not in pre.columns and "mf_main_net" in pre.columns:
  ```
- `projects\Project_16_LightGBM股票大师\versions\V20260909_门禁升级_纸面AB\deploy_predict_g2.py:98` 列 `industry_pct`（建列 L98，判断 L105）
  ```
  if "industry_pct" not in pre.columns and "ind_pct_ths" in pre.columns:
  ```
- `projects\Project_16_LightGBM股票大师\versions\V20260909_门禁升级_纸面AB\deploy_predict_g2.py:212` 列 `main_net`（建列 L212，判断 L215）
  ```
  if "main_net" not in ab_pre.columns and "mf_main_net" in ab_pre.columns:
  ```
- `projects\Project_16_LightGBM股票大师\versions\V20260909_门禁升级_纸面AB\deploy_predict_g2.py:212` 列 `industry_pct`（建列 L212，判断 L217）
  ```
  if "industry_pct" not in ab_pre.columns and "ind_pct_ths" in ab_pre.columns:
  ```
## P2 passorder 返回值当订单号（高）：真值判断永假
- `broker\qmt_builder.py:204` 变量 `order_id`
  ```
  if order_id:
  ```
- `broker\qmt_builder.py:332` 变量 `order_id`
  ```
  if order_id:
  ```
- `broker\qmt_builder.py:357` 变量 `order_id`
  ```
  if order_id:
  ```
