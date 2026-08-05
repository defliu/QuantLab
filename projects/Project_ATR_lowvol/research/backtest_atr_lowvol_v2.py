# coding: utf-8
"""
ATR 低波动策略 v2 — 分散化低波组合 (回测 + 实验开关)
设计(针对 v1 已暴露的问题):
  - 合格域(流动性/风控): 换手率[1,8]% & 近5日成交额>0 & 非ST & 有成交(vol>0) & 上市>=60日
  - 选股: 合格域内按「波动率估计器」升序(真正低波动)取前 N=100, 不等权于高成交额大票
  - 再平衡: 月频(M)/季频(Q), 等权
  - 去掉 v1 的「条件失效」退出(已证明是价值毁灭源)
  - 组合级回撤控制(可选): 默认日度NAV跌破峰值-15% -> 次日开盘清仓(二元, 下个再平衡重入);
                           VOLTARGET=1 时改为波动率目标化渐进降仓(组合 trailing 60日波动率相对目标 10% 缩放仓位, 下限20%)
  - 行业约束(可选): INDCAP=1 时单行业权重<=15%, 防低波挤防御行业
  - 单边成本 0.1%; 防未来函数(当日收盘指标选股, 次日开盘成交); 后复权价 adj_open/adj_close
数据源: E:/astock/daily/stock_daily.parquet ; ROE: E:/astock/finance/fina_indicator.parquet

实验开关(env):
  REBAL   = M(月频,默认) | Q(季频)
  VOLM    = atr(ATR%默认) | rankvol(截面排序波动率) | ivol(特质/市场调整残差波动率)
  QUALITY = 0(关,默认) | 1(低波筛选后按 ROE 双排序)
  VOLTARGET = 0(二元清仓默认) | 1(波动率目标化渐进降仓)
  INDCAP  = 0(关,默认) | 1(单行业<=15%权重上限)
  NHOLD   = 100(默认持仓数)
  KVOL    = 200(质量双排序时先筛的低波宽度, 默认)
  ROEMIN  = 0.0(质量门槛, 默认盈利即可)
输出文件名带 config tag: atr_lowvol_v2_{tag}_nav.csv / _summary.json
"""
import time, json, os
import numpy as np
import pandas as pd
import duckdb

PARQUET   = "E:/astock/daily/stock_daily.parquet"
FINA     = "E:/astock/finance/fina_indicator.parquet"
OUT_DIR  = "D:/QMT_STRATEGIES/backtest_results"
os.makedirs(OUT_DIR, exist_ok=True)

# ---- 实验开关 ----
REBAL   = os.environ.get("REBAL", "M").upper()
VOLM    = os.environ.get("VOLM", "atr").lower()
QUALITY = os.environ.get("QUALITY", "0") == "1"
N_HOLD  = int(os.environ.get("NHOLD", "100"))
K_VOL   = int(os.environ.get("KVOL", "200"))
ROE_MIN = float(os.environ.get("ROEMIN", "0.0"))
VOLTARGET = os.environ.get("VOLTARGET", "0") == "1"   # 波动率目标化渐进降仓(替代二元清仓)
INDCAP    = os.environ.get("INDCAP", "0") == "1"      # 单行业<=15%权重上限
WEIGHT  = os.environ.get("WEIGHT", "equal").lower()   # equal(等权) / volparity(波动率平价, 按1/atr_pct归一)
LOT     = os.environ.get("LOT", "0") == "1"           # 整数手+最小建仓资金约束(模拟10万实盘可交易性)
MIN_LOT_VALUE = float(os.environ.get("MINLOT", "1500"))  # 目标金额低于此则不建仓(防手续费占比过高+取整损耗)

# ---- 固定参数 ----
ATR_WINDOW     = int(os.environ.get("ATWIN", "14"))
VOL_WINDOW     = 60
MIN_TURNOVER   = 1.0
MAX_TURNOVER   = 8.0
MIN_BARS       = 60
MAX_TOTAL_RATIO= 0.95
COST           = 0.001
DD_EXIT        = -0.15
INIT_CAPITAL   = float(os.environ.get("CAPITAL", "100000"))   # 初始资金(模拟实盘规模)
WARMUP_START   = "2022-01-01"
BACKTEST_START = "2023-01-01"
# ---- 实盘化参数 ----
VT_TARGET = 0.10   # 目标年化波动率(组合)
VT_FLOOR  = 0.20   # 波动率目标化仓位下限(防过度空仓)
INDCAP_PCT = 0.15  # 单行业权重上限

parts = [VOLM, REBAL, "N%d"%N_HOLD]
if QUALITY:    parts.append("Q")
if VOLTARGET:  parts.append("VT")
if INDCAP:     parts.append("IC")
if WEIGHT != "equal": parts.append("VP")
if LOT:        parts.append("LOT")
if abs(INIT_CAPITAL - 100000.0) > 1:
    parts.append("C%d" % int(round(INIT_CAPITAL)))
if VOLM == "atr" and ATR_WINDOW != 14:
    parts.insert(0, "atr%d" % ATR_WINDOW)
TAG = "_".join(parts)
VOL_LABEL = {"atr":"ATR%","rankvol":"RANKVOL(截面排序波动率)","ivol":"IVOL(特质波动率)"}[VOLM]
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
           open, high, low, close, vol, amount, turnover_rate, adj_factor, is_st
    FROM read_parquet('{PARQUET}')
    WHERE CAST(trade_date AS DATE) >= DATE '{WARMUP_START}'
""").fetchdf()
con.close()
print("    加载 %d 行, 耗时 %.1fs" % (len(df), time.time()-t0))

for c in ["open","high","low","close"]:
    df["adj_"+c] = df[c] * df["adj_factor"]
df["is_st"] = (df["is_st"] == 1.0)
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

# 波动率估计器(按需)
if VOLM in ("rankvol","ivol"):
    print("[2b] 计算波动率估计器(%s) ..." % VOLM)
    df["ret"] = g["adj_close"].pct_change()
    if VOLM == "rankvol":
        # 截面排序波动率: 每日收益率在当日全市场截面上的分位(0~1), 再对时序做滚动 std
        # -> 对涨跌停等极端值稳健(被压到分位端点)
        df["xrank"] = df.groupby("date")["ret"].rank(pct=True)
        df["vol_est"] = g["xrank"].transform(lambda s: s.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std())
    else:  # ivol: 市场调整残差波动率 = std(r_i - r_mkt), 剥离系统性波动, 留特质波动
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
if VOLM == "atr":
    volpivot = pivot("atr_pct")
else:
    volpivot = pivot("vol_est")

# 行业映射(行业 cap 用)
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
    """按信号序保留, 单行业最多 ceil(cap_pct*N_HOLD) 只 -> 权重上限 cap_pct"""
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
    """合格域 -> 按波动率估计器升序选前 N; 若 QUALITY 则先扩到 K_VOL 再按 ROE 双排序; 最后行业 cap; 返回 (codes, 权重dict)"""
    sub = bt[bt["date"] == d]
    m = (sub["turnover_rate"] >= MIN_TURNOVER) & (sub["turnover_rate"] <= MAX_TURNOVER) & \
        (sub["amt5"] > 0) & (sub["vol"] > 0) & (~sub["is_st"]) & (sub["bar_idx"] >= MIN_BARS)
    cand = sub[m]
    if VOLM == "atr":
        cand = cand.sort_values("atr_pct").head(N_HOLD)
    else:
        cand = cand.sort_values("vol_est").head(N_HOLD)
    if QUALITY:
        # 低波筛选扩到 K_VOL, 再按 ROE 双排序取前 N_HOLD
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
    codes = cand["ts_code"].tolist()
    if INDCAP:
        codes = apply_indcap(codes)
    if WEIGHT == "volparity":
        sig = cand[cand["ts_code"].isin(codes)].set_index("ts_code")["atr_pct"]
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

# 质量(ROE)数据: 决策日有效值(end_date<=决策日, 防未来函数)
roe_at = {}
if QUALITY:
    from bisect import bisect_right
    print("[4b] 加载 ROE (fina_indicator) ...")
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
    # 按股票分组, 存 (end_dates, roes); 手动 asof 避免 merge_asof 的全局排序约束
    stock_groups = {}
    for code, g in fina.groupby("ts_code", sort=False):
        stock_groups[code] = (g["end_date"].values, g["roe"].values)
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

print("[6] 回测主循环(%s / %s / %s / %s / %s) ..." %
      (VOL_LABEL, REBAL, "质量双排序" if QUALITY else "纯低波", VT_LABEL, IC_LABEL))
cash = INIT_CAPITAL
positions = {}
nav_series = []
trades = []
peak = INIT_CAPITAL
pending_liquidate = False
derisked = False
in_cash_days = 0
rebalance_days = 0
lot_skipped = 0          # LOT 模式下因资金不足1手/低于门槛被跳过的股次数
lot_target_total = 0     # LOT 模式下目标建仓股次数(含成功与跳过)
eff_hold_hist = []       # 每次再平衡后的有效持仓数(实盘可交易性)

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
        # 波动率目标化: 组合 trailing 60日年化波动率相对目标 10% 缩放仓位(下限20%)
        vol_scale = 1.0
        if VOLTARGET and len(nav_series) >= 2:
            recent = pd.Series([x[1] for x in nav_series[-61:-1]])
            rets = recent.pct_change().dropna()
            if len(rets) >= 20:
                rv = rets.std() * np.sqrt(252)
                vol_scale = max(VT_FLOOR, min(1.0, VT_TARGET / rv)) if rv > 1e-9 else 1.0
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
                # 整数手(100股)约束 + 最小建仓资金门槛, 模拟真实可交易性
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
                if cost <= cash:
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
      (TAG, VOL_LABEL, REBAL, "质量双排序" if QUALITY else "纯低波", VT_LABEL, IC_LABEL, W_LABEL, LOT_LABEL))
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

nav_df.to_csv(os.path.join(OUT_DIR,"atr_lowvol_v2_%s_nav.csv"%TAG))
summary = {"tag":TAG,"config":{"VOLM":VOLM,"REBAL":REBAL,"QUALITY":QUALITY,
           "VOLTARGET":VOLTARGET,"INDCAP":INDCAP,"N_HOLD":N_HOLD,"K_VOL":K_VOL,
           "ROE_MIN":ROE_MIN,"DD_EXIT":DD_EXIT,"VT_TARGET":VT_TARGET,"INDCAP_PCT":INDCAP_PCT,
           "cost_oneway":COST,"turnover":[MIN_TURNOVER,MAX_TURNOVER]},
           "full":full,"insample_2023_2024":insample,"oos_2025_2026":oos,
           "years":{int(y.year):round(r*100,2) for y,r in years.items()},
           "in_cash_pct":round(100*in_cash_days/len(nav_df),1),"n_trades":len(trades),
           "weight":WEIGHT,"lot":LOT,"min_lot_value":MIN_LOT_VALUE,
           "eff_hold_avg":round(float(np.mean(eff_hold_hist)),1) if eff_hold_hist else n,
           "lot_fill_rate":round(100*(1-lot_skipped/max(1,lot_target_total)),1) if LOT else None}
with open(os.path.join(OUT_DIR,"atr_lowvol_v2_%s_summary.json"%TAG),"w") as f:
    json.dump(summary,f,indent=2,ensure_ascii=False)
print("结果已保存: atr_lowvol_v2_%s_{nav.csv,summary.json}" % TAG)
