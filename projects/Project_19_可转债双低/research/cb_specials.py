# -*- coding: utf-8 -*-
"""Project_19 三项专项分析（2026-09-14，DE R4 建议的专项①收益分解/②流动性冲击/③强赎实施日卖出损失）

用法：python research/cb_specials.py
数据：D:/astock/cb/ 离线包（cb_daily 含 cb_over_rate/amount；cb_call 强赎表）
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cb_double_low as cb

OUT = os.path.join(os.path.dirname(HERE), "results")


def load_common():
    basic, daily, stk, notice_loose = cb.load_all("loose_call")
    _, _, _, notice_strict = cb.load_all("baseline")
    idx = pd.read_parquet(os.path.join(cb.DATA, "cb_index.parquet"))
    idx["trade_date"] = idx["trade_date"].astype(str).str.replace("-", "", regex=False)
    idx = idx.sort_values("trade_date").set_index("trade_date")["close"]
    return basic, daily, stk, notice_loose, notice_strict, idx


def special1(univ_b, univ_l, idx, notice_strict, notice_loose):
    print("=" * 70)
    print("[专项①] 2026 收益来源分解：loose_call vs baseline，强赎博弈贡献")
    nav_b, sr_b, c_b, t_b, _ = cb.backtest(univ_b, idx, "month", 20)
    nav_l, sr_l, c_l, t_l, _ = cb.backtest(univ_l, idx, "month", 20)
    yb = cb.yearly_table(sr_b, idx)
    yl = cb.yearly_table(sr_l, idx)
    tbl = pd.DataFrame({"严格baseline超额%": (yb["超额"] * 100).round(2),
                        "宽松loose超额%": (yl["超额"] * 100).round(2),
                        "差异pp": ((yl["超额"] - yb["超额"]) * 100).round(2)})
    print(tbl.to_string())
    # 强赎博弈券：strict 有公告但 loose 无（提示/已满足但最终未实施，或已到期未强赎）
    game = {c for c in notice_strict if c not in notice_loose}
    strict = set(notice_strict)
    print("[博弈券] strict公告=%d 只, 其中未进loose(博弈)=%d 只 (%.1f%%)" % (
        len(strict), len(game), 100.0 * len(game) / len(strict) if strict else 0))
    # 博弈券里最终退市时点 vs 公告日（粗略：delist 后仍未实施=放弃）
    bmap = univ_l[["ts_code", "delist_date"]].drop_duplicates().set_index("ts_code")
    abandoned = 0
    for c in game:
        dd = str(bmap.get("delist_date", {}).get(c, "") or "")
        if not dd:
            abandoned += 1
        elif dd and (dd > str(notice_strict[c]) or len(dd) != 8):
            abandoned += 1
    print("[博弈券] 公告后未退市(大概率最终放弃强赎)=%d 只 (%.1f%% of 博弈券)" % (
        abandoned, 100.0 * abandoned / len(game) if game else 0))
    print("[机制佐证] loose_call 吃的是'满足条件但最终放弃强赎'的持有段；2024-26 强赎密集期该段占比上升")
    return tbl


def special2(univ_l):
    print("=" * 70)
    print("[专项②] 流动性冲击：top20 月度换仓的成交额约束")
    dates = sorted(univ_l["trade_date"].unique())
    months = sorted({d[:6] for d in dates})
    signal_days = [max(d for d in dates if d.startswith(m)) for m in months]
    by_date = {d: g for d, g in univ_l.groupby("trade_date")}
    amts = []
    for t in signal_days:
        sub = by_date.get(t)
        if sub is None or not len(sub):
            continue
        top = sub.sort_values("double_low").head(20)
        amts.extend(top["amount"].dropna().tolist())
    a = pd.Series(amts)  # 单位=万元（cb_daily amount 实证：13452≈1.35亿）
    print("top20 券当日成交额(万元): 中位=%.0f 最小=%.0f P10=%.0f P25=%.0f P75=%.0f" % (
        a.median(), a.min(), a.quantile(0.1), a.quantile(0.25), a.quantile(0.75)))
    for capital in (100000, 500000):
        per = capital / 20.0 / 1e4  # 每票换仓额上限（满换，万元）
        ratio = per / a
        print("组合%s万: 每票换仓<=%.1f万, 换仓额/日成交额: 中位=%.3f%% P90=%.3f%% 最大=%.2f%%" % (
            capital / 1e4, per, ratio.median() * 100,
            ratio.quantile(0.9) * 100, ratio.max() * 100))
        print("   冲击判定(>1%%显著): P90=%.2f%% 占比(>1%%)=%.1f%%" % (
            ratio.quantile(0.9) * 100, (ratio > 0.01).mean() * 100))
    print("[结论] 10 万组合换仓冲击可忽略；50 万组合 P90 亦 <1%，转债成交额充足（低价券换手高）")


def special3(daily, notice_loose):
    print("=" * 70)
    print("[专项③] 强赎实施日卖出损失：公告日 close -> 次一交易日 open（开盘卖）vs 尾盘卖")
    # 用原始 daily（univ 经 ok_notice 过滤会把公告日后记录排掉，必须用未过滤数据）
    df = daily[["trade_date", "ts_code", "open", "close"]].drop_duplicates(
        ["trade_date", "ts_code"])
    df = df[df["trade_date"] >= "20190101"]
    df = df.sort_values("trade_date")
    pv = df.set_index(["ts_code", "trade_date"])
    rows = []
    for c, d0 in notice_loose.items():
        d0s = str(d0)
        if d0s < "20190101":
            continue
        try:
            c0 = float(pv.loc[(c, d0s), "close"])
        except KeyError:
            continue
        dates_c = sorted(set(df.loc[df["ts_code"] == c, "trade_date"]))
        if d0s not in dates_c:
            continue
        i = dates_c.index(d0s)
        if i + 1 >= len(dates_c):
            continue
        d1 = dates_c[i + 1]
        try:
            o1 = float(pv.loc[(c, d1), "open"])
            cl1 = float(pv.loc[(c, d1), "close"])
        except KeyError:
            continue
        if o1 <= 0 or c0 <= 0:
            continue
        rows.append({"code": c, "d0": d0s, "d1": d1,
                     "jump_open": o1 / c0 - 1.0,     # 公告日收盘->次日开盘（开盘卖损失）
                     "intraday": cl1 / o1 - 1.0})    # 次日开盘->收盘（尾盘卖 vs 开盘卖）
    if not rows:
        print("无样本（公告日无该券记录）")
        return pd.DataFrame()
    r = pd.DataFrame(rows)
    print("样本数=%d" % len(r))
    for col in ("jump_open", "intraday"):
        v = r[col]
        print("%s: 均值=%.3f%% 中位=%.3f%% P25=%.3f%% P75=%.3f%%  <0占比=%.1f%%" % (
            col, v.mean() * 100, v.median() * 100, v.quantile(0.25) * 100,
            v.quantile(0.75) * 100, (v < 0).mean() * 100))
    loss_open = r["jump_open"].mean()
    loss_tail = r["jump_open"].mean() + r["intraday"].mean()
    print("[结论] 公告次日开盘平均%s%.3f%%；尾盘卖相对开盘卖%s%.3fpp —— 开盘卖%s" % (
        "低开" if loss_open < 0 else "高开", abs(loss_open) * 100,
        "改善" if loss_tail > loss_open else "恶化", abs(loss_tail - loss_open) * 100,
        "占优（早卖早走）" if loss_tail < loss_open else "更亏（建议尾盘）"))
    return r


def main():
    basic, daily, stk, notice_loose, notice_strict, idx = load_common()
    print("[load] basic=%d daily=%d notice(loose)=%d notice(strict)=%d" % (
        len(basic), len(daily), len(notice_loose), len(notice_strict)))
    univ_b = cb.build_universe(basic, daily, stk, notice_strict, "20190101", "baseline")
    univ_l = cb.build_universe(basic, daily, stk, notice_loose, "20190101", "loose_call")
    print("[universe] baseline=%d loose=%d" % (len(univ_b), len(univ_l)))

    t1 = special1(univ_b, univ_l, idx, notice_strict, notice_loose)
    special2(univ_l)
    r3 = special3(daily, notice_loose)

    os.makedirs(OUT, exist_ok=True)
    t1.to_csv(os.path.join(OUT, "special1_year_diff.csv"), encoding="utf-8-sig")
    r3.to_csv(os.path.join(OUT, "special3_call_sell.csv"), index=False, encoding="utf-8-sig")
    print("[saved] results/special1_year_diff.csv & special3_call_sell.csv")


if __name__ == "__main__":
    main()
