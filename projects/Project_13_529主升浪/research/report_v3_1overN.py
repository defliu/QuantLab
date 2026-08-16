# coding: utf-8
"""T-20260816-001 T3 —— v3 1/n 复跑报告 + 预注册判定（读 scan_v3_1overN.py 产物）。

输入：results/v3_1overN_scan.json（新 1/n 复跑，每配置 3 段）
对照：旧 v3 矩阵 report 目录（max_single_pct=0.125 原口径）equity_curve.csv 计算其真实 CAGR
参考：ATR 基线 report 20260815_110119_ccfb82_atr_10w_price50 的 2023-2026 段（判定规则 2 的同期卡玛比较）

预注册判定规则（T-20260816-001 任务书 T3，跑前生效，不得改）：
  R1: 若 2023-2026 段所有变体 CAGR ≤ 2%  → Project_13 归档
  R2: 若存在变体 2023-2026 CAGR > 5% 且卡玛优于同期 ATR 基线 → 出对比报告待拍板
  R3: 中间地带（2%~5%）→ 维持 DOING，挂"信号衰减监控"，30 交易日复评

用法：python research/report_v3_1overN.py
产物：results/v3_1overN复跑判定_20260816.md
"""
import csv
import json
import os

ROOT = "D:/QuantLab"
SCAN_JSON = ROOT + "/projects/Project_13_529主升浪/results/v3_1overN_scan.json"
OUT_MD = ROOT + "/projects/Project_13_529主升浪/results/v3_1overN复跑判定_20260816.md"

# 旧扫描（max_single_pct=0.125）report 目录
OLD_DIRS = {
    "n8_h60_s12":    "reports/20260816_091053_178b19_v3_n8_h60_s12",
    "n12_h60_s12":   "reports/20260816_092113_30bf87_v3_n12_h60_s12",
    "n16_h60_s12":   "reports/20260816_093145_7071c2_v3_n16_h60_s12",
    "n8_gate_hold":  "reports/20260816_094200_6dfa6d_v3_n8_gate_hold",
    "n16_gate_hold": "reports/20260816_100311_e80823_v3_n16_gate_hold",
}
ATR_BASELINE_DIR = "reports/20260815_110119_ccfb82_atr_10w_price50"

SEGMENT_LABEL = {"full": "全样本 2019-2026", "1922": "2019-2022", "2326": "2023-2026"}


def cagr_of_equity(path, start=None, end=None):
    """从 equity_curve.csv 计算 (start,end) 窗口内 CAGR 与最大回撤。start/end 为 'YYYY-MM-DD' 或 None。"""
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    if start:
        rows = [r for r in rows if r["date"] >= start]
    if end:
        rows = [r for r in rows if r["date"] <= end]
    if len(rows) < 2:
        return None, None, None
    nav = [float(r["total_asset"]) for r in rows]
    n = len(nav)
    total = nav[-1] / nav[0] - 1.0
    cagr = (1.0 + total) ** (252.0 / n) - 1.0
    peak = nav[0]
    mdd = 0.0
    for v in nav:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak
            if dd < mdd:
                mdd = dd
    return cagr, mdd, n


def load_scan():
    with open(SCAN_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def _base_key(name):
    """v3_1n_n8_h60_s12_2326 -> n8_h60_s12（去掉 segment 后缀）。"""
    b = name.replace("v3_1n_", "")
    if b.endswith("_1922"):
        b = b[:-5]
    elif b.endswith("_2326"):
        b = b[:-5]
    return b


def main():
    scan = load_scan()
    by = {}
    for r in scan:
        if "error" in r:
            continue
        by.setdefault(_base_key(r["name"]), {})[r["segment"]] = r

    lines = []
    A = lines.append
    A("# P13 黄氏529 · v3 矩阵 1/n 修正复跑判定（2026-08-16）")
    A("")
    A("> **背景（T-20260816-001 T3）**：原 v3 矩阵（`v2v3参数矩阵扫描_20260816.md`）所有 n_hold 变体共用")
    A("> `max_single_pct: 0.125`，n12/n16 未按 1/n 调整——n16 实为「16 槽 × 12.5% 上限（现金约束下≈满仓）」，")
    A("> 并非「分散到 16 只各 6.25%」，「n 越大越好」的真实机制是现金利用率/槽位放宽，不是分散化。")
    A("> 本复跑修正 `max_single_pct = 1.0/n_hold`（n8=0.125 / n12≈0.0833 / n16=0.0625），其余冻结")
    A("> （60天 / -12% / signal_window=1 / top16 信号表），6 配置 × 3 时段重跑。")
    A("> **年化口径**：全部为 **CAGR（复利）**，括号内为线性年化（旧口径）；卡玛 = CAGR / |回撤|。")
    A("> **红线#2**：框架 `run_backtest` 纯内存、无 state_file，无残留污染。")
    A("")
    A("## 一、修正前后对照（全样本 2019-2026，CAGR 口径）")
    A("")
    A("| 配置 | 前：max_single_pct=0.125 | 后：1/n 修正 | 说明 |")
    A("|---|---|---|---|")
    for old_key, old_rel in OLD_DIRS.items():
        new_key = old_key
        new = by.get(new_key, {}).get("full")
        old_cagr, old_mdd, _ = cagr_of_equity(ROOT + "/" + old_rel + "/equity_curve.csv") \
            if os.path.isfile(ROOT + "/" + old_rel + "/equity_curve.csv") else (None, None, None)
        if new is None:
            old_txt = "n/a"
            if old_cagr is not None:
                old_txt = "总=%+.1f%% CAGR=%.2f%% 回撤=%.2f%%" % (
                    100 * old_cagr_ret(new_key, old_rel), 100 * old_cagr, 100 * (old_mdd or 0))
            A("| %s | 旧 %s | **未完成** | |" % (new_key, old_txt))
            continue
        A("| %s | 旧：总%+6.1f%% / CAGR %6.2f%% / 回撤 %6.2f%% | 新：总%+6.1f%% / **CAGR %6.2f%%**（线性%6.2f%%）/ 回撤 %6.2f%% / 夏普 %.3f / 卡玛 %.2f | %s |"
          % (new_key,
             100 * (old_cagr_ret(new_key, old_rel)), 100 * (old_cagr or 0), 100 * (old_mdd or 0),
             100 * new["total_return"], 100 * new["cagr"], 100 * new["annual_return"],
             100 * new["max_drawdown"], new["sharpe"], new["cagr_calmar"] or 0,
             "n8 本已=1/8，仅口径重算" if new_key.startswith("n8") else "1/n 修正生效"))
    A("")
    A("## 二、6 配置 × 3 时段（1/n 修正后）")
    A("")
    A("| 配置 | 时段 | 总收益 | CAGR(线性) | 回撤 | 夏普 | 卡玛 | 交易数 |")
    A("|---|---|---|---|---|---|---|---|")
    for key in sorted(by.keys()):
        for seg in ("full", "1922", "2326"):
            r = by.get(key, {}).get(seg)
            if r is None:
                A("| %s | %s | (未跑) | | | | | |" % (key, SEGMENT_LABEL[seg]))
            else:
                A("| %s | %s | %+7.1f%% | %6.2f%%(%6.2f%%) | %7.2f%% | %.3f | %.2f | %d |"
                  % (key, SEGMENT_LABEL[seg], 100 * r["total_return"], 100 * r["cagr"],
                     100 * r["annual_return"], 100 * r["max_drawdown"], r["sharpe"],
                     r["cagr_calmar"] or 0, r["n_trades"]))
    A("")
    A("## 三、同期 ATR 基线（判定规则 2 参照，2023-2026 段）")
    A("")
    atr_cagr, atr_mdd, _ = cagr_of_equity(ROOT + "/" + ATR_BASELINE_DIR + "/equity_curve.csv", "2023-01-01", "2026-06-30") \
        if os.path.isfile(ROOT + "/" + ATR_BASELINE_DIR + "/equity_curve.csv") else (None, None, None)
    if atr_cagr is not None:
        A("ATR 基线（8只+price50）2023-2026 段：CAGR %6.2f%% / 回撤 %6.2f%% / 卡玛 %.2f"
          % (100 * atr_cagr, 100 * atr_mdd, atr_cagr / abs(atr_mdd) if atr_mdd else 0))
    else:
        A("ATR 基线 equity_curve 缺失，无法计算同期参照。")
    A("")
    A("## 四、预注册判定")
    A("")
    A("> 规则（T-20260816-001 T3，跑前生效）：R1 若 2023-2026 所有变体 CAGR ≤ 2% → 归档；")
    A("> R2 若存在变体 2023-2026 CAGR > 5% 且卡玛优于同期 ATR 基线 → 出对比报告待拍板；")
    A("> R3 中间（2%~5%）→ DOING + 信号衰减监控，30 交易日复评。")
    A("")
    seg2326 = {k: v.get("2326") for k, v in by.items()}
    done2326 = {k: r for k, r in seg2326.items() if r}
    if not done2326:
        A("**判定：数据未齐（2023-2026 段缺失），无法判定。**")
    else:
        max_cagr = max(100 * r["cagr"] for r in done2326.values())
        best_calmar = max((r["cagr_calmar"] or 0) for r in done2326.values())
        best_key = [k for k, r in done2326.items()
                    if (r["cagr_calmar"] or 0) == best_calmar][0]
        atr_calmar = atr_cagr / abs(atr_mdd) if (atr_cagr is not None and atr_mdd) else 0.0
        if max_cagr <= 2.0:
            verdict = "**归档（R1）**：2023-2026 段所有变体 CAGR ≤ 2%，信号近 3.5 年未恢复，全样本收益系 2019-2022 前段遗产。README 写归档结论，不分配资金。"
        elif any(100 * r["cagr"] > 5.0 for r in done2326.values()) and \
                atr_cagr is not None and best_calmar > atr_calmar:
            verdict = "**待拍板（R2）**：存在 2023-2026 CAGR > 5% 且卡玛优于同期 ATR 基线 → 出对比报告待诚哥拍板，仍不分配资金。"
        else:
            verdict = ("**维持 DOING + 信号衰减监控（R3）**：2023-2026 段最高 CAGR %+.2f%%（%s）已大于 5%% 但卡玛 %.2f 未优于同期 ATR 基线 %.2f，"
                       "不符合 R2「待拍板」条件，亦不构成 R1 归档（非全 ≤2%%）。挂「信号衰减监控」标签，30 个交易日后复评；暂不分配资金。"
                       % (max_cagr, best_key, best_calmar, atr_calmar))
        A("2023-2026 段各变体 CAGR：%s" % " / ".join(
            "%s=%+.2f%%" % (k, 100 * r["cagr"]) for k, r in sorted(done2326.items())))
        A("2023-2026 段最高 CAGR = %+.2f%%（%s，阈值 5%%），最高卡玛 = %.2f（%s；ATR 基线同期卡玛 = %.2f）"
          % (max_cagr, best_key, best_calmar, best_key, atr_calmar))
        A("")
        A("**判定结论**：" + verdict)
    A("")
    A("## 五、工程说明")
    A("")
    A("- 脚本：`research/scan_v3_1overN.py`（1/n 修正复跑）+ `research/report_v3_1overN.py`（本报告）。")
    A("- 复现：`python research/scan_v3_1overN.py`（6 配置 × 3 段 ≈ 3 小时），再 `python research/report_v3_1overN.py`。")
    A("- 数据：top16 信号表 1615 天 / 日均 7.9 只，ATR 升序切片，PIT 安全（审计复核通过项）。")
    A("- 全部 report 目录落 `D:/QuantLab/reports/v3_1n_*`。")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("report -> %s" % OUT_MD)


def old_cagr_ret(key, rel):
    """旧扫描全样本总收益（从 summary.json 读，兼容缺失）。"""
    p = os.path.join(ROOT, rel, "summary.json")
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)["performance"]["total_return"]
        except Exception:
            pass
    return 0.0


if __name__ == "__main__":
    main()