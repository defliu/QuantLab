# coding: utf-8
"""G2 实盘选股数据健康监控（T-20260919-002 三道防线·B，2026-09-20 立）。

每日候选落盘（data/selections/g2/<date>_g2_top10.csv，20:05 生成）后运行，检查评分卡是否再次坍缩
（2026-08-17~09-18 F2/F5 死代码事故形态：SC_F2 恒新浪兜底、SC_F5 恒 5.0，39 天无人发现）：

  1. 选股 CSV 存在且非空，SC_F1~F6 列齐全
  2. SC_F2 / SC_F5 唯一值数 < 2 → error（F2 主源失效 / F5 板块源失效，即事故复发形态）
  3. SC_F1/F3/F4/F6 唯一值数 < 2 → warn（信息坍缩但不致命）
  4. 快照 g2_latest_features.parquet：mf_main_net / ind_pct_ths 缺失或非空率 < 99% → error（主源断源）
  5. 快照新鲜度：trade_date 最大日距今 > 4 自然日 → warn（跨周末/节假日容差）
  6. paper_forward_live.csv：最近 7 自然日有候选记录（防候选链断流）
  7. 红线通过数（total_new >= 60）>= 1（防空仓断链）

异常 → push_alert_card.py 飞书告警（--level error/warn）+ 退出码非 0；正常 → 打印 OK 退出 0。

建议调度：每日候选落盘后 20:15（错开 19:30 moneyflow / 20:05 候选 / 19:45 数据新鲜度）。

用法：
  python check_scorecard_health.py                 # 正常检查 + 异常飞书告警
  python check_scorecard_health.py --no-send       # 只打印不推送（调试）
  python check_scorecard_health.py --date 20260921 # 指定检查日期（调试）
"""
import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

# ---- 路径（与 deploy_predict_g2.py / data_config.py 同源）----
P16 = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
SELECT_DIR = os.path.join(P16, "data", "selections", "g2")
SNAP = os.path.join(P16, "data_live", "g2_latest_features.parquet")
LIVE_LOG = os.path.join(P16, "data", "real", "paper_forward_live.csv")
ALERT = os.path.join(P16, "push_alert_card.py")

SC_COLS = ["SC_F1", "SC_F2", "SC_F3", "SC_F4", "SC_F5", "SC_F6"]
CRITICAL = {"SC_F2", "SC_F5"}   # 事故复发形态：主源失效/恒值
RED_LINE = 60.0                 # 评分卡红线（与 deploy_predict_g2 --threshold 默认一致）
FRESH_DAYS = 4                  # 快照新鲜度容差（自然日，容忍周末+节假日）


def latest_top10_csv():
    """取 selections/g2/ 下最近生成（mtime 最大）的 *_top10.csv，返回 (path, date_str) 或 (None, None)。

    原实现按文件名日期排序取最大（cands.sort(); f = cands[-1]），但假日/周末时夜间副本会以
    「下一交易日」命名（文件名日期 > 当日真实产物），按文件名取最大会挑到次日副本——2026-09-25
    中秋休市日 SC_F1/F3/F6 假阳 warn 即此因（副本停在旧口径）。改动 1（g2_candidates_night.ps1）
    后副本与真实产物同源（均按快照 target 日取源再改写 trade_date），故改按 mtime 取最近生成者，
    更贴近「检查当日部署产物」语义。
    """
    if not os.path.isdir(SELECT_DIR):
        return None, None
    cands = []
    for f in os.listdir(SELECT_DIR):
        if f.endswith("_top10.csv") and len(f) == 8 + len("_g2_top10.csv") and f[:8].isdigit():
            cands.append(f)
    if not cands:
        return None, None
    f = max(cands, key=lambda x: os.path.getmtime(os.path.join(SELECT_DIR, x)))
    return os.path.join(SELECT_DIR, f), f[:8]


def main():
    ap = argparse.ArgumentParser(description="G2 实盘选股数据健康监控（三道防线·B）")
    ap.add_argument("--date", default=None, help="指定检查日期 YYYYMMDD（默认最新）")
    ap.add_argument("--no-send", action="store_true", help="只打印不推送告警（调试）")
    args = ap.parse_args()

    problems = []   # (level, title, body)
    now = time.time()

    # ---- 1. 选股 CSV ----
    csv_path, csv_date = latest_top10_csv()
    if args.date:
        csv_path = os.path.join(SELECT_DIR, "%s_g2_top10.csv" % args.date)
        csv_date = args.date
        if not os.path.exists(csv_path):
            print("[CHECK] 指定日期 %s 无 top10 CSV，按最新日期回退" % args.date)
            csv_path, csv_date = latest_top10_csv()
    if not csv_path or not os.path.exists(csv_path):
        problems.append(("error", "G2 选股 CSV 缺失",
                         "data/selections/g2/ 下无 *_g2_top10.csv。候选链断流，次日 rebalance 将无池可用。"))
        _finish(problems, args.no_send)
        return

    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    missing = [c for c in SC_COLS if c not in df.columns]
    if missing:
        problems.append(("error", "G2 选股 CSV 缺评分卡列",
                         "%s 缺列 %s。评分卡输出异常（疑似代码/列名变更）。" % (os.path.basename(csv_path), ",".join(missing))))
    else:
        nunique = {c: int(df[c].nunique()) for c in SC_COLS}
        total_max = float(df["total_new"].max()) if "total_new" in df.columns else float("nan")
        n_pass = int((df["total_new"] >= RED_LINE).sum()) if "total_new" in df.columns else 0
        print("[CHECK] %s 候选 %d 只 · total_new 最高 %.1f · 过红线(>=%.0f) %d 只" %
              (csv_date, len(df), total_max, RED_LINE, n_pass))
        print("[CHECK] SC 列唯一值数: %s" % ", ".join("%s=%d" % (k, v) for k, v in sorted(nunique.items())))
        for c in CRITICAL:
            if nunique.get(c, 0) < 2:
                problems.append(("error", "G2 评分卡坍缩 %s" % c,
                                 "%s 的 %s 唯一值数=%d（恒值=主源失效，2026-08-17~09-18 死代码事故形态）。" % (
                                     os.path.basename(csv_path), c, nunique[c])))
        for c in SC_COLS:
            if c not in CRITICAL and nunique.get(c, 0) < 2:
                problems.append(("warn", "G2 评分卡信息坍缩 %s" % c,
                                 "%s 的 %s 唯一值数=%d（不致命，留意）。" % (os.path.basename(csv_path), c, nunique[c])))
        if n_pass < 1:
            problems.append(("warn", "G2 候选无过红线",
                             "%s 无候选达到红线 %.0f（最高 %.1f）→ 次日可能空仓。若连续多日需核查评分卡口径。" % (
                                 os.path.basename(csv_path), RED_LINE, total_max)))

    # ---- 4. 快照主源 ----
    snap_ok = True
    if not os.path.exists(SNAP):
        problems.append(("error", "G2 快照缺失",
                         "data_live/g2_latest_features.parquet 不存在。build_g2_daily 链路断流，次日候选将无特征可推。"))
        snap_ok = False
    else:
        snap = pd.read_parquet(SNAP)
        snap_date = pd.Timestamp(snap["trade_date"].max())
        age_days = (now - snap_date.timestamp()) / 86400.0
        print("[CHECK] 快照最新日 %s（%.1f 天前）" % (snap_date.strftime("%Y-%m-%d"), age_days))
        if age_days > FRESH_DAYS:
            problems.append(("warn", "G2 快照滞后",
                             "快照最新 %s（%.0f 天前），超过容差 %d 天。build_g2_daily / xtdata_update 链可能断流。" % (
                                 snap_date.strftime("%Y-%m-%d"), age_days, FRESH_DAYS)))
        for col in ("mf_main_net", "ind_pct_ths"):
            if col not in snap.columns:
                problems.append(("error", "G2 快照缺主源列 %s" % col,
                                 "快照无 %s 列。F2/F5 主源断源，评分卡将回退（坍缩风险）。" % col))
                snap_ok = False
            else:
                ratio = float(snap[col].notna().mean())
                if ratio < 0.99:
                    problems.append(("error", "G2 主源非空率异常 %s" % col,
                                     "%s 非空率=%.1f%%（<99%）。主源大量缺失，评分卡口径将漂移。" % (col, ratio * 100)))
                else:
                    print("[CHECK] %s 非空率 %.1f%% OK" % (col, ratio * 100))

    # ---- 6. paper_forward_live.csv 候选记录 ----
    if os.path.exists(LIVE_LOG):
        live = pd.read_csv(LIVE_LOG, encoding="utf-8-sig")
        if "date" in live.columns:
            live["date"] = pd.to_datetime(live["date"])
            recent = live[live["date"] >= pd.Timestamp(now - 7 * 86400)]
            print("[CHECK] live 日志近 7 日记录 %d 条" % len(recent))
            if len(recent) == 0:
                problems.append(("warn", "G2 live 日志 7 日无记录",
                                 "paper_forward_live.csv 近 7 日无候选记录。候选链可能断流（CSV 有但未追加 live 日志）。"))
            else:
                last_live = recent["date"].max().strftime("%Y-%m-%d")
                print("[CHECK] live 日志最新 %s" % last_live)
        else:
            problems.append(("warn", "live 日志无 date 列", "paper_forward_live.csv 结构异常（缺 date 列）。"))
    else:
        problems.append(("warn", "live 日志缺失",
                         "paper_forward_live.csv 不存在。若为初始部署可忽略，否则候选追加链路异常。"))

    _finish(problems, args.no_send)


def _finish(problems, no_send):
    if not problems:
        print("[CHECK] 全部健康项 PASS（exit=0）")
        sys.exit(0)
    for lv, title, body in problems:
        print("[CHECK][%s] %s: %s" % (lv.upper(), title, body))
    # 推送告警（error 优先合并推送，warn 单独）
    errs = [p for p in problems if p[0] == "error"]
    warns = [p for p in problems if p[0] == "warn"]
    _push(errs, "error", no_send)
    _push(warns, "warn", no_send)
    sys.exit(1 if errs else 2)


def _push(items, level, no_send):
    if not items:
        return
    title = "G2 数据健康[%s] %d 项" % ("ERROR" if level == "error" else "WARN", len(items))
    body = "\n".join("• %s：%s" % (t, b) for _, t, b in items)
    cmd = [sys.executable, ALERT, "--title", title, "--body", body, "--level", level]
    if no_send:
        cmd.append("--no-send")
    print("[PUSH] %s" % " ".join(cmd))
    if not no_send:
        import subprocess
        subprocess.run(cmd)


if __name__ == "__main__":
    main()
