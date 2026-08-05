# coding: utf-8
"""
ATR 低波动策略 — 离线回测（基于全景说明书界定的第一数据源 E:/astock）
忠实还原 atr_lowvol/strategy_atr.py 的选股 + 风控规则：
  选股: ATR(14)%<6 + 换手率1-8% + 近5日成交额降序取前3(持仓上限)
  卖出: 止损-8% / 止盈+20% / 移动止损(从峰值回落-10%) / 条件失效(持仓A%%>=6 或 换手越界)
  资金: 单票目标30% / 总仓位上限90% / 初始本金10万
  约定: 当日收盘算信号，次日开盘成交(防未来函数)；单边成本0.1%；后复权价算ATR与pnl
周期: 2023-01-01 ~ 数据实际截止(2026-07-31)
"""
import time, json, os
import numpy as np
import pandas as pd
import duckdb

PARQUET = "E:/astock/daily/stock_daily.parquet"
OUT_DIR = "D:/QMT_STRATEGIES/backtest_results"
os.makedirs(OUT_DIR, exist_ok=True)

# ---- 策略参数(与 strategy_atr.py 对齐) ----
ATR_THRESHOLD   = 6.0
MIN_TURNOVER    = 1.0
MAX_TURNOVER    = 8.0
MIN_BARS        = 60
STOP_LOSS       = -0.08
TAKE_PROFIT     = 0.20
TRAILING_STOP   = -0.10
MAX_HOLD        = 3
TARGET_RATIO    = 0.30
MAX_TOTAL_RATIO = 0.90
INIT_CAPITAL    = 100000.0
COST            = 0.001   # 单边 0.1%

# 消融开关: 设为 1 时关闭"条件失效"退出(只保留 止损/止盈/移动止损), 用于评估核心是否有肉
ABLATE_COND_FAILURE = os.environ.get("ABLATE_CF", "0") == "1"

BACKTEST_START  = "2023-01-01"
WARMUP_START    = "2022-01-01"

def val(piv, code, d):
    """安全取 pivot 值: 缺失/越界返回 nan"""
    try:
        if code in piv.index and d in piv.columns:
            v = piv.loc[code, d]
            return np.nan if pd.isna(v) else float(v)
    except Exception:
        pass
    return np.nan

def is_suspended(susp, code, d):
    # 数据源 suspend_type 字段不可靠(正常股也填充非空), 改以"当日是否有成交"判定
    return False

t0 = time.time()
print("[1] 加载数据 %s ..." % PARQUET)
con = duckdb.connect()
df = con.execute(f"""
    SELECT ts_code,
           CAST(trade_date AS DATE) AS date,
           open, high, low, close, vol, amount,
           turnover_rate, adj_factor, is_st,
           COALESCE(suspend_type,'') AS suspend_type
    FROM read_parquet('{PARQUET}')
    WHERE CAST(trade_date AS DATE) >= DATE '{WARMUP_START}'
""").fetchdf()
con.close()
print("    加载 %d 行, 耗时 %.1fs" % (len(df), time.time()-t0))

# 复权价(后复权)
for c in ["open","high","low","close"]:
    df["adj_"+c] = df[c] * df["adj_factor"]
df["is_st"] = (df["is_st"] == 1.0)
df["suspended"] = df["suspend_type"].astype(str).str.strip() != ""
df["ts_code"] = df["ts_code"].astype("category")
df = df.sort_values(["ts_code","date"]).reset_index(drop=True)

print("[2] 计算 ATR(14)% / 近5日成交额 ...")
g = df.groupby("ts_code", sort=False)
df["prev_close"] = g["adj_close"].shift(1)
df["tr1"] = df["adj_high"] - df["adj_low"]
df["tr2"] = (df["adj_high"] - df["prev_close"]).abs()
df["tr3"] = (df["adj_low"]  - df["prev_close"]).abs()
df["tr"]  = df[["tr1","tr2","tr3"]].max(axis=1)
df["atr14"] = g["tr"].transform(lambda s: s.rolling(14, min_periods=14).mean())
df["atr_pct"] = df["atr14"] / df["adj_close"] * 100.0
df["amt5"] = g["amount"].transform(lambda s: s.rolling(5, min_periods=5).sum())
df["bar_idx"] = g.cumcount()
print("    ATR计算完成, 耗时 %.1fs" % (time.time()-t0))

bt = df[df["date"] >= pd.Timestamp(BACKTEST_START)].copy()
print("[3] 回测区间: %s ~ %s, %d 行" % (bt["date"].min(), bt["date"].max(), len(bt)))

print("[4] 构建 pivot 矩阵 ...")
dates = sorted(bt["date"].unique())
def pivot(v):
    return bt.pivot_table(index="ts_code", columns="date", values=v, aggfunc="last")
px    = pivot("adj_close")
opx   = pivot("adj_open")
atrp  = pivot("atr_pct")
tov   = pivot("turnover_rate")
amt5p = pivot("amt5")
stp   = pivot("is_st").fillna(False)
susp  = pivot("suspended").fillna(False)
print("    pivot完成, %d 股票 x %d 交易日" % (px.shape[0], px.shape[1]))

print("[5] 预计算每日候选 ...")
mask = (bt["atr_pct"] < ATR_THRESHOLD) & (bt["turnover_rate"] >= MIN_TURNOVER) & \
       (bt["turnover_rate"] <= MAX_TURNOVER) & (bt["amt5"] > 0) & (bt["vol"] > 0) & \
       (~bt["is_st"]) & (bt["bar_idx"] >= MIN_BARS)
cand_df = bt[mask][["date","ts_code","amt5"]].sort_values(["date","amt5"], ascending=[True, False])
top = cand_df.groupby("date", group_keys=False).head(3)
candidates_by_date = {}
for d, grp in top.groupby("date"):
    candidates_by_date[d] = grp["ts_code"].tolist()
print("    候选预计算完成, 共 %d 个交易日有候选" % len(candidates_by_date))

print("[6] 回测主循环 ...")
cash = INIT_CAPITAL
positions = {}      # code -> {shares, buy_price, peak}
pending_buys = []   # [code, ...]
pending_sells = []  # [(code, reason), ...]
nav_series = []
trades = []
equity_peak = INIT_CAPITAL

for i, d in enumerate(dates):
    # 6.1 执行上一日订单(次日开盘成交)
    for code in list(pending_buys):
        if code in positions:
            pending_buys.remove(code); continue
        o = val(opx, code, d)
        if pd.isna(o) or is_suspended(susp, code, d):
            pending_buys.remove(code); continue   # 停牌/无数据放弃本次买入
        # 当前总资产与持仓市值
        mv = 0.0
        for c, p in positions.items():
            pc = val(px, c, d)
            if not pd.isna(pc): mv += p["shares"] * pc
        total_asset = cash + mv
        per_slot = min(total_asset*TARGET_RATIO, total_asset*MAX_TOTAL_RATIO - mv, cash)
        if per_slot <= 0:
            pending_buys.remove(code); continue
        shares = int(per_slot / (o*(1+COST)) / 100) * 100
        if shares < 100:
            pending_buys.remove(code); continue
        cost = shares * o * (1+COST)
        cash -= cost
        positions[code] = {"shares": shares, "buy_price": o, "peak": o}
        trades.append({"date": str(d), "code": code, "side": "BUY", "price": round(o,4),
                       "shares": shares, "cost": round(cost,2)})
        pending_buys.remove(code)

    for item in list(pending_sells):
        code, reason = item
        if code not in positions:
            pending_sells.remove(item); continue
        o = val(opx, code, d)
        if pd.isna(o) or is_suspended(susp, code, d):
            continue   # 停牌无法卖出, 顺延
        p = positions[code]
        proceeds = p["shares"] * o * (1-COST)
        cash += proceeds
        pnl = (o - p["buy_price"]) / p["buy_price"]
        trades.append({"date": str(d), "code": code, "side": "SELL", "price": round(o,4),
                       "shares": p["shares"], "pnl_pct": round(pnl*100,2), "reason": reason})
        del positions[code]
        pending_sells.remove(item)

    # 6.2 更新持仓峰值
    for code, p in positions.items():
        c = val(px, code, d)
        if not pd.isna(c) and c > p["peak"]:
            p["peak"] = c

    # 6.3 卖出判定(当日收盘)
    for code, p in list(positions.items()):
        c = val(px, code, d)
        if pd.isna(c):
            continue
        pnl = (c - p["buy_price"]) / p["buy_price"]
        if pnl <= STOP_LOSS:
            pending_sells.append((code, "止损%.1f%%" % (pnl*100))); continue
        if pnl >= TAKE_PROFIT:
            pending_sells.append((code, "止盈%.1f%%" % (pnl*100))); continue
        if p["peak"] > p["buy_price"]:
            dd = (c - p["peak"]) / p["peak"]
            if dd <= TRAILING_STOP:
                pending_sells.append((code, "移动止损回落%.1f%%" % (dd*100))); continue
        a = val(atrp, code, d)
        t = val(tov, code, d)
        cond_fail = (not pd.isna(a) and a >= ATR_THRESHOLD) or \
                    (not pd.isna(t) and (t < MIN_TURNOVER or t > MAX_TURNOVER))
        if cond_fail:
            if ABLATE_COND_FAILURE:
                pass  # 消融: 跳过条件失效退出
            else:
                pending_sells.append((code, "条件失效ATR=%.2f换手=%.2f" %
                                      (a if not pd.isna(a) else -1, t if not pd.isna(t) else -1)))

    # 6.4 选股填补空位
    slots = MAX_HOLD - len(positions)
    if slots > 0:
        for code in candidates_by_date.get(d, []):
            if len(pending_buys) >= slots:
                break
            if code not in positions and code not in pending_buys:
                pending_buys.append(code)

    # 6.5 记录净值
    mv = 0.0
    for c, p in positions.items():
        pc = val(px, c, d)
        if not pd.isna(pc): mv += p["shares"] * pc
    nav = cash + mv
    if nav > equity_peak:
        equity_peak = nav
    nav_series.append({"date": str(d), "nav": nav,
                       "drawdown": (nav/equity_peak - 1),
                       "positions": len(positions)})

    if (i+1) % 100 == 0:
        print("    进度 %d/%d  净值=%.2f  持仓=%d  待买=%d 待卖=%d" %
              (i+1, len(dates), nav, len(positions), len(pending_buys), len(pending_sells)))

print("    回测循环完成, 耗时 %.1fs" % (time.time()-t0))

# ---- 指标 ----
nav_df = pd.DataFrame(nav_series).set_index("date")
nav_df.index = pd.to_datetime(nav_df.index)
ret = nav_df["nav"].pct_change().dropna()
n_days = len(nav_df)
years = n_days / 252.0
total_ret = nav_df["nav"].iloc[-1] / INIT_CAPITAL - 1
ann_ret = (nav_df["nav"].iloc[-1] / INIT_CAPITAL) ** (1/years) - 1 if years > 0 else 0
sharpe = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0
max_dd = nav_df["drawdown"].min()
sell_trades = [t for t in trades if t["side"] == "SELL"]
win = sum(1 for t in sell_trades if t.get("pnl_pct",0) > 0)
wr = win / len(sell_trades) if sell_trades else 0

summary = {
    "strategy": "ATR_LOWVOL",
    "data_source": "E:/astock/daily/stock_daily.parquet",
    "period": [BACKTEST_START, str(bt["date"].max().date())],
    "params": {"atr_threshold": ATR_THRESHOLD, "turnover": [MIN_TURNOVER, MAX_TURNOVER],
               "stop_loss": STOP_LOSS, "take_profit": TAKE_PROFIT,
               "trailing_stop": TRAILING_STOP, "max_hold": MAX_HOLD,
               "target_ratio": TARGET_RATIO, "cost_oneway": COST},
    "init_capital": INIT_CAPITAL,
    "final_nav": round(float(nav_df["nav"].iloc[-1]), 2),
    "total_return_pct": round(total_ret*100, 2),
    "annual_return_pct": round(ann_ret*100, 2),
    "sharpe": round(float(sharpe), 3),
    "max_drawdown_pct": round(float(max_dd)*100, 2),
    "total_trading_days": n_days,
    "n_trades": len(trades),
    "n_sells": len(sell_trades),
    "win_rate_pct": round(wr*100, 2),
    "avg_position_hold": round(float(nav_df["positions"].mean()), 2),
}
print("\n===== ATR 低波动策略 回测结果 =====")
for k, v in summary.items():
    print("  %s: %s" % (k, v))

nav_df.to_csv(f"{OUT_DIR}/atr_lowvol_nav.csv")
pd.DataFrame(trades).to_csv(f"{OUT_DIR}/atr_lowvol_trades.csv", index=False)
with open(f"{OUT_DIR}/atr_lowvol_summary.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print("\n结果已保存至 %s/" % OUT_DIR)
