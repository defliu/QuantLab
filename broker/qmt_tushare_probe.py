# coding=gbk
# =============================================================
# QMT built-in Python -- Tushare connectivity probe
# BUILD_TAG = "@BUILD_TAG@"
#
# PURPOSE
#   Verify that a strategy running INSIDE the QMT client can call
#   api.tushare.pro and is not stopped by the client-side
#   python_anti_plugin_monitor ("[monitor] illegal ip ... try stop").
#
# WHITELIST EVIDENCE (client logs 2026-10-30, BOTH clients)
#   whitelist ip: 60.205.198.20:80
#   whitelist ip: 8.140.225.26:80
#   whitelist ip: 8.140.225.26:443
#   api.tushare.pro resolves to exactly those two A records.
#
# RISK
#   read-only: no passorder / cancel / account query / file IO.
#   one POST per endpoint, timeout <= 10s, runs once in init().
#
# OUTPUT
#   one line per step, prefix [TSWH], ASCII-safe text.
# =============================================================
BUILD_TAG = "@BUILD_TAG@"
TS_TOKEN = "@TS_TOKEN@"
TAG = "[TSWH]"

import json
import socket
import sys
import time
import traceback

API = "api.tushare.pro"
IP_PORT80 = ["60.205.198.20", "8.140.225.26"]
IP_443 = "8.140.225.26"
PAYLOAD = {
    "api_name": "trade_cal",
    "token": TS_TOKEN,
    "params": {"exchange": "SSE", "start_date": "20261001",
               "end_date": "20261031"},
    "fields": "exchange,cal_date,is_open",
}


def _log(msg):
    try:
        sys.stdout.write(TAG + " " + str(msg) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def _describe(d):
    data = d.get("data") or {}
    items = data.get("items") or []
    return "code=%s msg=%s rows=%d first=%s" % (
        d.get("code"), str(d.get("msg"))[:60], len(items),
        str(items[0]) if items else "None")


def _dns():
    try:
        infos = socket.getaddrinfo(API, 80, socket.AF_INET, socket.SOCK_STREAM)
        ips = []
        for it in infos:
            ip = it[4][0]
            if ip not in ips:
                ips.append(ip)
        _log("DNS %s -> %s" % (API, ",".join(ips)))
        return ips
    except Exception as e:
        _log("DNS FAIL %s" % repr(e))
        return []


def _tcp(ip, port):
    try:
        s = socket.create_connection((ip, port), 5)
        s.close()
        _log("TCP OK %s:%d" % (ip, port))
    except Exception as e:
        _log("TCP FAIL %s:%d %s" % (ip, port, repr(e)))


def _http_requests(name, url, headers):
    import requests
    try:
        requests.packages.urllib3.disable_warnings()
    except Exception:
        pass
    r = requests.post(url, data=json.dumps(PAYLOAD), headers=headers,
                      timeout=10, verify=False)
    _log("%s status=%s len=%d" % (name, r.status_code, len(r.text)))
    _log("%s body %s" % (name, _describe(r.json())))


def _http_urllib(name, url, headers):
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(PAYLOAD).encode("utf-8"),
                                 headers=headers or {})
    req.add_header("Content-Type", "application/json")
    r = urllib.request.urlopen(req, timeout=10)
    body = r.read().decode("utf-8", "replace")
    _log("%s status=%s len=%d" % (name, r.status, len(body)))
    _log("%s body %s" % (name, _describe(json.loads(body))))


def _post(name, url, headers=None):
    try:
        try:
            _http_requests(name, url, headers)
            return
        except ImportError:
            _log("%s requests missing, fallback urllib" % name)
        except Exception as e:
            _log("%s requests FAIL %s" % (name, repr(e)))
        _http_urllib(name, url, headers)
    except Exception as e:
        _log("%s ALL FAIL %s" % (name, repr(e)))
        _log("%s tb %s" % (name, traceback.format_exc()[-300:]))


def init(C):
    _log("INIT BUILD_TAG=%s py=%s" % (BUILD_TAG, sys.version.split()[0]))
    try:
        import requests
        _log("requests=%s" % requests.__version__)
    except Exception as e:
        _log("requests import FAIL %s" % repr(e))
    _log("token_len=%d" % len(TS_TOKEN))
    _dns()
    for ip in IP_PORT80:
        _tcp(ip, 80)
    _tcp(IP_443, 443)
    _post("HTTP-DOMAIN", "http://" + API)
    for ip in IP_PORT80:
        _post("HTTP-IP-" + ip, "http://" + ip, {"Host": API})
    _post("HTTPS-DOMAIN", "https://" + API)
    time.sleep(3)
    _log("ALIVE-AFTER-3S")
    _log("DONE BUILD_TAG=%s" % BUILD_TAG)


def handlebar(C):
    pass


def exit(C):
    pass
