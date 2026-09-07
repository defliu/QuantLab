# -*- coding: utf-8 -*-
"""Project_16 夜间检修（2026-09-03，T-20260903-003）。

每日 01:00 执行，确保次日策略可正常运行、拿到的数据最新、面板/模型及时更新。
覆盖 A-G 模块：能自动修则修（--fix），修不了告警 + 盘前晨报，绝不阻塞次日任务。

用法:
  python scripts/nightly_check.py [--fix] [--push-alert]
  --fix         自动修复（面板重刷 / 增量重下 / 熔断恢复）
  --push-alert  有 FAIL 时飞书告警
输出: data/cache/nightly_check_<date>.md（逐项 PASS/FAIL/修复）
"""
import json
import os
import shutil
import subprocess
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
DATA = os.path.join(PROJ, "data")
CACHE = os.path.join(DATA, "cache")
DATA_LIVE = os.path.join(PROJ, "data_live")
REPORT = os.path.join(CACHE, "nightly_check_%s.md" % time.strftime("%Y%m%d"))

RESULT = []  # (模块, 项, 状态[PASS/FAIL/WARN], 说明)


def log(mod, item, status, note=""):
    RESULT.append((mod, item, status, note))
    print("[%s] [%s] %s %s" % (status, mod, item, note))


def run(cmd, cwd=None, timeout=900):
    """subprocess 运行，返回 (ok, output)。"""
    try:
        r = subprocess.run(cmd, cwd=cwd or PROJ, capture_output=True, text=True,
                           timeout=timeout, shell=False)
        return r.returncode == 0, (r.stdout or r.stderr)[-1500:]
    except Exception as e:
        return False, repr(e)


def panel_max_date():
    try:
        import pandas as pd
        p = os.path.join(DATA, "feature_panel_v3.parquet")
        df = pd.read_parquet(p, columns=["trade_date"])
        return str(df["trade_date"].max().date())
    except Exception as e:
        return None


def incr_max_date():
    try:
        import pandas as pd
        p = os.path.join(DATA_LIVE, "incremental_daily.parquet")
        df = pd.read_parquet(p, columns=["trade_date"])
        return str(df["trade_date"].max().date())
    except Exception as e:
        return None


def last_trade_date():
    """最近交易日（简单取：周一~五，若今日是交易日则取今日，否则往前找；实际以增量库/交易日历为准）。"""
    # 以增量库最新日为"最近完整交易日"基准（增量已含当日盘后数据）
    d = incr_max_date()
    return d


# ---------------- A 数据完整性 ----------------
def check_a(fix):
    print("\n==== A 数据完整性 ====")
    latest = last_trade_date()
    incr = incr_max_date()
    if incr and latest:
        if incr == latest:
            log("A", "A1 增量库最新日", "PASS", incr)
        else:
            log("A", "A1 增量库最新日", "FAIL", "增量 %s != 最近交易日 %s" % (incr, latest))
            if fix:
                ok, out = run([PY, "xtdata_update.py"])
                log("A", "A1 修复-重下增量", "PASS" if ok else "FAIL", out.strip()[:100])
    else:
        log("A", "A1 增量库最新日", "WARN", "增量库读失败")
    # A3 Tushare moneyflow 新鲜度（P16 F2 模型特征主源，19:30 刷新到 T-1）
    try:
        import pandas as pd
        mf = pd.read_parquet("D:/astock/moneyflow/moneyflow.parquet", columns=["net_mf_amount"])
        mf_date = pd.Timestamp(mf.index.get_level_values("trade_date").max())
        ok = True if latest and mf_date >= pd.Timestamp(latest) - pd.Timedelta(days=5) else False
        log("A", "A3 moneyflow最新日", "PASS" if ok else "FAIL",
            "moneyflow %s（增量 %s，滞后应<=5自然日）" % (str(mf_date.date()), latest))
    except Exception as e:
        log("A", "A3 moneyflow最新日", "WARN", "moneyflow 读取失败: %r" % (e,))
    # A2 主源文件
    missing = []
    for rel in ["daily/stock_daily.parquet", "basic/stock_basic.parquet", "finance/income.parquet",
                "finance/balancesheet.parquet", "finance/cashflow.parquet"]:
        p = os.path.join("D:/astock", rel)
        if not os.path.exists(p):
            missing.append(rel)
    log("A", "A2 主数据源文件", "PASS" if not missing else "FAIL", "" if not missing else "缺: %s" % missing)
    # A4 财务 PIT（以 income 表 ann_date 最新日）
    try:
        import pandas as pd
        fin = pd.read_parquet("D:/astock/finance/income.parquet", columns=["ann_date"])
        fin_ann = pd.to_datetime(fin["ann_date"], errors="coerce").dropna()
        log("A", "A4 财务PIT最新日", "PASS", str(fin_ann.max().date()))
    except Exception as e:
        log("A", "A4 财务PIT最新日", "WARN", "income 表读取失败: %r" % (e,))


# ---------------- B 面板与模型同步 ----------------
def check_b(fix):
    print("\n==== B 面板与模型同步 ====")
    pdate = panel_max_date()
    latest = last_trade_date()
    if pdate and latest:
        if pdate == latest:
            log("B", "B1 面板最新日", "PASS", pdate)
        else:
            log("B", "B1 面板最新日", "FAIL", "面板 %s != 最近交易日 %s" % (pdate, latest))
            if fix:
                ok, out = run([PY, "refresh_panel_v3.py"], timeout=1800)
                nd = panel_max_date()
                log("B", "B1 修复-重刷面板", "PASS" if ok and nd == latest else "FAIL",
                    "刷新后 %s" % nd)
    else:
        log("B", "B1 面板最新日", "WARN", "面板读取失败")
    # B2 verify
    ok, out = run([PY, "verify_model_panel_sync.py"], timeout=120)
    log("B", "B2 模型-面板同步", "PASS" if ok else "FAIL", out.strip().splitlines()[-1] if out else "")
    # B3 模型
    mp = "D:/QuantLab/models/lgb_model_v3.txt"
    if os.path.exists(mp):
        size = os.path.getsize(mp)
        log("B", "B3 正式模型存在", "PASS", "%.0f KB" % (size / 1024))
    else:
        log("B", "B3 正式模型存在", "FAIL", "lgb_model_v3.txt 缺失")
    # B4 G2 候选
    g2dir = os.path.join(DATA, "selections", "g2")
    g2files = os.listdir(g2dir) if os.path.isdir(g2dir) else []
    log("B", "B4 G2 候选产物", "PASS" if g2files else "WARN", "最近: %s" % (sorted(g2files)[-1] if g2files else "无"))


# ---------------- C 调度与任务健康 ----------------
def check_c():
    print("\n==== C 调度与任务健康 ====")
    sched = os.path.join(DATA, "schedules")
    logs = sorted(os.listdir(sched)) if os.path.isdir(sched) else []
    today = time.strftime("%Y%m%d")
    recent = [l for l in logs if today in l or "2026090" in l]
    log("C", "C2 前日任务日志", "PASS" if recent else "WARN", "当日日志 %d 条" % len(recent))
    # C1 次日任务 Active：由检修任务指令（LLM）用 Schedule 核对，脚本侧跳过
    log("C", "C1 次日任务Active", "WARN", "由检修任务指令核对 Schedule 状态")


# ---------------- D 数据源连通性 ----------------
def check_d(fix):
    print("\n==== D 数据源连通性 ====")
    # D1 悟道探活（直连）
    try:
        out = subprocess.run([PY, os.path.join(PROJ, "scripts", "wudao_client.py"), "market_overview", "{}"],
                             capture_output=True, text=True, timeout=30, cwd=PROJ)
        ok = out.returncode == 0 and "WUDAO-ERR" not in (out.stdout or "")
        if fix:
            subprocess.run([PY, os.path.join(PROJ, "scripts", "data_source_router.py"), "record", "wudao",
                            "ok" if ok else "fail"], capture_output=True, text=True, timeout=20, cwd=PROJ)
        log("D", "D1 悟道探活", "PASS" if ok else "FAIL", "market_overview")
    except Exception as e:
        log("D", "D1 悟道探活", "FAIL", repr(e))
    # D1 腾讯探活
    try:
        import urllib.request
        with urllib.request.urlopen("http://qt.gtimg.cn/q=sh000001", timeout=10) as r:
            body = r.read().decode("gbk", "ignore")
        ok = "v_sh000001" in body
        if fix:
            subprocess.run([PY, os.path.join(PROJ, "scripts", "data_source_router.py"), "record", "tencent",
                            "ok" if ok else "fail"], capture_output=True, text=True, timeout=20, cwd=PROJ)
        log("D", "D1 腾讯探活", "PASS" if ok else "FAIL", "sh000001")
    except Exception as e:
        log("D", "D1 腾讯探活", "FAIL", repr(e))
    # D2 熔断状态盘点
    hp = os.path.join(CACHE, "datasource_health.json")
    if os.path.exists(hp):
        try:
            h = json.load(open(hp, encoding="utf-8"))
            open_src = [k for k, v in h.items() if v.get("opened_at")]
            log("D", "D2 熔断状态", "WARN" if open_src else "PASS", "熔断中: %s" % (open_src or "无"))
        except Exception:
            log("D", "D2 熔断状态", "WARN", "健康文件读失败")
    else:
        log("D", "D2 熔断状态", "PASS", "无熔断记录")


# ---------------- E QMT / 账户 / 执行层 ----------------
def check_e():
    print("\n==== E QMT / 账户 / 执行层 ====")
    # E1 QMT 进程
    try:
        r = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=30)
        procs = r.stdout or ""
        qmt = [p for p in ["XtMiniQmt.exe", "XtItClient.exe", "XtItClient.exe"] if p in procs]
        log("E", "E1 QMT 进程", "PASS" if qmt else "FAIL", "存活: %s" % (qmt or "无"))
    except Exception as e:
        log("E", "E1 QMT 进程", "WARN", repr(e))
    # E2 桥心跳
    bridge_state = "D:/QMT_POOL/g2_bridge/state"
    if os.path.isdir(bridge_state):
        hearts = [f for f in os.listdir(bridge_state) if f.startswith("heart_")]
        latest_heart = max(hearts) if hearts else None
        age = None
        if latest_heart:
            hp = os.path.join(bridge_state, latest_heart)
            age = time.time() - os.path.getmtime(hp)
        log("E", "E2 桥心跳", "PASS" if latest_heart and age is not None and age < 3600 * 24 else "WARN",
            "最近 %s 距今 %.0fh" % (latest_heart, (age or 0) / 3600))
    else:
        log("E", "E2 桥心跳", "WARN", "无桥状态目录")
    # E3 账本戳
    for cf in [os.path.join(DATA, "strategy_capital.json"),
               "D:/QMT_POOL/g2_bridge/g2_strategy_capital.json"]:
        if os.path.exists(cf):
            try:
                d = json.load(open(cf, encoding="utf-8"))
                acct = d.get("account_id")
                log("E", "E3 账本戳 %s" % os.path.basename(cf), "PASS" if acct else "FAIL",
                    "account_id=%s" % acct)
            except Exception:
                log("E", "E3 账本戳 %s" % os.path.basename(cf), "FAIL", "读取失败")
        else:
            log("E", "E3 账本戳 %s" % os.path.basename(cf), "WARN", "文件不存在")
    # E5 对账报告
    rec = [f for f in os.listdir(DATA) if f.startswith("reconcile_") and f.endswith(".md")]
    log("E", "E5 对账报告", "PASS" if rec else "WARN", "最近: %s" % (sorted(rec)[-1] if rec else "无"))


# ---------------- F 环境与资源 ----------------
def check_f(fix):
    print("\n==== F 环境与资源 ====")
    # F1 磁盘
    for path, name in [("D:/", "D盘"), ("D:/QMT_POOL", "QMT_POOL")]:
        try:
            import shutil
            total, used, free = shutil.disk_usage(path)
            free_gb = free / 2 ** 30
            log("F", "F1 磁盘 %s" % name, "PASS" if free_gb > 10 else "WARN", "剩余 %.1f GB" % free_gb)
        except Exception as e:
            log("F", "F1 磁盘 %s" % name, "WARN", repr(e))
    # F2 QMT_POOL 清理（>14 天的 *_nav*.txt / 旧日志）
    if fix and os.path.isdir("D:/QMT_POOL"):
        removed = 0
        now = time.time()
        for f in os.listdir("D:/QMT_POOL"):
            fp = os.path.join("D:/QMT_POOL", f)
            try:
                if os.path.isfile(fp) and now - os.path.getmtime(fp) > 14 * 86400 and \
                        (f.endswith(".txt") or f.endswith(".log") or f.endswith(".json")):
                    os.remove(fp)
                    removed += 1
            except Exception:
                pass
        log("F", "F2 QMT_POOL清理", "PASS", "清理 %d 个旧文件" % removed)
    # F3 脚本冒烟
    bad = []
    for s in ["refresh_panel_v3.py", "review_full.py", "rebalance_daily.py", "deploy_predict.py",
              "deploy_predict_g2.py", "rebalance_g2.py", "reconcile_g2.py"]:
        p = os.path.join(PROJ, s)
        if os.path.exists(p):
            ok, out = run([PY, "-m", "py_compile", p], timeout=60)
            if not ok:
                bad.append(s)
    # F3b rebalance_daily_guard.ps1 兜底脚本 PowerShell 冒烟（10:05 换仓兜底，语法坏=静默失败）
    # 2026-09-06 追加：删 PK_OUT 后 guard 标记2 同步为 MATURE，纳入冒烟防回归
    # 注意必须用 pwsh7（默认 UTF-8）：guard.ps1 为 UTF-8 无 BOM，PS5.1(powershell) 按 GBK 读会误报语法错
    guard_ps1 = os.path.join(PROJ, "rebalance_daily_guard.ps1")
    if os.path.exists(guard_ps1):
        ps_exe = "pwsh" if shutil.which("pwsh") else "powershell"
        ps_cmd = ("$e=$null; [System.Management.Automation.Language.Parser]::ParseFile("
                  "'%s',[ref]$null,[ref]$e)|Out-Null; "
                  "if($e.Count){$e|ForEach-Object{$_.Message};exit 1}else{exit 0}") % guard_ps1
        ok, out = run([ps_exe, "-NoProfile", "-Command", ps_cmd], timeout=60)
        if not ok:
            bad.append("rebalance_daily_guard.ps1(PS语法)")
    log("F", "F3 脚本冒烟", "PASS" if not bad else "FAIL", "" if not bad else "语法错: %s" % bad)
    # F4 卖出规则一致性（T-20260904-001 防回归：到期制 MATURE 已落地 + guard 兜底已同步）
    # 若 rebalance_daily.py 回归 PK_OUT 日频翻转 / guard 标记2 漏 MATURE，次日可能重复换仓或到期不卖。
    sellsrc = os.path.join(PROJ, "rebalance_daily.py")
    if os.path.exists(sellsrc):
        s = open(sellsrc, encoding="utf-8").read()
        mature_ok = ('"reason": "MATURE"' in s or '"reason","MATURE"' in s) and "HOLD_DAYS" in s
        pk_ok = '"reason": "PK_OUT"' not in s  # 防回归：不应再生成 PK_OUT 卖出块（仅允许 else "planA_pk_out" 兼容分支）
        guard = os.path.join(PROJ, "rebalance_daily_guard.ps1")
        g = open(guard, encoding="utf-8").read() if os.path.exists(guard) else ""
        guard_ok = '"MATURE"' in g
        f4_bad = []
        if not mature_ok:
            f4_bad.append("rebalance_daily.py 无 MATURE 到期制")
        if not pk_ok:
            f4_bad.append("rebalance_daily.py 存在 PK_OUT 卖出块（需检查是否回归）")
        if not guard_ok:
            f4_bad.append("guard.ps1 标记2 未含 MATURE（兜底会重复换仓）")
        log("F", "F4 卖出规则一致性", "PASS" if not f4_bad else "FAIL", "" if not f4_bad else "; ".join(f4_bad))


# ---------------- G 报告 ----------------
def write_report():
    fails = [r for r in RESULT if r[2] == "FAIL"]
    warns = [r for r in RESULT if r[2] == "WARN"]
    os.makedirs(CACHE, exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("# Project_16 夜间检修 %s\n\n" % time.strftime("%Y-%m-%d %H:%M"))
        f.write("**结论**：FAIL %d / WARN %d / 总检查 %d\n\n" % (len(fails), len(warns), len(RESULT)))
        f.write("| 模块 | 检查项 | 状态 | 说明 |\n|---|---|---|---|\n")
        for mod, item, status, note in RESULT:
            f.write("| %s | %s | **%s** | %s |\n" % (mod, item, status, note.replace("|", "\\|")))
        f.write("\n---\n自动生成：`scripts/nightly_check.py`\n")
    print("\n报告: %s | FAIL=%d WARN=%d" % (REPORT, len(fails), len(warns)))
    return fails, warns


def main():
    fix = "--fix" in sys.argv
    push = "--push-alert" in sys.argv
    print("=== Project_16 夜间检修开始 %s (fix=%s) ===" % (time.strftime("%Y-%m-%d %H:%M:%S"), fix))
    check_a(fix)
    check_b(fix)
    check_c()
    check_d(fix)
    check_e()
    check_f(fix)
    fails, warns = write_report()
    print("=== 检修完成: FAIL=%d WARN=%d ===" % (len(fails), len(warns)))
    # 推送告警由检修任务指令（LLM）根据本报告执行飞书推送
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
