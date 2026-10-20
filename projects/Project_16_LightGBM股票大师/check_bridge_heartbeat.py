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
# 午休感知（2026-09-21 修复，T-20260921-002）：A 股午休 11:30~13:00 无行情推送，桥心跳随 handlebar
# 停更（实测 09-18/09-21 两天 12:00 恒报 ~1801 秒、12:30 恒报 ~3600 秒、13:00:03 心跳 0 秒前准时恢复）。
# 午休窗口内阈值放宽为「距午休开始的时长 + MAX_AGE_SEC」：健康桥（最后心跳 11:30:0x）不告警；
# 午休前已死的桥（如 11:00 起停更）仍会被抓到（12:00 时 age=3600 > 阈值 2400）。
LUNCH_START_SEC = 11 * 3600 + 30 * 60  # 11:30:00
LUNCH_END_SEC = 13 * 3600  # 13:00:00


def _effective_max_age(now_sec):
    """午休窗口内（11:30~13:00）按已进入午休的时长放宽阈值，避免结构性误报。"""
    if LUNCH_START_SEC < now_sec <= LUNCH_END_SEC:
        return (now_sec - LUNCH_START_SEC) + MAX_AGE_SEC
    return MAX_AGE_SEC

BRIDGES = {
    "G2": "D:/QMT_POOL/g2_bridge/state",
    "ENS": "D:/QMT_POOL/p16_ensemble_bridge/state",
    "V13": "D:/QMT_POOL/p16_v13_risk/state",
}
# 自闭环心跳（2026-10-20，T-20261020-001）：三策略 2026-10-15~16 起零文件运行，
# heart_*.json 停写，唯一通道是 print [P16xx][HEART] → FormulaOutput 日志。
# 双客户端并存（AGENTS.md）：G2/ENS 在 D:\QMT交易端模拟，V13 在 D:\国金QMT交易端模拟。
LOG_ROOTS = [r"D:\国金QMT交易端模拟", r"D:\QMT交易端模拟"]
LOG_TAGS = {"G2": "P16G2", "ENS": "P16ENS", "V13": "P16V13"}


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


def _latest_heart_log(tag):
    """自闭环心跳（2026-10-20，T-20261020-001）：从 QMT FormulaOutput 日志取该策略
    最新 [HEART] 行，返回 (ts_str, epoch) 或 None。取最近 3 个日志文件兜底。"""
    import re
    pat = re.compile(r"\[%s\]\[HEART\]\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})" % re.escape(tag))
    best = None
    for root in LOG_ROOTS:
        d = os.path.join(root, "userdata", "log")
        if not os.path.isdir(d):
            continue
        try:
            logs = sorted([f for f in os.listdir(d)
                           if f.startswith("XtClient_FormulaOutput_") and f.endswith(".log")],
                          reverse=True)[:3]
        except OSError:
            continue
        for fn in logs:
            try:
                with open(os.path.join(d, fn), "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        m = pat.search(line)
                        if m and (best is None or m.group(1) > best[0]):
                            best = (m.group(1), os.path.join(d, fn))
            except Exception:
                continue
    if not best:
        return None
    try:
        ep = time.mktime(time.strptime(best[0], "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return None
    return best[0], ep


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
    eff_max_age = _effective_max_age(int(time.strftime("%H")) * 3600 + int(time.strftime("%M")) * 60 + int(time.strftime("%S")))
    for name, d in BRIDGES.items():
        # 自闭环优先：日志 [HEART] vs 文件心跳取较新者（旧 heart json 恒存在且恒陈旧，
        # 直接读文件会让每 30 分钟都误报「桥心跳超时」并推飞书，2026-10-16~20 实测 5 天）
        got = _latest_heart(d)
        file_age = (time.time() - got[1]) if got else None
        lgot = _latest_heart_log(LOG_TAGS.get(name, ""))
        log_age = (time.time() - lgot[1]) if lgot else None
        if log_age is not None and (file_age is None or log_age < file_age):
            age, src = log_age, "日志 %s" % lgot[0]
        elif got:
            age, src = file_age, os.path.basename(got[0])
        else:
            stale.append((name, "无心跳（文件与 FormulaOutput 日志均无）"))
            continue
        if age > eff_max_age:
            stale.append((name, "%s 已 %d 秒未刷新" % (src, int(age))))
        else:
            print("[OK] %s 心跳 %d 秒前（%s）" % (name, int(age), src))

    if stale:
        msg = "【P16桥心跳告警】%s 桥心跳超时（>%d秒）：\n%s" % (
            time.strftime("%Y-%m-%d %H:%M"), eff_max_age, "\n".join("  %s: %s" % s for s in stale))
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
