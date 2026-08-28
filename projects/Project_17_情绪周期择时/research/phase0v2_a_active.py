# -*- coding: utf-8 -*-
"""
Phase 0 v2 · 方向A：活跃股池（T日涨停股）情绪分层检验
判据冻结于 specs/任务书_v2.md（2026-08-26，先于本脚本运行落盘）。本脚本只实现判据。

信号：T日收盘 limit_list_d 中 limit=="U" 全部个股（含连板、不剔ST，任务书冻结口径）
收益：个股 fwd20 = pct_chg 对数链复利 T+1..T+20（任一日缺样则样本缺失，任务书局限①已披露）
基线：小盘池（circ_mv<500000万元 & 非ST & 上市>=60日）逐股 fwd20 等权均值（与v1完全同口径）
超额：e20(T) = 活跃池对数均值 - 基线对数均值
分层：sentiment_series.csv 的 pct_rank，高>=0.70 / 低<=0.30（冻结）
A1：高组非重叠抽样（每20交易日取1天，贪心间隔>=20）单样本单边 t p<0.05 且 mean>0
A2：高-低均值差>0（全期重叠序列）且 2021-01-01 起子窗口同为正
A3：执行感知变体（剔除 T+1 open/pre_close-1 >= +9.5% 疑似一字板；字面口径，gap缺失不剔）：
    高组 mean>0 且非重叠子样单边 p<0.10
三条全过 -> 通过进 Phase 1；任一不过 -> 方向一归档结题。
"""
import sys
import gc
import numpy as np
import pandas as pd
from scipy import stats

ROOT = r"d:\QuantLab\projects\Project_17_情绪周期择时"
DAILY = r"E:\astock\daily\stock_daily.parquet"
LIMITPQ = r"E:\astock\lhb\limit_list_d.parquet"
SENT = ROOT + r"\results\sentiment_series.csv"
POOL_V1 = ROOT + r"\results\pool_fwd_ret.csv"
OUT_CSV = ROOT + r"\results\phase0v2_a_daily_e20.csv"

# ---- 冻结参数（与任务书v2一致，禁止改动）----
FWD = 20
HIGH_TH, LOW_TH = 0.70, 0.30
GAP_TH = 0.095
SUB_FROM = pd.Timestamp("2021-01-01")
P_A1, P_A3 = 0.05, 0.10
SAMPLE_GAP = 20


def log(msg):
    print(msg, flush=True)


# ---------- 1. 情绪分位序列 ----------
s_raw = pd.read_csv(SENT)
date_col = next((c for c in ["trade_date", "date", "dt"] if c in s_raw.columns), None)
if date_col is None:
    s_raw = pd.read_csv(SENT, index_col=0)
    s_raw.index = pd.to_datetime(s_raw.index)
else:
    s_raw[date_col] = pd.to_datetime(s_raw[date_col])
    s_raw = s_raw.set_index(date_col)
pct_rank = s_raw["pct_rank"]
log("[1] 情绪序列 %s ~ %s 共%d天" % (pct_rank.index.min().date(), pct_rank.index.max().date(), len(pct_rank)))

# ---------- 2. 日线宽表 ----------
need = ["open", "pre_close", "pct_chg", "circ_mv", "is_st", "listed_days"]
try:
    import pyarrow.parquet as pq
    schema_cols = [f.name for f in pq.ParquetFile(DAILY).schema_arrow]
    missing = [c for c in need if c not in schema_cols]
    if missing:
        log("[中止] stock_daily 缺列 %s；实际列：%s" % (missing, schema_cols))
        sys.exit(1)
except ImportError:
    pass  # 无 pyarrow 时直接读取，缺列会由 pandas 报错暴露

daily = pd.read_parquet(DAILY, columns=need)
cal = daily.index.get_level_values(0).unique().sort_values()
cal_pos = pd.Series(np.arange(len(cal)), index=cal)
n_codes = daily.index.get_level_values(1).nunique()
log("[2] 日线 %s ~ %s 共%d交易日 / %d只股票" % (cal.min().date(), cal.max().date(), len(cal), n_codes))

wide_pct = daily["pct_chg"].unstack()
cols = wide_pct.columns
pool_mask = ((daily["circ_mv"] < 500000) & (daily["is_st"] != 1)
             & (daily["listed_days"].fillna(1e9) >= 60)).unstack()
pool_mask = pool_mask.reindex(index=wide_pct.index, columns=cols)

# T+1 开盘缺口：行=T，值 = T+1 的 open/pre_close - 1
g = (daily["open"] / daily["pre_close"] - 1.0).unstack()
g = g.reindex(index=wide_pct.index, columns=cols)
gap_t1 = g.shift(-1)
n_base_day = pool_mask.sum(axis=1)

# ---------- 3. 个股 fwd20：pct_chg 对数链 T+1..T+20（NaN 传播）----------
r = np.log1p(wide_pct / 100.0)
del wide_pct
gc.collect()
acc = None
for k in range(1, FWD + 1):
    sk = r.shift(-k)
    acc = sk if acc is None else acc.add(sk)
fwd20 = acc
del r, acc
gc.collect()
log("[3] fwd20 对数链计算完成")

base_log = fwd20.where(pool_mask).mean(axis=1)  # 小盘池基线（对数空间均值）

# ---------- 4. 涨停信号名单 ----------
ll = pd.read_parquet(LIMITPQ)
if not isinstance(ll.index, pd.MultiIndex) or ll.index.nlevels < 2:
    log("[中止] limit_list_d 不是 (日期,代码) 双层索引")
    sys.exit(1)
sig = ll.index.to_frame(index=False)
c_date = next((c for c in sig.columns if "date" in str(c).lower()), sig.columns[0])
c_code = next((c for c in sig.columns if "code" in str(c).lower()), sig.columns[1])
sig[c_date] = pd.to_datetime(sig[c_date])
u = ll[ll["limit"] == "U"]
sig_u = u.index.to_frame(index=False)
sig_u[c_date] = pd.to_datetime(sig_u[c_date])
sig_sets = sig_u.groupby(c_date)[c_code].agg(set).to_dict()
log("[4] 涨停(limit==U)记录 %d 条 / 信号日 %d 天（%s ~ %s）"
    % (len(u), len(sig_sets), min(sig_sets).date(), max(sig_sets).date()))

# ---------- 5. 逐信号日超额 ----------
rows = []
for d, codes in sorted(sig_sets.items()):
    if d not in cal_pos.index or d not in base_log.index:
        continue
    avail = [c for c in codes if c in cols]
    if not avail:
        continue
    f_d = fwd20.loc[d].reindex(avail)
    gp = gap_t1.loc[d].reindex(avail)
    act_exec = f_d[gp < GAP_TH]  # 字面实现：仅剔除 gap>=9.5%，gap 缺失不剔
    b = base_log.loc[d]
    rows.append({
        "trade_date": d,
        "e20_log": f_d.mean() - b,
        "e20_exec_log": (act_exec.mean() - b) if len(act_exec) else np.nan,
        "n_active": int(f_d.notna().sum()),
        "n_base": int(n_base_day.loc[d]),
        "cov_codes": len(avail) / max(len(codes), 1),
    })
df = pd.DataFrame(rows).set_index("trade_date").sort_index()
df["pct_rank"] = pct_rank.reindex(df.index)
df = df.dropna(subset=["pct_rank"])  # warmup 内无分位 -> 自然排除
log("[5] 有效信号日 %d 天（%s ~ %s）；活跃池日均有效样本 %.1f 只；基线池日均 %.0f 只；代码匹配率均值 %.1f%%"
    % (len(df), df.index.min().date(), df.index.max().date(),
       df["n_active"].mean(), df["n_base"].mean(), df["cov_codes"].mean() * 100))

hi = df[df["pct_rank"] >= HIGH_TH]
lo = df[df["pct_rank"] <= LOW_TH]


def nonoverlap(s):
    """贪心非重叠抽样：按时间序取点，与前一个已取点间隔>=SAMPLE_GAP个交易日"""
    picked, last = [], -10 ** 9
    for d, v in s.sort_index().items():
        if pd.isna(v):
            continue
        p = cal_pos[d]
        if p - last >= SAMPLE_GAP:
            picked.append(float(v))
            last = p
    return np.asarray(picked, dtype=float)


# ---------- 6. 判据判定 ----------
verdicts = {}

x1 = nonoverlap(hi["e20_log"])
t1, p1 = stats.ttest_1samp(x1, 0.0, alternative="greater")
a1_mean = float(x1.mean())
verdicts["A1"] = bool(a1_mean > 0 and p1 < P_A1)
log("")
log("=== A1 高情绪组超额（冻结：mean>0 且 非重叠单边 p<%.2f）===" % P_A1)
log("高情绪日 %d 天，非重叠抽样 %d 点 | mean(e20)=%+.4f（对数）≈%+.2f%% | t=%.3f p(one-sided)=%.4f"
    % (len(hi), len(x1), a1_mean, (np.exp(a1_mean) - 1) * 100, t1, p1))

diff_full = float(hi["e20_log"].mean() - lo["e20_log"].mean())
sub = df[df.index >= SUB_FROM]
hi_s = sub[sub["pct_rank"] >= HIGH_TH]
lo_s = sub[sub["pct_rank"] <= LOW_TH]
diff_sub = float(hi_s["e20_log"].mean() - lo_s["e20_log"].mean())
verdicts["A2"] = bool(diff_full > 0 and diff_sub > 0)
log("=== A2 高-低差（冻结：全期>0 且 2021+ 子窗口>0）===")
log("全期：高 %.4f - 低 %.4f = %+.4f（高%d天/低%d天）"
    % (hi["e20_log"].mean(), lo["e20_log"].mean(), diff_full, len(hi), len(lo)))
log("2021+ ：高 %.4f - 低 %.4f = %+.4f（高%d天/低%d天）"
    % (hi_s["e20_log"].mean(), lo_s["e20_log"].mean(), diff_sub, len(hi_s), len(lo_s)))

hi_exec_mean = float(hi["e20_exec_log"].mean())
x3 = nonoverlap(hi["e20_exec_log"])
t3, p3 = stats.ttest_1samp(x3, 0.0, alternative="greater")
verdicts["A3"] = bool(hi_exec_mean > 0 and p3 < P_A3)
log("=== A3 执行感知变体（冻结：mean>0 且 非重叠单边 p<%.2f；剔一字板阈值 %.1f%%）===" % (P_A3, GAP_TH * 100))
log("高情绪日执行感知 mean=%+.4f ≈%+.2f%% | 非重叠抽样 %d 点 | t=%.3f p(one-sided)=%.4f"
    % (hi_exec_mean, (np.exp(hi_exec_mean) - 1) * 100, len(x3), t3, p3))

log("")
log("=== 描述性补充（不构成判据）===")
q = pd.qcut(df["pct_rank"], 4, labels=["Q1最低", "Q2", "Q3", "Q4最高"])
desc = df.groupby(q, observed=True)["e20_log"].agg(["count", "mean"])
for k, rowi in desc.iterrows():
    log("  %s: n=%d mean(e20)=%+.4f" % (k, int(rowi["count"]), rowi["mean"]))
yr = df.copy()
yr["y"] = yr.index.year
tab = yr.groupby("y").apply(lambda g: pd.Series({
    "高n": int((g["pct_rank"] >= HIGH_TH).sum()),
    "高mean": g.loc[g["pct_rank"] >= HIGH_TH, "e20_log"].mean(),
    "低n": int((g["pct_rank"] <= LOW_TH).sum()),
    "低mean": g.loc[g["pct_rank"] <= LOW_TH, "e20_log"].mean(),
}), include_groups=False)
log(tab.to_string())

# 与 v1 基线交叉核对（仅打印，不影响判定）
try:
    pv1 = pd.read_csv(POOL_V1)
    dc1 = next((c for c in pv1.columns if "date" in str(c).lower()), pv1.columns[0])
    vc1 = next((c for c in pv1.columns if "fwd" in str(c).lower()), pv1.columns[-1])
    pv1[dc1] = pd.to_datetime(pv1[dc1])
    pv1 = pv1.set_index(dc1)[vc1].reindex(base_log.index)
    both = pd.concat([base_log.rename("v2基线"), pv1.rename("v1池fwd")], axis=1).dropna()
    log("与v1 pool_fwd_ret 交叉核对：重叠 %d 天，相关系数 %.4f，均值差 %.6f"
        % (len(both), both.corr().iloc[0, 1], (both.iloc[:, 0] - both.iloc[:, 1]).mean()))
except Exception as e:
    log("（跳过v1基线交叉核对：%s）" % e)

n_pass = sum(verdicts.values())
log("")
log("=" * 56)
log("判定：A1=%s A2=%s A3=%s（通过%d/3条）"
    % ("PASS" if verdicts["A1"] else "FAIL", "PASS" if verdicts["A2"] else "FAIL",
       "PASS" if verdicts["A3"] else "FAIL", n_pass))
log("结论：" + ("三条全过 -> 方向一【通过】，进 Phase 1 主线跟随选股设计"
               if n_pass == 3 else "任一不过 -> 方向一【归档结题】"))
log("=" * 56)

df.to_csv(OUT_CSV, encoding="utf-8-sig")
log("日度超额序列已保存：%s" % OUT_CSV)
