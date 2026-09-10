# coding: utf-8
"""融合（ensemble）大QMT 文件桥 · 外部信号层客户端（外部 Python 3.10）

与 qmt_bridge_client.py（G2）同构，仅目录/策略名不同：
  外部只写  cmd/orders_<date>.json   cmd/cancel_<date>.json
  外部只读  state/fills_<date>.json  state/positions_<date>.json
            state/asset_<date>.json  state/heart_<date>.json
桥目录：D:/QMT_POOL/p16_ensemble_bridge（与 g2_bridge 完全分离，账号同 70180771 虚拟子账户）
"""
import argparse
import json
import os
import sys
import time

# ============================================================
# 桥配置（与 meta.json 保持一致）
# ============================================================
BRIDGE_DIR = "D:/QMT_POOL/p16_ensemble_bridge"
CMD_DIR = os.path.join(BRIDGE_DIR, "cmd")
STATE_DIR = os.path.join(BRIDGE_DIR, "state")
META_FILE = os.path.join(BRIDGE_DIR, "meta.json")

# 默认账号（与 G2 同券商账号，虚拟子账户）；可被 meta.json 覆盖
DEFAULT_ACCOUNT = "70180771"
STRATEGY_NAME = "Project_16_ens"


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
    """读心跳。date 缺省时读 state 目录最新 heart_<date>.json（桥每天写新心跳，
    跨日运行时"数据日期(date)"≠"桥运行日"，存活判断应看最新心跳，避免误判桥死）。"""
    if not date:
        try:
            files = [f for f in os.listdir(STATE_DIR) if f.startswith("heart_") and f.endswith(".json")]
            if files:
                files.sort(reverse=True)  # YYYYMMDD 字典序 = 日期序
                return _read_json(os.path.join(STATE_DIR, files[0]))
        except Exception:
            pass
        return None
    return _read_json(_heart_path(date))


def notify_feishu(text):
    """融合飞书文本通知（私聊，bot 身份 + 清理 hermes/agent 环境变量回退本地 config）。
    供 rebalance/reconcile 等全自动任务复用；失败仅记录不阻断主流程。"""
    import subprocess

    import g2_ens_config as G
    cli = getattr(G, "LARK_CLI", "")
    uid = getattr(G, "FEISHU_OPEN_ID", "")
    if not cli or not uid:
        print("[通知] 未配置 LARK_CLI/FEISHU_OPEN_ID，跳过")
        return False
    env = dict(os.environ)
    env.pop("LARKSUITE_CLI_APP_ID", None)
    env.pop("LARKSUITE_CLI_USER_ACCESS_TOKEN", None)
    env.pop("HERMES_HOME", None)
    env.pop("OPENCLAW_HOME", None)
    env.pop("LARK_CHANNEL", None)
    env["LARKSUITE_CLI_STRICT_MODE"] = "off"
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    try:
        r = subprocess.run(
            [cli, "im", "+messages-send", "--user-id", uid,
             "--msg-type", "text", "--text", text, "--as", "bot"],
            capture_output=True, text=True, encoding="utf-8", timeout=15, env=env)
        ok = r.returncode == 0 and '"ok": true' in r.stdout
        print("[通知] 飞书%s: %s" % ("成功" if ok else "失败", (r.stdout or r.stderr).strip()[:120]))
        return ok
    except Exception as e:
        print("[通知] 飞书异常: %s" % e)
        return False


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
# 从融合选股 CSV 生成指令
# ============================================================
def build_orders_from_ens(date, top_k=2, capital=100000.0, reserve_pct=0.05, account_id=None):
    """读 data/selections/g2_ens/<date>_g2_top<top>.csv 生成 BUY orders（等权，整手）。

    返回 (orders, note)。orders 每项含 action/code/vol/price/reason/strategy_order_id。
    若预选池无票或取价失败，返回空列表并注明原因。
    """
    proj = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
    csv_path = os.path.join(proj, "data", "selections", "g2_ens", "%s_g2_top%d.csv" % (date, top_k))
    if not os.path.exists(csv_path):
        return [], "融合选股 CSV 不存在: %s" % csv_path

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

    picks.sort(key=lambda p: p["total"], reverse=True)
    picks = picks[:top_k]

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
            "reason": "融合选股Top%d(total=%.1f)" % (i + 1, p["total"]),
            "strategy_order_id": "P16_%s_%04d" % (date, seq0 + i + 1),
        })
    return orders, "融合选股 %d 只，等权预算 %.0f/只" % (len(picks), per_budget)


# ============================================================
# CLI
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="融合大QMT 文件桥外部客户端")
    sub = ap.add_subparsers(dest="cmd")

    p_build = sub.add_parser("build", help="从融合选股 CSV 生成指令（--live 写桥）")
    p_build.add_argument("--date", default=_today())
    p_build.add_argument("--top", type=int, default=2)
    p_build.add_argument("--capital", type=float, default=100000.0)
    p_build.add_argument("--live", action="store_true", help="真写 cmd（缺省 dry-run）")
    p_build.add_argument("--account", default=None)
    p_build.set_defaults(func=cli_build)

    p_alive = sub.add_parser("alive", help="检查内置桥存活")
    p_alive.add_argument("--max-age", type=int, default=300)
    p_alive.set_defaults(func=cli_alive)

    args = ap.parse_args()
    if not hasattr(args, "func"):
        ap.print_help()
        return 1
    return args.func(args)


def cli_build(args):
    orders, note = build_orders_from_ens(args.date, top_k=args.top, capital=args.capital,
                                         account_id=args.account)
    print(note)
    if not orders:
        return 0
    for o in orders:
        print("  %s %s %d股@%.3f %s" % (o["action"], o["code"], o["vol"], o["price"], o["reason"]))
    if args.live:
        seq = write_orders(orders, date=args.date, account_id=args.account or _default_account())
        print("已写 cmd/orders_%s.json seq=%d（%d 条）" % (args.date, seq, len(orders)))
    return 0


def cli_alive(args):
    alive, msg = is_bridge_alive(max_age=args.max_age)
    print("桥存活: %s | %s" % (alive, msg))
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
