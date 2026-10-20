# -*- coding: utf-8 -*-
"""N_STOCKS × CAPITAL_INIT 匹配测算（一次性分析，不入 QMT 产物）

口径完全镜像 strategy_v2.py:
  - 宇宙: circ_mv in (0, 300000) 万元 且 pe_ttm>0 且 pb>0 且 bps>0&roe>0&profit_dedt>0 且 非ST
  - 评分: 行业中性 BP(=1/pb) z-score (Z_WEIGHT=1.0, HP_WEIGHT=0)
  - 买入: per_stock = min(port_value/N, cash/n_to_buy); amount = floor(per_stock/price/100)*100; <=0 则跳过该股
数据: D:/QMT_POOL/financial_*.csv + st_list.csv + delist_info.csv；价格取 D:/astock daily 最新交易日收盘。
"""
import csv
import os
import json

POOL_DIR = "D:/QMT_POOL"
ASTOCK_DAILY = "D:/astock/daily/stock_daily.parquet"
ASTOCK_BASIC = "D:/astock/basic/stock_basic.parquet"

FIELDS = ["pe_ttm", "pb", "circ_mv", "total_mv", "bps", "roe", "profit_dedt"]
MARKET_CAP_MIN = 0.0
MARKET_CAP_MAX = 300000.0
PE_TTM_MIN = 0.0
PB_MIN = 0.0


def _csv(path, value_key):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = row.get("ts_code", "").strip()
            if not code:
                continue
            if value_key == "industry":
                out[code] = row.get("value", "")
            else:
                try:
                    out[code] = float(row.get("value", "") or 0)
                except Exception:
                    out[code] = 0.0
    return out


def main():
    import pandas as pd

    fin = {}
    for name in FIELDS:
        vals = _csv(os.path.join(POOL_DIR, "financial_%s.csv" % name), name)
        for code, v in vals.items():
            fin.setdefault(code, {})[name] = v
    ind_map = _csv(os.path.join(POOL_DIR, "industry_map.csv"), "industry")
    st_set = set(_csv(os.path.join(POOL_DIR, "st_list.csv"), "x").keys())

    # 全 A 股池 -> 对齐生产 pool（QMT get_stock_list_in_sector("沪深A股")，不含北交所 .BJ）
    basic = pd.read_parquet(ASTOCK_BASIC)
    code_col = "ts_code" if "ts_code" in basic.columns else basic.columns[0]
    all_codes = [c for c in basic[code_col].tolist()]
    pool = [c for c in all_codes if not c.endswith(".BJ")]
    print("池(basic parquet 全A) = %d ; 剔除北交所后(对齐 QMT 沪深A股) = %d"
          % (len(all_codes), len(pool)))

    # 宇宙筛选
    bp_vals = {}
    for code in pool:
        if code in st_set:
            continue
        fd = fin.get(code, {})
        circ_mv = fd.get("circ_mv", 0)
        if not (circ_mv and circ_mv > MARKET_CAP_MIN and circ_mv < MARKET_CAP_MAX):
            continue
        pe = fd.get("pe_ttm", 0)
        if not (pe and pe > PE_TTM_MIN):
            continue
        pb = fd.get("pb", 0)
        if not (pb and pb > PB_MIN):
            continue
        bps = fd.get("bps", 0)
        roe = fd.get("roe", 0)
        pdt = fd.get("profit_dedt", 0)
        if not (bps and bps > 0 and roe and roe > 0 and pdt and pdt > 0):
            continue
        bp_vals[code] = 1.0 / pb
    print("宇宙筛选后候选 = %d" % len(bp_vals))

    # 行业中性 z-score
    groups = {}
    for code, bp in bp_vals.items():
        ind = ind_map.get(code, "其他")
        groups.setdefault(ind, []).append((code, bp))
    z = {}
    for ind, items in groups.items():
        bps = [b for _, b in items]
        mean = sum(bps) / len(bps)
        std = max((sum((b - mean) ** 2 for b in bps) / len(bps)) ** 0.5, 1e-9)
        for code, bp in items:
            z[code] = (bp - mean) / std
    ranked = sorted(z.items(), key=lambda x: -x[1])

    # 价格: 最新交易日收盘
    # 注意: 该 parquet 的 pandas 元数据未清洗, pd.read_parquet(columns=[...]) 会崩 -> 走 pyarrow 列选择
    import pyarrow.parquet as pq
    t = pq.read_table(ASTOCK_DAILY, columns=["ts_code", "trade_date", "close"])
    try:
        daily = t.to_pandas(ignore_metadata=True)
    except TypeError:
        daily = pd.DataFrame({"ts_code": t.column("ts_code").to_pylist(),
                              "trade_date": t.column("trade_date").to_pylist(),
                              "close": t.column("close").to_pylist()})
    last_date = daily["trade_date"].max()
    px = daily[daily["trade_date"] == last_date].set_index("ts_code")["close"].to_dict()
    print("价格日期 = %s, 覆盖 = %d 只" % (str(last_date), len(px)))

    # 打印 top 候选价格分布
    Ns = [20, 25, 30, 40, 50, 60, 80, 100, 120, 160]
    Capitals = [100000.0, 200000.0, 500000.0, 1000000.0]

    print("\n=== 候选池价格分布 (全部 %d 只通过筛选) ===" % len(ranked))
    prices_all = [px.get(c) for c, _ in ranked if px.get(c)]
    prices_all.sort()
    for q in [0.10, 0.25, 0.50, 0.75, 0.90]:
        i = int(q * (len(prices_all) - 1))
        print("  价格 P%02d = %.2f 元" % (q * 100, prices_all[i]))

    # 打印排名前 90 的价格序列（诊断: 生产 top80 实际只买到 31 只 -> 校验排名与价格口径）
    print("\n=== 排名前 90 只 (rank, code, pb, price, 可买100股门槛12.5元) ===")
    for i, (code, sc) in enumerate(ranked[:90]):
        pb = 1.0 / bp_vals[code]
        p = px.get(code)
        mark = "OK" if p and p * 100 <= 1250 else "--"
        print("  %3d %s pb=%6.2f px=%7.2f %s" % (i + 1, code, pb, p if p else -1, mark))

    n_aff_top80 = 0
    for code, _sc in ranked[:80]:
        p = px.get(code)
        if p and p * 100 <= 1250:
            n_aff_top80 += 1
    print("\n[诊断] top80 中价格<=12.5元(10万/N=80可买一手)的只数 = %d" % n_aff_top80)

    # 生产真实语义（strategy_v2.py L1252-1269）: target = 评分 top-N（未持有），
    # 逐只 _execute_buy；买不起一手(amount<=0)则**静默跳过、不递补**。
    def simulate_topn(cap, N):
        """严格 top-N 口径: 只在前 N 名内建仓, 买不起一手即跳过(不向后递补)
        返回 (bought, invested): 实际建仓只数 / 实际投入金额"""
        cash = cap
        port_value = cap
        n_held = 0
        bought = 0
        invested = 0.0
        for code, _ in ranked[:N]:
            price = px.get(code)
            if not price or price <= 0:
                continue
            n_to_buy = N - n_held
            if n_to_buy <= 0:
                break
            per = min(port_value / N, cash / n_to_buy)
            amount = int(per / price / 100) * 100
            if amount <= 0:
                continue
            cost = amount * price
            if cost > cash:
                amount = int(cash / price / 100) * 100
                if amount <= 0:
                    continue
                cost = amount * price
            cash -= cost
            invested += cost
            n_held += 1
            bought += 1
        return bought, invested

    print("\n=== [生产口径] 模拟建仓：top-N 内实际能买到的只数 / N ===")
    print("(只在前 N 名内买；买不起一手则跳过、不递补)")
    header = "%-10s" % "本金"
    for N in Ns:
        header += " | N=%-7d" % N
    print(header)
    summary = {}
    util = {}
    for cap in Capitals:
        row = "%-10s" % ("%.0f万" % (cap / 10000))
        for N in Ns:
            bought, invested = simulate_topn(cap, N)
            summary[(cap, N)] = bought
            util[(cap, N)] = invested / cap
            row += " | %3d/%-3d " % (bought, N)
        print(row)

    print("\n=== 资金利用率 (实际投入 / 本金)，被跳过的股票=预算闲置 ===")
    print("%-10s" % "本金" + "".join(" | N=%-4d" % N for N in Ns))
    for cap in Capitals:
        line = "%-10s" % ("%.0f万" % (cap / 10000))
        for N in Ns:
            line += " | %5.1f%%" % (util[(cap, N)] * 100)
        print(line)

    # 每只预算 -> 可负担价格上限
    print("\n=== 单只预算 vs 一手门槛 (100股) ===")
    print("%-10s" % "本金" + "".join(" | N=%-4d" % N for N in Ns))
    for cap in Capitals:
        line = "%-10s" % ("%.0f万" % (cap / 10000))
        for N in Ns:
            line += " | %6.0f" % (cap / N)
        print(line)
    print("(单位: 元/只；单只预算 < 100×价格 即买不进一手)")

    # 买满 N 只所需本金 = N × 100 × (top-N 内最高价)
    print("\n=== 买满 N 只所需最低本金 (N×100股×top-N 最高价) ===")
    print("%-6s | %-10s | %-12s | %-12s" % ("N", "top-N最高价", "买满所需本金", "10万可得只数"))
    for N in Ns:
        topn_px = [px.get(c) for c, _ in ranked[:N] if px.get(c)]
        if not topn_px:
            continue
        hi = max(topn_px)
        need = N * 100 * hi
        print("%-6d | %8.2f元 | %9.0f元(%.1f万) | %3d只" % (N, hi, need, need / 10000, summary.get((100000.0, N), 0)))


if __name__ == "__main__":
    main()
