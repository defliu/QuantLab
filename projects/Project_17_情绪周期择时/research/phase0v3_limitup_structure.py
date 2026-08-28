# -*- coding: utf-8 -*-
"""
Phase 0 v3：活跃池连板结构检验
判据冻结于 specs/任务书_v3.md（2026-08-26，先于本脚本运行落盘）。本脚本只实现判据。

信号：T日收盘 limit_list_d 中 limit=="U" 全部个股（含连板、不剔ST，任务书冻结口径）
分组：连板 = T 日在 U 名单 且 前一交易日同 ts_code 亦在 U 名单；否则首板。
      up_stat（X/Y 取 Y=板数）仅作交叉核对（主口径为名单推导），不一致率>2% 须排查。
收益：个股 fwdK = pct_chg 对数链复利 T+1..T+K（NaN 传播，停牌缺样）
基线：小盘池（circ_mv<50亿 & 非ST & 上市>=60日）逐股 fwdK 等权均值（与v1/v2完全同口径）
超额：eK = 组内个股 fwdK 对数均值 - 基线对数均值
判据（冻结，三条全过才过）：
M1 : 连板组 mean(e5)>0 且 非重叠抽样(间隔>=5交易日) 单样本单边 t p<0.05
M1': 剔一字板(T+1 开盘缺口>=9.5%)后连板组 mean(e5)>0 且 单边 p<0.10
M2 : mean(e5|连板) - mean(e5|首板) > 0 且 非重叠两样本单边 Welch t p<0.05
任一不过 -> 活跃池方向永久关闭，Project_17 整体归档（任务书 v3 冻结条款）。
描述性披露（不构成判据）：fwd10/fwd20 同口径、情绪四分位、分年度、2021+ 子窗口。
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
OUT_CSV = ROOT + r"\results\phase0v3_daily_e5.csv"

# ---- 冻结参数（与任务书v3一致，禁止改动）----
FWD5 = 5
GAP_TH = 0.095
SUB_FROM = pd.Timestamp("2021-01-01")
P_M1, P_M1P, P_M2 = 0.05, 0.10, 0.05
SAMPLE_GAP = 5


def log(msg):
    print(msg, flush=True)


# ---------- 1. 情绪分位序列（描述性披露用）----------
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
    pass

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
n_base_day = pool_mask.sum(axis=1)

g = (daily["open"] / daily["pre_close"] - 1.0).unstack()
g = g.reindex(index=wide_pct.index, columns=cols)
gap_t1 = g.shift(-1)
del g
gc.collect()

r = np.log1p(wide_pct / 100.0)
del wide_pct
gc.collect()
log("[3] 日线宽表与对数收益就绪")


def fwd_log(K):
    acc = None
    for k in range(1, K + 1):
        sk = r.shift(-k)
        acc = sk if acc is None else acc.add(sk)
    return acc


# ---------- 3. 涨停信号名单与连板判定 ----------
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

# 交叉核对：主口径（T及T-1连续U） vs up_stat 板数Y>=2
try:
    uidx = u.index.to_frame(index=False)
    uidx.columns = [c_date, c_code] if uidx.shape[1] == 2 else uidx.columns
    uidx[c_date] = pd.to_datetime(uidx[c_date])
    uidx["prev"] = uidx[c_date].map(lambda d: cal[cal_pos[d] - 1] if d in cal_pos.index and cal_pos[d] > 0 else None)
    uidx["is_lian_main"] = [c in sig_sets.get(p, set()) for c, p in zip(uidx[c_code], uidx["prev"])]
    if "up_stat" in u.columns:
        uidx["board_y"] = u["up_stat"].values
        uidx["board_y"] = pd.Series(uidx["board_y"]).fillna("0/0").map(lambda s: int(str(s).split("/")[-1])).values
        both = uidx.dropna(subset=["prev", "board_y"])
        if len(both):
            lian_sub = both[both["is_lian_main"]]
            m2y = (lian_sub["board_y"].ge(2).mean() if len(lian_sub) else np.nan)
            y2m = (both.loc[both["board_y"] >= 2, "is_lian_main"].mean()
                   if (both["board_y"] >= 2).any() else np.nan)
            log("up_stat 交叉核对：主口径判连板 %d 只，其中 Y>=2 占比 %.1f%%；Y>=2 中主口径判连板占比 %.1f%%（总样本 %d）"
                % (len(lian_sub), m2y * 100, y2m * 100, len(both)))
        else:
            log("（up_stat 交叉核对：无重叠样本）")
except Exception as e:
    log("（up_stat 交叉核对跳过：%s）" % e)


# ---------- 4. 逐信号日分组超额 ----------
def compute_group_e(K, with_exec=True):
    fwdK = fwd_log(K)
    baseK = fwdK.where(pool_mask).mean(axis=1)
    rows = []
    for d, codes in sorted(sig_sets.items()):
        if d not in cal_pos.index or d not in baseK.index:
            continue
        pos = cal_pos[d]
        prev_date = cal[pos - 1] if pos > 0 else None
        prev_codes = sig_sets.get(prev_date, set()) if prev_date is not None else set()
        lian = codes & prev_codes
        shou = codes - prev_codes
        avail = [c for c in codes if c in cols]
        if not avail:
            continue
        f_d = fwdK.loc[d]
        lc = [c for c in lian if c in cols]
        sc = [c for c in shou if c in cols]
        l_vals = f_d.reindex(lc)
        s_vals = f_d.reindex(sc)
        rec = {
            "trade_date": d,
            "e_lian": float(l_vals.mean() - baseK.loc[d]) if len(l_vals) else np.nan,
            "e_shou": float(s_vals.mean() - baseK.loc[d]) if len(s_vals) else np.nan,
            "n_lian": int(l_vals.notna().sum()),
            "n_shou": int(s_vals.notna().sum()),
        }
        if with_exec:
            gt = gap_t1.loc[d]
            lc_exec = [c for c in lc if (pd.isna(gt.get(c)) or gt[c] < GAP_TH)]  # 字面口径：gap缺失不剔
            lv = f_d.reindex(lc_exec)
            rec["e_lian_exec"] = float(lv.mean() - baseK.loc[d]) if len(lv) else np.nan
        rows.append(rec)
    out = pd.DataFrame(rows).set_index("trade_date").sort_index()
    return out


df5 = compute_group_e(FWD5, with_exec=True)
df5["pct_rank"] = pct_rank.reindex(df5.index)
df5 = df5.dropna(subset=["e_lian"])  # 无连板日不参与连板组判据
log("[5] 连板结构 e5 有效信号日 %d 天（%s ~ %s）；连板组日均 %.1f 只 / 首板组日均 %.1f 只"
    % (len(df5), df5.index.min().date(), df5.index.max().date(), df5["n_lian"].mean(), df5["n_shou"].mean()))
df5.to_csv(OUT_CSV, encoding="utf-8-sig")


def nonoverlap(s):
    """贪心非重叠抽样：相邻入选点间隔>=SAMPLE_GAP个交易日位置"""
    picked, last = [], -10 ** 9
    for d, v in s.sort_index().items():
        if pd.isna(v):
            continue
        p = cal_pos[d]
        if p - last >= SAMPLE_GAP:
            picked.append(float(v))
            last = p
    return np.asarray(picked, dtype=float)


# ---------- 5. 判据判定 ----------
verdicts = {}

x1 = nonoverlap(df5["e_lian"])
t1, p1 = stats.ttest_1samp(x1, 0.0, alternative="greater")
m1_mean = float(x1.mean())
verdicts["M1"] = bool(m1_mean > 0 and p1 < P_M1)
log("")
log("=== M1 连板组 fwd5 超额（冻结：mean>0 且 非重叠单边 p<%.2f）===" % P_M1)
log("有效日 %d，非重叠抽样 %d 点 | mean(e5|连板)=%+.4f（对数）≈%+.2f%% | t=%.3f p(one-sided)=%.4f"
    % (len(df5), len(x1), m1_mean, (np.exp(m1_mean) - 1) * 100, t1, p1))

x1p = nonoverlap(df5["e_lian_exec"])
t1p, p1p = stats.ttest_1samp(x1p, 0.0, alternative="greater")
m1p_mean = float(x1p.mean())
verdicts["M1'"] = bool(m1p_mean > 0 and p1p < P_M1P)
log("=== M1' 剔一字板执行感知（冻结：mean>0 且 非重叠单边 p<%.2f；gap阈值 %.1f%%）===" % (P_M1P, GAP_TH * 100))
log("非重叠抽样 %d 点 | mean=%+.4f ≈%+.2f%% | t=%.3f p(one-sided)=%.4f"
    % (len(x1p), m1p_mean, (np.exp(m1p_mean) - 1) * 100, t1p, p1p))

xs = nonoverlap(df5["e_shou"])
m2_diff = float(df5["e_lian"].mean() - df5["e_shou"].mean())
t2, p2 = stats.ttest_ind(x1, xs, alternative="greater", equal_var=False)
verdicts["M2"] = bool(m2_diff > 0 and p2 < P_M2)
log("=== M2 结构差（冻结：连板-首板>0 且 非重叠两样本单边 Welch p<%.2f）===" % P_M2)
log("连板 %d 点 mean=%+.4f | 首板 %d 点 mean=%+.4f | 差=%+.4f（对数）| t=%.3f p(one-sided)=%.4f"
    % (len(x1), x1.mean(), len(xs), xs.mean(), m2_diff, t2, p2))

# ---------- 6. 描述性披露（不构成判据）----------
log("")
log("=== 描述性披露（不构成判据）===")

# 分年度
yr = df5.copy()
yr["y"] = yr.index.year
tab = yr.groupby("y").apply(lambda g: pd.Series({
    "n": len(g),
    "连板mean": g["e_lian"].mean(),
    "首板mean": g["e_shou"].mean(),
    "差": g["e_lian"].mean() - g["e_shou"].mean(),
    "n连板": int(g["n_lian"].mean()),
}), include_groups=False)
log("分年度 e5（连板 vs 首板）：")
log(tab.round(4).to_string())

# 情绪四分位下连板组
sub = df5.dropna(subset=["pct_rank"])
q = pd.qcut(sub["pct_rank"], 4, labels=["Q1最低", "Q2", "Q3", "Q4最高"])
desc = sub.groupby(q, observed=True)["e_lian"].agg(["count", "mean"])
log("连板组 e5 按情绪分位四分位：")
for k, rowi in desc.iterrows():
    log("  %s: n=%d mean(e5|连板)=%+.4f" % (k, int(rowi["count"]), rowi["mean"]))

# 2021+ 子窗口
sub21 = df5[df5.index >= SUB_FROM]
x1_21 = nonoverlap(sub21["e_lian"])
xs_21 = nonoverlap(sub21["e_shou"])
log("2021+ 子窗口：连板 mean=%+.4f（%d点）首板 mean=%+.4f（%d点）差=%+.4f"
    % (x1_21.mean(), len(x1_21), xs_21.mean(), len(xs_21), x1_21.mean() - xs_21.mean()))

# fwd10 / fwd20 同口径（连板/首板均值，不跑判据）
for K in (10, 20):
    dk = compute_group_e(K, with_exec=False)
    log("fwd%d 描述：连板 mean=%+.4f（%d天）首板 mean=%+.4f 差=%+.4f"
        % (K, dk["e_lian"].mean(), len(dk), dk["e_shou"].mean(),
           dk["e_lian"].mean() - dk["e_shou"].mean()))
    del dk
    gc.collect()

n_pass = sum(verdicts.values())
log("")
log("=" * 60)
log("判定：M1=%s M1'=%s M2=%s（通过%d/3条）"
    % ("PASS" if verdicts["M1"] else "FAIL", "PASS" if verdicts["M1'"] else "FAIL",
       "PASS" if verdicts["M2"] else "FAIL", n_pass))
log("结论：" + ("三条全过 -> 方向【通过】，进 Phase 1 窄口径（仅连板接力）设计讨论"
               if n_pass == 3 else "任一不过 -> 活跃池方向永久关闭，Project_17 整体归档"))
log("=" * 60)
