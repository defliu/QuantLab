# coding: utf-8
"""P13 黄氏529 本地冒烟验证（T6）

验证项：
  1) 真实信号 CSV 读取通（真实 D:/QMT_POOL，只读，最新 T-1 日）
  2) 候选数合理（合成 16 只 -> 补槽 12 只、1/12 上限）
  3) fail-open 生效（信号目录缺失 -> 空列表、当日不买入）
  4) 配置加载后路径仍锁在沙箱内（防生产路径回逃）
  5) 真实目录零写入守卫（测试前后快照对比，生产目录被碰即 FAIL）
  6) 不实盘下单（passorder 全程 mock，仅记录参数不触网）

【沙箱红线】本脚本会真实调用 _decision()/_save_holdings()/_log()/_log_trade()，
这些函数会写文件。2026-09-27 教训：早期版本只隔离了 SIGNAL_DIR，
导致合成持仓/交易日志/策略日志写进 D:/QMT_POOL/config（600000~600011 污染）。
现改为「全路径隔离 + 生产目录守卫」，任何新写入点必须先加进 _sandbox_paths()。

若 xtquant 可用且 QMT mini 客户端在跑 -> 额外用 LocalContext 做真实行情限价判定；
否则用 NullC 行情桩（行情不可得 -> 策略 fail-open，符合设计）。

用法:  D:/Python311/python.exe local_validate.py
  或  <miniqmt venv>/python.exe local_validate.py
"""
import importlib.util
import atexit
import os
import shutil
import sys
import tempfile
import time

PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
QUANTLAB = os.path.dirname(os.path.dirname(PROJ_DIR))
sys.path.insert(0, PROJ_DIR)
sys.path.insert(0, QUANTLAB)

from broker.local_context import load_strategy_source

BUILD = os.path.join(PROJ_DIR, "build", "strategy_huang529.py")
REAL_POOL = "D:/QMT_POOL"
REAL_CONFIG_DIR = "D:/QMT_POOL/config"
REAL_CFG_YAML = os.path.join(REAL_CONFIG_DIR, "p13_huang529_config.yaml")

# ============ Mock 下单（不触网） ============
ORDERS = []


def mock_passorder(*args):
    ORDERS.append(args)
    return 0


def mock_get_trade_detail_data(acct, typ, kind):
    return []


# ============ 生产目录守卫（只读快照） ============
GUARD_DIRS = [REAL_POOL, REAL_CONFIG_DIR]


def _snapshot(dirs):
    """采集目录下所有文件的 (path, mtime, size)，用于测试前后比对。"""
    snap = {}
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            snap[p] = (st.st_mtime, st.st_size)
    return snap


def _diff_snap(before, after):
    """返回被修改/新增/删除的文件列表。"""
    changed = []
    for p, v in after.items():
        if p not in before:
            changed.append("NEW      " + p)
        elif before[p] != v:
            changed.append("MODIFIED " + p)
    for p in before:
        if p not in after:
            changed.append("DELETED  " + p)
    return sorted(changed)


# ============ 沙箱构建 ============
def _rebase_yaml(src, dst, sig_dir, state_dir):
    """把真实 yaml 复制进沙箱，并把绝对路径重定位到沙箱。

    必要性：_load_config() 会用 yaml 里的 pool.* 绝对路径覆盖模块级
    HOLDINGS_FILE/NAV_FILE/TRADE_LOG_FILE，若不重定位，加载配置那一刻
    状态文件路径就会被打回生产目录 —— 这正是历史污染的第二个成因。
    """
    state_dir = state_dir.replace("\\", "/")
    with open(src, "r", encoding="utf-8") as f:
        lines = f.readlines()
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith("signal_dir:"):
            out.append('  signal_dir: "%s"\n' % sig_dir.replace("\\", "/"))
        elif s.startswith("holdings_file:"):
            out.append('  holdings_file: "%s/p13_huang529_holdings.json"\n' % state_dir)
        elif s.startswith("nav_file:"):
            out.append('  nav_file: "%s/p13_huang529_nav.json"\n' % state_dir)
        elif s.startswith("trade_log_file:"):
            out.append('  trade_log_file: "%s/p13_huang529_trade_log.csv"\n' % state_dir)
        else:
            out.append(line)
    with open(dst, "w", encoding="utf-8") as f:
        f.writelines(out)
    return dst


def _sandbox_paths(mod, sandbox):
    """把所有会写盘的模块级路径常量重定向到沙箱。返回 (sig_dir, state_dir)。"""
    sig_dir = os.path.join(sandbox, "signal")
    state_dir = os.path.join(sandbox, "state")
    os.makedirs(sig_dir)
    os.makedirs(state_dir)

    mod.SIGNAL_DIR = sig_dir
    mod.DATA_DIR = state_dir
    mod.HOLDINGS_FILE = os.path.join(state_dir, "p13_huang529_holdings.json")
    mod.NAV_FILE = os.path.join(state_dir, "p13_huang529_nav.json")
    mod.TRADE_LOG_FILE = os.path.join(state_dir, "p13_huang529_trade_log.csv")
    return sig_dir, state_dir


def _norm(p):
    """路径归一化：统一分隔符 + 去掉尾部分隔符，避免 / 与 \\ 造成的假逃逸。"""
    return str(p).replace("\\", "/").rstrip("/")


def _assert_sandboxed(mod, sig_dir, state_dir, tag):
    """断言所有写盘路径都在沙箱内（比较前做分隔符归一化）。"""
    state_n = _norm(state_dir)
    for name in ("HOLDINGS_FILE", "NAV_FILE", "TRADE_LOG_FILE"):
        p = getattr(mod, name, "")
        assert _norm(p).startswith(state_n), "%s %s 逃出沙箱: %s" % (tag, name, p)
    assert _norm(mod.DATA_DIR) == state_n, "%s DATA_DIR 逃出沙箱: %s" % (tag, mod.DATA_DIR)
    assert _norm(mod.SIGNAL_DIR) == _norm(sig_dir), "%s SIGNAL_DIR 逃出沙箱: %s" % (tag, mod.SIGNAL_DIR)


# ============ C 桩 ============
class NullC(object):
    """行情桩：可注入 price_map；取不到行情返回空 -> 策略 fail-open。"""

    def __init__(self):
        self.prices = {}

    def get_current_time(self):
        from datetime import datetime
        return datetime.now()

    def get_market_data_ex(self, fields, codes, period="1d"):
        import pandas as pd
        out = {}
        if isinstance(codes, str):
            codes = [codes]
        for c in codes:
            p = self.prices.get(c)
            if p is None:
                out[c] = pd.DataFrame()
                continue
            d = {}
            for f in fields:
                if f == "close":
                    d[f] = [p]
                elif f == "pre_close":
                    d[f] = [p]
                elif f == "open":
                    d[f] = [p]
                elif f == "volume":
                    d[f] = [1000]
                else:
                    d[f] = [0.0]
            out[c] = pd.DataFrame(d)
        return out

    def get_tick_timetag(self):
        return 0

    def get_bar_timetag(self, bp):
        return 0


def main():
    t0 = time.time()
    print("=== P13 黄氏529 本地冒烟验证（T6）===")

    # 生产目录基线快照（全程只读）
    guard_before = _snapshot(GUARD_DIRS)

    # 0) 加载构建产物（GBK 单文件 -> UTF-8）
    mod = load_strategy_source(BUILD)
    mod.passorder = mock_passorder
    mod.get_trade_detail_data = mock_get_trade_detail_data
    print("BUILD_TAG =", mod.BUILD_TAG)
    print("N_HOLD=%d MAX_SINGLE_PCT=%.4f STOP_LOSS=%.2f MAX_HOLDING_DAYS=%d trail=%s" % (
        mod.N_HOLD, mod.MAX_SINGLE_PCT, mod.STOP_LOSS, mod.MAX_HOLDING_DAYS, mod.TRAILING_STOP))

    # 1) 先建沙箱并全路径隔离 —— 必须早于任何策略函数调用。
    #    教训：_load_signal_csv() 在 fail-open 分支会调 _log()，而 _log() 写 DATA_DIR。
    #    若先探测真实池再建沙箱，那次 _log 就会写进生产目录（守卫第 2 次实测抓到）。
    sandbox = tempfile.mkdtemp(prefix="p13_smoke_")
    # 失败路径（断言/异常）也必须清理沙箱，否则 Temp 里会堆积 p13_smoke_* 垃圾目录
    atexit.register(shutil.rmtree, sandbox, True)
    sig_dir, state_dir = _sandbox_paths(mod, sandbox)
    print("\n[0] 沙箱 = %s" % sandbox)
    print("    信号目录 -> %s" % sig_dir)
    print("    状态目录 -> %s" % state_dir)
    _assert_sandboxed(mod, sig_dir, state_dir, "初始")

    # 1.5) 真实信号池只读探测
    #   必须临时把 SIGNAL_DIR 指向生产信号池，否则读的是空沙箱目录（覆盖缺口）。
    #   DATA_DIR 保持沙箱：_load_signal_csv() 在 fail-open 分支会调 _log() 写盘。
    #   try/finally 保证探测后必然恢复沙箱，防止后续步骤读到生产信号。
    print("\n[1] 真实信号池只读探测（只读 %s）" % REAL_POOL)
    _sandbox_sig = mod.SIGNAL_DIR
    mod.SIGNAL_DIR = REAL_POOL
    try:
        real_sig_date = mod._latest_signal_date()
        real_sig_n = len(mod._load_signal_csv())
    finally:
        mod.SIGNAL_DIR = _sandbox_sig
    print("    最新日=%s 候选=%d" % (real_sig_date, real_sig_n))
    _assert_sandboxed(mod, sig_dir, state_dir, "真实池探测后")

    # 2) 配置加载后路径仍锁在沙箱（防 yaml 绝对路径把状态打回生产）
    print("\n[2] 配置加载路径反逃逸检查")
    if os.path.exists(REAL_CFG_YAML):
        _rebase_yaml(REAL_CFG_YAML, os.path.join(state_dir, "p13_huang529_config.yaml"),
                     sig_dir, state_dir)
        mod._load_config()
        _assert_sandboxed(mod, sig_dir, state_dir, "配置加载后")
        print("    yaml 已重定位并加载，路径仍锁在沙箱  OK")
    else:
        print("    [SKIP] 真实 yaml 不存在（%s），仅验证内置默认值" % REAL_CFG_YAML)
        _assert_sandboxed(mod, sig_dir, state_dir, "默认配置")

    # 3) 合成候选：验证补槽 12 只 + 1/12 上限 + 不实盘下单
    print("\n[3] 合成 16 只信号 -> 补槽 12 只（1/12 上限）")
    with open(os.path.join(sig_dir, mod.SIGNAL_PREFIX + "20260817.csv"), "w", encoding="utf-8") as f:
        f.write("ts_code,atr_pct,rank\n")
        for i in range(16):
            f.write("600%03d.SH,%.1f,%d\n" % (i, 2.0 + i * 0.5, i + 1))
    C = NullC()
    for i in range(16):
        C.prices["600%03d.SH" % i] = 10.0
    ORDERS[:] = []
    mod._cash = mod.CAPITAL_BASE
    mod._holdings = {}
    mod._pending_orders = {}
    mod._today_orders = {}
    mod._suspended_sells = []
    mod._last_decision_date = ""
    mod._decision(C, C.get_current_time())
    n_buy = sum(1 for o in ORDERS if o[0] == 23)
    print("    买入委托 = %d（期望 12），持仓 = %d" % (n_buy, len(mod._holdings)))
    assert n_buy == 12, "补槽数量不对: %d" % n_buy
    for code, info in mod._holdings.items():
        assert info["volume"] * 10.0 <= mod.CAPITAL_BASE / 12.0 + 1, "单票超限: %s" % code
    print("    单票市值 <= 总资产/12  OK")

    # 4) 状态落盘必须只在沙箱
    print("\n[4] 状态落盘位置检查")
    mod._save_holdings()
    mod._log("[T6 sandbox] state write probe")
    mod._log_trade("买入", "600000.SH", 10.0, 100, "sandbox_probe")
    files = sorted(os.listdir(state_dir))
    print("    沙箱 state 目录产出: %s" % files)
    for expect in ("p13_huang529_holdings.json", "p13_huang529_trade_log.csv",
                   "strategy_log_p13_huang529.txt"):
        assert expect in files, "沙箱内未产出 %s（写盘未走沙箱）" % expect
    _assert_sandboxed(mod, sig_dir, state_dir, "_save_holdings 后")
    print("    holdings / trade_log / strategy_log 三路写盘均落在沙箱  OK")

    # 5) fail-open：信号目录缺失
    print("\n[5] fail-open：信号目录缺失")
    missing_dir = os.path.join(sandbox, "no_such_dir_xyz")
    mod.SIGNAL_DIR = missing_dir
    sigs = mod._load_signal_csv()
    print("    返回 =", sigs)
    assert sigs == [], "fail-open 应返回空列表"
    mod.SIGNAL_DIR = sig_dir
    _assert_sandboxed(mod, sig_dir, state_dir, "fail-open 后")
    print("    OK")

    # 6) 若 xtquant 可用且 miniQMT 在跑，做真实行情限价判定（可选）
    try:
        import xtquant  # noqa
        from broker.local_context import connect_data, LocalContext
        connect_data()
        lc = LocalContext()
        codes = ["600000.SH", "000001.SZ"]
        quotes = lc.get_market_data_ex(stock_code=codes, period="1d", count=1)
        ok = sum(1 for c in codes if quotes.get(c) is not None and len(quotes.get(c)) > 0)
        print("\n[6] xtquant 实时行情可用：%d/%d 有行情" % (ok, len(codes)))
    except Exception as e:
        print("\n[6] xtquant/miniQMT 不可用（跳过实时行情判定）: %s" % e)

    # 8) 生产目录守卫：本次运行不得新增/修改/删除任何生产文件
    print("\n[7] 生产目录零写入守卫")
    guard_after = _snapshot(GUARD_DIRS)
    changed = _diff_snap(guard_before, guard_after)
    if changed:
        for line in changed:
            print("    !! %s" % line)
        raise AssertionError("生产目录被改动，测试污染！")
    print("    %s 全部未变动  OK" % ", ".join(GUARD_DIRS))

    shutil.rmtree(sandbox, ignore_errors=True)
    print("\n=== T6 冒烟通过（%.1fs），全程未触发真实下单（ORDERS=%d 条 mock 记录），生产目录零写入 ===" % (
        time.time() - t0, len(ORDERS)))


if __name__ == "__main__":
    main()
