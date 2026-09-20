# coding: utf-8
"""DE R8 共用回测引擎（只读实证评估，2026-09-18）。

三策略口径（与实盘信号链对齐的离线近似）：
  V14        : lgb_model_v3.txt(27特征) + deploy_predict 代理评分卡，红线 58，N=5
  G2         : g2 live 模型 20260915(43特征) + scorecard_real 真实评分卡，红线 60，N=15
  ENS        : payload 口径 = G2 信号，仅持有期不同，N=10
  ENS_FUSION : 实盘融合桥真实信号（v3_enh + G2 rank 融合 w=0.5），红线 60，N=10（补充对照）

信号：T 日收盘 → 模型 top100 预选池 → 评分卡红线 → 按评分卡总分排名取 top10 候选
成交：T+1 日 open（维度B另测分钟级时刻），一字涨停/停牌/无量不买（开盘口径过滤）
出场：live_trail 口径 STOP -7% → TP +15% → TRAIL(激活+8%，回撤8%，保本底线)，N 交易日到期
成本：佣金万2双边最低5元 + 印花税万5(卖出) + 过户费万0.1(沪市双边)，无滑点

硬约束遵守：只读数据/模型/源码；不 import 任何触发行情/交易连接的模块；全部内存模拟。
"""
import json
import math
import os
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import data_config as DC     # noqa: E402
import deploy_predict as DP  # noqa: E402
import scorecard_real as SR  # noqa: E402
import qmt_config as QC      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "out")
os.makedirs(OUT_DIR, exist_ok=True)

PANEL_V14 = os.path.join(PROJ, "data", "feature_panel_v3.parquet")
PANEL_G2 = os.path.join(PROJ, "data", "feature_panel_v3_enh2_n3_bt.parquet")
MODEL_V14 = r"D:\QuantLab\models\lgb_model_v3.txt"
META_V14 = os.path.join(PROJ, "data", "features_v3.json")
MODEL_G2 = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260915_1522t.txt"
META_G2 = os.path.join(PROJ, "data", "features_v3_g2_strong_real_20260915.json")
MODEL_V3ENH = r"D:\QuantLab\models\lgb_model_v3_enh.txt"
META_V3ENH = os.path.join(PROJ, "data", "features_v3_enh.json")

COMM_RATE, COMM_MIN = float(QC.COMM_RATE), float(QC.COMM_MIN)
STAMP_RATE, TRANS_RATE = float(QC.STAMP_RATE), float(QC.TRANS_RATE)
STOP, TP = -0.07, 0.15
TRAIL_ACT, TRAIL_PCT = 0.08, 0.08
POOL_N = 100
TOP_LIST = 10
CAPITAL0 = 100000.0
DELIST_DAYS, DELIST_LOSS = 60, 0.5

STRATS = {
    "V14": dict(pool_key="V14", panel=PANEL_V14, model=MODEL_V14, meta=META_V14,
                score="proxy", redline=58.0, hold=5,
                label="V1.4（v3正式模型27特征+代理评分卡，红线58，N=5）"),
    "G2": dict(pool_key="G2", panel=PANEL_G2, model=MODEL_G2, meta=META_G2,
               score="real", redline=60.0, hold=15,
               label="G2桥（g2 live模型20260915+真实评分卡，红线60，N=15）"),
    "ENS": dict(pool_key="G2", panel=PANEL_G2, model=MODEL_G2, meta=META_G2,
                score="real", redline=60.0, hold=10,
                label="融合桥-payload口径（G2信号，红线60，N=10）"),
    "ENS_FUSION": dict(pool_key="ENSF", panel=PANEL_G2, model=MODEL_G2, meta=META_G2,
                       score="real", redline=60.0, hold=10, ensemble=True,
                       label="融合桥-真实信号（v3_enh+G2 rank融合w=0.5，红线60，N=10）"),
}

# 主测试窗口（对齐历史审计口径）
MAIN_START, MAIN_END = "2024-07-01", "2026-08-14"


def load_universe():
    u = set()
    for p in (PANEL_V14, PANEL_G2):
        s = pd.read_parquet(p, columns=["ts_code"])["ts_code"].astype(str)
        u.update(s.unique().tolist())
    return u


def build_daily(universe, start, end):
    """日线上下文：{code: {date: (open, high, close, down_limit)}} + est/nxt 索引表。"""
    cols = ["open", "high", "close", "vol", "up_limit", "down_limit",
            "suspend_timing", "pe_ttm", "turnover_rate"]
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=cols).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily[daily["ts_code"].isin(universe)]
    lo = pd.Timestamp(start) - pd.Timedelta(days=20)
    hi = pd.Timestamp(end) + pd.Timedelta(days=40)
    daily = daily[(daily["trade_date"] >= lo) & (daily["trade_date"] <= hi)]
    daily = daily.sort_values(["ts_code", "trade_date"])
    g = daily.groupby("ts_code")
    daily["open_next"] = g["open"].shift(-1)
    daily["up_limit_next"] = g["up_limit"].shift(-1)
    daily["vol_next"] = g["vol"].shift(-1)
    daily["suspend_next"] = g["suspend_timing"].shift(-1)
    px = {}
    for code, sub in daily.groupby("ts_code", sort=False):
        dts = sub["trade_date"].values
        oo, hh = sub["open"].values, sub["high"].values
        cc, dd = sub["close"].values, sub["down_limit"].values
        m = {}
        for k in range(len(sub)):
            m[pd.Timestamp(dts[k])] = (oo[k], hh[k], cc[k], dd[k])
        px[str(code)] = m
    est = daily.set_index(["trade_date", "ts_code"])[["pe_ttm", "turnover_rate"]]
    nxt = daily.set_index(["trade_date", "ts_code"])[["open_next", "up_limit_next", "vol_next", "suspend_next"]]
    return {"px": px, "est": est, "nxt": nxt}


def build_pools(skey, start, end, ctx):
    """逐日信号：top100 预选池 → 评分卡红线 → 总分排名 top10。返回 (dates, per_day, pool统计)。"""
    strat = STRATS[skey]
    meta = json.load(open(strat["meta"], encoding="utf-8"))
    feats = meta["feature_cols"]
    ens = bool(strat.get("ensemble"))
    feats2 = []
    if ens:
        feats2 = json.load(open(META_V3ENH, encoding="utf-8"))["feature_cols"]
    need = list(dict.fromkeys(feats + feats2 + ["ts_code", "trade_date"]))
    if strat["score"] == "real":
        need = list(dict.fromkeys(need + ["main_net", "industry_pct", "volume_ratio"]))
    panel = pd.read_parquet(strat["panel"], columns=need)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    lo, hi = pd.Timestamp(start), pd.Timestamp(end)
    panel = panel[(panel["trade_date"] >= lo) & (panel["trade_date"] <= hi)]
    booster = lgb.Booster(model_file=strat["model"])
    booster2 = lgb.Booster(model_file=MODEL_V3ENH) if ens else None
    redline = strat["redline"]
    per_day, dates = {}, []
    n_pass, n_days = 0, 0
    grouped = panel.groupby("trade_date", sort=True)
    n_all = len(grouped)
    for gi, (d, day) in enumerate(grouped):
        if len(day) < 20:
            continue
        dates.append(d)
        day = day.copy()
        if ens:
            day["pg"] = booster.predict(day[feats].astype("float32").values)
            day["pv"] = booster2.predict(day[feats2].astype("float32").values)
            day["ens"] = (day["pg"].rank(pct=True) + day["pv"].rank(pct=True)) / 2.0
            pre = day[(day["pg"] >= redline / 100.0) | (day["pv"] >= redline / 100.0)]
            pool = pre.nlargest(POOL_N, "ens").copy()
        else:
            day["prob"] = booster.predict(day[feats].astype("float32").values)
            pool = day.nlargest(POOL_N, "prob").copy()
        if strat["score"] == "proxy":
            pool["total"] = DP.compute_scorecard(pool)["total"].values
        else:
            idx = pd.MultiIndex.from_arrays([pool["trade_date"], pool["ts_code"].astype(str)])
            est = ctx["est"].reindex(idx)
            pool["total"] = SR.compute_real_scorecard(pool, est)["total_new_real"].values
        passed = pool[pool["total"] >= redline]
        n_pass += len(passed)
        n_days += 1
        rank_col = "ens" if ens else "prob"
        top = passed.nlargest(TOP_LIST, rank_col) if len(passed) else passed
        out = pd.DataFrame(index=pd.Index([], name="ts_code"),
                           columns=["total", "rank_score", "open_next", "up_limit_next",
                                    "vol_next", "suspend_next"])
        if len(top):
            top = top.copy()
            codes = top["ts_code"].astype(str).values
            idx2 = pd.MultiIndex.from_arrays([top["trade_date"], codes])
            nx = ctx["nxt"].reindex(idx2)
            out = pd.DataFrame({
                "total": top["total"].values,
                "rank_score": top[rank_col].values,
                "open_next": nx["open_next"].values,
                "up_limit_next": nx["up_limit_next"].values,
                "vol_next": nx["vol_next"].values,
                "suspend_next": nx["suspend_next"].values,
            }, index=pd.Index(codes, name="ts_code"))
            out = out.sort_values("total", ascending=False)
        per_day[d] = out
        if gi % 100 == 0:
            print("    pools %d/%d %s ..." % (gi, n_all, d.date()))
    return dates, per_day, {"avg_passed": n_pass / max(n_days, 1), "n_days": n_days}


def _is_sh(code):
    return code.startswith("6")


def buy_fee(amt, code):
    comm = max(COMM_MIN, amt * COMM_RATE)
    return comm + (amt * TRANS_RATE if _is_sh(code) else 0.0)


def sell_fee(amt, code):
    return buy_fee(amt, code) + amt * STAMP_RATE


def _base_exec(r):
    """T+1 买入可执行性（开盘口径）：一字涨停/停牌/无量不买。
    suspend_timing 2026-08 起正常日为 ''（旧数据为 NaN），两者都视为未停牌。"""
    if pd.isna(r["vol_next"]) or r["vol_next"] <= 0:
        return False
    sv = r["suspend_next"]
    if pd.notna(sv) and str(sv).strip() != "":
        return False
    if pd.notna(r["up_limit_next"]) and r["up_limit_next"] > 0 \
            and pd.notna(r["open_next"]) and r["open_next"] >= r["up_limit_next"]:
        return False
    return True


def simulate(dates, per_day, K, N, ctx, buy_mode=("open", None),
             mature_sell="open", minute_book=None, missing_out=None, buy_pairs_out=None):
    """资金模拟。buy_mode: ("open",None)/("close",None)/("minute","HH:MM")。
    mature_sell: MATURE 卖出时刻 open/close/next_open。"""
    pxmap = ctx["px"]
    cash = CAPITAL0
    prev_total = CAPITAL0
    hold = {}
    trades = []
    daily_ret = []
    reasons = {}
    total_buy = 0.0
    n_skip_exec = n_minute_block = n_minute_fallback = n_under = 0
    M = len(dates)
    for i, d in enumerate(dates):
        # pending（next_open 到期卖）当日开盘执行
        for c in list(hold):
            h = hold[c]
            if h.get("pending"):
                v = pxmap.get(c, {}).get(d)
                if v is None:
                    continue
                amt = h["value"]
                cash += amt - sell_fee(amt, c)
                trades.append((h["buy_i"], i, amt / h["buy_val"] - 1))
                reasons["MATURE"] = reasons.get("MATURE", 0) + 1
                del hold[c]
        row = per_day.get(d)
        # 盯市（开盘价）
        for c in hold:
            h = hold[c]
            v = pxmap.get(c, {}).get(d)
            if v is not None and h["px_buy"] > 0:
                h["value"] = h["invest"] * v[0] / h["px_buy"]
        # 出场评估（开盘价，live_trail：STOP→TP→TRAIL，T+1 卫生）
        for c in list(hold):
            h = hold[c]
            if h.get("pending"):
                continue
            v = pxmap.get(c, {}).get(d)
            if v is None:
                h["missing"] = h.get("missing", 0) + 1
                if h["missing"] >= DELIST_DAYS:
                    amt = h["value"] * (1 - DELIST_LOSS)
                    cash += amt - sell_fee(amt, c)
                    trades.append((h["buy_i"], i, amt / h["buy_val"] - 1))
                    reasons["DELIST"] = reasons.get("DELIST", 0) + 1
                    del hold[c]
                continue
            h["missing"] = 0
            o_cur, hi, cl, dn = v
            if not (h["px_buy"] > 0):
                continue
            peak = h.get("peak")
            if not peak or peak <= 0:
                peak = h["px_buy"]
            if (i - h["buy_i"]) >= 2 and np.isfinite(hi) and hi > 0:
                h["peak"] = max(peak, hi)
            ret = o_cur / h["px_buy"] - 1
            if np.isfinite(dn) and o_cur <= dn + 1e-9:
                reasons["SKIP_DOWN"] = reasons.get("SKIP_DOWN", 0) + 1
                continue
            sell, reason = False, ""
            if (i - h["buy_i"]) >= N + 1:
                sell, reason = True, "MATURE"
            elif (i - h["buy_i"]) < 2:
                sell = False
            else:
                trail_act = peak >= h["px_buy"] * (1 + TRAIL_ACT)
                trail_hit = trail_act and o_cur <= max(h["px_buy"], peak * (1 - TRAIL_PCT))
                if ret <= STOP:
                    sell, reason = True, "STOP"
                elif ret >= TP:
                    sell, reason = True, "TP"
                elif trail_hit:
                    sell, reason = True, "TRAIL"
            if sell and reason == "MATURE" and mature_sell == "close":
                px_sell = cl if np.isfinite(cl) else o_cur
                amt = h["invest"] * px_sell / h["px_buy"]
                cash += amt - sell_fee(amt, c)
                trades.append((h["buy_i"], i, amt / h["buy_val"] - 1))
                reasons[reason] = reasons.get(reason, 0) + 1
                del hold[c]
                continue
            if sell and reason == "MATURE" and mature_sell == "next_open":
                h["pending"] = True
                continue
            if sell:
                amt = h["value"]
                cash += amt - sell_fee(amt, c)
                trades.append((h["buy_i"], i, amt / h["buy_val"] - 1))
                reasons[reason] = reasons.get(reason, 0) + 1
                del hold[c]
        # 买入：补足到 K（红线池按总分排名依次尝试）
        if row is not None and len(row) > 0 and i + 1 < M:
            buy_date = dates[i + 1]
            mode, tt = buy_mode
            for ri in range(len(row)):
                if len(hold) >= K:
                    break
                code = row.index[ri]
                if code in hold:
                    continue
                r = row.iloc[ri]
                if not _base_exec(r):
                    n_skip_exec += 1
                    continue
                px, ok = None, True
                v = pxmap.get(code, {}).get(buy_date)
                if mode == "open":
                    px = v[0] if v is not None else None
                    if px is None or not np.isfinite(px) or px <= 0:
                        ok = False
                elif mode == "close":
                    px = v[2] if v is not None else None
                    if px is None or not np.isfinite(px) or px <= 0:
                        ok = False
                else:
                    hit = minute_book.lookup(code, buy_date, tt) if minute_book else None
                    if hit is None:
                        if missing_out is not None:
                            missing_out.append((code, buy_date))
                        px = v[0] if v is not None else None
                        if px is None or not np.isfinite(px) or px <= 0:
                            ok = False
                        else:
                            n_minute_fallback += 1
                    else:
                        mpx, mvol = hit
                        if mvol is not None and mvol <= 1e-8:
                            n_minute_block += 1
                            ok = False
                        elif np.isfinite(r["up_limit_next"]) and mpx >= r["up_limit_next"]:
                            n_minute_block += 1
                            ok = False
                        else:
                            px = mpx
                if not ok:
                    continue
                n = K - len(hold)
                budget = cash * 0.95 / n
                fee = buy_fee(budget, code)
                invest = budget - fee
                if invest <= 0:
                    break
                cash -= budget
                total_buy += budget
                if buy_pairs_out is not None:
                    buy_pairs_out.append((code, buy_date))
                hold[code] = {"value": invest, "buy_val": invest, "buy_i": i,
                              "invest": invest, "px_buy": px}
        if len(hold) < K:
            n_under += 1
        tot = cash + sum(h["value"] for h in hold.values())
        daily_ret.append(tot / prev_total - 1 if prev_total > 0 else 0.0)
        prev_total = tot
    for c, h in hold.items():
        trades.append((h["buy_i"], M, h["value"] / h["buy_val"] - 1))
    info = {"reasons": reasons, "total_buy": total_buy, "n_skip_exec": n_skip_exec,
            "n_minute_block": n_minute_block, "n_minute_fallback": n_minute_fallback,
            "n_underweight_days": n_under}
    return trades, pd.Series(daily_ret), info


def stats(trades, daily_ret, info=None):
    rets = pd.Series([t[2] for t in trades])
    n = len(daily_ret)
    out = {"n_trades": len(trades), "n_days": n}
    if len(rets):
        win = float((rets > 0).mean())
        aw = float(rets[rets > 0].mean()) if (rets > 0).any() else np.nan
        al = float(rets[rets < 0].mean()) if (rets < 0).any() else np.nan
        out.update(win_rate=win,
                   profit_loss_ratio=(aw / abs(al) if np.isfinite(al) and al != 0 else np.nan))
    else:
        out.update(win_rate=np.nan, profit_loss_ratio=np.nan)
    if n:
        nav = (1 + daily_ret).cumprod()
        years = n / 252.0
        out["total_return"] = float(nav.iloc[-1] - 1)
        out["annual_return"] = float(nav.iloc[-1] ** (1.0 / years) - 1)
        out["max_drawdown"] = float((nav / nav.cummax() - 1).min())
        sd = float(daily_ret.std())
        out["sharpe"] = float(daily_ret.mean() / sd * math.sqrt(252)) if sd > 0 else np.nan
        mean_val = CAPITAL0 * float(nav.mean())
        if info is not None and mean_val > 0:
            out["turnover_ann"] = float(info["total_buy"] / mean_val / years)
        hd = [t[1] - t[0] for t in trades]
        out["avg_hold_days"] = float(np.mean(hd)) if hd else np.nan
    else:
        for k in ("total_return", "annual_return", "max_drawdown", "sharpe",
                  "turnover_ann", "avg_hold_days"):
            out[k] = np.nan
    if info is not None:
        out["reasons"] = info["reasons"]
        out["n_underweight_days"] = info["n_underweight_days"]
    return out


def fmt_row(r, keys=("annual_return", "max_drawdown", "sharpe", "win_rate",
                     "profit_loss_ratio", "turnover_ann")):
    def f(k, pct=False):
        v = r.get(k, np.nan)
        if v is None or (isinstance(v, float) and not np.isfinite(v)):
            return "-"
        return ("%+.1f%%" % (v * 100)) if pct else ("%.2f" % v)
    return "年化%s 回撤%s 夏普%s 胜率%s 盈亏比%s 换手%s倍" % (
        f("annual_return", True), f("max_drawdown", True), f("sharpe"),
        f("win_rate", True), f("profit_loss_ratio"), f("turnover_ann"))


# ---------------- 分钟线（维度B 买入时刻） ----------------
MIN_DIR = r"D:\astock\min_1min"
MIN_TIMES = ["09:35", "10:00", "10:05", "10:10", "10:30", "14:45"]


def _t2m(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


class MinuteBook:
    """(code, date) -> {time: (px, vol)} 惰性提取器；缺失文件/日期记入 missing。"""

    def __init__(self):
        self.cache = {}
        self.missing = set()
        self.n_files = 0

    def _load(self, code, dates):
        path = os.path.join(MIN_DIR, code + ".parquet")
        want = set(dates)
        if not os.path.exists(path):
            for d in want:
                self.missing.add((code, d))
            return
        df = pd.read_parquet(path, columns=["close", "vol"]).reset_index()
        self.n_files += 1
        td = df["trade_date"]
        if not np.issubdtype(td.dtype, np.datetime64):
            td = pd.to_datetime(td)
        tt = df["trade_time"]
        if np.issubdtype(tt.dtype, np.datetime64):
            hhmm = tt.dt.strftime("%H:%M").values
        else:
            hhmm = tt.astype(str).str.slice(-8, -3).values
        mask = td.isin(want)
        if not mask.any():
            for d in want:
                self.missing.add((code, d))
            return
        sub_d = td[mask].values
        sub_t = hhmm[mask]
        sub_c = df["close"].values[mask]
        sub_v = df["vol"].values[mask]
        buckets = {}
        for k in range(len(sub_d)):
            key = pd.Timestamp(sub_d[k])
            buckets.setdefault(key, []).append((sub_t[k], sub_c[k], sub_v[k]))
        for d, rows in buckets.items():
            tm = {t: (c, v) for t, c, v in rows}
            entry = {}
            tlist = sorted(tm.keys(), key=_t2m)
            mins = [_t2m(t) for t in tlist]
            for t in MIN_TIMES:
                if t in tm:
                    entry[t] = tm[t]
                    continue
                target = _t2m(t)
                best, bd = None, 99
                for j, mv in enumerate(mins):
                    dd = abs(mv - target)
                    if dd < bd:
                        best, bd = tlist[j], dd
                if best is not None and bd <= 3:
                    entry[t] = tm[best]
            self.cache[(code, d)] = entry
            want.discard(d)
        for d in want:
            self.missing.add((code, d))

    def ensure(self, pairs):
        byc = {}
        for c, d in pairs:
            if (c, d) in self.cache or (c, d) in self.missing:
                continue
            byc.setdefault(c, set()).add(d)
        for c, ds in byc.items():
            self._load(c, sorted(ds))

    def lookup(self, code, date, t):
        e = self.cache.get((code, date))
        if not e:
            return None
        return e.get(t)
