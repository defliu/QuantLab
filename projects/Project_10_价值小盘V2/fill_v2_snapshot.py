# coding=utf-8
"""V2 自闭环数据填充（Step 2c）：生成 SNAPSHOT / PERIOD_LEDGER 字面量并注入 strategy_v2.py。

数据源（沙箱外管道，全部为 D:/QMT_POOL 既有产物）：
  - financial_{pe_ttm,pb,circ_mv,total_mv,bps,roe,profit_dedt}.csv
  - industry_map.csv / delist_info.csv / st_list.csv
  - bp_hist_pct.csv（只取 E1 通过者的尾窗 HP_WINDOW 条——HP_WEIGHT=0，尾窗与全量等价）
  - v2_holdings_state.json（期初账本：31 只持仓 + cash + entry_dates + nav_peak）

策略侧 E1 筛选在 _score_stocks 运行时仍执行（本脚本预筛只为压缩体积，语义等价：
预筛只用快照静态字段复刻同一组过滤条件，池外代码运行时也会被 continue 掉）。

用法: python fill_v2_snapshot.py [--dry-run] [--with-bp-hist]
  --dry-run       只打印统计与体积估算，不写回 strategy_v2.py
  --with-bp-hist  携带 bp_hist 尾窗（约 +549KB，默认不带）

体积决策（2026-10-16）: HP_WEIGHT=0.0（Z_WEIGHT=1.0），评分公式 = z*1.0 + hp*0.0，
bp_hist 对评分结果贡献恒为 0 —— 默认裁掉（724KB→175KB，粘贴可行）；
若未来把 HP_WEIGHT 调回非零，必须用 --with-bp-hist 重新填充后再部署。
"""
import csv
import json
import os
import sys
import io
import re

DATA_DIR = "D:/QMT_POOL"
HERE = os.path.dirname(os.path.abspath(__file__))
STRATEGY = os.path.join(HERE, "strategy_v2.py")

# E1 宇宙筛选阈值（必须与 strategy_v2.py 常量逐字一致——改了那边必须同步这里）
MARKET_CAP_MIN = 0           # 策略侧 MARKET_CAP_MIN = 0.0（circ_mv > 0）
MARKET_CAP_MAX = 300000      # 万元（30 亿）
PE_TTM_MIN = 0
PB_MIN = 0
QUALITY_BPS_MIN = 0
QUALITY_ROE_MIN = 0
QUALITY_PROFIT_DEDT_MIN = 0

HP_WINDOW = 36               # 策略侧 HP_WINDOW（只消费尾窗；HP_WEIGHT=0 时尾窗与全量评分等价）

FIN_FIELDS = ["pe_ttm", "pb", "circ_mv", "total_mv", "bps", "roe", "profit_dedt"]


def _read_financial():
    fin = {}
    for name in FIN_FIELDS:
        path = os.path.join(DATA_DIR, "financial_%s.csv" % name)
        if not os.path.exists(path):
            continue
        with io.open(path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = row.get("ts_code", "")
                val = row.get("value", "")
                if not code or not val:
                    continue
                if name == "industry":
                    fin.setdefault(code, {})[name] = val
                else:
                    try:
                        fin.setdefault(code, {})[name] = float(val)
                    except Exception:
                        pass
    return fin


def _read_map(path, key, out_fields):
    result = {}
    if not os.path.exists(path):
        return result
    with io.open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = row.get(key, "")
            if not code:
                continue
            if len(out_fields) == 1:
                result[code] = row.get(out_fields[0], "") or ""
            else:
                result[code] = tuple(row.get(x, "") or "" for x in out_fields)
    return result


def _read_st_set():
    result = set()
    path = os.path.join(DATA_DIR, "st_list.csv")
    if not os.path.exists(path):
        return result
    with io.open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("ts_code"):
                result.add(row["ts_code"])
    return result


def _read_bp_hist_tail(keep_codes):
    """bp_hist: {code: [(date, hp_pct), ...] 升序}，只取尾窗 HP_WINDOW 条。"""
    result = {}
    path = os.path.join(DATA_DIR, "bp_hist_pct.csv")
    if not os.path.exists(path):
        return result
    tmp = {}
    with io.open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = row.get("ts_code", "")
            if code not in keep_codes:
                continue
            date = row.get("date", "")
            pct = row.get("hp_pct", "")
            if not (code and date and pct):
                continue
            try:
                tmp.setdefault(code, []).append((date, float(pct)))
            except Exception:
                pass
    for code, lst in tmp.items():
        lst.sort(key=lambda x: x[0])          # 升序（原 CSV 应已有序，双保险）
        if len(lst) > HP_WINDOW:
            lst = lst[-HP_WINDOW:]
        result[code] = lst
    return result


def _e1_pass(d):
    cm = d.get("circ_mv", 0)
    if not (cm and cm > MARKET_CAP_MIN and cm < MARKET_CAP_MAX):
        return False
    pe = d.get("pe_ttm", 0)
    if not (pe and pe > PE_TTM_MIN):
        return False
    pb = d.get("pb", 0)
    if not (pb and pb > PB_MIN):
        return False
    bps = d.get("bps", 0)
    roe = d.get("roe", 0)
    pdt = d.get("profit_dedt", 0)
    if not (bps and bps > QUALITY_BPS_MIN and roe and roe > QUALITY_ROE_MIN
            and pdt and pdt > QUALITY_PROFIT_DEDT_MIN):
        return False
    return True


def build_snapshot(with_bp_hist=False):
    fin = _read_financial()
    ind = _read_map(os.path.join(DATA_DIR, "industry_map.csv"), "ts_code", ["industry"])
    delist = _read_map(os.path.join(DATA_DIR, "delist_info.csv"), "ts_code",
                       ["list_status", "delist_date"])
    st_set = _read_st_set()

    keep = {}
    skipped_st = 0
    for code, d in fin.items():
        if not _e1_pass(d):
            continue
        if code in st_set:
            skipped_st += 1
            continue
        keep[code] = d

    bp_hist = _read_bp_hist_tail(set(keep.keys())) if with_bp_hist else {}

    snap = {}
    for code, d in keep.items():
        rec = {
            "pe_ttm": round(d.get("pe_ttm", 0.0), 4),
            "pb": round(d.get("pb", 0.0), 4),
            "circ_mv": round(d.get("circ_mv", 0.0), 2),
            "total_mv": round(d.get("total_mv", 0.0), 2),
            "bps": round(d.get("bps", 0.0), 4),
            "roe": round(d.get("roe", 0.0), 4),
            "profit_dedt": round(d.get("profit_dedt", 0.0), 4),
            "industry": ind.get(code, ""),
            "is_st": False,
        }
        if code in delist:
            rec["delist"] = [delist[code][0], delist[code][1]]
        if code in bp_hist:
            rec["bp_hist"] = [[dt, round(v, 4)] for dt, v in bp_hist[code]]
        snap[code] = rec

    stats = {
        "financial_codes": len(fin),
        "e1_pass": len([1 for d in fin.values() if _e1_pass(d)]),
        "skipped_st": skipped_st,
        "snapshot_codes": len(snap),
        "bp_hist_codes": sum(1 for r in snap.values() if "bp_hist" in r),
        "industry_missing": sum(1 for r in snap.values() if not r["industry"]),
    }
    return snap, stats


def build_ledger():
    path = os.path.join(DATA_DIR, "v2_holdings_state.json")
    with io.open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    ledger = {
        "account_id": state.get("account_id", ""),
        "asof_date": "2026-10-16",
        "cash_hint": float(state.get("cash", 0.0)),
        "nav_peak": float(state.get("nav_peak", 1.0)),
        "holdings": {},
        "suspended_sells": [str(s) for s in state.get("suspended_sells", [])],
        "last_rebal_month": 10,
    }
    for code, vol in state.get("holdings", {}).items():
        ledger["holdings"][code] = {
            "vol": int(vol),
            "entry_date": state.get("entry_dates", {}).get(code, ""),
        }
    return ledger


def _fmt_snapshot(snap):
    """生成紧凑可读的字面量（数值不带无意义尾零，字符串用 repr）。"""
    lines = ["SNAPSHOT = {"]
    for code in sorted(snap.keys()):
        rec = snap[code]
        parts = []
        for k in ("pe_ttm", "pb", "circ_mv", "total_mv", "bps", "roe", "profit_dedt"):
            parts.append("%r: %r" % (k, rec.get(k)))
        parts.append("%r: %r" % ("industry", rec.get("industry", "")))
        parts.append("%r: %s" % ("is_st", "False"))
        if "delist" in rec:
            parts.append("%r: [%r, %r]" % ("delist", rec["delist"][0], rec["delist"][1]))
        if "bp_hist" in rec:
            pairs = ",".join("[%r,%r]" % (dt, v) for dt, v in rec["bp_hist"])
            parts.append("%r: [%s]" % ("bp_hist", pairs))
        lines.append("    %r: {%s}," % (code, ", ".join(parts)))
    lines.append("}")
    return "\n".join(lines)


def _fmt_ledger(ledger):
    lines = ["PERIOD_LEDGER = {"]
    lines.append("    'account_id': %r," % ledger["account_id"])
    lines.append("    'asof_date': %r," % ledger["asof_date"])
    lines.append("    'cash_hint': %r," % ledger["cash_hint"])
    lines.append("    'nav_peak': %r," % ledger["nav_peak"])
    lines.append("    'last_rebal_month': %r," % ledger["last_rebal_month"])
    lines.append("    'suspended_sells': [%s]," % ",".join(repr(s) for s in ledger["suspended_sells"]))
    lines.append("    'holdings': {")
    for code in sorted(ledger["holdings"].keys()):
        h = ledger["holdings"][code]
        lines.append("        %r: {'vol': %d, 'entry_date': %r}," % (code, h["vol"], h["entry_date"]))
    lines.append("    },")
    lines.append("}")
    return "\n".join(lines)


def inject(src_text, begin_tag, new_body):
    """按标记块替换中间内容，标记行逐字保留。"""
    pat = re.compile(
        re.escape(begin_tag) + r"\n.*?\n" + re.escape(begin_tag.replace("-BEGIN", "-END")),
        re.DOTALL)
    m = pat.search(src_text)
    if not m:
        raise SystemExit("[FILL] 未找到标记块: %s" % begin_tag)
    end_tag = begin_tag.replace("-BEGIN", "-END")
    replacement = begin_tag + "\n" + new_body + "\n" + end_tag
    return src_text[:m.start()] + replacement + src_text[m.end():]


def main():
    dry = "--dry-run" in sys.argv
    with_bp = "--with-bp-hist" in sys.argv
    snap, stats = build_snapshot(with_bp_hist=with_bp)
    ledger = build_ledger()
    snap_txt = _fmt_snapshot(snap)
    led_txt = _fmt_ledger(ledger)
    snap_bytes = len(snap_txt.encode("utf-8"))
    led_bytes = len(led_txt.encode("utf-8"))
    print("[STATS] %s" % json.dumps(stats, ensure_ascii=False))
    print("[STATS] SNAPSHOT 字面量 = %.0f KB, PERIOD_LEDGER = %.1f KB, with_bp_hist=%s"
          % (snap_bytes / 1024.0, led_bytes / 1024.0, with_bp))
    if with_bp:
        print("[STATS] bp_hist 尾窗 %d 条/只（HP_WINDOW=%d）" % (HP_WINDOW, HP_WINDOW))
    else:
        print("[STATS] bp_hist 未携带（HP_WEIGHT=0 对评分恒零贡献；调回非零须 --with-bp-hist 重填）")

    if dry:
        print("[DRY-RUN] 不写回")
        return 0

    with io.open(STRATEGY, "r", encoding="utf-8") as f:
        src = f.read()
    src2 = inject(src, "# --- V2SNAPSHOT-BEGIN ---", snap_txt)
    src2 = inject(src2, "# --- V2LEDGER-BEGIN ---", led_txt)
    with io.open(STRATEGY, "w", encoding="utf-8") as f:
        f.write(src2)
    compile(src2, STRATEGY, "exec")
    print("[FILL] 已写回 %s, 编译通过, 新大小 %.0f KB"
          % (STRATEGY, len(src2.encode("utf-8")) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
