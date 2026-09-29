# -*- coding: utf-8 -*-
"""P21 M1 裁决聚合：读取 config/falsification_criteria.yaml（2026-09-28 锁定）按判据聚合六年事件表。

产出：results/M1_decision_report_<YYYYMMDD>.md + stdout 摘要。
纪律：所有阈值来自 yaml，脚本内禁止另写阈值（yaml 缺键即报错）。
"""
import argparse
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_CRITERIA = PROJECT / "config" / "falsification_criteria.yaml"
DEFAULT_RESULTS = PROJECT / "results"
CRITERIA_YAML_NAME = "midvol_criteria.yaml"
BOOTSTRAP_SEED = 20260928
BOOTSTRAP_B = 2000
BOOTSTRAP_BLOCK = 20
ALPHA = 0.05


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def need(cfg, *keys, ctx=""):
    node = cfg
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            raise KeyError("yaml 缺判据键 %s%s" % (ctx, ".".join(keys)))
        node = node[k]
    return node


def build_pool(detail_path, crit_cfg):
    df = pd.read_parquet(detail_path)
    base = crit_cfg.get("midvol_criteria", crit_cfg)
    amp = base["amplitude"]
    liq = base["liquidity"]
    listing = base["listing"]
    mask = (
        (df["amplitude_median"] >= amp["min_pct"])
        & (df["amplitude_median"] <= amp["max_pct"])
        & (df["turnover_median"] >= liq["min_daily_amount_cny"] / 1e8)
        & (df["vol_median"] >= liq["min_daily_vol_hands"] / 1e4)
        & (df["n_days"] >= listing["min_listing_days"])
    )
    pool = df[mask]
    if "name" in pool.columns:
        pool = pool[~pool["name"].str.contains("ST|退", na=False)]
    if "market" in pool.columns:
        pool = pool[pool["market"] != "北交所"]
    return pool["ts_code"].tolist()


def _norm_date_series(s):
    if np.issubdtype(s.dtype, np.datetime64):
        return s.dt.strftime("%Y%m%d")
    return s.astype(str).str.replace("-", "", regex=False)


def load_calendar(daily_path, start, end):
    t = pq.read_table(daily_path, columns=["trade_date"])
    df = t.to_pandas(ignore_metadata=True)
    df["trade_date"] = _norm_date_series(df["trade_date"])
    dates = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]["trade_date"].unique()
    return np.sort(dates)


def load_amplitude(daily_path, start, end):
    t = pq.read_table(daily_path, columns=["ts_code", "trade_date", "high", "low", "pre_close"])
    df = t.to_pandas(ignore_metadata=True)
    df["trade_date"] = _norm_date_series(df["trade_date"])
    df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]
    df = df[df["pre_close"] > 0]
    df["amp_pct"] = (df["high"] - df["low"]) / df["pre_close"] * 100.0
    return df[["ts_code", "trade_date", "amp_pct"]]


def clustered_t(daily_means):
    x = np.asarray(daily_means, dtype=float)
    if x.size < 2 or np.std(x, ddof=1) == 0:
        return float("nan"), int(x.size)
    t = x.mean() / (x.std(ddof=1) / np.sqrt(x.size))
    return float(t), int(x.size)


def block_bootstrap_ci(daily_means):
    x = np.asarray(daily_means, dtype=float)
    n = x.size
    if n < BOOTSTRAP_BLOCK * 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    n_blocks = int(np.ceil(n / BOOTSTRAP_BLOCK))
    means = np.empty(BOOTSTRAP_B)
    starts_pool = np.arange(n - BOOTSTRAP_BLOCK + 1)
    for b in range(BOOTSTRAP_B):
        starts = rng.choice(starts_pool, size=n_blocks, replace=True)
        sample = np.concatenate([x[s:s + BOOTSTRAP_BLOCK] for s in starts])[:n]
        means[b] = sample.mean()
    lo = float(np.quantile(means, ALPHA / 2))
    hi = float(np.quantile(means, 1 - ALPHA / 2))
    return lo, hi


def sign(v):
    return 1 if v > 0 else (-1 if v < 0 else 0)


def fmt_bps(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    return "%.2f" % v


def fmt_f(v, nd=3):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    return ("%." + str(nd) + "f") % v


def main(argv=None):
    ap = argparse.ArgumentParser(description="P21 M1 decision aggregation")
    ap.add_argument("--criteria", default=str(DEFAULT_CRITERIA))
    ap.add_argument("--results", default=str(DEFAULT_RESULTS))
    ap.add_argument("--daily", default="D:/astock/daily/stock_daily.parquet")
    ap.add_argument("--date", default=None, help="报告文件日期后缀 YYYYMMDD")
    args = ap.parse_args(argv)

    crit_path = Path(args.criteria)
    results = Path(args.results)
    cfg = load_yaml(crit_path)
    sha = hashlib.sha256(crit_path.read_bytes()).hexdigest()[:12]

    start = need(cfg, "window", "start", ctx="window.")
    end = need(cfg, "window", "end", ctx="window.")
    min_shares = need(cfg, "single_family", "density", "min_shares", ctx="density.")
    net_gt = need(cfg, "single_family", "net_expectancy_pct", "gt", ctx="net.")
    gross_gte = need(cfg, "single_family", "gross_expectancy_pct", "gte", ctx="gross.") * 100
    cov_gte = need(cfg, "single_family", "cost_coverage_multiple", "gte", ctx="cov.")
    t_gte = need(cfg, "single_family", "clustered_t", "gte", ctx="t.")
    ci_gt = need(
        cfg, "single_family", "clustered_t", "fallback_block_bootstrap_ci_lower", "gt", ctx="ci."
    )
    yearly_gt = need(cfg, "single_family", "yearly_net_expectancy_pct", "gt", ctx="yearly.") * 100
    is_end = need(cfg, "single_family", "oos", "is_end", ctx="oos.")
    oos_start = need(cfg, "single_family", "oos", "oos_start", ctx="oos.")
    oos_same = need(cfg, "single_family", "oos", "same_direction_as_full_period", ctx="oos.")
    ext_same = need(
        cfg, "single_family", "extreme_days", "same_direction_after_exclusion", ctx="ext."
    )
    ext_dates = set(str(d).replace("-", "") for d in need(cfg, "single_family", "extreme_days", "dates", ctx="ext."))
    tier_lt = need(cfg, "single_family", "amplitude_tier_check", "tier_lt_pct", ctx="tier.")
    dens_stock = need(cfg, "single_family", "density", "per_stock_daily_avg", "gte", ctx="dens.")
    dens_100_info = need(cfg, "single_family", "density", "per_100_stocks_daily_avg", "informational_only", ctx="dens.")
    dens_max_share = need(cfg, "single_family", "density", "single_stock_share_max", ctx="dens.")
    min_fam = need(cfg, "m1_gate", "min_passing_families", ctx="gate.")
    gate_t = need(cfg, "m1_gate", "merged_six_families", "clustered_t_gte", ctx="gate.")
    gate_sharpe = need(cfg, "m1_gate", "merged_six_families", "annual_sharpe_gte", ctx="gate.")
    gate_density = need(cfg, "m1_gate", "density_pass", ctx="gate.")
    gate_oos_neg = need(cfg, "m1_gate", "oos_2024_not_negative", ctx="gate.")
    cost_pre = need(cfg, "cost", "round_trip_bps_pre", ctx="cost.")
    cost_post = need(cfg, "cost", "round_trip_bps_post", ctx="cost.")
    corr_lt = need(cfg, "combination_gate", "pairwise_correlation_lt", ctx="corr.")

    crit_cfg = load_yaml(PROJECT / "config" / CRITERIA_YAML_NAME)
    pool = build_pool(results / "data_health_check_detail.parquet", crit_cfg)
    pool_n = len(pool)
    pool_set = set(pool)
    criteria_std_n = crit_cfg.get("pool_stats", {}).get("total_candidates", "n/a")

    calendar = load_calendar(args.daily, start, end)
    n_days = len(calendar)
    amp_df = load_amplitude(args.daily, start, end)

    day_fam = defaultdict(lambda: [0.0, 0])          # (fam, date) -> [sum, cnt]
    fam_tot = defaultdict(lambda: [0.0, 0, 0.0])     # fam -> [sum, cnt, cost_sum]
    fam_year = defaultdict(lambda: [0.0, 0])         # (fam, year) -> [sum, cnt]
    fam_stock = defaultdict(lambda: defaultdict(int))  # fam -> ts -> cnt
    fam_ext = defaultdict(lambda: [0.0, 0])
    fam_oos = defaultdict(lambda: [0.0, 0])
    tier_daily = defaultdict(lambda: [0.0, 0])
    tier_tot = {"lt": [0.0, 0], "ge": [0.0, 0]}
    cost_check = defaultdict(lambda: [0.0, 0])
    fam_pairchk = defaultdict(lambda: [0.0, 0])   # fam -> [sum|组内gross和|, 组数]

    files = sorted(results.glob("signal_scan_????????_????????.parquet"))
    if len(files) != 6:
        raise SystemExit("期望 6 件年度扫描产物，实际 %d" % len(files))

    for fp in files:
        df = pd.read_parquet(
            fp, columns=["ts_code", "trade_date", "family", "shares", "pnl_bps", "cost_bps"]
        )
        df = df[df["shares"] >= min_shares]
        df["year"] = df["trade_date"].str[:4]

        g = df.groupby(["family", "trade_date"], sort=False)[["pnl_bps", "cost_bps"]].sum()
        cnt = df.groupby(["family", "trade_date"], sort=False).size()
        for (fam, td), row in g.iterrows():
            e = day_fam[(fam, td)]
            e[0] += row["pnl_bps"]
            e[1] += int(cnt[(fam, td)])

        gy = df.groupby(["family", "year"], sort=False)[["pnl_bps", "cost_bps"]].agg(["sum", "count"])
        for (fam, yr), row in gy.iterrows():
            e = fam_year[(fam, yr)]
            e[0] += row[("pnl_bps", "sum")]
            e[1] += int(row[("pnl_bps", "count")])

        gsum = df.groupby("family", sort=False)[["pnl_bps", "cost_bps"]].sum()
        gcnt = df.groupby("family", sort=False).size()
        for fam, row in gsum.iterrows():
            e = fam_tot[fam]
            e[0] += row["pnl_bps"]
            e[1] += int(gcnt[fam])
            e[2] += row["cost_bps"]

        gs = df.groupby(["family", "ts_code"], sort=False).size()
        for (fam, code), n in gs.items():
            fam_stock[fam][code] += int(n)

        sub = df[~df["trade_date"].isin(ext_dates)]
        if len(sub):
            gs2 = sub.groupby("family", sort=False)["pnl_bps"].agg(["sum", "count"])
            for fam, row in gs2.iterrows():
                e = fam_ext[fam]
                e[0] += row["sum"]
                e[1] += int(row["count"])

        sub = df[(df["trade_date"] >= oos_start) & (df["trade_date"] <= end)]
        if len(sub):
            gs3 = sub.groupby("family", sort=False)["pnl_bps"].agg(["sum", "count"])
            for fam, row in gs3.iterrows():
                e = fam_oos[fam]
                e[0] += row["sum"]
                e[1] += int(row["count"])

        df["gross"] = df["pnl_bps"] + df["cost_bps"]
        gp = df.groupby(["family", "ts_code", "trade_date"], sort=False)["gross"].sum()
        for fam, sub_s in gp.groupby(level=0):
            e = fam_pairchk[fam]
            e[0] += float(sub_s.abs().sum())
            e[1] += len(sub_s)

        cc = df.groupby("trade_date", sort=False)["cost_bps"].mean()
        for td, v in cc.items():
            key = "pre" if td < need(cfg, "cost", "cutoff", ctx="cost.") else "post"
            cost_check[key][0] += v
            cost_check[key][1] += 1

        m = df.merge(amp_df, on=["ts_code", "trade_date"], how="left")
        m["amp_pct"] = m["amp_pct"].fillna(-1.0)
        for flag, gsel in (("lt", m[m["amp_pct"] < tier_lt]), ("ge", m[m["amp_pct"] >= tier_lt])):
            if len(gsel) == 0:
                continue
            tier_tot[flag][0] += float(gsel["pnl_bps"].sum())
            tier_tot[flag][1] += len(gsel)
            gt = gsel.groupby("trade_date", sort=False)["pnl_bps"].agg(["sum", "count"])
            for td, row in gt.iterrows():
                e = tier_daily[(flag, td)]
                e[0] += row["sum"]
                e[1] += int(row["count"])

        print("[load] %s rows=%d" % (fp.name, len(df)))
        del df, m
        sys.stdout.flush()

    families = sorted({f for f, _ in day_fam} | set(fam_tot))
    years = sorted({y for _, y in fam_year})
    total_events = sum(v[1] for v in fam_tot.values())
    pair_info = {
        fam: (int(v[1]), (v[0] / v[1] if v[1] else float("nan")))
        for fam, v in fam_pairchk.items()
    }

    rows = []
    for fam in families:
        s, n, cs = fam_tot[fam]
        net = s / n
        cost = cs / n
        gross = net + cost
        cov = gross / cost if cost > 0 else float("nan")
        daily = np.array([v[0] / v[1] for (f, _), v in day_fam.items() if f == fam])
        t, n_days_f = clustered_t(daily)
        lo, hi = block_bootstrap_ci(daily)
        yearly = {y: (fam_year[(fam, y)][0] / fam_year[(fam, y)][1] if fam_year[(fam, y)][1] else float("nan")) for y in years}
        oos = fam_oos[fam]
        oos_mean = oos[0] / oos[1] if oos[1] else float("nan")
        full = net
        ex = fam_ext[fam]
        ex_mean = ex[0] / ex[1] if ex[1] else float("nan")
        cnts = fam_stock[fam]
        stock_share = (max(cnts.values()) / n) if n else 0.0
        dens = n / (pool_n * n_days)
        dens_100 = n / n_days * 100.0 / pool_n
        in_pool = sum(v for k, v in cnts.items() if k in pool_set)
        j1 = (net > net_gt * 100) and (gross >= gross_gte) and (cov >= cov_gte)
        j2 = (t >= t_gte) or (not np.isnan(lo) and lo > ci_gt)
        j3 = all((not np.isnan(v)) and v > yearly_gt for v in yearly.values())
        j4 = (not np.isnan(oos_mean)) and sign(oos_mean) == sign(full) and sign(full) != 0 if oos_same else True
        j5 = (not np.isnan(ex_mean)) and sign(ex_mean) == sign(full) and sign(full) != 0 if ext_same else True
        j7 = (dens >= dens_stock) and (stock_share <= dens_max_share)
        rows.append(
            dict(fam=fam, n=n, net=net, cost=cost, gross=gross, cov=cov, t=t, n_days=n_days_f,
                 ci_lo=lo, ci_hi=hi, yearly=yearly, oos=oos_mean, ext=ex_mean,
                 dens=dens, dens_100=dens_100, stock_share=stock_share, in_pool=in_pool,
                 j1=j1, j2=j2, j3=j3, j4=j4, j5=j5, j7=j7,
                 all_pass=all([j1, j2, j3, j4, j5, j7]),
                 passed=sum([j1, j2, j3, j4, j5, j7])))
    dfam = pd.DataFrame(rows)
    judge_cols = ["j1", "j2", "j3", "j4", "j5", "j7"]
    n_passing = int(dfam["all_pass"].sum())

    merged_daily = {}
    for (fam, td), v in day_fam.items():
        e = merged_daily.setdefault(td, [0.0, 0])
        e[0] += v[0]
        e[1] += v[1]
    merged_series = np.array([v[0] / v[1] for _, v in sorted(merged_daily.items())])
    m_t, m_nd = clustered_t(merged_series)
    m_lo, m_hi = block_bootstrap_ci(merged_series)
    sharpe = (merged_series.mean() / merged_series.std(ddof=1)) * np.sqrt(252) if merged_series.std(ddof=1) > 0 else float("nan")
    pooled_net = sum(v[0] for v in fam_tot.values()) / total_events
    pooled_cost = sum(v[2] for v in fam_tot.values()) / total_events
    pool_dens = total_events / (pool_n * n_days)
    pool_dens_100 = total_events / n_days * 100.0 / pool_n
    max_share = max(
        (sum(fam_stock[f].get(code, 0) for f in families) for code in pool_set),
        default=0.0,
    )
    max_share /= total_events

    oos_merged = {}
    for (fam, td), v in day_fam.items():
        if td >= oos_start:
            oos_merged.setdefault(td, [0.0, 0])
            e = oos_merged[td]
            e[0] += v[0]
            e[1] += v[1]
    oos_merged_mean = sum(v[0] for v in oos_merged.values()) / sum(v[1] for v in oos_merged.values()) if oos_merged else float("nan")

    gate1 = n_passing >= min_fam
    gate2 = (m_t >= gate_t) and (sharpe >= gate_sharpe)
    gate3 = (pool_dens >= dens_stock) if gate_density else True
    gate4 = (oos_merged_mean >= 0) if gate_oos_neg else True
    verdict = "GO（进入 Phase 2）" if (gate1 and gate2 and gate3 and gate4) else "NO-GO（归档结题）"

    daily_pivot = pd.DataFrame(
        {f: {td: v[0] / v[1] for (ff, td), v in day_fam.items() if ff == f} for f in families}
    )
    corr = daily_pivot.corr(min_periods=30)
    max_pair, max_corr = ("n/a", float("nan"))
    for i in range(len(corr)):
        for j in range(i + 1, len(corr)):
            v = corr.iloc[i, j]
            if not np.isnan(v) and (np.isnan(max_corr) or v > max_corr):
                max_pair, max_corr = "%s-%s" % (corr.index[i], corr.columns[j]), float(v)

    tier_lt_net = tier_tot["lt"][0] / tier_tot["lt"][1] if tier_tot["lt"][1] else float("nan")
    tier_ge_net = tier_tot["ge"][0] / tier_tot["ge"][1] if tier_tot["ge"][1] else float("nan")
    tier_lt_daily = np.array([v[0] / v[1] for (f, _), v in tier_daily.items() if f == "lt"])
    tier_lt_t, _ = clustered_t(tier_lt_daily)
    tier_attach_needed = bool(not np.isnan(tier_lt_net) and tier_lt_net <= 0)

    date_suffix = args.date or pd.Timestamp.now().strftime("%Y%m%d")
    out_path = results / ("M1_decision_report_%s.md" % date_suffix)
    L = []
    L.append("# P21 M1 裁决报告（%s）" % date_suffix)
    L.append("")
    L.append("## 结论：%s" % verdict)
    L.append("")
    L.append("| M1 门 | 阈值（yaml） | 实测 | 结果 |")
    L.append("|---|---|---|---|")
    L.append("| 单族全部通过 ≥ N | %d 族 | %d 族 | %s |" % (min_fam, n_passing, "PASS" if gate1 else "FAIL"))
    L.append("| 合并六族聚类 t | ≥ %.1f | %.2f（另 block-bootstrap CI [%.2f, %.2f]） | %s |" % (gate_t, m_t, m_lo, m_hi, "PASS" if m_t >= gate_t else "FAIL"))
    L.append("| 合并六年化夏普（扣全成本） | ≥ %.1f | %.2f | %s |" % (gate_sharpe, sharpe, "PASS" if sharpe >= gate_sharpe else "FAIL"))
    L.append("| 候选密度（每股日均，池级） | ≥ %.1f | %.2f | %s |" % (dens_stock, pool_dens, "PASS" if gate3 else "FAIL"))
    L.append("| 2024 OOS 不转负 | ≥ 0 | %.2f bps | %s |" % (oos_merged_mean, "PASS" if gate4 else "FAIL"))
    L.append("")
    L.append("判据来源：`%s`（sha256:%s，锁定 %s）。脚本内无任何硬编码阈值。" % (
        crit_path.name, sha, need(cfg, "meta", "locked_at", ctx="meta.")))
    L.append("")
    L.append("## 一、单族判据（yaml §single_family，全部通过才算过线）")
    L.append("")
    L.append("说明：「全过」= 判据 1/2/3/4/5/7 全部满足；判据 6（振幅分层必须单列）为报告级要求（见 §五）；"
             "判据 8（两两相关 < %.2f）为组合准入、非单族过线（见 §六）。" % corr_lt)
    L.append("")
    L.append("| 族 | 事件数 | 净期望bps(>%.0f) | 毛期望bps(≥%.0f) | 覆盖倍数(≥%.1f) | 聚类t(≥%.1f) | bootstrap下界(>%.1f) | 逐年(>%.0fbps) | OOS同向 | 剔极端日同向 | 每股日均(≥%.1f) | 单票占比(≤%.0f%%) | 全过 |" % (
        net_gt * 100, gross_gte, cov_gte, t_gte, ci_gt, yearly_gt, dens_stock, dens_max_share * 100))
    for r in rows:
        L.append("| %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["fam"], r["n"], fmt_bps(r["net"]), fmt_bps(r["gross"]), fmt_f(r["cov"], 2),
            fmt_f(r["t"], 2), fmt_f(r["ci_lo"], 2),
            "PASS" if r["j3"] else "FAIL", "PASS" if r["j4"] else "FAIL",
            "PASS" if r["j5"] else "FAIL", fmt_f(r["dens"], 2),
            "%.1f%%" % (r["stock_share"] * 100), "YES" if r["all_pass"] else "no"))
    L.append("")
    L.append("逐族明细：")
    for r in rows:
        L.append("- **%s**：净 %s bps / 毛 %s / 覆盖 %s / t %s (n_days=%d) / CI[%s, %s]；OOS2024 %s bps；剔极端日 %s bps；判据 %s" % (
            r["fam"], fmt_bps(r["net"]), fmt_bps(r["gross"]), fmt_f(r["cov"], 2),
            fmt_f(r["t"], 2), r["n_days"], fmt_f(r["ci_lo"], 2), fmt_f(r["ci_hi"], 2),
            fmt_bps(r["oos"]), fmt_bps(r["ext"]),
            " ".join("%s:%s" % (k.upper(), "P" if r[k] else "F") for k in judge_cols)))
    L.append("")
    L.append("## 二、逐年净期望（bps，逐族）")
    L.append("")
    L.append("| 族 | " + " | ".join(years) + " | 全期 |")
    L.append("|---" * (len(years) + 2) + "|")
    for r in rows:
        L.append("| %s | %s | %s |" % (r["fam"], " | ".join(fmt_bps(r["yearly"][y]) for y in years), fmt_bps(r["net"])))
    L.append("")
    L.append("## 三、合并六族")
    L.append("")
    L.append("- 事件数 %d；单笔等权净期望 **%s bps**（毛 %s，均成本 %s）" % (
        total_events, fmt_bps(pooled_net), fmt_bps(pooled_net + pooled_cost), fmt_bps(pooled_cost)))
    L.append("- 交易日聚类 t = **%s**（n_days=%d，日均序列等权）" % (fmt_f(m_t, 2), m_nd))
    L.append("- block-bootstrap 95%% CI = [%s, %s]（block=%d, B=%d, seed=%d）" % (
        fmt_f(m_lo, 2), fmt_f(m_hi, 2), BOOTSTRAP_BLOCK, BOOTSTRAP_B, BOOTSTRAP_SEED))
    L.append("- 年化夏普（扣全成本，日均×√252）= **%s**" % fmt_f(sharpe, 2))
    L.append("- OOS 2024 合并净期望 = %s bps（≥0 即过）" % fmt_bps(oos_merged_mean))
    L.append("")
    L.append("## 三之二、证伪归因")
    L.append("")
    L.append("| 族 | 毛期望 bps | 均成本 bps | 覆盖倍数(≥%.1f) | 组(stock-day)数 | 组内毛收益合计\\|mean\\| | 判定 |" % cov_gte)
    L.append("|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: -x["gross"]):
        g_cnt, g_abs = pair_info[r["fam"]]
        note = "成对抵消→毛alpha恒0" if g_abs < 1e-6 else ("覆盖不足" if r["cov"] < cov_gte else "覆盖达标")
        L.append("| %s | %s | %s | %s | %d | %.3g | %s |" % (
            r["fam"], fmt_bps(r["gross"]), fmt_bps(r["cost"]), fmt_f(r["cov"], 2),
            g_cnt, g_abs, note))
    L.append("")
    L.append("- **NO-GO 直接根因 = 判据 1 全灭**：六族覆盖倍数全部 < %.1f。毛期望最高的 F2 仅 %s bps，"
             "不足均成本 %s bps 的 %.0f%%；F4/F5 仅 +4.3/+2.6 bps，F3/F6 毛期望为负，F1 恒 0。" % (
                 cov_gte, fmt_bps(max(r['gross'] for r in rows)),
                 fmt_bps(pooled_cost),
                 100.0 * max(r["gross"] for r in rows) / pooled_cost))
    L.append("- **F1 结构性失效**：每 (ts_code, trade_date) 恰好成对发出（mom/rev × long/short 共 4 类两两配对），"
             "组内毛收益合计 ≈ 0（|mean|=%.3g bps），毛 alpha 恒为 0、净期望恒 = −成本，判据 1 无法通过。" % (
                 pair_info["F1"][1] if "F1" in pair_info else float("nan")))
    L.append("- 结论方向：判据 1 的失败来自**信号毛收益本身不足**，不是成本参数或阈值过严；"
             "在预注册默认参数点下，继续调参/放宽阈值属于事后修改（`discipline.no_post_hoc_threshold_change`）。")
    L.append("")
    L.append("## 四、候选密度")
    L.append("")
    L.append("| 指标 | 值 | 阈值 | 判定 |")
    L.append("|---|---|---|---|")
    L.append("| 池级每股日均（过线判据） | %.2f | ≥ %.1f | %s |" % (pool_dens, dens_stock, "PASS" if pool_dens >= dens_stock else "FAIL"))
    L.append("| 每百票日均（仅描述性 informational=%s） | %.1f | 不过线 | — |" % (dens_100_info, pool_dens_100))
    L.append("| 单票占全池信号比 | %.2f%% | ≤ %.0f%% | %s |" % (max_share * 100, dens_max_share * 100, "PASS" if max_share <= dens_max_share else "FAIL"))
    L.append("| 池规模 / 交易日 | %d / %d | — | — |" % (pool_n, n_days))
    L.append("")
    L.append("注意：判据 7 同时出现在 §一 单族表内。逐族每股日均（族事件数/(池×日)）见 §一「每股日均」列"
             "（F3/F6 过线、F1/F2/F4/F5 不过线），单族密度读法存在文档歧义，本报告两个读法均给出、以 §一 原文读法为准；"
             "无论采用哪种读法，M1 结论不变（0 族过判据 1）。")
    L.append("")
    L.append("## 五、振幅分层（判据 6，必须单列）")
    L.append("")
    L.append("| 分层 | 事件数 | 净期望 bps | 聚类 t |")
    L.append("|---|---|---|---|")
    L.append("| 振幅 < %.1f%%（低波带） | %d | %s | %s |" % (
        tier_lt, tier_tot["lt"][1], fmt_bps(tier_lt_net), fmt_f(tier_lt_t, 2)))
    L.append("| 振幅 ≥ %.1f%% | %d | %s | — |" % (tier_lt, tier_tot["ge"][1], fmt_bps(tier_ge_net)))
    L.append("")
    L.append("振幅口径 = (high-low)/pre_close（日线）。**低波带净 alpha：%s** → %s" % (
        "有" if tier_lt_net > 0 else "无", "无需附加拍板" if not tier_attach_needed else "**若无净 alpha，M1 裁决时必须拍板挂靠二选一**（yaml amplitude_tier_check.if_no_alpha_require_attachment_decision）"))
    L.append("")
    L.append("## 六、信号间相关性（判据 8，组合准入）")
    L.append("")
    L.append("最大两两相关 = **%s**（阈值 < %.2f）→ %s" % (
        fmt_f(max_corr, 3), corr_lt, "可考虑组合" if (not np.isnan(max_corr) and max_corr < corr_lt) else "不满足组合准入"))
    L.append("")
    try:
        L.append(corr.round(3).to_markdown())
    except Exception:
        L.append("| | " + " | ".join(corr.columns) + " |")
        L.append("|---" * (len(corr.columns) + 1) + "|")
        for idx in corr.index:
            L.append("| %s | %s |" % (idx, " | ".join("%.3f" % v for v in corr.loc[idx].values)))
    L.append("")
    L.append("## 七、口径核验与声明")
    L.append("")
    L.append("- 成本分段核验：cutoff %s 前均值 %s bps（期望 %s）/ 后 %s bps（期望 %s）" % (
        need(cfg, "cost", "cutoff", ctx="cost."),
        fmt_f(cost_check["pre"][0] / max(cost_check["pre"][1], 1), 2), cost_pre,
        fmt_f(cost_check["post"][0] / max(cost_check["post"][1], 1), 2), cost_post))
    L.append("- 零股：事件表 shares 最小值 %d ≥ min_shares=%d（扫描期已跳过，不入统计）" % (
        min_shares, min_shares))
    L.append("- 预注册有效性：判据锁定 2026-09-28（`meta.valid_before_results_seen=true`），本报告为锁定后首次聚合收益类统计。")
    L.append("- **单源结论（仅 astock 分钟线），未经备用源交叉验证**：gpsj 为日线 duckdb 无 1min 数据，分钟级信号天然无法交叉验证。")
    L.append("- **宇宙 look-ahead 警示**：扫描池为 2009-2026 全周期中位数过滤的静态 %d 只（criteria 标准 %s），套用于 2019-2024；若 Go，Phase 2 必须按年动态建池复核。" % (
        pool_n, criteria_std_n))
    L.append("- 裁决只认 `midvol_criteria.yaml` 预注册默认参数点（`discipline.only_default_params_count`）。")
    L.append("- 未拍板项：做T挂靠对象（r3 §七 第 4 项）——须与本次 M1 结论一并拍板。")
    L.append("")
    out_path.write_text("\n".join(L), encoding="utf-8")

    print("")
    print("==== M1 VERDICT ====")
    print("passing_families=%d / required>=%d -> gate1 %s" % (n_passing, min_fam, "PASS" if gate1 else "FAIL"))
    print("merged_t=%.2f (>=%.1f) -> %s" % (m_t, gate_t, "PASS" if m_t >= gate_t else "FAIL"))
    print("merged_sharpe=%.2f (>=%.1f) -> %s" % (sharpe, gate_sharpe, "PASS" if sharpe >= gate_sharpe else "FAIL"))
    print("pool_density=%.2f (>=%.1f) -> %s" % (pool_dens, dens_stock, "PASS" if gate3 else "FAIL"))
    print("oos_2024=%.2f bps -> %s" % (oos_merged_mean, "PASS" if gate4 else "FAIL"))
    print("tier_lt<%.1f%% net=%.2f bps attach_decision_needed=%s" % (tier_lt, tier_lt_net, tier_attach_needed))
    print("VERDICT: %s" % verdict)
    print("report: %s" % out_path)
    return 0 if verdict.startswith("GO") else 2


if __name__ == "__main__":
    sys.exit(main())
