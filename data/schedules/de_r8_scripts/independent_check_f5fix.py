# -*- coding: utf-8 -*-
"""F5/F2 重放产物独立交叉验证（opencode 复核，不信任 DE 聚合）.

验证三层：
  L1 聚合：从 rerun_daily.csv 逐笔独立重算 actual/corrected 两臂闭合均值/胜率/累计/浮动均值，
           与 rerun_summary.json 对照。
  L2 收益：抽 09-08 分歧日三只票（603093.SH/300475.SZ/002145.SZ），用 merged_daily_full.parquet
           原始 close+adj 独立算复权收益（不依赖前复权基准日），对照 CSV 的 ret。
  L3 评分：用权重公式 (0.25F1+0.20F2+0.20F3+0.15F4+0.10F5)*10 + 0.10F6*10 抽查 total_new_corr。
"""
import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "f5fix_rerun", "rerun_daily.csv")
SUM = os.path.join(HERE, "f5fix_rerun", "rerun_summary.json")
MERGED = "D:/QuantLab/projects/Project_16_LightGBM股票大师/data_live/merged_daily_full.parquet"


def L1_aggregation():
    df = pd.read_csv(CSV)
    out = {}
    for arm in ("actual", "corrected"):
        a = df[df["arm"] == arm]
        closed = a[a["reason"] != "TRUNCATED"]
        o = {
            "positions": len(a),
            "buyable": int(a["buyable"].sum()),
            "closed": len(closed),
            "truncated": int((a["reason"] == "TRUNCATED").sum()),
            "closed_mean_ret_pct": round(closed["ret"].mean() * 100, 3),
            "closed_median_ret_pct": round(closed["ret"].median() * 100, 3),
            "closed_win_rate_pct": round((closed["ret"] > 0).mean() * 100, 1),
            "closed_sum_ret_pct": round(closed["ret"].sum() * 100, 3),
            "all_mean_ret_pct_float": round(a["ret"].mean() * 100, 3),
            "all_sum_ret_pct_float": round(a["ret"].sum() * 100, 3),
            "all_win_rate_pct_float": round((a["ret"] > 0).mean() * 100, 1),
            "exit_reason_dist": a["reason"].value_counts().to_dict(),
        }
        out[arm] = o
    # 分歧持仓：同 trade_date 下两臂票集合差
    act = df[df["arm"] == "actual"].set_index(["trade_date", "ts_code"])
    cor = df[df["arm"] == "corrected"].set_index(["trade_date", "ts_code"])
    act_only = act.loc[~act.index.isin(cor.index)]
    cor_only = cor.loc[~cor.index.isin(act.index)]
    out["actual_only"] = {
        "n": len(act_only),
        "closed": int((act_only["reason"] != "TRUNCATED").sum()),
        "mean_ret_pct": round(act_only["ret"].mean() * 100, 3),
        "sum_ret_pct": round(act_only["ret"].sum() * 100, 3),
        "detail": act_only[["ret", "reason"]].to_dict("index"),
    }
    out["corrected_only"] = {
        "n": len(cor_only),
        "closed": int((cor_only["reason"] != "TRUNCATED").sum()),
        "mean_ret_pct": round(cor_only["ret"].mean() * 100, 3),
        "sum_ret_pct": round(cor_only["ret"].sum() * 100, 3),
        "detail": cor_only[["ret", "reason"]].to_dict("index"),
    }
    return out


def L2_returns_sample():
    """抽 09-08 分歧日三票：用 merged_daily_full 原始 close+adj 算跨复权收益。"""
    df = pd.read_parquet(MERGED)
    # 探测列布局
    idx_cols = [c for c in df.index.names if c in ("trade_date", "ts_code")]
    if not idx_cols:
        idx_cols = [c for c in df.columns if c in ("trade_date", "ts_code")]
        df = df.set_index(["trade_date", "ts_code"])
    else:
        df = df.reset_index()
        df = df.set_index(["trade_date", "ts_code"])
    if "adj_factor" not in df.columns and "adj" in df.columns:
        df = df.rename(columns={"adj": "adj_factor"})
    samples = [
        # (code, entry_date, exit_date)
        ("603093.SH", "2026-09-08", "2026-09-14"),
        ("300475.SZ", "2026-09-08", "2026-09-14"),
        ("002145.SZ", "2026-09-08", "2026-09-18"),
    ]
    out = {}
    for code, ed, xd in samples:
        sub = df.xs(code, level="ts_code")
        row_e = sub.loc[pd.Timestamp(ed)]
        row_x = sub.loc[pd.Timestamp(xd)]
        # 跨复权收益 = close_x*adj_x/(close_e*adj_e) - 1
        ret = row_e["close"] * row_e["adj_factor"], row_x["close"] * row_x["adj_factor"]
        r = ret[1] / ret[0] - 1
        out[code] = {
            "entry_close": float(row_e["close"]),
            "entry_adj": float(row_e["adj_factor"]),
            "exit_close": float(row_x["close"]),
            "exit_adj": float(row_x["adj_factor"]),
            "ret_calc_pct": round(r * 100, 4),
        }
    return out


def L3_score_sample():
    df = pd.read_csv(CSV)
    df["trade_date"] = df["trade_date"].astype(str)
    rows = [
        ("20260903", "corrected", 2, "300259.SZ"),
        ("20260908", "corrected", 2, "002145.SZ"),
        ("20260908", "actual", 1, "603093.SH"),
    ]
    out = []
    for d, arm, rk, code in rows:
        m = df[(df["trade_date"] == d) & (df["arm"] == arm) & (df["rank"] == rk) & (df["ts_code"] == code)]
        if len(m) == 0:
            out.append({"key": f"{d}-{arm}-{code}", "error": "row not found"})
            continue
        r = m.iloc[0]
        # 修正总分：F2_new/F5_new 即修正后评分档位，直接代入权重公式
        f2s = float(r["F2_new"]) if not np.isnan(r["F2_new"]) else 5.0
        f5s = float(r["F5_new"]) if not np.isnan(r["F5_new"]) else 5.0
        total = (0.25 * r["SC_F1"] + 0.20 * f2s + 0.20 * r["SC_F3"] + 0.15 * r["SC_F4"] + 0.10 * f5s) * 10 + 0.10 * r["SC_F6"] * 10
        out.append({
            "key": f"{d}-{arm}-{code}",
            "F2_new": float(r["F2_new"]) if not np.isnan(r["F2_new"]) else None, "F2_score": f2s,
            "F5_new": float(r["F5_new"]) if not np.isnan(r["F5_new"]) else None, "F5_score": f5s,
            "total_recalc": round(total, 1),
            "total_csv": float(r["total_new_corr"]),
            "match": abs(total - r["total_new_corr"]) < 0.05,
        })
    return out


if __name__ == "__main__":
    print("=" * 60)
    print("L1 聚合独立重算")
    l1 = L1_aggregation()
    for k in ("actual", "corrected"):
        o = l1[k]
        print(f"[{k}] 笔数={o['positions']} 可买={o['buyable']} 闭合={o['closed']} 截断={o['truncated']} "
              f"闭合均值={o['closed_mean_ret_pct']}% 中位={o['closed_median_ret_pct']}% 胜率={o['closed_win_rate_pct']}% "
              f"闭合累计={o['closed_sum_ret_pct']}pp 浮动均值={o['all_mean_ret_pct_float']}% 浮动累计={o['all_sum_ret_pct_float']}pp")
        print(f"    出场分布={o['exit_reason_dist']}")
    print(f"[actual_only] n={l1['actual_only']['n']} 闭合={l1['actual_only']['closed']} "
          f"均值={l1['actual_only']['mean_ret_pct']}% 累计={l1['actual_only']['sum_ret_pct']}pp")
    print(f"[corrected_only] n={l1['corrected_only']['n']} 闭合={l1['corrected_only']['closed']} "
          f"均值={l1['corrected_only']['mean_ret_pct']}% 累计={l1['corrected_only']['sum_ret_pct']}pp")
    print(f"[actual_only detail]")
    for kk, v in l1["actual_only"]["detail"].items():
        print(f"    {kk[0]} {kk[1]} ret={v['ret']*100:.2f}% {v['reason']}")
    print(f"[corrected_only detail]")
    for kk, v in l1["corrected_only"]["detail"].items():
        print(f"    {kk[0]} {kk[1]} ret={v['ret']*100:.2f}% {v['reason']}")

    print("=" * 60)
    print("L2 收益抽样（merged_daily_full 原始 close+adj 独立复算）")
    l2 = L2_returns_sample()
    csv_ret = {
        "603093.SH": -9.556,
        "300475.SZ": -10.175,
        "002145.SZ": 6.481,
    }
    for code, v in l2.items():
        d = v["ret_calc_pct"] - csv_ret[code]
        print(f"  {code}: 独立算={v['ret_calc_pct']}% vs DE={csv_ret[code]}% 差={d:+.3f}pp "
              f"(entry {v['entry_close']}@{v['entry_adj']:.4f} -> exit {v['exit_close']}@{v['exit_adj']:.4f})")

    print("=" * 60)
    print("L3 评分公式抽查")
    for r in L3_score_sample():
        if "error" in r:
            print(f"  {r['key']}: {r['error']}")
        else:
            print(f"  {r['key']}: 复算={r['total_recalc']} vs CSV={r['total_csv']} match={r['match']} (F2_new={r['F2_new']}->{r['F2_score']}, F5_new={r['F5_new']}->{r['F5_score']})")

    # 对照 summary
    s = json.load(open(SUM, encoding="utf-8"))
    print("=" * 60)
    print("L1 vs rerun_summary.json 对照")
    for arm in ("actual_arm", "corrected_arm"):
        so = s[arm]
        lo = l1["actual" if arm == "actual_arm" else "corrected"]
        print(f"[{arm}] DE闭合均值={so['closed_mean_ret_pct']} 独立={lo['closed_mean_ret_pct']} | "
              f"DE胜率={so['closed_win_rate_pct']} 独立={lo['closed_win_rate_pct']} | "
              f"DE闭合累计={so['closed_sum_ret_pct']} 独立={lo['closed_sum_ret_pct']} | "
              f"DE浮动均值={so['all_mean_ret_pct_float']} 独立={lo['all_mean_ret_pct_float']}")
    su = s["unique_positions"]
    print(f"[actual_only] DE n={su['actual_only']['n']} 均值={su['actual_only']['mean_ret_pct']} 累计={su['actual_only']['sum_ret_pct']} "
          f"| 独立 n={l1['actual_only']['n']} 均值={l1['actual_only']['mean_ret_pct']} 累计={l1['actual_only']['sum_ret_pct']}")
    print(f"[corrected_only] DE n={su['corrected_only']['n']} 均值={su['corrected_only']['mean_ret_pct']} 累计={su['corrected_only']['sum_ret_pct']} "
          f"| 独立 n={l1['corrected_only']['n']} 均值={l1['corrected_only']['mean_ret_pct']} 累计={l1['corrected_only']['sum_ret_pct']}")
