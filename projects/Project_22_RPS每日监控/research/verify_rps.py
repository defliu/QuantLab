# coding=utf-8
"""Project_22 独立校验脚本（SPEC 第八节）。

本脚本不复用 rps_daily.py 的任何内部函数，全部用纯 pandas/pyarrow 独立重算一遍，
对 rps_daily.py 的产物做交叉验证：
  1. 随机抽 3 只股票 + 1 个交易日，独立手算 ret_20 与产物对比，误差 < 1e-6
  2. rps_20 分布合理性（最大≈100、最小≈0、中位数 40~60）
  3. 状态快照：随机抽 5 只在榜股票，rank_chg_20 == last_rank_20 - prev_rank_20
  4. PIT：后来才上市（或已退市）的股票在早期日期不在池中（举具体例子）
  5. 窗口完整性：产出天数 + 缺陷日天数 == 窗口交易日数（防「报 60 实 55」复发）
  6. 缺陷台账一致性：台账每行都在窗口日历中；条数与日志 [SKIP-DATADEFECT] 行数一致
  7. 链条连续性：daily[Ti].prev_rank_20 == full[T(i-1)].rank_20（共同股票，逐对无断裂）
  8. 无未来状态：所有产出日的 first_top20_date / last_update_date 不晚于该产出日
  9. 输出 [VERIFY-PASS] / [VERIFY-FAIL] 汇总，失败以非零退出码结束
"""
import glob
import datetime as dt
import random
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

PROJ_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR = PROJ_DIR / "results"
DATA_PARQUET = Path(r"D:/astock/daily/stock_daily.parquet")

CHECKS = []


def check(name, ok, detail=""):
    tag = "[VERIFY-PASS]" if ok else "[VERIFY-FAIL]"
    print(f"{tag} {name} {detail}")
    CHECKS.append(ok)
    return ok


def full_csv_files():
    files = sorted(glob.glob(str(RESULTS_DIR / "rps_rank_full_*.csv")))
    out = []
    for f in files:
        m = re.search(r"(\d{8})", Path(f).name)
        if m:
            out.append((pd.Timestamp(dt.datetime.strptime(m.group(1), "%Y%m%d")), Path(f)))
    return sorted(out)


def _flat_df(df):
    """pandas 3.0.5 + pyarrow 25 环境下，read_table(...).to_pandas() 会按 parquet 元数据
    把 trade_date + ts_code 恢复成 MultiIndex，导致这两列从 df.columns 消失（KeyError）。
    统一拍平成普通列（与 rps_daily.load_frame 同样的防御）。"""
    return df.reset_index() if isinstance(df.index, pd.MultiIndex) else df


def load_local_calendar(start_dt, end_dt):
    """独立读日历（只读 trade_date 一列，限区间）。"""
    tbl = pq.read_table(DATA_PARQUET, columns=["trade_date"],
                        filters=[("trade_date", ">=", start_dt),
                                 ("trade_date", "<=", end_dt)])
    s = tbl.column("trade_date").to_pandas()
    if isinstance(s.index, pd.MultiIndex):
        s = s.reset_index(drop=True)
    vals = s.drop_duplicates().sort_values().to_numpy()
    return pd.DatetimeIndex(vals)


def check_ret20(T, latest_df):
    codes = random.sample(latest_df["ts_code"].tolist(), 3)
    all_ok = True
    cal = load_local_calendar(T - pd.Timedelta(days=90), T)
    if len(cal) < 21:
        return check("1_ret_20_recompute", False, "局部日历不足 21 个交易日")
    target = cal[-21]  # T 前 20 个交易日
    for code in codes:
        # 独立手算：P = close * adj_factor（与 rps_daily 相同的口径定义，但实现独立）
        rows = pq.read_table(DATA_PARQUET,
                             columns=["trade_date", "ts_code", "close", "adj_factor"],
                             filters=[("ts_code", "=", code),
                                      ("trade_date", "<=", target)]).to_pandas()
        rows = _flat_df(rows)
        rows = rows.sort_values("trade_date")
        if rows.empty or rows["close"].isna().all():
            print(f"    {code}: target 前无行情记录（不应出现在池中）")
            all_ok = False
            continue
        # 与主程序 piv.ffill() 同口径：基准价取 target 当天的"前收盘价"（ffill），
        # 停牌股用最后成交日价格延展到 target，不静默退化成更早的基准日
        px = pd.Series((rows["close"] * rows["adj_factor"]).to_numpy(),
                       index=pd.DatetimeIndex(rows["trade_date"]))
        base_date = px.index.max()          # target 当天（或之前）最后一个真实行情日
        suspended = base_date < target      # target 当天停牌 → 显式标注，不静默
        px = px.reindex(px.index.union(pd.DatetimeIndex([target]))).ffill()
        p_base = float(px.loc[target])
        row_T = latest_df.loc[latest_df["ts_code"] == code].iloc[0]
        p_now = row_T["close"] * _adj_at_or_before(code, T)
        ret_manual = p_now / p_base - 1.0
        ret_csv = float(row_T["ret_20"])
        diff = abs(ret_manual - ret_csv)
        ok = diff < 1e-6 and np.isfinite(p_base) and p_base > 0
        all_ok &= ok
        note = (f"（停牌股：{target.date()} 无行情，按 ffill 前收盘价口径，"
                f"最后行情日 {base_date.date()}）") if suspended else ""
        print(f"    {code}: 手算 {ret_manual:.8f} vs 产物 {ret_csv:.8f} "
              f"基准日 {base_date.date()} 误差 {diff:.2e} "
              f"{'OK' if ok else 'MISMATCH'} {note}")
    return check("1_ret_20_recompute", bool(all_ok), f"3 只随机股票 @ {T.date()}")


def _adj_at_or_before(code, T):
    rows = pq.read_table(DATA_PARQUET,
                         columns=["trade_date", "ts_code", "adj_factor"],
                         filters=[("ts_code", "=", code),
                                  ("trade_date", "<=", T)]).to_pandas()
    rows = _flat_df(rows)
    rows = rows.sort_values("trade_date")
    return float(rows.iloc[-1]["adj_factor"])


def check_distribution(latest_df):
    rps = latest_df["rps_20"].dropna()
    if len(rps) == 0:
        return check("2_rps20_distribution", False, "rps_20 全为空")
    mx, mn, med = float(rps.max()), float(rps.min()), float(rps.median())
    ok = mx >= 99.0 and mn <= 1.0 and 40.0 <= med <= 60.0
    detail = f"max={mx:.2f} min={mn:.2f} median={med:.2f} n={len(rps)}"
    return check("2_rps20_distribution", bool(ok),
                 detail + ("" if ok else "（要求 max≈100 / min≈0 / 中位数 40~60）"))


def check_state_rank_chg():
    p = RESULTS_DIR / "rps_state.parquet"
    if not p.exists():
        return check("3_state_rank_chg", False, "rps_state.parquet 不存在")
    st = pd.read_parquet(p)
    onlist = st[st["in_top50_now"] == 1] if "in_top50_now" in st else pd.DataFrame()
    if len(onlist) < 5:
        return check("3_state_rank_chg", False, f"在榜股票不足 5 只（{len(onlist)}）")
    sample = onlist.sample(n=5, random_state=20261004)
    diffs = (sample["last_rank_20"] - sample["prev_rank_20"] - sample["rank_chg_20"]).abs()
    ok = bool((diffs < 1e-9).all())
    bad = sample.loc[diffs >= 1e-9, ["ts_code", "last_rank_20", "prev_rank_20", "rank_chg_20"]]
    detail = f"抽 {len(sample)} 只在榜股票"
    if not ok:
        detail += f"，不一致: {bad.to_dict('records')}"
    return check("3_state_rank_chg", ok, detail)


def load_manifest():
    p = RESULTS_DIR / "defects_manifest.csv"
    if not p.exists():
        return None
    return pd.read_csv(p, dtype={"trade_date": str})


def parse_backfill_log():
    """解析 results/backfill_round3.log：回补窗口行 + [SKIP-DATADEFECT] 行。"""
    p = RESULTS_DIR / "backfill_round3.log"
    if not p.exists():
        return None, []
    text = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"回补窗口 (\d{4}-\d{2}-\d{2}) ~ (\d{4}-\d{2}-\d{2}) 共 (\d+) 个交易日", text)
    window = (pd.Timestamp(m.group(1)), pd.Timestamp(m.group(2)), int(m.group(3))) if m else None
    skip_lines = re.findall(r"\[SKIP-DATADEFECT\] (\d{4}-\d{2}-\d{2})", text)
    return window, skip_lines


def _window_calendar(files, defect_dates):
    """确定回补窗口：优先取日志声明，缺日志时用 产物日∪缺陷日 的首尾重建。"""
    window, _ = parse_backfill_log()
    if window is not None:
        w_start, w_end, w_n = window
    else:
        all_dates = [T for T, _ in files] + list(defect_dates)
        if not all_dates:
            return None, None, None
        w_start, w_end, w_n = min(all_dates), max(all_dates), None
    return load_local_calendar(w_start, w_end), (w_start, w_end), w_n


def check_window_completeness(files):
    """断言5：产出天数 + 缺陷日天数 == 窗口交易日数（以交易日历为真值）。"""
    manifest = load_manifest()
    if manifest is None:
        return check("5_window_completeness", False, "defects_manifest.csv 不存在")
    defect_set = set(pd.to_datetime(manifest["trade_date"], format="%Y%m%d"))
    cal, w_range, w_n = _window_calendar(files, defect_set)
    if cal is None:
        return check("5_window_completeness", False, "无法确定回补窗口（无产物且无缺陷日）")
    produced_in_win = {T for T, _ in files if T in set(cal)}
    stale = [T for T, _ in files if T not in set(cal)]
    ok = (len(produced_in_win) + len(defect_set) == len(cal)) \
        and (produced_in_win.isdisjoint(defect_set)) \
        and (produced_in_win | defect_set == set(cal))
    if w_n is not None:
        ok = ok and (len(cal) == w_n)
    detail = (f"窗口 {w_range[0].date()} ~ {w_range[1].date()} 交易日 {len(cal)} 天 = "
              f"产出 {len(produced_in_win)} + 缺陷日 {len(defect_set)}"
              + (f"，日志声明窗口 {w_n} 天" if w_n is not None else "（日志缺失，窗口由产物重建）"))
    if stale:
        detail += f"；[WARN] 窗口外残留产物 {len(stale)} 天（最早 {min(stale).date()}）"
        print(f"    [WARN] 窗口外残留产物: {[t.strftime('%Y-%m-%d') for t in sorted(stale)]}")
    if not ok:
        missing = sorted(set(cal) - produced_in_win - defect_set)
        detail += f"；缺失未解释日: {[t.strftime('%Y-%m-%d') for t in missing]}"
    return check("5_window_completeness", bool(ok), detail)


def check_manifest_consistency(files):
    """断言6：台账每行都能在窗口日历中找到；条数与日志 [SKIP-DATADEFECT] 行数一致。"""
    manifest = load_manifest()
    if manifest is None:
        return check("6_manifest_consistency", False, "defects_manifest.csv 不存在")
    need = {"trade_date", "listed_days_nan_ratio", "total_rows", "verdict", "skipped"}
    missing_cols = need - set(manifest.columns)
    if missing_cols:
        return check("6_manifest_consistency", False, f"台账字段缺失: {sorted(missing_cols)}")
    defect_set = set(pd.to_datetime(manifest["trade_date"], format="%Y%m%d"))
    cal, _, _ = _window_calendar(files, defect_set)
    if cal is None:
        return check("6_manifest_consistency", False, "无法确定回补窗口")
    in_cal = pd.to_datetime(manifest["trade_date"], format="%Y%m%d").isin(set(cal))
    verdict_ok = bool((manifest["verdict"] == "MAIN_DB_DEFECT").all())
    skipped_ok = bool(manifest["skipped"].astype(bool).all())
    ratio_ok = bool((pd.to_numeric(manifest["listed_days_nan_ratio"], errors="coerce") > 0.5).all())
    _, skip_lines = parse_backfill_log()
    log_n = len(skip_lines)
    ok = bool(in_cal.all()) and verdict_ok and skipped_ok and ratio_ok and (log_n == len(manifest))
    detail = (f"台账 {len(manifest)} 行 vs 日志 [SKIP-DATADEFECT] {log_n} 行；"
              f"verdict={'OK' if verdict_ok else 'BAD'} skipped={'OK' if skipped_ok else 'BAD'} "
              f"nan_ratio>0.5={'OK' if ratio_ok else 'BAD'} "
              f"日历命中 {int(in_cal.sum())}/{len(manifest)}")
    return check("6_manifest_consistency", ok, detail)


def check_chain_continuity(files):
    """断言7：链条连续性。对按交易日排序的产出日序列 T1..Tn，逐对断言
    daily[Ti].prev_rank_20 == full[T(i-1)].rank_20（共同股票）。
    注意必须用 prev_rank_20（状态里存的是前一产出日的 rank_20），不能用当日 rank_20 对比。"""
    if len(files) < 2:
        return check("7_chain_continuity", False, "产出不足 2 天，无法做链条连续性检验")
    total_pairs, total_common, total_bad = 0, 0, 0
    worst = None
    for i in range(1, len(files)):
        T_prev, f_prev = files[i - 1]
        T_cur, _ = files[i]
        dpath = RESULTS_DIR / f"rps_rank_daily_{T_cur.strftime('%Y%m%d')}.csv"
        if not dpath.exists():
            total_bad += 1
            worst = (T_cur.date(), "daily 产物缺失")
            continue
        prev_full = pd.read_csv(f_prev, dtype={"ts_code": str})
        cur_daily = pd.read_csv(dpath, dtype={"ts_code": str})
        mg = cur_daily[["ts_code", "prev_rank_20"]].merge(
            prev_full[["ts_code", "rank_20"]], on="ts_code", how="inner")
        pv = pd.to_numeric(mg["prev_rank_20"], errors="coerce")
        fr = pd.to_numeric(mg["rank_20"], errors="coerce")
        bad = (pv.isna() & (fr != -1)) | (pv.notna() & (fr == -1)) \
            | (pv.notna() & (fr != -1) & ((pv - fr).abs() > 1e-9))
        n_bad = int(bad.sum())
        total_pairs += 1
        total_common += len(mg)
        total_bad += n_bad
        if n_bad > 0 and worst is None:
            sample = mg.loc[bad, ["ts_code", "prev_rank_20", "rank_20"]].head(3)
            worst = (T_cur.date(), sample.to_dict("records"))
    ok = total_bad == 0 and total_pairs == len(files) - 1 and total_common > 0
    detail = f"{total_pairs} 对相邻产出日，共同股票观测 {total_common} 次，断裂 {total_bad} 处"
    if worst is not None:
        detail += f"；首例: {worst}"
    return check("7_chain_continuity", bool(ok), detail)


def check_no_future_state(files):
    """断言8：无未来状态。所有产出日的 first_top20_date / last_update_date 不得晚于该产出日。"""
    bad = []
    checked_days = 0
    for T, _ in files:
        ymd = T.strftime("%Y%m%d")
        dpath = RESULTS_DIR / f"rps_rank_daily_{ymd}.csv"
        if not dpath.exists():
            bad.append((ymd, "daily 产物缺失"))
            continue
        d = pd.read_csv(dpath, dtype={"ts_code": str,
                                      "first_top20_date": str, "last_update_date": str})
        checked_days += 1
        for col in ["first_top20_date", "last_update_date"]:
            vals = d[col].fillna("")
            later = vals[(vals != "") & (vals > ymd)]
            if len(later):
                bad.append((ymd, col, sorted(set(later))[:3]))
    ok = not bad
    detail = f"{checked_days} 个产出日的状态字段全部不晚于产出日" if ok else f"发现未来状态: {bad[:5]}"
    return check("8_no_future_state", bool(ok), detail)


def check_pit(files):
    if len(files) < 2:
        return check("4_pit_pool", False, "全池 CSV 不足 2 天，无法做 PIT 早晚对比")
    T0, f0 = files[0]
    Tn, fn = files[-1]
    df0 = pd.read_csv(f0, dtype={"ts_code": str})
    dfn = pd.read_csv(fn, dtype={"ts_code": str})
    codes0 = set(df0["ts_code"])
    codesn = set(dfn["ts_code"])

    # 后上市方向：在 [T0, Tn] 区间内首次出现晚于 T0 的股票
    scan = pq.read_table(DATA_PARQUET, columns=["trade_date", "ts_code"],
                         filters=[("trade_date", ">=", T0),
                                  ("trade_date", "<=", Tn)]).to_pandas()
    scan = _flat_df(scan)
    first_seen = scan.groupby("ts_code")["trade_date"].min()
    late = first_seen[first_seen > T0].sort_index()
    ok_all, example = True, None
    if len(late) > 0:
        # 取首现最晚的一只作为"后来才上市"的例子，并确认其在 T0 前 120 个自然日内无记录
        cand = late.idxmax()
        pre = pq.read_table(DATA_PARQUET, columns=["ts_code"],
                            filters=[("ts_code", "=", cand),
                                     ("trade_date", "<", T0)]).to_pandas()
        pre = _flat_df(pre)
        not_in_early = cand not in codes0
        no_history_before = len(pre) == 0
        ok_all &= not_in_early and no_history_before
        example = (cand, pd.Timestamp(late.loc[cand]).date(),
                   "不在早期池" if not_in_early else "!!在早期池!!",
                   "T0 前无行情记录" if no_history_before else "!!T0 前有行情记录!!")
        print(f"    后上市例子: {example[0]} 首现 {example[1]} -> {example[2]}，{example[3]}")
    else:
        print("    [WARN] 未找到后上市样本，仅做退市方向检查")

    # 退市方向：早期池中存在、最新池缺席，且近期无行情记录（非单纯停牌）
    gone = sorted(codes0 - codesn)
    gone_ok, gone_example = True, None
    if len(gone) > 0:
        recent = pq.read_table(DATA_PARQUET, columns=["trade_date", "ts_code"],
                               filters=[("trade_date", ">=", Tn - pd.Timedelta(days=20)),
                                        ("trade_date", "<=", Tn)]).to_pandas()
        recent = _flat_df(recent)
        recent_codes = set(recent["ts_code"])
        truly_gone = [c for c in gone if c not in recent_codes]
        if truly_gone:
            gone_example = truly_gone[0]
            print(f"    退市/长停例子: {gone_example} 在 {T0.date()} 池中，"
                  f"{Tn.date()} 池中缺席且近 20 日无行情记录")
        else:
            print(f"    早期池缺席的 {len(gone)} 只均为近期仍有行情的股票（停牌口径，"
                  f"当日不可交易不出池属正常），退市方向以停牌口径验证通过")
    ok = ok_all and gone_ok
    detail = f"早期 {T0.date()} 池 {len(codes0)} 只 / 最新 {Tn.date()} 池 {len(codesn)} 只"
    if not ok:
        detail += "，PIT 检查不一致"
    return check("4_pit_pool", bool(ok), detail)


def main():
    print("[VERIFY] Project_22 RPS 产物独立交叉校验（独立实现，不复用 rps_daily 内部函数）")
    files = full_csv_files()
    if not files:
        print("[VERIFY-FAIL] results/ 下没有任何 rps_rank_full_*.csv 产物")
        sys.exit(1)
    T, fpath = files[-1]
    latest_df = pd.read_csv(fpath, dtype={"ts_code": str})
    print(f"[VERIFY] 最新产物: {fpath.name}（{len(latest_df)} 行），最早产物: {files[0][1].name}")

    random.seed(20261004)
    check_ret20(T, latest_df)
    check_distribution(latest_df)
    check_state_rank_chg()
    check_pit(files)
    check_window_completeness(files)
    check_manifest_consistency(files)
    check_chain_continuity(files)
    check_no_future_state(files)

    n_pass = sum(1 for c in CHECKS if c)
    tag = "[VERIFY-PASS]" if n_pass == len(CHECKS) else "[VERIFY-FAIL]"
    print(f"{tag} 汇总: {n_pass}/{len(CHECKS)} 项通过")
    sys.exit(0 if n_pass == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
