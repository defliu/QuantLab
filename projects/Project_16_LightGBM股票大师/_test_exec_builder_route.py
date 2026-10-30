# coding=utf-8
"""执行壳 builder 的 --route 覆盖回归（2026-10-30 立）。

钉住的坑：早期 `_apply_overrides` 把 route 拼在 `if port:` 里面，于是 `--route v13`
单独传入时**静默不生效**，产物回落到 `/signal`（hub 的默认路由 = G2 计划）。
G2 与 V1.3 同账号 67014907，plan_legs 的账号校验会通过 => 拿 G2 的计划下 V1.3 的单。

只测纯函数 `_apply_overrides`，不落盘、不碰 QMT。
"""
import importlib.util
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))

spec = importlib.util.spec_from_file_location("b_g2exec", os.path.join(HERE, "build_p16_g2_exec.py"))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

fails = []


def check(name, cond):
    if cond:
        print("  OK   " + name)
    else:
        print("  FAIL " + name)
        fails.append(name)


SRC = b._read(b.SRC)


def url_of(src):
    import re
    m = re.search(r'^SIGNAL_URL = "(.*?)"$', src, re.M)
    return m.group(1) if m else ""


def report_url_of(src):
    import re
    m = re.search(r'^REPORT_URL = SIGNAL_URL\.replace\("/signal", "/report"\)', src, re.M)
    return m is not None


# --- R1 route 单独传入必须生效（不需要 --port）---
s = b._apply_overrides(SRC, route="v13")
check("R1 --route v13 单独生效", url_of(s) == "http://127.0.0.1:58610/signal/v13")

s = b._apply_overrides(SRC, route="g2sbx")
check("R1 --route g2sbx 单独生效", url_of(s) == "http://127.0.0.1:58610/signal/g2sbx")

s = b._apply_overrides(SRC, route="g2")
check("R1 --route g2 单独生效", url_of(s) == "http://127.0.0.1:58610/signal/g2")

# --- R2 route + port 组合 ---
s = b._apply_overrides(SRC, route="v13", port=58610)
check("R2 route+port 组合", url_of(s) == "http://127.0.0.1:58610/signal/v13")

s = b._apply_overrides(SRC, port=58620, route="g2")
check("R2 port 换端口仍带 route", url_of(s) == "http://127.0.0.1:58620/signal/g2")

# --- R3 只传 port 时默认 g2 路由（兼容旧调用）---
s = b._apply_overrides(SRC, port=58610)
check("R3 只传 port 默认 g2", url_of(s) == "http://127.0.0.1:58610/signal/g2")

# --- R4 什么都不传则原样 ---
s = b._apply_overrides(SRC)
check("R4 无覆盖则保持源码", url_of(s) == url_of(SRC))

# --- R5 REPORT_URL 必须始终由 SIGNAL_URL 派生（改路由时账本回写跟着走）---
for kw in ({"route": "v13"}, {"route": "g2sbx"}, {"port": 58610}, {}):
    s = b._apply_overrides(SRC, **kw)
    check("R5 REPORT_URL 派生自 SIGNAL_URL (%s)" % (kw or "无覆盖"), report_url_of(s))

# --- R6 三个产物路由必须互不相同（防再次静默回落）---
urls = set()
for r in ("g2", "g2sbx", "v13"):
    urls.add(url_of(b._apply_overrides(SRC, route=r)))
check("R6 三条路由互不相同", len(urls) == 3)

# --- R7 账号覆盖与路由互不干扰 ---
s = b._apply_overrides(SRC, account="70180771", route="g2sbx", capital=20000)
check("R7 账号+路由+资金同时生效",
      'ACCOUNT_ID = "70180771"' in s
      and url_of(s) == "http://127.0.0.1:58610/signal/g2sbx"
      and "CAPITAL_BASE = 20000.0" in s)

print("")
if fails:
    print("FAILED %d: %s" % (len(fails), " | ".join(fails)))
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
