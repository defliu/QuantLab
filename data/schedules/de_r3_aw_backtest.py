# -*- coding: utf-8 -*-
"""DE R3 Task B：全天候 NAV 最小回测
6 线：V1(30/30/25/15) / 风险平价(60日协方差ERC,月频) / 1/4等权 / 100%股 / 100%货 / V2(含纳指10%)
月频再平衡，成本 = Σ|Δw|×0.05%（单边口径，写在报告）
"""
import json
import math
import os

OUT = os.path.dirname(os.path.abspath(__file__))


def load(name):
    with open(os.path.join(OUT, name), encoding="utf-8") as f:
        return json.load(f)


def rets(rows):
    out = {}
    for i in range(1, len(rows)):
        pc = rows[i].get("pre_close")
        if pc:
            out[rows[i]["trade_date"]] = rows[i]["close"] / pc - 1.0
    return out


def erc_weights(cov, iters=100):
    n = len(cov)
    w = [1.0 / n] * n
    for _ in range(iters):
        rc = []
        for i in range(n):
            mrc = sum(cov[i][j] * w[j] for j in range(n))
            rc.append(w[i] * mrc)
        trc = sum(rc) / n
        new_w = []
        for i in range(n):
            if rc[i] > 0:
                new_w.append(w[i] * math.sqrt(trc / rc[i]))
            else:
                new_w.append(w[i])
        s = sum(new_w)
        w = [x / s for x in new_w]
    return w


def run_portfolio(dates, rmat, target_fn, rebalance_months, cost_rate=0.0005):
    """rmat: {leg: {date: ret}}; target_fn(idx, date) -> weights or None(rebalance needed)"""
    legs = list(rmat.keys())
    n = len(dates)
    nav = 1.0
    w = None
    navs, w_log = [], []
    for t in range(n):
        d = dates[t]
        if w is None:
            w = target_fn(t, d)
            s = sum(w.values())
            w = {k: v / s for k, v in w.items()}
        # 当日收益
        rp = sum(w[leg] * rmat[leg][d] for leg in w)
        nav *= (1 + rp)
        # 权重漂移
        tot = 1 + rp
        w = {k: v * (1 + rmat[k][d]) / tot for k, v in w.items()}
        # 月末再平衡
        is_month_end = (t == n - 1) or (dates[t + 1][:6] != d[:6])
        if is_month_end and rebalance_months:
            tw = target_fn(t, d)
            if tw is not None:
                s = sum(tw.values())
                tw = {k: v / s for k, v in tw.items()}
                turn = sum(abs(tw.get(k, 0) - w.get(k, 0)) for k in set(tw) | set(w))
                nav *= (1 - turn * cost_rate)
                w = dict(tw)
        navs.append(nav)
        w_log.append(dict(w))
    return navs


def metrics(navs, dates):
    n = len(navs)
    years = (int(dates[-1][:4]) - int(dates[0][:4]) +
             (int(dates[-1][4:6]) - int(dates[0][4:6])) / 12.0)
    cagr = navs[-1] ** (1 / years) - 1 if years > 0 else 0
    peak, mdd, mdd_pt = navs[0], 0.0, None
    for i, v in enumerate(navs):
        if v > peak:
            peak = v
        dd = v / peak - 1
        if dd < mdd:
            mdd = dd
            mdd_pt = dates[i]
    rets_d = [navs[i] / navs[i - 1] - 1 for i in range(1, n) if navs[i - 1] > 0]
    mu = sum(rets_d) / len(rets_d)
    sd = math.sqrt(sum((x - mu) ** 2 for x in rets_d) / (len(rets_d) - 1))
    sharpe = mu / sd * math.sqrt(252) if sd > 0 else 0
    calmar = cagr / abs(mdd) if mdd < 0 else float("inf")
    return {"cagr": round(cagr, 4), "maxdd": round(mdd, 4), "mdd_date": mdd_pt,
            "calmar": round(calmar, 3), "sharpe": round(sharpe, 3),
            "total": round(navs[-1], 4)}


def yearly(navs, dates):
    out = {}
    start = {}
    prev = 1.0
    cur_y = dates[0][:4]
    base = navs[0] / (1)  # not used
    y0_nav = 1.0
    for i, d in enumerate(dates):
        y = d[:4]
        if y != cur_y:
            out[cur_y] = round(navs[i - 1] / y0_nav - 1, 4)
            y0_nav = navs[i - 1]
            cur_y = y
    out[cur_y] = round(navs[-1] / y0_nav - 1, 4)
    return out


def intra_year_mdd(navs, dates, year):
    seg = [(d, v) for d, v in zip(dates, navs) if d[:4] == year]
    if not seg:
        return None
    peak, mdd = seg[0][1], 0.0
    for _, v in seg:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return round(mdd, 4)


def main():
    legs = load("de_r3_aw_legs.json")
    stock = rets(legs["stock_000300"])
    bond_e = rets(legs["bond_511010"])
    bond_i = rets(legs["bond_000012"])
    gold = rets(legs["gold_518880"])
    money = rets(load("de_r3_aw_money_h11025.json"))  # H11025.CSI 货币基金指数
    money_alt = rets(legs["money_511880"])  # 511880 稳健性对照
    nq = rets(legs["nq_513100"])
    nq_missing = [d for d in sorted(set(stock) & set(bond_e) & set(gold) & set(money))
                  if d not in nq]
    for d in nq_missing:
        nq[d] = 0.0  # 513100 缺失日按0收益补齐（记录在报告）
    result_nq_missing = nq_missing

    dates = sorted(set(stock) & set(bond_e) & set(gold) & set(money))
    dates = [d for d in dates if d >= "20141001"]  # warmup for RP
    bt_dates = [d for d in dates if d >= "20150101"]
    # 货币/纳指腿缺失日按 0 收益补齐（个别日指数未更新）
    for d in bt_dates:
        if d not in money:
            money[d] = 0.0
        if d not in money_alt:
            money_alt[d] = 0.0

    def mat(legs_map):
        return {k: v for k, v in legs_map.items()}, None

    rmat4 = {"stock": stock, "bond": bond_e, "money": money, "gold": gold}
    rmat4i = {"stock": stock, "bond": bond_i, "money": money, "gold": gold}
    rmat4m = {"stock": stock, "bond": bond_e, "money": money_alt, "gold": gold}
    rmat5 = {"stock": stock, "bond": bond_e, "money": money, "gold": gold, "nq": nq}

    # RP 用 warmup 起点；回测统计从 2015-01-01 截取
    def fixed(wmap):
        def fn(t, d):
            return dict(wmap)
        return fn

    def rp_fn(t, d):
        window = [x for x in dates if x < d][-60:]
        if len(window) < 60:
            return {k: 0.25 for k in ("stock", "bond", "money", "gold")}
        legs_r = {k: [rmat4[k][x] for x in window] for k in rmat4}
        keys = list(rmat4.keys())
        n = len(keys)
        mu = [sum(legs_r[k]) / 60 for k in keys]
        cov = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i, n):
                c = sum((legs_r[keys[i]][t] - mu[i]) * (legs_r[keys[j]][t] - mu[j])
                        for t in range(60)) / 59
                cov[i][j] = cov[j][i] = c
        w = erc_weights(cov)
        return {keys[i]: w[i] for i in range(n)}

    def rp_fn_idx(legs_map, rmap):
        def fn(t, d):
            window = [x for x in dates if x < d][-60:]
            if len(window) < 60:
                return {k: 1.0 / len(legs_map) for k in legs_map}
            keys = list(legs_map.keys())
            rs = {k: [rmap[k][x] for x in window] for k in keys}
            n = len(keys)
            mu = [sum(rs[k]) / 60 for k in keys]
            cov = [[0.0] * n for _ in range(n)]
            for i in range(n):
                for j in range(i, n):
                    c = sum((rs[keys[i]][t] - mu[i]) * (rs[keys[j]][t] - mu[j])
                            for t in range(60)) / 59
                    cov[i][j] = cov[j][i] = c
            w = erc_weights(cov)
            return {keys[i]: w[i] for i in range(n)}
        return fn

    V1 = {"stock": 0.30, "bond": 0.30, "money": 0.25, "gold": 0.15}
    V2 = {"stock": 0.25, "bond": 0.30, "money": 0.20, "gold": 0.15, "nq": 0.10}
    EW = {"stock": 0.25, "bond": 0.25, "money": 0.25, "gold": 0.25}

    lines = {}
    lines["V1"] = run_portfolio(bt_dates, rmat4, fixed(V1), True)
    lines["V1_bondidx"] = run_portfolio(bt_dates, rmat4i, fixed(V1), True)
    lines["V1_money511880"] = run_portfolio(bt_dates, rmat4m, fixed(V1), True)
    lines["RP"] = run_portfolio(bt_dates, rmat4, rp_fn_idx(rmat4, rmat4), True)
    lines["EW"] = run_portfolio(bt_dates, rmat4, fixed(EW), True)
    lines["V2"] = run_portfolio(bt_dates, rmat5, fixed(V2), True)

    # 100% 股 / 100% 货（无再平衡）
    nav_s, m = 1.0, 1.0
    navs_s, navs_m = [], []
    for d in bt_dates:
        nav_s *= (1 + stock[d])
        m *= (1 + money[d])
        navs_s.append(nav_s)
        navs_m.append(m)
    lines["STOCK100"] = navs_s
    lines["MONEY100"] = navs_m

    result = {"window": [bt_dates[0], bt_dates[-1]],
              "nq_missing_filled": result_nq_missing, "lines": {}}

    # 各腿单资产表现（2015 起）
    leg_stats = {}
    for nm, rmap in (("stock", stock), ("bond_511010", bond_e), ("bond_000012", bond_i),
                     ("gold", gold), ("money_H11025", money), ("money_511880", money_alt),
                     ("nq", nq)):
        nav = 1.0
        peak, mdd = 1.0, 0.0
        for d in bt_dates:
            nav *= (1 + rmap[d])
            peak = max(peak, nav)
            mdd = min(mdd, nav / peak - 1)
        yrs = 11.7
        leg_stats[nm] = {"total": round(nav, 3), "cagr": round(nav ** (1 / yrs) - 1, 4),
                         "maxdd": round(mdd, 4)}
    result["leg_stats"] = leg_stats
    print("leg stats: %s" % json.dumps(leg_stats))
    print("window: %s ~ %s (%d days)" % (bt_dates[0], bt_dates[-1], len(bt_dates)))
    for name, navs in lines.items():
        mt = metrics(navs, bt_dates)
        yr = yearly(navs, bt_dates)
        mt["yearly"] = yr
        mt["dd_2018"] = intra_year_mdd(navs, bt_dates, "2018")
        mt["dd_2022"] = intra_year_mdd(navs, bt_dates, "2022")
        result["lines"][name] = {"metrics": mt, "nav_last": navs[-1]}
        print("%-10s CAGR=%6.2f%% MDD=%7.2f%% Calmar=%5.2f Sharpe=%5.2f | "
              "2018: %7.2f%%(dd %6.2f%%) 2022: %7.2f%%(dd %6.2f%%)" % (
                  name, mt["cagr"] * 100, mt["maxdd"] * 100, mt["calmar"],
                  mt["sharpe"], yr.get("2018", 0) * 100, (mt["dd_2018"] or 0) * 100,
                  yr.get("2022", 0) * 100, (mt["dd_2022"] or 0) * 100))

    # RP 权重抽样（首年末/2026-09）
    w_sample = rp_fn(0, bt_dates[-1])
    print("RP weights @end: %s" % {k: round(v, 3) for k, v in w_sample.items()})
    result["rp_end_weights"] = {k: round(v, 4) for k, v in w_sample.items()}

    # 压力情景（线性叠加）
    shock = {"stock": -0.30, "bond": -0.05, "money": 0.0, "gold": -0.15, "nq": -0.35}
    stress = {}
    for name, wm in (("V1", V1), ("V2", V2)):
        stress[name] = round(sum(w * shock[k] for k, w in wm.items()), 4)
    result["stress"] = stress
    print("stress: %s" % json.dumps(stress))

    # 腿间相关矩阵（2015 起）
    keys = ["stock", "bond", "money", "gold", "nq"]
    rmaps = [stock, bond_e, money, gold, nq]
    cors = {}
    for i in range(5):
        for j in range(i + 1, 5):
            ds = [d for d in bt_dates if d in rmaps[i] and d in rmaps[j]]
            a = [rmaps[i][d] for d in ds]
            b = [rmaps[j][d] for d in ds]
            ma, mb = sum(a) / len(a), sum(b) / len(b)
            cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
            va = math.sqrt(sum((x - ma) ** 2 for x in a))
            vb = math.sqrt(sum((y - mb) ** 2 for y in b))
            cors["%s~%s" % (keys[i], keys[j])] = round(cov / (va * vb), 3)
    result["leg_corr"] = cors
    print("leg corr: %s" % json.dumps(cors))

    # §3.7 预注册判据
    m = {k: result["lines"][k]["metrics"] for k in result["lines"]}
    v1, rp, ew, st = m["V1"], m["RP"], m["EW"], m["STOCK100"]
    checks = {
        "1_calmar_vs_stock_1.5x": {
            "rule": "V1 卡玛 ≥ 1.5×100%股 卡玛，否则关闭",
            "v1": v1["calmar"], "stock": st["calmar"],
            "line": round(1.5 * st["calmar"], 3),
            "pass": v1["calmar"] >= 1.5 * st["calmar"]},
        "2_ew_dominates": {
            "rule": "等权 年化≥V1+1pp 且 卡玛不差 → 弃V1",
            "ew_cagr": ew["cagr"], "v1_cagr": v1["cagr"],
            "ew_calmar": ew["calmar"], "v1_calmar": v1["calmar"],
            "pass": (ew["cagr"] >= v1["cagr"] + 0.01) and (ew["calmar"] >= v1["calmar"])},
        "3_rp_vs_v1_1pp": {
            "rule": "风险平价与V1年化差<1pp → 弃RP",
            "rp_cagr": rp["cagr"], "v1_cagr": v1["cagr"],
            "diff": round(abs(rp["cagr"] - v1["cagr"]), 4),
            "pass_drop_rp": abs(rp["cagr"] - v1["cagr"]) < 0.01},
        "4_stress": {
            "rule": "V1压力回撤>-15%",
            "v1_stress": stress["V1"], "pass": stress["V1"] > -0.15},
        "5_corr_existing": {
            "rule": "与现有5策略合成后夏普无实质改善 → 降级",
            "status": "未评（需W1-0相关性审计数据，本轮不编造）"},
    }
    result["checks_37"] = checks
    print(json.dumps(checks, ensure_ascii=False, indent=1))

    with open(os.path.join(OUT, "de_r3_aw_results.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("[SAVED] de_r3_aw_results.json")


if __name__ == "__main__":
    main()
