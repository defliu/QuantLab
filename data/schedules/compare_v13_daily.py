# coding: utf-8
"""V1.3 每日选股复跑对比（T-20260920-001 扩展，2026-09-20 立）。

与 compare_g2_daily.py（G2/ENS）同构的每日复跑双重保障，覆盖 V1.3（deploy_predict.py --model v3，
16:30 daily 链路）。V1.3 是纯离线确定性链路——离线代理评分卡（compute_scorecard），无新浪/Tushare
主源之分，因此复跑对比要求严格一致，任一不一致一律 error 告警（当日运行时执行偏差/数据扰动/代码回退）。

流程：
  1) 读留档快照 data_live/snapshot_v13_<runDay>.parquet（g2_candidates_night.ps1 已固化
     latest_features.parquet，复跑用它保证可复现——次日 merge_live_features 会重建并覆盖）
  2) 备份当日基准 data/selections/<runDay>_model_top10.csv → .compare_base
     （复跑会用同名路径重写，必须先备份再重放）
  3) subprocess 重放 deploy_predict.py --date <runDay YYYY-MM-DD> --model v3 --top-k 10（cwd=项目目录）
  4) 对比 ts_code 集合/排序 + model_prob/score_total/combo/SC_F1~F6 逐位，不一致一律 error
  5) 退出码：0=PASS 1=error 2=warn（降级健康校验）
  6) 通知（2026-09-20 诚哥拍板「失败成功都通知」）：PASS 推 info 成功卡，error 推红卡，
     warn 推橙卡，SKIP（非交易日）不推避免噪音，降级路径已推 warn 不重复。

无留档快照时降级：用当前 latest_features.parquet 重放（仅当最新日仍为 run_day）做健康校验；
快照缺失/过期本身 warn 告警（留档步骤可能故障）。

用法：
  python compare_v13_daily.py                 # 默认对比今天（运行日）
  python compare_v13_daily.py --date 20260918 # 指定运行日（调试/补跑）
  python compare_v13_daily.py --no-send       # 只打印不推送（调试）
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

import pandas as pd

P16 = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
SELECT_DIR = os.path.join(P16, "data", "selections")
LIVE_DIR = os.path.join(P16, "data_live")
SNAP_CUR = os.path.join(LIVE_DIR, "latest_features.parquet")
DEPLOY = os.path.join(P16, "deploy_predict.py")
ALERT = os.path.join(P16, "push_alert_card.py")
LOG = os.path.join(P16, "data", "schedules", "compare_v13_daily.log")
PY = sys.executable

CMP_COLS = ["model_prob", "score_total", "SC_F1", "SC_F2", "SC_F3", "SC_F4", "SC_F5", "SC_F6", "combo"]
HEALTH_COLS = ["ts_code"] + CMP_COLS


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


def _snap_latest_day():
    """当前 latest_features.parquet 最新交易日（YYYYMMDD），读失败返回 None。"""
    try:
        df = pd.read_parquet(SNAP_CUR, columns=["trade_date"])
        return pd.Timestamp(df["trade_date"].max()).strftime("%Y%m%d")
    except Exception:
        return None


def _replay(date_str):
    """重放 deploy_predict.py --date YYYY-MM-DD --model v3 --top-k 10（与 16:30 daily 链路同参）。"""
    d = "%s-%s-%s" % (date_str[:4], date_str[4:6], date_str[6:])
    cmd = [PY, DEPLOY, "--date", d, "--model", "v3", "--top-k", "10"]
    r = subprocess.run(cmd, cwd=P16, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def _cmp(base, rep):
    """对比基准 vs 复跑，返回 (severity, msg)。V1.3 纯确定性，不一致一律 error。"""
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
    for c in CMP_COLS:
        if c not in base.columns or c not in rep.columns:
            diffs.append("%s 列缺失（基准=%s 复跑=%s）" % (c, c in base.columns, c in rep.columns))
            continue
        if base[c].tolist() != rep[c].tolist():
            diffs.append("%s 不一致：基准 %s vs 复跑 %s" % (c, base[c].tolist(), rep[c].tolist()))
    if diffs:
        return "error", "；".join(diffs)
    return "pass", "完全一致（%d 只，ts_code 排序 / model_prob / score_total / SC_F1~F6 / combo 逐位相同）" % len(base)


def _health_check(date_str, no_send):
    """降级路径健康校验（无留档快照时用）：候选存在、列齐、评分卡非坍缩。"""
    csv = os.path.join(SELECT_DIR, "%s_model_top10.csv" % date_str)
    if not os.path.exists(csv):
        _push("V1.3 每日复跑 FAIL", "复跑后 %s 未落盘" % os.path.basename(csv), "error", no_send)
        _log("!! 复跑后 %s 未落盘" % os.path.basename(csv))
        return False
    df = pd.read_csv(csv, encoding="utf-8-sig")
    miss = [c for c in HEALTH_COLS if c not in df.columns]
    if miss:
        _push("V1.3 每日复跑 FAIL", "复跑 CSV 缺列 %s（%s）" % (miss, os.path.basename(csv)), "error", no_send)
        _log("!! 复跑 CSV 缺列 %s" % miss)
        return False
    if len(df) == 0:
        _push("V1.3 每日复跑 FAIL", "复跑 CSV 候选为空（%s）" % os.path.basename(csv), "error", no_send)
        _log("!! 复跑 CSV 候选为空")
        return False
    bad = []
    if df["score_total"].nunique() <= 1:
        bad.append("score_total 坍缩")
    for f in ("SC_F1", "SC_F2", "SC_F3", "SC_F4", "SC_F5", "SC_F6"):
        if df[f].nunique() <= 1:
            bad.append("%s 坍缩" % f)
            break
    if bad:
        _push("V1.3 每日复跑 FAIL", "评分卡坍缩: %s（%s）" % ("/".join(bad), os.path.basename(csv)), "error", no_send)
        _log("!! 评分卡坍缩: %s" % "/".join(bad))
        return False
    _log("[健康] %s 行数=%d score_total nunique=%d" % (os.path.basename(csv), len(df), df["score_total"].nunique()))
    return True


def main():
    ap = argparse.ArgumentParser(description="V1.3 每日选股复跑对比（T-20260920-001 扩展）")
    ap.add_argument("--date", default=None, help="运行日 YYYYMMDD（默认今天）")
    ap.add_argument("--no-send", action="store_true", help="不推送告警（调试）")
    args = ap.parse_args()

    run_day = args.date or time.strftime("%Y%m%d")
    snap = os.path.join(LIVE_DIR, "snapshot_v13_%s.parquet" % run_day)
    baseline = os.path.join(SELECT_DIR, "%s_model_top10.csv" % run_day)
    base_backup = baseline + ".compare_base"

    _log("==== V1.3 每日选股复跑对比 运行日=%s 开始 ====" % run_day)

    # ---- 0) 双无 → 非交易日/链路未生成，跳过 ----
    if not os.path.exists(snap) and not os.path.exists(baseline):
        _log("[SKIP] 无留档快照且无当日候选（非交易日或链路未运行），exit=0")
        return 0

    # ---- 降级：无留档快照 → 用当前快照仅健康校验（单源结论） ----
    if not os.path.exists(snap):
        cur = _snap_latest_day()
        if cur != run_day:
            _push("V1.3 每日复跑 降级（无留档快照）",
                  "运行日 %s 无 snapshot_v13 留档，且当前 latest_features 最新日=%s（非当日），无法重放校验。"
                  "请核查 g2_candidates_night.ps1 V1.3 留档步骤是否失效。" % (run_day, cur), "warn", args.no_send)
            _log("[DEGRADE] 留档缺失且当前快照非当日（%s），无法校验（exit=2）" % cur)
            return 2
        _log("[DEGRADE] 留档快照 %s 不存在 → 用当前快照重放，仅健康校验（单源结论，未经留档对比）"
             % os.path.basename(snap))
        rc, out = _replay(run_day)
        print(out[-2000:])
        if rc != 0:
            _push("V1.3 每日复跑 FAIL", "复跑退出码 %d（降级路径，运行日 %s）" % (rc, run_day), "error", args.no_send)
            _log("!! 降级复跑退出码 %d" % rc)
            return 1
        ok = _health_check(run_day, args.no_send)
        if not ok:
            _log("!! 降级健康校验不通过（exit=1）")
            return 1
        _push("V1.3 每日复跑 降级（无留档快照）",
              "运行日 %s 无 snapshot_v13 留档，仅用当前快照做了健康校验（PASS），未做基准对比（单源结论）。"
              "请核查 g2_candidates_night.ps1 V1.3 留档步骤是否失效。" % run_day, "warn", args.no_send)
        _log("[DEGRADE] 健康校验 PASS（exit=2，warn：留档缺失）")
        return 2

    # ---- 1) 备份基准（复跑会重写同名路径） ----
    if os.path.exists(baseline):
        shutil.copy2(baseline, base_backup)
        _log("[1/4] 备份基准 %s -> %s" % (os.path.basename(baseline), os.path.basename(base_backup)))
    else:
        _log("[1/4] 当日无基准候选文件（红线空仓路径），跳过备份")

    # ---- 2) 重放 ----
    _log("[2/4] 重放 deploy_predict.py --date %s --model v3 --top-k 10" % run_day)
    rc, out = _replay(run_day)
    print(out[-3000:])
    if rc != 0:
        _push("V1.3 每日复跑 FAIL", "复跑退出码 %d（运行日 %s）" % (rc, run_day), "error", args.no_send)
        _log("!! 复跑退出码 %d（exit=1）" % rc)
        return 1

    # ---- 3) 读取双方产物对比 ----
    base = _load_csv(base_backup)   # 原始基准（已备份）
    rep = _load_csv(baseline)       # 复跑产物（已重写同名路径）
    sev, msg = _cmp(base, rep)
    if sev == "pass":
        _log("[4/4] PASS：%s（exit=0）" % msg)
        print("[PASS] %s" % msg)
        _push("V1.3 每日复跑 PASS", "运行日 %s：%s（exit=0）" % (run_day, msg), "info", args.no_send)
        return 0
    _push("V1.3 每日复跑 ERROR", "运行日 %s：%s —— V1.3 为纯确定性链路，不一致必为运行时偏差/数据扰动/代码回退"
          % (run_day, msg), "error", args.no_send)
    _log("[4/4] ERROR：%s（exit=1）" % msg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
