# coding=utf-8
"""qmt_autodeploy.wait_for_tag 回归：专治「假 PASS」（2026-10-29 实证踩到）。

事故现场：执行壳 STRATEGY_P16_G2_EXEC_SBX 落盘后客户端没加载（日志零条 [P16SIG]），
但部署脚本报 PASS——因为 excerpt 缓冲跨轮复用，上一轮收集的其它策略 [INIT] 行让
`if excerpt: return True` 在**没有命中 BUILD_TAG** 时成立。

本测试直接喂假日志目录，四类场景都必须钉住：
  A) 只有别的策略的 [INIT]/[BUILD] 行、无本 TAG  → 必须 False
  B) 出现本 TAG                                  → 必须 True 且 excerpt 只有命中行
  C) 先噪声再多轮，TAG 出现在第 3 轮             → 必须 True（且不是被噪声误判）
  D) configFormula not exist + 模型名             → 必须 False + MODEL_NOT_SEEDED
"""
import importlib.util
import io
import os
import sys
import tempfile
import threading
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ad", os.path.join(HERE, "qmt_autodeploy.py"))
ad = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ad)

fails = []


def check(name, cond):
    if cond:
        print("  OK   " + name)
    else:
        print("  FAIL " + name)
        fails.append(name)


MODEL = "STRATEGY_P16_G2_EXEC_SBX"
TAG = "20261029-071059"


def make_client(lines):
    root = tempfile.mkdtemp(prefix="adq_")
    d = os.path.join(root, "userdata", "log")
    os.makedirs(d)
    date = time.strftime("%Y%m%d")
    p = os.path.join(d, "XtClient_FormulaOutput_%s.log" % date)
    with io.open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return root, p


def run_once(lines, timeout=6):
    root, p = make_client(lines)
    # wait_for_tag 会先取文件当前大小作为起点；为了让「新增行」被读到，先写好再追加
    base = lines
    threading.Timer(1.5, lambda: io.open(p, "a", encoding="utf-8").write(EXTRA[0])).start()
    ok, reason, excerpt = ad.wait_for_tag(root, MODEL, TAG, timeout_sec=timeout, interval=0.5)
    return ok, reason, excerpt


# ---- A) 只有别的策略噪声，多轮后仍无 TAG ----
EXTRA = ["\n[P16G2I][HEART] date=20261029 pending=0 positions=0\n"]
noise = [
    "[P16G2I][BUILD] BUILD_TAG=20261029-092007 account=67014907",
    "[P16G2I][INIT] date=20261029 passive=True pending=0 positions=0",
]
ok, reason, excerpt = run_once(noise, timeout=4)
check("A 只有别的策略噪声 -> 必须 False（不得假 PASS）", ok is False and reason == "TIMEOUT")

# ---- B) 本 TAG 出现 -> True ----
EXTRA = ["\n[P16SIG][EXEC] INIT BUILD_TAG=%s account=70180771 dry_run=True\n" % TAG]
ok, reason, excerpt = run_once(noise, timeout=6)
check("B 出现本 TAG -> True", ok is True and reason == "matched")
check("B excerpt 只含命中行（不混噪声）",
      len(excerpt) == 1 and TAG in excerpt[0] and "P16G2I" not in excerpt[0])

# ---- C) 第 3 轮才出现 TAG，前几轮全是噪声 ----
EXTRA = ["\n", "\n[P16G2I][HEART] tick\n", "\n[P16SIG][EXEC] INIT BUILD_TAG=%s\n" % TAG]

_root, _p = make_client(noise)


def dribble():
    for chunk in EXTRA:
        time.sleep(1.0)
        io.open(_p, "a", encoding="utf-8").write(chunk)


threading.Thread(target=dribble, daemon=True).start()
ok, reason, excerpt = ad.wait_for_tag(_root, MODEL, TAG, timeout_sec=8, interval=0.4)
check("C 多轮噪声后才出现 TAG -> True（未被噪声误判）", ok is True and reason == "matched")

# ---- D) 未预注册 ----
EXTRA = ["\nconfigFormula not exist, continue  utfName:%s\n" % MODEL]
ok, reason, excerpt = run_once(noise, timeout=6)
check("D 未注册模型 -> False + MODEL_NOT_SEEDED",
      ok is False and reason == "MODEL_NOT_SEEDED")

print("")
if fails:
    print("FAILED %d: %s" % (len(fails), " | ".join(fails)))
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
