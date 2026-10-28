# -*- coding: utf-8 -*-
"""信号通道离线回归（不需要 QMT、不需要登录）。

覆盖：hub 包装/缓存、当天计划放行、非当天拒绝、客户端校验（schema/日期/账号/过期/
字段非法/重复腿）、服务器不可达、signal_id 幂等与跨重启去重、以及「没信号≠清仓」。
"""
import datetime
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import g2_signal_client as cli          # noqa: E402

PY = sys.executable
fails = []


def check(name, cond):
    if cond:
        print("  OK   " + name)
    else:
        print("  FAIL " + name)
        fails.append(name)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def today():
    return datetime.datetime.now().strftime("%Y%m%d")


def write_plan(path, date=None, account="67014907", orders=None):
    plan = {
        "date": date or today(),
        "account_id": account,
        "capital": 100000.0,
        "written": True,
        "note": "test",
        "sells": [{"code": "600000.SH", "vol": 100, "cost": 9.9}],
        "buys": [{"code": "000001.SZ", "vol": 200, "price": 11.49, "held": 0, "target_vol": 200}],
        "orders": orders if orders is not None else [
            {"action": "SELL", "code": "600000.SH", "strategy_order_id": "g2-s1",
             "vol": 100, "price": 9.78, "reason": "rotate_out"},
            {"action": "BUY", "code": "000001.SZ", "strategy_order_id": "g2-b1",
             "vol": 200, "price": 11.49, "reason": "rotate_in"},
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False)
    return plan


def start_hub(plan_path, port, extra=None):
    cmd = [PY, os.path.join(HERE, "qmt_signal_hub.py"),
           "--plan", plan_path, "--account", "67014907", "--port", str(port)]
    if extra:
        cmd += extra
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = "http://127.0.0.1:%d/health" % port
    for _ in range(60):
        env, err = cli.fetch(url, timeout=0.5)
        if env is not None:
            return p
        time.sleep(0.1)
    out = p.stdout.read().decode("utf-8", "replace") if p.stdout else ""
    p.kill()
    raise RuntimeError("hub 起不来:\n" + out)


def stop(p):
    try:
        p.kill()
    except Exception:
        pass
    p.wait()


tmp = tempfile.mkdtemp(prefix="sigch_")
plan = os.path.join(tmp, "rebalance_g2_%s.json" % today())
write_plan(plan)
port = free_port()
hub = start_hub(plan, port)
base = "http://127.0.0.1:%d" % port

try:
    # --- H1 hub health + 信封 ---
    h, _ = cli.fetch(base + "/health", timeout=2.0)
    check("H1 health ok", h is not None and h.get("ok") is True)

    env, err = cli.fetch(base + "/signal", timeout=2.0)
    check("H2 /signal 返回信封", env is not None and env.get("schema") == "p16.signal.v1")
    check("H2 信封带 signal_id 与 trade_date",
          bool(env.get("signal_id")) and env.get("trade_date") == today())
    check("H2 订单条数=2", len(env.get("orders") or []) == 2)

    env2, _ = cli.fetch(base + "/signal", timeout=2.0)
    check("H3 同内容重复拉取 signal_id 不变（幂等）", env2["signal_id"] == env["signal_id"])

    # --- C1 正常放行 ---
    ok, reasons, orders = cli.validate(env, today(), "67014907", int(time.time()))
    check("C1 正常信号通过", ok and not reasons and len(orders) == 2)
    check("C1 腿字段完整", orders[0]["action"] == "SELL" and orders[1]["code"] == "000001.SZ")
    check("C1 日志可 grep", "[P16SIG][OK]" in cli.describe(env, ok, reasons, orders))

    # --- C2 过期 ---
    ok, reasons, _ = cli.validate(env, today(), "67014907", int(time.time()) + 3600)
    check("C2 过期信号被拒", (not ok) and any("过期" in r for r in reasons))

    # --- C3 日期/账号/schema ---
    ok, reasons, _ = cli.validate(env, "20261028", "67014907", int(time.time()))
    check("C3 日期不符被拒", (not ok) and any("日期不符" in r for r in reasons))
    ok, reasons, _ = cli.validate(env, today(), "70180771", int(time.time()))
    check("C3 账号不符被拒", (not ok) and any("账号不符" in r for r in reasons))
    bad = dict(env); bad["schema"] = "p16.signal.v0"
    ok, reasons, _ = cli.validate(bad, today(), "67014907", int(time.time()))
    check("C3 schema 不符被拒", (not ok) and any("schema" in r for r in reasons))

    # --- C4 字段非法：整单放弃而不是部分执行 ---
    bad = dict(env)
    bad["orders"] = [
        {"action": "BUY", "code": "000001.SZ", "vol": 150, "price": 11.49},   # 非整百
        {"action": "HOLD", "code": "600000.SH", "vol": 100, "price": 9.78},   # 动作非法
        {"action": "SELL", "code": "60000X.SH", "vol": 100, "price": 9.78},   # 代码非法
        {"action": "SELL", "code": "600000.SH", "vol": 100, "price": 9.78},   # 与上同键? 不同代码
        {"action": "BUY", "code": "000001.SZ", "vol": 200, "price": 11.49},   # 与第1条重复键
    ]
    ok, reasons, _ = cli.validate(bad, today(), "67014907", int(time.time()))
    check("C4 非法股数/动作/代码/重复键全部被拒",
          (not ok) and len(reasons) >= 4)
    check("C4 拒绝时仍打印可读原因", "[P16SIG][REJECT]" in cli.describe(env, False, reasons, []))

    # --- C5 空订单 = 拒绝（不能被当成清仓）---
    empty = dict(env); empty["orders"] = []
    ok, reasons, _ = cli.validate(empty, today(), "67014907", int(time.time()))
    check("C5 空订单被拒（不当作清仓）", (not ok) and any("订单为空" in r for r in reasons))

    # --- C6 跨重启去重 ---
    check("C6 当日委托 remark 命中即视为已执行",
          cli.already_applied(env["signal_id"], ["%s-BUY-000001.SZ" % env["signal_id"]]))
    check("C6 无命中则未执行", not cli.already_applied(env["signal_id"], ["other-remark"]))

    # --- H4 计划文件不是当天的 -> hub 明确 404 ---
    stop(hub)
    stale = os.path.join(tmp, "stale.json")
    write_plan(stale, date="20261028")
    port2 = free_port()
    hub = start_hub(stale, port2)
    env3, err3 = cli.fetch("http://127.0.0.1:%d/signal" % port2, timeout=2.0)
    check("H4 非当天计划不被发布", env3 is None and err3 is not None)
    h2, _ = cli.fetch("http://127.0.0.1:%d/health" % port2, timeout=2.0)
    check("H4 health 说明原因", h2 is not None and "plan_date_not_today" in (h2.get("reason") or ""))
    stop(hub)
finally:
    try:
        stop(hub)
    except Exception:
        pass

# --- H5 服务器不可达：客户端必须安静失败，不抛异常 ---
dead, err = cli.fetch("http://127.0.0.1:%d/signal" % free_port(), timeout=0.6)
check("H5 hub 不可达 -> (None, err) 且不抛", dead is None and err is not None)
ok, reasons, orders = cli.validate(dead, today(), "67014907", int(time.time()))
check("H5 拿不到信号 = 不下单", (not ok) and orders == [])

# --- M1..M5 沙箱镜像器（主号计划 -> 沙箱计划）---
import qmt_signal_mirror as mir

src_plan = {
    "date": today(), "account_id": "67014907", "capital": 100000.0, "written": True,
    "orders": [
        {"action": "SELL", "code": "600000.SH", "vol": 500, "price": 9.78, "reason": "a"},
        {"action": "BUY", "code": "000001.SZ", "vol": 1000, "price": 11.49, "reason": "b"},
        {"action": "BUY", "code": "000002.SZ", "vol": 200, "price": 8.0, "reason": "tiny"},
    ],
}
sp = os.path.join(tmp, "rebalance_g2_%s.json" % today())
with open(sp, "w", encoding="utf-8") as f:
    json.dump(src_plan, f, ensure_ascii=False)
dst_dir = os.path.join(tmp, "sbx")
dp, out = mir.mirror(sp, dst_dir, "70180771", 20000.0)
check("M1 账号改写为沙箱", out["account_id"] == "70180771")
check("M2 资金改写且股数按 1/5 缩放", out["capital"] == 20000.0
      and out["orders"][0]["vol"] == 100 and out["orders"][1]["vol"] == 200)
check("M3 缩放到 0 股的腿被丢弃并留原因",
      len(out["orders"]) == 2 and len(out["mirror"]["dropped"]) == 1
      and out["mirror"]["dropped"][0]["code"] == "000002.SZ")
check("M4 落盘为目标日期文件名且可复读",
      os.path.basename(dp) == "rebalance_g2_%s.json" % today() and os.path.exists(dp))
# 镜像产物是「计划」不是「信封」：要用 hub 的 build_envelope 包一层，再跑客户端校验，
# 这样 M5 验的才是真实链路（mirror -> hub -> client）。
import qmt_signal_hub as hub
_env = hub.build_envelope(out, json.dumps(out, ensure_ascii=False), "70180771", dp)
_ok5, _why5, _legs5 = cli.validate(_env, today(), "70180771", int(time.time()))
check("M5 镜像计划经 hub 装信封后能过客户端校验",
      _ok5 and len(_legs5) == 2)

print("")
if fails:
    print("FAILED %d: %s" % (len(fails), " | ".join(fails)))
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
