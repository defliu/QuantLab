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
    return max(COMM_MIN, amt * COMM_RATE) + (amt * TRANS_RATE if _is_sh(code) else 0.0)


def _sell_fee(amt, code):
    return max(COMM_MIN, amt * COMM_RATE) + amt * STAMP_RATE + (amt * TRANS_RATE if _is_sh(code) else 0.0)


def fifo_positions():
    """从 qmt_trade_log.csv 用 FIFO 推导持仓净额 + 含费成本（口径对齐 qmt_monitor.fifo_positions_from_log）。
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
            qq[code].append((vol, (amt + _buy_fee(amt, code)) / vol))
        elif side == "SELL":
            sv = vol
            while sv > 0 and qq[code]:
                v, cp = qq[code][0]
                take = min(v, sv)
                sv -= take
                qq[code][0] = (v - take, cp)
                if qq[code][0][0] <= 0:
                    qq[code].popleft()
    out = {}
    for code, dq in qq.items():
        tv = sum(v for v, _ in dq)
        if tv > 0:
            tc = sum(v * c for v, c in dq)
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
