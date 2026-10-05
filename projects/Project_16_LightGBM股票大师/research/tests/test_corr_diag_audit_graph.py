# -*- coding: utf-8 -*-
"""corr_diag 阈值审计脚本 · 独立连通分量实现专项测试。

背景
----
审计脚本 audit_corr_diag_thresholds_20261004.py 的第 17 项交叉核对
``cor_largest_cluster``，刻意**不复制** Core 的并查集实现，改用
「邻接表 + 迭代 DFS 连通分量」——同算法自证等于没验证。

锁的语义
--------
  A. 边界：无节点 / 无边 / 孤立点 / 单边 / 自环 / 边含未知代码
  B. 结构：链式连通、环、多簇取最大、重复边不重复计数、size>=2 过滤
  C. 交叉互证：随机图上与 core._union_find_clusters 结果一致（本测试的
     核心价值——两边算法路径不同，结果必须相同）
  D. 键名契约：recalc_corr 返回 ``cor_largest_cluster``，不是
     ``cor_n_clusters``（旧键名会把「最大簇规模」误读成「簇数量」）

与 test_corr_diag.py（核心）/ _io.py / _report.py 互补。
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.abspath(os.path.join(HERE, ".."))
if RESEARCH not in sys.path:
    sys.path.insert(0, RESEARCH)

import audit_corr_diag_thresholds_20261004 as audit  # noqa: E402
import corr_diag_core as core  # noqa: E402

LC = audit._largest_component

PASS = [0]
FAIL = []
SKIPPED = []


def check(name, got, want):
    if got == want:
        PASS[0] += 1
    else:
        FAIL.append("%s got=%r want=%r" % (name, got, want))
        print("[FAIL] %s got=%r want=%r" % (name, got, want))


def pair(a, b, r=0.9):
    return (a, b, r)


# ---------------------------------------------------------------------------
# A. 边界
# ---------------------------------------------------------------------------
check("A1_empty_codes", LC([], []), 0)
check("A2_no_edges", LC(["a", "b", "c"], []), 0)
check("A3_single_isolated", LC(["a"], []), 0)
check("A4_single_node_with_selfloop", LC(["a"], [pair("a", "a")]), 0)
check("A5_one_edge_two_nodes", LC(["a", "b"], [pair("a", "b")]), 2)
check("A6_edge_with_unknown_code", LC(["a"], [pair("a", "zz")]), 0)
check("A7_isolated_plus_pair", LC(["a", "b", "c"], [pair("a", "b")]), 2)

# ---------------------------------------------------------------------------
# B. 结构
# ---------------------------------------------------------------------------
check("B1_chain_three", LC(["a", "b", "c"], [pair("a", "b"), pair("b", "c")]), 3)
check("B2_reverse_edge_direction",
      LC(["a", "b", "c"], [pair("c", "b"), pair("b", "a")]), 3)
check("B3_cycle_four",
      LC(["a", "b", "c", "d"],
         [pair("a", "b"), pair("b", "c"), pair("c", "d"), pair("d", "a")]), 4)
check("B4_star_five",
      LC(["a", "b", "c", "d", "e"],
         [pair("a", "b"), pair("a", "c"), pair("a", "d"), pair("a", "e")]), 5)
check("B5_dup_edges_no_double_count",
      LC(["a", "b", "c"], [pair("a", "b"), pair("a", "b"), pair("a", "b")]), 2)
check("B6_two_clusters_take_max",
      LC(["a", "b", "c", "d"],
         [pair("a", "b"), pair("c", "d")]), 2)
check("B7_big_plus_small",
      LC(["a", "b", "c", "d", "e"],
         [pair("a", "b"), pair("b", "c"), pair("d", "e")]), 3)
check("B8_bridge_merges_clusters",
      LC(["a", "b", "c", "d"],
         [pair("a", "b"), pair("c", "d"), pair("b", "c")]), 4)
check("B9_disconnected_singletons_only",
      LC(["a", "b", "c", "d"], []), 0)
check("B10_all_connected_seven",
      LC(list("abcdefg"),
         [pair("a", "b"), pair("b", "c"), pair("c", "d"),
          pair("e", "f"), pair("f", "g"), pair("d", "e")]), 7)

# ---------------------------------------------------------------------------
# C. 交叉互证：随机图 vs core 并查集
# ---------------------------------------------------------------------------
rnd = random.Random(20261004)
mismatch = 0
n_graph = 0
for _ in range(400):
    n = rnd.randint(0, 12)
    codes = ["s%02d" % i for i in range(n)]
    p = rnd.choice([0.0, 0.15, 0.35, 0.7, 1.0])
    seen = set()
    edges = []
    for i in range(n):
        for j in range(i + 1, n):
            if rnd.random() < p:
                a, b = codes[i], codes[j]
                if rnd.random() < 0.5:
                    a, b = b, a
                key = (a, b)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(pair(a, b))
    mine = LC(codes, edges)
    core_pairs = [{"a": a, "b": b} for a, b, _r in edges]
    clusters = core._union_find_clusters(codes, core_pairs)
    theirs = max([len(c) for c in clusters]) if clusters else 0
    n_graph += 1
    if mine != theirs:
        mismatch += 1
        if mismatch <= 3:
            print("[FAIL] graph codes=%r mine=%d core=%d" % (codes, mine, theirs))

check("C1_random_graph_400_vs_core", mismatch, 0)
check("C2_random_graph_count", n_graph, 400)

# 显式断言若干随机样本确实产生了非平凡簇（防止随机图全是孤立点导致
# 「两边都返回 0」的假通过）
rnd2 = random.Random(777)
nontrivial = 0
for _ in range(200):
    codes = ["t%02d" % i for i in range(8)]
    edges = []
    for i in range(8):
        for j in range(i + 1, 8):
            if rnd2.random() < 0.45:
                edges.append(pair(codes[i], codes[j]))
    if LC(codes, edges) >= 3:
        nontrivial += 1
check("C3_random_graphs_with_cluster_ge3", nontrivial > 100, True)

# ---------------------------------------------------------------------------
# D. 键名契约
# ---------------------------------------------------------------------------
# 行为级契约：用一组确定性数据真跑 recalc_corr，检查返回键名与取值。
# （不用 co_consts/co_names 断言——dict 字面量键的存放形式是实现细节。）
TH = {"corr_min_obs": 3, "corr_max_zero_ratio": 0.5,
      "corr_min_std": 1e-9, "corr_pair": 0.70}
_a = [1.0, 2.0, 3.0, 4.0, 5.0]
_b = [2.0, 4.0, 6.0, 8.0, 10.0]   # 与 _a 完全线性 -> r=1.0 >= 0.70
_c = [5.0, 1.0, 4.0, 2.0, 3.0]    # 与 _a 近乎无关 -> r=-0.3
_rc = audit.recalc_corr({"a": _a, "b": _b, "c": _c}, ["a", "b", "c"], TH)
check("D1_recalc_corr_has_new_key", "cor_largest_cluster" in _rc, True)
check("D2_recalc_corr_no_old_key", "cor_n_clusters" in _rc, False)
check("D3_recalc_corr_largest_cluster_value", _rc["cor_largest_cluster"], 2)
check("D4_recalc_corr_n_high_pairs", _rc["cor_n_high_pairs"], 1)
check("D5_recalc_corr_n_passed", _rc["cor_n_passed"], 3)

# 源码层面禁止旧键名残留（防止下游又读回 cor_n_clusters）
src_path = os.path.join(RESEARCH, "audit_corr_diag_thresholds_20261004.py")
with open(src_path, "r", encoding="utf-8") as fh:
    src_text = fh.read()
check("D6_no_old_key_in_source", "cor_n_clusters" in src_text, False)
check("D7_new_key_in_source", "cor_largest_cluster" in src_text, True)
check("D8_docstring_not_claim_bfs",
      "邻接表 + BFS 连通分量" in src_text, False)

print("=" * 74)
print("PASS=%d FAIL=%d SKIP=%d" % (PASS[0], len(FAIL), len(SKIPPED)))
if FAIL:
    print("[FAILED LIST]")
    for f in FAIL:
        print("  - %s" % f)
print("[CORR-DIAG-AUDIT-GRAPH-TEST-DONE]")
sys.exit(0 if not FAIL else 1)