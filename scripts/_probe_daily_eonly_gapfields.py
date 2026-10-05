# coding: utf-8
"""下钻 recent_baseline 暴露的唯一真风险：E-only 新行的 is_st / listed_days 22% 缺失。

背景：三个历史异常日（20260820/0825/0827 换手率倍率、20260828 circ_mv 27 行）
全部落在 E∩D 交集区，若采用「仅追加 E-only」策略，一个都不会写入生产库。
真正会改动 D:\astock 的只有 E-only 的 50,052 行。

B 部分已确认：
  - E is_st 空值率：全量 0.11% / 历史段 0.04% / E-only 段 22.21% → E-only 特有
  - 缺 is_st 的 11115 行横跨 5565 只股票，无一只"9 天全缺"
    → 缺失按【交易日】聚集，不是按股票
  - D 侧 20260911 有 200 只 is_st=1（3.6%），该字段不可为空
  - 5565 只缺失股票中 5562 只在 D 有代码 → 理论上可用 D 末值回填

本脚本补齐：
  C. 缺失的交易日聚集度（哪几天整批缺）→ 决定能否整段跳过
  D. D 末值回填的可靠性（D 侧 is_st 末值 vs 该股最近一次非空值是否稳定）
  E. 20260828 那 27 行的 D/E 逐字段对比（闭合发现 2）

只读 D:/astock 与 E 盘，不写任何生产文件。
"""
import gc

import numpy as np
import pandas as pd

D_PATH = r"D:/astock/daily/stock_daily.parquet"
E_PATH = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

EONLY_DATES = ["20260914", "20260915", "20260916", "20260917", "20260918",
               "20260921", "20260922", "20260923", "20260924"]


def td8(s):
    return s.astype(str).str.slice(0, 10).str.replace("-", "", regex=False)


def nanrate(s):
    return 100.0 * float(pd.isna(s).mean())


def part_c_date_cluster():
    print("=" * 100)
    print("C  E-only 缺 is_st 的【交易日】聚集度（决定能否整段跳过）")
    print("=" * 100)
    e = pd.read_parquet(
        E_PATH, columns=["ts_code", "trade_date", "is_st", "listed_days"])
    etd = td8(pd.Series(e["trade_date"])).to_numpy()
    sub = e[np.isin(etd, EONLY_DATES)].copy()
    sub["_td"] = etd[np.isin(etd, EONLY_DATES)]
    sub["_miss"] = pd.isna(sub["is_st"])

    print("  %-12s %8s %10s %12s %12s" % ("日期", "行数", "缺is_st", "缺is_st率", "缺listed_days率"))
    g = sub.groupby("_td")
    for dt, s in g:
        print("  %-12s %8d %10d %11.2f%% %11.2f%%"
              % (dt, len(s), int(s["_miss"].sum()),
                 100.0 * s["_miss"].mean(), nanrate(s["listed_days"])))
    print("\n  判读：某日缺 is_st 率≈100%% → 该日 is_st 整批未落地，")
    print("        整段跳过该日比逐行填补更干净；若各日均≈22%% → 日内部分股票缺。")

    # 同一只股票在 9 天里的缺失天数分布 → 判断是"某几天整批缺"还是"零散缺"
    per = sub.groupby(sub["ts_code"].astype(str))["_miss"].sum()
    print("\n  每只股票的缺失天数分布（0=不缺，9=全缺）：")
    vc = per.value_counts().sort_index()
    for k, v in vc.items():
        print("    缺 %d 天：%5d 只" % (int(k), int(v)))

    # 每日"缺 is_st"的股票集合是否一致 → 若一致，说明是同一批票全程没值
    miss_sets = {}
    for dt, s in sub[sub["_miss"]].groupby("_td"):
        miss_sets[dt] = set(s["ts_code"].astype(str))
    print("\n  各日缺失股票集合的两两重合率：")
    dts = sorted(miss_sets.keys())
    for i in range(len(dts)):
        for j in range(i + 1, len(dts)):
            a, b = miss_sets[dts[i]], miss_sets[dts[j]]
            jac = len(a & b) / max(1, len(a | b))
            print("    %s vs %s : Jaccard=%.3f (|A|=%d |B|=%d |A∩B|=%d)"
                  % (dts[i], dts[j], jac, len(a), len(b), len(a & b)))

    codes = pd.Index(sub.loc[sub["_miss"], "ts_code"].astype(str).unique())
    del e, etd, sub
    gc.collect()
    return codes


def part_d_backfill(codes):
    print("\n" + "=" * 100)
    print("D  用 D 末值回填 is_st 的可靠性评估")
    print("=" * 100)
    cols = ["is_st", "listed_days", "close", "float_share", "circ_mv",
            "total_share", "total_mv"]
    d = pd.read_parquet(D_PATH, columns=cols)
    dtd = td8(pd.Series(d.index.get_level_values("trade_date"))).to_numpy()
    dcode = pd.Series(d.index.get_level_values("ts_code").astype(str)).to_numpy()

    arr = np.asarray(list(codes), dtype=object)
    in_codes = np.isin(dcode, arr)
    last_td = dtd[in_codes].max()
    print("  目标股票在 D 的最后交易日 = %s" % last_td)

    sel = in_codes & (dtd == last_td)
    s = d[sel]
    print("  该日命中行数 = %d ；D 中完全不存在的代码数 = %d"
          % (len(s), len(arr) - int(np.unique(dcode[in_codes]).size)))
    print("  该日 is_st 分布：")
    print("    " + s["is_st"].value_counts(dropna=False).to_string().replace("\n", "\n    "))
    print("  该日 listed_days 空值率 = %.2f%%" % nanrate(s["listed_days"]))

    # 回填可靠性：该股在 D 最近 20 个交易日的 is_st 是否稳定
    print("\n  回填可靠性：目标股票在 D 最近 20 个交易日的 is_st 变动情况")
    win = in_codes & (dtd >= "20260814")
    w = d[win]
    g = w.groupby(w.index.get_level_values("ts_code").astype(str))["is_st"]
    nuniq = g.nunique(dropna=True)
    print("    窗口内有 is_st 变动的股票数 = %d / %d"
          % (int((nuniq > 1).sum()), int(len(nuniq))))
    if (nuniq > 1).any():
        chg = nuniq[nuniq > 1].index[:20].tolist()
        print("    变动样本（最多 20 只）：%s" % ", ".join(chg))
    print("  判读：变动数≈0 → 用末值回填 9 天内风险极低；")
    print("        变动数显著>0 → 回填会引入未来信息，禁止。")
    return last_td


def part_e_20260828_de():
    print("\n" + "=" * 100)
    print("E  20260828 那 27 行：D / E 逐字段对比（闭合发现 2）")
    print("=" * 100)
    cols = ["ts_code", "trade_date", "close", "float_share", "circ_mv",
            "total_share", "total_mv"]
    e = pd.read_parquet(E_PATH, columns=cols)
    etd = td8(pd.Series(e["trade_date"])).to_numpy()
    e28 = e[etd == "20260828"].copy()
    e28["ts_code"] = e28["ts_code"].astype(str)

    d = pd.read_parquet(
        D_PATH, columns=["close", "float_share", "circ_mv", "total_share", "total_mv"])
    dtd = td8(pd.Series(d.index.get_level_values("trade_date"))).to_numpy()
    dcode = pd.Series(d.index.get_level_values("ts_code").astype(str)).to_numpy()
    sel = dtd == "20260828"
    d28 = d[sel].copy()
    d28 = d28.reset_index(drop=True)
    d28["ts_code"] = dcode[sel]
    del e, etd, d, dtd, dcode
    gc.collect()

    def num(df, c):
        return pd.to_numeric(df[c], errors="coerce")

    calc = num(e28, "close") * num(e28, "float_share")
    bad = (num(e28, "float_share") > 0) & np.isfinite(num(e28, "circ_mv")) & \
          ~np.isclose(calc, num(e28, "circ_mv"), rtol=2e-3, atol=1.0)
    eb = e28[bad]
    print("  E 不自洽 %d 只；其中 D 同日有记录的 %d 只"
          % (len(eb), int(d28["ts_code"].isin(set(eb["ts_code"])).sum())))

    j = d28[d28["ts_code"].isin(set(eb["ts_code"]))][
        ["ts_code", "close", "float_share", "circ_mv", "total_share", "total_mv"]
    ].merge(
        eb[["ts_code", "close", "float_share", "circ_mv", "total_share", "total_mv"]],
        on="ts_code", suffixes=("_D", "_E"))
    if not len(j):
        print("  D 侧无对应行 → 无法对比")
        return

    j["calc_D"] = num(j, "close_D") * num(j, "float_share_D")
    j["calc_E"] = num(j, "close_E") * num(j, "float_share_E")
    same_close = np.isclose(num(j, "close_E"), num(j, "close_D"), rtol=1e-6, atol=1e-4)
    same_fsh = np.isclose(num(j, "float_share_E"), num(j, "float_share_D"),
                          rtol=1e-6, atol=1e-3)
    same_tsh = np.isclose(num(j, "total_share_E"), num(j, "total_share_D"),
                          rtol=1e-6, atol=1e-3)
    same_cmv = np.isclose(num(j, "circ_mv_E"), num(j, "circ_mv_D"), rtol=2e-3, atol=1.0)
    print("  字段一致性：close %d/%d  float_share %d/%d  total_share %d/%d  circ_mv %d/%d"
          % (int(same_close.sum()), len(j), int(same_fsh.sum()), len(j),
             int(same_tsh.sum()), len(j), int(same_cmv.sum()), len(j)))
    j["fsh_rel"] = num(j, "float_share_E") / num(j, "float_share_D") - 1.0
    j["tsh_rel"] = num(j, "total_share_E") / num(j, "total_share_D") - 1.0

    print("\n  %-10s %9s %9s %13s %13s %9s %9s"
          % ("ts_code", "close_D", "close_E", "fsh_D", "fsh_E", "fsh_rel", "tsh_rel"))
    for _, r in j.sort_values("fsh_rel", key=lambda s: s.abs(),
                              ascending=False).iterrows():
        print("  %-10s %9.2f %9.2f %13.1f %13.1f %8.2f%% %8.2f%%"
              % (r["ts_code"], r["close_D"], r["close_E"],
                 r["float_share_D"], r["float_share_E"],
                 100 * r["fsh_rel"], 100 * r["tsh_rel"]))
    print("\n  判读：close 相同而 float_share 不同 → E 的股本版本错；")
    print("        float_share 相同而 circ_mv 不同 → E 的市值字段错；")
    print("        两者都不同 → E 该日整行是另一版本，绝不能覆盖 D。")


def main():
    codes = part_c_date_cluster()
    part_d_backfill(codes)
    part_e_20260828_de()
    print("\nGAP2_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())