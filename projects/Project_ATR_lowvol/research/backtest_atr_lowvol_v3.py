# coding: utf-8
"""
ATR 低波动策略 v3 — 多因子复合 (回测 + 实验开关)
在 v2 分散化低波组合基础上, 新增:
  - VALUE  = 0/1 : 价值因子 (BP=1/pb 截面排序, 高 BP 偏好)
  - MOM    = 0/1 : 动量因子 (12-1 月, 跳过最近 1 月反包: 价格 252 日前 / 21 日前 - 1)
  - COMBINE= 0/1 : 复合选股 (z/rank 加总分 = 低波 + 质量 + 价值 + 动量); 关则沿用 v2 顺序筛选
  - VT_CAP = 波动率目标化杠杆上限 (默认 1.0=不加杠杆; 设 2.0 演示两融杠杆天花板)
其余沿用 v2:
  - 合格域: 换手[1,8]% & amt5>0 & vol>0 & 非ST & 上市>=60日
  - VOLM=atr/rankvol/ivol ; REBAL=M/Q ; QUALITY ; VOLTARGET ; INDCAP ; WEIGHT ; LOT ; CAPITAL
数据源: E:/astock/daily/stock_daily.parquet (含 pb) ; ROE: fina_indicator.parquet
"""
import time, json, os
import numpy as np
import pandas as pd
import duckdb

PARQUET   = "E:/astock/daily/stock_daily.parquet"
FINA      = "E:/astock/finance/fina_indicator.parquet"
OUT_DIR   = "D:/QMT_STRATEGIES/backtest_results"
os.makedirs(OUT_DIR, exist_ok=True)

# ---- 实验开关 ----
REBAL    = os.environ.get("REBAL", "M").upper()
VOLM     = os.environ.get("VOLM", "atr").lower()
QUALITY  = os.environ.get("QUALITY", "0") == "1"
VALUE    = os.environ.get("VALUE", "0") == "1"      # 价值因子 BP
MOM      = os.environ.get("MOM", "0") == "1"        # 动量因子 12-1m
COMBINE  = os.environ.get("COMBINE", "0") == "1"    # 复合选股(z/rank 加总)
N_HOLD   = int(os.environ.get("NHOLD", "100"))
K_VOL    = int(os.environ.get("KVOL", "200"))
ROE_MIN  = float(os.environ.get("ROEMIN", "0.0"))
VOLTARGET= os.environ.get("VOLTARGET", "0") == "1"
INDCAP   = os.environ.get("INDCAP", "0") == "1"
WEIGHT   = os.environ.get("WEIGHT", "equal").lower()
LOT      = os.environ.get("LOT", "0") == "1"
MIN_LOT_VALUE = float(os.environ.get("MINLOT", "1500"))
VT_CAP   = float(os.environ.get("VT_CAP", "1.0"))    # 杠杆上限(波动率目标化); >1 需两融
LEVMULT  = float(os.environ.get("LEVMULT", "1.0"))   # 恒定杠杆(两融); >1 时允许 cash 为负(借入)
MOMGATE  = os.environ.get("MOMGATE", "0") == "1"     # 动量门控: 低波质量选中再剔除 12-1 月动量<=0 的近期输家

# ---- 固定参数 ----
ATR_WINDOW     = int(os.environ.get("ATWIN", "14"))
VOL_WINDOW     = 60
MIN_TURNOVER   = 1.0
MAX_TURNOVER   = 8.0
MIN_BARS       = 60
MAX_TOTAL_RATIO= 0.95
COST           = 0.001
DD_EXIT        = -0.15
INIT_CAPITAL   = float(os.environ.get("CAPITAL", "100000"))
WARMUP_START   = "2021-01-01"
BACKTEST_START = "2023-01-01"
VT_TARGET = 0.10
VT_FLOOR  = 0.20
INDCAP_PCT = 0.15
MOM_LOOK  = 252
MOM_SKIP  = 21

parts = [VOLM, REBAL, "N%d"%N_HOLD]
if QUALITY:    parts.append("Q")
if VALUE:      parts.append("VAL")
if MOM:        parts.append("MOM")
if COMBINE:    parts.append("CMB")
if VOLTARGET:  parts.append("VT")
if INDCAP:     parts.append("IC")
if WEIGHT != "equal": parts.append("VP")
if LOT:        parts.append("LOT")
if abs(INIT_CAPITAL - 100000.0) > 1:
    parts.append("C%d" % int(round(INIT_CAPITAL)))
if VT_CAP != 1.0:
    parts.append("LEV%.1f"%VT_CAP)
if LEVMULT != 1.0:
    parts.append("x%.1f"%LEVMULT)
if MOMGATE:
    parts.append("MG")
if VOLM == "atr" and ATR_WINDOW != 14:
    parts.insert(0, "atr%d" % ATR_WINDOW)
TAG = "_".join(parts)
VOL_LABEL = {"atr":"ATR%","rankvol":"RANKVOL","ivol":"IVOL"}[VOLM]
VT_LABEL  = "波动率目标化" if VOLTARGET else "二元清仓"
IC_LABEL  = "行业cap15%" if INDCAP else "无行业约束"
W_LABEL   = "波动率平价" if WEIGHT=="volparity" else "等权"
LOT_LABEL = "整数手约束" if LOT else "无交易约束"

def val(piv, code, d):
    try:
        if code in piv.index and d in piv.columns:
            v = piv.loc[code, d]
            return np.nan if pd.isna(v) else float(v)
    except Exception:
        pass
    return np.nan

t0 = time.time()
print("[1] 加载 %s ..." % PARQUET)
con = duckdb.connect()
df = con.execute(f"""
    SELECT ts_code, CAST(trade_date AS DATE) AS date,
           open, high, low, close, vol, amount, turnover_rate, adj_factor, is_st, pb
    FROM read_parquet('{PARQUET}')
    WHERE CAST(trade_date AS DATE) >= DATE '{WARMUP_START}'
""").fetchdf()
con.close()
print("    加载 %d 行, 耗时 %.1fs" % (len(df), time.time()-t0))

for c in ["open","high","low","close"]:
    df["adj_"+c] = df[c] * df["adj_factor"]
df["is_st"] = (df["is_st"] == 1.0)
df["bp"] = np.where((df["pb"]>0) & (~df["pb"].isna()), 1.0/df["pb"], np.nan)
df = df.sort_values(["ts_code","date"]).reset_index(drop=True)

print("[2] 计算 ATR(%d)%% ..." % ATR_WINDOW)
g = df.groupby("ts_code", sort=False)
df["prev_close"] = g["adj_close"].shift(1)
df["tr1"] = df["adj_high"] - df["adj_low"]
df["tr2"] = (df["adj_high"] - df["prev_close"]).abs()
df["tr3"] = (df["adj_low"]  - df["prev_close"]).abs()
df["tr"]  = df[["tr1","tr2","tr3"]].max(axis=1)
df["atr14"] = g["tr"].transform(lambda s: s.rolling(ATR_WINDOW, min_periods=ATR_WINDOW).mean())
df["atr_pct"] = df["atr14"] / df["adj_close"] * 100.0
df["amt5"] = g["amount"].transform(lambda s: s.rolling(5, min_periods=5).sum())
df["bar_idx"] = g.cumcount()

if VOLM in ("rankvol","ivol"):
    print("[2b] 计算波动率估计器(%s) ..." % VOLM)
    df["ret"] = g["adj_close"].pct_change()
    if VOLM == "rankvol":
        df["xrank"] = df.groupby("date")["ret"].rank(pct=True)
        df["vol_est"] = g["xrank"].transform(lambda s: s.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std())
    else:
        df["mkt_ret"] = df.groupby("date")["ret"].transform("mean")
        df["madj_ret"] = df["ret"] - df["mkt_ret"]
        df["vol_est"] = g["madj_ret"].transform(lambda s: s.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std())
    df["vol_est"] = df["vol_est"].astype("float64")

bt = df[df["date"] >= pd.Timestamp(BACKTEST_START)].copy()
print("[3] 回测区间: %s ~ %s, %d 行" % (bt["date"].min(), bt["date"].max(), len(bt)))

print("[4] 构建 pivot ...")
dates = sorted(bt["date"].unique())
def pivot(v):
    return bt.pivot_table(index="ts_code", columns="date", values=v, aggfunc="last")
px    = pivot("adj_close")
opx   = pivot("adj_open")
tovp  = pivot("turnover_rate")
stp   = pivot("is_st").fillna(False)
amt5p = pivot("amt5")
volp  = pivot("vol")
bpp   = pivot("bp")                      # 价值因子 pivot
if VOLM == "atr":
    volpivot = pivot("atr_pct")
else:
    volpivot = pivot("vol_est")

# 动量 pivot: 价格 MOM_LOOK 日前 / MOM_SKIP 日前 - 1 (12-1 月, 跳过最近 1 月反包)
print("[4b] 计算动量 pivot (12-1m) ...")
momp = px.shift(MOM_LOOK, axis=1) / px.shift(MOM_SKIP, axis=1) - 1.0
momp = momp.replace([np.inf, -np.inf], np.nan)

IND = {}
if INDCAP:
    print("[4c] 加载行业映射 (stock_basic.industry) ...")
    con3 = duckdb.connect()
    ind_df = con3.execute(
        "SELECT ts_code, industry FROM read_parquet('E:/astock/basic/stock_basic.parquet') "
        "WHERE industry IS NOT NULL").fetchdf()
    con3.close()
    IND = dict(zip(ind_df["ts_code"].astype(str), ind_df["industry"].astype(str)))
    print("    行业数 %d, 覆盖股票 %d" % (ind_df["industry"].nunique(), len(IND)))

def apply_indcap(codes, cap_pct=INDCAP_PCT):
    cap_n = max(1, int(round(cap_pct * N_HOLD)))
    counts = {}
    out = []
    for c in codes:
        ind = IND.get(str(c))
        if ind is None:
            out.append(c); continue
        if counts.get(ind, 0) < cap_n:
            out.append(c); counts[ind] = counts.get(ind, 0) + 1
    return out

def eligible_at(d):
    """合格域 -> 选股. COMBINE: rank 加总(低波+质量+价值+动量); 否则 v2 顺序筛选."""
    sub = bt[bt["date"] == d]
    m = (sub["turnover_rate"] >= MIN_TURNOVER) & (sub["turnover_rate"] <= MAX_TURNOVER) & \
        (sub["amt5"] > 0) & (sub["vol"] > 0) & (~sub["is_st"]) & (sub["bar_idx"] >= MIN_BARS)
    cand = sub[m].copy()
    lv_col = "atr_pct" if VOLM == "atr" else "vol_est"

    if COMBINE:
        # 以 ts_code 为索引, 保证各因子 rank 按股票对齐
        cand = cand.set_index("ts_code")
        cand["_lv_r"] = cand[lv_col].rank()                 # 升序: 低波=小rank
        cand["_score"] = -cand["_lv_r"]                     # 低波越好 score 越高
        if VALUE and d in bpp.columns:
            bv = bpp[d].reindex(cand.index).astype(float)
            cand["_val_r"] = bv.rank(ascending=False)       # 高 BP 偏好
            cand["_score"] = cand["_score"] + cand["_val_r"].fillna(cand["_val_r"].median())
        if MOM and d in momp.columns:
            mv = momp[d].reindex(cand.index).astype(float)
            cand["_mom_r"] = mv.rank(ascending=False)       # 高动量偏好
            cand["_score"] = cand["_score"] + cand["_mom_r"].fillna(cand["_mom_r"].median())
        if QUALITY:
            roe_s = roe_at.get(d)
            if roe_s is not None and len(roe_s) > 0:
                cand = cand.join(roe_s.rename("roe"))
                cand = cand[cand["roe"] > ROE_MIN]
                cand["_q_r"] = cand["roe"].rank(ascending=False)
                cand["_score"] = cand["_score"] + cand["_q_r"].fillna(cand["_q_r"].median())
        cand = cand.sort_values("_score", ascending=False).head(N_HOLD).reset_index()
    else:
        if VOLM == "atr":
            cand = cand.sort_values("atr_pct").head(N_HOLD)
        else:
            cand = cand.sort_values("vol_est").head(N_HOLD)
        if QUALITY:
            if VOLM == "atr":
                cands = sub[m].sort_values("atr_pct").head(K_VOL)
            else:
                cands = sub[m].sort_values("vol_est").head(K_VOL)
            roe_s = roe_at.get(d)
            if roe_s is not None and len(roe_s) > 0:
                cands = cands.merge(roe_s.rename("roe"), left_on="ts_code", right_index=True, how="left")
                cands = cands[cands["roe"] > ROE_MIN]
                cands = cands.sort_values("roe", ascending=False).head(N_HOLD)
            cand = cands

    if MOMGATE and d in momp.columns:
        mv = momp[d].reindex(cand["ts_code"]).astype(float).to_numpy()
        cand = cand[mv > 0]
    codes = cand["ts_code"].tolist()
    if INDCAP:
        codes = apply_indcap(codes)
    if WEIGHT == "volparity":
        sig = cand[cand["ts_code"].isin(codes)].set_index("ts_code")[lv_col]
        inv = (1.0 / sig.replace(0, np.nan)).fillna(0.0)
        w = (inv / inv.sum()).to_dict() if inv.sum() > 0 else None
    else:
        w = None
    return codes, w

# 决策日
seen = set()
decision_dates = []
for d in dates:
    k = (d.year, d.month)
    if REBAL == "Q" and d.month not in (1,4,7,10):
        continue
    if k not in seen:
        seen.add(k); decision_dates.append(d)
date_to_pos = {d: i for i, d in enumerate(dates)}
target_by_exec = {}

roe_at = {}
if QUALITY:
    from bisect import bisect_right
    print("[4d] 加载 ROE (fina_indicator) ...")
    con2 = duckdb.connect()
    fina = con2.execute("""
        SELECT ts_code,
          CAST(CASE
            WHEN regexp_full_match(CAST(end_date AS VARCHAR), '^\\d{8}$')
                 THEN strptime(CAST(end_date AS VARCHAR), '%%Y%%m%%d')
                 ELSE strptime(CAST(end_date AS VARCHAR), '%%Y-%%m-%%d %%H:%%M:%%S')
          END AS DATE) AS end_date, roe
        FROM read_parquet('%s')
        WHERE roe IS NOT NULL
    """ % FINA).fetchdf()
    con2.close()
    fina = fina.sort_values(["ts_code","end_date"])
    stock_groups = {}
    for code, gg in fina.groupby("ts_code", sort=False):
        stock_groups[code] = (gg["end_date"].values, gg["roe"].values)
    for d in decision_dates:
        mp = {}
        dd = np.datetime64(d)
        for code, (eds, roes) in stock_groups.items():
            j = int(np.searchsorted(eds, dd, side="right")) - 1
            if j >= 0:
                mp[code] = float(roes[j])
        roe_at[d] = pd.Series(mp)
    print("    ROE 决策日有效值就绪: %d 个决策日" % len(roe_at))

weights_by_exec = {}
for d in decision_dates:
    pos = date_to_pos[d]
    if pos + 1 < len(dates):
        tgt, wts = eligible_at(d)
        target_by_exec[dates[pos+1]] = tgt
        weights_by_exec[dates[pos+1]] = wts
print("[5] %s频决策 %d 次 (首月 %s, 末次执行 %s)" %
      (REBAL, len(decision_dates), decision_dates[0], list(target_by_exec.keys())[-1]))

print("[6] 回测主循环(%s / %s / %s / %s / %s / %s / %s) ..." %
      (VOL_LABEL, REBAL, "复合" if COMBINE else ("质量双排序" if QUALITY else "纯低波"),
       VT_LABEL, IC_LABEL, W_LABEL, LOT_LABEL))
cash = INIT_CAPITAL
positions = {}
nav_series = []
trades = []
peak = INIT_CAPITAL
pending_liquidate = False
derisked = False
in_cash_days = 0
rebalance_days = 0
lot_skipped = 0
lot_target_total = 0
eff_hold_hist = []

for i, d in enumerate(dates):
    if (not VOLTARGET) and pending_liquidate:
        for code in list(positions):
            o = val(opx, code, d)
            if not pd.isna(o):
                cash += positions[code] * o * (1-COST)
            del positions[code]
        pending_liquidate = False
        derisked = True
        peak = cash
    elif d in target_by_exec:
        rebalance_days += 1
        target = target_by_exec[d]
        wdict = weights_by_exec.get(d)
        mv = 0.0
        for c in positions:
            pc = val(px, c, d)
            if not pd.isna(pc): mv += positions[c]*pc
        total_equity = cash + mv
        vol_scale = 1.0
        if VOLTARGET and len(nav_series) >= 2:
            recent = pd.Series([x[1] for x in nav_series[-61:-1]])
            rets = recent.pct_change().dropna()
            if len(rets) >= 20:
                rv = rets.std() * np.sqrt(252)
                vol_scale = max(VT_FLOOR, min(VT_CAP, VT_TARGET / rv)) if rv > 1e-9 else 1.0
        if LEVMULT > 1.0:
            vol_scale = min(VT_CAP * LEVMULT, vol_scale * LEVMULT)   # 恒定杠杆(两融), 可与 VT 叠加
        invest = total_equity * MAX_TOTAL_RATIO * vol_scale
        n = len(target)
        per = invest / n if n>0 else 0.0
        for code in list(positions):
            if code not in target:
                o = val(opx, code, d)
                if not pd.isna(o):
                    cash += positions[code]*o*(1-COST)
                    trades.append({"date":str(d),"code":code,"side":"SELL","price":round(o,4),
                                   "shares":positions[code],"reason":"调出"})
                del positions[code]
        for code in target:
            o = val(opx, code, d)
            if pd.isna(o):
                continue
            if wdict is not None:
                target_value = invest * wdict.get(code, 0.0)
            else:
                target_value = per
            if LOT:
                lot_val = o * 100 * (1+COST)
                lot_target_total += 1
                if lot_val <= 0 or target_value < MIN_LOT_VALUE:
                    lot_skipped += 1
                    continue
                lots = int(target_value // lot_val)
                if lots < 1:
                    lot_skipped += 1
                    continue
                target_shares = float(lots * 100)
            else:
                target_shares = target_value / (o*(1+COST))
            cur = positions.get(code, 0.0)
            if target_shares > cur + 1e-9:
                buy = target_shares - cur
                cost = buy * o * (1+COST)
                if LEVMULT > 1.0 or cost <= cash:
                    cash -= cost
                    positions[code] = cur + buy
                    trades.append({"date":str(d),"code":code,"side":"BUY","price":round(o,4),
                                   "shares":round(buy,2)})
            elif target_shares < cur - 1e-9:
                sell = cur - target_shares
                cash += sell * o * (1-COST)
                positions[code] = target_shares
                trades.append({"date":str(d),"code":code,"side":"SELL","price":round(o,4),
                               "shares":round(sell,2),"reason":"再平衡"})
        eff_hold_hist.append(len(positions))
        if derisked:
            peak = total_equity
            derisked = False

    mv = 0.0
    for c in positions:
        pc = val(px, c, d)
        if not pd.isna(pc): mv += positions[c]*pc
    nav = cash + mv
    nav_series.append((d, nav))
    if len(positions) == 0:
        in_cash_days += 1
    if nav > peak:
        peak = nav
    if (not VOLTARGET) and (not pending_liquidate) and nav < peak*(1+DD_EXIT):
        pending_liquidate = True

print("    循环完成, 耗时 %.1fs" % (time.time()-t0))

nav_df = pd.DataFrame(nav_series, columns=["date","nav"]).set_index("date")
nav_df["ret"] = nav_df["nav"].pct_change().fillna(0)
def metrics(nav):
    n = len(nav)
    if n < 2: return {}
    total = nav["nav"].iloc[-1]/nav["nav"].iloc[0]-1
    ann = (1+total)**(252/n)-1
    daily = nav["nav"].pct_change().dropna()
    sharpe = daily.mean()/daily.std()*np.sqrt(252) if daily.std()>0 else 0
    peakv = nav["nav"].cummax()
    mdd = (nav["nav"]/peakv-1).min()
    return {"total_ret":round(total*100,2),"annual":round(ann*100,2),
            "sharpe":round(sharpe,3),"max_dd":round(mdd*100,2),"days":n}

full = metrics(nav_df)
insample = metrics(nav_df[(nav_df.index>=pd.Timestamp("2023-01-01"))&(nav_df.index<pd.Timestamp("2025-01-01"))])
oos = metrics(nav_df[nav_df.index>=pd.Timestamp("2025-01-01")])
years = nav_df.resample("YE").apply(lambda x: x["nav"].iloc[-1]/x["nav"].iloc[0]-1)
ystr = " ".join("%d:%+.1f%%"%(y.year,r*100) for y,r in years.items())

print("\n===== [%s] %s / %s / %s / %s / %s / %s / %s =====" %
      (TAG, VOL_LABEL, REBAL, "复合" if COMBINE else ("质量双排序" if QUALITY else "纯低波"),
       VT_LABEL, IC_LABEL, W_LABEL, LOT_LABEL))
print("  全周期: 总 %.2f%% 年化 %.2f%% 夏普 %.3f 回撤 %.2f%%" %
      (full["total_ret"],full["annual"],full["sharpe"],full["max_dd"]))
print("  样本内2023-24: 总 %.2f%% 年化 %.2f%% 夏普 %.3f 回撤 %.2f%%" %
      (insample.get("total_ret",0),insample.get("annual",0),insample.get("sharpe",0),insample.get("max_dd",0)))
print("  样本外2025-26: 总 %.2f%% 年化 %.2f%% 夏普 %.3f 回撤 %.2f%%" %
      (oos.get("total_ret",0),oos.get("annual",0),oos.get("sharpe",0),oos.get("max_dd",0)))
print("  年度: %s" % ystr)
print("  空仓占比 %.1f%%  交易笔数 %d  再平衡 %d" % (100*in_cash_days/len(nav_df), len(trades), rebalance_days))
if LOT:
    _fill = 100*(1 - lot_skipped/max(1,lot_target_total))
    _eff = float(np.mean(eff_hold_hist)) if eff_hold_hist else float(n)
    print("  有效持仓均值 %.1f  建仓成功率 %.1f%%  (跳过 %d/%d, 门槛>=%.0f元)" %
          (_eff, _fill, lot_skipped, lot_target_total, MIN_LOT_VALUE))
print("RESULT|%s|%.2f|%.2f|%.3f|%.2f|%.2f|%.2f|%.2f|%.2f|%.1f|%d|%.1f|%.1f|%s" %
      (TAG, full["total_ret"], full["annual"], full["sharpe"], full["max_dd"],
       insample.get("total_ret",0), oos.get("total_ret",0),
       insample.get("sharpe",0), oos.get("sharpe",0),
       100*in_cash_days/len(nav_df), len(trades),
       float(np.mean(eff_hold_hist)) if eff_hold_hist else float(n),
       100*(1-lot_skipped/max(1,lot_target_total)) if LOT else -1.0,
       ystr))

nav_df.to_csv(os.path.join(OUT_DIR,"atr_lowvol_v3_%s_nav.csv"%TAG))
summary = {"tag":TAG,"config":{"VOLM":VOLM,"REBAL":REBAL,"QUALITY":QUALITY,"VALUE":VALUE,
           "MOM":MOM,"COMBINE":COMBINE,"VOLTARGET":VOLTARGET,"INDCAP":INDCAP,"N_HOLD":N_HOLD,
           "K_VOL":K_VOL,"ROE_MIN":ROE_MIN,"VT_TARGET":VT_TARGET,"VT_CAP":VT_CAP,"INDCAP_PCT":INDCAP_PCT,
           "cost_oneway":COST,"turnover":[MIN_TURNOVER,MAX_TURNOVER]},
           "full":full,"insample_2023_2024":insample,"oos_2025_2026":oos,
           "years":{int(y.year):round(r*100,2) for y,r in years.items()},
           "in_cash_pct":round(100*in_cash_days/len(nav_df),1),"n_trades":len(trades),
           "weight":WEIGHT,"lot":LOT,"min_lot_value":MIN_LOT_VALUE,
           "eff_hold_avg":round(float(np.mean(eff_hold_hist)),1) if eff_hold_hist else n,
           "lot_fill_rate":round(100*(1-lot_skipped/max(1,lot_target_total)),1) if LOT else None}
with open(os.path.join(OUT_DIR,"atr_lowvol_v3_%s_summary.json"%TAG),"w") as f:
    json.dump(summary,f,indent=2,ensure_ascii=False)
print("结果已保存: atr_lowvol_v3_%s_{nav.csv,summary.json}" % TAG)
