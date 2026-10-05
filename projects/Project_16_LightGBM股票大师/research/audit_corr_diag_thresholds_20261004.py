# -*- coding: utf-8 -*-
"""corr_diag 阈值人工审核：真实批次 + 历史样本分布统计（只读）。

目的
----
THRESHOLDS 里的默认值是**占位值**，未经真实数据校准。本脚本遍历
data/selections 下所有历史选股批次，实测每个维度的指标分布与每个阈值的
触发率，给出「有无鉴别力」的判读依据，供人工定稿阈值。

纪律
----
* 只读：不 import corr_diag_core 的判定逻辑之外的东西做**独立复算**，
  关键指标（行业 top1/HHI、宽基 top1、风格覆盖率、快照滞后天数、
  相关性 max/通过数/高相关对数）一律用原始输入重新算一遍，
  避免「用被审核对象审核自己」。复算与 core 输出的偏差超容差即 FAIL。
* 阈值一律**不改**：本脚本只产出证据，定稿是人工决策。
* 每个批次的 today 注入为该批次日自身（当天视角），使 date 维度
  不因「历史批次天然滞后」而污染其他维度的判读。

用法（Python 3.11 研究环境）
--------------------------
    python audit_corr_diag_thresholds_20261004.py                # 三策略全跑
    python audit_corr_diag_thresholds_20261004.py --kinds v13    # 只跑一个
    python audit_corr_diag_thresholds_20261004.py --limit 5      # 快速试跑

退出码 0 = 复算全部吻合（阈值定稿与否由人工判读，不影响退出码）。
"""
from __future__ import absolute_import, print_function

import argparse
import datetime as dt
import glob
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import corr_diag_core as core  # noqa: E402
import corr_diag_io as cio  # noqa: E402

PROJ = os.path.dirname(HERE)
OUT_DIR = os.path.join(PROJ, "results", "corr_diag")

# kind -> (selections 子目录, 文件名后缀)
CASE_DIRS = [
    ("v13", "", "_selection_full.csv"),
    ("g2", "g2", "_g2_top10.csv"),
    ("ens", "g2_ens", "_g2_top10.csv"),
]

_DATE8 = re.compile(r"^(\d{8})")

# 独立复算容差。core 对不同字段的舍入精度并不一致，容差必须按字段分档，
# 否则会把「core 舍入到 4 位」误报成「复算不一致」：
#   · _ratio() 原样返回、未舍入的字段 -> 1e-9（只容忍浮点求和次序差）
#   · _round(x, 4) 舍入的字段          -> 1e-4（round 半区间 5e-5 再留余量）
TOL_EXACT = 1e-9
TOL_R4 = 1e-4


# ---------------------------------------------------------------------------
# 独立复算（不引用 core 的判定逻辑）
# ---------------------------------------------------------------------------

def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / float(n)
    my = sum(ys) / float(n)
    sxy = sxx = syy = 0.0
    for a, b in zip(xs, ys):
        da = a - mx
        db = b - my
        sxy += da * db
        sxx += da * da
        syy += db * db
    if sxx <= 0.0 or syy <= 0.0:
        return None
    return sxy / ((sxx ** 0.5) * (syy ** 0.5))


def recalc_industry(codes, ind_map):
    """复算行业分布。

    口径对齐 core.industry_concentration：键用 str(code) 查（core 不做
    隐式类型转换），并且用「falsy 判缺失」而非「None 判缺失」——
    空字符串行业名在 core 里算缺失，这里若只判 None 就会漏计。
    """
    dist = {}
    miss = 0
    for c in codes:
        v = (ind_map or {}).get(str(c))
        if not v:
            miss += 1
        else:
            dist[v] = dist.get(v, 0) + 1
    n = len(codes)
    if not n or not dist:
        return {"ind_n_industries": len(dist), "ind_top1_ratio": None,
                "ind_hhi": None, "ind_miss_ratio": (miss / float(n)) if n
                else None}
    return {
        "ind_n_industries": len(dist),
        "ind_top1_ratio": max(dist.values()) / float(n),
        "ind_hhi": sum((float(v) / n) ** 2 for v in dist.values()),
        "ind_miss_ratio": miss / float(n),
    }


def recalc_index(codes, index_members):
    """复算宽基成分重叠（口径对齐 core.index_overlap：键用 str(code)）。"""
    dist = {}
    for idx, members in (index_members or {}).items():
        if not members:
            continue
        k = sum(1 for c in codes if str(c) in members)
        if k:
            dist[idx] = k
    n = len(codes)
    return {
        "idx_n_indexes": len(dist),
        "idx_top1_ratio": (max(dist.values()) / float(n))
        if (dist and n) else None,
    }


def _to_date(compact):
    """把 YYYYMMDD 字符串转成 date，用于计算自然日滞后。

    不能对 YYYYMMDD 字符串直接做减法（那是字符串运算，结果无意义）。
    不可解析一律返回 None，与 core._stale_days 的 fail-closed 一致。
    """
    if not compact:
        return None
    s = "".join(ch for ch in str(compact) if ch.isdigit())
    if len(s) != 8:
        return None
    try:
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def recalc_style(codes, style_members, members_date, signal_date):
    """复算风格覆盖。

    两处口径必须与 core.style_concentration 一致，否则交叉核对会假 FAIL：
      1. **桶计数不是唯一覆盖**：一只票可同时落进多个风格基准，
         bucket_counts 是「每个基准各命中多少」，求和可以大于候选数。
         唯一覆盖率要用 covered 集合，不能用桶计数之和。
      2. 覆盖率分母是候选总数 n，不是命中数。
    """
    sm = style_members or {}
    loaded = dict((k, v) for k, v in sm.items() if v)
    n = len(codes)
    covered = set()
    bucket = {}
    for c in codes:
        cs = str(c)
        for s, members in loaded.items():
            if cs in members:
                bucket[s] = bucket.get(s, 0) + 1
                covered.add(cs)
    d1 = _to_date(members_date)
    d2 = _to_date(signal_date)
    return {
        "sty_n_loaded": len(loaded),
        "sty_n_hit": len(covered),
        "sty_coverage": (len(covered) / float(n)) if n else None,
        "sty_n_buckets": len(bucket),
        "sty_top1_ratio": (max(bucket.values()) / float(n))
        if (bucket and n) else None,
        "sty_members_date": members_date,
        "sty_stale_days": (d2 - d1).days if (d1 and d2) else None,
    }


def recalc_corr(rets, codes, th):
    """复算相关性质量筛查、成对相关与高相关对数。

    口径对齐 core.correlation_clusters：
      · 收益序列键用 str(code)；
      · 高相关判定用**未舍入**的 r（core 里 high 也是拿未舍入 r 比的，
        只把 corr 字段舍入到 4 位）；若拿舍入值比，r=0.69996 会被
        误判成 0.7000 从而虚增一对高相关；
      · 质量筛查顺序同样是 样本数 -> 零收益占比 -> 标准差。

    注意 n_pairs 与 n_valid 的区别：core 的 res["pairs"] 对**每一个** i<j
    组合都占位（样本不足时写 corr=None 仍然 append），所以
    len(pairs) == n(n-1)/2；而 max/mean 只统计 r 非 None 的那些。
    两种计数必须分开，否则拿「有效对数」去比 len(pairs) 会必然假 FAIL。
    """
    passed = []
    # core 的 code_list 是 sorted(...)；虽然聚合量与顺序无关，但保持一致
    # 才能在 pairs 的逐项比对上定位差异来源。
    for c in sorted(str(c) for c in codes):
        seq = [x for x in (rets.get(c) or []) if x is not None]
        n = len(seq)
        if n < th["corr_min_obs"]:
            continue
        zr = sum(1 for x in seq if abs(float(x)) < 1e-12) / float(n)
        if zr > th["corr_max_zero_ratio"]:
            continue
        m = sum(seq) / float(n)
        sd = (sum((x - m) ** 2 for x in seq) / float(n)) ** 0.5
        if sd < th["corr_min_std"]:
            continue
        passed.append(c)
    hi = []
    mx = None
    n_slots = 0
    n_valid = 0
    acc = 0.0
    for i in range(len(passed)):
        for j in range(i + 1, len(passed)):
            ca, cb = passed[i], passed[j]
            n_slots += 1
            xs, ys = [], []
            for a, b in zip(rets.get(ca) or [], rets.get(cb) or []):
                if a is not None and b is not None:
                    xs.append(float(a))
                    ys.append(float(b))
            if len(xs) < th["corr_min_obs"]:
                continue
            r = _pearson(xs, ys)
            if r is None:
                continue
            n_valid += 1
            acc += r
            if mx is None or r > mx:
                mx = r
            if r >= th["corr_pair"]:
                hi.append((ca, cb, r))
    return {
        "cor_n_passed": len(passed),
        "cor_n_pairs": n_slots,
        "cor_n_valid": n_valid,
        "cor_max": mx,
        "cor_mean": (acc / float(n_valid)) if n_valid else None,
        "cor_n_high_pairs": len(hi),
        "cor_largest_cluster": _largest_component(passed, hi),
    }


def _largest_component(codes, high_pairs):
    """独立复算最大高相关簇规模。

    core 用的是并查集（路径压缩 + 按 root 分组）。这里刻意换成
    **邻接表 + 迭代 DFS 连通分量**：算法路径不同，若 core 在路径压缩、
    分组或 size 过滤上写错，独立实现才会暴露出来；照抄并查集
    等于用同一份逻辑验证自己，交叉核对就失去意义。

    注意实现是显式栈的迭代 DFS（``stack.pop()`` 取末元素），不是
    BFS；连通分量的节点集合与 BFS 等价，故结果不受影响，此处仅订正
    术语以免文档与代码不符。

    语义对齐 core：
      - 边 = 相关系数 >= corr_pair 的配对（已在 high_pairs 里）
      - 连通分量 size >= 2 才算「簇」（core 过滤掉孤立点）
      - 无簇时返回 0
    """
    if not codes:
        return 0
    adj = dict((c, set()) for c in codes)
    for ca, cb, _r in high_pairs:
        if ca in adj and cb in adj:
            adj[ca].add(cb)
            adj[cb].add(ca)

    seen = set()
    best = 0
    for start in codes:
        if start in seen:
            continue
        size = 0
        stack = [start]
        seen.add(start)
        while stack:
            cur = stack.pop()
            size += 1
            for nxt in adj.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        if size >= 2 and size > best:
            best = size
    return best


# ---------------------------------------------------------------------------
# 批次发现（先只读 CSV 拿 signal_date，用于分组预热缓存）
# ---------------------------------------------------------------------------

def discover(kinds, limit=None):
    paths = cio.default_paths()
    found = []
    for kind, sub, suffix in CASE_DIRS:
        if kind not in kinds:
            continue
        d = os.path.join(paths["sel_v13_dir"], sub) if sub \
            else paths["sel_v13_dir"]
        files = sorted(glob.glob(os.path.join(d, "*%s" % suffix)))
        for fp in files:
            bn = os.path.basename(fp)
            m = _DATE8.match(bn)
            if not m:
                continue
            found.append((kind, m.group(1), fp))
    found.sort(key=lambda x: (x[0], x[1]))
    if limit:
        by_kind = {}
        keep = []
        for kind, bd, fp in found:
            by_kind.setdefault(kind, 0)
            if by_kind[kind] >= limit:
                continue
            by_kind[kind] += 1
            keep.append((kind, bd, fp))
        found = keep
    return found


def presignals(found):
    """只读 CSV 计算每个批次的 PIT 基准日（不触碰 index_weight）。"""
    out = []
    for kind, bd, fp in found:
        try:
            _rows, m = cio.load_candidates(fp)
        except Exception:  # noqa: BLE001
            m = {}
        rtd, sg = core.resolve_signal_date(m.get("file_date"),
                                          m.get("row_trade_dates"))
        out.append((kind, bd, fp, sg))
    return out


def warm_cache(items, asof):
    """对同一 asof 只探测一次成分目录，后续批次全部命中缓存。"""
    if not items:
        return
    paths = cio.default_paths()
    codes = [c for c, _ in core.STYLE_BENCHMARKS] + \
            [c for c, _ in core.BROAD_BENCHMARKS]
    cio.load_index_members(paths["index_weight_dir"], codes,
                           use_cache=True, asof_date=asof)


# ---------------------------------------------------------------------------
# 单批次采集
# ---------------------------------------------------------------------------

def collect(kind, batch, filename, today, th):
    inp = cio.load_batch_inputs(kind, batch, filename=filename,
                                thresholds=th)
    date_info = core.resolve_batch_dates(
        file_date=inp.get("file_date"),
        row_trade_dates=inp.get("row_trade_dates"),
        market_last_date=inp.get("market_last_date"),
        today=today)
    res = core.diagnose(
        codes=inp["codes"], ind_map=inp["ind_map"],
        style_members=inp["style_members"],
        index_members=inp["index_members"], rets=inp["rets"],
        date_info=date_info,
        style_members_date=inp.get("style_members_date"),
        thresholds=th)
    sec = res.get("sections") or {}
    ind = sec.get("industry") or {}
    sty = sec.get("style") or {}
    idx = sec.get("index") or {}
    cor = sec.get("correlation") or {}

    row = {
        "kind": kind, "batch": batch,
        "signal_date": inp.get("signal_date"),
        "market_last_date": inp.get("market_last_date"),
        "n_cand": res.get("n_candidates"),
        "io_status": inp.get("io_status"),
        "overall": res.get("overall"),
        "st_ind": ind.get("status"), "st_sty": sty.get("status"),
        "st_idx": idx.get("status"), "st_cor": cor.get("status"),
        "ind_top1_ratio": ind.get("top1_ratio"),
        "ind_hhi": ind.get("hhi"),
        "ind_miss_ratio": ind.get("missing_ratio"),
        "ind_n_industries": ind.get("n_industries"),
        "sty_coverage": sty.get("coverage_ratio"),
        "sty_n_buckets": sty.get("n_buckets"),
        "sty_top1_ratio": sty.get("bucket_top1_ratio"),
        "sty_stale_days": sty.get("stale_days"),
        "sty_members_date": sty.get("members_date"),
        "sty_n_loaded": sty.get("n_benchmarks_loaded"),
        "idx_top1_ratio": idx.get("top1_ratio"),
        "idx_n_indexes": idx.get("n_indexes"),
        "cor_n_passed": cor.get("n_passed"),
        "cor_n_pairs": len(cor.get("pairs") or []),
        "cor_max": cor.get("max_corr"),
        "cor_mean": cor.get("mean_corr"),
        "cor_n_high_pairs": len([p for p in (cor.get("pairs") or [])
                                 if p.get("high")]),
        "cor_largest_cluster": cor.get("largest_cluster"),
    }

    # ---- 独立复算（只用原始输入，不看 core 的判定结果）----
    mine = {}
    mine.update(recalc_industry(inp["codes"], inp["ind_map"]))
    mine.update(recalc_index(inp["codes"], inp["index_members"]))
    mine.update(recalc_style(inp["codes"], inp["style_members"],
                             inp.get("style_members_date"),
                             inp.get("signal_date")))
    mine.update(recalc_corr(inp["rets"], inp["codes"], th))
    row["_mine"] = mine
    return row


def cross_check(row, npass, nfail, notes):
    m = row["_mine"]

    def cmp(name, a, b, tol):
        nonlocal npass, nfail
        if a is None and b is None:
            npass[0] += 1
            return
        if a is None or b is None:
            nfail[0] += 1
            notes.append("%s/%s 一侧为空 core=%s mine=%s"
                         % (row["kind"], row["batch"], a, b))
            return
        if abs(float(a) - float(b)) <= tol:
            npass[0] += 1
        else:
            nfail[0] += 1
            notes.append("%s/%s %s core=%s mine=%s"
                         % (row["kind"], row["batch"], name, a, b))

    # 容差按 core 实际舍入精度分档：
    #   ind_top1_ratio / ind_miss_ratio  : _ratio() 未舍入 -> TOL_EXACT
    #   ind_hhi                          : _round(...,4)  -> TOL_R4
    #   sty_coverage                     : _round(...,4)  -> TOL_R4
    #   sty_top1_ratio                   : _ratio() 未舍入 -> TOL_EXACT
    #   idx_top1_ratio                   : _round(...,4)  -> TOL_R4
    #   cor_max / cor_mean               : _round(...,4)  -> TOL_R4
    #   计数类（n_industries/n_buckets/stale_days/n_passed/n_pairs/
    #           n_high_pairs/largest_cluster）
    #                                    : 整数，必须完全相等
    cmp("ind_top1_ratio", row["ind_top1_ratio"], m["ind_top1_ratio"],
        TOL_EXACT)
    cmp("ind_hhi", row["ind_hhi"], m["ind_hhi"], TOL_R4)
    cmp("ind_miss_ratio", row["ind_miss_ratio"], m["ind_miss_ratio"],
        TOL_EXACT)
    cmp("ind_n_industries", row["ind_n_industries"], m["ind_n_industries"],
        0)
    cmp("idx_top1_ratio", row["idx_top1_ratio"], m["idx_top1_ratio"], TOL_R4)
    cmp("idx_n_indexes", row["idx_n_indexes"], m["idx_n_indexes"], 0)
    cmp("sty_coverage", row["sty_coverage"], m["sty_coverage"], TOL_R4)
    cmp("sty_top1_ratio", row["sty_top1_ratio"], m["sty_top1_ratio"],
        TOL_EXACT)
    cmp("sty_n_loaded", row["sty_n_loaded"], m["sty_n_loaded"], 0)
    cmp("sty_n_buckets", row["sty_n_buckets"], m["sty_n_buckets"], 0)
    cmp("sty_stale_days", row["sty_stale_days"], m["sty_stale_days"], 0)
    cmp("cor_n_passed", row["cor_n_passed"], m["cor_n_passed"], 0)
    # cor_n_pairs 比的是 len(core 的 pairs)（含 corr=None 的占位），
    # 所以复算侧必须给 n_slots 而不是「有效对数」。
    cmp("cor_n_pairs", row["cor_n_pairs"], m["cor_n_pairs"], 0)
    cmp("cor_max", row["cor_max"], m["cor_max"], TOL_R4)
    cmp("cor_mean", row["cor_mean"], m["cor_mean"], TOL_R4)
    cmp("cor_n_high_pairs", row["cor_n_high_pairs"], m["cor_n_high_pairs"], 0)
    # 最大簇规模：core 用并查集，本脚本用邻接表+迭代 DFS 连通分量，算法独立。
    cmp("cor_largest_cluster", row["cor_largest_cluster"],
        m["cor_largest_cluster"], 0)


# ---------------------------------------------------------------------------
# 统计与判读
# ---------------------------------------------------------------------------

def _quantile(vals, q):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    if len(v) == 1:
        return v[0]
    pos = q * (len(v) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    frac = pos - lo
    return v[lo] * (1 - frac) + v[hi] * frac


def _fmt(x, nd=4):
    if x is None:
        return "NA"
    if isinstance(x, float):
        return ("%%.%df" % nd) % x
    return str(x)


def describe(name, vals, unit=""):
    ok = [x for x in vals if x is not None]
    print("  %-18s n=%-3d min=%-9s p25=%-9s med=%-9s p75=%-9s "
          "p90=%-9s max=%-9s %s"
          % (name, len(ok), _fmt(_quantile(vals, 0.0)),
             _fmt(_quantile(vals, 0.25)), _fmt(_quantile(vals, 0.5)),
             _fmt(_quantile(vals, 0.75)), _fmt(_quantile(vals, 0.9)),
             _fmt(_quantile(vals, 1.0)), unit))
    return ok


def _diagnosed(r, th):
    """该批次是否真的产出了四个 section（core 未走 min_candidates 短路分支）。"""
    nc = r.get("n_cand")
    return nc is not None and nc >= th["min_candidates"]


def discrim(rate, side):
    """按「指标恶化方向」解释触发率。

    side="high_bad"：指标越大越糟（top1 占比、HHI、滞后天数、max corr…）。
        触发率高 = 阈值偏松、几乎人人命中，鉴别力不足。
    side="low_bad" ：指标越小越糟（覆盖率下限）。触发率高 = **阈值偏严**，
        把多数正常批次都判成异常，噪音远大于信号。

    早期版本对两种方向用同一套文案，会把「覆盖率下限 0.50 触发 5/6」
    说成「偏松、接近普遍命中」——语义正好反了：覆盖率低才是问题，
    触发多说明这条线卡得太狠。
    """
    if rate is None:
        return "无法判定（该指标无有效样本）"
    if rate <= 0.0:
        return ("从未触发（阈值偏严，或样本本就健康）" if side == "high_bad"
                else "从未触发（阈值偏松，几乎无 batches 低于下限）")
    if rate < 0.2:
        return "低触发（有鉴别力，偏严可能漏报）" if side == "high_bad" \
            else "低触发（阈值合理，仅少数批次低于下限）"
    if rate <= 0.7:
        return "有鉴别力"
    return ("高触发（偏松，接近普遍命中，鉴别力不足）" if side == "high_bad"
            else "高触发（偏严，把多数正常批次误判为异常，需放宽下限）")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--kinds", default="v13,g2,ens")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args(argv)
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]

    th = dict(core.THRESHOLDS)
    print("=" * 78)
    print("corr_diag 阈值人工审核（只读；阈值不改，仅产出证据）")
    print("=" * 78)
    print("阈值: %s" % ", ".join("%s=%s" % (k, th[k])
                                 for k in sorted(th)))
    found = discover(kinds, args.limit)
    if not found:
        print("[FATAL] 未发现任何历史批次 CSV")
        return 2
    pre = presignals(found)
    print("批次发现: %d 个（%s）"
          % (len(pre), ", ".join("%s=%d" % (k, len(
              [1 for x in pre if x[0] == k])) for k in kinds)))

    rows = []
    npass = [0]
    nfail = [0]
    notes = []
    last_asof = object()
    for kind, bd, fp, sg in pre:
        if sg != last_asof:
            warm_cache([1], sg)
            last_asof = sg
        try:
            row = collect(kind, bd, os.path.basename(fp), bd, th)
        except Exception as exc:  # noqa: BLE001
            nfail[0] += 1
            notes.append("%s/%s 采集异常: %s: %s"
                         % (kind, bd, type(exc).__name__, exc))
            continue
        # 只有真正产出四个 section 的批次才可核对：短路批次 core 侧全为 None，
        # 与复算侧比较会产出「一侧为空」的假 FAIL。
        if _diagnosed(row, th):
            cross_check(row, npass, nfail, notes)
        rows.append(row)
        print("  [%s] %-4s %s cand=%-3s overall=%-4s "
              "ind=%s sty=%s idx=%s cor=%s"
              % (kind, kind, bd, row["n_cand"], row["overall"],
                 row["st_ind"], row["st_sty"], row["st_idx"], row["st_cor"]))

    if not os.path.isdir(args.out):
        os.makedirs(args.out)
    import csv as _csv
    cols = ["kind", "batch", "signal_date", "market_last_date", "n_cand",
            "io_status", "overall", "st_ind", "st_sty", "st_idx", "st_cor",
            "ind_top1_ratio", "ind_hhi", "ind_miss_ratio",
            "ind_n_industries", "sty_coverage", "sty_n_buckets",
            "sty_top1_ratio", "sty_stale_days", "sty_members_date",
            "sty_n_loaded", "idx_top1_ratio", "idx_n_indexes",
            "cor_n_passed", "cor_n_pairs", "cor_max", "cor_mean",
            "cor_n_high_pairs", "cor_largest_cluster"]
    for kind in kinds:
        sub = [r for r in rows if r["kind"] == kind]
        if not sub:
            continue
        fp = os.path.join(args.out, "threshold_audit_%s.csv" % kind)
        with io.open(fp, "w", newline="", encoding="utf-8-sig") as fh:
            wr = _csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            wr.writeheader()
            for r in sub:
                wr.writerow(r)
        print("[CSV] %s (%d 行)" % (fp, len(sub)))

    # 候选数 < min_candidates 时 core.diagnose 走短路分支：只写 sections["_skip"]，
    # **不产出** industry/style/index/correlation 四个 section，因此这些批次的
    # st_ind 是 None（不是 "SKIP"）。判据必须按「候选数是否达到门槛」来写，
    # 用 status=="SKIP" 去过滤会一个都剔不掉。
    diag = [r for r in rows if _diagnosed(r, th)]
    skipped = [r for r in rows if not _diagnosed(r, th)]
    print("-" * 78)
    print("指标分布（参与诊断 %d 个批次；候选数 < %d 的 %d 个批次已剔除，"
          "其四个 section 未产出）"
          % (len(diag), th["min_candidates"], len(skipped)))
    describe("ind_top1_ratio", [r["ind_top1_ratio"] for r in diag])
    describe("ind_hhi", [r["ind_hhi"] for r in diag])
    describe("ind_miss_ratio", [r["ind_miss_ratio"] for r in diag])
    describe("sty_coverage", [r["sty_coverage"] for r in diag])
    describe("sty_n_buckets", [r["sty_n_buckets"] for r in diag])
    describe("sty_stale_days", [r["sty_stale_days"] for r in diag],
             unit="(天)")
    describe("idx_top1_ratio", [r["idx_top1_ratio"] for r in diag])
    describe("cor_max", [r["cor_max"] for r in diag])
    describe("cor_mean", [r["cor_mean"] for r in diag])
    describe("cor_n_high_pairs", [r["cor_n_high_pairs"] for r in diag],
             unit="(对)")
    describe("cor_largest_cluster", [r["cor_largest_cluster"] for r in diag])

    checks = [
        ("industry_top1_ratio", "ind_top1_ratio", ">",
         th["industry_top1_ratio"], "high_bad"),
        ("industry_hhi", "ind_hhi", ">", th["industry_hhi"], "high_bad"),
        ("industry_miss_ratio", "ind_miss_ratio", ">",
         th["industry_miss_ratio"], "high_bad"),
        ("style_min_coverage", "sty_coverage", "<", th["style_min_coverage"],
         "low_bad"),
        ("style_member_max_stale_days", "sty_stale_days", ">",
         th["style_member_max_stale_days"], "high_bad"),
        ("style_top1_ratio", "sty_top1_ratio", ">", th["style_top1_ratio"],
         "high_bad"),
        ("index_top1_ratio", "idx_top1_ratio", ">", th["index_top1_ratio"],
         "high_bad"),
        ("corr_pair", "cor_max", ">=", th["corr_pair"], "high_bad"),
        ("corr_cluster_min", "cor_largest_cluster", ">=",
         th["corr_cluster_min"], "high_bad"),
    ]
    print("-" * 78)
    print("阈值触发率（分子=触发批次数，分母=%d）" % len(diag))
    for name, key, op, limit, side in checks:
        hit = 0
        tot = 0
        for r in diag:
            v = r.get(key)
            if v is None:
                continue
            tot += 1
            ok = (v > limit) if op == ">" else (
                (v < limit) if op == "<" else (v >= limit))
            if ok:
                hit += 1
        rate = (hit / float(tot)) if tot else None
        print("  %-30s %-4s %-9s 触发 %-3d/%-3d = %-7s %s"
              % (name, op, _fmt(limit), hit, tot,
                 ("%.1f%%" % (rate * 100)) if rate is not None else "NA",
                 discrim(rate, side)))
    print("  %-30s %-4s %-9s 跳过 %-3d/%-3d（候选数 < %d 时 core 走短路分支，"
          "四 section 不产出）"
          % ("min_candidates", "<", th["min_candidates"], len(skipped),
             len(rows), th["min_candidates"]))

    print("-" * 78)
    print("独立复算一致性: PASS=%d FAIL=%d" % (npass[0], nfail[0]))
    for nt in notes[:20]:
        print("  ! %s" % nt)
    if len(notes) > 20:
        print("  ! ... 其余 %d 条" % (len(notes) - 20))
    print("=" * 78)
    print("[THRESHOLD-AUDIT-DONE] 阈值定稿为人工决策，本脚本不改任何阈值")
    return 0 if nfail[0] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())