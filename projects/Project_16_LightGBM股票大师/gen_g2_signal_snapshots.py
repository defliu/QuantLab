# coding: utf-8
"""G2 信号内产版 · 构建期静态快照生成器（s4-5d，v3 int32 定点打包版）。

产出 strategy/_g2_signal_snapshots.py（纯数据常量，供 build_p16_g2_selfcontained.py 注入 QMT 单文件）:
  SNAPSHOT_DATE   快照生成日（YYYYMMDD，init 打印 + 过期告警）
  ACCOUNT_STAMP   账号戳（70180771，账本防错红线 T-20260823-004）
  SNAP_N          宇宙票数（与各平行数组长度一致）
  SNAP_BLOB       lzma+base64 二进制块，布局（全部 SNAP_N 票对齐）:
                    keys  9B/票  "000001.SZ" 定长 ascii
                    pe    int32 定点 x1000   （哨兵 -1000 -> NaN）
                    pb    int32 定点 x1000   （哨兵 -1000 -> NaN）
                    dv    int32 定点 x10000  （哨兵 -10000 -> NaN）
                    adj   int32 定点 x10000  （缺失 -> 1.0 即 10000）
                    ind   int16 行业序号     （-1 = 无行业；池见 IND_POOL）
  ST_CODES        最新交易日 is_st==1 的 code 文本元组
  IND_POOL        行业名池（文本元组）
  HOLD_DATES      L3 期初账本·持仓建仓日 {code: YYYYMMDD}（g2_hold_dates.json）
  ENS_HOLDS       ENS 互斥持仓 code 元组（g2_ens_hold_dates.json）
  CAPITAL_SNAP    L3 资金池快照（g2_strategy_capital.json，缺失回退 100000.0）

★ float_share 不入快照：运行时按必修③ 取 C.get_instrument_detail(code)['FloatVolume']/1e4。
★ 口径规则（H2 修复，2026-10-17）：估值/股本必须「逐字段取该票最后有效值」，严禁整行取。
★ 行集规则（H1 修复）：只生成「过滤帧」= universe(enabled) ∩ is_st==0 ∩ 剔 688/4/8
  （build_features_v2.py L93-97 逐字复刻），与 B21 训练行集一致。
★ 体积（<550KB 红线）：文本元组 295KB -> f32 打包 100KB -> int32 定点打包约 66KB。
用法: python gen_g2_signal_snapshots.py
"""
import base64
import json
import lzma
import os
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
MERGED = os.path.join(HERE, "data_live", "merged_daily_full.parquet")
BASIC = r"D:\astock\basic\stock_basic.parquet"
UNIVERSE = r"D:\QuantLab\data\universe_all_a.csv"

# 双臂配置（--arm g2|ens，同一份代码生成两份快照，供 G2/ENS 两策略内嵌）
# arm: (输出文件, 本方持仓日志, 对方持仓账本(买入互斥), 资金池文件)
ARMS = {
    "g2": ("_g2_signal_snapshots.py",
           os.path.join("data", "rebalance_g2", "g2_hold_dates.json"),
           os.path.join("data", "rebalance_g2_ens", "g2_ens_hold_dates.json"),
           r"D:\QMT_POOL\g2_bridge\g2_strategy_capital.json"),
    "ens": ("_ens_signal_snapshots.py",
            os.path.join("data", "rebalance_g2_ens", "g2_ens_hold_dates.json"),
            os.path.join("data", "rebalance_g2", "g2_hold_dates.json"),
            r"D:\QMT_POOL\p16_ensemble_bridge\ens_strategy_capital.json"),
}

# 2026-10-22 拍板：G2/ENS 由 70180771 迁至 67014907（70180771 未开通 miniQMT）。
# 本生成器读的是 *_strategy_capital.json 的资金池戳，必须同步，否则读不到资金池
# 会静默回退 START_CAPITAL(10 万)，导致内置快照的资金基准与实际不符。
ACCOUNT_ID = "67014907"
CODE_LEN = 9          # "000001.SZ" 定长
PE_SCALE = 1000.0
PB_SCALE = 1000.0
DV_SCALE = 10000.0
ADJ_SCALE = 10000.0
PE_NAN_Q = -1000
PB_NAN_Q = -1000
DV_NAN_Q = -10000
START_CAPITAL = 100000.0


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    t0 = time.time()
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="g2", choices=sorted(ARMS.keys()))
    args = ap.parse_args()
    _out_name, hold_rel, oppo_rel, capital_file = ARMS[args.arm]
    hold_dates_file = os.path.join(HERE, hold_rel)
    opponent_file = os.path.join(HERE, oppo_rel)
    out_path = os.path.join(HERE, "strategy", _out_name)
    print("[snap] arm=%s hold=%s opponent=%s" % (args.arm, hold_rel, oppo_rel))
    mg = pd.read_parquet(MERGED, columns=[
        "ts_code", "trade_date", "float_share", "pe_ttm", "pb", "dv_ttm",
        "adj_factor", "is_st"])
    if "ts_code" not in mg.columns:
        mg = mg.reset_index()
    mg["ts_code"] = mg["ts_code"].astype(str)
    last_date = mg["trade_date"].max()
    print("[snap] merged rows=%d last=%s" % (len(mg), last_date))

    # ---- 行集（build_features_v2 L93-97 逐字）----
    uni = pd.read_csv(UNIVERSE)
    enabled = set(uni[uni["enabled"] == True]["code"].astype(str).tolist())
    df = mg[mg["ts_code"].isin(enabled)]
    df = df[df["is_st"] == 0]
    codes_series = df["ts_code"]
    df = df[~codes_series.str.startswith("688") & ~codes_series.str.startswith(("4", "8"))]
    codes = sorted(set(df["ts_code"]))
    print("[snap] universe: %d -> %d (过滤帧)  (%.1fs)" % (
        len(enabled), len(codes), time.time() - t0))

    # 最新 ST 名单（全量口径，运行时剔当日新 ST）
    st_row = mg[mg["trade_date"] == last_date]
    st_codes = sorted(set(st_row[st_row["is_st"].astype(float) == 1]["ts_code"]))
    print("[snap] ST codes=%d" % len(st_codes))

    # ---- 逐字段最后有效值（H2 规则），仅过滤帧 ----
    sub = df[df["ts_code"].isin(codes)].sort_values("trade_date")
    cols = ["pe_ttm", "pb", "dv_ttm", "adj_factor"]
    snap = {}
    for c in cols:
        ok = sub[["ts_code", c]].dropna()
        last = ok.groupby("ts_code").tail(1)
        snap[c] = dict(zip(last["ts_code"], last[c].astype(float)))

    # ---- 行业 map（stock_basic.industry，训练口径）----
    basic = pd.read_parquet(BASIC, columns=["ts_code", "industry"])
    ind_map = dict(zip(basic["ts_code"].astype(str), basic["industry"].astype(str)))
    ind_pool = sorted({v for v in ind_map.values() if v and v == v})
    ind_idx = dict((n, i) for i, n in enumerate(ind_pool))

    # ---- 平行数组（int32 定点；缺失 -> 哨兵；adj 缺失 -> 1.0）----
    n = len(codes)
    miss = dict((c, 0) for c in cols)
    pe = np.empty(n, dtype=np.int32)
    pb = np.empty(n, dtype=np.int32)
    dv = np.empty(n, dtype=np.int32)
    adj = np.empty(n, dtype=np.int32)
    ind = np.empty(n, dtype=np.int16)
    for i, code in enumerate(codes):
        v = snap["pe_ttm"].get(code)
        if v is None:
            miss["pe_ttm"] += 1
            pe[i] = PE_NAN_Q
        else:
            pe[i] = int(round(v * PE_SCALE))
        v = snap["pb"].get(code)
        if v is None:
            miss["pb"] += 1
            pb[i] = PB_NAN_Q
        else:
            pb[i] = int(round(v * PB_SCALE))
        v = snap["dv_ttm"].get(code)
        if v is None:
            miss["dv_ttm"] += 1
            dv[i] = DV_NAN_Q
        else:
            dv[i] = int(round(v * DV_SCALE))
        v = snap["adj_factor"].get(code)
        if v is None:
            miss["adj_factor"] += 1
            adj[i] = int(round(1.0 * ADJ_SCALE))
        else:
            adj[i] = int(round(v * ADJ_SCALE))
        nm = ind_map.get(code)
        ind[i] = ind_idx.get(nm, -1) if nm else -1
        if int(pe[i]) >= 2 ** 31 or int(pb[i]) >= 2 ** 31 or int(dv[i]) >= 2 ** 31 \
                or int(adj[i]) >= 2 ** 31:
            raise SystemExit("[snap] int32 溢出 %s pe=%d pb=%d dv=%d adj=%d" % (
                code, pe[i], pb[i], dv[i], adj[i]))

    bad = [c for c in codes if len(c) != CODE_LEN]
    if bad:
        raise SystemExit("[snap] 非 %d 定长 code %d 个: %r" % (CODE_LEN, len(bad), bad[:5]))

    blob = b"".join(c.encode("ascii") for c in codes)
    blob += pe.tobytes() + pb.tobytes() + dv.tobytes() + adj.tobytes() + ind.tobytes()
    # ST 名单并入 blob（文本元组约 5.9KB -> 打包后 <2KB，为满足 <550KB 体积门禁）
    blob += np.int32(len(st_codes)).tobytes()
    for c in st_codes:
        if len(c) != CODE_LEN:
            raise SystemExit("[snap] ST code 非定长: %r" % c)
        blob += c.encode("ascii")
    packed = base64.b64encode(lzma.compress(blob, preset=9 | lzma.PRESET_EXTREME)).decode("ascii")

    # ---- L3 期初账本（account_id 戳校验，T-20260823-004）----
    hold_dates = {}
    if os.path.exists(hold_dates_file):
        d = _load_json(hold_dates_file)
        if str(d.get("account_id", "")) != ACCOUNT_ID:
            raise SystemExit("[snap] hold_dates account_id 不匹配: %r" % d.get("account_id"))
        hold_dates = dict(d.get("hold_dates", {}) or {})
    oppo_holds = []
    if os.path.exists(opponent_file):
        d = _load_json(opponent_file)
        oppo_holds = sorted(str(k) for k in (d.get("hold_dates", {}) or {}).keys())
    capital = START_CAPITAL
    if os.path.exists(capital_file):
        d = _load_json(capital_file)
        if str(d.get("account_id", "")) == ACCOUNT_ID and float(d.get("capital", 0) or 0) > 0:
            capital = float(d["capital"])
        else:
            print("[snap] 资金池 account_id 不匹配/空，回退 START_CAPITAL")
    print("[snap] hold_dates=%d oppo_holds=%d capital=%.0f" % (
        len(hold_dates), len(oppo_holds), capital))

    # ---- 写出 ----
    def _lines(s, w=400):
        # ★ 宽度 400（2026-10-17 体积门禁实证）：100 宽时每行 8B 包装开销占 ~10%，
        #   400 宽省约 3.3KB（快照/模型 blob 同法，合计省 ~20KB）。
        # ★ 无尾逗号 = 隐式字符串拼接；加逗号会把 SNAP_BLOB 变成 tuple -> 解包失败。
        return "\n".join('    "%s"' % s[i:i + w] for i in range(0, len(s), w))

    snap_date = str(last_date).replace("-", "")[:8]
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# coding: utf-8\n")
        f.write('"""%s 信号内产版静态快照（gen_g2_signal_snapshots.py 自动生成，禁止手改）。\n' % args.arm.upper())
        f.write("    生成日 %s  过滤帧 %d 只  账号 %s\n" % (snap_date, n, ACCOUNT_ID))
        f.write('"""\n')
        f.write('SNAPSHOT_DATE = "%s"\n' % snap_date)
        f.write('ACCOUNT_STAMP = "%s"\n' % ACCOUNT_ID)
        f.write("SNAP_N = %d\n" % n)
        f.write("CODE_LEN = %d\n" % CODE_LEN)
        f.write("PE_SCALE = %s\n" % repr(PE_SCALE))
        f.write("PB_SCALE = %s\n" % repr(PB_SCALE))
        f.write("DV_SCALE = %s\n" % repr(DV_SCALE))
        f.write("ADJ_SCALE = %s\n" % repr(ADJ_SCALE))
        f.write("PE_NAN_Q = %d\nPB_NAN_Q = %d\nDV_NAN_Q = %d\n" % (
            PE_NAN_Q, PB_NAN_Q, DV_NAN_Q))
        f.write("SNAP_BLOB = (\n%s\n)\n" % _lines(packed))
        f.write("IND_POOL = (\n%s\n)\n" % "\n".join(
            '    "%s",' % nm.replace('"', '\\"') for nm in ind_pool))
        f.write("HOLD_DATES = {\n%s\n}\n" % "\n".join(
            '    "%s": "%s",' % (k, v) for k, v in sorted(hold_dates.items())))
        # OPPONENT_HOLDS：对方持仓（买入互斥；G2 -> ENS 持仓 / ENS -> G2 持仓）
        f.write("OPPONENT_HOLDS = (\n%s\n)\n" % "\n".join(
            '    "%s",' % c for c in oppo_holds))
        f.write("CAPITAL_SNAP = %s\n" % repr(float(capital)))

    size = os.path.getsize(out_path)
    print("[snap] 写出 %s" % out_path)
    print("[snap] size=%d bytes (%.1f KB)  n=%d ind_pool=%d st=%d blob_b64=%d" % (
        size, size / 1024.0, n, len(ind_pool), len(st_codes), len(packed)))
    print("[snap] 缺失统计(逐字段最后有效值后): %s" % miss)
    print("[snap] done %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
