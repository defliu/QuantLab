# -*- coding: utf-8 -*-
"""DE independent audit script (read-only): Q1 src/build consistency + Q5 Py3.6 AST checks.
Task: T-20261003-001. Writes nothing; prints results only.
"""
import ast
import difflib
import io
import re
import sys

BASE = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"

PAIRS = [
    (r"strategy\strategy_p16_g2_selfcontained_src.py", r"build\strategy_p16_g2_selfcontained.py", "g2"),
    (r"strategy\strategy_p16_v13_risk_src.py", r"build\strategy_p16_v13_risk.py", "v13"),
    (r"strategy\strategy_p16_ens_selfcontained_src.py", r"build\strategy_p16_ens_selfcontained.py", "ens"),
]

BUILD_TAG_RE = re.compile(r"^BUILD_TAG\s*=\s*[\"']([0-9]{8}-[0-9]{6})[\"']")


def read_lines(path, enc):
    with io.open(path, "r", encoding=enc) as f:
        return f.read().splitlines()


def norm_line(line):
    if line.startswith("# coding="):
        return "# coding=<ENC>"
    m = BUILD_TAG_RE.match(line.strip())
    if m:
        return "BUILD_TAG = <TAG>"
    return line


def q1():
    print("=" * 72)
    print("Q1 src/build full-decode line comparison")
    print("=" * 72)
    for src_rel, bld_rel, tag in PAIRS:
        src = read_lines(BASE + "\\" + src_rel, "utf-8")
        bld = read_lines(BASE + "\\" + bld_rel, "gbk")
        raw_diff = []
        n = max(len(src), len(bld))
        for i in range(n):
            s = src[i] if i < len(src) else "<EOF>"
            b = bld[i] if i < len(bld) else "<EOF>"
            if s != b:
                raw_diff.append(i + 1)
        # show the actual differing lines
        print("[%s] src_lines=%d build_lines=%d len_equal=%s full_decode_diff=%d diff_lines=%s"
              % (tag, len(src), len(bld), len(src) == len(bld), len(raw_diff), raw_diff))
        for ln in raw_diff:
            print("    L%d src : %r" % (ln, src[ln - 1] if ln <= len(src) else "<EOF>"))
            print("    L%d bld : %r" % (ln, bld[ln - 1] if ln <= len(bld) else "<EOF>"))
        # normalized
        ns = [norm_line(x) for x in src]
        nb = [norm_line(x) for x in bld]
        norm_diff = [i + 1 for i in range(max(len(ns), len(nb)))
                     if (ns[i] if i < len(ns) else "<EOF>") != (nb[i] if i < len(nb) else "<EOF>")]
        sm = difflib.SequenceMatcher(None, ns, nb)
        blocks = [b for b in sm.get_opcodes() if b[0] != "equal"]
        print("    normalized_diff_lines=%d  seq_ratio=%.6f  non_equal_blocks=%d"
              % (len(norm_diff), sm.ratio(), len(blocks)))
        # also detect extra BUILD_TAG-like lines beyond the declared one (3rd diff guard)
        tag_lines_src = [i + 1 for i, x in enumerate(src) if BUILD_TAG_RE.match(x.strip())]
        tag_lines_bld = [i + 1 for i, x in enumerate(bld) if BUILD_TAG_RE.match(x.strip())]
        print("    BUILD_TAG line idx: src=%s bld=%s" % (tag_lines_src, tag_lines_bld))


def _ann_nodes(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for a in node.args.args + node.args.kwonlyargs:
                if a.annotation is not None:
                    yield a.annotation
            if node.args.vararg and node.args.vararg.annotation is not None:
                yield node.args.vararg.annotation
            if node.args.kwarg and node.args.kwarg.annotation is not None:
                yield node.args.kwarg.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign):
            if node.annotation is not None:
                yield node.annotation


def q5():
    print("=" * 72)
    print("Q5 Python 3.6.8 AST static checks (6 bridge files)")
    print("=" * 72)
    files = [p for pair in PAIRS for p in pair[:2]]
    for rel in files:
        path = BASE + "\\" + rel
        enc = "gbk" if rel.split("\\")[0] == "build" else "utf-8"
        with io.open(path, "r", encoding=enc) as f:
            text = f.read()
        tree = ast.parse(text)
        fstring = sum(1 for n in ast.walk(tree) if isinstance(n, ast.JoinedStr))
        walrus = sum(1 for n in ast.walk(tree) if isinstance(n, ast.NamedExpr))
        match = sum(1 for n in ast.walk(tree) if hasattr(ast, "Match") and isinstance(n, ast.Match))
        pep604 = 0
        genann_sub = 0
        for ann in _ann_nodes(tree):
            if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
                pep604 += 1
            if (isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name)
                    and ann.value.id in ("dict", "list", "tuple", "set")):
                genann_sub += 1
        ret_ann = sum(1 for n in ast.walk(tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.returns is not None)
        head = text.splitlines()[0] if text.splitlines() else ""
        gbk_ok = head.strip() == "# coding=gbk"
        # textual red-line scans (coarse, reported separately from AST)
        c_passorder = len(re.findall(r"C\s*\.\s*passorder", text))
        dt_now = len(re.findall(r"datetime\s*\.\s*now", text))
        pyarrow = len(re.findall(r"\bpyarrow\b", text))
        parquet = len(re.findall(r"\.parquet\b", text))
        print("%-42s fstr=%d walrus=%d pep604=%d genann_sub=%d match=%d ret_ann=%d head=%r gbk_head=%s C.passorder=%d datetime.now=%d pyarrow=%d parquet=%d"
              % (rel, fstring, walrus, pep604, genann_sub, match, ret_ann, head, gbk_ok,
                 c_passorder, dt_now, pyarrow, parquet))


if __name__ == "__main__":
    q1()
    print()
    q5()
    print()
    print("DONE")
