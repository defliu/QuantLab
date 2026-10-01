# -*- coding: utf-8 -*-
"""v13_paper_parallel.py — 方案B 纸面并行：old链(09:45重排) vs new链(T-1晚final_list) 双臂前向对比。

SPEC：specs/V13口径对齐方案B落地_SPEC_20260930.md §3.2（T-20260929-006）。

双臂定义（执行日 T，两臂统一 T 开盘入场）：
  old臂 = <T>_selection_full.csv 中 total>=58 按 total 降序 Top5（旧链 09:45 重排产物）
  new臂 = D=max(final_list日期 < T) 的 <D>_final_list.csv 中 redline_pass 按 total 降序 Top5
          （跨节假日自动配对，如 09-30 final_list → 10-09 执行日）
两臂均不施加 position_filter（PF 是执行层正交件，live 两链都保留 lite，信号对比保持纯度）。

收益口径：entry=执行日 T 开盘；exit=第 HOLD_DAYS(=5) 个交易日开盘；另记 ret_1d。
停牌/无量 → fillable=False 剔除统计；一字涨停无法用 open 面板判定（诚实降级，报告有注记）。

幂等：log 按 (exec_date, arm, ts_code) 去重 keep last；同日重跑安全。
fail-loud：数据源停更（合并面板最新交易日距今 >5 自然日）exit 2（同 paper_forward 惯例）。
门禁（SPEC §5，仅报告展示，Phase 2 另启）：≥15 执行交易日配对样本；
  new−old 日均配对差 ≥ -3bp/日；胜率差 ≤10pp。

用法：
  python v13_paper_parallel.py              # 默认：record(若交易日) + backfill + report
  python v13_paper_parallel.py --no-record  # 只 backfill + report
  python v13_paper_parallel.py --hold 5
"""
import argparse
import datetime
import glob
import os
import re
import sys

import numpy as np
import pandas as pd

import data_config as DC
import paper_forward as PFWD  # 复用 load_open_panel（import 副作用：BT_* env + scan_rotate_cost_real，无害）

PROJ = DC.PROJECT_DIR
SELECT_DIR = os.path.join(DC.DATA_DIR, "selections")
RESULTS = os.path.join(PROJ, "results")
LOG_CSV = os.path.join(RESULTS, "v13_paper_parallel_log.csv")
REPORT_MD = os.path.join(RESULTS, "v13_paper_parallel_report.md")
INCR = os.path.join(DC.LIVE_DIR, "incremental_daily.parquet")

REDLINE = 58.0     # 与 build_v13_final / rebalance_daily 同值
TOP_N = 5          # 与 build_v13_final --top 同值
HOLD_DAYS = 5      # SPEC §3.2：第 5 个交易日开盘退出
GATE_MIN_DAYS = 15     # SPEC §5 门禁样本下限
GATE_DIFF_BP = -3.0    # new−old 日均配对差下限（bp/日）
GATE_WIN_PP = 10.0     # 胜率差上限（pp）
EMPTY, NOFILE = "__EMPTY__", "__NOFILE__"   # 哨兵行：空仓日 / 臂产物缺失日

LOG_COLS = ["exec_date", "arm", "signal_date", "ts_code", "rank", "total",
            "model_prob", "fillable", "entry", "exit", "ret", "ret_1d", "note"]


def _ts(d):
    return pd.Timestamp(d).strftime("%Y-%m-%d")


def _sentinel(T, arm, kind, signal_date=""):
    return {"exec_date": _ts(T), "arm": arm, "signal_date": signal_date, "ts_code": kind,
            "rank": 0, "total": np.nan, "model_prob": np.nan,
            "fillable": False, "entry": np.nan, "exit": np.nan,
            "ret": np.nan, "ret_1d": np.nan,
            "note": "红线0过(空仓日)" if kind == EMPTY else "臂产物文件缺失"}


def append_dedup(rows):
    """幂等追加 log：读现有 → concat → 按 (exec_date, arm, ts_code) 去重 keep last → 原子写回。"""
    new = pd.DataFrame(rows, columns=LOG_COLS)
    if os.path.exists(LOG_CSV):
        old = pd.read_csv(LOG_CSV, encoding="utf-8-sig", dtype={"ts_code": str})
        df = pd.concat([old, new], ignore_index=True)
    else:
        df = new
    df = df.drop_duplicates(subset=["exec_date", "arm", "ts_code"], keep="last")
    df = df.sort_values(["exec_date", "arm", "rank", "ts_code"]).reset_index(drop=True)
    tmp = LOG_CSV + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, LOG_CSV)


def record(T):
    """记录执行日 T 的双臂选股（20:30 跑，两臂产物均已就位）。"""
    rows = []
    tstr = T.strftime("%Y%m%d")

    # ---- old 臂：T 日 09:45 重排产物 ----
    p_old = os.path.join(SELECT_DIR, "%s_selection_full.csv" % tstr)
    if not os.path.isfile(p_old):
        print("    [WARN] old臂 %s_selection_full.csv 不存在（旧链09:45未产出）" % tstr)
        rows.append(_sentinel(T, "old", NOFILE))
    else:
        sf = pd.read_csv(p_old, encoding="utf-8-sig")
        sf["ts_code"] = sf["ts_code"].astype(str)
        if "total" not in sf.columns:
            print("    [WARN] old臂 selection_full 无 total 列")
            rows.append(_sentinel(T, "old", NOFILE))
        else:
            sf = sf[sf["total"] >= REDLINE].sort_values("total", ascending=False).head(TOP_N)
            if len(sf) == 0:
                print("    old臂红线 %.0f 过滤后 0 只（空仓日）" % REDLINE)
                rows.append(_sentinel(T, "old", EMPTY, signal_date=_ts(T)))
            else:
                for rk, (_, s) in enumerate(sf.iterrows(), start=1):
                    rows.append({"exec_date": _ts(T), "arm": "old", "signal_date": _ts(T),
                                 "ts_code": s["ts_code"], "rank": rk, "total": float(s["total"]),
                                 "model_prob": float(s["model_prob"]) if "model_prob" in sf.columns else np.nan,
                                 "fillable": True, "entry": np.nan, "exit": np.nan,
                                 "ret": np.nan, "ret_1d": np.nan, "note": ""})

    # ---- new 臂：D = max(final_list 日期 < T)，严格小于 T（当日 20:20 产物服务于 T+1）----
    fl_files = glob.glob(os.path.join(SELECT_DIR, "*_final_list.csv"))
    fl_dates = []
    for p in fl_files:
        m = re.match(r"^(\d{8})_final_list\.csv$", os.path.basename(p))
        if m:
            d = pd.Timestamp(m.group(1))
            if d < T:
                fl_dates.append(d)
    if not fl_dates:
        print("    [WARN] new臂无早于 %s 的 final_list（首日或产物缺失）" % T.date())
        rows.append(_sentinel(T, "new", NOFILE))
    else:
        D = max(fl_dates)
        fl = pd.read_csv(os.path.join(SELECT_DIR, "%s_final_list.csv" % D.strftime("%Y%m%d")),
                         encoding="utf-8-sig", dtype={"ts_code": str})
        if "redline_pass" not in fl.columns:
            print("    [WARN] new臂 final_list 无 redline_pass 列")
            rows.append(_sentinel(T, "new", NOFILE, signal_date=_ts(D)))
        else:
            fl = fl[fl["redline_pass"].astype(str).str.lower() == "true"]
            fl = fl.sort_values("total", ascending=False).head(TOP_N)
            if len(fl) == 0:
                print("    new臂红线 %.0f 过滤后 0 只（空仓日，信号日 %s）" % (REDLINE, D.date()))
                rows.append(_sentinel(T, "new", EMPTY, signal_date=_ts(D)))
            else:
                for rk, (_, s) in enumerate(fl.iterrows(), start=1):
                    rows.append({"exec_date": _ts(T), "arm": "new", "signal_date": _ts(D),
                                 "ts_code": s["ts_code"], "rank": rk, "total": float(s["total"]),
                                 "model_prob": float(s["model_prob"]) if "model_prob" in fl.columns else np.nan,
                                 "fillable": True, "entry": np.nan, "exit": np.nan,
                                 "ret": np.nan, "ret_1d": np.nan, "note": ""})
    append_dedup(rows)
    n_old = sum(1 for r in rows if r["arm"] == "old" and r["ts_code"] not in (EMPTY, NOFILE))
    n_new = sum(1 for r in rows if r["arm"] == "new" and r["ts_code"] not in (EMPTY, NOFILE))
    print("    记录完成：old臂 %d 只 | new臂 %d 只 → %s" % (n_old, n_new, os.path.basename(LOG_CSV)))


def backfill(hold):
    """回填全部行的 entry/exit/ret/ret_1d（复用 load_open_panel 主库+增量合并 open 面板）。

    新值 NaN 时保留旧值（防数据源抖动回退，同 paper_forward.backfill_live）。
    返回 stale 标志（合并面板最新交易日距今 >5 自然日）。
    """
    if not os.path.exists(LOG_CSV):
        print("    无 log，跳过 backfill")
        return False
    df = pd.read_csv(LOG_CSV, encoding="utf-8-sig", dtype={"ts_code": str})
    daily, main_last, inc_last = PFWD.load_open_panel()
    trades = sorted(set(daily.index.get_level_values(0)))
    trade_idx = {t: i for i, t in enumerate(trades)}
    open_map = daily["open"].to_dict()
    # 无量检查：增量库覆盖日可查 volume/suspendFlag
    vol_map, susp_map = {}, {}
    if os.path.isfile(INCR):
        try:
            inc = pd.read_parquet(INCR)
            if {"trade_date", "ts_code", "volume"}.issubset(inc.columns):
                inc2 = inc.copy()
                inc2["trade_date"] = pd.to_datetime(inc2["trade_date"])
                iv = inc2.set_index(["trade_date", inc2["ts_code"].astype(str)])["volume"]
                vol_map = iv.to_dict()
                if "suspendFlag" in inc2.columns:
                    susp_map = inc2.set_index(["trade_date", inc2["ts_code"].astype(str)])["suspendFlag"].to_dict()
        except Exception as e:
            print("    [WARN] 增量库无量检查读取失败: %s" % e)

    stale = False
    for i, r in df.iterrows():
        if r["ts_code"] in (EMPTY, NOFILE):
            continue
        T = pd.Timestamp(r["exec_date"])
        code = str(r["ts_code"])
        if T not in trade_idx:
            continue  # 执行日尚未被面板覆盖（等待主库周更/增量）
        idx = trade_idx[T]
        old_ret, old_ret1 = r.get("ret"), r.get("ret_1d")
        old_entry = r.get("entry")
        key = (T, code)
        entry = open_map.get(key)
        vol = vol_map.get(key)
        susp = susp_map.get(key)
        note = ""
        if susp is not None and not (isinstance(susp, float) and np.isnan(susp)) and susp == 1:
            note = "suspend"
        elif vol is not None and not (isinstance(vol, float) and np.isnan(vol)) and vol <= 0:
            note = "no_volume"
        if entry is None or (isinstance(entry, float) and np.isnan(entry)) or entry <= 0:
            note = note or "no_open"
            df.at[i, "fillable"] = False
            df.at[i, "note"] = note
            continue
        if note:
            df.at[i, "fillable"] = False
            df.at[i, "note"] = note
            continue
        df.at[i, "fillable"] = True
        df.at[i, "note"] = ""
        df.at[i, "entry"] = entry
        # ret_1d
        if idx + 1 < len(trades):
            o1 = open_map.get((trades[idx + 1], code))
            if o1 and o1 > 0:
                df.at[i, "ret_1d"] = o1 / entry - 1
            elif old_ret1 is not None and not (isinstance(old_ret1, float) and np.isnan(old_ret1)):
                df.at[i, "ret_1d"] = old_ret1
        # exit / ret（第 hold 个交易日开盘）
        if idx + hold < len(trades):
            o_exit = open_map.get((trades[idx + hold], code))
            if o_exit and o_exit > 0:
                df.at[i, "exit"] = o_exit
                df.at[i, "ret"] = o_exit / entry - 1
            elif old_ret is not None and not (isinstance(old_ret, float) and np.isnan(old_ret)):
                df.at[i, "ret"] = old_ret
                if old_entry is not None and not (isinstance(old_entry, float) and np.isnan(old_entry)):
                    df.at[i, "exit"] = old_entry * (1 + old_ret)
    tmp = LOG_CSV + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, LOG_CSV)

    done = df[(df["fillable"].astype(str).str.lower() == "true") & df["ret"].notna()]
    print("    backfill: 可成交 %d 行中已满持有期 %d 行（hold=%d）" % (
        int((df["fillable"].astype(str).str.lower() == "true").sum()), len(done), hold))
    last_trade = trades[-1]
    gap = (pd.Timestamp.today().normalize() - pd.Timestamp(last_trade)).days
    if gap > 5:
        stale = True
        print("    [ALERT] 行情数据源停更：最新交易日 %s 距今 %d 天 → exit 2" % (str(last_trade)[:10], gap))
    return stale


def _arm_stats(sub):
    if len(sub) == 0:
        return "n=0"
    return "n=%d 均值%+.3f%% 胜率%.1f%% 中位%+.3f%%" % (
        len(sub), sub["ret"].mean() * 100, (sub["ret"] > 0).mean() * 100, sub["ret"].median() * 100)


def report(hold):
    """生成 results/v13_paper_parallel_report.md；返回 stale 标志。"""
    if not os.path.exists(LOG_CSV):
        print("    无 log，跳过 report")
        return False
    df = pd.read_csv(LOG_CSV, encoding="utf-8-sig", dtype={"ts_code": str})
    df["fillable"] = df["fillable"].astype(str).str.lower() == "true"
    real = df[~df["ts_code"].isin([EMPTY, NOFILE])].copy()
    filled = real[real["fillable"] & real["ret"].notna()].copy()
    md = ["# V1.3 口径对齐方案B：纸面并行对比报告", "",
          "> 生成：%s | SPEC：specs/V13口径对齐方案B落地_SPEC_20260930.md §3.2" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
          "> 口径：old臂=执行日T 09:45重排名单(total≥58 Top5) vs new臂=T-1晚 final_list(redline Top5)；",
          "> 两臂统一执行日 T 开盘入场、第 %d 个交易日开盘退出；均不施加 position_filter。" % hold, "",
          "## 注记（诚实偏差）",
          "- old臂实盘实际 09:45 入场，纸面统一 T 开盘（口径可比前提）",
          "- 一字涨停无法用 open 面板判定（诚实降级，可能高估两臂成交率）",
          "- 停牌/无量行 fillable=False 已剔除统计",
          "- new臂 F6 在 daily_basic 缺失时回退面板口径（见 final_list.meta.json f6_source）",
          ""]

    md.append("## 单臂统计（5日 open→open）")
    for arm in ("old", "new"):
        sub = filled[filled["arm"] == arm]
        md.append("- **%s臂**: %s" % (arm, _arm_stats(sub)))
    md.append("")

    md.append("## 逐日配对差（new − old，按执行日均臂收益）")
    paired = []
    for d in sorted(real["exec_date"].unique()):
        fo = filled[(filled["exec_date"] == d) & (filled["arm"] == "old")]
        fn = filled[(filled["exec_date"] == d) & (filled["arm"] == "new")]
        if len(fo) and len(fn):
            paired.append({"exec_date": d, "n_old": len(fo), "n_new": len(fn),
                           "old": fo["ret"].mean(), "new": fn["ret"].mean(),
                           "diff_bp": (fn["ret"].mean() - fo["ret"].mean()) * 1e4})
    if paired:
        md.append("| 执行日 | old均值 | new均值 | new−old(bp) | n_old | n_new |")
        md.append("|---|---|---|---|---|---|")
        for p in paired:
            md.append("| %s | %+.3f%% | %+.3f%% | %+.1f | %d | %d |" % (
                p["exec_date"], p["old"] * 100, p["new"] * 100, p["diff_bp"], p["n_old"], p["n_new"]))
        mean_diff = float(np.mean([p["diff_bp"] for p in paired]))
        md.append("")
        md.append("**日均配对差 = %+.1f bp/日**（%d 个配对执行日）" % (mean_diff, len(paired)))
    else:
        mean_diff = None
        md.append("（尚无双方都满持有期的配对执行日）")
    md.append("")

    md.append("## Top%d 重合度与空仓日" % TOP_N)
    ovl_counts, ovl_days = [], 0
    for d in sorted(real["exec_date"].unique()):
        so = set(real[(real["exec_date"] == d) & (real["arm"] == "old")]["ts_code"])
        sn = set(real[(real["exec_date"] == d) & (real["arm"] == "new")]["ts_code"])
        if so or sn:
            ovl_days += 1
            ovl_counts.append(len(so & sn))
    if ovl_days:
        md.append("- 逐日重合数均值 %.2f/%d（%d 个执行日）" % (
            float(np.mean(ovl_counts)), TOP_N, ovl_days))
    for arm, tag in (("old", "old臂（09:45重排）"), ("new", "new臂（final_list）")):
        n_empty = int(((df["arm"] == arm) & (df["ts_code"] == EMPTY)).sum())
        n_nofile = int(((df["arm"] == arm) & (df["ts_code"] == NOFILE)).sum())
        md.append("- %s：红线空仓日 %d | 产物缺失日 %d" % (tag, n_empty, n_nofile))
    md.append("")

    md.append("## Phase 2 门禁判据（SPEC §5，达标后另启，本报告仅展示）")
    win_old = (filled[filled["arm"] == "old"]["ret"] > 0).mean() if len(filled[filled["arm"] == "old"]) else np.nan
    win_new = (filled[filled["arm"] == "new"]["ret"] > 0).mean() if len(filled[filled["arm"] == "new"]) else np.nan
    n_paired = len(paired)
    md.append("- 配对执行日样本：%d / %d %s" % (n_paired, GATE_MIN_DAYS,
                                               "（不足）" if n_paired < GATE_MIN_DAYS else "（已足）"))
    if mean_diff is not None:
        md.append("- new−old 日均配对差：%+.1f bp/日（判据 ≥ %.0f bp/日）%s" % (
            mean_diff, GATE_DIFF_BP, "✓" if mean_diff >= GATE_DIFF_BP else "✗"))
    if not (np.isnan(win_old) or np.isnan(win_new)):
        md.append("- 胜率：old %.1f%% vs new %.1f%%（差 %.1f pp，判据 ≤ %.0f pp）%s" % (
            win_old * 100, win_new * 100, (win_new - win_old) * 100, GATE_WIN_PP,
            "✓" if abs(win_new - win_old) * 100 <= GATE_WIN_PP else "✗"))
    md.append("")

    md.append("## 明细（最近 20 行已回填记录）")
    tail = filled.sort_values(["exec_date", "arm", "rank"]).tail(20)
    md.append("| 执行日 | 臂 | 信号日 | 代码 | rank | total | entry | exit | ret5d | ret_1d |")
    md.append("|---|---|---|---|---|---|---|---|---|---|")
    for _, r in tail.iterrows():
        md.append("| %s | %s | %s | %s | %d | %.1f | %.2f | %.2f | %+.2f%% | %+.2f%% |" % (
            r["exec_date"], r["arm"], r["signal_date"], r["ts_code"], int(r["rank"]),
            r["total"], r["entry"], r["exit"], r["ret"] * 100,
            (r["ret_1d"] * 100) if pd.notna(r["ret_1d"]) else float("nan")))
    md.append("")

    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("    报告: %s" % REPORT_MD)
    # 停更复检（report 单独跑时也守纪律）
    stale = False
    if os.path.isfile(INCR):
        inc = pd.read_parquet(INCR)
        inc_max = pd.to_datetime(inc["trade_date"]).max()
        gap = (pd.Timestamp.today().normalize() - inc_max).days
        if gap > 5:
            stale = True
    return stale


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=HOLD_DAYS)
    ap.add_argument("--no-record", action="store_true", help="只 backfill + report（不记录今日）")
    args = ap.parse_args()

    if not os.path.isdir(RESULTS):
        os.makedirs(RESULTS)

    if not args.no_record:
        if not os.path.isfile(INCR):
            print("!! FAIL-LOUD: incremental_daily.parquet 不存在（16:30 链未跑）")
            sys.exit(1)
        inc = pd.read_parquet(INCR)
        T = pd.to_datetime(inc["trade_date"]).max()
        today = pd.Timestamp.today().normalize()
        if T < today:
            print("[SKIP] 记录：增量库最新日 %s < 今日 %s（非交易日或 16:30 链未跑），仅 backfill+report"
                  % (T.date(), today.date()))
        else:
            print("[1/3] 记录执行日 %s 双臂选股..." % T.date())
            record(T)

    print("[2/3] 回填收益（entry/exit/ret/ret_1d）...")
    stale = backfill(args.hold)
    print("[3/3] 生成对比报告...")
    stale = report(args.hold) or stale
    if stale:
        sys.exit(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
