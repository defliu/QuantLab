# coding: utf-8
"""今晚（8/28）定时任务产出核验器（自愈版）。

等到 16:52 后自动检查 16:30/16:45 两个定时任务产出；发现可恢复异常自动重跑对应步骤并复验。
  A. Quant_Daily_Update (16:30)：增量 -> 8/28；V1.1 reconcile 报告
  B. paper_forward_daily (16:45)：paper_forward_live.csv 追加 8/28；g2 selections 20260828
可恢复异常的自愈动作（仅研究管道，不碰生产下单）：
  - 增量未到 8/28 → 重跑 xtdata_update.py
  - 8/28 样本未追加 → 重跑 build_g2_daily.py --date 20260828 + deploy_predict_g2.py --date 20260828
仍失败则标记"需人工"，保留诊断日志。报告：data/real/verify_tonight_20260828.md
"""
import os
import subprocess
import sys
import time
from datetime import datetime

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
DATA = os.path.join(PROJ, "data")
REAL = os.path.join(DATA, "real")
REPORT = os.path.join(REAL, "verify_tonight_20260828.md")
PY = r"C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"
TARGET_DATE = "2026-08-28"

L = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    L.append(s)


def wait_until(hhmm):
    now = datetime.now()
    target = now.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:]), second=10, microsecond=0)
    if now < target:
        log(f"等待到 {hhmm}（{(target - now).total_seconds():.0f}s）...")
        while time.time() < target.timestamp():
            time.sleep(min(60, max(1, target.timestamp() - time.time())))
    else:
        log(f"已过 {hhmm}，直接检查")


def run(cmd, cwd=PROJ):
    r = subprocess.run([PY, "-u"] + cmd, cwd=cwd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)[-2000:]


def latest_incr_date():
    incr = os.path.join(PROJ, "data_live", "incremental_daily.parquet")
    if not os.path.exists(incr):
        return None
    import pandas as pd
    d = pd.read_parquet(incr, columns=["trade_date"])
    return str(pd.to_datetime(d["trade_date"]).max().date())


def live_has(date):
    live = os.path.join(REAL, "paper_forward_live.csv")
    if not os.path.exists(live):
        return False
    with open(live, encoding="utf-8-sig") as f:
        return any(date in l for l in f.readlines())


def task_result(name):
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Get-ScheduledTaskInfo -TaskName '{name}' | "
                        f"Select-Object LastRunTime, LastTaskResult | Format-List"],
                       capture_output=True, text=True).stdout
    return [ln.strip() for ln in r.splitlines() if ln.strip()]


def main():
    log(f"# 定时任务产出核验（自愈） · 2026-08-28 · {datetime.now():%H:%M:%S}")
    wait_until("1652")

    # ---- A. 增量数据 ----
    log("")
    log("## A. Quant_Daily_Update (16:30)")
    mx = latest_incr_date()
    log(f"- incremental_daily.parquet 最新日: {mx} {'✅' if mx == TARGET_DATE else '⚠️'}")
    if mx != TARGET_DATE:
        log("  !! 增量未到目标日，自愈：重跑 xtdata_update.py ...")
        rc, out = run(["xtdata_update.py"])
        log(f"  xtdata_update.py exit={rc}")
        if rc == 0:
            log(f"  自愈后最新日: {latest_incr_date()} ✅")
        else:
            log(f"  自愈失败（需人工）:\n{out[-800:]}")
            log("  ⚠️ 需人工")

    rec = os.path.join(DATA, "reconcile_20260828.md")
    log(f"- reconcile 报告: {'存在 ✅' if os.path.exists(rec) else '缺失 ⚠️'}")
    if os.path.exists(rec):
        with open(rec, encoding="utf-8") as f:
            txt = f.read()
        log("  - 对账: " + ("持仓一致/无异常 ✅" if ("不一致 0 只" in txt or "无异常" in txt) else "⚠️ 见报告"))

    # ---- B. paper_forward_daily ----
    log("")
    log("## B. paper_forward_daily (16:45)")
    ok = live_has(TARGET_DATE)
    g2f = os.path.join(PROJ, "data", "selections", "g2", "20260828_g2_top2.csv")
    log(f"- paper_forward_live.csv 含 {TARGET_DATE}: {'✅' if ok else '⚠️ 未追加'}")
    log(f"- g2 selections 20260828: {'存在 ✅' if os.path.exists(g2f) else '缺失 ⚠️'}")
    if not ok or not os.path.exists(g2f):
        log("  !! 8/28 样本缺失，自愈：重跑 build_g2_daily + deploy_predict_g2 ...")
        rc1, o1 = run(["build_g2_daily.py", "--date", "20260828"])
        rc2, o2 = run(["deploy_predict_g2.py", "--date", "20260828"])
        log(f"  build exit={rc1} / deploy exit={rc2}")
        if rc1 == 0 and rc2 == 0 and live_has(TARGET_DATE):
            log(f"  自愈成功：live 含 {TARGET_DATE} ✅")
        else:
            log(f"  自愈失败（需人工）:\n{(o1 + o2)[-1200:]}")
            log("  ⚠️ 需人工")

    # ---- C. g2 日志尾部 ----
    glog = os.path.join(REAL, "g2_pipeline_daily.log")
    if os.path.exists(glog):
        tail = subprocess.run(["powershell", "-NoProfile", "-Command",
                               f"Get-Content '{glog}' -Tail 20"],
                              capture_output=True, text=True).stdout
        log(f"- g2 日志尾部（含今日 OK/FAILED 判断）:")
        for ln in tail.splitlines()[-10:]:
            log("    " + ln)

    # ---- D. 定时任务结果 ----
    log("")
    log("## D. 定时任务最近结果")
    for t in ["Quant_Daily_Update", "paper_forward_daily"]:
        log(f"- {t}:")
        for ln in task_result(t):
            log("    " + ln)

    txt = "\n".join(L)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(txt)
    print(f"\n核验报告已写入: {REPORT}")
    log(f"（核验完成 {datetime.now():%H:%M:%S}）")


if __name__ == "__main__":
    main()
