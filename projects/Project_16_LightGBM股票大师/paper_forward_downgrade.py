# -*- coding: utf-8 -*-
"""纸面前向自动降级闸（2026-09-13 立，T-20260913-001 P1-8）。

任一策略纸面臂样本外持续为负 → 自动把该策略 rebalance 降为「只卖不买」（冻结加仓）+ 飞书告警。
人工复盘后 `--unfreeze` 解除。设计文档：`D:/QuantLab/data/纸面自动降级闸_设计_20260913.md`。

判定（保守，防误触发）：
  - 已到期样本 n >= 30（rank<=2 实盘 TOP2 口径）
  - 样本外超额均值 mean_excess < 0
  - mean_excess < -0.0002（-0.02pp/日 幅度阈值，防微负抖动）

超额口径：个股 open→open 复权收益 − 同窗口全市场等权基准（与 forward_stats / paper_forward_ab_stats v3 同源）。

用法：
  python paper_forward_downgrade.py               # 只读评估 + 新触发自动写标记+飞书
  python paper_forward_downgrade.py --check       # 只读评估，不写标记不推送（dry-run）
  python paper_forward_downgrade.py --unfreeze G2 # 人工解除冻结（V1.3/ENS 同理）
  python paper_forward_downgrade.py --force-freeze ENS --reason "人工强制"

退出码：0 正常（含冻结触发）；2 触发冻结告警但推送失败（fail-loud 供调度感知）；1 运行错误。
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
import data_config as DC

REAL = os.path.join(DC.DATA_DIR, "real")
MERGED = os.path.join(DC.LIVE_DIR, "merged_daily_full.parquet")
MARK_FILE = os.path.join(REAL, "paper_forward_downgrade.json")

# 臂 -> (csv 相对文件名, 持有期, 对应 rebalance 策略 key)；路径运行时 join REAL（测试可覆盖 D.REAL）
# 口径注记（P2-8 确认）：G2 臂 N15=live N15 一致；ENS 臂 N10=live N10 一致；
# V1.3 臂 N10=纸面候选口径（ab_v3enh 按 N10 回填），与实盘 rebalance_daily HOLD_DAYS=5（到期制）不一致——
# 评估窗口比实盘长，属"候选口径"有意为之（与 paper_forward_ab_stats 三臂同源），降级判定按候选口径统一。
ARMS = {
    "G2": ("paper_forward_live.csv", 15),
    "V1.3": ("paper_forward_ab_v3enh.csv", 10),
    "ENS": ("paper_forward_ens.csv", 10),
}

MIN_SAMPLES = 30          # 最小已到期样本（rank<=2）
EXCESS_FLOOR = -0.0002    # 超额均值幅度阈值（-0.02pp/日）
EXCESS_SIGN = 0.0         # 均值必须为负

_RET_CACHE = {}
_BENCH_CACHE = {}
_CAL = None


def _calendar():
    global _CAL
    if _CAL is None:
        sd = pd.read_parquet(MERGED).reset_index()
        sd["ds"] = pd.to_datetime(sd["trade_date"]).dt.strftime("%Y-%m-%d")
        _CAL = sorted(sd["ds"].unique().tolist())
    return _CAL


def load_open(hold):
    """{code: {date_str: 信号日 date 起持有 hold 交易日 open→open 复权收益}}（同 paper_forward_ab_stats）。"""
    if hold in _RET_CACHE:
        return _RET_CACHE[hold]
    sd = pd.read_parquet(MERGED, columns=["open", "adj_factor"]).reset_index()
    sd["trade_date"] = pd.to_datetime(sd["trade_date"])
    sd["adj_open"] = sd["open"] * sd["adj_factor"]
    ao = sd.set_index(["ts_code", "trade_date"])["adj_open"].sort_index()
    op1 = ao.groupby(level=0).shift(-1)
    opN = ao.groupby(level=0).shift(-1 - hold)
    m = pd.DataFrame({"op1": op1, "opN": opN}).reset_index()
    m = m[(m["op1"] > 0) & (m["opN"] > 0)]  # 过滤停牌/异常 open<=0（与 forward_stats.market_ret 同卫生）
    m["ds"] = m["trade_date"].dt.strftime("%Y-%m-%d")
    out = {c: dict(zip(sub["ds"], sub["opN"] / sub["op1"] - 1)) for c, sub in m.groupby("ts_code")}
    _RET_CACHE[hold] = out
    return out


def load_bench(hold):
    """{date_str: 全市场等权 open→open 复权收益（同窗口，与 load_open 完全对齐）}。"""
    if hold in _BENCH_CACHE:
        return _BENCH_CACHE[hold]
    sd = pd.read_parquet(MERGED, columns=["open", "adj_factor"]).reset_index()
    sd["trade_date"] = pd.to_datetime(sd["trade_date"])
    sd["adj_open"] = sd["open"] * sd["adj_factor"]
    ao = sd.set_index(["ts_code", "trade_date"])["adj_open"].sort_index()
    op1 = ao.groupby(level=0).shift(-1)
    opN = ao.groupby(level=0).shift(-1 - hold)
    m = pd.DataFrame({"op1": op1, "opN": opN}).reset_index()
    m = m[(m["op1"] > 0) & (m["opN"] > 0)]  # 同卫生：剔除 open<=0 行
    m["ds"] = m["trade_date"].dt.strftime("%Y-%m-%d")
    r = m.assign(r=m["opN"] / m["op1"] - 1)
    r = r.replace([np.inf, -np.inf], np.nan).dropna(subset=["r"])
    bench = r.groupby("ds")["r"].mean()  # 全市场等权（逐日横截面均值，与 forward_stats.market_ret 同源）
    _BENCH_CACHE[hold] = bench.to_dict()
    return _BENCH_CACHE[hold]


def load_mark():
    """读取标记文件；不存在 → 默认空标记；存在但损坏 → 告警 + 返回默认（N1 修复：不再静默）。"""
    if not os.path.exists(MARK_FILE):
        return {"_说明": "纸面前向自动降级闸标记（2026-09-13 立）"}
    try:
        with open(MARK_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("[降级闸] !! 冻结标记文件损坏/解析失败（%s），读取默认标记——请核查 %s（%s）" % (type(e).__name__, MARK_FILE, e))
        return {"_说明": "纸面前向自动降级闸标记（2026-09-13 立）"}


def is_frozen(key):
    """rebalance 消费方调用：策略 key（G2/V1.3/ENS）是否被降级闸冻结（只卖不买）。
    P2-4 修复：文件存在但损坏/解析失败 → 打印告警（保留拍板的 fail-open 行为但须可见，防静默解冻）。"""
    if not os.path.exists(MARK_FILE):
        return False
    try:
        with open(MARK_FILE, encoding="utf-8") as f:
            mark = json.load(f)
        return bool(mark.get(key, {}).get("frozen", False))
    except Exception as e:
        print("[降级闸] !! 冻结标记文件损坏/解析失败（%s），fail-open 视为未冻结——请核查 %s（%s）" % (type(e).__name__, MARK_FILE, e))
        return False


def save_mark(mark):
    os.makedirs(REAL, exist_ok=True)
    tmp = MARK_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(mark, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MARK_FILE)


def _eval_arm(key, hold, check_only=False):
    """评估单个臂，返回 (n, mean_excess, t, win, ret, bench, frozen, trigger)。"""
    csv_path = os.path.join(REAL, ARMS[key][0])
    if not os.path.exists(csv_path):
        return (0, None, None, None, None, None, False, False)
    cal = _calendar()
    ret_map = load_open(hold)
    bench_map = load_bench(hold)
    rets, benches, excs = [], [], []
    try:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
    except Exception:
        return (0, None, None, None, None, None, False, False)
    if df is None or df.empty:
        return (0, None, None, None, None, None, False, False)
    for _, r in df.iterrows():
        if "rank" in df.columns and pd.notna(r.get("rank")) and int(r["rank"]) > 2:
            continue  # 实盘 TOP2 口径
        d0 = str(r["date"])
        if d0 not in cal:
            continue
        i = cal.index(d0)
        if i + hold + 1 >= len(cal):
            continue  # 尚未到期
        v = ret_map.get(str(r["code"]), {}).get(d0)
        b = bench_map.get(d0)
        if pd.notna(v) and b is not None and not (isinstance(b, float) and np.isnan(b)):
            rets.append(float(v))
            benches.append(float(b))
            excs.append(float(v) - float(b))
    n = len(excs)
    frozen = trigger = False
    mean_excess = t = win = None
    if n > 0:
        excs = np.asarray(excs)
        mean_excess = float(excs.mean())
        win = float((excs > 0).mean())
        sd = float(excs.std(ddof=1)) if n > 1 else 0.0
        t = mean_excess / (sd / np.sqrt(n)) if (n > 1 and sd and sd > 0) else None
    mark = load_mark()
    st = mark.get(key, {})
    frozen = bool(st.get("frozen", False))
    if n >= MIN_SAMPLES and mean_excess is not None and mean_excess < EXCESS_SIGN and mean_excess < EXCESS_FLOOR:
        trigger = True
    return (n, mean_excess, t, win, float(np.mean(rets)) if rets else None,
            float(np.mean(benches)) if benches else None, frozen, trigger)


def _notify(text):
    try:
        from qmt_bridge_client_ens import notify_feishu
        notify_feishu(text)
    except Exception:
        try:
            from qmt_bridge_client import notify_feishu as nf2
            nf2(text)
        except Exception as e:
            print("[通知] 飞书异常: %s" % e)
            return False
    return True


def _eval_all(check_only=False):
    """评估三臂，新触发且非 check_only 时写标记+推送。返回 (changed, any_trigger, alerts)。"""
    mark = load_mark()
    changed = False
    any_trigger = False
    alerts = []
    for key, (_, hold) in ARMS.items():
        n, mean_excess, t, win, ret, bench, frozen, trigger = _eval_arm(key, hold, check_only)
        line = "%-5s n=%-3d 超额=%-9s t=%s 胜率=%s ret=%s bench=%s" % (
            key, n,
            ("%.5f" % mean_excess) if mean_excess is not None else "-",
            ("%.2f" % t) if t is not None else "-",
            ("%.0f%%" % (win * 100)) if win is not None else "-",
            ("%.4f" % ret) if ret is not None else "-",
            ("%.4f" % bench) if bench is not None else "-")
        print(line)
        if check_only:
            continue  # dry-run：只评估不写标记不推送
        st = mark.setdefault(key, {"frozen": False, "since": "", "n": 0, "mean_excess": None, "note": ""})
        st["n"] = n
        st["mean_excess"] = mean_excess
        if trigger and not frozen:
            st["frozen"] = True
            st["since"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
            st["note"] = "自动触发：n=%d 超额均值 %.5f < %.5f" % (n, mean_excess, EXCESS_FLOOR)
            changed = True
            any_trigger = True
            alerts.append("【降级闸】策略 %s 样本外纸面前向超额均值 %.5f（n=%d，t=%s），冻结加仓（只卖不买）。人工复盘后执行 paper_forward_downgrade.py --unfreeze %s 解除" % (
                key, mean_excess, n, ("%.2f" % t) if t is not None else "-", key))
        elif trigger and frozen:
            any_trigger = True
            print("  [已冻结] %s 持续为负，保持冻结（不自动解除）" % key)
    if not check_only:
        # N1 修复：标记文件损坏被 load_mark 归默认后，覆写会静默清掉冻结态——先备份损坏原件再写
        try:
            if os.path.exists(MARK_FILE):
                with open(MARK_FILE, encoding="utf-8") as f:
                    json.load(f)
        except Exception as e:
            import shutil as _sh
            bak = "%s.bak_corrupt_%s" % (MARK_FILE, pd.Timestamp.now().strftime("%Y%m%d_%H%M%S"))
            try:
                _sh.copy2(MARK_FILE, bak)
                print("!! [降级闸] 标记文件损坏，已备份到 %s 后重建（损坏前冻结态需人工核对，勿盲信重建后 frozen 状态）" % bak)
                alerts.append("【降级闸】标记文件损坏已备份重建：%s —— 请人工核对原冻结态是否丢失" % os.path.basename(bak))
            except Exception as e2:
                print("!! [降级闸] 标记文件损坏且备份失败（%s）——未覆写，请人工处理" % e2)
                return changed, any_trigger, alerts
        save_mark(mark)
    return changed, any_trigger, alerts


def main():
    ap = argparse.ArgumentParser(description="纸面前向自动降级闸")
    ap.add_argument("--check", action="store_true", help="只读评估，不写标记不推送")
    ap.add_argument("--unfreeze", choices=list(ARMS.keys()), help="人工解除冻结")
    ap.add_argument("--force-freeze", choices=list(ARMS.keys()), help="人工强制冻结")
    ap.add_argument("--reason", default="人工", help="强制冻结原因")
    args = ap.parse_args()

    if args.unfreeze:
        mark = load_mark()
        st = mark.setdefault(args.unfreeze, {})
        if st.get("frozen"):
            st["frozen"] = False
            st["since"] = ""
            st["note"] = "人工解除 " + pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
            save_mark(mark)
            _notify("【降级闸】策略 %s 已人工解除冻结" % args.unfreeze)
            print("已解除冻结: %s" % args.unfreeze)
        else:
            print("策略 %s 当前未冻结，无需解除" % args.unfreeze)
        return 0

    if args.force_freeze:
        mark = load_mark()
        st = mark.setdefault(args.force_freeze, {})
        st["frozen"] = True
        st["since"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        st["note"] = "人工强制冻结: %s" % args.reason
        save_mark(mark)
        _notify("【降级闸】策略 %s 人工强制冻结：%s" % (args.force_freeze, args.reason))
        print("已强制冻结: %s (%s)" % (args.force_freeze, args.reason))
        return 0

    print("=== 纸面前向降级闸评估（%s）===" % pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"))
    changed, any_trigger, alerts = _eval_all(check_only=args.check)
    push_fail = False
    for a in alerts:
        print("  [TRIGGER] %s" % a)
        ok = _notify(a)
        if not ok:
            push_fail = True
            print("!! 冻结推送失败（已写标记，调度应感知）")
    if args.check:
        print("[CHECK] dry-run：未写标记未推送")
    # P1-1 修复：冻结触发且推送失败 → exit 2（fail-loud，paper_forward_daily.ps1 DOWNGRADE-ALERT 分支可达）
    if push_fail:
        print("!! 冻结已写标记但飞书推送失败，exit 2 供调度感知")
        return 2
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
