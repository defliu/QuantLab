# coding: utf-8
"""V13（V1.3，桥 p16_v13_risk）实盘选股数据健康监控（三道防线·B，2026-10-12 立）。

与 G2 `scripts/check_scorecard_health.py` 同厚度的独立事后检查（建议调度 20:25，
20:20 Quant_P16_V13_FinalList_2020 落盘后、20:30 PaperParallel 前）。

V13 有三套候选（与 G2/ENS 单套不同），一次全查：
  (a) final_list   20:20 方案B纸面链 data/selections/<date>_final_list.csv（+ meta.json 溯源）
  (b) model_top10  16:30 池候选     data/selections/<date>_model_top10.csv（SC_F1~F6）
  (c) selection_full 09:45 生产换仓 data/selections/<date>_selection_full.csv（rebalance_daily 消费）

检查项：
  1. 三套候选 CSV 均存在、关键列齐全（按 mtime 取最新，假日次日副本同 G2 教训）
  2. 评分卡坍缩（行数 >= MIN_SAMPLE_ROWS(5) 才判定，同 G2 小样本保护）：
     - model_top10 SC_F2 / SC_F5 nunique < 2 → error（死代码事故复发形态，同 G2）
     - final_list  F2 / F5 nunique < 2 → error（build_v13_final 生成期 FAIL-LOUD 的事后兜底）
     - selection_full F2/F5 nunique < 2 → warn（Tushare 快照滞后>2天+实时源失效时回退中性 5 分
       属设计内，但恒值即评分信息丢失，需人工确认数据源；附 f2_source 归因）
  3. 红线通过数（total >= 58）：final_list redline_pass=0 / selection_full 过线=0 → warn
     （宁缺毋滥空仓日属设计内，连续多日才需核查）
  4. final_list.meta.json：存在性、data_max_date（incremental/daily_basic/moneyflow）落后
     「最近应出数据的交易日」> 1 → warn（三源均为当日 16:30/19:30/20:05 刷新，20:20 应齐）；
     f2_source_counts sina+tushare_mf 全 0 → error（生成期 FAIL-LOUD 应已拦截，出现即不一致）
  5. 快照 data_live/latest_features.parquet 按【交易日】新鲜度（容差 1）+ trade_date 列存在
     （该快照为 29 列技术因子，无 mf_main_net/ind_pct_ths——主源完整性由 4 的 meta 承担）
  6. paper_forward_ab_v3enh.csv 最近 LIVE_LOOKBACK_TD(5) 个【交易日】有记录（AB 臂断流检测）
  7. 假期语义标注：复用 SC._basis_note（trade_date 晚于快照锚 → 预生成副本标注）

交易日历/滞后/副本判定复用 `scripts/check_scorecard_health.py` 公共函数（单一事实源）。

退出码：0=PASS，1=有 error，2=仅 warn。异常 → push_alert_card.py 飞书告警。

用法：
  python check_v13_health.py                 # 正常检查 + 异常飞书告警
  python check_v13_health.py --no-send       # 只打印不推送（调试）
  python check_v13_health.py --date 20261012 # 指定检查日期（调试）
"""
import argparse
import datetime
import json
import os
import sys
import time

import pandas as pd

# ---- 复用 G2 健康检查的交易日历/副本判定公共函数（同目录模块，单一事实源）----
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_scorecard_health as SC

# ---- 路径（与 build_v13_final.py / rebalance_daily.py / deploy_predict.py 同源）----
P16 = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
SELECT_DIR = os.path.join(P16, "data", "selections")
SNAP = os.path.join(P16, "data_live", "latest_features.parquet")
LIVE_LOG = os.path.join(P16, "data", "real", "paper_forward_ab_v3enh.csv")
ALERT = os.path.join(P16, "push_alert_card.py")

SC_COLS = ["SC_F1", "SC_F2", "SC_F3", "SC_F4", "SC_F5", "SC_F6"]   # model_top10
F_COLS = ["F1", "F2", "F3", "F4", "F5", "F6"]                        # final_list / selection_full
CRITICAL_SC = {"SC_F2", "SC_F5"}   # 死代码事故复发形态（G2 2026-08-17~09-18 同源）
CRITICAL_F = {"F2", "F5"}
RED_LINE = 58.0            # 与 build_v13_final.REDLINE / rebalance_daily.REDLINE 同值
FRESH_TRADE_DAYS = 1       # 快照新鲜度容差（交易日）
FRESH_DAYS_FALLBACK = 4    # 交易日历不可用时降级的自然日容差
LIVE_LOOKBACK_TD = 5       # live 日志回看窗口（交易日）
LIVE_LOOKBACK_CAL_FALLBACK = 7
MIN_SAMPLE_ROWS = 5        # 坍缩判定最小样本量（同 G2）
SEL_FULL_LAG_TD = 2        # selection_full trade_date 容差：生产链 T-1 数据为准（09:45 用昨日数据）


def _latest_csv(suffix):
    """取 selections/ 下最近生成（mtime 最大）的 <date>_<suffix>.csv，返回 (path, date_str)。

    按 mtime 而非文件名日期取最近：假日/数据日命名错位时按文件名取最大会挑错（同 G2 09-25 教训；
    实测 20260930_selection_full.csv 的 mtime 晚于 20261008_ 即此形态）。
    """
    if not os.path.isdir(SELECT_DIR):
        return None, None
    cands = [f for f in os.listdir(SELECT_DIR)
             if f.endswith("_" + suffix) and f[:8].isdigit() and f[8] == "_"]
    if not cands:
        return None, None
    f = max(cands, key=lambda x: os.path.getmtime(os.path.join(SELECT_DIR, x)))
    return os.path.join(SELECT_DIR, f), f[:8]


def _csv_trade_date(df):
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


def _nunique_guard(df, cols, fname, problems, tag, strict=True, skip=frozenset()):
    """评分卡坍缩检查（带小样本保护 + 底层特征归因 + 链级分层）。

    判据分层（2026-10-12 历史分布实证：32 selection_full + 4 final_list + 29 model_top10）：
      - SC 列（model_top10，16:30 deploy_predict 链）：29 文件 nunique 从未 <2 → CRITICAL 恒值
        = error（事故形态，生成期 FAIL-LOUD 的事后兜底，同 G2）
      - 裸 F 列：nunique=1 高频常态（selection_full F5 约 37% 交易日，10 只同跌同桶属市场真实
        状态），单靠 nunique 判 error 必每周假阳 → 查底层特征列归因：
        * CRITICAL 恒值 且 底层特征列全空 → 主源真断：
          strict=True（final_list，生成期 build_v13_final 有 FAIL-LOUD）→ error
          strict=False（selection_full，09:45 链回退中性分是设计内降级）→ warn
        * CRITICAL 恒值但底层特征有值 → warn（市场分桶巧合，非断源）
      - 非 CRITICAL 列恒值 → warn（信息坍缩不致命）
    行数 < MIN_SAMPLE_ROWS 时整体跳过（nunique 无统计意义，防假阳性，同 G2 小样本保护）。
    """
    if len(df) < MIN_SAMPLE_ROWS:
        print("[%s][INFO] %s 仅 %d 行（< %d），样本不足，跳过 nunique 坍缩检查" %
              (tag, fname, len(df), MIN_SAMPLE_ROWS))
        return
    nuniq = {}
    for c in cols:
        if c in df.columns:
            nuniq[c] = int(df[c].nunique())
    print("[%s] %s 唯一值数: %s" % (tag, fname, ", ".join("%s=%d" % (k, v) for k, v in sorted(nuniq.items()))))
    is_pool = "model" in fname          # model_top10 走 SC 列，无底层特征列可归因
    critical = CRITICAL_SC if is_pool else CRITICAL_F
    # 底层特征列（两链列名不同）：F2 → main_net(final_list) / main_net_inflow(selection_full)
    feat_alias = {"F2": ("main_net", "main_net_inflow"), "F5": ("industry_pct",)}
    for c in critical:
        if c in skip:
            continue   # 调用方已按根因单独告警（如 f2_source 全 missing），避免同一根因两条
        if nuniq.get(c, 1) >= 2:
            continue
        if is_pool:
            problems.append(("error", "%s 坍缩 %s" % (tag, c),
                             "%s 的 %s 唯一值数=%d（恒值=主源失效，2026-08-17~09-18 "
                             "G2 死代码事故同源形态）。" % (fname, c, nuniq[c])))
            continue
        feat = next((a for a in feat_alias.get(c, ()) if a in df.columns), None)
        if feat and df[feat].notna().any():
            problems.append(("warn", "%s 坍缩 %s（底层正常）" % (tag, c),
                             "%s 的 %s 唯一值数=1 但 %s 有值（市场分桶巧合，非断源，"
                             "如全样本同涨跌桶）。" % (fname, c, feat)))
        else:
            lv = "error" if strict else "warn"
            problems.append((lv, "%s 坍缩 %s（底层断源）" % (tag, c),
                             "%s 的 %s 唯一值数=1 且 %s 全空/缺失——主源真断，评分恒中性分。" %
                             (fname, c, feat or "底层特征列（缺列）")))
    for c in cols:
        if c not in critical and nuniq.get(c, 0) < 2:
            problems.append(("warn", "%s 信息坍缩 %s" % (tag, c),
                             "%s 的 %s 唯一值数=%d（不致命，留意）。" % (fname, c, nuniq[c])))


def _check_meta(csv_path, problems, now_dt, exp_td):
    """final_list 溯源 meta.json：存在性 + data_max_date 新鲜度 + f2_source_counts 完整性。"""
    meta_path = csv_path[:-len(".csv")] + ".meta.json"
    if not os.path.exists(meta_path):
        problems.append(("warn", "V13 final_list meta 缺失",
                         "%s 不存在。溯源信息缺失（model/snapshot sha256、数据源日期不可核验）。" %
                         os.path.basename(meta_path)))
        return
    try:
        meta = json.load(open(meta_path, encoding="utf-8"))
    except Exception as e:
        problems.append(("warn", "V13 final_list meta 解析失败", "%r" % e))
        return
    # data_max_date：incremental/daily_basic/moneyflow 当日 16:30/19:30/20:05 刷新，20:20 应 = 当日
    for src, d in (meta.get("data_max_date") or {}).items():
        try:
            d_date = pd.to_datetime(str(d)).date()
        except Exception:
            continue
        lag = SC.trade_day_lag(d_date, exp_td)
        print("[V13-CHECK] meta data_max_date.%s = %s（落后 %s 个%s）" %
              (src, d_date.isoformat(), lag,
               "交易日" if lag is not None else "（日历不可用）"))
        if lag is not None and lag > FRESH_TRADE_DAYS:
            problems.append(("warn", "V13 数据源滞后 %s" % src,
                             "meta data_max_date.%s=%s 落后最近应出数据的交易日 %s 共 %d 个"
                             "交易日（容差 %d）。刷新链（16:30 incremental / 19:30 moneyflow / "
                             "20:05 daily_basic）可能断流。" %
                             (src, d_date.isoformat(), exp_td.isoformat(), lag, FRESH_TRADE_DAYS)))
    # f2_source_counts：生成期 FAIL-LOUD（全部缺失即中止）之后仍全 0 → 产物与口径不一致
    f2c = meta.get("f2_source_counts") or {}
    n_ok = int(f2c.get("sina", 0)) + int(f2c.get("tushare_mf", 0))
    if n_ok == 0:
        problems.append(("error", "V13 final_list F2 主源全缺失",
                         "meta f2_source_counts sina+tushare_mf 合计 0（%r）。"
                         "生成期 FAIL-LOUD（build_v13_final L141）应已拦截，出现即产物不一致。" % f2c))


def main():
    ap = argparse.ArgumentParser(description="V13 实盘选股数据健康监控（三道防线·B）")
    ap.add_argument("--date", default=None, help="指定检查日期 YYYYMMDD（默认最新）")
    ap.add_argument("--no-send", action="store_true", help="只打印不推送告警（调试）")
    args = ap.parse_args()

    problems = []
    now = time.time()
    now_dt = datetime.datetime.fromtimestamp(now)
    SC._load_cal()
    exp_td = SC.latest_expected_td(now_dt)

    # ---- (a) final_list（方案B纸面链 20:20 产物 + meta 溯源）----
    fl_path, fl_date = _latest_csv("final_list.csv")
    if args.date:
        p = os.path.join(SELECT_DIR, "%s_final_list.csv" % args.date)
        if os.path.exists(p):
            fl_path, fl_date = p, args.date
    if not fl_path or not os.path.exists(fl_path):
        problems.append(("error", "V13 final_list 缺失",
                         "data/selections/ 下无 *_final_list.csv。方案B纸面链断流，"
                         "v13_paper_parallel 将记 __NOFILE__。"))
    else:
        df = pd.read_csv(fl_path, encoding="utf-8-sig")
        missing = [c for c in F_COLS + ["total", "trade_date"] if c not in df.columns]
        basis_note = SC._basis_note(df)
        if missing:
            problems.append(("error", "V13 final_list 缺列",
                             "%s 缺列 %s。评分输出异常（疑似代码/列名变更）。" %
                             (os.path.basename(fl_path), ",".join(missing))))
        else:
            ctd = _csv_trade_date(df)
            csv_lag = SC.trade_day_lag(ctd, exp_td) if ctd else None
            n_pass = int((df["total"] >= RED_LINE).sum())
            total_max = float(df["total"].max())
            print("[V13-CHECK] final_list %s · %d 只 · total 最高 %.1f · 过红线(>=%.0f) %d 只%s" %
                  (fl_date, len(df), total_max, RED_LINE, n_pass,
                   "（落后 %d 个交易日）" % csv_lag if csv_lag is not None else ""))
            if basis_note:
                print("[V13-CHECK][INFO] %s" % basis_note)
            if csv_lag is not None and csv_lag > FRESH_TRADE_DAYS:
                problems.append(("warn", "V13 final_list 滞后",
                                 "%s 的 trade_date=%s 落后最近应出数据的交易日 %s 共 %d 个"
                                 "交易日（容差 %d）。20:20 链可能断流。%s" %
                                 (os.path.basename(fl_path), ctd.isoformat(),
                                  exp_td.isoformat(), csv_lag, FRESH_TRADE_DAYS, basis_note)))
            _nunique_guard(df, F_COLS, os.path.basename(fl_path), problems, "V13-final")
            if n_pass < 1:
                problems.append(("warn", "V13 final_list 无过红线",
                                 "%s 无候选过红线 %.0f（最高 %.1f）→ 空仓日。宁缺毋滥属设计内，"
                                 "连续多日需核查红线口径。%s" %
                                 (os.path.basename(fl_path), RED_LINE, total_max, basis_note)))
            if exp_td is not None:
                _check_meta(fl_path, problems, now_dt, exp_td)

    # ---- (b) model_top10（16:30 池候选，SC 评分卡）----
    mt_path, mt_date = _latest_csv("model_top10.csv")
    if not mt_path or not os.path.exists(mt_path):
        problems.append(("error", "V13 model_top10 缺失",
                         "data/selections/ 下无 *_model_top10.csv。16:30 deploy_predict 链断流，"
                         "final_list 将无池可用。"))
    else:
        df = pd.read_csv(mt_path, encoding="utf-8-sig")
        missing = [c for c in SC_COLS if c not in df.columns]
        if missing:
            problems.append(("error", "V13 model_top10 缺评分卡列",
                             "%s 缺列 %s。" % (os.path.basename(mt_path), ",".join(missing))))
        else:
            ctd = _csv_trade_date(df)
            csv_lag = SC.trade_day_lag(ctd, exp_td) if ctd else None
            print("[V13-CHECK] model_top10 %s · %d 只%s" %
                  (mt_date, len(df),
                   "（落后 %d 个交易日）" % csv_lag if csv_lag is not None else ""))
            if csv_lag is not None and csv_lag > FRESH_TRADE_DAYS:
                problems.append(("warn", "V13 model_top10 滞后",
                                 "%s 的 trade_date=%s 落后最近应出数据的交易日 %s 共 %d 个"
                                 "交易日（容差 %d）。16:30 链可能断流。" %
                                 (os.path.basename(mt_path), ctd.isoformat(),
                                  exp_td.isoformat(), csv_lag, FRESH_TRADE_DAYS)))
            _nunique_guard(df, SC_COLS, os.path.basename(mt_path), problems, "V13-pool")

    # ---- (c) selection_full（09:45 生产换仓，rebalance_daily 消费）----
    sf_path, sf_date = _latest_csv("selection_full.csv")
    if not sf_path or not os.path.exists(sf_path):
        problems.append(("error", "V13 selection_full 缺失",
                         "data/selections/ 下无 *_selection_full.csv。09:45 生产换仓链断流，"
                         "rebalance_daily 将无清单可用。"))
    else:
        df = pd.read_csv(sf_path, encoding="utf-8-sig")
        missing = [c for c in F_COLS + ["total"] if c not in df.columns]
        if missing:
            problems.append(("error", "V13 selection_full 缺列",
                             "%s 缺列 %s。" % (os.path.basename(sf_path), ",".join(missing))))
        else:
            # trade_date = T-1 数据日（09:45 用昨日权威数据），容差 2 个交易日（含长假断档）
            ctd = _csv_trade_date(df)
            csv_lag = SC.trade_day_lag(ctd, exp_td) if ctd else None
            n_pass = int((df["total"] >= RED_LINE).sum())
            # f2_source 归因：全 missing → F2 恒中性 5 分（Tushare 快照>2天+实时失效的回退态，
            # review_full L311-321 设计内降级，仅 WARN 不 fail → 检查级别 = warn 非 error）
            f2_attr = ""
            if "f2_source" in df.columns and set(df["f2_source"].value_counts()) and \
                    set(df["f2_source"].unique()) == {"missing"}:
                f2_attr = "（f2_source 全 missing → F2 恒中性 5 分，设计内回退态）"
                problems.append(("warn", "V13 selection_full F2 主源全缺失",
                                 "%s 的 f2_source 全部 missing → F2 恒中性 5 分（评分信息丢失）。"
                                 "Tushare moneyflow 本地快照（19:30 刷新，新鲜 lag<=2 优先）与"
                                 "实时源双失效；review_full 设计内降级（仅 WARN），但需核查数据刷新。"
                                 % os.path.basename(sf_path)))
            total_max = float(df["total"].max())
            print("[V13-CHECK] selection_full %s · %d 只 · total 最高 %.1f · 过红线(>=%.0f) %d 只%s%s" %
                  (sf_date, len(df), total_max, RED_LINE, n_pass,
                   "（trade_date 落后 %d 个交易日）" % csv_lag if csv_lag is not None else "", f2_attr))
            if csv_lag is not None and csv_lag > SEL_FULL_LAG_TD:
                problems.append(("warn", "V13 selection_full 滞后",
                                 "%s 的 trade_date=%s 落后最近应出数据的交易日 %s 共 %d 个"
                                 "交易日（容差 %d，生产链 T-1 口径）。09:45 链可能断流。" %
                                 (os.path.basename(sf_path), ctd.isoformat(),
                                  exp_td.isoformat(), csv_lag, SEL_FULL_LAG_TD)))
            # 坍缩检查（strict=False：09:45 链回退中性分属设计内降级 → 断源最多 warn；
            # f2 已按 f2_source 根因单独告警 → skip F2 防同一根因两条）
            _nunique_guard(df, F_COLS, os.path.basename(sf_path), problems, "V13-sel",
                           strict=False, skip={"F2"} if f2_attr else frozenset())
            if n_pass < 1:
                problems.append(("warn", "V13 selection_full 无过红线",
                                 "%s 无候选过红线 %.0f（最高 %.1f）→ 生产日不买入（宁缺毋滥，"
                                 "rebalance_daily L579 设计内）。连续多日需核查红线口径。" %
                                 (os.path.basename(sf_path), RED_LINE, total_max)))

    # ---- 5. 快照新鲜度（latest_features：29 列技术因子，主源完整性由 meta 承担）----
    if not os.path.exists(SNAP):
        problems.append(("error", "V13 快照缺失",
                         "data_live/latest_features.parquet 不存在。merge_live_features / 16:30 链断流。"))
    else:
        snap = pd.read_parquet(SNAP, columns=["trade_date"])
        snap_day = pd.Timestamp(snap["trade_date"].max()).date()
        td_lag = SC.trade_day_lag(snap_day, exp_td)
        age_days = (now - pd.Timestamp(snap["trade_date"].max()).timestamp()) / 86400.0
        if td_lag is None:
            print("[V13-CHECK][INFO] 交易日历不可用，快照按自然日容差 %d 天判定" % FRESH_DAYS_FALLBACK)
            if age_days > FRESH_DAYS_FALLBACK:
                problems.append(("warn", "V13 快照滞后",
                                 "快照最新 %s（%.0f 天前），超过自然日容差 %d 天（降级口径）。" %
                                 (snap_day.isoformat(), age_days, FRESH_DAYS_FALLBACK)))
        else:
            print("[V13-CHECK] 快照最新日 %s（最近应出数据的交易日 %s，落后 %d 个交易日）" %
                  (snap_day.isoformat(), exp_td.isoformat(), td_lag))
            if td_lag > FRESH_TRADE_DAYS:
                problems.append(("warn", "V13 快照滞后",
                                 "快照最新 %s，落后最近应出数据的交易日 %s 共 %d 个交易日（容差 %d）。"
                                 "merge_live_features / xtdata_update 链可能断流。" %
                                 (snap_day.isoformat(), exp_td.isoformat(), td_lag, FRESH_TRADE_DAYS)))

    # ---- 6. paper_forward_ab_v3enh.csv（V13 AB 臂 live 日志）----
    if os.path.exists(LIVE_LOG):
        live = pd.read_csv(LIVE_LOG, encoding="utf-8-sig")
        if "date" in live.columns:
            live["date"] = pd.to_datetime(live["date"])
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
                print("[V13-CHECK] AB live 日志近 %d 个交易日（自 %s）记录 %d 条" %
                      (LIVE_LOOKBACK_TD, win_start.isoformat(), len(recent)))
            else:
                recent = live[live["date"] >= pd.Timestamp(now - LIVE_LOOKBACK_CAL_FALLBACK * 86400)]
                print("[V13-CHECK][INFO] 交易日历不可用，AB live 按自然日 %d 天判定" %
                      LIVE_LOOKBACK_CAL_FALLBACK)
            if len(recent) == 0:
                problems.append((
                    "warn", "V13 AB live 日志窗口无记录",
                    "paper_forward_ab_v3enh.csv 在最近 %d 个%s无记录。"
                    "AB 臂追加链可能断流（候选有但未写日志）。" % (
                        LIVE_LOOKBACK_TD if SC._CAL_OK else LIVE_LOOKBACK_CAL_FALLBACK,
                        "交易日（自 %s）" % win_start.isoformat() if win_start else "自然日")))
            else:
                print("[V13-CHECK] AB live 日志最新 %s" % recent["date"].max().strftime("%Y-%m-%d"))
        else:
            problems.append(("warn", "V13 AB live 无 date 列", "paper_forward_ab_v3enh.csv 结构异常。"))
    else:
        problems.append(("warn", "V13 AB live 日志缺失",
                         "paper_forward_ab_v3enh.csv 不存在。若为初始部署可忽略，否则 AB 臂链路异常。"))

    _finish(problems, args.no_send)


def _finish(problems, no_send):
    if not problems:
        print("[V13-CHECK] 全部健康项 PASS（exit=0）")
        sys.exit(0)
    for lv, title, body in problems:
        print("[V13-CHECK][%s] %s: %s" % (lv.upper(), title, body))
    errs = [p for p in problems if p[0] == "error"]
    warns = [p for p in problems if p[0] == "warn"]
    _push(errs, "error", no_send)
    _push(warns, "warn", no_send)
    sys.exit(1 if errs else 2)


def _push(items, level, no_send):
    if not items:
        return
    title = "V13 数据健康[%s] %d 项" % ("ERROR" if level == "error" else "WARN", len(items))
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
