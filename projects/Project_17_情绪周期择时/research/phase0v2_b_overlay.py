# -*- coding: utf-8 -*-
"""
Phase 0 v2 · 方向B：顺势覆盖层（情绪分择时最小实现）
判据冻结于 specs/任务书_v2.md（2026-08-26，先于本脚本运行落盘）。本脚本只实现判据。

策略：T-1 日 pct_rank >= 0.5 -> T 日满仓小盘池等权；否则空仓
日收益：仓位 × 小盘池等权日收益（成员当日 pct_chg 均值/100，成员口径与v1完全一致）
基准：同池无择时买入持有
主窗口：pct_rank 首个有效信号的次交易日起，至日线数据末端
B1：覆盖层夏普 > 基准夏普
B2：覆盖层最大回撤浅于基准最大回撤
B3：覆盖层 CAGR >= 基准 CAGR - 2pp
B1∧B2∧B3 全过 -> 通过；仅 B2∧B3 -> 弱阳性（只作回撤控制工具）；其余 -> 证伪归档。
夏普年化因子 244（A股年均交易日；两口径同用，排序结论不受该选择影响）。
成本：不计交易成本（任务书未设成本项，报告中如实披露）。
"""
import numpy as np
import pandas as pd

ROOT = r"d:\QuantLab\projects\Project_17_情绪周期择时"
DAILY = r"E:\astock\daily\stock_daily.parquet"
SENT = ROOT + r"\results\sentiment_series.csv"
OUT_CSV = ROOT + r"\results\phase0v2_b_overlay_nav.csv"

# ---- 冻结参数 ----
POS_TH = 0.5          # 仓位分位阈值
ANN = 244             # 年化因子


def log(msg):
    print(msg, flush=True)


def metrics(r):
    """r: 日收益序列(pd.Series)。返回 CAGR/夏普/最大回撤"""
    nav = (1 + r).cumprod()
    years = len(r) / ANN
    cagr = nav.iloc[-1] ** (1 / years) - 1
    sh = r.mean() / r.std(ddof=1) * np.sqrt(ANN)
    mdd = float((nav / nav.cummax() - 1).min())
    return cagr, sh, mdd


# ---------- 1. 情绪分位 ----------
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

# ---------- 2. 小盘池日收益 ----------
need = ["pct_chg", "circ_mv", "is_st", "listed_days"]
daily = pd.read_parquet(DAILY, columns=need)
cal = daily.index.get_level_values(0).unique().sort_values()
cal_pos = pd.Series(np.arange(len(cal)), index=cal)
wide_pct = daily["pct_chg"].unstack()
cols = wide_pct.columns
pool_mask = ((daily["circ_mv"] < 500000) & (daily["is_st"] != 1)
             & (daily["listed_days"].fillna(1e9) >= 60)).unstack()
pool_mask = pool_mask.reindex(index=wide_pct.index, columns=cols)
pool_ret = wide_pct.where(pool_mask).mean(axis=1) / 100.0
n_mem = pool_mask.sum(axis=1)
log("[2] 日线 %s ~ %s 共%d交易日；小盘池日均成员 %.0f 只"
    % (cal.min().date(), cal.max().date(), len(cal), n_mem.mean()))

# ---------- 3. 主窗口与仓位信号 ----------
pr_valid = pct_rank.dropna()
first_pos = cal_pos[pr_valid.index[0]]
start_pos = first_pos + 1  # 冻结口径：首个有效信号的次交易日起
win = cal[start_pos:]
if win[-1] > pr_valid.index[-1]:
    win = win[win <= pr_valid.index[-1]]
sig_prev = pct_rank.reindex(cal).shift(1).loc[win]  # T-1 分位
pos = (sig_prev >= POS_TH).astype(float)
pos[sig_prev.isna()] = np.nan
ret_pool = pool_ret.reindex(win)
df = pd.DataFrame({"pos": pos, "pool": ret_pool}).dropna()
strat = df["pos"] * df["pool"]
log("[3] 主窗口 %s ~ %s 共%d交易日；持仓天数占比 %.1f%%；仓位切换 %d 次"
    % (df.index.min().date(), df.index.max().date(), len(df), df["pos"].mean() * 100,
       int(df["pos"].diff().abs().sum())))

# ---------- 4. 判据判定 ----------
c_b, s_b, m_b = metrics(df["pool"])
c_s, s_s, m_s = metrics(strat)
b1 = bool(s_s > s_b)
b2 = bool(m_s > m_b)   # 回撤为负数，更浅=数值更大
b3 = bool(c_s >= c_b - 0.02)

log("")
log("=== 方向B 判定（冻结：B1∧B2∧B3通过；仅B2∧B3弱阳性）===")
log("%-14s %10s %10s %10s" % ("", "覆盖层", "基准(买入持有)", "差"))
log("%-14s %9.2f%% %9.2f%% %+9.2f%%" % ("CAGR", c_s * 100, c_b * 100, (c_s - c_b) * 100))
log("%-14s %10.3f %10.3f %+10.3f" % ("夏普(244)", s_s, s_b, s_s - s_b))
log("%-14s %9.2f%% %9.2f%% %+9.2fpp" % ("最大回撤", m_s * 100, m_b * 100, (m_s - m_b) * 100))
log("")
log("B1 覆盖层夏普>基准:      %s (%.3f vs %.3f)" % ("PASS" if b1 else "FAIL", s_s, s_b))
log("B2 覆盖层回撤更浅:        %s (%.2f%% vs %.2f%%)" % ("PASS" if b2 else "FAIL", m_s * 100, m_b * 100))
log("B3 CAGR让渡<=2pp:        %s (%.2f%% vs %.2f%%, 让渡%.2fpp)"
    % ("PASS" if b3 else "FAIL", c_s * 100, c_b * 100, (c_b - c_s) * 100))

log("")
log("=== 描述性补充（不构成判据）：分年度 ===")
yy = pd.DataFrame({"pos": df["pos"], "strat": strat, "base": df["pool"]})
yy["y"] = yy.index.year


def yr_row(g):
    cs, ss, ms = metrics(g["strat"])
    cb, sb, mb = metrics(g["base"])
    return pd.Series({"持仓%%": g["pos"].mean() * 100,
                      "覆盖CAGR%": cs * 100, "基准CAGR%": cb * 100,
                      "覆盖MDD%": ms * 100, "基准MDD%": mb * 100})


tab = yy.groupby("y").apply(yr_row, include_groups=False)
log(tab.round(2).to_string())

n_pass = int(b1) + int(b2) + int(b3)
log("")
log("=" * 56)
if b1 and b2 and b3:
    concl = "B1∧B2∧B3 全过 -> 方向二【通过】，进 Phase 1 设计"
elif b2 and b3:
    concl = "仅 B2∧B3 成立 -> 【弱阳性】只作回撤控制工具继续评估，不当作收益增强"
else:
    concl = "-> 方向二【证伪归档】"
log("判定：B1=%s B2=%s B3=%s（通过%d/3条）%s"
    % ("PASS" if b1 else "FAIL", "PASS" if b2 else "FAIL", "PASS" if b3 else "FAIL", n_pass, ""))
log("结论：" + concl)
log("=" * 56)

out = pd.DataFrame({"pos": df["pos"], "pool_ret": df["pool"],
                    "strat_ret": strat,
                    "nav_strat": (1 + strat).cumprod(),
                    "nav_base": (1 + df["pool"]).cumprod()})
out.to_csv(OUT_CSV, encoding="utf-8-sig")
log("净值序列已保存：%s" % OUT_CSV)
