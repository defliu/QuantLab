# coding: utf-8
"""桥心跳超时告警（DE 体检 P0 加固，2026-09-11）：盘中每 30 分钟巡检三桥（G2/ENS/V13），
任一最新 heart 文件超过 MAX_AGE 秒未刷新 → 飞书告警 + 打印。
交易日 09:30-15:10 窗口内才检查（脚本由每 30 分钟的 Windows 任务调度，窗口外零动作）。

用法：python check_bridge_heartbeat.py
退出码：0=正常；2=发现桥心跳超时（已告警）。
"""
import json
import os
import sys
import time
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import is_trade_day as ITD
from qmt_bridge_client import notify_feishu

MAX_AGE_SEC = 600  # 心跳最大允许间隔（G2/ENS/V13 均 ~2 分钟一写，10 分钟未刷新=异常）

BRIDGES = {
    "G2": "D:/QMT_POOL/g2_bridge/state",
    "ENS": "D:/QMT_POOL/p16_ensemble_bridge/state",
    "V13": "D:/QMT_POOL/p16_v13_risk/state",
}


def _latest_heart(dirpath):
    """返回该目录最新 heart_<date>.json 的 (path, mtime_epoch)；无文件返回 None。"""
    try:
        files = [f for f in os.listdir(dirpath) if f.startswith("heart_") and f.endswith(".json")]
        if not files:
            return None
        files.sort(reverse=True)
        p = os.path.join(dirpath, files[0])
        return p, os.path.getmtime(p)
    except Exception:
        return None


def main():
    try:
        cal = ITD.load_calendar()
        res = ITD.is_trade_day(datetime.now().date(), cal)
        if not res.get("is_trade_day", False):
            return 0
    except Exception:
        return 0
    hhmm = int(time.strftime("%H%M"))
    if hhmm < 930 or hhmm > 1510:
        return 0

    stale = []
    for name, d in BRIDGES.items():
        got = _latest_heart(d)
        if not got:
            stale.append((name, "无心跳文件"))
            continue
        p, mt = got
        age = time.time() - mt
        if age > MAX_AGE_SEC:
            stale.append((name, "%s 已 %d 秒未刷新" % (p, int(age))))
        else:
            print("[OK] %s 心跳 %d 秒前" % (name, int(age)))

    if stale:
        msg = "【P16桥心跳告警】%s 桥心跳超时（>%d秒）：\n%s" % (
            time.strftime("%Y-%m-%d %H:%M"), MAX_AGE_SEC, "\n".join("  %s: %s" % s for s in stale))
        print(msg)
        try:
            notify_feishu(msg)
        except Exception as e:
            print("飞书告警失败: %s" % e)
        return 2
    print("[OK] 三桥心跳均正常（%s）" % time.strftime("%H:%M"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
