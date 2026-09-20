# coding: utf-8
"""盘中异动检测（DE 保障 #5，2026-09-11）：每 30 分钟盘中巡检。
- 三桥心跳最新文件超 MAX_AGE 秒未刷新 → 异动
- 三策略持仓任一单日涨跌幅超 VOLATILITY% → 异动
输出：stdout 打印异常清单；退出码 0=无异常，2=有异常（供 ps1 判断是否触发 DE 归因）。
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

MAX_AGE_SEC = 600
VOLATILITY_PCT = 5.0

BRIDGES = {
    "G2": ("D:/QMT_POOL/g2_bridge", "state"),
    "ENS": ("D:/QMT_POOL/p16_ensemble_bridge", "state"),
    "V13": ("D:/QMT_POOL/p16_v13_risk", "state"),
}
CFGS = {
    "V1.3": ("D:/QMT_POOL/p16_v13_risk/cmd", "positions_cfg_v13_"),
    "G2": ("D:/QMT_POOL/g2_bridge/cmd", "positions_cfg_"),
    "ENS": ("D:/QMT_POOL/p16_ensemble_bridge/cmd", "positions_cfg_"),
}
# 账户快照检测（T-20260915-003：603332 被模拟盘服务端废单回滚，账户空仓但账本仍记、
# 三桥无卖出记录——需"持仓意外消失"告警）。快照是全账户，归属用对应策略账本 cfg 判断。
# V1.3(miniQMT 直连)无 positions 快照文件，跳过。
SNAPS = {
    "G2": ("D:/QMT_POOL/g2_bridge/state", "positions_", "D:/QMT_POOL/g2_bridge/cmd", "positions_cfg_"),
    "ENS": ("D:/QMT_POOL/p16_ensemble_bridge/state", "positions_", "D:/QMT_POOL/p16_ensemble_bridge/cmd", "positions_cfg_"),
}


def _latest_heart_age(state_dir):
    try:
        files = [f for f in os.listdir(state_dir) if f.startswith("heart_") and f.endswith(".json")]
        if not files:
            return None
        files.sort(reverse=True)
        return time.time() - os.path.getmtime(os.path.join(state_dir, files[0]))
    except Exception:
        return None


def _latest_positions(cmd_dir, prefix):
    try:
        files = [f for f in os.listdir(cmd_dir) if f.startswith(prefix) and f.endswith(".json")]
        files.sort(reverse=True)
        if not files:
            return []
        with open(os.path.join(cmd_dir, files[0]), encoding="utf-8") as f:
            d = json.load(f)
        return d.get("positions", []) or []
    except Exception:
        return []


def _snap_codes(state_dir, prefix):
    """按文件名日期倒序，返回最近两个不同日期快照 (prev, today)，各为 (date_str, {code:vol})。
    快照文件由桥在 15:00 收盘后写入（账户真实持仓），盘前/盘中当日快照可能未生成。"""
    try:
        files = sorted([f for f in os.listdir(state_dir) if f.startswith(prefix) and f.endswith(".json")], reverse=True)
        snaps = []
        for fn in files:
            date_str = fn[len(prefix):-5]
            with open(os.path.join(state_dir, fn), encoding="utf-8") as f:
                d = json.load(f)
            codes = {}
            for p in d.get("positions", []) or []:
                c = str(p.get("code", "") or "")
                v = int(p.get("volume", p.get("vol", 0)) or 0)
                if c and v > 0:
                    codes[c] = v
            snaps.append((date_str, codes))
            if len(snaps) >= 2:
                break
        if not snaps:
            return None, None
        if len(snaps) == 1:
            return None, snaps[0]
        return snaps[1], snaps[0]
    except Exception:
        return None, None


def _cfg_has_code(cmd_dir, prefix, date_str, code):
    """该策略指定日期的账本 cfg 是否含该 code（用于归属判断：快照是全账户、账本才归策略）。
    日期回退（当日 cfg 可能未生成，取 <= date 的最近一份）。"""
    try:
        files = sorted([f for f in os.listdir(cmd_dir) if f.startswith(prefix) and f.endswith(".json")], reverse=True)
        for fn in files:
            if fn[len(prefix):-5] > date_str:
                continue
            with open(os.path.join(cmd_dir, fn), encoding="utf-8") as f:
                d = json.load(f)
            for p in d.get("positions", []) or []:
                if str(p.get("code", "") or "") == code:
                    return True
            return False
    except Exception:
        pass
    return False


def _today_sells(state_dir, fills_prefix, date_str):
    """当日该桥是否有 SELL 成交（有卖出记录 → 持仓消失属正常，不告警）。"""
    try:
        fn = os.path.join(state_dir, "%s%s.json" % (fills_prefix, date_str))
        if not os.path.exists(fn):
            return set()
        with open(fn, encoding="utf-8") as f:
            d = json.load(f)
        sells = set()
        for fl in d.get("fills", []) or []:
            if str(fl.get("action", "") or "").upper() == "SELL":
                sells.add(str(fl.get("code", "") or ""))
        return sells
    except Exception:
        return set()


def _quote(code):
    """腾讯行情 (price, pre_close)。失败返回 None。"""
    try:
        import urllib.request
        sym = code.split(".")[0]
        ex = code.split(".")[1].lower()
        url = "http://qt.gtimg.cn/q=%s%s" % (ex, sym)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=5).read().decode("gbk", errors="ignore")
        f = raw.split("~")
        if len(f) > 4:
            return float(f[3]), float(f[4])
    except Exception:
        pass
    return None


def main():
    anomalies = []
    # 1) 桥心跳
    for name, (base, sub) in BRIDGES.items():
        age = _latest_heart_age(os.path.join(base, sub))
        if age is None:
            anomalies.append("[桥] %s 无心跳文件" % name)
        elif age > MAX_AGE_SEC:
            anomalies.append("[桥] %s 心跳已 %d 秒未刷新" % (name, int(age)))
    # 2) 持仓单日波动
    for strat, (cmd_dir, prefix) in CFGS.items():
        for p in _latest_positions(cmd_dir, prefix):
            code = p.get("code", "")
            vol = int(p.get("vol", 0) or 0)
            if not code or vol <= 0:
                continue
            q = _quote(code)
            if not q or q[1] <= 0:
                continue
            chg = (q[0] / q[1] - 1.0) * 100.0
            if abs(chg) >= VOLATILITY_PCT:
                anomalies.append("[持仓] %s %s 单日 %+.2f%%（现价 %.2f）" % (strat, code, chg, q[0]))
    # 3) 账户快照持仓意外消失（T-20260915-003：模拟盘服务端废单回滚致账户空仓、账本未同步、
    #    无卖出记录——需人工核实，避免明日换仓重复建仓/误风控）
    for strat, (state_dir, snap_prefix, cmd_dir, cfg_prefix) in SNAPS.items():
        prev, today = _snap_codes(state_dir, snap_prefix)
        if not prev or not today:
            continue
        prev_date, prev_codes = prev
        today_date, today_codes = today
        for code, vol in prev_codes.items():
            if code in today_codes:
                continue
            if code in _today_sells(state_dir, "fills_", today_date):
                continue
            if not _cfg_has_code(cmd_dir, cfg_prefix, prev_date, code):
                continue
            anomalies.append(
                "[快照] %s 持仓 %s(%d股) 在 %s 快照消失、%s 账本仍持有，且当日无 SELL 成交"
                "——疑似外部/废单回滚，需人工核实（勿用旧账本重复建仓）"
                % (strat, code, vol, today_date, prev_date)
            )
    if not anomalies:
        print("[OK] 盘中无异常")
        return 0
    print("盘中异动 %d 项：" % len(anomalies))
    for a in anomalies:
        print("  ⚠ " + a)
    return 2


if __name__ == "__main__":
    sys.exit(main())
