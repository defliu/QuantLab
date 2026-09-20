# coding: utf-8
"""探针（2026-09-20）：确认 F2 的 Tushare 主源是否被 L112-119 死代码阻断。

纯离线：只读本地 parquet + LightGBM 推理，不联网、不触发行情/新浪。
复现 deploy_predict_g2.py L112-143 的 F2 段逻辑，逐点输出硬证据。
用法：miniqmt venv python probe_f2_main_source.py（cwd=项目目录或任意）
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb

HERE = r"D:/QuantLab/projects/Project_16_LightGBM股票大师"
sys.path.insert(0, HERE)
import data_config as DC  # noqa: E402

G2_MODEL, G2_META = DC.g2_live()
SNAP = os.path.join(DC.LIVE_DIR, "g2_latest_features.parquet")
print("model:", os.path.basename(G2_MODEL))

feat_cols = json.load(open(G2_META, encoding="utf-8"))["feature_cols"]
day = pd.read_parquet(SNAP)
day["trade_date"] = pd.to_datetime(day["trade_date"])
target = day["trade_date"].max()
day = day[day["trade_date"] == target].copy()
bst = lgb.Booster(model_file=G2_MODEL)
day["prob"] = bst.predict(day[feat_cols].astype("float32").values)
pre = day.nlargest(100, "prob").copy()

print("\n[1] 复现 L112-114（先填 NaN，使列必然存在）")
for c in ["main_net", "industry_pct", "volume_ratio"]:
    if c not in pre.columns:
        pre[c] = np.nan
print("  快照原生 main_net 列:", "main_net" in day.columns,
      "| mf_main_net 列:", "mf_main_net" in day.columns)

print("\n[2] 复现 L117 死代码判定（Tushare 值写入 main_net 的唯一入口）")
entered = ("main_net" not in pre.columns and "mf_main_net" in pre.columns)
print("  L117 条件值（True 才会执行 mf_main_net*1e4）:", entered)
if not entered:
    print("  → L117 分支【未执行 = Tushare 主源被阻断】")
    print("  实际 pre['main_net'] 非空数:", int(pre["main_net"].notna().sum()), "（0 = mf_main_net 从未写入）")
else:
    pre["main_net"] = pre["mf_main_net"] * 1e4
print("  对照组：若 L117 能执行，main_net 非空数应为", int(pre["mf_main_net"].notna().sum()))

print("\n[3] Tushare moneyflow 主库新鲜度（判断主源数据本应可用与否）")
MF_PATH = os.path.join(DC.ASTOCK_DIR, "moneyflow", "moneyflow.parquet")
try:
    _mf = pd.read_parquet(MF_PATH, columns=["net_mf_amount"])
    _latest = pd.Timestamp(_mf.index.get_level_values("trade_date").max())
    lag = (target - _latest).days
    mf_fresh = lag <= 2
    print("  moneyflow 最新:", _latest.date(), "| 目标:", target.date(),
          "| 滞后", lag, "天 | mf_fresh =", mf_fresh)
except Exception as e:
    print("  读 moneyflow 失败:", e)
    mf_fresh = None

print("\n[4] 复现 L143 新浪覆盖条件（关键分支 pd.isna(_snap)）")
_snap = pre["main_net"]
print("  _snap 全 NaN:", bool(pd.isna(_snap).all()))
print("  → L143 覆盖条件恒为 True（只要新浪 rt 返回该 code 即覆盖），与 mf_fresh 无关")
print("  → main_net 最终值来源：新浪覆盖 或 保持 NaN；Tushare 主源值无任何入口")
print("\n结论：F2 Tushare 主源【确认被阻断】" if not entered else "结论：F2 Tushare 主源未被阻断（意外）")
