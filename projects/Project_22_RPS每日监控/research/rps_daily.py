# coding=utf-8
"""Project_22 RPS 强势股每日监控 —— 数据层（监控与研究辅助工具）。

定位：只做排序与观察跟踪，不产生任何下单/交易语义的输出（SPEC 第一节风险边界）。

用法:
    python research/rps_daily.py --date 20260930      # 单日
    python research/rps_daily.py --backfill 60        # 回补最近 60 个交易日（建状态用，日期升序逐日推进）
    python research/rps_daily.py --backfill 60 --workers 4

复权口径（SPEC 4.1，错则全盘皆错）:
    adj_factor 为后复权因子。本项目采用 P = close * adj_factor 作为
    "后复权价的单调等价变换"序列（价差约等序列），ret_w = P(T) / P(T-w) - 1。
    选择理由:
      1) 分子分母同源于同一条 P 序列，天然满足"同一基准的复权价序列"要求，
         除权日不会产生假跳空；
      2) 相比"前复权价 = close * (adj_factor / 最新 adj_factor)"，不引入对
         "最新因子"这一口径外数据的依赖，PIT 更干净；
      3) 收益率是比值，对公共常数因子不变，两种口径数学等价。

RPS 百分位（SPEC 4.3）:
    当日 PIT 可交易池内，rps_w = pool 内 ret_w 的百分位 * 100（最强=100），
    用 pandas rank(pct=True)（并列取平均）实现，NaN 不参与排名。

历史状态（SPEC 4.4）:
    results/rps_state.parquet 键为 ts_code，每跑一天原位更新；
    "入榜以来收益/最大涨幅"以首次入榜日（top20）的后复权约等序列价为锚。
    设计约定：
      - 不在当日池中的历史股票：in_top50_now 置 0、连续在榜天数清零（停牌/出池
        即中断连续口径），其余字段冻结为"最后一次在池时"的值；
      - 同日重跑（last_update_date == 当日）幂等：prev_rank_20/consec 不二次移动。
"""
import argparse
import datetime as dt
import importlib.util
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
import pyarrow.parquet as pq

PROJ_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR = PROJ_DIR / "results"
CONFIG_PATH = PROJ_DIR / "config" / "rps_monitor.yaml"
DATA_PARQUET = Path(r"D:/astock/daily/stock_daily.parquet")
ASTOCK_KIT = Path(__file__).resolve().parents[3] / "data" / "astock_kit.py"

# 只读需要的列（SPEC 第七节：严禁全量加载，列裁剪 + 行过滤）
READ_COLS = ["trade_date", "ts_code", "close", "adj_factor", "amount",
             "circ_mv", "is_st", "listed_days", "pct_chg"]
MAX_WINDOW = 120        # 三个窗口的最大值
LOOKBACK_BUFFER = 10    # 早于最早需求日的额外缓冲交易日，保证 ffill 基准有真实数据

# 主库缺陷日判定阈值（2026-10-04 业主拍板：坏数据日显式跳过 + 落缺陷台账留痕）。
# 也可在 config/rps_monitor.yaml 的 defect.nan_ratio_threshold 覆盖。
DEFECT_NAN_RATIO_THRESHOLD = 0.5

STATE_COLS = ["first_top20_date", "first_top50_date", "last_rank_20",
              "prev_rank_20", "rank_chg_20", "ret_since_top20",
              "max_ret_since_top20", "top20_anchor_price", "in_top50_now",
              "consec_days_in_top50", "last_update_date"]

_ZT_LOCK = threading.Lock()  # astock_kit.em_get 的模块级限流器非线程安全，用锁串行化涨停池请求

_DEFAULT_CONFIG = {
    "windows": [20, 60, 120],
    "pool": {
        "min_listed_days": 252,
        "min_amount": 50000,        # 千元单位 = 5000 万元
        "circ_mv_min_wan": 200000,
        "circ_mv_max_wan": 50000000,
        "exclude_st": True,
    },
    "top_n_report": 50,
    "gate": {
        "min_cover_ratio": 0.80,
        "max_missing_ratio": 0.02,
        "min_unique_ratio": 0.30,
    },
    "enrich": {
        "enable_zt_pool": True,
        "enable_ths_reason": True,
    },
    "defect": {
        "nan_ratio_threshold": DEFECT_NAN_RATIO_THRESHOLD,  # listed_days NaN 比例超此值判为主库缺陷日
        "manifest": "defects_manifest.csv",
        "action": "skip_with_manifest",  # 已拍板：跳过+留痕；禁止改为 continue/silent
    },
}


def _merge_cfg(base, user):
    out = dict(base)
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_cfg(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    cfg = dict(_DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = _merge_cfg(cfg, yaml.safe_load(f) or {})
    except FileNotFoundError:
        print("[WARN] config/rps_monitor.yaml 不存在，使用内置默认配置")
    for w in cfg["windows"]:
        if w > MAX_WINDOW:
            raise ValueError(f"window {w} 超过支持上限 {MAX_WINDOW}")
    return cfg


def load_calendar():
    """全量交易日历（只读 trade_date 一列，非全量加载）。"""
    tbl = pq.read_table(DATA_PARQUET, columns=["trade_date"])
    cal = tbl.column("trade_date").to_pandas().drop_duplicates().sort_values().to_numpy()
    return pd.DatetimeIndex(cal)


def load_frame(start_dt, end_dt):
    """按日期区间 + 列裁剪读取行情（pyarrow filters 下推，只读 9 列）。"""
    tbl = pq.read_table(DATA_PARQUET, columns=READ_COLS,
                        filters=[("trade_date", ">=", start_dt),
                                 ("trade_date", "<=", end_dt)])
    df = tbl.to_pandas()
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
    assert "ts_code" in df.columns and "trade_date" in df.columns
    df = df.drop_duplicates(subset=["trade_date", "ts_code"])
    df = df.sort_values(["trade_date", "ts_code"], kind="mergesort").reset_index(drop=True)
    return df


def pool_mask(df, pcfg):
    m = (df["amount"] >= pcfg["min_amount"]) \
        & (df["circ_mv"] >= pcfg["circ_mv_min_wan"]) \
        & (df["circ_mv"] <= pcfg["circ_mv_max_wan"]) \
        & (df["listed_days"] >= pcfg["min_listed_days"]) \
        & df["is_st"].notna() & df["circ_mv"].notna() & df["listed_days"].notna()
    if pcfg["exclude_st"]:
        m &= df["is_st"] != 1
    return m


def detect_defect_days(df, days, threshold):
    """识别窗口内的主库缺陷日（2026-10-04 业主拍板口径）。

    某交易日 listed_days 列 NaN 比例 > threshold 时，判定为 D:/astock 主库缺陷日
    （上游数据缺陷，当日 PIT 池过滤 listed_days>=min_listed_days 全灭，无法产出合法产物）。
    返回 (缺陷日列表, 每交易日 NaN 比例, 每交易日总行数)，后两者供缺陷台账留痕。
    """
    nan_ratio_map = df["listed_days"].isna().groupby(df["trade_date"]).mean().astype(float)
    total_rows_map = df.groupby("trade_date").size()
    defect_days = [d for d in days
                   if d in nan_ratio_map.index and float(nan_ratio_map.loc[d]) > threshold]
    return defect_days, nan_ratio_map, total_rows_map


def write_defects_manifest(defect_days, nan_ratio_map, total_rows_map, path):
    """缺陷台账（覆盖写）：仅记缺陷日，正常日不入账。verdict 固定 MAIN_DB_DEFECT。"""
    rows = []
    for d in defect_days:
        rows.append({
            "trade_date": d.strftime("%Y%m%d"),
            "listed_days_nan_ratio": round(float(nan_ratio_map.loc[d]), 6),
            "total_rows": int(total_rows_map.loc[d]),
            "verdict": "MAIN_DB_DEFECT",
            "skipped": True,
        })
    out = pd.DataFrame(rows, columns=["trade_date", "listed_days_nan_ratio",
                                      "total_rows", "verdict", "skipped"])
    out.to_csv(path, index=False, encoding="utf-8-sig")
    return len(out)


def build_return_pivots(df, windows):
    """P = close * adj_factor（后复权约等序列），pivot 后按时间 ffill（停牌日用最后成交价），ret_w = P/P.shift(w)-1。"""
    tmp = pd.DataFrame({
        "trade_date": df["trade_date"],
        "ts_code": df["ts_code"],
        "adj_close": df["close"] * df["adj_factor"],
    })
    piv = tmp.pivot(index="trade_date", columns="ts_code", values="adj_close").sort_index()
    piv = piv.ffill()
    rets = {w: piv / piv.shift(w) - 1.0 for w in windows}
    return piv, rets


def compute_day(T, pool_df, rets, windows):
    """当日 PIT 池内三窗口 ret/rps 与 rank_20（1=最强；rps_20 为 NaN 者不参与排名）。"""
    out = pool_df[["ts_code", "close", "pct_chg", "circ_mv", "amount"]].copy()
    for w in windows:
        ret_T = rets[w].loc[T]
        out[f"ret_{w}"] = out["ts_code"].map(ret_T)
        out[f"rps_{w}"] = out[f"ret_{w}"].rank(pct=True) * 100.0
    out = out.sort_values("rps_20", ascending=False, na_position="last").reset_index(drop=True)
    out["rank_20"] = np.where(out["rps_20"].notna(), np.arange(1, len(out) + 1), np.nan)
    return out


def _load_astock_kit():
    spec = importlib.util.spec_from_file_location("p22_astock_kit", ASTOCK_KIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fetch_enrich(days, cfg, workers):
    """预取涨停池（YYYYMMDD）与同花顺题材归因（YYYY-MM-DD，注意两者 date 格式不同）。
    astock_kit 网络/接口错误统一返回 []，无法与真 0 条区分，一律按降级处理，不阻断。"""
    results = {}
    use_zt = bool(cfg["enrich"]["enable_zt_pool"])
    use_ths = bool(cfg["enrich"]["enable_ths_reason"])
    if not (use_zt or use_ths) or len(days) == 0:
        return results
    try:
        kit = _load_astock_kit()
    except Exception as e:
        print(f"[ENRICH-DEGRADE] 加载 astock_kit 失败: {e}")
        return results

    def one(T):
        zt, ths = [], []
        if use_zt:
            try:
                with _ZT_LOCK:
                    zt = kit.em_zt_pool(T.strftime("%Y%m%d"))
            except Exception:
                zt = []
        if use_ths:
            try:
                ths = kit.ths_hot_reason(T.strftime("%Y-%m-%d"))
            except Exception:
                ths = []
        return T, zt, ths

    workers = max(1, int(workers))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for T, zt, ths in ex.map(one, list(days)):
            results[T] = (zt, ths)
    return results


def apply_enrich(out, zt, ths):
    """按 6 位代码对齐（ts_code 前缀）。字段仅作观察标注。"""
    zt_by = {str(z.get("code", "")): z for z in (zt or [])}
    ths_by = {str(t.get("code", "")): t for t in (ths or [])}
    code6 = out["ts_code"].str[:6]

    def zt_field(c, key):
        z = zt_by.get(c)
        return "" if z is None else ("" if z.get(key) is None else str(z.get(key)))

    def ths_field(c, key):
        t = ths_by.get(c)
        return "" if t is None else ("" if t.get(key) is None else str(t.get(key)))

    out["in_zt_pool"] = code6.isin(zt_by).astype(int)
    out["zt_stat"] = code6.map(lambda c: zt_field(c, "zt_stat"))
    out["zt_industry"] = code6.map(lambda c: zt_field(c, "industry"))
    out["ths_reason"] = code6.map(lambda c: ths_field(c, "reason"))
    return out


def empty_state():
    data = {
        "first_top20_date": pd.Series(dtype=object),
        "first_top50_date": pd.Series(dtype=object),
        "last_rank_20": pd.Series(dtype=float),
        "prev_rank_20": pd.Series(dtype=float),
        "rank_chg_20": pd.Series(dtype=float),
        "ret_since_top20": pd.Series(dtype=float),
        "max_ret_since_top20": pd.Series(dtype=float),
        "top20_anchor_price": pd.Series(dtype=float),
        "in_top50_now": pd.Series(dtype=float),
        "consec_days_in_top50": pd.Series(dtype=float),
        "last_update_date": pd.Series(dtype=object),
    }
    return pd.DataFrame(data)


def load_state():
    p = RESULTS_DIR / "rps_state.parquet"
    if not p.exists():
        return empty_state()
    st = pd.read_parquet(p).set_index("ts_code")
    for c in ["last_rank_20", "prev_rank_20", "rank_chg_20", "ret_since_top20",
              "max_ret_since_top20", "top20_anchor_price", "in_top50_now",
              "consec_days_in_top50"]:
        st[c] = pd.to_numeric(st.get(c), errors="coerce")
    for c in ["first_top20_date", "first_top50_date", "last_update_date"]:
        st[c] = st.get(c, pd.Series(dtype=object)).astype(object)
        st[c] = st[c].where(st[c].notna(), "")
    return st[STATE_COLS]


def save_state(state):
    state.reset_index().to_parquet(RESULTS_DIR / "rps_state.parquet",
                                   engine="pyarrow", index=False)


def update_state(state, out, adj_close_T, T, prev_td):
    """逐日更新滚动状态快照（SPEC 4.4），返回新 state。adj_close_T: 当日后复权约等序列价（按 ts_code 索引）。"""
    T_str = T.strftime("%Y%m%d")
    prev_str = prev_td.strftime("%Y%m%d") if prev_td is not None else ""
    codes = list(out["ts_code"])
    today = out.set_index("ts_code")
    old = state.reindex(codes)

    rank20 = today["rank_20"].astype(float)
    in20 = (rank20 >= 1) & (rank20 <= 20)
    in50 = (rank20 >= 1) & (rank20 <= 50)

    old_upd = old["last_update_date"].fillna("").values
    rerun = old_upd == T_str

    prev_rank = np.where(rerun, old["prev_rank_20"].values, old["last_rank_20"].values)
    last_rank = rank20.values
    rank_chg = last_rank - prev_rank

    first20_old = old["first_top20_date"].fillna("").values
    first20 = np.where(in20 & (first20_old == ""), T_str, first20_old)
    first50_old = old["first_top50_date"].fillna("").values
    first50 = np.where(in50 & (first50_old == ""), T_str, first50_old)

    adj_now = adj_close_T.reindex(codes).values.astype(float)
    anchor_old = old["top20_anchor_price"].values.astype(float)
    new_entry = in20 & (first20_old == "")
    anchor = np.where(new_entry, adj_now, anchor_old)
    with np.errstate(invalid="ignore", divide="ignore"):
        ret_since = np.where(np.isfinite(anchor) & (anchor > 0), adj_now / anchor - 1.0, np.nan)
    max_ret = np.fmax(old["max_ret_since_top20"].values.astype(float), ret_since)

    consec_old = np.nan_to_num(old["consec_days_in_top50"].values.astype(float), nan=0.0)
    consec = np.where(in50,
                      np.where(rerun, consec_old,
                               np.where(old_upd == prev_str, consec_old + 1.0, 1.0)),
                      0.0)

    upd = pd.DataFrame({
        "first_top20_date": first20,
        "first_top50_date": first50,
        "last_rank_20": last_rank,
        "prev_rank_20": prev_rank,
        "rank_chg_20": rank_chg,
        "ret_since_top20": ret_since,
        "max_ret_since_top20": max_ret,
        "top20_anchor_price": anchor,
        "in_top50_now": in50.astype(float),
        "consec_days_in_top50": consec,
        "last_update_date": T_str,
    }, index=pd.Index(codes, name="ts_code"))

    notpool = ~state.index.isin(codes)
    frozen = state.loc[notpool].copy()
    frozen["in_top50_now"] = 0.0
    frozen["consec_days_in_top50"] = 0.0
    new_state = pd.concat([frozen, upd])[STATE_COLS]
    new_state.index.name = "ts_code"
    return new_state


def quality_gate(T, out, cfg, expected_pool, enrich_counts):
    """质量门控（SPEC 4.5）：任一项不过打印 [GATE-FAIL] 并 sys.exit(1)，不静默出正常日报。"""
    fails, warns = [], []
    g = cfg["gate"]
    n = len(out)
    rps20 = out["rps_20"]

    # 1) 预期交易日检查在 main 里前置完成（--date 不在日历 → FAIL；与数据最大日期不一致 → WARN）
    # 2) 当日池覆盖 / 预期池大小
    if expected_pool and expected_pool > 0:
        ratio = n / float(expected_pool)
        if ratio < g["min_cover_ratio"]:
            fails.append(f"池覆盖比 {ratio:.3f} < min_cover_ratio {g['min_cover_ratio']} "
                         f"(当日 {n} / 预期 {expected_pool})")
    else:
        warns.append("无足够历史日用于估计预期池大小，跳过覆盖比检查")
    # 3) rps_20 缺失
    miss = float(rps20.isna().mean()) if n else 1.0
    if miss > g["max_missing_ratio"]:
        fails.append(f"rps_20 缺失率 {miss:.4f} > max_missing_ratio {g['max_missing_ratio']}")
    # 4) 排名唯一性
    valid = int(rps20.notna().sum())
    uniq = float(rps20.nunique()) / valid if valid > 0 else 0.0
    if uniq < g["min_unique_ratio"]:
        fails.append(f"rps_20 唯一比例 {uniq:.4f} < min_unique_ratio {g['min_unique_ratio']}")
    # 5) 涨停池/题材返回条数（astock_kit 网络失败返回 []，无法与真 0 条区分 → 降级 WARN 不阻断）
    zt_n, ths_n = enrich_counts
    if cfg["enrich"]["enable_zt_pool"] and zt_n == 0:
        warns.append("涨停池接口返回 0 条（网络失败降级或当日无涨停），不阻断")
    if cfg["enrich"]["enable_ths_reason"] and ths_n == 0:
        warns.append("同花顺题材接口返回 0 条（网络失败降级或当日无数据），不阻断")
    if n == 0:
        fails.append("当日池为空")

    for w in warns:
        print(f"[GATE-WARN] {T.strftime('%Y-%m-%d')} {w}")
    if fails:
        for f_ in fails:
            print(f"[GATE-FAIL] {T.strftime('%Y-%m-%d')} {f_}")
        sys.exit(1)


def write_outputs(T, full, state, top_n):
    ymd = T.strftime("%Y%m%d")
    full_out = full.copy()
    full_out["rank_20"] = full_out["rank_20"].fillna(-1).astype(int)
    for c in ["rps_20", "rps_60", "rps_120"]:
        if c in full_out:
            full_out[c] = full_out[c].round(4)
    full_cols = ["rank_20", "ts_code", "close", "pct_chg", "circ_mv", "amount",
                 "ret_20", "rps_20", "ret_60", "rps_60", "ret_120", "rps_120",
                 "in_zt_pool", "zt_stat", "zt_industry", "ths_reason"]
    full_out = full_out.reset_index(drop=True)  # compute_day 已按 rps_20 降序排好（NaN 在末尾）
    full_out[full_cols].to_csv(RESULTS_DIR / f"rps_rank_full_{ymd}.csv",
                               index=False, encoding="utf-8-sig")

    top = full_out.head(int(top_n)).copy()
    view = state.reindex(top["ts_code"])[
        ["first_top20_date", "first_top50_date", "prev_rank_20", "rank_chg_20",
         "ret_since_top20", "max_ret_since_top20", "consec_days_in_top50",
         "last_update_date"]].reset_index(drop=True)
    top = pd.concat([top.reset_index(drop=True), view], axis=1)
    top.to_csv(RESULTS_DIR / f"rps_rank_daily_{ymd}.csv",
               index=False, encoding="utf-8-sig")


def peak_memory_gb():
    """进程峰值工作集（Windows PROCESS_MEMORY_COUNTERS.PeakWorkingSetSize）。"""
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        h = wintypes.HANDLE(-1)  # GetCurrentProcess 伪句柄，必须显式 64 位 HANDLE 类型
        for api in (ctypes.windll.psapi.GetProcessMemoryInfo,
                    getattr(ctypes.windll.kernel32, "K32GetProcessMemoryInfo", None)):
            try:
                if api and api(h, ctypes.byref(pmc), pmc.cb):
                    return pmc.PeakWorkingSetSize / (1024 ** 3)
            except Exception:
                continue
    except Exception:
        pass
    try:
        import psutil
        return psutil.Process().memory_info().peak_wset / (1024 ** 3)
    except Exception:
        return -1.0


def main():
    ap = argparse.ArgumentParser(description="RPS 强势股每日监控数据层（观察/跟踪工具）")
    ap.add_argument("--date", type=str, default=None, help="单日运行，YYYYMMDD")
    ap.add_argument("--backfill", type=int, default=None, help="回补最近 N 个交易日（升序逐日）")
    ap.add_argument("--workers", type=int, default=2, help="题材归因预取并发线程数")
    args = ap.parse_args()
    if not args.date and args.backfill is None:
        ap.error("必须指定 --date YYYYMMDD 或 --backfill N")
    if args.date and args.backfill is not None:
        ap.error("--date 与 --backfill 互斥")
    if args.backfill is not None and args.backfill < 1:
        ap.error("--backfill 必须 >= 1")

    t0 = time.time()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    windows = list(cfg["windows"])
    if cfg["defect"].get("action") != "skip_with_manifest":
        raise ValueError("defect.action 仅支持 skip_with_manifest（业主拍板口径，禁止 continue/silent）")

    print("[STEP] 读取交易日历 ...")
    t_step = time.time()
    cal = load_calendar()
    data_max = cal[-1]
    print(f"[INFO] 数据区间 {cal[0].date()} ~ {data_max.date()}，共 {len(cal)} 个交易日"
          f"（耗时 {time.time() - t_step:.1f}s）")

    if args.date:
        try:
            T = pd.Timestamp(dt.datetime.strptime(args.date, "%Y%m%d"))
        except ValueError:
            print(f"[GATE-FAIL] --date 格式错误: {args.date}，应为 YYYYMMDD")
            sys.exit(1)
        if T not in cal:
            print(f"[GATE-FAIL] {T.date()} 不是交易日或无行情数据（与 stock_daily 日历不一致）")
            sys.exit(1)
        if T != data_max:
            print(f"[GATE-WARN] {T.date()} 早于数据最大日期 {data_max.date()}（历史单日研究口径）")
        days = [T]
    else:
        n = min(int(args.backfill), len(cal))
        if n < int(args.backfill):
            print(f"[GATE-WARN] --backfill {args.backfill} 超过日历长度，截断为 {n}")
        days = list(cal[-n:])

    pos0 = cal.get_loc(days[0])
    start_pos = max(0, pos0 - (MAX_WINDOW + LOOKBACK_BUFFER))
    if start_pos == 0 and pos0 < MAX_WINDOW + LOOKBACK_BUFFER:
        print(f"[GATE-WARN] 历史不足 {MAX_WINDOW + LOOKBACK_BUFFER} 个交易日，早期窗口收益将为 NaN")
    start_dt, end_dt = cal[start_pos], days[-1]

    print(f"[STEP] 读取行情 {start_dt.date()} ~ {end_dt.date()}（列裁剪 {len(READ_COLS)} 列）...")
    t_step = time.time()
    df = load_frame(start_dt, end_dt)
    print(f"[INFO] 载入 {len(df)} 行（耗时 {time.time() - t_step:.1f}s）")

    # 主库缺陷日分流（2026-10-04 业主拍板）：在 quality_gate 之前识别并跳过，落缺陷台账留痕。
    # 正常日的 quality_gate 行为一字不改；缺陷日不调用 compute_day/quality_gate/update_state/write_outputs。
    print("[STEP] 识别主库缺陷日（listed_days NaN 比例）...")
    t_step = time.time()
    defect_thr = float(cfg["defect"]["nan_ratio_threshold"])
    defect_days, nan_ratio_map, total_rows_map = detect_defect_days(df, days, defect_thr)
    defect_set = set(defect_days)
    for d in defect_days:
        print(f"[SKIP-DATADEFECT] {d.date()} listed_days NaN 比例 {nan_ratio_map.loc[d]:.3f} "
              f"> {defect_thr}（D:/astock 主库缺陷，当日 PIT 池无法构建，跳过产出）")
    if defect_days:
        print(f"[INFO] 缺陷日清单（共 {len(defect_days)} 天）: "
              f"{', '.join(d.strftime('%Y-%m-%d') for d in defect_days)}")
    if args.backfill is not None:
        print(f"[INFO] 回补窗口 {days[0].date()} ~ {days[-1].date()} 共 {len(days)} 个交易日，"
              f"跳过 {len(defect_days)} 个缺陷日，实际产出 {len(days) - len(defect_days)} 天")
    # 台账留痕（覆盖写）：回补模式每次覆盖写当前窗口台账；单日模式仅当日为缺陷日时覆盖，
    # 防止正常日的单日运行清掉历史台账。
    if args.backfill is not None or defect_days:
        manifest_path = RESULTS_DIR / str(cfg["defect"]["manifest"])
        n_manifest = write_defects_manifest(defect_days, nan_ratio_map, total_rows_map,
                                            manifest_path)
        print(f"[INFO] 缺陷台账已写: {manifest_path.name}（{n_manifest} 行）")
    valid_days = [d for d in days if d not in defect_set]
    print(f"[INFO] 缺陷日识别完成（耗时 {time.time() - t_step:.1f}s）")

    print("[STEP] 构建复权收益矩阵 ...")
    t_step = time.time()
    piv, rets = build_return_pivots(df, windows)
    print(f"[INFO] 复权收益矩阵构建完成（耗时 {time.time() - t_step:.1f}s）")

    # 预期池大小：加载区间内各交易日同口径池大小的中位数（排除当日）
    pmask = pool_mask(df, cfg["pool"])
    day_counts = df.loc[pmask].groupby("trade_date").size()

    print(f"[STEP] 预取涨停池/题材归因（workers={max(1, args.workers)}，产出日 {len(valid_days)} 天）...")
    t_step = time.time()
    enrich = fetch_enrich(valid_days, cfg, args.workers)
    print(f"[INFO] 涨停池/题材归因预取完成（耗时 {time.time() - t_step:.1f}s）")

    state = load_state()
    print(f"[STEP] 状态快照载入：{len(state)} 只股票的历史状态")

    t_loop = time.time()
    for T in valid_days:
        loc = cal.get_loc(T)
        prev_td = cal[loc - 1] if loc > 0 else None
        day_df = df[df["trade_date"] == T]
        pool_df = day_df.loc[pool_mask(day_df, cfg["pool"])]
        out = compute_day(T, pool_df, rets, windows)
        zt, ths = enrich.get(T, ([], []))
        out = apply_enrich(out, zt, ths)

        peers = day_counts.drop(T, errors="ignore")
        expected_pool = int(peers.median()) if len(peers) > 0 else 0
        quality_gate(T, out, cfg, expected_pool, (len(zt), len(ths)))

        state = update_state(state, out, piv.loc[T], T, prev_td)
        save_state(state)
        write_outputs(T, out, state, cfg["top_n_report"])

        top1 = out.iloc[0] if len(out) else None
        top1_desc = f"{top1['ts_code']} rps20={top1['rps_20']:.1f}" if top1 is not None else "无"
        print(f"[RUN] {T.date()} 池={len(out)} 预期池={expected_pool} "
              f"涨停池={len(zt)} 题材={len(ths)} top1={top1_desc}")

    print(f"[INFO] 逐日产出循环耗时 {time.time() - t_loop:.1f}s")
    print(f"[DONE] 实际产出 {len(valid_days)} / 窗口 {len(days)} 个交易日（缺陷日 {len(defect_days)} 个），"
          f"总耗时 {time.time() - t0:.1f}s，峰值内存 {peak_memory_gb():.2f} GB")
    print(f"[DONE] 产物目录: {RESULTS_DIR}")


if __name__ == "__main__":
    main()
