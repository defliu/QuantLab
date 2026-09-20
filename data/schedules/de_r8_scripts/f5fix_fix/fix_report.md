# F5/F2 死代码修复报告（T-20260919-002）

- 文件：`D:\QuantLab\projects\Project_16_LightGBM股票大师\deploy_predict_g2.py`（唯一改动文件）
- 修复方式：三处均只做**顺序调换**——两个真实回退 `if` 移到填 NaN 的 `for c in [...]` 循环之前（先真实回退、后 NaN 兜底），代码内容一字未改。
- 说明：`git diff` 中 L94-105 的 `daily` 读取 try/except 兼容块为工作区**既有未提交改动**（修复前已存在，非本次任务引入）。

## 1. 主流程（现 L117-125，变量 `pre`）

改前：

```python
for c in ["main_net", "industry_pct", "volume_ratio"]:
    if c not in pre.columns:
        pre[c] = np.nan
if "main_net" not in pre.columns and "mf_main_net" in pre.columns:
    pre["main_net"] = pre["mf_main_net"] * 1e4
if "industry_pct" not in pre.columns and "ind_pct_ths" in pre.columns:
    pre["industry_pct"] = pre["ind_pct_ths"]
```

改后（回退 if 前移，注释随原代码块移动）：

```python
if "main_net" not in pre.columns and "mf_main_net" in pre.columns:
    pre["main_net"] = pre["mf_main_net"] * 1e4
if "industry_pct" not in pre.columns and "ind_pct_ths" in pre.columns:
    pre["industry_pct"] = pre["ind_pct_ths"]
for c in ["main_net", "industry_pct", "volume_ratio"]:
    if c not in pre.columns:
        pre[c] = np.nan
```

## 2. --ab 分支（现 L229-235，变量 `ab_pre`）

改前：

```python
for c in ["main_net", "industry_pct", "volume_ratio"]:
    if c not in ab_pre.columns:
        ab_pre[c] = np.nan
if "main_net" not in ab_pre.columns and "mf_main_net" in ab_pre.columns:
    ab_pre["main_net"] = ab_pre["mf_main_net"] * 1e4
```

改后：

```python
if "main_net" not in ab_pre.columns and "mf_main_net" in ab_pre.columns:
    ab_pre["main_net"] = ab_pre["mf_main_net"] * 1e4
if "industry_pct" not in ab_pre.columns and "ind_pct_ths" in ab_pre.columns:
    ab_pre["industry_pct"] = ab_pre["ind_pct_ths"]
for c in ["main_net", "industry_pct", "volume_ratio"]:
    if c not in ab_pre.columns:
        ab_pre[c] = np.nan
```

## 3. --ensemble 分支（现 L272-278，变量 `en_pool`）

改前：

```python
for c in ["main_net", "industry_pct", "volume_ratio"]:
    if c not in en_pool.columns:
        en_pool[c] = np.nan
if "main_net" not in en_pool.columns and "mf_main_net" in en_pool.columns:
    en_pool["main_net"] = en_pool["mf_main_net"] * 1e4
```

改后：

```python
if "main_net" not in en_pool.columns and "mf_main_net" in en_pool.columns:
    en_pool["main_net"] = en_pool["mf_main_net"] * 1e4
if "industry_pct" not in en_pool.columns and "ind_pct_ths" in en_pool.columns:
    en_pool["industry_pct"] = en_pool["ind_pct_ths"]
for c in ["main_net", "industry_pct", "volume_ratio"]:
    if c not in en_pool.columns:
        en_pool[c] = np.nan
```

## 语法检查

```
C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe -m py_compile D:\QuantLab\projects\Project_16_LightGBM股票大师\deploy_predict_g2.py
→ PY_COMPILE_OK（退出码 0）
```

## 修复后语义

- 快照含 `mf_main_net`/`ind_pct_ths` 时：`main_net` = mf_main_net×1e4（万元→元，F2 评分卡口径），`industry_pct` = ind_pct_ths（F5 真实板块涨幅）。
- 列真缺失时才补 NaN 兜底（volume_ratio 行为不变，仍 NaN 兜底）。
