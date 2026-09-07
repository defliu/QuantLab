# coding: utf-8
"""Tushare 权威数据源封装（T-20260903 需求「3条都加」）。

F3/F5/F6 的 T-1 权威口径补充（review_full.py 复用，全部 fail-safe：任何失败返回空，绝不阻塞主流程）：
  1) F3 业绩公告：Tushare forecast（业绩预告）+ express（业绩快报）
     - anns_d（全量公告）需 5000 积分，当前 token 无权限 → 用业绩预告/快报替代（催化价值最高的公告类型）
  2) F5 申万一级行业 T-1 涨幅：本地 sw_l1_daily 成分映射 + incremental_daily 新鲜收盘 等权自算
     - sw_daily 接口需 5000 积分，当前无权限 → 本地申万一级行业日线（sw_l1_daily）+ 增量库自算等价替代
  3) F6 daily_basic（T-1 PE/换手）：交叉验证 + 实时缺失时权威兜底

注意：本模块必须兼容 review_full 所在 python（miniqmt venv，>=3.7），不要引入重依赖。
"""
import json
import os
import time

import numpy as np
import pandas as pd
import tushare as ts

KEYS_PATH = "D:/QuantLab/config/data_source_keys.json"
CACHE_DIR = "D:/QuantLab/projects/Project_16_LightGBM股票大师/data/tushare_cache"
SW_L1_PATH = "D:/astock/index/sw_l1_daily.parquet"
INCR_DAILY_PATH = "D:/QuantLab/projects/Project_16_LightGBM股票大师/data_live/incremental_daily.parquet"
FRESH_LAG = 2  # 权威口径允许的最大滞后天数（T-1 计 1 天，滞后<=2 视为新鲜，与 F2 moneyflow 一致）

_token = None
_pro = None


def get_token():
    global _token
    if _token is None:
        try:
            _token = json.load(open(KEYS_PATH, encoding="utf-8"))["sources"]["tushare"]["token"]
        except Exception:
            _token = ""
    return _token


def get_pro():
    global _pro
    if _pro is None:
        tok = get_token()
        if not tok or "待填" in str(tok):
            return None
        try:
            _pro = ts.pro_api(tok, timeout=30)
        except Exception as e:
            print("    [tushare_source] pro_api 初始化失败: %s" % e)
            _pro = None
    return _pro


def _ensure_cache():
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
    except Exception:
        pass


def _today():
    return pd.Timestamp.now().normalize()


def _fresh(asof_date, asof):
    """判断权威口径新鲜度：asof（YYYYMMDD）相对今日滞后<=FRESH_LAG 天视为新鲜。"""
    try:
        lag = (_today() - pd.Timestamp(str(asof_date))).days
        return lag <= FRESH_LAG
    except Exception:
        return False


# ---------------------------------------------------------------- F6 daily_basic
def daily_basic_map(codes, asof):
    """Tushare daily_basic 指定日（T-1）pe_ttm/turnover_rate，按日缓存本地 parquet。
    返回 {ts_code: {"pe_ttm": float, "turnover_rate": float, "asof": YYYYMMDD}}；失败/无权限返回 {}。"""
    if not get_pro():
        return {}
    d = str(asof)
    cache = os.path.join(CACHE_DIR, "daily_basic_%s.parquet" % d)
    _ensure_cache()
    try:
        if os.path.exists(cache):
            df = pd.read_parquet(cache)
        else:
            df = get_pro().daily_basic(trade_date=d, fields="ts_code,pe_ttm,turnover_rate,volume_ratio")
            if len(df):
                df.to_parquet(cache, index=False)
            else:
                return {}
        codes = set(str(c) for c in codes)
        df = df[df["ts_code"].isin(codes)]
        out = {}
        for _, r in df.iterrows():
            out[r["ts_code"]] = {
                "pe_ttm": float(r["pe_ttm"]) if pd.notna(r.get("pe_ttm")) else np.nan,
                "turnover_rate": float(r["turnover_rate"]) if pd.notna(r.get("turnover_rate")) else np.nan,
                "asof": d,
            }
        return out
    except Exception as e:
        print("    [tushare_source] daily_basic(%s) 失败: %s" % (d, e))
        return {}


# ---------------------------------------------------------------- F3 forecast/express
_POSITIVE_TYPES = {"预增", "略增", "扭亏", "续盈", "减亏"}
_NEGATIVE_TYPES = {"预减", "略减", "首亏", "续亏"}


def _ann_base_score(items):
    """业绩公告 → 保守基准催化分：有公告事件 5.0；预告明确向好 6.0；预告明确变差 4.0。"""
    has_pre = any(i["type"] == "业绩预告" for i in items)
    if has_pre:
        for i in items:
            t = str(i.get("type_s", ""))
            if t in _POSITIVE_TYPES:
                return 6.0
            if t in _NEGATIVE_TYPES:
                return 4.0
        return 5.0  # 预告但类型不确定
    return 5.0  # 仅业绩快报（中性事件）


def _ann_note(items):
    parts = []
    for i in items[:3]:
        p = i.get("p_change_min")
        pmax = i.get("p_change_max")
        rng = ""
        if p is not None and pd.notna(p) and pmax is not None and pd.notna(pmax):
            rng = " 净利变动[%.0f%%~%.0f%%]" % (float(p), float(pmax))
        parts.append("%s%s(%s)" % (i["type"], rng, i.get("ann_date", "")))
    return "近15日业绩公告: " + "; ".join(parts)


def earnings_anns_map(codes, asof):
    """Tushare forecast（业绩预告）+ express（业绩快报），候选近 15 天业绩公告。
    返回 {ts_code: {"score": float, "note": str, "items": [...]}}；失败/无权限返回 {}。"""
    if not get_pro():
        return {}
    end = str(asof)
    try:
        start_dt = pd.Timestamp(end) - pd.Timedelta(days=25)
        start = start_dt.strftime("%Y%m%d")
    except Exception:
        start = end
    out = {}
    try:
        for code in codes:
            code = str(code)
            items = []
            fd = get_pro().forecast(ts_code=code, start_date=start, end_date=end)
            if fd is not None and len(fd):
                for _, r in fd.iterrows():
                    items.append({"type": "业绩预告", "ann_date": str(r.get("ann_date", "")),
                                  "type_s": str(r.get("type", "")),
                                  "p_change_min": r.get("p_change_min"), "p_change_max": r.get("p_change_max")})
            ex = get_pro().express(ts_code=code, start_date=start, end_date=end)
            if ex is not None and len(ex):
                for _, r in ex.iterrows():
                    items.append({"type": "业绩快报", "ann_date": str(r.get("ann_date", ""))})
            if items:
                out[code] = {"score": _ann_base_score(items), "note": _ann_note(items), "items": items}
        return out
    except Exception as e:
        print("    [tushare_source] forecast/express 失败: %s" % e)
        return {}


# ---------------------------------------------------------------- F5 申万一级行业 T-1 自算
def _load_sw_membership():
    """读本地 sw_l1_daily（申万一级行业日线，静态成分映射），返回 {con_code: (index_ts_code, index_name)}。
    con_codes 列是 numpy 数组的字符串表示（"['600825.SH' '600831.SH' ...]"），需解析。"""
    try:
        sw = pd.read_parquet(SW_L1_PATH).reset_index()
        if "trade_date" in sw.columns:
            sw["trade_date"] = pd.to_datetime(sw["trade_date"])
            last = sw["trade_date"].max()
            sw = sw[sw["trade_date"] == last]
        mapping = {}
        for _, r in sw.iterrows():
            raw = str(r.get("con_codes", ""))
            raw = raw.strip("[]").replace("'", "").replace('"', "")
            for c in raw.split():
                c = c.strip()
                if c and c not in mapping:
                    mapping[c] = (str(r["ts_code"]), str(r["name"]))
        return mapping
    except Exception as e:
        print("    [tushare_source] sw_l1 成分映射失败: %s" % e)
        return {}


def sw_l1_pct_map(codes, asof):
    """本地自算候选的申万一级行业 T-1 涨幅（T-20260903 权威口径）：
    sw_l1_daily 成分（静态）+ incremental_daily（T-1 新鲜个股 close/preClose）等权自算。
    返回 {ts_code: {"sw_l1": 行业名, "pct": float, "asof": YYYYMMDD}}；成分缺失/不新鲜/失败返回 {}。"""
    try:
        mem = _load_sw_membership()
        if not mem:
            return {}
        inc = pd.read_parquet(INCR_DAILY_PATH)
        inc = inc[inc["suspendFlag"].fillna(0) == 0]
        latest = pd.Timestamp(pd.Timestamp(inc["trade_date"].max()).date())
        if not _fresh(latest.strftime("%Y%m%d"), latest):
            print("    [tushare_source] 增量库行业自算最新 %s 不新鲜，跳过" % latest.date())
            return {}
        # 候选 -> 申万一级行业
        cand_ind = {}
        for code in codes:
            c = str(code).strip()
            if c in mem:
                cand_ind[c] = mem[c]
        if not cand_ind:
            return {}
        # 最新日个股 pct
        day = inc[inc["trade_date"] == latest]
        if len(day) == 0:
            return {}
        day = day.copy()
        day["pct"] = (day["close"] / day["preClose"] - 1.0) * 100.0
        day = day.set_index("ts_code")["pct"]
        out = {}
        for code, (idx_code, ind_name) in cand_ind.items():
            # 该行业成分在最新日的涨跌幅（等权平均，近似申万一级行业指数涨幅）
            cons = [c2 for c2 in mem if mem[c2][0] == idx_code]
            sub = day.reindex(cons).dropna()
            if len(sub) < max(5, len(cons) // 10):
                continue
            out[code] = {"sw_l1": ind_name, "pct": float(sub.mean()), "asof": latest.strftime("%Y%m%d")}
        return out
    except Exception as e:
        print("    [tushare_source] sw_l1 行业自算失败: %s" % e)
        return {}


if __name__ == "__main__":
    # 自测：真实候选
    codes = ["300413.SZ", "002190.SZ", "003005.SZ", "300475.SZ", "301183.SZ"]
    asof = "20260903"
    t0 = time.time()
    db = daily_basic_map(codes, asof)
    print("daily_basic:", {k: (round(v['pe_ttm'], 1), v['asof']) for k, v in db.items()}, "%.1fs" % (time.time() - t0))
    t0 = time.time()
    ea = earnings_anns_map(codes, asof)
    print("earnings_anns:", {k: (v['score'], v['note'][:40]) for k, v in ea.items()}, "%.1fs" % (time.time() - t0))
    t0 = time.time()
    sw = sw_l1_pct_map(codes, asof)
    print("sw_l1:", {k: (v['sw_l1'], round(v['pct'], 2), v['asof']) for k, v in sw.items()}, "%.1fs" % (time.time() - t0))
