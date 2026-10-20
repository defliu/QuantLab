# coding: utf-8
"""Project_16 g2 大QMT 文件桥 · 构建脚本。

读 strategy/strategy_p16_g2_bridge_src.py (UTF-8 源码)，
替换 BUILD_TAG 为当前时间戳，GBK 编码写出到 build/strategy_p16_g2_bridge.py，
并做 Python 3.6 语法检查（QMT 内置兼容）。

用法:
  python build_p16_g2.py
产物:
  build/strategy_p16_g2_bridge.py   # coding=gbk, BUILD_TAG=最新时间戳
"""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "strategy", "strategy_p16_g2_bridge_src.py")
OUT = os.path.join(HERE, "build", "strategy_p16_g2_bridge.py")

# ---- vendor 注入（s4-5d G2/ENS 信号内产版）----
# 源码内用行 `# __VENDOR__:<键>` 占位，构建时替换为对应文件全文（外加 BEGIN/END 注释围栏）。
# 扫描门禁作用于【替换后】的最终源，保证 vendor 内容同样受 py36/禁用词/体积约束。
VENDOR_FILES = {
    "model": os.path.join(HERE, "vendor", "g2_tree_model_B21_k1500.py"),
    "features": os.path.join(HERE, "vendor", "g2_features_qmt.py"),
    "snapshot": os.path.join(HERE, "strategy", "_g2_signal_snapshots.py"),
}
VENDOR_SIZE_LIMIT = 550 * 1024   # §九 item5 拍板后体积目标 <550KB


def _inject_vendor(src):
    """把 `# __VENDOR__:<键>` 占位行替换为 vendor 文件全文。
    vendor 文件首行编码声明/编码头剔除（避免单文件内出现第二个 coding 声明与
    重复 shebang 类行），其余原样注入。替换结果行数会变（门禁基于最终源扫描）。"""
    for key, path in VENDOR_FILES.items():
        token = "# __VENDOR__:%s" % key
        if token not in src:
            continue
        if not os.path.exists(path):
            raise SystemExit("[VENDOR-REJECT] 缺 vendor 文件 %s（键 %s）" % (path, key))
        with open(path, "r", encoding="utf-8") as f:
            body = f.read()
        # 剔除首行 coding 声明与模型文件的 # -*- coding 头（单文件只留首行 # coding=gbk）
        lines = body.split("\n")
        while lines and (lines[0].startswith("# coding") or lines[0].startswith("# -*-")):
            lines = lines[1:]
        body = "\n".join(lines).rstrip("\n")
        fence = ("# ======== VENDOR BEGIN: %s ========\n" % key) + \
                body + ("\n# ======== VENDOR END: %s ========" % key)
        n_before = src.count(token)
        src = src.replace(token, fence)
        print("[vendor] 注入 %s (%s) %d bytes，占位 %d 处" % (
            key, os.path.basename(path), len(body.encode("utf-8")), n_before))
    return src

# QMT 生产产物禁用语法（Python 3.6 不兼容）
FORBIDDEN = [
    (r"[:,].*\| *None", "类型联合 str | None"),
    (r"(?<![:\w])dict\[", "dict[...] 泛型"),
    (r"(?<![:\w])list\[", "list[...] 泛型"),
    (r"(?<![:\w])tuple\[", "tuple[...] 泛型"),
    (r":= *", "海象运算符 :="),
    (r"\bmatch\b *\w", "match/case"),
    (r"\bf[\"']", "f-string"),
]
# MOCK / 测试残留（生产版红线：不得混入 mock 或测试代码；T-20261003-003 与 build_p16_v13_risk.py 对齐）
FORBIDDEN_TEXT = [
    ("MOCK", "MOCK 残留"),
    ("mock", "mock 残留"),
    ("pytest", "pytest 残留"),
    ("unittest", "unittest 残留"),
    ("assert ", "assert 残留"),
]


def _is_blob_data_line(line):
    """判定 base64 数据行（vendor 模型 _BLOB 内容）：整行去除缩进后形如
    "xxxx",（引号包裹的纯 base64 字符集）。这类行由 dump 工具生成，是数据不是代码，
    偶发包含 'mock' / f' 等子串属 base64 随机分布，须从禁用词扫描豁免，
    否则每次重训都要手工改数据（2026-10-17 实证 L2074 含 'mock'）。"""
    s = line.strip()
    if len(s) < 10 or not s.startswith('"'):
        return False
    if not (s.endswith('",') or s.endswith('"')):
        return False
    # 剥掉首引号与尾部 `",` / `"` / 逗号（旧版只剥一层导致 base64 残尾 `"`
    # 落进 fullmatch -> 数据行被判非数据行 -> 门禁误报，2026-10-17 实测）
    inner = s.strip('" \t,')
    if len(inner) < 8:
        return False
    return re.fullmatch(r"[A-Za-z0-9+/=]{8,}", inner) is not None


def build():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(SRC, "r", encoding="utf-8") as f:
        src = f.read()

    # 0. vendor 注入（模型/特征/快照）—— 门禁全部作用于注入后的最终源
    src = _inject_vendor(src)

    # 1. 语法检查（py3.6 兼容正则已另有覆盖；此处用编译器查基础语法错误）
    _tmp = OUT + ".syntax_check.tmp"
    with open(_tmp, "w", encoding="utf-8") as f:
        f.write(src)
    try:
        subprocess.run([sys.executable, "-m", "py_compile", _tmp], check=True)
    finally:
        if os.path.exists(_tmp):
            os.remove(_tmp)

    # 1.1 死代码静态扫描前置（T-20260919-002 三道防线·A，2026-09-20）：命中 P1 建列后判 / P2 passorder 真值判断即中止构建
    _scan = subprocess.run([sys.executable, r"D:\QuantLab\scripts\scan_deadcode.py", "--paths", SRC],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if _scan.stdout:
        # GBK 控制台无法编码替换字符时降级打印，不阻断构建
        try:
            print(_scan.stdout.decode("utf-8", "replace")[-2000:])
        except UnicodeEncodeError:
            print(repr(_scan.stdout[-500:]))
    if _scan.returncode != 0:
        raise SystemExit("[DEADCODE-REJECT] 死代码扫描命中（P1建列后判 / P2 passorder真值判断），中止构建")

    # 2. 禁用语法扫描（base64 数据行豁免，见 _is_blob_data_line）
    src_lines = src.split("\n")
    for i, line in enumerate(src_lines, 1):
        if _is_blob_data_line(line):
            continue
        for pat, desc in FORBIDDEN:
            if re.search(pat, line):
                raise SystemExit("[PY36-REJECT] 第%d行 含 %s: %s" % (i, desc, line.strip()))

    # 2.5 MOCK / 测试残留扫描（AGENTS：生产版不得混入 mock 或测试代码；T-20261003-003 与 v13 构建脚本对齐）
    for i, line in enumerate(src_lines, 1):
        if _is_blob_data_line(line):
            continue
        for token, desc in FORBIDDEN_TEXT:
            if token in line:
                raise SystemExit("[MOCK-REJECT] 第%d行 含 %s: %s" % (i, desc, line.strip()))

    # 3. 替换 BUILD_TAG（T-20261003-003 加固：源码赋值必须恰为 1 处；原 count=1 弱校验会静默漏替第二处）
    tag = time.strftime("%Y%m%d-%H%M%S")
    _assign = re.compile(r'BUILD_TAG\s*=\s*"\d{8}-?\d{0,6}"')
    _n_assign = len(_assign.findall(src))
    if _n_assign != 1:
        raise SystemExit("[BUILD_TAG-REJECT] 源码 BUILD_TAG 赋值应恰为 1 处，实测 %d 处" % _n_assign)
    new_src = _assign.sub('BUILD_TAG = "%s"' % tag, src, count=1)
    if len(_assign.findall(new_src)) != 1 or 'BUILD_TAG = "%s"' % tag not in new_src:
        raise SystemExit("[BUILD_TAG-REJECT] BUILD_TAG 替换失败或替换后校验不通过")

    # 4. 首行编码声明统一替换为 gbk（源码可能是 # coding=utf-8）
    new_src = re.sub(r'^# coding[ \t]*=[ \t]*[\w-]+', '# coding=gbk',
                     new_src, count=1, flags=re.MULTILINE)
    if not new_src.lstrip().startswith("# coding=gbk"):
        raise SystemExit("产物首行必须是 # coding=gbk")

    # 4.5 体积门禁（§九 item5 拍板 <550KB）。★ 按【实际落盘字节】计量，
    #   且用 newline="\n" 保持 LF 行尾写出：文本模式默认把 \n 翻译成 \r\n，
    #   每行 +1 字节，2026-10-17 实测开销 7,176 B（会让 549KB 产物虚胖到 556KB
    #   被门禁误杀）。QMT 策略编辑器粘贴与 QMT Python 解析对 LF/CRLF 无差异。
    with open(OUT, "w", encoding="gbk", newline="\n") as f:
        f.write(new_src)
    actual = os.path.getsize(OUT)
    if actual > VENDOR_SIZE_LIMIT:
        os.remove(OUT)
        raise SystemExit("[SIZE-REJECT] 产物 %d bytes > %d bytes (550KB)，已删除产物" % (
            actual, VENDOR_SIZE_LIMIT))
    print("[vendor] 产物实际体积 = %d bytes (%.1f KB) / 上限 %d bytes" % (
        actual, actual / 1024.0, VENDOR_SIZE_LIMIT))

    # 5. GBK 写出（T-20261003-003 加固：写后回读复核，防截断/未刷新/编码异常）
    with open(OUT, "r", encoding="gbk") as f:
        _back = f.read()
    if (not _back.lstrip().startswith("# coding=gbk")
            or 'BUILD_TAG = "%s"' % tag not in _back
            or len(_back.split("\n")) != len(src.split("\n"))):
        raise SystemExit("[BUILD_TAG-REJECT] 产物写后复核失败（首行/BUILD_TAG/行数）")
    size = os.path.getsize(OUT)
    print("生成: %s (%d bytes) BUILD_TAG=%s" % (OUT, size, tag))
    return tag


if __name__ == "__main__":
    build()