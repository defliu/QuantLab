# coding: utf-8
"""g2 大QMT 文件桥 · 外部信号层客户端（外部 Python 3.10）

职责（替代 miniQMT xttrader 下单链路的 xtquant 部分）：
  信号层（g2 选股 / rebalance 计划）在外部算好买卖指令，
  通过文件桥交给大QMT 内置桥执行：
    外部只写  cmd/orders_<date>.json   cmd/cancel_<date>.json
    外部只读  state/fills_<date>.json  state/positions_<date>.json
              state/asset_<date>.json  state/heart_<date>.json

用法：
  # 1) 从 g2 选股 CSV 生成当日指令并写桥（推荐）
  python qmt_bridge_client.py build --date 20260831 --top 2 --capital 100000 [--live]
  #     --live 才真正写 cmd；缺省仅打印计划（dry-run）
  # 2) 等待某笔成交回写
  python qmt_bridge_client.py wait --order-id P16_20260831_0001 --timeout 300
  # 3) 检查内置桥存活
  python qmt_bridge_client.py alive

依赖：pyarrow 读取 g2 选股 CSV（外部环境自带）；取价优先本机 xtdata，失败降级腾讯 qt.gtimg.cn。
"""
import argparse
import json
import os
import sys
import time

# ============================================================
# 桥配置（与 meta.json 保持一致）
# ============================================================
BRIDGE_DIR = "D:/QMT_POOL/g2_bridge"
CMD_DIR = os.path.join(BRIDGE_DIR, "cmd")
STATE_DIR = os.path.join(BRIDGE_DIR, "state")
META_FILE = os.path.join(BRIDGE_DIR, "meta.json")

# 默认账号（V2.0 大QMT 新账号）；可被 meta.json 覆盖
DEFAULT_ACCOUNT = "70180771"
STRATEGY_NAME = "Project_16_g2"


def _default_account():
    try:
        with open(META_FILE, encoding="utf-8") as f:
            m = json.load(f)
        return str(m.get("account_id", DEFAULT_ACCOUNT))
    except Exception:
        return DEFAULT_ACCOUNT


def _today():
    return time.strftime("%Y%m%d")


# ============================================================
# 原子写 / 读
# ============================================================
def _atomic_write_json(path, data):
    """临时文件 + rename，避免内置桥读到半个 JSON。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _cmd_path(date):
    return os.path.join(CMD_DIR, "orders_%s.json" % date)


def _cancel_path(date):
    return os.path.join(CMD_DIR, "cancel_%s.json" % date)


def _positions_cfg_path(date):
    return os.path.join(CMD_DIR, "positions_cfg_%s.json" % date)


def _fills_path(date):
    return os.path.join(STATE_DIR, "fills_%s.json" % date)


def _positions_path(date):
    return os.path.join(STATE_DIR, "positions_%s.json" % date)


def _asset_path(date):
    return os.path.join(STATE_DIR, "asset_%s.json" % date)


def _heart_path(date):
    return os.path.join(STATE_DIR, "heart_%s.json" % date)


# ============================================================
# seq 管理（幂等）
# ============================================================
def next_seq(date):
    """计算下一指令 seq：现有 orders 文件最大 seq + 1；若内置桥已处理更大 seq 则跟随。"""
    cur = 0
    data = _read_json(_cmd_path(date))
    if data:
        cur = int(data.get("seq", 0) or 0)
    heart = _read_json(_heart_path(date))
    if heart:
        cur = max(cur, int(heart.get("last_cmd_seq_processed", 0) or 0))
    return cur + 1


# ============================================================
# 写指令（外部 → 内置）
# ============================================================
def write_orders(orders, date=None, account_id=None):
    """写 cmd/orders_<date>.json。orders: [{action, code, vol, price, reason, strategy_order_id}, ...]"""
    date = date or _today()
    account_id = account_id or _default_account()
    seq = next_seq(date)
    os.makedirs(CMD_DIR, exist_ok=True)
    payload = {
        "account_id": account_id,
        "strategy": STRATEGY_NAME,
        "date": date,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seq": seq,
        "orders": orders,
    }
    _atomic_write_json(_cmd_path(date), payload)
    return seq


def write_cancels(cancels, date=None, account_id=None):
    """写 cmd/cancel_<date>.json。cancels: [{strategy_order_id, code, reason}, ...]"""
    date = date or _today()
    account_id = account_id or _default_account()
    seq = next_seq(date)
    os.makedirs(CMD_DIR, exist_ok=True)
    payload = {
        "account_id": account_id,
        "strategy": STRATEGY_NAME,
        "date": date,
        "seq": seq,
        "cancels": cancels,
    }
    _atomic_write_json(_cancel_path(date), payload)
    return seq


def positions_from_fills(date=None):
    """从 state/fills_<date>.json 用 FIFO 推导持仓与成本（供写成本表给内置止损锚定）。

    返回 {code(bridge格式): {"cost": 含费前成交均价, "vol": 净持仓}}。
    code 已统一为桥协议格式（600522.SH）；仅统计 BUY/SELL 成交（status=FILLED/PARTIAL_FILLED）。
    """
    date = date or _today()
    fills = read_fills(date)
    buys = {}   # code -> deque[(vol, cost)]
    vols = {}
    if not fills:
        return {}
    from collections import deque
    for f in fills.get("fills", []):
        status = str(f.get("status", ""))
        if status not in ("FILLED", "PARTIAL_FILLED"):
            continue
        code = str(f.get("code", "") or "")
        action = str(f.get("action", "") or "").upper()
        if not code or action not in ("BUY", "SELL"):
            continue
        try:
            vol = int(float(f.get("vol", 0) or 0))
            price = float(f.get("price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if vol <= 0 or price <= 0:
            continue
        if action == "BUY":
            if code not in buys:
                buys[code] = deque()
            buys[code].append((vol, price))
            vols[code] = vols.get(code, 0) + vol
        else:
            sv = vol
            if code in buys:
                while sv > 0 and buys[code]:
                    v, cp = buys[code][0]
                    take = min(v, sv)
                    sv -= take
                    buys[code][0] = (v - take, cp)
                    if buys[code][0][0] <= 0:
                        buys[code].popleft()
            vols[code] = vols.get(code, 0) - vol
    out = {}
    for code, dq in buys.items():
        tv = sum(v for v, _ in dq)
        if tv > 0 and vols.get(code, 0) > 0:
            tc = sum(v * c for v, c in dq)
            out[code] = {"cost": tc / tv, "vol": vols[code]}
    return out


def write_positions_cfg(positions, date=None, account_id=None):
    """写 cmd/positions_cfg_<date>.json（外部每日写桥，内置止损成本锚）。
    positions: {code(bridge格式): cost} 或 {code: {"cost":x,"vol":n}} 或 [{"code","cost","vol"}, ...]。"""
    date = date or _today()
    account_id = account_id or _default_account()
    os.makedirs(CMD_DIR, exist_ok=True)
    rows = []
    if isinstance(positions, dict):
        for code, v in positions.items():
            if isinstance(v, dict):
                rows.append({"code": code, "cost": round(float(v.get("cost", 0) or 0), 4),
                             "vol": int(v.get("vol", 0) or 0)})
            else:
                rows.append({"code": code, "cost": round(float(v or 0), 4), "vol": 0})
    else:
        for p in positions:
            rows.append({"code": str(p.get("code", "") or ""),
                         "cost": round(float(p.get("cost", 0) or 0), 4),
                         "vol": int(p.get("vol", 0) or 0)})
    rows = [r for r in rows if r["code"] and r["cost"] > 0]
    payload = {
        "account_id": account_id,
        "strategy": STRATEGY_NAME,
        "date": date,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "positions": rows,
    }
    _atomic_write_json(_positions_cfg_path(date), payload)
    return rows


# ============================================================
# 读状态（内置 → 外部）
# ============================================================
def read_fills(date=None):
    date = date or _today()
    return _read_json(_fills_path(date))


def read_positions(date=None):
    date = date or _today()
    return _read_json(_positions_path(date))


def read_asset(date=None):
    date = date or _today()
    return _read_json(_asset_path(date))


def read_heart(date=None):
    date = date or _today()
    return _read_json(_heart_path(date))


def wait_fill(strategy_order_id, timeout=300, poll=2, date=None):
    """轮询 fills 直到该 strategy_order_id 成交/失败/超时。返回 fill dict 或 None。"""
    date = date or _today()
    deadline = time.time() + timeout
    while time.time() < deadline:
        fills = read_fills(date)
        if fills:
            for f in fills.get("fills", []):
                if str(f.get("strategy_order_id", "")) == strategy_order_id:
                    status = str(f.get("status", ""))
                    if status in ("FILLED", "PARTIAL_FILLED"):
                        return f
                    if status in ("CANCELED", "REJECTED", "LIMIT_SKIP", "ABANDONED"):
                        return f
        time.sleep(poll)
    return None


def is_bridge_alive(max_age=300, date=None):
    """内置桥心跳检查：last_heartbeat 距今 < max_age 秒 视为存活。"""
    heart = read_heart(date)
    if not heart:
        return False, "无心跳文件"
    ts = str(heart.get("last_heartbeat", ""))
    try:
        import datetime
        last = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        age = (datetime.datetime.now() - last).total_seconds()
    except Exception:
        return False, "心跳时间解析失败: %s" % ts
    if age > max_age:
        return False, "心跳过期 %.0f 秒前" % age
    return True, "心跳正常(%.0fs前, build=%s, pending=%s)" % (
        age, heart.get("build_tag", "?"), heart.get("pending_count", 0))


# ============================================================
# 取价（外部算 vol 用）：xtdata 优先，降级腾讯
# ============================================================
def fetch_price(code):
    """取最新价。失败返回 None。code 形如 600522.SH。"""
    # 1) 本机 xtdata（miniQMT 在跑时可用）
    try:
        sys.path.append(r"D:\国金QMT交易端模拟\bin.x64\Lib\site-packages")
        from xtquant import xtdata
        xtdata.subscribe_quote(code, period="tick", count=-1)
        time.sleep(0.3)
        tick = xtdata.get_full_tick([code]).get(code)
        if tick:
            p = float(tick.get("lastPrice", 0) or 0)
            if p > 0:
                return p
    except Exception:
        pass
    # 2) 腾讯 qt.gtimg.cn（HTTP 直连）
    try:
        import urllib.request
        sym = code.split(".")[0]
        ex = code.split(".")[1].lower()
        url = "http://qt.gtimg.cn/q=%s%s" % (ex, sym)  # sh600522 / sz000001
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=5).read().decode("gbk", errors="ignore")
        if "~" in raw:
            fields = raw.split("~")
            p = float(fields[3] if len(fields) > 3 else 0)
            return p if p > 0 else None
    except Exception:
        pass
    return None


# ============================================================
# 从 g2 选股 CSV 生成指令
# ============================================================
def build_orders_from_g2(date, top_k=2, capital=100000.0, reserve_pct=0.05, account_id=None):
    """读 data/selections/g2/<date>_g2_top<top>.csv 生成 BUY orders（等权，整手）。

    返回 (orders, note)。orders 每项含 action/code/vol/price/reason/strategy_order_id。
    若预选池无票或取价失败，返回空列表并注明原因。
    """
    # 读选股 CSV
    proj = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
    csv_path = os.path.join(proj, "data", "selections", "g2", "%s_g2_top%d.csv" % (date, top_k))
    if not os.path.exists(csv_path):
        # 回退：尝试完整版清单
        alt = os.path.join(proj, "data", "selections", "%s_selection_full.csv" % date)
        if os.path.exists(alt):
            csv_path = alt
        else:
            return [], "g2 选股 CSV 不存在: %s" % csv_path

    import csv as _csv
    picks = []
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in _csv.DictReader(f):
            code = (row.get("ts_code") or "").strip()
            if not code:
                continue
            try:
                total = float(row.get("total_new", row.get("total", 0) or 0))
            except ValueError:
                total = 0.0
            picks.append({"code": code, "total": total})
    if not picks:
        return [], "选股 CSV 为空"

    # 按总分排序（若 CSV 已排好则保持）
    picks.sort(key=lambda p: p["total"], reverse=True)
    picks = picks[:top_k]

    # 取价 + 算 vol（等权）
    account_id = account_id or _default_account()
    investable = capital * (1 - reserve_pct)
    per_budget = investable / max(len(picks), 1)
    orders = []
    seq0 = next_seq(date)
    for i, p in enumerate(picks):
        price = fetch_price(p["code"])
        if not price or price <= 0:
            orders.append({
                "action": "BUY", "code": p["code"], "vol": 0, "price": 0.0,
                "reason": "取价失败跳过(%s)" % p["code"],
                "strategy_order_id": "P16_%s_%04d" % (date, seq0 + i + 1),
            })
            continue
        vol = int(per_budget / price / 100) * 100
        if vol <= 0:
            orders.append({
                "action": "BUY", "code": p["code"], "vol": 0, "price": round(price, 3),
                "reason": "资金不足一手(%s @ %.2f)" % (p["code"], price),
                "strategy_order_id": "P16_%s_%04d" % (date, seq0 + i + 1),
            })
            continue
        orders.append({
            "action": "BUY", "code": p["code"], "vol": vol, "price": round(price, 3),
            "reason": "g2选股Top%d(total=%.1f)" % (i + 1, p["total"]),
            "strategy_order_id": "P16_%s_%04d" % (date, seq0 + i + 1),
        })
    return orders, "g2 选股 %d 只，等权预算 %.0f/只" % (len(picks), per_budget)


# ============================================================
# CLI
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="g2 大QMT 文件桥外部客户端")
    sub = ap.add_subparsers(dest="cmd")

    p_build = sub.add_parser("build", help="从 g2 选股 CSV 生成指令（--live 写桥）")
    p_build.add_argument("--date", default=_today())
    p_build.add_argument("--top", type=int, default=2)
    p_build.add_argument("--capital", type=float, default=100000.0)
    p_build.add_argument("--live", action="store_true", help="真正写 cmd（缺省 dry-run）")

    p_wait = sub.add_parser("wait", help="等待某笔成交回写")
    p_wait.add_argument("--order-id", required=True)
    p_wait.add_argument("--timeout", type=int, default=300)
    p_wait.add_argument("--date", default=None)

    p_alive = sub.add_parser("alive", help="检查内置桥心跳")

    p_cfg = sub.add_parser("write-cfg", help="写当日成本表（内置止损成本锚）")
    p_cfg.add_argument("--date", default=None)
    p_cfg.add_argument("--from-fills", action="store_true", help="从 fills FIFO 推导持仓成本")
    p_cfg.add_argument("--positions", default=None, help='手动指定 "code:cost[:vol],..."')

    args = ap.parse_args()
    if args.cmd == "build":
        orders, note = build_orders_from_g2(args.date, args.top, args.capital)
        print("== 桥指令计划 ==")
        print(note)
        for o in orders:
            print("  %s %s %s %5d股 @%.2f %s" % (
                o["action"], o["code"], o["strategy_order_id"], o["vol"], o["price"], o["reason"]))
        if args.live:
            seq = write_orders(orders, date=args.date)
            print("已写入 cmd/orders_%s.json seq=%d" % (args.date, seq))
        else:
            print("DRY-RUN：未写桥。确认后加 --live。")
    elif args.cmd == "wait":
        f = wait_fill(args.order_id, args.timeout, date=args.date)
        if f:
            print("成交/终态回写:", json.dumps(f, ensure_ascii=False))
        else:
            print("超时未回写: %s" % args.order_id)
    elif args.cmd == "alive":
        ok, msg = is_bridge_alive()
        print("桥存活: %s | %s" % (ok, msg))
    elif args.cmd == "write-cfg":
        positions = {}
        if args.from_fills:
            positions = positions_from_fills(args.date)
            print("== 从 fills 推导持仓 %d 只 ==" % len(positions))
            for code, v in positions.items():
                print("  %s cost=%.4f vol=%d" % (code, v["cost"], v["vol"]))
        elif args.positions:
            for item in args.positions.split(","):
                parts = item.strip().split(":")
                if len(parts) >= 2:
                    code = parts[0]
                    positions[code] = {"cost": float(parts[1]), "vol": int(float(parts[2])) if len(parts) >= 3 and parts[2] else 0}
        else:
            print("!! 需要 --from-fills 或 --positions")
            return
        rows = write_positions_cfg(positions, date=args.date)
        print("已写 cmd/positions_cfg_%s.json（%d 条）" % ((args.date or _today()), len(rows)))
        for r in rows:
            print("  %s cost=%.4f vol=%d" % (r["code"], r["cost"], r["vol"]))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
