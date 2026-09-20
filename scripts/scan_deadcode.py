# coding: utf-8
"""死代码静态扫描（T-20260919-002 三道防线·A 静态扫描，2026-09-20 立）。

检测两类高危险死代码模式（历史事故教训）：
  P1 建列后判：先 `for c in [...]: if c not in df.columns: df[c] = np.nan` 无条件建列，
              后置 `if "x" not in df.columns and "y" in df.columns` 判断永远为假
              —— 2026-08-17~09-18 F2/F5 死代码事故（T-20260919-002）：F2 恒新浪兜底、F5 恒 5.0，
              实盘 39 天评分卡坍缩无人发现。
  P2 passorder 返回值当订单号：`order_id = passorder(...)` 后 `if order_id:` 真值判断永假
              （passorder 异步返回 0/None，正确写法 ret == 0 判 SUBMITTED，见 broker/qmt_order.py），
              会导致买入分支永不执行 → 持仓账本漏记 → 下一 bar 重复买入。

用法：
  python scan_deadcode.py                  # 扫描默认路径（P16 实盘链路 + broker + data/schedules + versions 存档）
  python scan_deadcode.py --paths a.py b/  # 指定文件或目录
  python scan_deadcode.py --out report.md  # 同时落报告

退出码：0=无命中；1=有命中（P1/P2 任一）。

接入点：
  1. build.py 构建前置（每次发版必扫）
  2. 独立周扫定时任务（建议每周六 09:00，非交易日零并发，覆盖 versions/ 存档与 broker 模板）
"""
import argparse
import ast
import os
import re
import sys

# ---- 默认扫描路径：P16 实盘链路 + broker + data/schedules + versions 存档 ----
P16 = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
DEFAULT_PATHS = [
    P16,  # deploy_predict_g2 / deploy_predict / scorecard_real / build_g2_daily /
          # rebalance_g2 / rebalance_ens / rebalance_daily / g2_realtime /
          # position_filter / merge_live_features / paper_forward* / refresh_panel_v3
    r"D:\QuantLab\broker\qmt_builder.py",
    r"D:\QuantLab\broker\qmt_order.py",
    r"D:\QuantLab\data\schedules",
    os.path.join(P16, "versions"),  # 存档副本（回滚/复制即复活的死代码）
]

# 参与 P1 列名匹配的关键列（命中即高可疑；全列名通用匹配见代码逻辑）
# 仅用于输出说明文案，匹配逻辑对所有动态列名生效


def walk_py_files(paths):
    """展开 paths 中所有 .py 文件（目录递归，跳过 __pycache__ / .venv / venv / build）。"""
    out = []
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isfile(p):
            if p.endswith(".py"):
                out.append(p)
        elif os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in ("__pycache__", ".venv", "venv", "build", "node_modules")]
                for f in files:
                    if f.endswith(".py"):
                        out.append(os.path.join(root, f))
    return sorted(set(out))


def _is_nan_call(node):
    """value 是否为 np.nan / np.nan() / float('nan') 之类 NaN 写入。"""
    # np.nan 是 Attribute（最常见写法）
    if isinstance(node, ast.Attribute) and node.attr == "nan":
        return True
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id == "nan":
            return True
        if isinstance(fn, ast.Attribute) and fn.attr == "nan":
            return True
        if isinstance(fn, ast.Name) and fn.id == "float" and node.args:
            a0 = node.args[0]
            if isinstance(a0, ast.Str) and a0.s == "nan":
                return True
    return False


def _col_name(node):
    """取 Subscript 的 slice 名：df[c] 的 c（Name 或常量字符串）。"""
    if isinstance(node, ast.Subscript):
        sl = node.slice
        if isinstance(sl, ast.Name):
            return sl.id
        if isinstance(sl, ast.Str):
            return sl.s
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return sl.value
    return None


def find_p1_hits(tree, src):
    """P1 建列后判：先 `for c in [cols]: if c not in df.columns: df[c]=np.nan` 无条件建列，
    后置 `if "x" not in df.columns and ...` 判断（后者永假）。

    命中条件：
      fill:  For 循环（target=Name 循环变量 v，iter=List[str]）内 `df[v]=np.nan` 建列
      check: If 条件含 Compare("x", NotIn, <expr>.columns)
      fill 与 check 必须在同一父块（body/orelse），且 fill 先于 check，列名 ∈ 建列循环列表
    """
    parents = {}
    for pnode in ast.walk(tree):
        for child in ast.iter_child_nodes(pnode):
            parents[child] = pnode

    def _blocks(n):
        b = []
        for _attr in ("body", "orelse", "finalbody"):
            if hasattr(n, _attr):
                b.extend(getattr(n, _attr))
        return b

    fills = []   # (for_node, loop_var, col_names)
    checks = []  # (if_node, col_name)
    for node in ast.walk(tree):
        # 建列循环：for v in ["a","b",...]: if v not in df.columns: df[v] = np.nan
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            v = node.target.id
            if isinstance(node.iter, ast.List):
                col_names = []
                for el in node.iter.elts:
                    if isinstance(el, ast.Str):
                        col_names.append(el.s)
                    elif isinstance(el, ast.Constant) and isinstance(el.value, str):
                        col_names.append(el.value)
                if not col_names:
                    continue
                # body 里存在 df[v] = np.nan（v 为循环变量）
                for st in ast.walk(node):
                    if (isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Subscript)
                            and _col_name(st.targets[0]) == v and _is_nan_call(st.value)):
                        fills.append((node, v, col_names))
                        break
        # 后判：if "x" not in ...columns ...
        if isinstance(node, ast.If):
            conds = []
            test = node.test
            if isinstance(test, ast.BoolOp):
                conds = test.values
            else:
                conds = [test]
            for cd in conds:
                if isinstance(cd, ast.Compare):
                    for op, rgt in zip(cd.ops, cd.comparators):
                        if isinstance(op, ast.NotIn):
                            left = cd.left
                            c = None
                            if isinstance(left, ast.Str):
                                c = left.s
                            elif isinstance(left, ast.Constant) and isinstance(left.value, str):
                                c = left.value
                            if c and isinstance(rgt, ast.Attribute) and rgt.attr == "columns":
                                checks.append((node, c))
    hits = []
    for (fn, fv, fcols), (cn, cc) in [(f, c) for f in fills for c in checks]:
        if cc not in fcols:
            continue
        fp, cp = parents.get(fn), parents.get(cn)
        if fp is None or fp is not cp:
            continue
        blk = _blocks(fp)
        try:
            fi, ci = blk.index(fn), blk.index(cn)
        except ValueError:
            continue
        if fi < ci:
            hits.append((fn.lineno, cc, fn.col_offset, cn.lineno))
    return hits


def find_p2_hits(tree, src):
    """P2 passorder 返回值当订单号：order_id = passorder(...) 后 if order_id:（真值判断永假）。

    覆盖：
      a) 赋值后 `if <var>:`（Compare 如 `ret == 0` 是正确的，不命中）
      b) 直接 `if passorder(...):`
    """
    assigns = {}  # var_name -> (lineno, col_offset, line_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            t = node.targets[0]
            v = node.value
            if isinstance(t, ast.Name) and isinstance(v, ast.Call):
                fn = v.func
                is_po = (isinstance(fn, ast.Name) and fn.id == "passorder") or \
                        (isinstance(fn, ast.Attribute) and fn.attr == "passorder")
                if is_po:
                    assigns[t.id] = (node.lineno, node.col_offset, ast.get_source_segment(src, node) or "")
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        # b) if passorder(...):
        if isinstance(test, ast.Call):
            fn = test.func
            is_po = (isinstance(fn, ast.Name) and fn.id == "passorder") or \
                    (isinstance(fn, ast.Attribute) and fn.attr == "passorder")
            if is_po:
                hits.append((node.lineno, "passorder", node.col_offset,
                             ast.get_source_segment(src, node) or ""))
            continue
        # a) if <var>:
        if isinstance(test, ast.Name) and test.id in assigns:
            al, ao, _ = assigns[test.id]
            if al < node.lineno and ao == node.col_offset:
                hits.append((node.lineno, test.id, node.col_offset,
                             ast.get_source_segment(src, node) or ""))
    return hits


def find_p2_text_hits(src):
    """P2 文本层兜底：AST 看不到模板字符串（qmt_builder 的 mfic 模板 passorder 是三重引号字符串）。

    模式：
      `order_id = passorder(...)` 后同缩进 `if order_id:`（真值判断永假，passorder 异步返回 0/None）
      `if passorder(...):` 直接真值判断
    """
    hits = []
    lines = src.splitlines()
    assigns = {}  # 缩进长度 -> [(lineno, var)]
    for i, ln in enumerate(lines, 1):
        m = re.match(r"^(\s*)(\w+)\s*=\s*passorder\s*\(", ln)
        if m:
            assigns.setdefault(len(m.group(1)), []).append((i, m.group(2)))
    for i, ln in enumerate(lines, 1):
        m = re.match(r"^(\s*)if\s+(\w+)\s*:\s*$", ln)
        if m:
            ind = len(m.group(1))
            var = m.group(2)
            for (al, av) in assigns.get(ind, []):
                if av == var and al < i:
                    hits.append((i, var, 0, ln.strip()))
        if re.match(r"^(\s*)if\s+passorder\s*\(", ln):
            hits.append((i, "passorder", 0, ln.strip()))
    return hits


def scan_file(path):
    """扫描单个文件，返回 (p1_hits, p2_hits)。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
        tree = ast.parse(src)
    except SyntaxError as e:
        return [("SYNTAX", e.lineno or 0, 0, "解析失败: %s" % e)], []
    lines = src.splitlines()
    p1 = []
    for (fl, cc, fo, cl) in find_p1_hits(tree, src):
        _t = lines[cl - 1].strip() if 0 < cl <= len(lines) else ""
        p1.append((fl, cc, fo, cl, _t))
    # P2 = AST 层 + 文本层（模板字符串里的 passorder 真值判断）兜底，按行号去重
    p2 = find_p2_hits(tree, src)
    _p2_ln = set(h[0] for h in p2)
    for (ln, col, off, seg) in find_p2_text_hits(src):
        if ln not in _p2_ln:
            p2.append((ln, col, off, seg))
            _p2_ln.add(ln)
    return p1, p2


def main():
    ap = argparse.ArgumentParser(description="死代码静态扫描（P1 建列后判 / P2 passorder 真值判断）")
    ap.add_argument("--paths", nargs="*", default=DEFAULT_PATHS, help="文件或目录，默认扫 P16 链路+broker+data/schedules+versions")
    ap.add_argument("--out", default=None, help="同时写报告 md 路径")
    args = ap.parse_args()

    files = walk_py_files(args.paths)
    total_p1 = []
    total_p2 = []
    for fp in files:
        p1, p2 = scan_file(fp)
        rel = fp
        for base in (r"D:\QuantLab", P16):
            if fp.startswith(base):
                rel = os.path.relpath(fp, base)
                break
        for (ln, col, off, cl, src) in p1:
            total_p1.append((rel, ln, col, off, cl, src))
        for (ln, col, off, src) in p2:
            total_p2.append((rel, ln, col, off, src))

    print("=" * 70)
    print("死代码静态扫描（T-20260919-002 防线A） · 扫描文件 %d 个" % len(files))
    print("=" * 70)
    lines = ["# 死代码静态扫描报告（T-20260919-002 防线A）", "",
             "- 扫描日期：%s" % __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
             "- 扫描文件数：%d" % len(files), ""]
    if not total_p1 and not total_p2:
        lines.append("## 结果：无命中（PASS）")
        print("结果：无命中（PASS）")
    if total_p1:
        lines.append("## P1 建列后判（高）：先 df[c]=np.nan 建列，后 if 判 not in columns（后者永假）")
        print("## P1 建列后判（高）")
        for rel, ln, col, off, cl, src in total_p1:
            msg = "  %s:%d  [P1] 建列后判，列 %r（建列 L%d，判 L%d）：\n        %s" % (rel, ln, col, ln, cl, (src or "").replace("\n", " ")[:160])
            lines.append("- `%s:%d` 列 `%s`（建列 L%d，判断 L%d）\n  ```\n  %s\n  ```" % (rel, ln, col, ln, cl, src))
            print(msg)
    if total_p2:
        lines.append("## P2 passorder 返回值当订单号（高）：真值判断永假")
        print("## P2 passorder 返回值当订单号（高）")
        for rel, ln, col, off, src in total_p2:
            msg = "  %s:%d  [P2] passorder 返回值当订单号，变量 %r：\n        %s" % (rel, ln, col, (src or "").replace("\n", " ")[:160])
            lines.append("- `%s:%d` 变量 `%s`\n  ```\n  %s\n  ```" % (rel, ln, col, src))
            print(msg)
    print("=" * 70)

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("报告已写: %s" % args.out)

    sys.exit(1 if (total_p1 or total_p2) else 0)


if __name__ == "__main__":
    main()
