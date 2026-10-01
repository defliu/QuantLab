# coding: utf-8
"""G2 每日选股复跑对比（T-20260919-002 每日复跑双重保障机制，2026-09-20 立）。

与 A/B/C/D 四道防线的分工：
  A 静态死代码扫描 / B 数据健康监控（产物） / C 低频快照回放回归（周） / D FAIL-LOUD（运行期内嵌）
  —— 四者都查「事后产物」，抓不到「当日运行时执行偏差」：当日主源失效被新浪兜底、
     数据扰动、代码回退等导致选股偏差时，落盘产物仍健康、后续防线不报。
本脚本每天盘后（20:10，紧随 20:05 候选）把当日候选「原样复跑一遍」对比当日产物，堵该盲区：

  1) 读留档快照 data_live/snapshot_g2_<runDay>.parquet（g2_candidates_night.ps1 已固化，
     复跑用它保证可复现——次日 build_g2_daily 会重建并覆盖 g2_latest_features.parquet）
  2) 备份当日基准 CSV <runDay>_g2_top10.csv → <runDay>_g2_top10.csv.compare_base
     （复跑会用同名路径重写，必须先备份再重放）
  3) subprocess 重放 deploy_predict_g2.py --date <runDay> --top 10（cwd=项目目录，同夜间链路）
  4) 对比基准 vs 复跑：
     - 解析复跑 stdout 的「F2 主源=」：Tushare（主源，确定性口径）→ ts_code 集合/排序 +
       SC_F2/SC_F5 逐位严格一致，任一不一致 = error 告警（当日运行时可能主源失效/数据扰动/代码回退）
     - 新浪当日（兜底，盘中实时值盘后不可复现）→ 记录差异不告警（仅写日志，不阻断）
     - 无法判定主源但结果不一致 → warn（保守）
  5) 退出码：0=PASS（含新浪兜底一致/记录差异/双方均空候选） 1=error（复跑失败或主源口径不一致）
     2=warn（降级健康校验 / 主源未知）
  6) 通知（2026-09-20 诚哥拍板「失败成功都通知」）：PASS/新浪兜底差异推 info 成功卡，error 推红卡，
     warn 推橙卡，SKIP（非交易日）与降级健康校验路径不重复推（降级已推 warn）。

无留档快照时降级：用当前快照重放，仅做健康校验（不判差异），标注「单源结论（仅复跑，
未经留档快照对比）」——既不静默跳过、也不误判差异。快照缺失本身 warn 告警（留档步骤可能故障）。

用法：
  python compare_g2_daily.py                 # 默认对比今天（运行日）
  python compare_g2_daily.py --date 20260918 # 指定运行日（调试/补跑）
  python compare_g2_daily.py --no-send       # 只打印不推送（调试）
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

import pandas as pd

P16 = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
SELECT_DIR = os.path.join(P16, "data", "selections", "g2")
ENS_SELECT_DIR = os.path.join(P16, "data", "selections", "g2_ens")
LIVE_DIR = os.path.join(P16, "data_live")
SNAP_CUR = os.path.join(LIVE_DIR, "g2_latest_features.parquet")
DEPLOY = os.path.join(P16, "deploy_predict_g2.py")
ALERT = os.path.join(P16, "push_alert_card.py")
LOG = os.path.join(P16, "data", "schedules", "compare_g2_daily.log")
PY = sys.executable

HEALTH_COLS = ["ts_code", "SC_F1", "SC_F2", "SC_F3", "SC_F4", "SC_F5", "SC_F6"]


def _log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def _push(title, body, level, no_send):
    cmd = [PY, ALERT, "--title", title, "--body", body, "--level", level]
    if no_send:
        cmd.append("--no-send")
    print("[PUSH] %s" % " ".join(cmd))
    if not no_send:
        subprocess.run(cmd, cwd=P16, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _load_csv(path):
    if not os.path.exists(path):
        return None
    return pd.read_csv(path, encoding="utf-8-sig")


_BACKUP_SEEN = {}   # 同进程内已备份路径 → 当时原件是否存在（F7 复核补丁 2026-09-29）


def _backup_baseline(path, tag):
    """复跑前备份原件 → <path>.compare_base；返回备份前原件是否存在。
    （F7 修复配套，2026-09-28）
    同进程内同路径只备份一次（2026-09-29 DE 复核补丁）：main 的 G2 臂与 _ens_compare 的
    ENS 臂会对 G2 主目录同一 csv/md 各备份一次；若 G2 臂恢复失败（文件锁等，per-item
    try/except 只告警不中断），盘上留的是复跑产物，ENS 臂二次 copy2 会把它当原件固化进
    .compare_base —— 最后一份好原件被覆盖，污染永久化。首次备份优先（first-wins）杜绝该场景。"""
    if path in _BACKUP_SEEN:
        return _BACKUP_SEEN[path]
    existed = os.path.exists(path)
    if existed:
        shutil.copy2(path, path + ".compare_base")
        _log("[%s] 备份基准 %s -> %s" % (tag, os.path.basename(path), os.path.basename(path) + ".compare_base"))
    _BACKUP_SEEN[path] = existed
    return existed


def _restore_baseline(items, tag):
    """F7 修复（2026-09-28，候选可审计性）：复跑会用同名路径重写历史候选，
    对比完成后必须恢复原件，否则盘上留下的是复跑产物而非夜间正品 → 历史候选
    不可审计（实证：20260924_g2_top10.csv 于 2026-09-28 16:48 被 --date 补跑覆写，
    内容由 002617/002569 变为 603272/003029）。
    items: [(path, 复跑前原件是否存在), ...]
    - 原件存在过 → 从 .compare_base 恢复
    - 原件本不存在（红线空仓日）而复跑新建 → 删除复跑产物，保持无候选状态
    恢复失败只告警不阻断（对比结论已得出），日志标注审计注意。"""
    for path, existed in items:
        try:
            backup = path + ".compare_base"
            if existed:
                if os.path.exists(backup):
                    shutil.copy2(backup, path)
                    _log("[%s] 恢复原件 %s（复跑产物不落盘）" % (tag, os.path.basename(path)))
                else:
                    _log("!! [%s] %s 原件存在过但备份 %s 缺失，无法恢复——盘上是复跑产物，审计时注意"
                         % (tag, os.path.basename(path), os.path.basename(backup)))
            else:
                if os.path.exists(path):
                    os.remove(path)
                    _log("[%s] 移除复跑产物 %s（当日原为红线空仓，保持无候选状态）" % (tag, os.path.basename(path)))
        except Exception as e:
            _log("!! [%s] 恢复 %s 失败（盘上可能留有复跑产物，审计时注意）: %s" % (tag, os.path.basename(path), e))


def _safe_print(text):
    """重放 stdout 按 utf-8/replace 解码后可能含 \\ufffd，在 GBK 控制台 print 会
    UnicodeEncodeError（2026-09-29 E2E 实测）——降级为可编码字符再打印，绝不让
    打印崩溃中断「重放→恢复」链路。"""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(enc, "replace").decode(enc))


def _replay(date_str):
    cmd = [PY, DEPLOY, "--date", date_str, "--top", "10"]
    r = subprocess.run(cmd, cwd=P16, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def _f2_source(out):
    """解析复跑 stdout 的 F2 主源标志：tushare / sina / unknown。"""
    for line in out.splitlines():
        if "F2 主源=" in line:
            if "Tushare每日快照" in line:
                return "tushare"
            if "新浪当日" in line:
                return "sina"
    return "unknown"


def _cmp(base, rep):
    """对比基准 vs 复跑，返回 (severity, msg)。severity: pass / error / diff。"""
    if base is None and rep is None:
        return "pass", "双方均无候选（红线空仓一致性，PASS）"
    if base is None:
        return "error", "当日无候选（红线空仓）但复跑产生了候选 —— 运行时执行偏差，需核查"
    if rep is None:
        return "error", "当日有候选但复跑无候选 —— 复跑链路异常（数据/代码变更），需核查"
    bl = base["ts_code"].astype(str).tolist()
    rl = rep["ts_code"].astype(str).tolist()
    diffs = []
    if bl != rl:
        diffs.append("ts_code 集合/排序不一致：基准 %s vs 复跑 %s" % (bl, rl))
    for c in ("SC_F2", "SC_F5"):
        if c not in base.columns or c not in rep.columns:
            diffs.append("%s 列缺失（基准=%s 复跑=%s）" % (c, c in base.columns, c in rep.columns))
            continue
        if base[c].tolist() != rep[c].tolist():
            diffs.append("%s 不一致：基准 %s vs 复跑 %s" % (c, base[c].tolist(), rep[c].tolist()))
    if diffs:
        return "diff", "；".join(diffs)
    return "pass", "完全一致（%d 只，ts_code 排序 / SC_F2 / SC_F5 逐位相同）" % len(base)


def _replay_ens(date_str):
    """融合(ENS)复跑：同快照重放 deploy_predict_g2.py --top 10 --ensemble --ens-live。"""
    cmd = [PY, DEPLOY, "--date", date_str, "--top", "10", "--ensemble", "--ens-live"]
    r = subprocess.run(cmd, cwd=P16, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def _cmp_ens(base, rep):
    """对比 ENS 基准 vs 复跑（ts_code 集合/排序 + total_new 逐位）。"""
    if base is None and rep is None:
        return "pass", "双方均无融合候选（红线空仓一致性，PASS）"
    if base is None:
        return "error", "当日无融合候选（红线空仓）但复跑产生了融合候选 —— 运行时执行偏差，需核查"
    if rep is None:
        return "error", "当日有融合候选但复跑无融合候选 —— 复跑链路异常（数据/代码变更），需核查"
    bl = base["ts_code"].astype(str).tolist()
    rl = rep["ts_code"].astype(str).tolist()
    diffs = []
    if bl != rl:
        diffs.append("ts_code 集合/排序不一致：基准 %s vs 复跑 %s" % (bl, rl))
    if "total_new" not in base.columns or "total_new" not in rep.columns:
        diffs.append("total_new 列缺失（基准=%s 复跑=%s）" % ("total_new" in base.columns, "total_new" in rep.columns))
    else:
        bt = base["total_new"].tolist()
        rt = rep["total_new"].tolist()
        if bt != rt:
            diffs.append("total_new 不一致：基准 %s vs 复跑 %s" % (bt, rt))
    if diffs:
        return "diff", "；".join(diffs)
    return "pass", "融合完全一致（%d 只，ts_code 排序 / total_new 逐位相同）" % len(base)


def _ens_compare(run_day, no_send):
    """融合(ENS)复跑对比（T-20260920-001 扩展）：与 G2 同留档快照，独立重放并分级。
    返回退出码（0=PASS 含新浪兜底一致/记录差异 1=error 2=warn）。"""
    ens_baseline = os.path.join(ENS_SELECT_DIR, "%s_g2_top10.csv" % run_day)
    # F7 补修（2026-09-28 首跑实测发现）：ENS 重放（--ensemble --ens-live）除 g2_ens/ 外
    # 还会重写 G2 主目录的同名 csv+md（deploy L309/L320 无 ensemble 分支），
    # 必须一并备份/恢复，否则 ENS 复跑漂移会污染 G2 正品且无恢复。
    g2_csv_baseline = os.path.join(SELECT_DIR, "%s_g2_top10.csv" % run_day)
    g2_md_baseline = os.path.join(SELECT_DIR, "%s_g2_selection.md" % run_day)

    _log("[ENS] 当日无融合基准候选（红线空仓路径），重放确认" if not os.path.exists(ens_baseline)
         else "[ENS] 备份融合基准")
    items = [(ens_baseline, _backup_baseline(ens_baseline, "ENS")),
             (g2_csv_baseline, _backup_baseline(g2_csv_baseline, "ENS")),
             (g2_md_baseline, _backup_baseline(g2_md_baseline, "ENS"))]

    _log("[ENS] 重放 deploy_predict_g2.py --date %s --top 10 --ensemble --ens-live" % run_day)
    # F7 修复加固（2026-09-29 复核）：原实现逐 return 内联 _restore_baseline，
    # 一旦 _replay_ens/_load_csv/print 抛异常（GBK \ufffd 打印崩溃最典型），
    # 恢复永不执行 → ENS 复跑产物永久留在 G2 主目录（正品被污染）。
    try:
        rc, out = _replay_ens(run_day)
        _safe_print(out[-2500:])
        if rc != 0:
            _push("G2/ENS 每日复跑 FAIL", "融合复跑退出码 %d（运行日 %s）—— FAIL-LOUD 或运行时异常" % (rc, run_day),
                  "error", no_send)
            _log("!! 融合复跑退出码 %d（exit=1）" % rc)
            return 1

        base = _load_csv(ens_baseline + ".compare_base")   # 原始融合基准（已备份）
        rep = _load_csv(ens_baseline)                      # 复跑融合产物（已重写同名路径）
    finally:
        _restore_baseline(items, "ENS")   # F7 修复：对比后恢复原件（含 G2 主目录 csv+md）
    sev, msg = _cmp_ens(base, rep)
    if sev == "pass":
        _log("[ENS] PASS：%s（exit=0）" % msg)
        print("[ENS-PASS] %s" % msg)
        _push("G2/ENS 每日复跑 PASS", "运行日 %s 融合：%s（exit=0）" % (run_day, msg), "info", no_send)
        return 0
    if sev == "error":
        _push("G2/ENS 每日复跑 ERROR", "运行日 %s 融合：%s" % (run_day, msg), "error", no_send)
        _log("[ENS] ERROR：%s（exit=1）" % msg)
        return 1
    # diff：ENS 与 G2 同快照同 F2 主源口径，按复跑 stdout 的 F2 主源分级
    src = _f2_source(out)
    if src == "sina":
        _log("[ENS] 差异（F2 新浪兜底，盘中实时盘后不可复现，仅记录不告警，exit=0）：%s" % msg)
        print("[ENS-DIFF-SINA] 记录差异不告警：%s" % msg)
        _push("G2/ENS 每日复跑 PASS（新浪兜底差异仅记录）",
              "运行日 %s 融合：%s（新浪盘中实时盘后不可复现，仅记录不告警）" % (run_day, msg), "info", no_send)
        return 0
    if src == "tushare":
        _push("G2/ENS 每日复跑 DIFF", "运行日 %s 融合（Tushare 主源，确定性口径）：%s —— 当日运行时可能主源失效/数据扰动/代码回退"
              % (run_day, msg), "error", no_send)
        _log("[ENS] DIFF（Tushare 主源）：%s（exit=1）" % msg)
        return 1
    _push("G2/ENS 每日复跑 DIFF", "运行日 %s 融合（无法判定 F2 主源）：%s" % (run_day, msg), "warn", no_send)
    _log("[ENS] 差异（F2 主源未知，保守 warn）：%s（exit=2）" % msg)
    return 2


def _health_check(date_str, no_send):
    """降级路径健康校验（无留档快照时用）：候选存在、列齐、SC_F2/SC_F5 非坍缩。"""
    csv = os.path.join(SELECT_DIR, "%s_g2_top10.csv" % date_str)
    if not os.path.exists(csv):
        _push("G2 每日复跑 FAIL", "复跑后 %s 未落盘" % os.path.basename(csv), "error", no_send)
        _log("!! 复跑后 %s 未落盘" % os.path.basename(csv))
        return False
    df = pd.read_csv(csv, encoding="utf-8-sig")
    miss = [c for c in HEALTH_COLS if c not in df.columns]
    if miss:
        _push("G2 每日复跑 FAIL", "复跑 CSV 缺列 %s（%s）" % (miss, os.path.basename(csv)), "error", no_send)
        _log("!! 复跑 CSV 缺列 %s" % miss)
        return False
    if len(df) == 0:
        _push("G2 每日复跑 FAIL", "复跑 CSV 候选为空（%s）" % os.path.basename(csv), "error", no_send)
        _log("!! 复跑 CSV 候选为空")
        return False
    bad = []
    if df["SC_F2"].nunique() <= 1:
        bad.append("SC_F2 坍缩")
    if df["SC_F5"].nunique() <= 1:
        bad.append("SC_F5 坍缩")
    if bad:
        _push("G2 每日复跑 FAIL", "评分卡坍缩: %s（%s）" % ("/".join(bad), os.path.basename(csv)), "error", no_send)
        _log("!! 评分卡坍缩: %s" % "/".join(bad))
        return False
    _log("[健康] %s 行数=%d SC_F2 nunique=%d SC_F5 nunique=%d" % (
        os.path.basename(csv), len(df), df["SC_F2"].nunique(), df["SC_F5"].nunique()))
    return True


def main():
    ap = argparse.ArgumentParser(description="G2 每日选股复跑对比（T-20260919-002 每日复跑机制）")
    ap.add_argument("--date", default=None, help="运行日 YYYYMMDD（默认今天）")
    ap.add_argument("--no-send", action="store_true", help="不推送告警（调试）")
    args = ap.parse_args()

    run_day = args.date or time.strftime("%Y%m%d")
    snap = os.path.join(LIVE_DIR, "snapshot_g2_%s.parquet" % run_day)
    baseline = os.path.join(SELECT_DIR, "%s_g2_top10.csv" % run_day)
    md_baseline = os.path.join(SELECT_DIR, "%s_g2_selection.md" % run_day)   # 复跑同样会重写 md（F7）
    ens_baseline = os.path.join(ENS_SELECT_DIR, "%s_g2_top10.csv" % run_day)

    _log("==== G2+ENS 每日选股复跑对比 运行日=%s 开始 ====" % run_day)

    # ---- 0) 双无 → 非交易日/链路未生成，跳过 ----
    if not os.path.exists(snap) and not os.path.exists(baseline) and not os.path.exists(ens_baseline):
        _log("[SKIP] 无留档快照且无当日候选（非交易日或链路未运行），exit=0")
        return 0

    # ---- 降级：无留档快照 → 用当前快照仅健康校验（单源结论） ----
    if not os.path.exists(snap):
        _log("[DEGRADE] 留档快照 %s 不存在 → 用当前快照重放，仅健康校验（单源结论，未经留档对比）"
             % os.path.basename(snap))
        # F7 修复：降级路径的重放同样会覆写当日候选，先备份、校验后恢复原件
        deg_items = [(baseline, _backup_baseline(baseline, "DEGRADE")),
                     (md_baseline, _backup_baseline(md_baseline, "DEGRADE"))]
        # F7 修复加固（2026-09-29 复核）：原实现逐 return 内联 _restore_baseline，
        # 异常出口（GBK 打印崩溃/健康校验抛错）会跳过恢复 → 复跑产物留在盘上。
        try:
            rc, out = _replay(run_day)
            _safe_print(out[-2000:])
            if rc != 0:
                _push("G2 每日复跑 FAIL", "复跑退出码 %d（降级路径，运行日 %s）" % (rc, run_day), "error", args.no_send)
                _log("!! 降级复跑退出码 %d" % rc)
                return 1
            ok = _health_check(run_day, args.no_send)
        finally:
            _restore_baseline(deg_items, "DEGRADE")
        if not ok:
            _log("!! 降级健康校验不通过（exit=1）")
            return 1
        _push("G2 每日复跑 降级（无留档快照）",
              "运行日 %s 无 snapshot_g2 留档，仅用当前快照做了健康校验（PASS），未做基准对比（单源结论）。"
              "请核查 g2_candidates_night.ps1 留档步骤是否失效。" % run_day, "warn", args.no_send)
        _log("[DEGRADE] 健康校验 PASS（exit=2，warn：留档缺失）")
        _log("[ENS] 无留档快照，跳过融合对比（与 G2 同根因：留档缺失）")
        return 2

    # ---- 1) 备份基准（复跑会重写同名路径） ----
    base_items = [(baseline, _backup_baseline(baseline, "1/4")),
                  (md_baseline, _backup_baseline(md_baseline, "1/4"))]
    if not base_items[0][1]:
        _log("[1/4] 当日无基准候选文件（红线空仓路径），跳过备份")

    # ---- 2) 重放（F7 修复加固：try/finally 兜底恢复，任何异常/早退都不留复跑产物） ----
    _log("[2/4] 重放 deploy_predict_g2.py --date %s --top 10" % run_day)
    try:
        rc, out = _replay(run_day)
        _safe_print(out[-3000:])
        if rc != 0:
            _push("G2 每日复跑 FAIL", "复跑退出码 %d（运行日 %s）—— FAIL-LOUD 或运行时异常" % (rc, run_day),
                  "error", args.no_send)
            _log("!! 复跑退出码 %d（exit=1）" % rc)
            return 1

        # ---- 3) 读取双方产物对比（原件由 finally 恢复，复跑产物不留在盘上） ----
        base = _load_csv(baseline + ".compare_base")   # 原始基准（已备份）
        rep = _load_csv(baseline)                      # 复跑产物（已重写同名路径）
    finally:
        _restore_baseline(base_items, "F7")
    src = _f2_source(out)
    _log("[3/4] 复跑 F2 主源=%s" % src)

    sev, msg = _cmp(base, rep)
    rc_g2 = 0
    if sev == "pass":
        _log("[4/4] PASS：%s（exit=0）" % msg)
        print("[PASS] %s" % msg)
        _push("G2 每日复跑 PASS", "运行日 %s：%s（exit=0）" % (run_day, msg), "info", args.no_send)
        rc_g2 = 0
    elif sev == "error":
        _push("G2 每日复跑 ERROR", "运行日 %s：%s" % (run_day, msg), "error", args.no_send)
        _log("[4/4] ERROR：%s（exit=1）" % msg)
        rc_g2 = 1
    elif src == "sina":
        # sev == "diff"：按 F2 主源分级
        _log("[4/4] 差异（F2 新浪兜底，盘中实时盘后不可复现，仅记录不告警，exit=0）：%s" % msg)
        print("[DIFF-SINA] 记录差异不告警：%s" % msg)
        _push("G2 每日复跑 PASS（新浪兜底差异仅记录）",
              "运行日 %s：%s（新浪盘中实时盘后不可复现，仅记录不告警）" % (run_day, msg), "info", args.no_send)
        rc_g2 = 0
    elif src == "tushare":
        _push("G2 每日复跑 DIFF", "运行日 %s（Tushare 主源，确定性口径）：%s —— 当日运行时可能主源失效/数据扰动/代码回退"
              % (run_day, msg), "error", args.no_send)
        _log("[4/4] DIFF（Tushare 主源）：%s（exit=1）" % msg)
        rc_g2 = 1
    else:
        _push("G2 每日复跑 DIFF", "运行日 %s（无法判定 F2 主源）：%s" % (run_day, msg), "warn", args.no_send)
        _log("[4/4] 差异（F2 主源未知，保守 warn）：%s（exit=2）" % msg)
        rc_g2 = 2

    # ---- 4) ENS 融合对比（T-20260920-001 扩展）：同快照独立重放，退出码取两臂最严重者 ----
    rc_ens = _ens_compare(run_day, args.no_send)
    rc = max(rc_g2, rc_ens)
    _log("==== G2+ENS 每日选股复跑对比 运行日=%s 结束（G2=%d ENS=%d 综合=%d）====" % (run_day, rc_g2, rc_ens, rc))
    return rc


if __name__ == "__main__":
    sys.exit(main())
