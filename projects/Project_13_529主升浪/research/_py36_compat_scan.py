import ast
import io
import re
import sys
import tokenize

P = r"D:\QuantLab\projects\Project_13_529主升浪\build\strategy_huang529.py"

src = io.open(P, "rb").read().decode("gbk")
lines = src.split("\n")

fstrings = []
walrus = []
matchcase = []
annots = []
for n, line in enumerate(lines, 1):
    if re.search(r"(?<![A-Za-z0-9_])[fF][rR]?[\"']", line):
        if not line.lstrip().startswith("#"):
            fstrings.append((n, line.strip()[:110]))
    if re.search(r":=", line) and not line.lstrip().startswith("#"):
        walrus.append((n, line.strip()[:110]))
    if re.search(r"^\s*match\s+.*:\s*$", line) and not line.lstrip().startswith("#"):
        matchcase.append((n, line.strip()[:110]))
    if re.search(r"->\s*(list|dict|set|tuple|str|int|float|bool)\[", line):
        annots.append((n, line.strip()[:110]))
    if re.search(r":\s*(list|dict|set|tuple)\[[A-Za-z]", line) and not line.lstrip().startswith("#"):
        annots.append((n, line.strip()[:110]))

token_fstr = []
for tok in tokenize.generate_tokens(io.StringIO(src).readline):
    if tok.type == tokenize.STRING and "f" in tok.string[:2].lower():
        if "{" in tok.string or "}" in tok.string:
            token_fstr.append((tok.start[0], tok.string[:60]))

try:
    ast.parse(src, feature_version=(3, 6))
    ast_ok = True
    ast_err = ""
except SyntaxError as e:
    ast_ok = False
    ast_err = "%s line %s" % (e.msg, e.lineno)

print("ast_feature_version_3_6 =", ast_ok, ast_err)
print("fstring_regex_hits =", len(fstrings))
for x in fstrings:
    print("   ", x)
print("fstring_token_hits =", len(token_fstr))
for x in token_fstr:
    print("   ", x)
print("walrus_hits =", len(walrus), walrus)
print("matchcase_hits =", len(matchcase), matchcase)
print("builtin_generic_annot_hits =", len(annots), annots)
print("MOCK_residue =", len(re.findall(r"MOCK|MockC|class Mock", src)))
print("_TEST_MODE_residue =", src.count("_TEST_MODE"))
# passorder(24,1101,...) 是「限价卖出」合法调用，不是撤单。
# 真正要拦的是拿 passorder 干撤单：QMT 撤单只能走 can_cancel_order + cancel()。
sell_calls = []
for n, line in enumerate(lines, 1):
    s = line.strip()
    if s.startswith("#"):
        continue
    m = re.search(r"passorder\(\s*(\d+)\s*,\s*(\d+)", s)
    if m:
        sell_calls.append((n, m.group(1), m.group(2), s[:100]))
print("passorder_live_calls =", sell_calls)
print("cancel_entry_ok =", ("can_cancel_order" in src) and ("cancel(oid, ACCOUNT_ID" in src))
bare_cancel = [n for n, line in enumerate(lines, 1)
               if not line.strip().startswith("#")
               and re.search(r"(?<![_\w.])cancel\(\s*(?!oid\b)", line)
               and "can_cancel_order" not in line]
print("cancel_bare_call_sites =", bare_cancel)

# ---- 作用域校验：_log(msg, C) 里的 C 必须在本函数可解析 ----
# 2026-09-27 实测 P0：_calibrate_filled 传了作用域里不存在的 C，
# 单测跑到「deal 领先快照」分支才炸 NameError，实盘会崩在成交校准路径。
# 这里用 AST 逐函数收集「参数名 + 局部赋值 + 模块全局」作为可见集合。
tree = ast.parse(src)
module_globals = set()
for n in tree.body:
    if isinstance(n, ast.Assign):
        for t in n.targets:
            if isinstance(t, ast.Name):
                module_globals.add(t.id)
    elif isinstance(n, (ast.FunctionDef, ast.ClassDef)):
        module_globals.add(n.name)
    elif isinstance(n, (ast.Import, ast.ImportFrom)):
        for a in n.names:
            module_globals.add((a.asname or a.name).split(".")[0])

bad_log_ctx = []
for fn in ast.walk(tree):
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    bound = set(a.arg for a in fn.args.args)
    if fn.args.vararg:
        bound.add(fn.args.vararg)
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            bound.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
        elif isinstance(n, (ast.comprehension,)):
            pass
    for n in ast.walk(fn):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "_log" and len(n.args) >= 2
                and isinstance(n.args[1], ast.Name)):
            if n.args[1].id not in bound and n.args[1].id not in module_globals:
                bad_log_ctx.append((fn.name, n.lineno, n.args[1].id))
print("undefined_log_ctx =", bad_log_ctx)
