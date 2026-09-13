# -*- coding: utf-8 -*-
"""Project_16 夜间检修（2026-09-03 建，2026-09-13 按 DE 体检 T-20260912-003 升级）。

每日 01:00 执行，确保次日策略可正常运行、拿到的数据最新、面板/模型及时更新。
覆盖 A-G 模块：能自动修则修（--fix），修不了告警 + 盘前晨报，绝不阻塞次日任务。

用法:
  python scripts/nightly_check.py [--fix] [--push-alert]
  --fix         自动修复（面板重刷 / 增量重下 / 熔断恢复）
  --push-alert  有 FAIL 时飞书告警
输出: data/cache/nightly_check_<date>.md（逐项 PASS/FAIL/修复）

2026-09-13 升级（T-20260912-003，DE 体检 de_nightly_review_20260912.md）：
  P0-1 A1/B1 数据新鲜度改用外部日历锚（is_trade_day），增量库断流不再恒真；
  P0-2 E2 三桥化（G2/ENS/V13）+ JSON 内容校验（NUL/坏文件 FAIL）+ 交易日感知阈值；
  P1-3 C1 关键计划任务白名单自动核对（替代纯人工）；
  P1-4 F4 卖出规则改语义匹配（T0_LIQUIDATE 三元表达式不再误报）；
  P1-5 --push-alert 接 notify_feishu（FAIL 即推，不再静默）；
  P1-6 E3 补 ENS/V13/G2 三资金池账本戳，E5 三对账目录（V1.3/G2/ENS）；
  P1-7 B5 enh 面板 / B6 merged_daily_full / B7 ENS 候选 三项新检查，B4 修 _bak 排序误导；
  P1-8 F3 冒烟补 ENS 链（rebalance_ens/reconcile_ens/check_bridge_heartbeat/heartbeat_alarm.ps1）；
  P1-9 D2 熔断按 600s 窗口判真熔断（过期不再假 WARN）；
  P1-10 run()/探活 subprocess 统一 UTF-8 解码（修 GBK 崩溃）。
"""
import datetime
import json
import os
import re
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

# P0-2: 三桥心跳状态目录（V13 前缀为 heart_v13_）
BRIDGES = [
    ("G2", "D:/QMT_POOL/g2_bridge/state", "heart_"),
    ("ENS", "D:/QMT_POOL/p16_ensemble_bridge/state", "heart_"),
    ("V13", "D:/QMT_POOL/p16_v13_risk/state", "heart_v13_"),
]
# P1-3: 三策略每日链路关键计划任务白名单（缺失/禁用即 FAIL）
EXPECTED_TASKS = [
    "Quant_P16_NightlyCheck_0100",
    "Quant_P16_CfgPremarket_0900",
    "Quant_V13_Rebalance_Guard",
    "Quant_Monitor_0945",
    "Quant_P16_DE_V13_Compare",
    "Quant_P16_DE_G2_Compare",
    "Quant_P16_DE_ENS_Compare",
    "Quant_P16_DE_Report_Midday",
    "Quant_P16_DE_Report_Close",
    "Quant_P16_G2Candidates_Night",
    "Quant_Tushare_Moneyflow_Refresh",
    "Quant_P16_Heartbeat_Alarm",
    "quant_daily_update",
    "quant_weekly_retrain",
]
# P1-9: 熔断窗口（秒），与 data_source_router 熔断期一致（300s，取 600s 上界容差）
CIRCUIT_WINDOW = 600
# P1-7: enh 面板允许滞后自然日（>7 即 FAIL，训练特征陈旧）
ENH_MAX_LAG_DAYS = 7

RESULT = []  # (模块, 项, 状态[PASS/FAIL/WARN], 说明)


def log(mod, item, status, note=""):
    RESULT.append((mod, item, status, note))
    print("[%s] [%s] %s %s" % (status, mod, item, note))


def run(cmd, cwd=None, timeout=900):
    """subprocess 运行，返回 (ok, output)。统一 UTF-8 解码（P1-10，修 GBK 崩溃）。"""
    try:
        r = subprocess.run(cmd, cwd=cwd or PROJ, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
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


def panel_enh_max_date():
    """enh 面板最新日（P1-7；writer=refresh_panel_enh.py，T-20260912-004 已落地刷至 09-11）。"""
    try:
        import pandas as pd
        p = os.path.join(DATA, "feature_panel_v3_enh.parquet")
        df = pd.read_parquet(p, columns=["trade_date"])
        return str(df["trade_date"].max().date())
    except Exception as e:
        return None


def merged_max_date():
    """merged_daily_full 最新日（P1-7；纸面收益回填唯一数据源，T-20260910-105；trade_date 为索引）。"""
    try:
        import pandas as pd
        p = os.path.join(DATA_LIVE, "merged_daily_full.parquet")
        df = pd.read_parquet(p, columns=["open"])
        return str(pd.Timestamp(df.index.get_level_values("trade_date").max()).date())
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


def recent_trade_date():
    """最近应出数据的交易日（P0-1 外部锚：交易日历 + 节假日表，不依赖增量库/面板自身）。
    今天若为交易日则返回今天；否则回退最近交易日。日历不可用时降级增量库锚。"""
    try:
        sys.path.insert(0, PROJ)
        import is_trade_day as ITD
        cal = ITD.load_calendar()
        today = datetime.date.today()
        if ITD.is_trade_day(today, cal)["is_trade_day"]:
            return today.isoformat()
        d = today
        for _ in range(20):
            d -= datetime.timedelta(days=1)
            if ITD.is_trade_day(d, cal)["is_trade_day"]:
                return d.isoformat()
    except Exception as e:
        print("[warn] 交易日历不可用，降级增量库锚: %r" % (e,))
    return incr_max_date()


def _sel_latest(sel_dir):
    """候选目录最新日期 YYYYMMDD（P1-7：忽略 _bak_* 目录与异常文件，修 B4 排序误导）。"""
    try:
        if not os.path.isdir(sel_dir):
            return None
        files = [f for f in os.listdir(sel_dir)
                 if len(f) >= 8 and f[:8].isdigit() and os.path.isfile(os.path.join(sel_dir, f))]
        return max(f[:8] for f in files) if files else None
    except Exception:
        return None


# ---------------- A 数据完整性 ----------------
def check_a(fix):
    print("\n==== A 数据完整性 ====")
    latest = recent_trade_date()
    incr = incr_max_date()
    if incr and latest:
        if incr >= latest:
            log("A", "A1 增量库最新日", "PASS", incr)
        else:
            log("A", "A1 增量库最新日", "FAIL", "增量 %s < 最近交易日 %s（数据断流，需 xtdata_update）" % (incr, latest))
            if fix:
                ok, out = run([PY, "xtdata_update.py"])
                nd = incr_max_date()
                log("A", "A1 修复-重下增量", "PASS" if ok and nd else "FAIL", out.strip()[:100])
    else:
        log("A", "A1 增量库最新日", "WARN", "增量库读失败")
    # A3 Tushare moneyflow 新鲜度（P16 F2 模型特征主源，19:30 刷新到 T-1）
    try:
        import pandas as pd
        mf = pd.read_parquet("D:/astock/moneyflow/moneyflow.parquet", columns=["net_mf_amount"])
        mf_date = pd.Timestamp(mf.index.get_level_values("trade_date").max())
        ok = True if latest and mf_date >= pd.Timestamp(latest) - pd.Timedelta(days=5) else False
        log("A", "A3 moneyflow最新日", "PASS" if ok else "FAIL",
            "moneyflow %s（最近交易日 %s，滞后应<=5自然日）" % (str(mf_date.date()), latest))
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
    latest = recent_trade_date()
    if pdate and latest:
        if pdate >= latest:
            log("B", "B1 面板最新日", "PASS", pdate)
        else:
            log("B", "B1 面板最新日", "FAIL", "面板 %s < 最近交易日 %s（增量断流或刷新未跑）" % (pdate, latest))
            if fix:
                ok, out = run([PY, "refresh_panel_v3.py"], timeout=1800)
                nd = panel_max_date()
                log("B", "B1 修复-重刷面板", "PASS" if ok and nd and nd >= latest else "FAIL",
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
    # B4 G2 候选（P1-7：忽略 _bak_* 目录，取候选文件最大日期）
    g2d = _sel_latest(os.path.join(DATA, "selections", "g2"))
    log("B", "B4 G2 候选产物", "PASS" if g2d else "WARN", "最近: %s" % (g2d or "无"))
    # B5 enh 面板新鲜度（P1-7：writer=refresh_panel_enh.py 每日维护，T-20260912-004）
    enh = panel_enh_max_date()
    if enh and latest:
        try:
            lag = (datetime.date.fromisoformat(latest) - datetime.date.fromisoformat(enh)).days
            log("B", "B5 enh面板最新日", "PASS" if lag <= ENH_MAX_LAG_DAYS else "FAIL",
                "enh %s（最近交易日 %s，滞后 %d 天>阈值%d）" % (enh, latest, lag, ENH_MAX_LAG_DAYS))
        except Exception:
            log("B", "B5 enh面板最新日", "WARN", enh)
    else:
        log("B", "B5 enh面板最新日", "WARN", "enh 面板读取失败")
    # B6 merged_daily_full 新鲜度（P1-7：纸面收益唯一数据源）
    mfd = merged_max_date()
    if mfd and latest:
        log("B", "B6 merged_daily_full", "PASS" if mfd >= latest else "FAIL",
            "merged %s（最近交易日 %s）" % (mfd, latest))
    else:
        log("B", "B6 merged_daily_full", "WARN", "merged 读取失败")
    # B7 ENS 候选（P1-7）
    ensd = _sel_latest(os.path.join(DATA, "selections", "g2_ens"))
    if ensd and latest:
        ok = ensd >= latest.replace("-", "")
        log("B", "B7 ENS 候选产物", "PASS" if ok else "WARN",
            "最近: %s（最近交易日 %s）" % (ensd, latest))
    else:
        log("B", "B7 ENS 候选产物", "WARN", "最近: %s" % (ensd or "无"))


# ---------------- C 调度与任务健康 ----------------
def check_c():
    print("\n==== C 调度与任务健康 ====")
    # C1 关键计划任务白名单（P1-3 自动化；实证：Heartbeat_Alarm 缺失 9 天无人发现）
    missing = []
    for tn in EXPECTED_TASKS:
        try:
            r = subprocess.run(["schtasks", "/query", "/tn", tn], capture_output=True,
                               timeout=30, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                missing.append(tn)
        except Exception:
            missing.append(tn)
    log("C", "C1 次日任务Active", "PASS" if not missing else "FAIL",
        "" if not missing else "缺失/禁用: %s" % missing)
    # C2 前日任务日志（口径修复：近 2 天内写过日志文件即 PASS，原 "2026090" 前缀只覆盖 9 月上旬）
    sched = os.path.join(DATA, "schedules")
    now = time.time()
    try:
        logs = os.listdir(sched) if os.path.isdir(sched) else []
        recent = [l for l in logs
                  if now - os.path.getmtime(os.path.join(sched, l)) < 2 * 86400]
    except Exception:
        recent = []
    log("C", "C2 前日任务日志", "PASS" if recent else "WARN", "近2日日志 %d 条" % len(recent))


# ---------------- D 数据源连通性 ----------------
def check_d(fix):
    print("\n==== D 数据源连通性 ====")
    # D1 悟道探活（直连）
    try:
        out = subprocess.run([PY, os.path.join(PROJ, "scripts", "wudao_client.py"), "market_overview", "{}"],
                             capture_output=True, text=True, timeout=30, cwd=PROJ,
                             encoding="utf-8", errors="replace")
        ok = out.returncode == 0 and "WUDAO-ERR" not in (out.stdout or "")
        if fix:
            subprocess.run([PY, os.path.join(PROJ, "scripts", "data_source_router.py"), "record", "wudao",
                            "ok" if ok else "fail"], capture_output=True, text=True, timeout=20, cwd=PROJ,
                           encoding="utf-8", errors="replace")
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
                            "ok" if ok else "fail"], capture_output=True, text=True, timeout=20, cwd=PROJ,
                           encoding="utf-8", errors="replace")
        log("D", "D1 腾讯探活", "PASS" if ok else "FAIL", "sh000001")
    except Exception as e:
        log("D", "D1 腾讯探活", "FAIL", repr(e))
    # D2 熔断状态（P1-9：仅 opened_at 距今 <600s 才算真熔断；过期=已自动恢复，不再假 WARN）
    hp = os.path.join(CACHE, "datasource_health.json")
    if os.path.exists(hp):
        try:
            h = json.load(open(hp, encoding="utf-8"))
            now = time.time()
            tripped = [k for k, v in h.items()
                       if v.get("opened_at") and now - v["opened_at"] < CIRCUIT_WINDOW]
            expired = [k for k, v in h.items()
                       if v.get("opened_at") and now - v["opened_at"] >= CIRCUIT_WINDOW]
            if tripped:
                log("D", "D2 熔断状态", "WARN", "真熔断中(≤%ss): %s" % (CIRCUIT_WINDOW, tripped))
            elif expired:
                log("D", "D2 熔断状态", "PASS", "熔断已过期自动恢复: %s" % expired)
            else:
                log("D", "D2 熔断状态", "PASS", "无熔断记录")
        except Exception:
            log("D", "D2 熔断状态", "WARN", "健康文件读失败")
    else:
        log("D", "D2 熔断状态", "PASS", "无熔断记录")


# ---------------- E QMT / 账户 / 执行层 ----------------
def _bridge_heart(state_dir, prefix):
    """返回 (最新心跳文件名, 距今秒, 内容dict或None, 坏标记)。P0-2。"""
    if not os.path.isdir(state_dir):
        return None, None, None, False
    hearts = [f for f in os.listdir(state_dir) if f.startswith(prefix)]
    if not hearts:
        return None, None, None, False
    lh = max(hearts)
    hp = os.path.join(state_dir, lh)
    age = time.time() - os.path.getmtime(hp)
    data = None
    bad = False
    try:
        with open(hp, encoding="utf-8") as f:
            data = json.load(f)
        if not data or not data.get("last_heartbeat"):
            bad = True
    except Exception:
        bad = True
    return lh, age, data, bad


def check_e():
    print("\n==== E QMT / 账户 / 执行层 ====")
    # E1 QMT 进程
    try:
        r = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        procs = r.stdout or ""
        want = ["XtMiniQmt.exe", "XtItClient.exe"]
        qmt = sorted(set(p for p in want if p in procs))
        log("E", "E1 QMT 进程", "PASS" if qmt else "FAIL", "存活: %s" % (qmt or "无"))
    except Exception as e:
        log("E", "E1 QMT 进程", "WARN", repr(e))
    # E2 桥心跳（P0-2 三桥化 + JSON 内容校验 + 交易日感知阈值）
    latest = recent_trade_date()
    try:
        days_since = (datetime.date.today() - datetime.date.fromisoformat(latest)).days
    except Exception:
        days_since = 3
    threshold = 96 * 3600 if days_since > 2 else 48 * 3600  # 长假 96h / 正常 48h
    for name, st_dir, prefix in BRIDGES:
        lh, age, data, bad = _bridge_heart(st_dir, prefix)
        if bad:
            log("E", "E2 桥心跳 %s" % name, "FAIL", "心跳文件损坏/内容空（最近 %s，需查桥进程）" % (lh or "无"))
        elif not lh:
            log("E", "E2 桥心跳 %s" % name, "WARN", "无心跳文件")
        elif age is not None and age < threshold:
            log("E", "E2 桥心跳 %s" % name, "PASS", "最近 %s 距今 %.0fh build=%s" % (
                lh, age / 3600, (data or {}).get("build_tag", "?")))
        else:
            log("E", "E2 桥心跳 %s" % name, "WARN", "最近 %s 距今 %.0fh（>阈值 %dh，桥可能停）" % (
                lh, (age or 0) / 3600, threshold / 3600))
    # E3 账本戳（P1-6：V1.3/G2/ENS 三资金池 account_id 校验）
    caps = [("V13", os.path.join(DATA, "strategy_capital.json")),
            ("G2", "D:/QMT_POOL/g2_bridge/g2_strategy_capital.json"),
            ("ENS", "D:/QMT_POOL/p16_ensemble_bridge/ens_strategy_capital.json")]
    for name, cf in caps:
        if os.path.exists(cf):
            try:
                d = json.load(open(cf, encoding="utf-8"))
                acct = d.get("account_id")
                log("E", "E3 账本戳 %s" % name, "PASS" if acct else "FAIL", "account_id=%s" % acct)
            except Exception:
                log("E", "E3 账本戳 %s" % name, "FAIL", "读取失败")
        else:
            log("E", "E3 账本戳 %s" % name, "WARN", "文件不存在")
    # E5 对账报告（P1-6：V1.3/G2/ENS 三目录，各自最新日 >= 最近交易日）
    rec_dirs = [("V1.3", DATA), ("G2", os.path.join(DATA, "reconcile_g2")),
                ("ENS", os.path.join(DATA, "reconcile_g2_ens"))]
    latest_y8 = latest.replace("-", "") if latest else ""
    for name, d in rec_dirs:
        dates = []
        if os.path.isdir(d):
            for f in os.listdir(d):
                m = re.search(r"(\d{8})", f)
                if m and f.endswith(".md"):
                    dates.append(m.group(1))
        last = max(dates) if dates else None
        ok = bool(last and latest_y8 and last >= latest_y8)
        log("E", "E5 对账 %s" % name, "PASS" if ok else "WARN",
            "最近: %s%s" % (last or "无", "" if ok else "（滞后于最近交易日 %s）" % latest_y8))
    # E6 双调度器一致性巡检（2026-09-13 立，T-20260913-001 E3；T-20260910-008 教训固化）
    # 读 data/tw_schedule_snapshot.json（TW 侧任务清单快照，agent 会话手动更新）：
    #   ① 标记 conflict_with 的任务若仍 Active → FAIL（双写风险，如 eda9b0c3 成本锚应已删除）
    #   ② 快照缺失/损坏 → WARN（人工核对 TW 侧清单）
    snap = os.path.join(DATA, "tw_schedule_snapshot.json")
    if not os.path.exists(snap):
        log("E", "E6 TW调度快照", "WARN", "快照文件缺失（data/tw_schedule_snapshot.json），无法核对双写风险")
    else:
        try:
            d = json.load(open(snap, encoding="utf-8"))
            # P2-6：快照新鲜度校验——手动维护的快照过期会基于过期清单假 PASS
            snap_age_ok = True
            try:
                import datetime as _dt
                gen = _dt.datetime.strptime(d.get("generated_at", ""), "%Y-%m-%d %H:%M:%S")
                snap_age_ok = (_dt.datetime.now() - gen).days <= 7
            except Exception:
                snap_age_ok = False
            risks = [t for t in d.get("tasks", []) if t.get("conflict_with") and t.get("status") == "Active"]
            if risks:
                log("E", "E6 TW调度冲突", "FAIL",
                    "冲突任务仍 Active: %s（%s 应删除/暂停，双写 %s 风险）" % (
                        ", ".join(t["id"] for t in risks),
                        ", ".join(t["name"] for t in risks),
                        ", ".join(t["conflict_with"] for t in risks)))
            elif not snap_age_ok:
                log("E", "E6 TW调度快照", "WARN",
                    "快照已过期（generated_at=%s，>7 天未更新），请用 Schedule list 刷新后再判定" % d.get("generated_at"))
            else:
                log("E", "E6 TW调度一致性", "PASS", "快照 %d 项新鲜，无 Active 冲突任务（owner 边界见快照）" % len(d.get("tasks", [])))
        except Exception as e:
            log("E", "E6 TW调度快照", "WARN", "快照解析失败: %s" % e)


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
    # F3 脚本冒烟（P1-8：补 ENS 链 + 心跳告警链）
    bad = []
    for s in ["refresh_panel_v3.py", "refresh_panel_enh.py", "review_full.py", "rebalance_daily.py",
              "deploy_predict.py", "deploy_predict_g2.py", "rebalance_g2.py", "reconcile_g2.py",
              "reconcile_ens.py", "rebalance_ens.py", "check_bridge_heartbeat.py",
              "merge_live_features.py", "build_g2_daily.py"]:
        p = os.path.join(PROJ, s)
        if os.path.exists(p):
            ok, out = run([PY, "-m", "py_compile", p], timeout=60)
            if not ok:
                bad.append(s)
    # F3b 兜底/告警 ps1 语法冒烟（UTF-8 无 BOM，必须 pwsh7 解析，PS5.1 按 GBK 会误报）
    for ps1 in ["rebalance_daily_guard.ps1", "heartbeat_alarm.ps1"]:
        gp = os.path.join(PROJ, ps1)
        if os.path.exists(gp):
            ps_exe = "pwsh" if shutil.which("pwsh") else "powershell"
            ps_cmd = ("$e=$null; [System.Management.Automation.Language.Parser]::ParseFile("
                      "'%s',[ref]$null,[ref]$e)|Out-Null; "
                      "if($e.Count){$e|ForEach-Object{$_.Message};exit 1}else{exit 0}") % gp
            ok, out = run([ps_exe, "-NoProfile", "-Command", ps_cmd], timeout=60)
            if not ok:
                bad.append(ps1 + "(PS语法)")
    log("F", "F3 脚本冒烟", "PASS" if not bad else "FAIL", "" if not bad else "语法错: %s" % bad)
    # F4 卖出规则一致性（P1-4 语义匹配：T0_LIQUIDATE 三元表达式不应误报）
    # T-20260904-001 到期制 MATURE 落地 + guard 标记2 MATURE 同步，防 PK_OUT 回归。
    sellsrc = os.path.join(PROJ, "rebalance_daily.py")
    if os.path.exists(sellsrc):
        s = open(sellsrc, encoding="utf-8").read()
        mature_ok = "HOLD_DAYS" in s and "MATURE" in s   # 语义：到期制机制存在
        pk_ok = '"reason": "PK_OUT"' not in s            # 防回归：不应再生成 PK_OUT 卖出块
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
        f.write("\n---\n自动生成：`scripts/nightly_check.py`（2026-09-13 T-20260912-003 升级）\n")
    print("\n报告: %s | FAIL=%d WARN=%d" % (REPORT, len(fails), len(warns)))
    return fails, warns


def _push_fail(fails):
    """P1-5：FAIL 时飞书告警（notify_feishu，lark-cli bot 私聊），失败仅记录。"""
    try:
        sys.path.insert(0, PROJ)
        from qmt_bridge_client import notify_feishu
        summary = "夜间检修 %s FAIL %d 项\n%s" % (
            time.strftime("%Y-%m-%d"), len(fails),
            "\n".join("· %s: %s" % (r[1], (r[3] or "")[:80]) for r in fails[:5]))
        notify_feishu(summary)
    except Exception as e:
        print("[告警] 飞书推送失败: %r" % (e,))


def main():
    fix = "--fix" in sys.argv
    push = "--push-alert" in sys.argv
    print("=== Project_16 夜间检修开始 %s (fix=%s push=%s) ===" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), fix, push))
    check_a(fix)
    check_b(fix)
    check_c()
    check_d(fix)
    check_e()
    check_f(fix)
    fails, warns = write_report()
    if push and fails:
        _push_fail(fails)
    print("=== 检修完成: FAIL=%d WARN=%d ===" % (len(fails), len(warns)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
