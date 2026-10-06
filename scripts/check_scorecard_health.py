# coding: utf-8
"""G2 实盘选股数据健康监控（T-20260919-002 三道防线·B，2026-09-20 立）。

每日候选落盘（data/selections/g2/<date>_g2_top10.csv，20:05 生成）后运行，检查评分卡是否再次坍缩
（2026-08-17~09-18 F2/F5 死代码事故形态：SC_F2 恒新浪兜底、SC_F5 恒 5.0，39 天无人发现）：

  1. 选股 CSV 存在且非空，SC_F1~F6 列齐全
  2. SC_F2 / SC_F5 唯一值数 < 2 → error（F2 主源失效 / F5 板块源失效，即事故复发形态）
     ※ 仅当行数 >= MIN_SAMPLE_ROWS(5) 时判定：行数 < 5 时 nunique 无统计意义（1 行必为 1），
       跳过坍缩判定防假阳性（2026-09-28 候选枯竭 1 行误报 6 条坍缩即此因）
  3. SC_F1/F3/F4/F6 唯一值数 < 2 → warn（信息坍缩但不致命，同样受最小样本量保护）
  3b. CSV 行数 < MIN_CAND(5) → warn「候选池枯竭」（红线过严/预选池质量差，区别于评分卡坍缩）
  4. 快照 g2_latest_features.parquet：mf_main_net / ind_pct_ths 缺失或非空率 < 99% → error（主源断源）
  5. 快照新鲜度：按【交易日】判定——快照日落后「最近应出数据的交易日」> FRESH_TRADE_DAYS(1) → warn
     （T-20261006-001 修复：原按 4 自然日判定，2026 国庆休市 10/1-10/7 期间 09-30 快照在 10-06
      被算成「滞后 6 天」假阳——该期间本无交易日，不该期待更新。判定锚复用 P16 权威交易日历
      is_trade_day.py（主库日历 + 法定节假日表），与 scripts/nightly_check.recent_trade_date 同口径；
      日历不可用时降级 4 自然日并显式打印，不静默跳过）
  6. paper_forward_live.csv：最近 LIVE_LOOKBACK_TD(5) 个【交易日】有候选记录（防候选链断流；
     日历不可用降级 7 自然日）——原按 7 自然日，长假末期同样会假阳
  7. 红线通过数（total_new >= 60）>= 1（防空仓断链）
  8. 假期（非交易日）运行时的语义标注：所检 CSV 通常是 g2_candidates_night.ps1 预生成的
     「下一交易日副本」，其数据基准 = 最近应出数据的交易日（非假期当天）。候选枯竭/无过红线
     文案带上该标注，避免把假期副本误读成当日产物（2026-10-05 实测即此形态）

异常 → push_alert_card.py 飞书告警（--level error/warn）+ 退出码非 0；正常 → 打印 OK 退出 0。

建议调度：每日候选落盘后 20:15（错开 19:30 moneyflow / 20:05 候选 / 19:45 数据新鲜度）。

用法：
  python check_scorecard_health.py                 # 正常检查 + 异常飞书告警
  python check_scorecard_health.py --no-send       # 只打印不推送（调试）
  python check_scorecard_health.py --date 20260921 # 指定检查日期（调试）
"""
import argparse
import datetime
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
FRESH_TRADE_DAYS = 1            # 快照新鲜度容差（交易日）：快照落后「最近应出数据的交易日」超过此值 → warn
FRESH_DAYS_FALLBACK = 4         # 交易日历不可用时降级的自然日容差
LIVE_LOOKBACK_TD = 5            # live 日志回看窗口（交易日）：最近 N 个交易日内应有候选记录
LIVE_LOOKBACK_CAL_FALLBACK = 7  # 交易日历不可用时降级的自然日窗口
MIN_SAMPLE_ROWS = 5             # 坍缩判定最小样本量：行数低于此值时 nunique 必然 <= 行数，无统计意义
MIN_CAND = 5                    # 候选枯竭告警阈值：CSV 行数低于此值时视为候选池枯竭

# ---- 交易日历锚（复用 P16 权威模块，与 scripts/nightly_check.recent_trade_date 同口径）----
_CAL = None          # 主库交易日历集合，None=未加载
_CAL_OK = False      # 日历是否可用（不可用则降级自然日）


def _load_cal():
    """加载 P16 交易日历（主库日历 + 2026 法定节假日表）。

    复用 is_trade_day.load_calendar()：该模块同时覆盖周末、法定节假日与未来日期兜底
    （未来日期不在主库快照时按节假日表判定），正是「长假期间不该期待数据更新」的判据。
    日历不可用时返回 None，调用方降级自然日并显式打印，不静默跳过。
    """
    global _CAL, _CAL_OK
    if _CAL is not None or _CAL_OK:
        return _CAL
    try:
        if P16 not in sys.path:
            sys.path.insert(0, P16)
        import is_trade_day as ITD
        _CAL = ITD.load_calendar()
        _CAL_OK = True
    except Exception as e:
        print("[CHECK][WARN] 交易日历不可用（%r），降级自然日判定" % (e,))
        _CAL, _CAL_OK = set(), False
    return _CAL


def _is_trade_day(d):
    """d（datetime.date）是否为交易日；日历不可用时返回 None（调用方降级）。

    惰性加载日历：本函数可能被 main() 之外的工具直接调用，故不假设调用方已 _load_cal()。
    """
    _load_cal()
    if not _CAL_OK:
        return None
    try:
        if P16 not in sys.path:
            sys.path.insert(0, P16)
        import is_trade_day as ITD
        return bool(ITD.is_trade_day(d, _CAL)["is_trade_day"])
    except Exception:
        return None


def latest_expected_td(now_dt):
    """最近应出数据的交易日（快照锚）。

    语义与 nightly_check.recent_trade_date 一致：今天已收盘（>=15:00）则期望今天，否则回退上一交易日。
    长假期间回退到节前最后一个交易日（如 10-06 → 09-30），因此「未更新」不再被误判为滞后。
    惰性加载日历：本函数可能被 main() 之外的工具直接调用。
    """
    _load_cal()
    if not _CAL_OK:
        return None
    today = now_dt.date()
    if _is_trade_day(today) and now_dt.hour >= 15:
        return today
    d = today
    for _ in range(20):
        d -= datetime.timedelta(days=1)
        if _is_trade_day(d):
            return d
    return None


def trade_day_lag(actual_date, expected_date):
    """actual 落后 expected 多少个交易日；日历不可用返回 None。

    计数区间为 (actual_date, expected_date]——即 expected 自身若为交易日且快照未到该日，
    也算 1 个交易日的滞后，故与 FRESH_TRADE_DAYS=1「允许落后 1 个交易日」的容差语义一致：
        actual=10-07、expected=10-08（盘后）→ 0（当日数据未出不算滞后）
        actual=09-30、expected=10-08（节后首日盘后）→ 1（09-30 之后缺 10-08 这一天）
        actual=09-29、expected=10-08 → 2
    """
    _load_cal()
    if not _CAL_OK or actual_date is None or expected_date is None:
        return None
    if actual_date >= expected_date:
        return 0
    n, d = 0, expected_date
    while d > actual_date:
        if _is_trade_day(d):
            n += 1
        d -= datetime.timedelta(days=1)
    return n


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


def _basis_note(df):
    """假期副本语义标注：所检 CSV 是「下一交易日预生成副本」时返回标注串，否则返回空串。

    g2_candidates_night.ps1 在非交易日夜间会预生成下一交易日的候选副本：文件名日期 =
    目标交易日，但数据基准仍是最近应出数据的交易日（快照 target 日），trade_date 列
    也被改写为目标交易日。此时直接读文件名/日期会误判为「该日产物」，故显式标注。

    判定：CSV 内 trade_date（目标交易日）晚于快照锚（最近应出数据的交易日）→ 副本。
    仅在交易日历可用时判定；不可用则不标注（避免用不可靠的日历推断）。
    """
    _load_cal()
    if not _CAL_OK or "trade_date" not in df.columns or df.empty:
        return ""
    try:
        raw = df["trade_date"].iloc[0]
        # CSV read_csv 后 trade_date 常为 int64（如 20261008），pd.to_datetime(int) 会被当
        # 纳秒时间戳解析成 1970 年，故数值一律先转字符串；ISO 字符串走通用解析。
        s = str(int(raw)) if isinstance(raw, (int, float)) else str(raw).strip()
        try:
            target = pd.to_datetime(s, format="%Y%m%d").date()
        except ValueError:
            target = pd.to_datetime(s).date()
    except Exception:
        return ""
    exp_td = latest_expected_td(datetime.datetime.now())
    if exp_td is None or target <= exp_td:
        return ""
    return ("【假期预生成副本】本文件为 %s 交易日生效的候选，数据基准 = 最近应出数据的交易日 %s"
            "（非 %s 当日新产物，系非交易日夜间预生成）。" % (
                target.isoformat(), exp_td.isoformat(), target.isoformat()))


def main():
    ap = argparse.ArgumentParser(description="G2 实盘选股数据健康监控（三道防线·B）")
    ap.add_argument("--date", default=None, help="指定检查日期 YYYYMMDD（默认最新）")
    ap.add_argument("--no-send", action="store_true", help="只打印不推送告警（调试）")
    args = ap.parse_args()

    problems = []   # (level, title, body)
    now = time.time()
    _load_cal()     # 先加载交易日历：后续快照滞后、live 窗口、假期副本标注均依赖它

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
        # 假期语义标注：所检 CSV 若为非交易日夜间预生成的「下一交易日副本」，其数据基准仍是
        # 最近应出数据的交易日（非假期当天）。此时文件名日期 != 实际数据日，告警文案需带标注，
        # 避免把副本误读成当日产物（2026-10-05 实测：20261008 副本实为 09-30 快照口径）。
        basis_note = _basis_note(df)
        print("[CHECK] %s 候选 %d 只 · total_new 最高 %.1f · 过红线(>=%.0f) %d 只" %
              (csv_date, len(df), total_max, RED_LINE, n_pass))
        if basis_note:
            print("[CHECK][INFO] %s" % basis_note)
        print("[CHECK] SC 列唯一值数: %s" % ", ".join("%s=%d" % (k, v) for k, v in sorted(nunique.items())))
        if len(df) < MIN_SAMPLE_ROWS:
            # 行数过少时 nunique 必然 <= 行数（1 行时任何列必为 1），坍缩判定无统计意义。
            # 跳过 nunique 坍缩检查防假阳性（2026-09-28 单行候选误报 6 条坍缩），仅打印信息行。
            print("[CHECK][INFO] %s 仅 %d 行（< %d），样本不足，无法判定评分卡坍缩（跳过 nunique 检查）" %
                  (os.path.basename(csv_path), len(df), MIN_SAMPLE_ROWS))
        else:
            for c in CRITICAL:
                if nunique.get(c, 0) < 2:
                    problems.append(("error", "G2 评分卡坍缩 %s" % c,
                                     "%s 的 %s 唯一值数=%d（恒值=主源失效，2026-08-17~09-18 死代码事故形态）。" % (
                                         os.path.basename(csv_path), c, nunique[c])))
            for c in SC_COLS:
                if c not in CRITICAL and nunique.get(c, 0) < 2:
                    problems.append(("warn", "G2 评分卡信息坍缩 %s" % c,
                                     "%s 的 %s 唯一值数=%d（不致命，留意）。" % (os.path.basename(csv_path), c, nunique[c])))
        if len(df) < MIN_CAND:
            problems.append(("warn", "G2 候选池枯竭",
                             "%s 仅 %d 行候选（< %d），total_new 最高 %.1f（红线 %.0f）。"
                             "候选池枯竭/红线过严或预选池质量差——非评分卡坍缩；次日可能空仓，若连续多日需核查预选池与红线阈值。%s" % (
                                 os.path.basename(csv_path), len(df), MIN_CAND, total_max, RED_LINE, basis_note)))
        if n_pass < 1:
            problems.append(("warn", "G2 候选无过红线",
                             "%s 无候选达到红线 %.0f（最高 %.1f）→ 次日可能空仓。若连续多日需核查评分卡口径。%s" % (
                                 os.path.basename(csv_path), RED_LINE, total_max, basis_note)))

    # ---- 4. 快照主源 ----
    snap_ok = True
    if not os.path.exists(SNAP):
        problems.append(("error", "G2 快照缺失",
                         "data_live/g2_latest_features.parquet 不存在。build_g2_daily 链路断流，次日候选将无特征可推。"))
        snap_ok = False
    else:
        snap = pd.read_parquet(SNAP)
        snap_date = pd.Timestamp(snap["trade_date"].max())
        snap_day = snap_date.date()
        now_dt = datetime.datetime.fromtimestamp(now)
        _load_cal()
        exp_td = latest_expected_td(now_dt)
        td_lag = trade_day_lag(snap_day, exp_td)
        age_days = (now - snap_date.timestamp()) / 86400.0

        if td_lag is None:
            # 日历不可用 → 降级自然日（显式打印，不静默）
            print("[CHECK] 快照最新日 %s（%.1f 天前，降级自然日判定）" %
                  (snap_day.isoformat(), age_days))
            print("[CHECK][INFO] 交易日历不可用，本次按自然日容差 %d 天判定（假期可能仍假阳）" % FRESH_DAYS_FALLBACK)
            if age_days > FRESH_DAYS_FALLBACK:
                problems.append(("warn", "G2 快照滞后",
                                 "快照最新 %s（%.0f 天前），超过容差 %d 天（自然日降级口径）。"
                                 "build_g2_daily / xtdata_update 链可能断流。" % (
                                     snap_day.isoformat(), age_days, FRESH_DAYS_FALLBACK)))
        else:
            print("[CHECK] 快照最新日 %s（最近应出数据的交易日 %s，落后 %d 个交易日；自然日距今 %.1f 天）" %
                  (snap_day.isoformat(), exp_td.isoformat(), td_lag, age_days))
            if td_lag > FRESH_TRADE_DAYS:
                problems.append(("warn", "G2 快照滞后",
                                 "快照最新 %s，落后最近应出数据的交易日 %s 共 %d 个交易日（容差 %d）。"
                                 "build_g2_daily / xtdata_update 链可能断流。" % (
                                     snap_day.isoformat(), exp_td.isoformat(), td_lag, FRESH_TRADE_DAYS)))
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
            _load_cal()
            exp_td = latest_expected_td(datetime.datetime.fromtimestamp(now))
            # 回看窗口：从「最近应出数据的交易日」往前数 LIVE_LOOKBACK_TD 个交易日（含锚日）
            win_start = None
            if _CAL_OK and exp_td is not None:
                d, cnt = exp_td, 0
                while cnt < LIVE_LOOKBACK_TD:
                    if _is_trade_day(d):
                        cnt += 1
                        if cnt == LIVE_LOOKBACK_TD:
                            win_start = d
                            break
                    d -= datetime.timedelta(days=1)
                if win_start is None:
                    win_start = exp_td
                recent = live[live["date"].dt.date >= win_start]
                print("[CHECK] live 日志近 %d 个交易日（自 %s）记录 %d 条" %
                      (LIVE_LOOKBACK_TD, win_start.isoformat(), len(recent)))
            else:
                recent = live[live["date"] >= pd.Timestamp(now - LIVE_LOOKBACK_CAL_FALLBACK * 86400)]
                print("[CHECK][INFO] 交易日历不可用，live 日志按自然日 %d 天判定（假期可能仍假阳）" %
                      LIVE_LOOKBACK_CAL_FALLBACK)
                print("[CHECK] live 日志近 %d 日记录 %d 条" % (LIVE_LOOKBACK_CAL_FALLBACK, len(recent)))
            if len(recent) == 0:
                problems.append((
                    "warn",
                    "G2 live 日志窗口无记录",
                    "paper_forward_live.csv 在最近 %d 个交易日（自 %s）无候选记录。"
                    "候选链可能断流（CSV 有但未追加 live 日志）。" % (
                        LIVE_LOOKBACK_TD if _CAL_OK else LIVE_LOOKBACK_CAL_FALLBACK,
                        win_start.isoformat() if win_start else "今"))
                )
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
