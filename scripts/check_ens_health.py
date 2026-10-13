# coding: utf-8
"""ENS（融合桥 p16_ensemble_bridge）实盘选股数据健康监控（三道防线·B，2026-10-12 立）。

与 G2 `scripts/check_scorecard_health.py` 同厚度的独立事后检查（建议调度 20:16，20:05
g2_candidates_night 的 deploy_predict_g2 --ens-live 候选落盘后）：

  1. ENS 选股 CSV 存在且列齐全（ts_code/total_new/ens/pv/pg/trade_date）
  2. ens 唯一值数 < 2 → error（融合排序分恒值 = 融合逻辑失效，同 G2 SC_F2/F5 事故形态）
     total_new 唯一值数 < 2 → warn（信息坍缩不致命）
     ※ 仅行数 >= MIN_SAMPLE_ROWS(5) 时判定（2026-09-28 单行候选误报教训，同 G2 小样本保护）
  3. CSV 行数 == 0 → warn「空候选」。※ 不照搬 G2 的 MIN_CAND=5 枯竭阈值：ENS 池为
     红线过滤后按 ens 取 Top，行数 1~4 属常态（2026-10-12/10-13 实测 2 行），<5 报枯竭会常态假阳
  4. total_new >= 红线 60 过线数 0 → warn（次日融合可能空仓，宁缺毋滥；连续多日才需核查）
  5. CSV trade_date 落后「最近应出数据的交易日」> CSV_LAG_TD(2) → warn
     （ENS 无候选日 deploy 不写文件属设计内，滞后 1 个交易日正常；连续 >2 才视为断链）
  6. 快照（与 G2 共用 data_live/g2_latest_features.parquet，独立复查）：存在性、按【交易日】
     的新鲜度（容差 1 交易日）、主源列 mf_main_net / ind_pct_ths 非空率 >= 99%（F2/F5 主源）
     —— G2 的 20:15 任务挂掉不影响本检查独立告警
  7. paper_forward_ens.csv 最近 LIVE_LOOKBACK_TD(5) 个【交易日】有候选记录（候选链断流检测）
  8. 假期（非交易日）运行时的语义标注：所检 CSV 通常是 g2_candidates_night.ps1 预生成的
     「下一交易日副本」，数据基准 = 最近应出数据的交易日（非假期当天）

交易日历、交易日滞后、假期副本判定复用 `scripts/check_scorecard_health.py` 公共函数
（单一事实源，避免两套日历口径漂移）。

异常 → push_alert_card.py 飞书告警（--level error/warn）+ 退出码非 0；正常 → 打印 OK 退出 0。
退出码：0=PASS，1=有 error，2=仅 warn。

用法：
  python check_ens_health.py                 # 正常检查 + 异常飞书告警
  python check_ens_health.py --no-send       # 只打印不推送（调试）
  python check_ens_health.py --date 20261012 # 指定检查日期（调试）
"""
import argparse
import datetime
import os
import sys
import time

import pandas as pd

# ---- 复用 G2 健康检查的交易日历/副本判定公共函数（同目录模块，单一事实源）----
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_scorecard_health as SC

# ---- 路径（与 deploy_predict_g2.py / g2_ens_config.py 同源）----
P16 = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
SELECT_DIR = os.path.join(P16, "data", "selections", "g2_ens")
SNAP = os.path.join(P16, "data_live", "g2_latest_features.parquet")   # 与 G2 共用
LIVE_LOG = os.path.join(P16, "data", "real", "paper_forward_ens.csv")
ALERT = os.path.join(P16, "push_alert_card.py")

ENS_COLS = ["ts_code", "total_new", "ens", "pv", "pg", "trade_date"]
CRITICAL = {"ens"}           # 融合排序分恒值 = 融合逻辑失效
RED_LINE = 60.0              # 与 g2_ens_config.REDLINE / deploy_predict_g2 --threshold 60 一致
FRESH_TRADE_DAYS = 1         # 快照新鲜度容差（交易日）
FRESH_DAYS_FALLBACK = 4      # 交易日历不可用时降级的自然日容差
LIVE_LOOKBACK_TD = 5         # live 日志回看窗口（交易日）
LIVE_LOOKBACK_CAL_FALLBACK = 7
MIN_SAMPLE_ROWS = 5          # 坍缩判定最小样本量（同 G2）
MIN_CAND = 0                 # ENS 空文件（0 行）才告警；1~4 行属常态（区别于 G2 的 5）
CSV_LAG_TD = 2               # CSV trade_date 允许落后最近应出数据交易日的交易日数（空仓日不写文件）


def latest_top10_csv():
    """取 selections/g2_ens/ 下最近生成（mtime 最大）的 *_g2_top10.csv，返回 (path, date_str)。

    按 mtime 而非文件名日期取最近：假日/周末夜间副本以「下一交易日」命名（文件名日期 >
    当日真实产物），按文件名取最大会挑到次日副本（同 G2 2026-09-25 中秋假阳教训）。
    """
    if not os.path.isdir(SELECT_DIR):
        return None, None
    cands = []
    for f in os.listdir(SELECT_DIR):
        if (f.endswith("_g2_top10.csv")
                and len(f) == 8 + len("_g2_top10.csv") and f[:8].isdigit()):
            cands.append(f)
    if not cands:
        return None, None
    f = max(cands, key=lambda x: os.path.getmtime(os.path.join(SELECT_DIR, x)))
    return os.path.join(SELECT_DIR, f), f[:8]


def csv_trade_date(df):
    """解析 CSV 首行 trade_date → datetime.date；失败返回 None。"""
    if "trade_date" not in df.columns or df.empty:
        return None
    try:
        raw = df["trade_date"].iloc[0]
        s = str(int(raw)) if isinstance(raw, (int, float)) else str(raw).strip()
        try:
            return pd.to_datetime(s, format="%Y%m%d").date()
        except ValueError:
            return pd.to_datetime(s).date()
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="ENS 融合选股数据健康监控（三道防线·B）")
    ap.add_argument("--date", default=None, help="指定检查日期 YYYYMMDD（默认最新）")
    ap.add_argument("--no-send", action="store_true", help="只打印不推送告警（调试）")
    args = ap.parse_args()

    problems = []   # (level, title, body)
    now = time.time()
    now_dt = datetime.datetime.fromtimestamp(now)
    SC._load_cal()  # 先加载交易日历（快照滞后 / live 窗口 / 副本标注均依赖）

    # ---- 1. ENS 选股 CSV ----
    csv_path, csv_date = latest_top10_csv()
    if args.date:
        csv_path = os.path.join(SELECT_DIR, "%s_g2_top10.csv" % args.date)
        csv_date = args.date
        if not os.path.exists(csv_path):
            print("[ENS-CHECK] 指定日期 %s 无 top10 CSV，按最新日期回退" % args.date)
            csv_path, csv_date = latest_top10_csv()
    if not csv_path or not os.path.exists(csv_path):
        problems.append(("error", "ENS 选股 CSV 缺失",
                         "data/selections/g2_ens/ 下无 *_g2_top10.csv。融合候选链断流，"
                         "次日 rebalance_ens 将无池可用（或自上次成功以来连续空仓日）。"))
        _finish(problems, args.no_send)
        return

    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    missing = [c for c in ENS_COLS if c not in df.columns]
    basis_note = SC._basis_note(df)
    if missing:
        problems.append(("error", "ENS 选股 CSV 缺列",
                         "%s 缺列 %s。候选输出异常（疑似代码/列名变更）。" %
                         (os.path.basename(csv_path), ",".join(missing))))
    else:
        # ---- 5. trade_date 滞后（空仓日不写文件 → 容差 2 个交易日）----
        ctd = csv_trade_date(df)
        exp_td = SC.latest_expected_td(now_dt)
        csv_lag = SC.trade_day_lag(ctd, exp_td) if ctd else None
        if csv_lag is not None:
            print("[ENS-CHECK] %s trade_date=%s（最近应出数据的交易日 %s，落后 %d 个交易日）" %
                  (csv_date, ctd.isoformat(), exp_td.isoformat(), csv_lag))
            if csv_lag > CSV_LAG_TD:
                problems.append(("warn", "ENS 候选连续无新产物",
                                 "%s 的 trade_date=%s 落后最近应出数据的交易日 %s 共 %d 个"
                                 "交易日（容差 %d）。单个空仓日 deploy 不写文件属设计内，"
                                 "连续多日需核查 --ens-live 链路与红线口径。%s" %
                                 (os.path.basename(csv_path), ctd.isoformat(),
                                  exp_td.isoformat(), csv_lag, CSV_LAG_TD, basis_note)))

        nunique = {c: int(df[c].nunique()) for c in ("ens", "total_new") if c in df.columns}
        total_max = float(df["total_new"].max()) if "total_new" in df.columns else float("nan")
        n_pass = int((df["total_new"] >= RED_LINE).sum()) if "total_new" in df.columns else 0
        print("[ENS-CHECK] %s 候选 %d 只 · total_new 最高 %.1f · 过红线(>=%.0f) %d 只" %
              (csv_date, len(df), total_max, RED_LINE, n_pass))
        if basis_note:
            print("[ENS-CHECK][INFO] %s" % basis_note)
        print("[ENS-CHECK] 关键列唯一值数: %s" % ", ".join("%s=%d" % (k, v) for k, v in sorted(nunique.items())))

        if len(df) == MIN_CAND:
            problems.append(("warn", "ENS 空候选",
                             "%s 0 行候选。红线/ST 剔除后为空属设计内宁缺毋滥，"
                             "次日融合将空仓；连续多日需核查红线 60 与预选池。%s" %
                             (os.path.basename(csv_path), basis_note)))
        elif len(df) < MIN_SAMPLE_ROWS:
            # 行数过少时 nunique 必然 <= 行数，坍缩判定无统计意义（同 G2 小样本保护）。
            print("[ENS-CHECK][INFO] %s 仅 %d 行（< %d），样本不足，跳过 nunique 坍缩检查" %
                  (os.path.basename(csv_path), len(df), MIN_SAMPLE_ROWS))
        else:
            for c in CRITICAL:
                if nunique.get(c, 0) < 2:
                    problems.append(("error", "ENS 坍缩 %s" % c,
                                     "%s 的 %s 唯一值数=%d（恒值=融合排序分失效，"
                                     "同 G2 SC_F2/F5 事故形态）。" %
                                     (os.path.basename(csv_path), c, nunique[c])))
            for c in ("ens", "total_new"):
                if c not in CRITICAL and nunique.get(c, 0) < 2:
                    problems.append(("warn", "ENS 信息坍缩 %s" % c,
                                     "%s 的 %s 唯一值数=%d（不致命，留意）。" %
                                     (os.path.basename(csv_path), c, nunique[c])))
        if n_pass < 1:
            problems.append(("warn", "ENS 候选无过红线",
                             "%s 无候选达到红线 %.0f（最高 %.1f）→ 次日融合可能空仓。"
                             "宁缺毋滥属设计内，若连续多日需核查评分卡口径。%s" %
                             (os.path.basename(csv_path), RED_LINE, total_max, basis_note)))

    # ---- 6. 快照（与 G2 共用，独立复查：G2 任务挂掉不影响 ENS 告警）----
    if not os.path.exists(SNAP):
        problems.append(("error", "ENS 快照缺失",
                         "data_live/g2_latest_features.parquet 不存在。build_g2_daily 链路断流，"
                         "次日 ENS 候选将无特征可推。"))
    else:
        snap = pd.read_parquet(SNAP)
        snap_day = pd.Timestamp(snap["trade_date"].max()).date()
        exp_td = SC.latest_expected_td(now_dt)
        td_lag = SC.trade_day_lag(snap_day, exp_td)
        age_days = (now - pd.Timestamp(snap["trade_date"].max()).timestamp()) / 86400.0
        if td_lag is None:
            print("[ENS-CHECK][INFO] 交易日历不可用，快照按自然日容差 %d 天判定" % FRESH_DAYS_FALLBACK)
            if age_days > FRESH_DAYS_FALLBACK:
                problems.append(("warn", "ENS 快照滞后",
                                 "快照最新 %s（%.0f 天前），超过容差 %d 天（自然日降级口径）。"
                                 "build_g2_daily / xtdata_update 链可能断流。" %
                                 (snap_day.isoformat(), age_days, FRESH_DAYS_FALLBACK)))
        else:
            print("[ENS-CHECK] 快照最新日 %s（最近应出数据的交易日 %s，落后 %d 个交易日）" %
                  (snap_day.isoformat(), exp_td.isoformat(), td_lag))
            if td_lag > FRESH_TRADE_DAYS:
                problems.append(("warn", "ENS 快照滞后",
                                 "快照最新 %s，落后最近应出数据的交易日 %s 共 %d 个交易日（容差 %d）。"
                                 "build_g2_daily / xtdata_update 链可能断流。" %
                                 (snap_day.isoformat(), exp_td.isoformat(), td_lag, FRESH_TRADE_DAYS)))
        for col in ("mf_main_net", "ind_pct_ths"):
            if col not in snap.columns:
                problems.append(("error", "ENS 快照缺主源列 %s" % col,
                                 "快照无 %s 列。F2/F5 主源断源，评分卡将回退（坍缩风险）。" % col))
            else:
                ratio = float(snap[col].notna().mean())
                if ratio < 0.99:
                    problems.append(("error", "ENS 主源非空率异常 %s" % col,
                                     "%s 非空率=%.1f%%（<99%%）。主源大量缺失，评分卡口径将漂移。" %
                                     (col, ratio * 100)))
                else:
                    print("[ENS-CHECK] %s 非空率 %.1f%% OK" % (col, ratio * 100))

    # ---- 7. paper_forward_ens.csv 候选记录 ----
    if os.path.exists(LIVE_LOG):
        live = pd.read_csv(LIVE_LOG, encoding="utf-8-sig")
        if "date" in live.columns:
            live["date"] = pd.to_datetime(live["date"])
            exp_td = SC.latest_expected_td(now_dt)
            win_start = None
            if SC._CAL_OK and exp_td is not None:
                d, cnt = exp_td, 0
                while cnt < LIVE_LOOKBACK_TD:
                    if SC._is_trade_day(d):
                        cnt += 1
                        if cnt == LIVE_LOOKBACK_TD:
                            win_start = d
                            break
                    d -= datetime.timedelta(days=1)
                if win_start is None:
                    win_start = exp_td
                recent = live[live["date"].dt.date >= win_start]
                print("[ENS-CHECK] live 日志近 %d 个交易日（自 %s）记录 %d 条" %
                      (LIVE_LOOKBACK_TD, win_start.isoformat(), len(recent)))
            else:
                recent = live[live["date"] >= pd.Timestamp(now - LIVE_LOOKBACK_CAL_FALLBACK * 86400)]
                win_start = None
                print("[ENS-CHECK][INFO] 交易日历不可用，live 日志按自然日 %d 天判定" %
                      LIVE_LOOKBACK_CAL_FALLBACK)
            if len(recent) == 0:
                problems.append((
                    "warn", "ENS live 日志窗口无记录",
                    "paper_forward_ens.csv 在最近 %d 个%s无候选记录。"
                    "候选链可能断流（CSV 有但未追加 live 日志）。" % (
                        LIVE_LOOKBACK_TD if SC._CAL_OK else LIVE_LOOKBACK_CAL_FALLBACK,
                        "交易日（自 %s）" % win_start.isoformat() if win_start else "自然日")))
            else:
                print("[ENS-CHECK] live 日志最新 %s" % recent["date"].max().strftime("%Y-%m-%d"))
        else:
            problems.append(("warn", "ENS live 日志无 date 列", "paper_forward_ens.csv 结构异常。"))
    else:
        problems.append(("warn", "ENS live 日志缺失",
                         "paper_forward_ens.csv 不存在。若为初始部署可忽略，否则融合候选追加链路异常。"))

    _finish(problems, args.no_send)


def _finish(problems, no_send):
    if not problems:
        print("[ENS-CHECK] 全部健康项 PASS（exit=0）")
        sys.exit(0)
    for lv, title, body in problems:
        print("[ENS-CHECK][%s] %s: %s" % (lv.upper(), title, body))
    errs = [p for p in problems if p[0] == "error"]
    warns = [p for p in problems if p[0] == "warn"]
    _push(errs, "error", no_send)
    _push(warns, "warn", no_send)
    sys.exit(1 if errs else 2)


def _push(items, level, no_send):
    if not items:
        return
    title = "ENS 数据健康[%s] %d 项" % ("ERROR" if level == "error" else "WARN", len(items))
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
