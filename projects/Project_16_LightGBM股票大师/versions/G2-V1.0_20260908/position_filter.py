# coding: utf-8
"""统一位置过滤模块（T0，2026-09-08 落地，VERSIONS 见 G2-V1.0 / V1.4）。

依据 2026-09-08 三步验证（回测对照 + 实盘21笔回放 + 面板分层）落地：
  - R2 高位剔除（Lite/Full 共用）: 距20日高点 > -2% 且 非温和放量突破 → 剔除（核心规则，两口径均有效）
  - R1 追高剔除（仅 Full）      : pre20 > 30% 且 量比 > 1.5 → 剔除（红线60 有效）
  - R3 流动性过滤（仅 Full）    : 5日均额 < 1.0 亿 → 剔除（阈值验证后由 2.0 下调，防误伤 001378）
  - gap_guard 低开校验（执行端）: 极端低开(<=-5%)跳过；低开(-5~-3%)放量(量比>=2)放行，缩量跳过

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
GAP_VR       = float(os.environ.get("GAP_VR", "2.0"))         # 低开放行量比

_CACHE = {"mkt": None}


def _load_merged():
    """主库(前复权 fclose) + 增量库合并。返回列：ts_code/trade_date/fclose/pct_chg/vol/amount。"""
    if _CACHE["mkt"] is not None:
        return _CACHE["mkt"]
    main = pd.read_parquet(DC.MAIN_DAILY, columns=["close", "pct_chg", "vol", "amount", "adj_factor"])
    main = main[main.index.get_level_values("trade_date") >= "2026-01-01"].reset_index()
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


def gap_guard(code, price, pre_close=None, open_price=None, vr=None):
    """执行端低开校验（T3/T4，供 rebalance 买入循环调用）。
    返回 (skip: bool, reason: str|None)。
    规则：
      - 缺数据（pre_close/open 拿不到）→ 不拦截（fail-open，返回 False）
      - open_pct <= GAP_HARD_PCT(-5%)            → 跳过（极端低开，防 003005 型跌停接刀）
      - GAP_HARD_PCT < open_pct <= GAP_OPEN_PCT(-3%) → 需量比>=GAP_VR 放行，否则跳过
      - open_pct > GAP_OPEN_PCT                  → 放行
    """
    if pre_close is None or open_price is None or pre_close <= 0:
        return False, None
    open_pct = (open_price / pre_close - 1.0) * 100.0
    if open_pct <= GAP_HARD_PCT:
        return True, "极端低开%.1f%%跳过买入" % open_pct
    if open_pct <= GAP_OPEN_PCT:
        if vr is not None and vr >= GAP_VR:
            return False, None   # 低开但放量 → 强势承接，放行
        return True, "低开%.1f%%且量比不足%.1f跳过买入" % (open_pct, GAP_VR)
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
