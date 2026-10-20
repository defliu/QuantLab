# coding: utf-8
"""V1.3 QMT 内置风控 · 外部成本锚生成器（Python 3.10，只读 qmt_trade_log.csv）。

QMT 内置风控策略（build/strategy_p16_v13_risk.py）的止损/止盈/追盈线依赖持仓成本，
而 QMT open_price 不可靠（T-20260827-002 教训），成本权威源 = qmt_trade_log.csv FIFO 含费成本。
本脚本每日（盘前）把 FIFO 持仓成本写成 QMT 内置可读的成本表：
    D:/QMT_POOL/p16_v13_risk/cmd/positions_cfg_v13_<date>.json
    {"account_id":"67014907","date":"YYYYMMDD","positions":[{"code":"600522.SH","cost":...,"vol":...}]}

用法：
    python gen_positions_cfg_v13.py                 # 默认日期=今天
    python gen_positions_cfg_v13.py --date 20260907
建议接入定时任务：工作日 09:00（开盘前）生成，QMT 内置 init 时读取。
"""
import argparse
import collections
import json
import os
import time

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
TRADE_LOG = os.path.join(PROJ, "data", "qmt_trade_log.csv")
ACCOUNT_ID = "67014907"
BRIDGE_DIR = "D:/QMT_POOL/p16_v13_risk"
CMD_DIR = os.path.join(BRIDGE_DIR, "cmd")

# 费用（与 qmt_config / FIFO 口径一致：佣金万2.5 最低5元、印花税万5 仅卖出、过户费万0.1 仅沪市）
COMM_RATE = 0.00025
COMM_MIN = 5.0
STAMP_RATE = 0.0005
TRANS_RATE = 0.00001


def _is_sh(code):
    return code.startswith("6")


def _buy_fee(amt, code):
    # 过户费沪深双边（2026-10-19 修正，审计 C1）
    return max(COMM_MIN, amt * COMM_RATE) + amt * TRANS_RATE


def _sell_fee(amt, code):
    return max(COMM_MIN, amt * COMM_RATE) + amt * STAMP_RATE + amt * TRANS_RATE


def _adj_series(code):
    """构造 {YYYY-MM-DD: adj} 累计复权序列 + 返回 (series, latest_adj)。

    权威源 = 主库 adj_factor（后复权因子，已含历史全部除权跳变，逐日稳定）。
    增量库/腾讯仅用于把 latest_adj 延伸到主库未覆盖的最新交易日（只取相对变化率，
    不复写 series 中已存在日期的值）——避免增量库滚动窗口丢跳变导致除权因子次日回弹
    （T-20260915-003：601058 成本 14.4838→14.331→14.4838→14.8426 漂移根因）。"""
    import pandas as pd
    series = {}
    last_adj = None
    last_close = None
    main = r"D:/astock/daily/stock_daily.parquet"
    if os.path.exists(main):
        try:
            d = pd.read_parquet(main, columns=["adj_factor", "close"])
            d = d[d.index.get_level_values("ts_code") == code]
            if len(d):
                dates = [str(x)[:10] for x in d.index.get_level_values("trade_date")]
                adjs = d["adj_factor"].tolist()
                closes = d["close"].tolist()
                for dt, a, c in zip(dates, adjs, closes):
                    series[dt] = float(a)
                last_adj = float(adjs[-1])
                last_close = float(closes[-1])
        except Exception:
            pass
    incr = os.path.join(PROJ, "data_live", "incremental_daily.parquet")
    incr_latest = None
    if os.path.exists(incr) and last_adj:
        try:
            d2 = pd.read_parquet(incr, columns=["trade_date", "ts_code", "close", "preClose"])
            d2["ts_code"] = d2["ts_code"].astype(str)
            sub = d2[d2["ts_code"] == code].sort_values("trade_date")
            for _, row in sub.iterrows():
                dt = str(row["trade_date"])[:10]
                close = float(row["close"])
                pre = float(row["preClose"])
                if dt in series:
                    last_close = close
                    continue
                if last_close is not None and pre > 0 and abs(pre / last_close - 1.0) > 0.005:
                    last_adj = last_adj * (pre / last_close)
                series[dt] = last_adj
                last_close = close
            incr_latest = str(sub["trade_date"].iloc[-1])[:10]
        except Exception:
            pass
    # 腾讯延伸仅当增量库滞后于主库（最后日期 < 主库最后日期）才用——增量库已覆盖最新时
    # 腾讯昨收(last_close 的 preClose) 会与 last_close 跨日比较误触发跳变（T-20260915-003）。
    if last_close and (incr_latest is None or incr_latest < (sorted(series.keys())[-1] if series else "")):
        try:
            import urllib.request
            sym = code.split(".")[0]
            ex = code.split(".")[1].lower()
            url = "http://qt.gtimg.cn/q=%s%s" % (ex, sym)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=5).read().decode("gbk", errors="ignore")
            f = raw.split("~")
            if len(f) > 4:
                pre_close = float(f[4])
                if pre_close > 0 and abs(pre_close / last_close - 1.0) > 0.005:
                    last_adj = last_adj * (pre_close / last_close)
        except Exception:
            pass
    return series, last_adj


def fifo_positions():
    """从 qmt_trade_log.csv 用 FIFO 推导持仓净额 + 含费成本（口径对齐 qmt_monitor.fifo_positions_from_log）。

    除权调整（2026-09-11 加）：成本 = 原始含费成本 × (今日累计 adj / 买入日 adj)——
    对齐前复权口径，累计除权（含今日 XD）稳定生效、不会次日回弹。
    否则除息日（如 601058 09-11 XD）成本锚不调、止损线虚高被除权跌价机械消耗。
    返回 {code: (vol, cost_per_share)}。"""
    if not os.path.exists(TRADE_LOG):
        print("!! 无成交记录 %s" % TRADE_LOG)
        return {}
    import csv
    rows = []
    with open(TRADE_LOG, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if str(r.get("account_id", "") or "").strip() != ACCOUNT_ID:
                continue
            rows.append(r)
    rows.sort(key=lambda r: r.get("time", ""))
    qq = collections.defaultdict(collections.deque)
    for r in rows:
        code, side = r.get("code", ""), r.get("side", "")
        if not code:
            continue
        try:
            vol = int(float(r["vol"]))
            price = float(r["price"])
        except (TypeError, ValueError, KeyError):
            continue
        if side == "BUY":
            amt = price * vol
            buy_date = (r.get("time", "") or "")[:10]
            qq[code].append((vol, (amt + _buy_fee(amt, code)) / vol, buy_date))
        elif side == "SELL":
            sv = vol
            while sv > 0 and qq[code]:
                v, cp, bd = qq[code][0]
                take = min(v, sv)
                sv -= take
                qq[code][0] = (v - take, cp, bd)
                if qq[code][0][0] <= 0:
                    qq[code].popleft()
    adj_cache = {}
    out = {}
    for code, dq in qq.items():
        tv = sum(v for v, _, _ in dq)
        if tv > 0:
            if code not in adj_cache:
                adj_cache[code] = _adj_series(code)
            series, latest = adj_cache[code]
            tc = 0.0
            for v, cp, bd in dq:
                factor = 1.0
                if latest and latest > 0:
                    base = series.get(bd, 0) or 0
                    if base > 0 and base != latest:
                        # 前复权口径：成本折算 = 买入日adj / 最新adj（除息后 latest 跳升 → factor<1 成本下调）
                        factor = base / latest
                tc += v * cp * factor
            out[code] = (tv, tc / tv)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=time.strftime("%Y%m%d"))
    args = ap.parse_args()
    os.makedirs(CMD_DIR, exist_ok=True)
    pos = fifo_positions()
    if not pos:
        print("[gen_positions_cfg_v13] FIFO 持仓为空（空仓），仍写出空成本表以清空 QMT 端持仓")
    positions = [{"code": code, "vol": vol, "cost": round(cost, 4)} for code, (vol, cost) in sorted(pos.items())]
    data = {"account_id": ACCOUNT_ID, "date": args.date, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "positions": positions}
    out = os.path.join(CMD_DIR, "positions_cfg_v13_%s.json" % args.date)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, out)
    print("[gen_positions_cfg_v13] %d 条持仓 -> %s" % (len(positions), out))
    for p in positions:
        print("    %s  %d股  成本%.4f" % (p["code"], p["vol"], p["cost"]))


if __name__ == "__main__":
    main()
