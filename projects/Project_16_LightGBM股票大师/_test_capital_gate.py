# coding: utf-8
"""G2 资金池数据源门回归（2026-10-30 立，审计 §3.4）。

背景：`_roll_g2_capital` 的两个输入都来自已作废的文件桥——
  · `_realized_pnl_from_fills()` 遍历 `state/fills_*.json`（最新 20261013）
  · `_float_pnl_now()` 读 `cmd/positions_cfg_*.json`（最新 20261021）
而 QMT 内置 open() 恒抛 Foribdden FileIO，这两个目录永远不会再有新文件。
于是算出的 capital 会永久停在旧值、当日买入的浮盈永远不计入，
而这个数又是次日建仓预算的唯一依据（`rebalance_g2.py:605` → `G.load_g2_capital()`）。

本条只做「不写」：当日 state 快照不存在就跳过滚动，**不改变计算方式**。
真正的修复（数据源迁到 g2_trade_log.csv / 回环 hub）是架构改动，另行评估。

纯离线：把 STATE_DIR / CMD_DIR / 资金池文件全指到临时目录，不碰真 state、不写真资金池。
"""
import importlib.util
import io
import json
import os
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
fails = []


def check(name, cond):
    if cond:
        print("  OK   " + name)
    else:
        print("  FAIL " + name)
        fails.append(name)


spec = importlib.util.spec_from_file_location("rcg2", os.path.join(HERE, "reconcile_g2.py"))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

G = R.G
QC = R.QC     # ★ CC 复核 §4.8：read_fills 用的是 qmt_bridge_client.STATE_DIR，
              #   与 g2_config.STATE_DIR 是**两个独立模块常量**，必须同时 patch，
              #   否则 _all_fills 列 A 目录、read_fills 读 B 目录 ⇒ K3 变成空断言。
tmp = tempfile.mkdtemp()
state = os.path.join(tmp, "state")
cmd = os.path.join(tmp, "cmd")
os.makedirs(state)
os.makedirs(cmd)
cap_file = os.path.join(tmp, "g2_strategy_capital.json")

orig = (G.STATE_DIR, G.CMD_DIR, G.G2_CAPITAL_FILE, QC.STATE_DIR, QC.CMD_DIR)
G.STATE_DIR = state
G.CMD_DIR = cmd
G.G2_CAPITAL_FILE = cap_file
QC.STATE_DIR = state
QC.CMD_DIR = cmd


def seed_capital(v):
    io.open(cap_file, "w", encoding="utf-8").write(json.dumps(
        {"account_id": G.ACCOUNT_ID, "capital": v, "note": "seed"}, ensure_ascii=False))


def read_capital():
    try:
        return json.load(io.open(cap_file, encoding="utf-8")).get("capital")
    except Exception:
        return None


DATE = "20261030"

try:
    # --- K1 当日 state 快照不存在 ⇒ 必须跳过滚动，资金池原封不动 ---
    seed_capital(93501.0)
    got = R._roll_g2_capital(DATE)
    check("K1 缺当日快照 => 返回 None", got is None)
    check("K1 资金池未被改写", read_capital() == 93501.0)

    # --- K2 只有 positions、没有 fills ⇒ 仍然跳过（两个输入缺一不可）---
    io.open(os.path.join(state, "positions_%s.json" % DATE), "w", encoding="utf-8").write(
        json.dumps({"account_id": G.ACCOUNT_ID, "positions": []}))
    got = R._roll_g2_capital(DATE)
    check("K2 缺 fills 也跳过", got is None and read_capital() == 93501.0)

    # --- K3 两个快照都在 ⇒ 才允许滚动 ---
    io.open(os.path.join(state, "fills_%s.json" % DATE), "w", encoding="utf-8").write(
        json.dumps({"fills": []}))
    io.open(os.path.join(state, "positions_%s.json" % DATE), "w", encoding="utf-8").write(
        json.dumps({"account_id": G.ACCOUNT_ID, "positions": []}))
    got = R._roll_g2_capital(DATE)
    check("K3 快照齐全 => 执行滚动", got is not None)
    check("K3 资金池被更新", read_capital() == round(got, 2))
    check("K3 无历史 fills/持仓 => 等于 START_CAPITAL",
          abs(got - G.START_CAPITAL) < 1e-6)

    # --- K3b ★ 非零路径（CC 复核 §4.8 指出的空断言补丁）---
    #    造一笔真实已实现亏损：BUY 100@10 后 SELL 100@8 ⇒ realized = -200。
    #    这才能证明 `_all_fills` 真的**读到了**文件（而不是「列不到文件 ⇒ 全零 ⇒ 恰好等于初值」）。
    #    字段名照抄 `_realized_pnl_from_fills` 的实际读法：status / action / code / vol / price。
    io.open(os.path.join(state, "fills_20261001.json"), "w", encoding="utf-8").write(json.dumps({
        "fills": [
            {"code": "600000.SH", "action": "BUY", "status": "FILLED",
             "vol": 100, "price": 10.0, "time": "2026-10-01 09:50:00"},
            {"code": "600000.SH", "action": "SELL", "status": "FILLED",
             "vol": 100, "price": 8.0, "time": "2026-10-02 09:50:00"},
        ]}))
    got = R._roll_g2_capital(DATE)
    check("K3b 非零 realized 被真正读到（不是全零空断言）",
          got is not None and abs(got - (G.START_CAPITAL - 200.0)) < 1e-6)
    check("K3b 资金池反映 -200 已实现亏损", read_capital() == round(got, 2))
    # 清理，避免影响后续
    os.remove(os.path.join(state, "fills_20261001.json"))

    # --- K4 门是「不写」而不是「算得更好」：不得引入新的计算分支 ---
    src = io.open(os.path.join(HERE, "reconcile_g2.py"), encoding="utf-8").read()
    i_gate = src.find('if miss:')
    i_calc = src.find("realized = _realized_pnl_from_fills()")
    check("K4 数据源门在计算之前", i_gate != -1 and i_calc != -1 and i_gate < i_calc)
    check("K4 仍然保留原计算式（未改成别的口径）",
          "G.START_CAPITAL + realized + float_pnl" in src)

    # --- K5 ★ 只 patch QC.STATE_DIR（不 patch G.STATE_DIR）也必须生效 ---
    #    CC 复核 §5：早期 _roll_g2_capital 与 main() 查文件用的是 G.STATE_DIR，
    #    与 read_fills 的 QC.STATE_DIR 是两个模块常量。现在统一走 _state_dir()，
    #    因此只 patch 读侧口径就应该完全生效（这条是分叉消除的行为证据）。
    orig_g = G.STATE_DIR
    try:
        G.STATE_DIR = os.path.join(tmp, "wrong_dir")     # 故意指向不存在的目录
        os.makedirs(G.STATE_DIR, exist_ok=True)
        # 当日快照都在 QC 目录里（已由 K3 建好）
        got = R._roll_g2_capital(DATE)
        check("K5 只 patch QC.STATE_DIR 也能看到当日快照（分叉已消除）", got is not None)
        # 反过来：QC 目录清空后必须跳过（证明它看的是 QC 而不是 G）
        for fn in ("positions_%s.json" % DATE, "fills_%s.json" % DATE):
            fp = os.path.join(state, fn)
            if os.path.exists(fp):
                os.remove(fp)
        got2 = R._roll_g2_capital(DATE)
        check("K5 QC 目录清空后正确跳过（不是在看 G 目录）", got2 is None)
    finally:
        G.STATE_DIR = orig_g

finally:
    G.STATE_DIR, G.CMD_DIR, G.G2_CAPITAL_FILE, QC.STATE_DIR, QC.CMD_DIR = orig

print("")
if fails:
    print("FAILED %d: %s" % (len(fails), " | ".join(fails)))
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
