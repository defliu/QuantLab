# -*- coding: utf-8 -*-
"""数据源熔断路由（2026-09-03，T-20260903-002）。

背景：各数据源不太稳定（TDX 6 日 3 挂、悟道插件层连挂、iFind 口径存疑），
用熔断器让故障源快速短路、自动切到可用源，避免每次都在死源上耗超时。

规则：
  连续失败 >= CIRCUIT_FAIL_THRESHOLD(3)  -> 熔断 CIRCUIT_OPEN_SECONDS(300s)
  熔断期 check 返回 OPEN（调用方直接跳过该源，不再尝试）
  熔断剩余 < HALF_OPEN_AFTER(120s) 时 check 返回 PROBE（半开探测，调用方可试一次）
  成功 record ok -> 立即恢复（fail_count=0, circuit 清除）
  失败 record fail -> fail_count+1，达到阈值再次熔断

状态持久化 data/cache/datasource_health.json（多任务共用，幂等，无锁并发安全：原子写 + 读时容错）。

CLI:
  python scripts/data_source_router.py check <source>         # OK / OPEN / PROBE
  python scripts/data_source_router.py record <source> ok|fail [latency_ms]
  python scripts/data_source_router.py status                 # 全部源状态
  python scripts/data_source_router.py reset <source>         # 手动清除某源熔断
"""
import json
import os
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEALTH_FILE = os.path.join(PROJ, "data", "cache", "datasource_health.json")

CIRCUIT_FAIL_THRESHOLD = 3
CIRCUIT_OPEN_SECONDS = 300
HALF_OPEN_AFTER = 120

_KNOWN_SOURCES = [
    "tencent", "tencent_index", "mcp_tdx", "tdx_index",
    "sina_finance", "wudao", "ifind", "eastmoney_mx", "eastmoney_curl",
    "full_link_tdx", "tdx_independent", "miniqmt", "akshare",
]


def _market_open_restricted():
    """悟道免费版盘中受限窗口（交易日 09:15-10:30，服务端 FREE_TIER_MARKET_OPEN_RESTRICTED）。"""
    now = time.localtime()
    if now.tm_wday >= 5:
        return False
    t = now.tm_hour * 60 + now.tm_min
    return 9 * 60 + 15 <= t < 10 * 60 + 30


def _load():
    try:
        with open(HEALTH_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data):
    os.makedirs(os.path.dirname(HEALTH_FILE), exist_ok=True)
    tmp = HEALTH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, HEALTH_FILE)


def _state(h, source):
    st = h.get(source) or {}
    fail = int(st.get("fail_count", 0))
    opened_at = st.get("opened_at")
    return st, fail, opened_at


def check(source):
    """返回 OK（可用）/ OPEN（熔断或盘中受限跳过）/ PROBE（半开可试一次）。"""
    if source == "wudao" and _market_open_restricted():
        return "OPEN"
    h = _load()
    st, fail, opened_at = _state(h, source)
    if not opened_at:
        return "OK"
    remaining = CIRCUIT_OPEN_SECONDS - (time.time() - float(opened_at))
    if remaining <= 0:
        # 熔断到期自动恢复为半开探测
        return "PROBE"
    if remaining <= HALF_OPEN_AFTER:
        return "PROBE"
    return "OPEN"


def record(source, ok, latency_ms=None):
    """记录一次调用结果，自动熔断/恢复。"""
    h = _load()
    st, fail, opened_at = _state(h, source)
    now = time.time()
    if ok:
        st["fail_count"] = 0
        st.pop("opened_at", None)
        st["last_ok"] = now
        st["last_ok_ts"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    else:
        st["fail_count"] = fail + 1
        st["last_fail"] = now
        st["last_fail_ts"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        if st["fail_count"] >= CIRCUIT_FAIL_THRESHOLD and not opened_at:
            st["opened_at"] = now
            st["circuit"] = True
            print("[熔断] %s 连续失败 %d 次，熔断 %ds" % (source, st["fail_count"], CIRCUIT_OPEN_SECONDS))
    if latency_ms is not None:
        st["latency_ms"] = round(float(latency_ms), 1)
    h[source] = st
    _save(h)
    return st


def reset(source):
    h = _load()
    h.pop(source, None)
    _save(h)
    print("[重置] %s 熔断状态已清除" % source)


def status():
    h = _load()
    if not h:
        print("（无任何源状态记录）")
        return
    for src in sorted(h):
        st, fail, opened_at = _state(h, src)
        stt = check(src)
        line = "%-16s %-5s fail=%d" % (src, stt, fail)
        if opened_at:
            remaining = CIRCUIT_OPEN_SECONDS - (time.time() - float(opened_at))
            line += " 剩余%.0fs" % max(remaining, 0)
        if st.get("last_ok_ts"):
            line += " 上次OK %s" % st["last_ok_ts"]
        if st.get("last_fail_ts"):
            line += " 上次FAIL %s" % st["last_fail_ts"]
        print(line)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "status":
        status()
        return 0
    if cmd == "reset" and len(sys.argv) >= 3:
        reset(sys.argv[2])
        return 0
    if cmd == "check" and len(sys.argv) >= 3:
        print(check(sys.argv[2]))
        return 0
    if cmd == "record" and len(sys.argv) >= 4:
        src = sys.argv[2]
        ok = sys.argv[3].lower() in ("ok", "true", "1", "success")
        lat = float(sys.argv[4]) if len(sys.argv) >= 5 else None
        st = record(src, ok, lat)
        print("recorded %s ok=%s fail_count=%d" % (src, ok, st["fail_count"]))
        return 0
    print("未知命令")
    return 2


if __name__ == "__main__":
    sys.exit(main())
