# -*- coding: utf-8 -*-
"""XVAL5 工作包 B —— rank-F5 OOS 边际票 alpha 符号裁决（预注册实验）。

预注册：research/xval5_prereg.md（冻结 2026-09-29 01:17:43，先于本脚本首次运行）。
数据：复用 xval_de_bt_cache.parquet / xval_de_bt_cache_mkt.parquet（516 日，TASK3 产物）。
模拟内核：xval_de_p345.simulate（与生产 scan_rotate_cost_real 语义对齐，V2/V3 已保真校验）。

裁决规则（三重门，见预注册 §3）：
  (a) circular block bootstrap 95% CI 不含 0（L=5, B=10000, seed=20260929）
  (b) Bonferroni 校正 p < 0.05（4 变体族）
  (c) OOS 前/后 1/3 子段均值符号一致且与全段一致
任一门失败 -> 该变体不可识别；4 变体全部失败 -> 工作包 B 总裁决=不可识别。
"""
import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import xval_de_p345 as P345  # noqa: E402  (自产物，TASK3 已授权)

SEED = 20260929
BLOCK = 5
NBOOT = 10000
THR = 60.0
IS_CUT = P345.IS_CUT
OUT_JSON = os.path.join(HERE, "xval5_b_results_20260929.json")

VARIANTS = ["rank_univ", "rank_pool", "ladder545", "ladder2468"]


# ---------------------------------------------------------------- helpers
def exec_ok_mask(g):
    """simulate 买入步同款可执行过滤（p345 L217-220）。"""
    return ~(g["vol_next"].isna() | (g["vol_next"] <= 0)
             | g["suspend_next"].notna()
             | (g["up_limit_next"].notna() & g["open_next"].notna()
                & (g["open_next"] >= g["up_limit_next"])))


def build_ladder2468(df):
    """恒等式构造 ladder{2,4,6,8,10} 列；先用 ladder545 校验恒等式（预注册 §2）。"""
    r = df["ind_rank_pct"].values
    f5_rku = np.where(np.isnan(r), 5.0, 10.0 * r)
    f5_l545 = np.where(np.isnan(r), 5.0,
                       np.select([r >= 0.8, r >= 0.6, r >= 0.4, r >= 0.2],
                                 [10.0, 8.0, 5.0, 4.0], default=2.0))
    f5_l268 = np.where(np.isnan(r), 5.0,
                       np.select([r > 0.8, r > 0.6, r > 0.4, r > 0.2],
                                 [10.0, 8.0, 6.0, 4.0], default=2.0))
    id_err = float(np.nanmax(np.abs((df["total_rku"].values + (f5_l545 - f5_rku))
                                    - df["total_rkl"].values)))
    assert id_err < 1e-9, f"恒等式校验失败: max err={id_err}"
    df = df.copy()
    df["total_l268"] = df["total_rku"].values + (f5_l268 - f5_rku)
    return df, id_err


def delta_series(top10_by_day, oos_dates, mkt_by_date, total_col, base_col="total_abs"):
    """预注册 §3 步骤 1-2：逐日边际票配对差 Δ_d。空集/全NaN 侧记 0；双侧空 → 0。"""
    deltas, n_days_any, n_R, n_A = [], 0, 0, 0
    win_R, win_A = [], []
    for d in oos_dates:
        g = top10_by_day[d]
        ok = exec_ok_mask(g)
        g = g[ok]
        ex = g["fwd_ret"].values - mkt_by_date[d]
        pv = g[total_col].values >= THR
        pa = g[base_col].values >= THR
        mR = pv & ~pa
        mA = pa & ~pv
        exR = ex[mR]
        exA = ex[mA]

        def mean0(x):
            return float(np.nanmean(x)) if len(x) and not np.all(np.isnan(x)) else 0.0

        deltas.append(mean0(exR) - mean0(exA))
        if mR.any() or mA.any():
            n_days_any += 1
        n_R += int(mR.sum())
        n_A += int(mA.sum())
        vR = exR[~np.isnan(exR)]
        vA = exA[~np.isnan(exA)]
        win_R.extend(vR.tolist())
        win_A.extend(vA.tolist())
    return (np.array(deltas), n_days_any, n_R, n_A,
            np.array(win_R), np.array(win_A), len(win_R), len(win_A))


def block_bootstrap_mean(delta, block=BLOCK, nboot=NBOOT, seed=SEED):
    """circular block bootstrap of the mean（预注册 §3 步骤 4）。"""
    n = len(delta)
    rng = np.random.RandomState(seed)
    k = int(np.ceil(n / block))
    starts = rng.randint(0, n, size=(nboot, k))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    samples = delta[idx].reshape(nboot, -1)[:, :n]
    means = samples.mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    p_raw = 2.0 * min(float((means >= 0).mean()), float((means <= 0).mean()))
    p_raw = min(1.0, p_raw)
    return float(lo), float(hi), p_raw, means


def sgn(x):
    return int(np.sign(x))


# ---------------------------------------------------------------- main
def main():
    t_start = time.time()
    print(f"[xval5_b] run start: {datetime.now():%Y-%m-%d %H:%M:%S}"
          f"（prereg 冻结 2026-09-29 01:17:43，顺序须 prereg < run）")
    df, mkt = P345.load()
    df, id_err = build_ladder2468(df)
    print(f"[xval5_b] 恒等式校验 PASS（ladder545 max err={id_err:.2e}）")
    dates = sorted(pd.Timestamp(d) for d in df["trade_date"].unique())
    mkt_by_date = dict(zip(mkt["trade_date"], mkt["mkt"]))
    assert len(mkt_by_date) == len(dates)

    is_dates = [d for d in dates if d < IS_CUT]
    oos_dates = [d for d in dates if d >= IS_CUT]
    print(f"[xval5_b] {len(dates)} 日 {dates[0].date()}~{dates[-1].date()}"
          f" | IS {len(is_dates)} | OOS {len(oos_dates)} | OOS 起点 {oos_dates[0].date()}")

    # Top10-by-prob 池（引擎红线实际作用池）
    df_sorted = df.sort_values(["trade_date", "prob"], ascending=[True, False], kind="stable")
    top10_all = df_sorted.groupby("trade_date", sort=True).head(10)
    top10_by_day = {pd.Timestamp(d): g for d, g in top10_all.groupby("trade_date", sort=True)}
    cols = {"rank_univ": "total_rku", "rank_pool": "total_rkp",
            "ladder545": "total_rkl", "ladder2468": "total_l268"}

    n3 = len(oos_dates) // 3
    print(f"\n===== 主判据：边际票配对日均超额差（OOS，红线{THR:.0f}，Top10-by-prob 池）=====")
    primary = {}
    for name in VARIANTS:
        (delta, n_any, nR, nA, winR, winA, cR, cA) = delta_series(
            top10_by_day, oos_dates, mkt_by_date, cols[name])
        lo, hi, p_raw, _ = block_bootstrap_mean(delta)
        p_bonf = min(1.0, 4.0 * p_raw)
        m_full = float(delta.mean())
        m_first = float(delta[:n3].mean())
        m_last = float(delta[-n3:].mean())
        s_full, s_first, s_last = sgn(m_full), sgn(m_first), sgn(m_last)
        med = float(np.median(delta[delta != 0])) if (delta != 0).any() else 0.0
        wr = float((winR > 0).mean()) if len(winR) else np.nan
        wa = float((winA > 0).mean()) if len(winA) else np.nan
        gate_a = (lo > 0 or hi < 0)
        gate_b = p_bonf < 0.05
        gate_c = (s_full != 0 and s_first == s_full and s_last == s_full)
        verdict = "有方向证据" if (gate_a and gate_b and gate_c) else "不可识别"
        primary[name] = dict(
            delta_mean=m_full, ci_lo=lo, ci_hi=hi, p_raw=p_raw, p_bonf=p_bonf,
            sub_first_mean=m_first, sub_last_mean=m_last,
            n_oos_days=len(oos_dates), n_effective_days=n_any,
            n_rank_added=nR, n_abs_added=nA,
            win_rate_rank=wr, win_rate_abs=wa,
            median_delta_nonzero=med,
            gate_a=bool(gate_a), gate_b=bool(gate_b), gate_c=bool(gate_c),
            verdict=verdict)
        print(f"\n[{name}]")
        print(f"  Δ̄(OOS) = {m_full:+.6f}   95% CI = [{lo:+.6f}, {hi:+.6f}]"
              f"   p_raw={p_raw:.4f}  p_bonf={p_bonf:.4f}")
        print(f"  子段 前1/3 {m_first:+.6f} | 后1/3 {m_last:+.6f}（n3={n3} 日）")
        print(f"  门(a)CI不含0={gate_a}  门(b)bonf<0.05={gate_b}  门(c)符号一致={gate_c}")
        print(f"  → 裁决：{verdict}")
        print(f"  次判据：中位数差(非零日)={med:+.6f} | 胜率 rank={wr:.4f} abs={wa:.4f}"
              f" | 有效日 {n_any}/{len(oos_dates)} | 边际票 rank侧+{nR} abs侧+{nA}"
              f"（有效fwd: {cR}/{cA}）")

    overall = "有方向证据" if any(v["verdict"] == "有方向证据" for v in primary.values()) \
        else "不可识别"
    print(f"\n===== 工作包 B 总裁决：{overall} =====")

    # ---------------- 附带输出：组合级 simulate（非判据） ----------------
    print("\n===== 附带：组合级 simulate（abs + 4 变体 × IS/OOS/FULL，thr=60，非裁决依据）=====")
    print("[load] MAIN_DAILY open/down_limit ...", flush=True)
    import data_config as DC
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["open", "down_limit"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily[daily["ts_code"].isin(set(df["ts_code"].unique()))]
    daily = daily[daily["trade_date"] >= P345.START - pd.Timedelta(days=45)]
    daily = daily.set_index(["trade_date", "ts_code"])
    open_map = daily["open"].to_dict()
    down_map = daily["down_limit"].to_dict()
    del daily

    segs = {"IS": is_dates, "OOS": oos_dates, "FULL": dates}
    sim_results = {}
    all_cols = dict(cols, **{"abs": "total_abs"})
    for name, col in all_cols.items():
        pd_day = P345.make_per_day(df, col)
        for seg_name, seg in segs.items():
            tr, dr, *_ = P345.simulate(seg, pd_day, THR, P345.N_HOLD, P345.SLIP,
                                       open_map, down_map)
            mkt_seg = np.array([mkt_by_date[d] for d in seg])
            sim_results[(name, seg_name)] = dict(
                n=len(tr), ex=float(np.nanmean(dr - mkt_seg)), mdd=P345.mdd(dr))
    print("变体 | 段 | 笔数 | 日均超额 | 最大回撤")
    for name in all_cols:
        for seg in ("IS", "OOS", "FULL"):
            r = sim_results[(name, seg)]
            print(f"  {name:11s} | {seg:4s} | {r['n']:4d} | {r['ex']:+.4%} | {r['mdd']:.1%}")

    print("\n[附带] walk-forward：IS 定参（thr 扫 50/52/55/58/60）→ OOS（非裁决依据）")
    thr_grid = (50, 52, 55, 58, 60)
    wf_results = {}
    for name, col in all_cols.items():
        pd_day = P345.make_per_day(df, col)
        best_thr, best_ex = None, -9e9
        for thr in thr_grid:
            tr, dr, *_ = P345.simulate(is_dates, pd_day, float(thr), P345.N_HOLD,
                                       P345.SLIP, open_map, down_map)
            mkt_seg = np.array([mkt_by_date[d] for d in is_dates])
            ex = float(np.nanmean(dr - mkt_seg))
            if ex > best_ex:
                best_thr, best_ex = thr, ex
        tr, dr, *_ = P345.simulate(oos_dates, pd_day, float(best_thr), P345.N_HOLD,
                                   P345.SLIP, open_map, down_map)
        mkt_seg = np.array([mkt_by_date[d] for d in oos_dates])
        wf_results[name] = dict(best_thr=int(best_thr), is_ex=best_ex,
                                oos_n=len(tr), oos_ex=float(np.nanmean(dr - mkt_seg)),
                                oos_mdd=P345.mdd(dr))
        print(f"  {name:11s} | IS最优thr={best_thr} | OOS {len(tr)} 笔 "
              f"| OOS ex={wf_results[name]['oos_ex']:+.4%} | OOS mdd={wf_results[name]['oos_mdd']:.1%}")

    # ---------------- 落盘 ----------------
    out = dict(
        run_started=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        prereg_frozen="2026-09-29 01:17:43",
        seed=SEED, block=BLOCK, nboot=NBOOT, thr=THR,
        n_dates=len(dates), n_is=len(is_dates), n_oos=len(oos_dates),
        n3=n3,
        identity_check_max_err=id_err,
        primary={k: {kk: (vv if not isinstance(vv, (np.floating, np.integer, np.bool_))
                          else vv.item()) for kk, vv in v.items()}
                 for k, v in primary.items()},
        overall_verdict=overall,
        sim_portfolio={f"{k[0]}|{k[1]}": {kk: vv.item() if isinstance(vv, np.floating) else vv
                                          for kk, vv in v.items()}
                       for k, v in sim_results.items()},
        walk_forward={k: {kk: (vv.item() if isinstance(vv, np.floating) else vv)
                          for kk, vv in v.items()} for k, v in wf_results.items()},
    )
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n[xval5_b] results -> {OUT_JSON}  (total {time.time()-t_start:.0f}s)")


if __name__ == "__main__":
    main()
