# -*- coding: utf-8 -*-
"""三臂纸面季评（2026-09-13 立，DE 意见书 P1-1；T-20260913-001 执行）。

按季度汇总 G2 / V1.3 / ENS 三条纸面臂的样本外前向超额（与降级闸同口径），
产出对比报告：季度超额、胜率、bootstrap 区间（仅参考）、臂间候选重合率、
双季劣化提议。**只提议、不动资金分配**——资金分配是共享账户红线
（config/capital_allocation.yaml + 校验器硬流程），季评无权触碰。

口径（与 paper_forward_downgrade 完全同源，直接 import 复用）：
  - 超额 = 个股 open→open 复权收益 − 同窗口全市场等权基准
  - 实盘 TOP2 口径（rank<=2），已到期样本（信号日+hold+1 交易日 <= 最新日）
  - G2 臂 N15=live 一致；ENS 臂 N10=live 一致；V1.3 臂 N10=候选口径（实盘 N5，有意为之）

判定规则（保守）：
  - 单季纳入排名需该臂到期样本 n >= MIN_N(30)；不足标「样本不足」不排名
  - bootstrap 95% 区间仅参考，不作硬门槛（30-60 笔小样本 t 检验功效低）
  - 提议退役/降配 = 连续两个季度排名最末 且 两季与最优差均 > DIFF_FLOOR(0.0005)；
    单季劣化只记录。裁决永远人工。

用法：
  python paper_quarterly_review.py            # 生成 data/quarterly_review_<date>.md
  python paper_quarterly_review.py --check    # 只打印不落盘

退出码：0 正常；1 运行错误。
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
import data_config as DC
import paper_forward_downgrade as PD  # 复用 ARMS/load_open/load_bench/_calendar（同口径同源）

REAL = PD.REAL
OUT_MD = os.path.join(DC.DATA_DIR, "quarterly_review_%s.md" % pd.Timestamp.now().strftime("%Y%m%d"))

MIN_N = 30        # 单季纳入排名的最小到期样本数（与降级闸 MIN_SAMPLES 同值）
DIFF_FLOOR = 0.0005  # 双季提议阈值：最末臂与最优臂的季度超额差（>0.05pp/日 才算显著落后）
BOOT_N = 1000     # bootstrap 重抽样次数
BOOT_SEED = 42    # 固定种子（报告可复现）


def _quarter(ds):
    """'2026-08-17' -> '2026Q3'（自然季，按信号日归季）。"""
    return "%dQ%d" % (pd.Timestamp(ds).year, (pd.Timestamp(ds).month - 1) // 3 + 1)


def _arm_rows(key):
    """读单臂 CSV → 已到期 TOP2 样本 DataFrame[quarter, date, code, ret, bench, excess]。
    成熟口径与 PD._eval_arm 完全一致（rank<=2 + 日历到期 + load_open/load_bench）。"""
    _, hold = PD.ARMS[key]
    csv_path = os.path.join(REAL, PD.ARMS[key][0])
    if not os.path.exists(csv_path):
        return pd.DataFrame(columns=["quarter", "date", "code", "ret", "bench", "excess"])
    cal = PD._calendar()
    ret_map = PD.load_open(hold)
    bench_map = PD.load_bench(hold)
    try:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
    except Exception:
        return pd.DataFrame(columns=["quarter", "date", "code", "ret", "bench", "excess"])
    if df is None or df.empty:
        return pd.DataFrame(columns=["quarter", "date", "code", "ret", "bench", "excess"])
    rows = []
    for _, r in df.iterrows():
        if "rank" in df.columns and pd.notna(r.get("rank")) and int(r["rank"]) > 2:
            continue  # 实盘 TOP2 口径
        d0 = str(r["date"])
        if d0 not in cal:
            continue
        i = cal.index(d0)
        if i + hold + 1 >= len(cal):
            continue  # 尚未到期
        v = ret_map.get(str(r["code"]), {}).get(d0)
        b = bench_map.get(d0)
        if pd.notna(v) and b is not None and not (isinstance(b, float) and np.isnan(b)):
            rows.append((_quarter(d0), d0, str(r["code"]), float(v), float(b), float(v) - float(b)))
    return pd.DataFrame(rows, columns=["quarter", "date", "code", "ret", "bench", "excess"])


def _boot_ci(excs):
    """超额均值 bootstrap 95% 区间（固定种子，仅参考不作硬门槛）。n<2 返回 (None, None)。"""
    if len(excs) < 2:
        return (None, None)
    arr = np.asarray(excs, dtype=float)
    rng = np.random.RandomState(BOOT_SEED)
    means = np.empty(BOOT_N)
    for i in range(BOOT_N):
        means[i] = rng.choice(arr, size=len(arr), replace=True).mean()
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def _arm_sets(key, top_n):
    """{date: {code,...}}（rank<=top_n 的候选集合，重合率用；不做到期过滤——重合率看选股重叠，与到期无关）。"""
    csv_path = os.path.join(REAL, PD.ARMS[key][0])
    if not os.path.exists(csv_path):
        return {}
    try:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
    except Exception:
        return {}
    if df is None or df.empty or "rank" not in df.columns:
        return {}
    out = {}
    for _, r in df.iterrows():
        if pd.notna(r.get("rank")) and int(r["rank"]) <= top_n:
            out.setdefault(str(r["date"]), set()).add(str(r["code"]))
    return out


def _jaccard_pair(a, b):
    """两臂共同信号日上的候选集合 Jaccard 均值；返回 (共同日数, 平均Jaccard)。"""
    common = sorted(set(a.keys()) & set(b.keys()))
    if not common:
        return (0, None)
    js = []
    for d in common:
        sa, sb = a.get(d, set()), b.get(d, set())
        u = sa | sb
        if u:
            js.append(len(sa & sb) / float(len(u)))
    return (len(common), float(np.mean(js)) if js else None)


def build_report(check_only=False):
    arms = list(PD.ARMS.keys())
    data = {}   # key -> rows DataFrame
    for k in arms:
        data[k] = _arm_rows(k)
        print("[载入] %-5s 到期TOP2样本 %d 笔（CSV 全量行含未到期/非TOP2）" % (k, len(data[k])))

    quarters = sorted(set().union(*[set(d["quarter"]) for d in data.values()])) if any(len(d) for d in data.values()) else []

    # ---- 逐季度逐臂指标 ----
    qstat = {}  # quarter -> {key: dict}
    for q in quarters:
        qstat[q] = {}
        for k in arms:
            sub = data[k][data[k]["quarter"] == q]
            n = len(sub)
            st = {"n": n, "mean_excess": None, "win": None, "mean_ret": None, "mean_bench": None,
                  "ci_lo": None, "ci_hi": None, "rankable": n >= MIN_N, "pos": None}
            if n > 0:
                st["mean_excess"] = float(sub["excess"].mean())
                st["win"] = float((sub["excess"] > 0).mean())
                st["mean_ret"] = float(sub["ret"].mean())
                st["mean_bench"] = float(sub["bench"].mean())
                st["ci_lo"], st["ci_hi"] = _boot_ci(sub["excess"].tolist())
            qstat[q][k] = st
        # 季内排名（仅 rankable 臂，按超额降序；不足 30 笔不参与排名）
        rankable = [k for k in arms if qstat[q][k]["rankable"]]
        rankable.sort(key=lambda k: qstat[q][k]["mean_excess"], reverse=True)
        for pos, k in enumerate(rankable, 1):
            qstat[q][k]["pos"] = pos

    # ---- 双季劣化提议（最近两个季度，全历史重算，自包含） ----
    proposals = []
    if len(quarters) >= 2:
        q1, q2 = quarters[-2], quarters[-1]
        for k in arms:
            s1, s2 = qstat[q1][k], qstat[q2][k]
            if s1["pos"] is None or s2["pos"] is None:
                continue  # 任一季样本不足 → 连续性断裂，不提议
            # 「排名最末」= 参与排名的臂中 pos 最大
            n_rank1 = sum(1 for x in arms if qstat[q1][x]["pos"] is not None)
            n_rank2 = sum(1 for x in arms if qstat[q2][x]["pos"] is not None)
            if not (s1["pos"] == n_rank1 and s2["pos"] == n_rank2):
                continue
            # 两季与最优差均超阈值
            best1 = max(qstat[q1][x]["mean_excess"] for x in arms if qstat[q1][x]["pos"] is not None)
            best2 = max(qstat[q2][x]["mean_excess"] for x in arms if qstat[q2][x]["pos"] is not None)
            g1, g2 = best1 - s1["mean_excess"], best2 - s2["mean_excess"]
            if g1 > DIFF_FLOOR and g2 > DIFF_FLOOR:
                proposals.append((k, q1, q2, g1, g2))

    # ---- 臂间重合率（TOP2 与 TOP10 两口径） ----
    overlaps = []
    sets2 = {k: _arm_sets(k, 2) for k in arms}
    sets10 = {k: _arm_sets(k, 10) for k in arms}
    for i in range(len(arms)):
        for j in range(i + 1, len(arms)):
            ka, kb = arms[i], arms[j]
            d2, j2 = _jaccard_pair(sets2[ka], sets2[kb])
            d10, j10 = _jaccard_pair(sets10[ka], sets10[kb])
            overlaps.append((ka, kb, d2, j2, d10, j10))

    # ---- 渲染 markdown ----
    lines = []
    lines.append("# 三臂纸面季评（G2 / V1.3 / ENS）")
    lines.append("")
    lines.append("> 生成：%s" % pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("> 数据：paper_forward_live（G2, N15）/ paper_forward_ab_v3enh（V1.3, N10 候选口径，实盘 N5）/ paper_forward_ens（ENS, N10）")
    lines.append("> 超额口径 = 个股 open→open 复权收益 − 同窗口全市场等权基准（与降级闸同源，rank<=2 已到期）")
    lines.append("")
    lines.append("**制度声明：季评只提议、不动资金分配。** 资金分配是共享账户红线"
                 "（config/capital_allocation.yaml + scripts/check_capital_allocation.py 硬流程），"
                 "任何退役/降配决定须人工拍板后按分配表流程执行。")
    lines.append("")
    lines.append("## 判定规则")
    lines.append("")
    lines.append("- 单季纳入排名需到期样本 n ≥ %d；不足标「样本不足」不排名" % MIN_N)
    lines.append("- bootstrap 95% 区间仅参考，不作硬门槛（小样本 t 检验功效低）")
    lines.append("- 提议退役/降配 = 连续两季排名最末 且 两季与最优差均 > %.4f；单季劣化只记录" % DIFF_FLOOR)
    lines.append("")
    for q in quarters:
        lines.append("## %s" % q)
        lines.append("")
        lines.append("| 臂 | n | 超额均值 | 胜率 | 个股ret | 基准 | bootstrap95%(参考) | 排名 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for k in arms:
            s = qstat[q][k]
            if s["n"] == 0:
                lines.append("| %s | 0 | — | — | — | — | — | 样本不足 |" % k)
                continue
            ci = ("[%.5f, %.5f]" % (s["ci_lo"], s["ci_hi"])) if s["ci_lo"] is not None else "—"
            pos = ("%d" % s["pos"]) if s["pos"] is not None else "样本不足(n<%d)" % MIN_N
            lines.append("| %s | %d | %+.5f | %.0f%% | %+.4f | %+.4f | %s | %s |" % (
                k, s["n"], s["mean_excess"], s["win"] * 100, s["mean_ret"], s["mean_bench"], ci, pos))
        lines.append("")
        # 臂间 bootstrap 区间不叠盖提示（仅当 >=2 臂可排名）
        rankable = [k for k in arms if qstat[q][k]["pos"] is not None]
        if len(rankable) >= 2:
            for i in range(len(rankable)):
                for j in range(i + 1, len(rankable)):
                    ka, kb = rankable[i], rankable[j]
                    sa, sb = qstat[q][ka], qstat[q][kb]
                    if sa["ci_lo"] is None or sb["ci_lo"] is None:
                        continue
                    if sa["ci_hi"] < sb["ci_lo"] or sb["ci_hi"] < sa["ci_lo"]:
                        lines.append("> ⚠️ %s 与 %s 的 bootstrap 区间不叠盖（%s 显著劣于 %s）——"
                                     "但区间仅参考，不构成硬判据，以提议规则为准。" % (ka, kb, kb, ka))
                        lines.append("")
    if not quarters:
        lines.append("## （无到期样本）")
        lines.append("")
        lines.append("三臂当前均无已到期 TOP2 样本，无季度可评估。")
        lines.append("")

    lines.append("## 臂间候选重合率（非独立性警告）")
    lines.append("")
    lines.append("> 三臂非独立：G2 与 ENS 共享 G2 模型（融合 50% 权重），v3_enh（V1.3 臂/ENS 半边）与 G2 特征集大量重叠。"
                 "重合率高时「多臂同时劣化」应读作**同源证据**，不是独立相互确认；反之重合率低才具独立证据价值。")
    lines.append("")
    lines.append("| 臂对 | 共同信号日 | TOP2 Jaccard均值 | TOP10 Jaccard均值 |")
    lines.append("|---|---|---|---|")
    for ka, kb, d2, j2, d10, j10 in overlaps:
        lines.append("| %s × %s | %d | %s | %s |" % (
            ka, kb, d2,
            ("%.2f" % j2) if j2 is not None else "—",
            ("%.2f" % j10) if j10 is not None else "—"))
    lines.append("")

    lines.append("## 提议（只提议，不动资金）")
    lines.append("")
    if proposals:
        for k, q1, q2, g1, g2 in proposals:
            lines.append("- **提议人工复议 %s 臂的退役/降配**：%s、%s 连续两季排名最末，与最优差分别为 %+.5f / %+.5f（均 > %.4f 阈值）。"
                         "本提议仅为报告结论，不执行任何操作；处置须人工拍板并走资金分配表流程。" % (k, q1, q2, g1, g2, DIFF_FLOOR))
    else:
        lines.append("- 无提议。触发条件（连续两季最末 + 差超阈值）未满足——当前各臂样本量远未达单季 %d 笔门槛，"
                     "属预期（G2 纸面 2026-08-17 起 / V1.3 臂 09-08 起 / ENS 臂 09-09 起，均不足一个季度）。" % MIN_N)
    lines.append("")
    lines.append("## 口径注记")
    lines.append("")
    lines.append("- V1.3 臂按 N10 候选口径评估（与 ab_stats/降级闸同源），实盘 rebalance_daily 为 N5 到期制——"
                 "臂间对比在候选口径下进行，读作「模型/配置候选的相对优劣」，非实盘收益复述。")
    lines.append("- ENS 实盘若按 P0-4 对齐清单补大盘门控，实盘口径将与纸面臂分叉，届时本报告需带注记解读。")
    lines.append("- 到期判定依赖 merged_daily_full.parquet 交易日历；数据末端不足 hold+1 交易日的信号自动未到期。")
    lines.append("")

    md = "\n".join(lines)
    if check_only:
        print("[CHECK] dry-run：不落盘。报告预览前 40 行：")
        print("\n".join(md.split("\n")[:40]))
    else:
        with open(OUT_MD, "w", encoding="utf-8") as f:
            f.write(md)
        print("报告: %s" % OUT_MD)
    return 0


def main():
    ap = argparse.ArgumentParser(description="三臂纸面季评（只提议不动资金）")
    ap.add_argument("--check", action="store_true", help="只打印预览不落盘")
    args = ap.parse_args()
    print("=== 三臂纸面季评（%s）===" % pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"))
    return build_report(check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())
