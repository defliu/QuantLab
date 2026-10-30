# coding: utf-8
"""运行态 TAG 核对回归（2026-10-30 立）。

钉住的判据：
  1. **只认日志 INIT 行**，不认文件 mtime / 不认 BUILD_TAG 出现在盘上文件里
     （--no-restart 落盘后「盘上 TAG = 新、运行中 TAG = 旧」是已知失真态）；
  2. 必须按「模型名.py」精确过滤 —— FormulaOutput 是各策略混写的，
     `..._G2_EXEC` 不能吃掉 `..._G2_EXEC_SBX`；
  3. 交易版路径 `D:\\国金证券QMT交易端` 一律拒绝，不进任何读取。
"""
import importlib.util
import os
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))

spec = importlib.util.spec_from_file_location("tagchk", os.path.join(HERE, "check_running_tag.py"))
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)

fails = []

# 固定日期：测试 fixture 写的是 20261030 的日志，不能依赖系统当天日期
D30 = "20261030"


def check(name, cond):
    if cond:
        print("  OK   " + name)
    else:
        print("  FAIL " + name)
        fails.append(name)


def make_client(root, model, disk_tag_val, log_lines, date_str="20261030"):
    py = os.path.join(root, "python")
    lg = os.path.join(root, "userdata", "log")
    os.makedirs(py)
    os.makedirs(lg)
    body = '# coding=gbk\nBUILD_TAG = "%s"\n' % disk_tag_val
    with open(os.path.join(py, "%s.py" % model), "wb") as f:
        f.write(body.encode("gbk"))
    with open(os.path.join(lg, "XtClient_FormulaOutput_%s.log" % date_str), "w",
              encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    return root


def init_line(model, tag, ts="2026-10-30 08:52:25"):
    # 照抄真实日志形状：`reqid = 0<客户端路径>\\python\\<模型>.py_NNN, output = ...`
    return ("%s,000 [INFO] [0x00001114] reqid = 0D:\\国金QMT交易端模拟\\python\\%s.py_00030011, "
            "output = [P16SIG][EXEC] INIT BUILD_TAG=%s"
            % (ts, model, tag))


# --- T1 盘上与运行一致 => OK ---
r1 = make_client(tempfile.mkdtemp(), "STRATEGY_P16_G2_EXEC", "20261030-140212",
                 [init_line("STRATEGY_P16_G2_EXEC", "20261030-140212")])
res = T.check_one("STRATEGY_P16_G2_EXEC", r1, D30)
check("T1 盘上=运行 => OK", res["status"] == "OK" and res["disk"] == res["running"])

# --- T2 盘上比运行新（--no-restart 落盘未加载）=> STALE ---
r2 = make_client(tempfile.mkdtemp(), "STRATEGY_P16_G2_EXEC", "20261030-180615",
                 [init_line("STRATEGY_P16_G2_EXEC", "20261030-140212")])
res = T.check_one("STRATEGY_P16_G2_EXEC", r2, D30)
check("T2 盘上新/运行旧 => STALE", res["status"] == "STALE")
check("T2 STALE 同时报出两个 TAG",
      res["disk"] == "20261030-180615" and res["running"] == "20261030-140212")
check("T2 STALE 提示 --no-restart 失真", "no-restart" in res["note"])

# --- T3 盘上有、运行中无 INIT => NOT_RUNNING（策略停着）---
r3 = make_client(tempfile.mkdtemp(), "STRATEGY_P16_V13_EXEC", "20261030-140213", [])
res = T.check_one("STRATEGY_P16_V13_EXEC", r3, D30)
check("T3 无 INIT => NOT_RUNNING", res["status"] == "NOT_RUNNING")
check("T3 盘上 TAG 仍报出", res["disk"] == "20261030-140213")

# --- T4 盘上产物不存在 => NO_DISK ---
empty = tempfile.mkdtemp()
os.makedirs(os.path.join(empty, "userdata", "log"))
res = T.check_one("STRATEGY_P16_G2_EXEC", empty, D30)
check("T4 盘上缺失 => NO_DISK", res["status"] == "NO_DISK")

# --- T5 日志串读：沙箱的 INIT 不能被 G2 认成自己的 ---
r5 = make_client(tempfile.mkdtemp(), "STRATEGY_P16_G2_EXEC", "20261030-140212",
                 [init_line("STRATEGY_P16_G2_EXEC_SBX", "20261030-140219")])
res = T.check_one("STRATEGY_P16_G2_EXEC", r5, D30)
check("T5 只有沙箱 INIT => G2 判 NOT_RUNNING（不串读）", res["status"] == "NOT_RUNNING")

# 反过来：两个都在，各自取自己的
r5b = make_client(tempfile.mkdtemp(), "STRATEGY_P16_G2_EXEC", "20261030-140212", [
    init_line("STRATEGY_P16_G2_EXEC", "20261030-140212"),
    init_line("STRATEGY_P16_G2_EXEC_SBX", "20261030-140219"),
])
check("T5 共存时 G2 取自己的 TAG",
      T.check_one("STRATEGY_P16_G2_EXEC", r5b, D30)["running"] == "20261030-140212")

# --- T6 多条 INIT（重启过）取最后一条 ---
r6 = make_client(tempfile.mkdtemp(), "STRATEGY_P16_G2_EXEC", "20261030-140212", [
    init_line("STRATEGY_P16_G2_EXEC", "20261029-195335", "2026-10-30 08:52:25"),
    init_line("STRATEGY_P16_G2_EXEC", "20261030-140212", "2026-10-30 10:00:32"),
])
res = T.check_one("STRATEGY_P16_G2_EXEC", r6, D30)
check("T6 多次 INIT 取最新一条", res["status"] == "OK" and res["running"] == "20261030-140212")

# --- T7 交易版路径一律拒绝 ---
res = T.check_one("STRATEGY_P16_G2_EXEC", r"D:\国金证券QMT交易端", D30)
check("T7 交易版路径 => FORBIDDEN", res["status"] == "FORBIDDEN")
check("T7 交易版不产生 disk/running 读取",
      res.get("disk") is None and res.get("running") is None)

# --- T8 盘上密文（无 BUILD_TAG 字面量）=> NO_DISK 且不误判 OK ---
r8 = tempfile.mkdtemp()
os.makedirs(os.path.join(r8, "python"))
os.makedirs(os.path.join(r8, "userdata", "log"))
with open(os.path.join(r8, "python", "STRATEGY_P16_G2_EXEC.py"), "wb") as f:
    f.write(b"\x00\x01\x02encrypted-blob")
res = T.check_one("STRATEGY_P16_G2_EXEC", r8, D30)
check("T8 密文无 TAG => NO_DISK（不伪造结论）", res["status"] == "NO_DISK")

# --- T9 INIT 打印成多行（TAG 在续行）=> 必须仍能取到 ---
# 审计 §2.5.5：旧实现用 `模型名.py in line` 只能命中首行，INIT 一旦多行就会误判 NOT_RUNNING。
r9 = make_client(tempfile.mkdtemp(), "STRATEGY_P16_G2_EXEC", "20261030-140212", [
    "2026-10-30 08:52:25,515 [INFO] [0x00001114] reqid = 0D:\\国金QMT交易端模拟\\python\\"
    "STRATEGY_P16_G2_EXEC.py_00030011, output = [P16SIG][EXEC] INIT",
    "  BUILD_TAG=20261030-140212 account=67014907 dry_run=False",
])
res = T.check_one("STRATEGY_P16_G2_EXEC", r9, D30)
check("T9 多行 INIT 也能取到 TAG（续行归属）", res["status"] == "OK"
      and res["running"] == "20261030-140212")

# --- T10 续行不能串到别的模型 ---
r10 = make_client(tempfile.mkdtemp(), "STRATEGY_P16_G2_EXEC", "20261030-140212", [
    "2026-10-30 08:52:25,594 [INFO] [0x00001114] reqid = 0D:\\国金QMT交易端模拟\\python\\"
    "STRATEGY_P16_V13_EXEC.py_00030013, output = [P16SIG][EXEC] INIT",
    "  BUILD_TAG=20261030-140213",
])
res = T.check_one("STRATEGY_P16_G2_EXEC", r10, D30)
check("T10 V13 的续行 INIT 不算 G2 的", res["status"] == "NOT_RUNNING")

print("")
if fails:
    print("FAILED %d: %s" % (len(fails), " | ".join(fails)))
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
