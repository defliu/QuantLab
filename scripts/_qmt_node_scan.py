# coding: utf-8
# 一次性只读排查工具（T-20261029-002）：券商模拟环境登录服务 200003 超时期间，
# 从**模拟盘**客户端目录里枚举出现过的服务端地址，逐个做 TCP 探活，
# 用来判断「是否存在另一台可用的登录节点」（若有，切换登录地址才是可评估选项）。
#
# 硬边界：只扫描 D:\QMT交易端模拟 与 D:\国金QMT交易端模拟 两台模拟客户端，
#         **绝不读取** D:\国金证券QMT交易端（真钱交易版）的任何文件；
#         只读文件 + 只建 TCP 连接，不写任何文件、不改任何配置。
import os
import re
import socket
import collections
from concurrent.futures import ThreadPoolExecutor

CLIENTS = [r"D:\QMT交易端模拟", r"D:\国金QMT交易端模拟"]
FORBIDDEN = "国金证券QMT交易端"
# 只进这几个子目录（配置类），避免整树遍历卡死（实测整树 >4 分钟不返回）
DIRS = ["config", "userdata", "indexConfig", "quote", "bin"]
EXTS = (".lua", ".ini", ".xml", ".json", ".txt", ".config", ".properties", ".dat", ".csv")
MAX_BYTES = 2 * 1024 * 1024
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{2,5})?\b")
IPV4_PORT = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b")
SKIP_IP = ("127.", "0.", "255.", "224.", "239.", "192.0.0.", "10.", "192.168.", "172.")


def log(msg):
    print(msg)
    try:
        import sys
        sys.stdout.flush()
    except Exception:
        pass


def collect():
    hits = collections.defaultdict(list)
    for root in CLIENTS:
        assert FORBIDDEN not in root, root
        for sub in DIRS:
            base = os.path.join(root, sub)
            if not os.path.isdir(base):
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                low = dirpath.lower()
                # \\users 是账号/凭据目录（凭据管理器之外的一切客户端凭据文件一律不读）
                if any(k in low for k in ("\\log", "\\temp", "\\backup", "\\users")):
                    dirnames[:] = []
                    continue
                for fn in filenames:
                    if not fn.lower().endswith(EXTS):
                        continue
                    p = os.path.join(dirpath, fn)
                    try:
                        if os.path.getsize(p) > MAX_BYTES:
                            continue
                        raw = open(p, "rb").read()
                    except Exception:
                        continue
                    txt = None
                    for enc in ("utf-8", "gbk", "latin-1"):
                        try:
                            txt = raw.decode(enc)
                            break
                        except Exception:
                            txt = None
                    if txt is None:
                        continue
                    for m in IPV4_PORT.finditer(txt):
                        ip, _, port = m.group(0).rpartition(":")
                        if ip.startswith(SKIP_IP):
                            continue
                        try:
                            port_i = int(port)
                        except ValueError:
                            continue
                        hits[(ip, port_i)].append(os.path.relpath(p, root))
                log("  scanned %s" % os.path.relpath(dirpath, root))
    return hits


def probe(item):
    (ip, port), files = item
    try:
        s = socket.create_connection((ip, port), timeout=1.5)
        s.close()
        return (ip, port, True, len(files), files[0])
    except Exception:
        return (ip, port, False, len(files), files[0])


def main():
    hits = collect()
    log("候选服务端地址（去重后）= %d" % len(hits))
    with ThreadPoolExecutor(max_workers=16) as ex:
        rows = list(ex.map(probe, sorted(hits.items(), key=lambda kv: kv[0])))
    for ip, port, ok, n, sample in rows:
        log("%-18s %-6s TCP=%s 引用数=%d 例=%s" % (ip, port, "OK " if ok else "FAIL", n, sample))
    log("\n可达 %d / %d" % (sum(1 for r in rows if r[2]), len(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
