# coding: utf-8
"""daily_market_phase.py — 市场情绪周期 6 阶段每日标签（夜盘任务）。

数据路径: astock 主库优先 + Tushare 在线增量补缺（daily/stk_limit/adj_factor/index_daily，
2000 积分档实测全可用）。astock 主库常态滞后（离线包节奏），窗口内缺失的交易日从
Tushare 在线拉取，口径与 astock 同源（tushare up_limit / pre_close / adj_factor 逐列一致）。

输出:
  D:/QMT_POOL/market_phase.csv   滚动 ~80 交易日窗口（原子写，整文件覆盖，含逐日数据来源列 src）
  飞书卡片日报（复用 qmt_card.send_lark_card，S4U 会话安全）

消费红线: 连板梯队/封板率是收盘截面信息，标签 T 日算、T+1 用，禁止当开盘可知变量。
定位: 描述性 regime 标签（研究侧），不是 P17 已证伪择时假设的翻案，不接任何下单逻辑。

窗口说明: 80 交易日 ≈ MA20 预热 + EMA(α=1/3) 收敛(40日残差<1e-7) + 5 日回看 + 2 日确认；
最早 ~20 个交易日的 phase 可能因 EMA 预热与全量回填略有出入，末日标签为权威值。

用法:
  python daily_market_phase.py                 # 最近已收盘交易日；今日非交易日则跳过(exit 0)
  python daily_market_phase.py --date 20260924 # 指定目标日（测试/补算）
  python daily_market_phase.py --force         # 今日非交易日也按最近交易日算
  python daily_market_phase.py --no-push       # 不推送（本地验证）
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

import numpy as np
import pandas as pd

for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, "D:/QuantLab")
sys.path.insert(0, "D:/QuantLab/projects/Project_16_LightGBM股票大师")  # qmt_card / qmt_config

from factors.market_phase import (  # noqa: E402
    PHASE_LABELS, add_limit_flags, build_daily_metrics,
    classify_phase_series, classify_state_series)

KEY_FILE = "D:/QuantLab/config/data_source_keys.json"
ASTOCK_DAILY = "D:/astock/daily/stock_daily.parquet"
OUT_CSV = "D:/QMT_POOL/market_phase.csv"
WINDOW_DAYS = 80          # 滚动窗口（交易日）
RETRY = 3
CALL_SLEEP = 0.15         # Tushare 限频保护（2000 积分档 ~500 次/min）


def load_pro():
    with open(KEY_FILE, encoding="utf-8") as f:
        tok = json.load(f)["sources"]["tushare"]["token"]
    import tushare as ts
    return ts.pro_api(tok)


def retry_call(fn, *args, **kw):
    last = None
    for i in range(RETRY):
        try:
            return fn(*args, **kw)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("Tushare 调用重试 %d 次仍失败: %s" % (RETRY, last))


def open_days(pro, start, end):
    cal = retry_call(pro.trade_cal, exchange="SSE", start_date=start,
                     end_date=end, is_open="1", fields="cal_date")
    return sorted(cal["cal_date"].tolist())


def fetch_day(pro, ymd):
    """单日全市场: daily + stk_limit + adj_factor → astock 同构行。"""
    d = retry_call(pro.daily, trade_date=ymd,
                   fields="ts_code,close,high,pre_close")
    if len(d) == 0:
        raise RuntimeError("tushare daily %s 返回 0 行（未发布或非交易日）" % ymd)
    sl = retry_call(pro.stk_limit, trade_date=ymd, fields="ts_code,up_limit")
    af = retry_call(pro.adj_factor, trade_date=ymd, fields="ts_code,adj_factor")
    row = d.merge(sl, on="ts_code", how="left").merge(af, on="ts_code", how="left")
    row["trade_date"] = pd.to_datetime(ymd, format="%Y%m%d")
    time.sleep(CALL_SLEEP)
    return row[["trade_date", "ts_code", "close", "high", "pre_close", "up_limit", "adj_factor"]]


def phase_streak(phase_series):
    """从尾部数当前阶段已连续天数。"""
    vals = list(phase_series)
    if not vals:
        return 0
    last = vals[-1]
    n = 0
    for v in reversed(vals):
        if v == last:
            n += 1
        else:
            break
    return n


def build_card(target, daily, src_desc, streak, prev_phase):
    row = daily.iloc[-1]
    ph, ph_cn = row["phase"], PHASE_LABELS.get(row["phase"], "")
    switch = "" if prev_phase == ph else "（前日 %s → 切换）" % PHASE_LABELS.get(prev_phase, prev_phase)
    lines = [
        "**阶段：%s**（连续第 %d 天）%s" % (ph_cn, streak, switch),
        "环境分：%s（%.0f 分）" % (row["state"], row["score"]),
        "",
        "高度 %d ｜ 首板 %d ｜ 2板+ %d ｜ 涨停 %d" % (
            int(row["height"]), int(row["first_board"]), int(row["ge2"]), int(row["limit_up"])),
        "晋级率 %.1f%% ｜ 封板率 %.1f%%" % (row["promo_rate"] * 100, row["seal_rate"] * 100),
        "上涨占比 %.0f%% ｜ 站上MA20 %.0f%% ｜ 沪深300 %+.1f%%" % (
            row["up_pct"], row["above_ma20_pct"] * 100, row["index_pct"] * 100),
        "",
        "<font color='grey'>数据: %s ｜ T 日标签 T+1 可用，描述性观察不作交易依据</font>" % src_desc,
    ]
    return {
        "schema": "2.0",
        "config": {"width_mode": "default", "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "情绪周期日报 · %s %s" % (target, ph_cn)},
            "subtitle": {"tag": "plain_text",
                         "content": "%s · market_phase" % time.strftime("%Y-%m-%d %H:%M:%S")},
            "template": "blue",
            "icon": {"tag": "standard_icon", "token": "notification_colorful"},
            "text_tag_list": [{"tag": "text_tag",
                               "text": {"tag": "plain_text", "content": "情绪周期"},
                               "color": "blue"}],
        },
        "body": {
            "direction": "vertical", "padding": "12px 12px 20px 12px", "vertical_spacing": "8px",
            "elements": [
                {"tag": "interactive_container", "width": "fill", "has_border": True,
                 "border_color": "blue-100", "background_style": "blue-50",
                 "corner_radius": "8px", "padding": "12px", "vertical_spacing": "4px",
                 "elements": [{"tag": "markdown", "content": "\n".join(lines)}]},
            ],
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="目标日 YYYYMMDD（默认最近已收盘交易日）")
    ap.add_argument("--force", action="store_true", help="今日非交易日也按最近交易日计算")
    ap.add_argument("--no-push", action="store_true", help="不推送飞书")
    args = ap.parse_args()
    t0 = time.time()

    today = dt.date.today()
    pro = load_pro()
    days = open_days(pro, (today - dt.timedelta(days=200)).strftime("%Y%m%d"),
                     today.strftime("%Y%m%d"))
    if not days:
        print("[SKIP] 无交易日")
        return 0
    if args.date:
        target = args.date
        if target not in days:
            print("[SKIP] %s 非 A 股交易日" % target)
            return 0
    else:
        target = days[-1]
        if target != today.strftime("%Y%m%d") and not args.force:
            print("[SKIP] 今日(%s)非 A 股交易日，最近交易日=%s（--force 可强算）"
                  % (today.strftime("%Y%m%d"), target))
            return 0
    window = days[-WINDOW_DAYS:]
    print("[1/4] 目标日=%s, 窗口=%s ~ %s (%d 交易日)"
          % (target, window[0], window[-1], len(window)))

    print("[2/4] 数据准备: astock 优先 + Tushare 补缺 ...")
    df = pd.read_parquet(ASTOCK_DAILY,
                         columns=["high", "close", "pre_close", "up_limit", "adj_factor"])
    df = df.reset_index()
    w_start, w_end = pd.to_datetime(window[0]), pd.to_datetime(window[-1])
    base = df[(df["trade_date"] >= w_start) & (df["trade_date"] <= w_end)].copy()
    del df
    # astock「名义覆盖」≠「有效覆盖」: 2026-07-27 实测 up_limit 全列 NaN(数据空洞),
    # 该日会被 add_limit_flags 整日剔除 → 静默断日。判据: 有效涨停价行数>=100 才算覆盖。
    base["_ymd"] = base["trade_date"].dt.strftime("%Y%m%d")
    valid_counts = base.loc[base["up_limit"].notna(), "_ymd"].value_counts()
    covered = set(valid_counts[valid_counts >= 100].index)
    missing = [d for d in window if d not in covered]
    src_map = {d: "astock" for d in covered}
    if missing:
        # 剔除无效日的 astock 残行(防与 Tushare 补数重复)
        base = base[~base["_ymd"].isin(missing)]
    frames = [base.drop(columns=["_ymd"])]
    if missing:
        print("      astock 有效覆盖 %d/%d 日, Tushare 补 %d 日: %s"
              % (len(covered), len(window), len(missing), ", ".join(missing[:12]) +
                 ("..." if len(missing) > 12 else "")))
        for d in missing:
            frames.append(fetch_day(pro, d))
            src_map[d] = "tushare"
    data = pd.concat(frames, ignore_index=True)
    data["ts_code"] = data["ts_code"].astype("category")
    n_src_tushare = sum(1 for d in window if src_map.get(d) == "tushare")
    src_desc = "astock %d 日 + Tushare %d 日" % (len(window) - n_src_tushare, n_src_tushare)

    print("[3/4] 指标 + state + phase（口径与 backfill_market_phase.py 一致）...")
    data["change_pct"] = data["close"] / data["pre_close"] - 1.0
    data = data.sort_values(["ts_code", "trade_date"], kind="mergesort")
    adj_close = data["close"] * data["adj_factor"]
    ma20 = adj_close.groupby(data["ts_code"], observed=True).transform(
        lambda s: s.rolling(20, min_periods=20).mean())
    data["above_ma20"] = (adj_close > ma20).astype(bool)
    data = data.drop(columns=["pre_close", "adj_factor"])
    flagged = add_limit_flags(data)
    idx = retry_call(pro.index_daily, ts_code="000300.SH",
                     start_date=window[0], end_date=window[-1],
                     fields="trade_date,pct_chg")
    idx["trade_date"] = pd.to_datetime(idx["trade_date"], format="%Y%m%d")
    index_pct = dict(zip(idx["trade_date"], np.nan_to_num(idx["pct_chg"].values) / 100.0))
    daily = build_daily_metrics(flagged, index_pct=index_pct)
    daily = daily.loc[pd.to_datetime(window)]  # 只保留窗口内交易日
    states, scores = classify_state_series(daily)
    daily["state"] = states
    daily["score"] = scores
    daily = classify_phase_series(daily, states=states)
    daily["phase_cn"] = daily["phase"].map(PHASE_LABELS)
    daily.index.name = "trade_date"
    daily["src"] = [src_map.get(d.strftime("%Y%m%d"), "?") for d in daily.index]
    assert str(daily.index[-1].date()).replace("-", "") == target, "末日 != 目标日"

    print("[4/4] 落盘 + 推送 ...")
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    tmp = OUT_CSV + ".tmp"
    daily.to_csv(tmp, encoding="utf-8-sig")
    os.replace(tmp, OUT_CSV)
    row = daily.iloc[-1]
    streak = phase_streak(daily["phase"])
    prev_phase = daily["phase"].iloc[-2] if len(daily) >= 2 else row["phase"]
    print("      已写 %s (rows=%d)" % (OUT_CSV, len(daily)))
    print("      %s: phase=%s(%s) state=%s(%.0f) streak=%d 高度=%d 首板=%d ge2=%d 晋级率=%.3f 封板率=%.3f"
          % (target, row["phase"], row["phase_cn"], row["state"], row["score"], streak,
             int(row["height"]), int(row["first_board"]), int(row["ge2"]),
             row["promo_rate"], row["seal_rate"]))
    print("      数据来源: %s, took %.0fs" % (src_desc, time.time() - t0))
    if args.no_push:
        print("      [NO-PUSH] 跳过推送")
        return 0
    import qmt_card as QC
    ok = QC.send_lark_card(build_card(target, daily, src_desc, streak, prev_phase))
    print("      [SEND] 飞书推送 %s" % ("成功" if ok else "失败"))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
