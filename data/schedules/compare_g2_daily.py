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
    ens_backup = ens_baseline + ".compare_base"

    if not os.path.exists(ens_baseline):
        _log("[ENS] 当日无融合基准候选（红线空仓路径），重放确认")
    else:
        shutil.copy2(ens_baseline, ens_backup)
        _log("[ENS] 备份融合基准 %s -> %s" % (os.path.basename(ens_baseline), os.path.basename(ens_backup)))

    _log("[ENS] 重放 deploy_predict_g2.py --date %s --top 10 --ensemble --ens-live" % run_day)
    rc, out = _replay_ens(run_day)
    print(out[-2500:])
    if rc != 0:
        _push("G2/ENS 每日复跑 FAIL", "融合复跑退出码 %d（运行日 %s）—— FAIL-LOUD 或运行时异常" % (rc, run_day),
              "error", no_send)
        _log("!! 融合复跑退出码 %d（exit=1）" % rc)
        return 1

    base = _load_csv(ens_backup)   # 原始融合基准（已备份）
    rep = _load_csv(ens_baseline)  # 复跑融合产物（已重写同名路径）
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
    base_backup = baseline + ".compare_base"
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
        rc, out = _replay(run_day)
        print(out[-2000:])
        if rc != 0:
            _push("G2 每日复跑 FAIL", "复跑退出码 %d（降级路径，运行日 %s）" % (rc, run_day), "error", args.no_send)
            _log("!! 降级复跑退出码 %d" % rc)
            return 1
        ok = _health_check(run_day, args.no_send)
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
    if os.path.exists(baseline):
        shutil.copy2(baseline, base_backup)
        _log("[1/4] 备份基准 %s -> %s" % (os.path.basename(baseline), os.path.basename(base_backup)))
    else:
        _log("[1/4] 当日无基准候选文件（红线空仓路径），跳过备份")

    # ---- 2) 重放 ----
    _log("[2/4] 重放 deploy_predict_g2.py --date %s --top 10" % run_day)
    rc, out = _replay(run_day)
    print(out[-3000:])
    if rc != 0:
        _push("G2 每日复跑 FAIL", "复跑退出码 %d（运行日 %s）—— FAIL-LOUD 或运行时异常" % (rc, run_day),
              "error", args.no_send)
        _log("!! 复跑退出码 %d（exit=1）" % rc)
        return 1

    # ---- 3) 读取双方产物对比 ----
    base = _load_csv(base_backup)   # 原始基准（已备份）
    rep = _load_csv(baseline)       # 复跑产物（已重写同名路径）
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
