# coding: utf-8
"""统一位置过滤模块（T0，2026-09-08 落地，VERSIONS 见 G2-V1.0 / V1.4）。

依据 2026-09-08 三步验证（回测对照 + 实盘21笔回放 + 面板分层）落地：
  - R2 高位剔除（Lite/Full 共用）: 距20日高点 > -2% 且 非温和放量突破 → 剔除（核心规则，两口径均有效）
  - R1 追高剔除（仅 Full）      : pre20 > 30% 且 量比 > 1.5 → 剔除（红线60 有效）
  - R3 流动性过滤（仅 Full）    : 5日均额 < 1.0 亿 → 剔除（阈值验证后由 2.0 下调，防误伤 001378）
  - gap_guard 低开校验（执行端）: 极端低开(<=-5%)跳过；低开(-5~-3%)放量(单位时间量比>=3.3)放行，缩量跳过
    （T-20261023-007：量比改为时间归一化口径，原「09:50累计量/全天均量」与阈值2.0 组合实测放行率仅0.49%=死代码）

Fail-open 原则：行情/特征计算失败一律放行并告警；PF_DISABLE=1 一键回滚。
只依赖 data_config（行情路径），不 import 任何策略业务模块，避免循环依赖。
"""
import os

import numpy as np
import pandas as pd

import data_config as DC

# ---- 常量（环境变量可覆盖） ----
PF_MODE      = os.environ.get("PF_MODE", "lite")       # lite=仅R2 / full=R1+R2+R3
PF_R2_DIST   = float(os.environ.get("PF_R2_DIST", "-2.0"))    # 距20日高点阈值(%)
PF_BREAK_VR  = float(os.environ.get("PF_BREAK_VR", "2.0"))    # 温和放量突破豁免-量比
PF_BREAK_PCT = float(os.environ.get("PF_BREAK_PCT", "4.0"))   # 温和放量突破豁免-当日涨幅
PF_R1_PRE20  = float(os.environ.get("PF_R1_PRE20", "30.0"))   # 追高-前20日涨幅(%)
PF_R1_VR     = float(os.environ.get("PF_R1_VR", "1.5"))       # 追高-量比
PF_R3_AMT5   = float(os.environ.get("PF_R3_AMT5", "1.0"))     # 流动性阈值(亿)
PF_DISABLE   = os.environ.get("PF_DISABLE", "0") == "1"
# 低开校验（执行端 gap_guard）
GAP_HARD_PCT = float(os.environ.get("GAP_HARD_PCT", "-5.0"))  # 极端低开阈值，直接跳过
GAP_OPEN_PCT = float(os.environ.get("GAP_OPEN_PCT", "-3.0"))  # 低开触发阈值
GAP_VR       = float(os.environ.get("GAP_VR", "3.3"))         # 低开放行量比（单位时间口径，≈全样本 p75）
# 时间归一化量比用：A 股连续竞价总分钟数（09:30-11:30 + 13:00-15:00）
DAY_SESSION_MIN = 240.0

_CACHE = {"mkt": None}


def _load_merged():
    """主库(前复权 fclose) + 增量库合并。返回列：ts_code/trade_date/fclose/pct_chg/vol/amount。"""
    if _CACHE["mkt"] is not None:
        return _CACHE["mkt"]
    main = pd.read_parquet(DC.MAIN_DAILY, columns=["close", "pct_chg", "vol", "amount", "adj_factor"])
    # 窗口自 2023-06 起：既覆盖回测期（2024-07 起 pre20 预热），也覆盖实盘（2026）
    main = main[main.index.get_level_values("trade_date") >= "2023-06-01"].reset_index()
    main["trade_date"] = pd.to_datetime(main["trade_date"])
    main["ts_code"] = main["ts_code"].astype(str)
    last_adj = main.sort_values("trade_date").groupby("ts_code")["adj_factor"].last()
    main["fclose"] = main["close"] * main["adj_factor"] / main["ts_code"].map(last_adj).fillna(1.0)
    main = main[["ts_code", "trade_date", "fclose", "pct_chg", "vol", "amount"]]
    incr = os.path.join(DC.LIVE_DIR, "incremental_daily.parquet")
    parts = [main]
    if os.path.exists(incr):
        try:
            inc = pd.read_parquet(incr)
            inc["trade_date"] = pd.to_datetime(inc["trade_date"])
            inc["ts_code"] = inc["ts_code"].astype(str)
            inc = inc.rename(columns={"volume": "vol"})
            inc["pct_chg"] = (inc["close"] / inc["preClose"] - 1) * 100.0
            inc["amount"] = inc["amount"] / 1000.0          # 元 -> 千元（与主库一致）
            inc["fclose"] = inc["close"]
            parts.append(inc[["ts_code", "trade_date", "fclose", "pct_chg", "vol", "amount"]])
        except Exception as e:
            print("[position_filter] 增量库读取失败，忽略: %r" % (e,))
    df = pd.concat(parts, ignore_index=True)
    df = df.drop_duplicates(["ts_code", "trade_date"], keep="last").sort_values(["ts_code", "trade_date"])
    # 统一 trade_date 精度为 ns（主库 ns vs 增量库可能 s，避免 merge_asof 键类型冲突）
    df["trade_date"] = pd.to_datetime(df["trade_date"]).astype("datetime64[ns]")
    _CACHE["mkt"] = df
    return df


def _asof_dates(series):
    """把 trade_date 统一为 datetime（兼容 int64 '20260907' / 字符串 '2026-09-04'）。"""

    def _one(x):
        try:
            s = str(x).strip()
            if len(s) == 8 and s.isdigit():
                return pd.Timestamp("%s-%s-%s" % (s[:4], s[4:6], s[6:]))
            return pd.Timestamp(s)
        except Exception:
            return pd.NaT
    return series.apply(_one)


def add_position_features(cand, target_date):
    """为候选 df（含 ts_code 列）附加位置特征：pre20/dist_hi20/vr/amt5/pct_chg。
    asof 语义：每只候选取「自身 trade_date（≤target_date）」的最新特征行，避免跨日取错。
    返回复制后的 df；特征计算失败/缺失填 NaN（调用方按 fail-open 放行）。"""
    out = cand.copy()
    for c in ("pre20", "dist_hi20", "vr", "amt5", "pct_chg"):
        if c not in out.columns:
            out[c] = np.nan
    try:
        mkt = _load_merged()
        mkt = mkt[mkt["trade_date"] <= pd.Timestamp(target_date)].copy()
        g = mkt.groupby("ts_code")
        mkt["pre20"] = (mkt["fclose"] / g["fclose"].shift(20) - 1) * 100.0
        mkt["hi20"] = g["fclose"].transform(lambda s: s.rolling(20, min_periods=5).max())
        mkt["dist_hi20"] = (mkt["fclose"] / mkt["hi20"] - 1) * 100.0
        mkt["vr"] = mkt["vol"] / g["vol"].transform(lambda s: s.rolling(5, min_periods=3).mean().shift(1))
        mkt["amt5"] = g["amount"].transform(lambda s: s.rolling(5, min_periods=3).mean()) / 1e5
        feat = mkt[["ts_code", "trade_date", "pre20", "dist_hi20", "vr", "amt5", "pct_chg"]]
        feat = feat.sort_values("trade_date")
        # 候选自身 asof 日（缺 trade_date 时用 target_date）
        if "trade_date" in out.columns and out["trade_date"].notna().any():
            out["_asof"] = _asof_dates(out["trade_date"])
        else:
            out["_asof"] = pd.Timestamp(target_date)
        # 统一 merge_asof 键精度为 ns（标量 Timestamp 赋值会得到 datetime64[s]，与 feat 的 ns 冲突）
        out["_asof"] = pd.to_datetime(out["_asof"]).astype("datetime64[ns]")
        feat["trade_date"] = feat["trade_date"].astype("datetime64[ns]")
        out = out.sort_values("_asof")
        out = pd.merge_asof(out, feat, left_on="_asof", right_on="trade_date",
                            by="ts_code", direction="backward")
        for c in ("pre20", "dist_hi20", "vr", "amt5", "pct_chg"):
            if c + "_x" in out.columns and c + "_y" in out.columns:
                out[c] = out[c + "_y"].where(out[c + "_y"].notna(), out[c + "_x"])
                out = out.drop(columns=[c + "_x", c + "_y"])
            elif c + "_y" in out.columns:
                out[c] = out[c + "_y"]
                out = out.drop(columns=[c + "_y"])
        out = out.drop(columns=["_asof"], errors="ignore")
        out = out.drop(columns=["trade_date_y"], errors="ignore")
        out = out.rename(columns={"trade_date_x": "trade_date"}, errors="ignore")
    except Exception as e:
        print("[position_filter] 特征计算失败，fail-open 放行: %r" % (e,))
    return out


def apply_rules(cand, target_date=None, mode=None, reason_out=None):
    """对候选池应用位置过滤。返回过滤后的 df（保留原始列）。
    mode: "lite"=仅R2 / "full"=R1+R2+R3，缺省取 PF_MODE。reason_out(可选 dict) 收集 {code: 规则名}。
    PF_DISABLE=1 时直接返回原候选（一键回滚）。"""
    if PF_DISABLE or cand is None or len(cand) == 0:
        return cand
    mode = mode or PF_MODE
    if target_date is None:
        if "trade_date" in cand.columns and len(cand):
            target_date = _asof_dates(cand["trade_date"]).max()
        else:
            target_date = pd.Timestamp.today().strftime("%Y-%m-%d")
    df = add_position_features(cand, pd.Timestamp(target_date))
    drop = {}
    if mode in ("lite", "full"):
        m = (df["dist_hi20"].notna()) & (df["dist_hi20"] > PF_R2_DIST) & \
            ~((df["vr"].fillna(0) >= PF_BREAK_VR) & (df["pct_chg"].fillna(99) <= PF_BREAK_PCT))
        drop.update({c: "R2高位" for c in df.loc[m, "ts_code"]})
    if mode == "full":
        m1 = (df["pre20"].notna()) & (df["pre20"] > PF_R1_PRE20) & (df["vr"].fillna(0) > PF_R1_VR)
        m3 = (df["amt5"].notna()) & (df["amt5"] < PF_R3_AMT5)
        drop.update({c: "R1追高" for c in df.loc[m1, "ts_code"]})
        drop.update({c: "R3低流动" for c in df.loc[m3, "ts_code"]})
    if reason_out is not None:
        reason_out.update(drop)
    if not drop:
        return df
    kept = df[~df["ts_code"].isin(drop)]
    return kept


def fetch_ohlc(code):
    """腾讯行情取 现价/昨收/今开/当日成交量(手)/实时名称。失败返回 None（T4 低开校验用，供 rebalance 端共用）。
    name（T-20261012-001）：实时证券简称（含 ST 前缀），供 st_guard 免二次请求直判。"""
    try:
        import urllib.request
        sym = code.split(".")[0]
        ex = code.split(".")[1].lower()
        url = "http://qt.gtimg.cn/q=%s%s" % (ex, sym)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=5).read().decode("gbk", errors="ignore")
        if "~" in raw:
            f = raw.split("~")
            if len(f) > 6:
                return {"price": float(f[3]), "pre_close": float(f[4]),
                        "open": float(f[5]), "vol_lot": float(f[6]),
                        "name": f[1].strip() if len(f) > 1 and f[1].strip() else None}
    except Exception:
        pass
    return None


def recent_avg_vol(code, n=5):
    """增量库取该股最近 n 个交易日平均成交量（手）；数据不足/失败返回 None（T4 低开校验用）。"""
    try:
        incr = os.path.join(DC.LIVE_DIR, "incremental_daily.parquet")
        if not os.path.exists(incr):
            return None
        d = pd.read_parquet(incr, columns=["ts_code", "trade_date", "volume"])
        d["ts_code"] = d["ts_code"].astype(str)
        sub = d[d["ts_code"] == code].sort_values("trade_date")
        if len(sub) < 3:
            return None
        return float(sub["volume"].tail(n).mean())
    except Exception:
        return None


def elapsed_session_min(now_hm=None):
    """当日已完成的连续竞价分钟数（09:30 起算，跳过 11:30-13:00 午休）。

    now_hm: 'HH:MM' 字符串；None 取本机当前时间。开盘前返回 0，收盘后返回 240。
    仅用于把「盘中累计量」还原成「单位时间成交量」，不做任何交易决策。
    """
    if now_hm is None:
        import datetime as _dt
        now_hm = _dt.datetime.now().strftime("%H:%M")
    try:
        hh, mm = int(now_hm[:2]), int(now_hm[3:5])
    except Exception:
        return 0.0
    cur = hh * 60 + mm
    am_s, am_e = 9 * 60 + 30, 11 * 60 + 30
    pm_s, pm_e = 13 * 60, 15 * 60
    if cur <= am_s:
        return 0.0
    if cur <= am_e:
        return float(cur - am_s)
    if cur <= pm_s:
        return float(am_e - am_s)
    return float((am_e - am_s) + min(cur, pm_e) - pm_s)


def gap_volume_ratio(code, vol_lot, now_hm=None, n=5):
    """低开校验用的**单位时间量比**（T-20261023-007）。

    vr = (当日累计成交量 / 已开盘分钟数) / (前 n 日全天均量 / 240)

    语义：>1 表示该股今日成交节奏快于自身近期常态，2.0 = 放量一倍。
    缺数据（无均量 / 未开盘 / vol 非法）返回 None → gap_guard fail-open。

    背景（原口径缺陷）：旧口径 vr = 当日累计量 / 前5日**全天**均量，09:50 时点分子只含
    全天 8% 的时间却与全天量比，正常量水平 vr≈0.08~0.2，阈值 2.0 实测放行率仅 0.49%
    → 「低开放量放行」分支等于死代码。本函数按已开盘分钟数归一化后，阈值才具备量能含义。
    """
    if not vol_lot or vol_lot <= 0:
        return None
    nmin = elapsed_session_min(now_hm)
    if nmin <= 0:
        return None
    avg5 = recent_avg_vol(code, n=n)
    if not avg5 or avg5 <= 0:
        return None
    return float((vol_lot / nmin) / (avg5 / DAY_SESSION_MIN))


def gap_guard(code, price, pre_close=None, open_price=None, vr=None):
    """执行端低开校验（T3/T4，供 rebalance 买入循环调用）。
    返回 (skip: bool, reason: str|None)。
    规则：
      - 缺数据（pre_close/open 拿不到）→ 不拦截（fail-open，返回 False）
      - open_pct <= GAP_HARD_PCT(-5%)            → 跳过（极端低开，防 003005 型跌停接刀）
      - GAP_HARD_PCT < open_pct <= GAP_OPEN_PCT(-3%) → 需单位时间量比>=GAP_VR 放行，否则跳过
      - open_pct > GAP_OPEN_PCT                  → 放行
    vr 由 gap_volume_ratio() 计算（时间归一化口径，见该函数 docstring）。
    """
    if pre_close is None or open_price is None or pre_close <= 0:
        return False, None
    open_pct = (open_price / pre_close - 1.0) * 100.0
    if open_pct <= GAP_HARD_PCT:
        return True, "极端低开%.1f%%跳过买入" % open_pct
    if open_pct <= GAP_OPEN_PCT:
        if vr is not None and vr >= GAP_VR:
            return False, None   # 低开但放量 → 强势承接，放行
        return True, "低开%.1f%%且量比不足%.1f跳过买入(量比%s)" % (
            open_pct, GAP_VR, ("%.2f" % vr) if vr is not None else "NA")
    return False, None


# ============================================================================
# ST 风险警示剔除（T-20261012-001，诚哥拍板 2026-10-12）：三层防线
#   背景：候选链特征快照（g2_latest_features.parquet 等）无 is_st 列，候选生成端
#         完全看不到 ST 状态 → 2026-10-12 G2 臂实盘买入 *ST华鹏 603021.SH 实锤。
#   L1 候选生成端（根治）：deploy_predict*.py / review_full.py 批量剔除
#       （load_st_codes 主库 is_st 快照 + review_full 实时名称直判）
#   L2 执行端买入拦截：rebalance_g2/ens/daily 买入循环 st_guard
#       （主库 is_st 快照 + 腾讯实时名称双层，抓快照后新戴帽）
#   L3 执行端强制卖出：rebalance_g2/ens 卖出循环 + rebalance_daily 成熟度循环对
#       ST 持仓强制 SELL，不等持有期（诚哥拍板 2026-10-12：次日换仓日卖出）
#   fail-open 哲学与 gap_guard 一致：两层都拿不到 → 放行（不因数据缺失阻塞交易）；
#   但 L1/L2 双层叠加使漏网概率极低（主库快照滞后约 2 周由腾讯实时名称补）。
# ============================================================================

_ST_CACHE = {"codes": None, "asof": None}


def load_st_codes(refresh=False):
    """主库 is_st（最新数据日快照）→ ST 代码 set；读取失败返回 None（fail-open）。

    主库 daily parquet 的 is_st 为日频快照（1=ST），ts_code/trade_date 为 MultiIndex
    索引列；随供应商全量包更新（当前滞后约 2 周），快照后新戴帽由 fetch_name
    实时层兜底（见 st_guard）。进程内缓存，重复调用零开销。
    """
    if _ST_CACHE["codes"] is not None and not refresh:
        return _ST_CACHE["codes"]
    try:
        main = pd.read_parquet(DC.MAIN_DAILY, columns=["is_st"])
        dates = main.index.get_level_values("trade_date")
        asof = dates.max()
        sub = main[dates == asof]
        codes = set(sub.index.get_level_values("ts_code")[sub["is_st"].values == 1].astype(str))
        _ST_CACHE["codes"] = codes
        _ST_CACHE["asof"] = str(asof)
        return codes
    except Exception as e:
        print("[position_filter] ST 名单读取失败（fail-open 放行）: %r" % (e,))
        return None


def fetch_name(code):
    """腾讯行情取实时证券名称（f[1]，含 ST/*ST 前缀）。失败返回 None。

    与 fetch_ohlc 同源 API（qt.gtimg.cn），名称为交易所最新简称——主库快照后
    新戴帽/摘帽以此为准（2026-10-12 实测 *ST华鹏 603021 返回 "*ST华鹏"）。
    """
    try:
        import urllib.request
        sym = code.split(".")[0]
        ex = code.split(".")[1].lower()
        url = "http://qt.gtimg.cn/q=%s%s" % (ex, sym)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=5).read().decode("gbk", errors="ignore")
        if "~" in raw:
            f = raw.split("~")
            if len(f) > 1 and f[1].strip():
                return f[1].strip()
    except Exception:
        pass
    return None


def is_st_name(name):
    """证券名称是否含 ST 风险警示前缀（ST/*ST/S*ST 统一判 'ST' in name）。

    A 股证券简称中 'ST' 仅出现在风险警示前缀，中文简称无歧义误伤。
    """
    return bool(name) and "ST" in str(name).upper()


def st_guard(code, name=None):
    """ST 风险警示买入/持仓拦截（T-20261012-001）。返回 (skip: bool, reason: str|None)。

    双层判定（与 gap_guard 同 fail-open 哲学）：
      1) 主库 is_st 名单（load_st_codes，内存批量，最新快照日，滞后约 2 周）；
      2) 腾讯实时名称含 'ST'（name 参数已取则直用，否则现取——抓快照后新戴帽）。
    两层都拿不到 → (False, None) 放行。
    """
    try:
        st_codes = load_st_codes()
        if st_codes is not None and code in st_codes:
            return True, "ST风险警示剔除(主库is_st快照%s)" % (_ST_CACHE.get("asof") or "?")
    except Exception:
        pass
    try:
        nm = name if name else fetch_name(code)
        if is_st_name(nm):
            return True, "ST风险警示剔除(%s)" % nm
    except Exception:
        pass
    return False, None


if __name__ == "__main__":
    # 自检：20 笔实盘样本回放应与验证报告一致（601999 放行 / 003005 R1 命中 / 601579 R1+R2）
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    samples = [
        ("300413.SZ", "2026-09-04"), ("003005.SZ", "2026-09-04"), ("601999.SH", "2026-09-07"),
        ("601579.SH", "2026-09-07"), ("002190.SZ", "2026-09-02"), ("000737.SZ", "2026-08-26"),
        ("300456.SZ", "2026-08-31"), ("002237.SZ", "2026-08-21"), ("300916.SZ", "2026-08-21"),
        ("300475.SZ", "2026-09-04"), ("300964.SZ", "2026-09-02"), ("000960.SZ", "2026-08-25"),
    ]
    cand = pd.DataFrame(samples, columns=["ts_code", "trade_date"])
    df = add_position_features(cand, "2026-09-07")
    print(df[["ts_code", "trade_date", "pre20", "dist_hi20", "vr", "amt5", "pct_chg"]].round(2).to_string(index=False))
