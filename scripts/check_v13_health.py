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
     - F3 / SC_F3 恒值 → INFO（特征面板 v3 缺 ex_days_since/fc_pchange/dv_year_sum ⇒
       _score_f3 恒等基线 4.0，结构性非断源，见 STRUCTURAL_PINNED）
     - F4 / SC_F4 恒值 → 回面板查 rsi6/macd_hist：有值 ⇒ INFO（全样本同技术形态桶，市场条件）；
       全空/读不到 ⇒ warn（技术面主源断流）
     - 其余列恒值 → warn（信息坍缩不致命）
  3. 红线通过数（total >= 58）：final_list redline_pass=0 / selection_full 过线=0 → warn
     （宁缺毋滥空仓日属设计内，连续多日才需核查）
  4. final_list.meta.json：存在性、data_max_date（market/incremental/daily_basic/moneyflow）落后
     「最近应出数据的交易日」> 1 → warn（market=17:40 主库，incremental=16:30 增量库，
     daily_basic=20:05，moneyflow=19:30）；f2_source_counts sina+tushare_mf 全 0 → error
  5. 特征面板 data/feature_panel_v3.parquet 按【交易日】新鲜度（容差 1）——信号真实来源；
     data_live/latest_features.parquet 仅「比面板新时优先」的增量覆盖，陈旧只打 INFO 不告警
  6. paper_forward_ab_v3enh.csv 已退役（全仓库无写入者），只打 INFO 不告警
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

# 中文/• 等字符在 GBK 控制台下会抛 UnicodeEncodeError（会让定时任务连日志都拿不到，
# 且推卡片那行 print 先崩、告警根本没发出去）。统一降级替换，绝不因编码中断。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

# ---- 复用 G2 健康检查的交易日历/副本判定公共函数（同目录模块，单一事实源）----
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_scorecard_health as SC

# ---- 路径（与 build_v13_final.py / rebalance_daily.py / deploy_predict.py 同源）----
P16 = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
SELECT_DIR = os.path.join(P16, "data", "selections")
PANEL = os.path.join(P16, "data", "feature_panel_v3.parquet")   # 特征面板＝信号真实来源（权威）
SNAP = os.path.join(P16, "data_live", "latest_features.parquet")  # 仅增量覆盖，陈旧不影响信号日
LIVE_LOG = os.path.join(P16, "data", "real", "paper_forward_ab_v3enh.csv")  # 已退役臂，仅 INFO
ALERT = os.path.join(P16, "push_alert_card.py")

SC_COLS = ["SC_F1", "SC_F2", "SC_F3", "SC_F4", "SC_F5", "SC_F6"]   # model_top10
F_COLS = ["F1", "F2", "F3", "F4", "F5", "F6"]                        # final_list / selection_full
CRITICAL_SC = {"SC_F2", "SC_F5"}   # 死代码事故复发形态（G2 2026-08-17~09-18 同源）
CRITICAL_F = {"F2", "F5"}
# 结构性恒值（口径决定，不是断源）：特征面板 v3 只保留 32 列，缺 ex_days_since / fc_pchange /
# dv_year_sum ⇒ deploy_predict._score_f3 的三条加分分支永不成立，恒等于基线 4.0。
# 这是当前口径的必然结果，面板不补列就不会变，故只打 INFO、不进每日告警（2026-11-01 立）。
STRUCTURAL_PINNED = {
    "F3": "特征面板 v3 无 ex_days_since/fc_pchange/dv_year_sum ⇒ _score_f3 恒等基线 4.0（结构性，非断源）",
    "SC_F3": "同上（deploy_predict._score_f3 同口径，恒等基线 4.0）",
}
# 可归因的非 CRITICAL 列：底层特征（特征面板列）有值 ⇒ 市场同桶（INFO）；全空/读不到 ⇒ warn 断源
FEAT_ATTRIB = {"F4": ("rsi6", "macd_hist"), "SC_F4": ("rsi6", "macd_hist")}
RED_LINE = 58.0            # 与 build_v13_final.REDLINE / rebalance_daily.REDLINE 同值
FRESH_TRADE_DAYS = 1       # 快照新鲜度容差（交易日）
FRESH_DAYS_FALLBACK = 4    # 交易日历不可用时降级的自然日容差
MIN_SAMPLE_ROWS = 5        # 坍缩判定最小样本量（同 G2）
SEL_FULL_LAG_TD = 2        # selection_full trade_date 容差：生产链 T-1 数据为准（09:45 用昨日数据）
_PANEL_CACHE = {}          # (日期, 列名) → 面板当日特征子集，供坍缩归因复用


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


def _panel_feat_cols(day, cols):
    """读特征面板某交易日的底层特征列，供坍缩归因（缓存，失败返回 None）。

    CSV 产物本身不含 rsi6/macd_hist（final_list / model_top10 / selection_full 都没有），
    要判「F4 恒值是市场同桶还是底层断源」只能回面板看这两列有没有值。
    """
    key = (str(day), tuple(cols))
    if key in _PANEL_CACHE:
        return _PANEL_CACHE[key]
    out = None
    try:
        p = pd.read_parquet(PANEL, columns=["trade_date"] + list(cols))
        p = p[p["trade_date"] == pd.Timestamp(str(day))]
        if len(p):
            out = p
    except Exception as e:
        print("[V13-CHECK][INFO] 面板特征读取失败（%s），坍缩归因降级" % e)
    _PANEL_CACHE[key] = out
    return out


def _nunique_guard(df, cols, fname, problems, tag, strict=True, skip=frozenset(), day=None):
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
        if c in critical or nuniq.get(c, 0) >= 2:
            continue
        if c in STRUCTURAL_PINNED:
            print("[%s][INFO] %s 的 %s 唯一值数=%d 属结构性恒值：%s" %
                  (tag, fname, c, nuniq[c], STRUCTURAL_PINNED[c]))
            continue
        if c in FEAT_ATTRIB:
            attrs = list(FEAT_ATTRIB[c])
            pf = _panel_feat_cols(day, attrs) if day is not None else None
            if pf is not None and all(pf[a].notna().any() for a in attrs):
                print("[%s][INFO] %s 的 %s 唯一值数=%d，面板当日 %s 均有值（全样本同技术形态桶，"
                      "市场条件，非断源）" % (tag, fname, c, nuniq[c], "/".join(attrs)))
                continue
            problems.append(("warn", "%s 坍缩 %s（底层断源）" % (tag, c),
                             "%s 的 %s 唯一值数=%d 且面板当日 %s 全空/读不到——技术面主源可能断流"
                             "（build_features_v2 链），需核查。" %
                             (fname, c, nuniq[c], "/".join(attrs))))
            continue
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
    # data_max_date：market（主库 17:40 / 增量库兜底）/ daily_basic（20:05）/ moneyflow（19:30），20:20 应 = 当日
    # 2026-10-31 起 build_v13_final 改为「主库优先」口径，键名 incremental → market（旧产物仍可能是 incremental）
    SRC_CHAIN = {"market": "17:40 主库 Tushare", "incremental": "16:30 增量库",
                 "daily_basic": "20:05 daily_basic", "moneyflow": "19:30 moneyflow"}
    for src, d in (meta.get("data_max_date") or {}).items():
        if src not in SRC_CHAIN:      # 如 market_source="main"（源标签，不是日期）
            continue
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
                             "交易日（容差 %d）。刷新链（%s）可能断流。" %
                             (src, d_date.isoformat(), exp_td.isoformat(), lag,
                              FRESH_TRADE_DAYS, SRC_CHAIN[src])))
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
            _nunique_guard(df, F_COLS, os.path.basename(fl_path), problems, "V13-final", day=ctd)
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
            _nunique_guard(df, SC_COLS, os.path.basename(mt_path), problems, "V13-pool", day=ctd)

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
                           strict=False, skip={"F2"} if f2_attr else frozenset(), day=ctd)
            if n_pass < 1:
                problems.append(("warn", "V13 selection_full 无过红线",
                                 "%s 无候选过红线 %.0f（最高 %.1f）→ 生产日不买入（宁缺毋滥，"
                                 "rebalance_daily L579 设计内）。连续多日需核查红线口径。" %
                                 (os.path.basename(sf_path), RED_LINE, total_max)))

    # ---- 5. 快照新鲜度 ----
    # 权威 = 特征面板 data/feature_panel_v3.parquet（build_v13_final / deploy_predict 实际消费的面板，
    #        由 build_features_v2 维护；2026-10-31 改口径前误把 latest_features 当权威）。
    # latest_features.parquet 只是「比面板更新时优先使用」的增量覆盖（deploy_predict._prefer_snapshot
    # 判 snap_max > panel_max 才启用），它陈旧不影响信号日，故只作 INFO 打印、不再告警。
    if not os.path.exists(PANEL):
        problems.append(("error", "V13 特征面板缺失",
                         "data/feature_panel_v3.parquet 不存在。build_features_v2 链断流，"
                         "build_v13_final / deploy_predict 均无信号日可用。"))
    else:
        panel = pd.read_parquet(PANEL, columns=["trade_date"])
        panel_day = pd.Timestamp(panel["trade_date"].max()).date()
        td_lag = SC.trade_day_lag(panel_day, exp_td)
        age_days = (now - pd.Timestamp(panel["trade_date"].max()).timestamp()) / 86400.0
        if td_lag is None:
            print("[V13-CHECK][INFO] 交易日历不可用，特征面板按自然日容差 %d 天判定" % FRESH_DAYS_FALLBACK)
            if age_days > FRESH_DAYS_FALLBACK:
                problems.append(("warn", "V13 特征面板滞后",
                                 "feature_panel_v3 最新 %s（%.0f 天前），超过自然日容差 %d 天（降级口径）。" %
                                 (panel_day.isoformat(), age_days, FRESH_DAYS_FALLBACK)))
        else:
            print("[V13-CHECK] 特征面板最新日 %s（最近应出数据的交易日 %s，落后 %d 个交易日）" %
                  (panel_day.isoformat(), exp_td.isoformat(), td_lag))
            if td_lag > FRESH_TRADE_DAYS:
                problems.append(("warn", "V13 特征面板滞后",
                                 "feature_panel_v3 最新 %s，落后最近应出数据的交易日 %s 共 %d 个交易日"
                                 "（容差 %d）。build_features_v2 / 17:40 主库链可能断流。" %
                                 (panel_day.isoformat(), exp_td.isoformat(), td_lag, FRESH_TRADE_DAYS)))
    if os.path.exists(SNAP):
        try:
            s_day = pd.Timestamp(pd.read_parquet(SNAP, columns=["trade_date"])["trade_date"].max()).date()
            print("[V13-CHECK][INFO] latest_features 最新 %s（仅增量覆盖，陈旧不影响信号日）" % s_day.isoformat())
        except Exception as e:
            print("[V13-CHECK][INFO] latest_features 读取失败（不影响）: %r" % e)
    else:
        print("[V13-CHECK][INFO] latest_features 不存在（仅增量覆盖，不影响信号日）")

    # ---- 6. paper_forward_ab_v3enh.csv（V13 AB 臂 live 日志）----
    # 2026-10-31 定论：该臂**已退役**——全仓库无任何 .py/.ps1 写入者（仅 deploy_predict_g2 保留
    # --ab-log 形参但无人传参），纸面前向已由 43f 主臂（paper_forward_live.csv）+
    # 21f 影子臂（paper_shadow_b21）承担。故陈旧只作 INFO，不再告警（避免每天一条结构性假警）。
    if os.path.exists(LIVE_LOG):
        try:
            live = pd.read_csv(LIVE_LOG, encoding="utf-8-sig")
            last = pd.to_datetime(live["date"]).max().date() if "date" in live.columns else None
            print("[V13-CHECK][INFO] AB 臂日志（已退役，仅留档）最新 %s · %d 条" %
                  (last.isoformat() if last else "?", len(live)))
        except Exception as e:
            print("[V13-CHECK][INFO] AB 臂日志读取失败（已退役，不影响）: %r" % e)
    else:
        print("[V13-CHECK][INFO] AB 臂日志不存在（已退役，不告警）")

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
