# -*- coding: utf-8 -*-
"""实盘卖出后前向跟踪（T-20260916-003 落地，2026-09-16）。

目标：对每笔实盘 SELL，自动回填卖出后 T+1/T+3/T+5 的开/收盘 vs 卖出价，进每日报告。
解决「卖出后卖飞/卖早无从感知」问题（601579 案例：09-14 卖出、09-15 涨停）。
数据源：
  - qmt_trade_log.csv（V1.3 miniQMT 67014907 成交）
  - 三桥 state/fills_<date>.json（G2/ENS 桥成交，70180771）
  - 主库 D:/astock/daily/stock_daily.parquet + data_live/incremental_daily.parquet（增量优先）算后续走势
产出：
  - data/real/sold_forward_live.csv（累计台账，幂等追加）
  - data/real/sold_forward_<date>.md（当日报告：近 N 笔卖出后的 T+1/3/5 表现）
用法：python track_sold_forward.py            # 默认回填所有 SELL 并出当日报告
"""
import collections
import datetime
import glob
import json
import os

import numpy as np
import pandas as pd

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
REAL = os.path.join(PROJ, "data", "real")
TRADE_LOG = os.path.join(PROJ, "data", "qmt_trade_log.csv")
MAIN = r"D:/astock/daily/stock_daily.parquet"
INCR = os.path.join(PROJ, "data_live", "incremental_daily.parquet")
BRIDGES = ["D:/QMT_POOL/g2_bridge", "D:/QMT_POOL/p16_ensemble_bridge", "D:/QMT_POOL/p16_v13_risk"]
OUT_CSV = os.path.join(REAL, "sold_forward_live.csv")


def load_sells():
    """收集实盘 SELL 全集：qmt_trade_log(V1.3) + 三桥 fills(G2/ENS)。返回 list[dict]。"""
    rows = []
    # V1.3 miniQMT
    if os.path.exists(TRADE_LOG):
        for r in pd.read_csv(TRADE_LOG, dtype=str).fillna("").to_dict("records"):
            if str(r.get("side", "")).upper() != "SELL":
                continue
            rows.append({
                "time": str(r.get("time", "")),
                "code": str(r.get("code", "")),
                "vol": int(float(r.get("vol", 0) or 0)),
                "price": float(r.get("price", 0) or 0),
                "acct": str(r.get("account_id", "")),
                "src": "qmt_trade_log",
            })
    # 三桥 fills
    for bdir in BRIDGES:
        sdir = os.path.join(bdir, "state")
        if not os.path.isdir(sdir):
            continue
        for fp in glob.glob(os.path.join(sdir, "fills_*.json")):
            try:
                with open(fp, encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                continue
            acct = str(d.get("account_id", "") or "")
            for fl in d.get("fills", []) or []:
                if str(fl.get("action", "")).upper() != "SELL":
                    continue
                rows.append({
                    "time": str(fl.get("ts", "") or ""),
                    "code": str(fl.get("code", "")),
                    "vol": int(float(fl.get("vol", 0) or 0)),
                    "price": float(fl.get("price", 0) or 0),
                    "acct": acct,
                    "src": "bridge_fills",
                })
    # 过滤无效（date 形如 YYYY-MM-DD）
    out = [r for r in rows if r["code"] and r["time"] and len(r["time"]) >= 10
           and r["time"][:10].replace("-", "").isdigit()]
    out.sort(key=lambda r: r["time"])
    return out


def load_daily():
    """主库 + 增量合并日线（增量优先），返回 {code: DataFrame(index=trade_date, open, close)}。"""
    d = pd.read_parquet(MAIN, columns=["open", "close"]).reset_index()
    d["trade_date"] = d["trade_date"].astype(str).str[:10]
    d["ts_code"] = d["ts_code"].astype(str)
    inc = None
    if os.path.exists(INCR):
        try:
            inc = pd.read_parquet(INCR, columns=["trade_date", "ts_code", "open", "close"])
            inc["trade_date"] = inc["trade_date"].astype(str).str[:10]
            inc["ts_code"] = inc["ts_code"].astype(str)
        except Exception:
            inc = None
    out = {}
    for code, g in d.groupby("ts_code"):
        g = g[["trade_date", "open", "close"]].sort_values("trade_date")
        out[code] = g
    if inc is not None:
        for code, g in inc.groupby("ts_code"):
            g = g[["trade_date", "open", "close"]].sort_values("trade_date")
            if code in out:
                merged = pd.concat([out[code], g]).drop_duplicates("trade_date", keep="last").sort_values("trade_date")
                out[code] = merged
            else:
                out[code] = g
    return out


def build_tracker(sells, daily):
    """为每笔 SELL 计算 T+1/T+3/T+5 开收盘。返回 DataFrame。"""
    recs = []
    for s in sells:
        code, sd = s["code"], s["time"][:10]
        g = daily.get(code)
        if g is None or g.empty:
            continue
        # 卖出日行（用于兜底卖出价=当日开盘）
        sd_row = g[g["trade_date"] == sd]
        exit_price = s["price"] if s["price"] and s["price"] > 0 else None
        if exit_price is None and not sd_row.empty:
            exit_price = float(sd_row.iloc[0]["open"])
        after = g[g["trade_date"] > sd]
        row = {
            "time": s["time"], "code": code, "vol": s["vol"],
            "price": s["price"], "acct": s["acct"], "src": s["src"],
            "exit": round(exit_price, 4) if exit_price else None,
        }
        for n in (1, 3, 5):
            if len(after) >= n:
                r = after.iloc[n - 1]
                row["T%d_open" % n] = round(float(r["open"]), 4)
                row["T%d_close" % n] = round(float(r["close"]), 4)
                if exit_price and exit_price > 0:
                    row["T%d_open_ret" % n] = round(float(r["open"]) / exit_price - 1.0, 4)
                    row["T%d_close_ret" % n] = round(float(r["close"]) / exit_price - 1.0, 4)
                else:
                    row["T%d_open_ret" % n] = None
                    row["T%d_close_ret" % n] = None
            else:
                row["T%d_open" % n] = None
                row["T%d_close" % n] = None
                row["T%d_open_ret" % n] = None
                row["T%d_close_ret" % n] = None
        recs.append(row)
    return pd.DataFrame(recs)


def main():
    sells = load_sells()
    daily = load_daily()
    df = build_tracker(sells, daily)
    print("实盘 SELL 记录:", len(sells), " 有效回填:", len(df))

    # 幂等合并累计台账（去重子集列统一 str，避免 int vs str 类型不一致导致去重失效）
    if os.path.exists(OUT_CSV):
        old = pd.read_csv(OUT_CSV, encoding="utf-8-sig", dtype=str)
        merged = pd.concat([old, df], ignore_index=True)
    else:
        merged = df
    for c in ("time", "code", "vol", "acct"):
        if c in merged.columns:
            merged[c] = merged[c].astype(str)
    merged = merged.drop_duplicates(subset=["time", "code", "vol", "acct"], keep="last")
    merged = merged.sort_values("time").reset_index(drop=True)
    merged.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print("累计台账:", OUT_CSV, "总行数:", len(merged))

    # 当日报告：近 30 天卖出且 T+3 已回填的
    today = datetime.date.today()
    cutoff = today - datetime.timedelta(days=40)
    recent = merged[pd.to_datetime(merged["time"]).dt.date >= cutoff]
    report = ["# 实盘卖出后前向跟踪报告 · %s" % today.isoformat(), ""]
    report.append("> 对每笔实盘 SELL 回填卖出后 T+1/T+3/T+5 开/收盘 vs 卖出价（open 基准）。")
    report.append("> 来源：qmt_trade_log(V1.3) + 三桥 fills(G2/ENS) | 主库+增量合并日线")
    report.append("")
    n_done = 0
    rows_md = []
    for _, r in recent.iterrows():
        t3 = r.get("T3_close_ret")
        if pd.isna(t3):
            continue
        n_done += 1
        rows_md.append("| %s | %s | %d | %.2f | %+.1f%% | %+.1f%% | %+.1f%% | %s |" % (
            r["time"][:10], r["code"], int(r["vol"]), r["exit"],
            (r.get("T1_close_ret") or 0) * 100, (r.get("T3_close_ret") or 0) * 100,
            (r.get("T5_close_ret") or 0) * 100, r["acct"]))
    if rows_md:
        report.append("近 40 天内卖出且已回填 T+3 的：%d 笔" % n_done)
        report.append("")
        report.append("| 卖出日 | 代码 | 股数 | 卖出价 | T+1收盘 | T+3收盘 | T+5收盘 | 账号 |")
        report.append("|---|---|---|---|---|---|---|---|")
        report += rows_md
        # 汇总统计
        vals = recent.dropna(subset=["T3_close_ret"])
        if len(vals):
            report.append("")
            report.append("## 汇总（已回填 T+3，n=%d）" % len(vals))
            for col, name in [("T1_close_ret", "T+1收盘"), ("T3_close_ret", "T+3收盘"), ("T5_close_ret", "T+5收盘")]:
                v = vals[col].dropna()
                if len(v):
                    report.append("- %s: 均值 %+.2f%% 中位 %+.2f%% 胜率 %.0f%%（n=%d）" % (
                        name, v.mean() * 100, v.median() * 100, (v > 0).mean() * 100, len(v)))
        # 卖飞提示：T+1 或 T+3 大涨（>=5%）的（T+1 捕捉隔日涨停类，如 601579 09-15 涨停）
        big = recent[(pd.to_numeric(recent["T1_close_ret"], errors="coerce") >= 0.05) |
                     (pd.to_numeric(recent["T3_close_ret"], errors="coerce") >= 0.05)]
        if len(big):
            report.append("")
            report.append("## ⚠ 卖飞警示（T+1/T+3 收盘涨>=5%）")
            for _, r in big.iterrows():
                t1 = r.get("T1_close_ret")
                t3 = r.get("T3_close_ret")
                tag = "T+1"
                v = t1
                if pd.notna(t3) and (pd.isna(t1) or t3 >= 0.05):
                    tag, v = "T+3", t3
                report.append("- %s %s 卖出 %.2f → %s %+.1f%%" % (r["time"][:10], r["code"], r["exit"], tag, v * 100))
    else:
        report.append("近 40 天内暂无可回填 T+3 的卖出记录。")
    out_md = os.path.join(REAL, "sold_forward_%s.md" % today.strftime("%Y-%m-%d"))
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("当日报告:", out_md)
    print("\n".join(report[:30]))


if __name__ == "__main__":
    main()
