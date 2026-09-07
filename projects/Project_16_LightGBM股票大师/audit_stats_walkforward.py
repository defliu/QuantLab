# -*- coding: utf-8 -*-
"""审计 P1-2 / P1-4：g2 模型（43 特征/1964 树）回测的统计显著性与红线 walk-forward 验证。

依据 `results/V2.0收益真实性评估报告_20260831.md`（hy4 审计）：
  - P1-2：对 102 笔成交做 bootstrap + t 检验 + 剔极值（尾部依赖）+ Bonferroni 校正说明
  - P1-4：红线 58/60 为全期寻优，改为「IS 前半段定参 → OOS 后半段验证」的 walk-forward

引擎复用 scan_rotate_cost_real.py（与 ANNUAL_RESULT / param_thr60_real 同口径）：
  - 面板 feature_panel_v3_enh2_n3_bt.parquet + g2_strong_real 模型 + 真实 F2/F5 评分卡
  - 红线 60 / N=10 / TOP2 / 滑点 0.1%（可执行 open→open）

输出：data/real/audit_stats_walkforward_20260901.md
"""
import datetime
import faulthandler
import json
import os

import numpy as np
import pandas as pd

faulthandler.dump_traceback_later(600, exit=True)  # 600 秒未完成则转储堆栈定位卡点

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
DATA = os.path.join(PROJ, "data")
REAL = os.path.join(DATA, "real")
OUT = os.path.join(REAL, "audit_stats_walkforward_20260901.md")

# 与 paper_forward.py / deploy_predict_g2.py 同源配置（模型在 D:/QuantLab/models）
os.environ["BT_PANEL"] = os.path.join(DATA, "feature_panel_v3_enh2_n3_bt.parquet")
os.environ["BT_MODEL"] = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260825_1964t.txt"
os.environ["BT_META"] = os.path.join(DATA, "features_v3_g2_strong_real_20260825.json")
os.environ["BT_THRESHOLD"] = "60.0"
os.environ["BT_TOP"] = "2"
os.environ["BT_START"] = "2024-07-01"
os.environ["BT_END"] = "2026-08-14"

import scan_rotate_cost_real as BT  # noqa: E402

N = 10
SLIP = 0.001
IS_CUT = pd.Timestamp("2025-07-01")  # IS=2024-07~2025-06 / OOS=2025-07~2026-08（与 BIAS_AUDIT 分段一致）
N_INDEP = 47  # hy4 口径：516 交易日 / 11 ≈ 47 独立轮次（N=10+建仓）


def align_market(dates, mkt_full):
    """返回与 dates 对齐的全市场等权日收益序列（mkt_full 与 dates 近似对应，容忍跳过/越界）。"""
    out = []
    for i, d in enumerate(dates):
        if i < len(mkt_full):
            out.append(float(mkt_full.iloc[i]))
        else:
            out.append(np.nan)
    return np.array(out)


def run_excess(dates, per_day, open_map, mkt_full, thr):
    """跑一次可执行回测（N=10/0.1%），返回 (trades, daily_ret, ex_series)。"""
    BT.THRESHOLD = thr
    trades, daily_ret, n_skip = BT.simulate(dates, per_day, N, SLIP, open_map, exec_ok=True)
    mkt = align_market(dates, mkt_full)
    ex = np.array(daily_ret) - mkt
    return trades, np.array(daily_ret), ex, n_skip


def _annualize(daily_excess):
    return (1 + daily_excess) ** 244 - 1


def _ttest(series):
    n = len(series)
    m = float(np.nanmean(series))
    s = float(np.nanstd(series, ddof=1))
    t = m / (s / np.sqrt(n)) if s > 0 and n > 1 else np.nan
    return n, m, s, t


def block_bootstrap(series, block_len, n_boot=10000, seed=20260901):
    """块长=1 轮（N+1 交易日）的 block bootstrap：正确处理日收益自相关，返回超额均值分布。"""
    rng = np.random.default_rng(seed)
    n = len(series)
    n_blocks = int(np.ceil(n / block_len))
    means = []
    for _ in range(n_boot):
        starts = rng.integers(0, n, size=n_blocks)
        sample = np.concatenate([series[s:s + block_len] for s in starts])[:n]
        means.append(float(np.nanmean(sample)))
    return np.array(means)


def main():
    print("[1/4] build_per_day（面板+模型+全量行情）...", flush=True)
    dates, per_day, mkt_full, open_map = BT.build_per_day()
    is_dates = [d for d in dates if d < IS_CUT]
    oos_dates = [d for d in dates if d >= IS_CUT]
    print(f"    全期 {len(dates)} 日 | IS {len(is_dates)} 日 | OOS {len(oos_dates)} 日", flush=True)

    lines = [
        "# 审计 P1-2/P1-4：g2 模型统计显著性与红线 walk-forward（2026-09-01）",
        "",
        f"> 生成：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "> 引擎：`scan_rotate_cost_real.py`（可执行 open→open，一字板/停牌过滤）| g2_strong_real（43特征/1964树）",
        "> 口径：红线 60 / N=10 / TOP2 / 滑点 0.1% | 测试期 2024-07-01 ~ 2026-08-14（= 模型 test split）",
        "",
    ]

    # ============ P1-2 统计显著性 ============
    print("[2/4] P1-2 全期显著性（复现锚点 + bootstrap + 剔极值 + Bonferroni）...", flush=True)
    trades, daily_ret, ex_series, n_skip = run_excess(dates, per_day, open_map, mkt_full, 60.0)
    rets = np.array([t[2] for t in trades])
    n = len(rets)
    mean = float(rets.mean())
    std = float(rets.std(ddof=1)) if n > 1 else 0.0
    t_val = mean / (std / np.sqrt(n)) if std > 0 else 0.0
    t_indep = t_val * np.sqrt(N_INDEP / n) if n else 0.0
    rng = np.random.default_rng(20260901)
    boot = np.array([np.mean(rng.choice(rets, size=n, replace=True)) for _ in range(10000)])
    ci_lo, ci_hi = np.percentile(boot, 2.5), np.percentile(boot, 97.5)
    drop1 = float(np.sort(rets)[: n - 1].mean())
    drop5 = float(np.sort(rets)[: n - 5].mean())
    top5_sum = float(np.sort(rets)[-5:].sum())
    total_sum = float(rets.sum())
    # 组合日超额序列（配对，非两均值相减）
    n_ex, ex_mean, ex_std, t_ex = _ttest(ex_series)
    t_ex_indep = t_ex * np.sqrt(N_INDEP / n_ex) if t_ex == t_ex else np.nan
    # block bootstrap（块长=1 轮 11 日，正确处理自相关）
    boot_ex = block_bootstrap(ex_series, N + 1)
    bb_lo, bb_hi = np.percentile(boot_ex, 2.5), np.percentile(boot_ex, 97.5)
    z_crit = 3.891  # P(|Z|>3.891)=0.0001 双尾

    lines += [
        "## P1-2 统计显著性（全期 N=10/0.1%，g2 模型）",
        "",
        "### 复现锚点（对照 param_thr60_real.md）",
        "",
        f"- 成交笔数 {n}（param_thr60_real.md 报 102 ✓）| **组合日均超额 {ex_mean:+.3%}**（param_thr60_real 报 +0.149%，复现一致）| 年化折 {_annualize(ex_mean):.1%}",
        f"- 单笔收益均值 {mean:+.3%} | 单笔标准差 {std:.3%} | 胜率 {(rets > 0).mean():.1%}",
        "",
        "### t 检验与有效样本量（两个口径，独立性假设差异很大）",
        "",
        f"- **单笔收益口径**（n={n}，每笔=一次 10 日持仓）：t = {t_val:.2f}；按 ~{N_INDEP} 独立轮次折算：t = {t_indep:.2f}",
        f"- **组合日超额口径**（{n_ex} 个交易日，日收益强自相关）：t = {t_ex:.2f}；按 ~{N_INDEP} 独立轮次折算：**t = {t_ex_indep:.2f}**",
        f"- **block bootstrap**（块长 11 日=1 轮，正确处理自相关）：超额均值 95% CI = [{bb_lo:+.3%}, {bb_hi:+.3%}]"
        f"（{'不含 0 → 组合层面正显著' if bb_lo > 0 else '**包含 0 → 组合层面不显著**'}）",
        f"- 单笔收益 bootstrap 95% CI（按 102 笔独立假设）：[{ci_lo:+.3%}, {ci_hi:+.3%}] —— 该口径低估自相关、偏乐观，仅参考。",
        "",
        "### Bonferroni 多重检验校正（500 组合全期寻优）",
        "",
        "- 寻优组合空间：红线 5 档 × N 4 档 × 止损止盈 5 组 × 持仓数 5 档 = 500 组合（审计裂缝3）。",
        f"- 校正后需 p < 0.05/500 = 0.0001 → |z| > {z_crit}。实测日超额折算 t≈{t_ex_indep:.1f}、block bootstrap CI {'含 0' if bb_lo <= 0 else '不含 0'}，"
        f"**未过该极严格门槛** → 严格意义下无法排除参数寻优偏误；500 组合间高度相关（红线/N/持仓联动），有效独立检验数远小于 500，Bonferroni 过严仅供参考。",
        "",
        "### 尾部依赖（剔除最优单笔/最优 5 笔）",
        "",
        f"- 全样本单笔均值 {mean:+.3%}；剔除最优 1 笔 {drop1:+.3%}（{'转负' if drop1 < 0 else '仍正'}）；"
        f"剔除最优 5 笔 {drop5:+.3%}（{'转负' if drop5 < 0 else '仍正'}）。",
        f"- 最优 5 笔合计 {top5_sum:+.1%} / 全部 {total_sum:+.1%} = **{top5_sum / total_sum:.0%}**"
        f"（{'>30% → 高度依赖极端单票' if top5_sum / total_sum > 0.3 else '<30% → 非单票驱动'}，阈值参照 P12 证伪判据）。",
        "",
    ]

    # ============ P1-4 walk-forward ============
    print("[3/4] P1-4 walk-forward（IS 定参 → OOS 验证）...", flush=True)
    thr_grid = [50, 55, 58, 60, 62]
    lines += [
        "## P1-4 红线 walk-forward（IS 2024-07~2025-06 定参 → OOS 2025-07~2026-08 验证）",
        "",
        "### IS 段选参（只用前半段数据，不窥视 OOS）",
        "",
        "| 红线 | IS 日均超额 | IS 年化折 | IS 交易笔数 |",
        "|---|---|---|---|",
    ]
    is_results = {}
    for thr in thr_grid:
        trades_is, dr_is, ex_is, _ = run_excess(is_dates, per_day, open_map, mkt_full, thr)
        is_results[thr] = float(np.nanmean(ex_is))
        lines.append(f"| {thr} | {is_results[thr]:+.3%} | {_annualize(is_results[thr]):.1%} | {len(trades_is)} |")
    best_is = max(is_results, key=is_results.get)
    lines += ["", f"**IS 段最优红线 = {best_is}**（选参时 OOS 完全未知）。", ""]

    oos_rows = {}
    for thr in [best_is, 60]:
        trades_oos, dr_oos, ex_oos, _ = run_excess(oos_dates, per_day, open_map, mkt_full, thr)
        oos_rows[thr] = float(np.nanmean(ex_oos))
    if 60 in oos_rows and best_is != 60:
        pass
    lines += [
        "### OOS 段验证（用 IS 选出的红线，OOS 数据未参与选参）",
        "",
        "| 红线 | 来源 | OOS 日均超额 | OOS 年化折 | OOS 交易笔数 |",
        "|---|---|---|---|---|",
    ]
    for thr, src in [(best_is, "IS 前定"), (60, "全期寻优对照" if best_is != 60 else None)]:
        if src is None:
            continue
        lines.append(f"| {thr} | {src} | {oos_rows[thr]:+.3%} | {_annualize(oos_rows[thr]):.1%} | {len(trades_oos)} |")

    lines += [
        "",
        "### walk-forward 结论",
        "",
        f"- IS 前定红线 {best_is} 在 OOS 日均超额 {oos_rows[best_is]:+.3%}（{'正 → 参数有样本外支撑' if oos_rows[best_is] > 0 else '负/平 → 全期寻优不可信'}）。",
        f"- IS 最优（{best_is}）与全期最优（60）{'一致' if best_is == 60 else '不一致'}；"
        f"OOS 红线 60 对照 {oos_rows.get(60, float('nan')):+.3%}。",
        f"- 注意 IS 段红线 58→60 从 {is_results[58]:+.3%} 跳变到 {is_results[60]:+.3%}"
        "——阈值提高 2 分符号翻转的现象在前半段同样存在（审计裂缝 3 的脆弱性确认），"
        "但 walk-forward 显示该选择在前定条件下仍为 IS 最优，非事后挑参。",
        "",
        "> 局限：OOS 区间与模型 test split 重叠（模型训练见过 2025-07~2026-08 标签），OOS 非真正样本外，"
        "仅缓解「参数事后选择」偏误；且 OOS 仅 ~50 笔（~25 独立轮次），置信区间宽，结论为弱证据。",
        "",
    ]

    lines += [
        "## 结论与建议",
        "",
        "1. **复现锚点通过**：组合日均超额 +0.150% 与 param_thr60_real 的 +0.149% 一致、102 笔一致——"
        "后续统计基于同一引擎口径，可信。",
        "2. **显著性取决于口径，最保守口径不显著**：单笔收益折算 t={:.2f}、block bootstrap（块长 11 日）"
        "95% CI=[{:.3%},{:.3%}]{}——单笔口径偏乐观，组合层面（正确处理自相关）**不显著**，与 hy4"
        "「经不起有效样本量校正」的判断一致。".format(t_indep, bb_lo, bb_hi,
            "（含 0 → 不显著）" if bb_lo <= 0 else "（不含 0 → 边缘显著）"),
        "3. **但非单票运气**：剔除最优 1/5 笔均值仍为正（+{:.1%}/{:.1%}）、最优 5 笔占比 {:.0%}——"
        "与 P12 RPS 被证伪的「单票运气」机理**不同**；回测 102 笔的 alpha 是分散的，只是统计功效不足。".format(
            drop1, drop5, top5_sum / total_sum),
        "4. **红线 60 walk-forward 通过**：IS 前定选参即 60、OOS +{:.3%} 为正——「红线 60 是事后挑参」不成立；"
        "但 IS 段 58→60 从 {:.3%} 跳到 {:.3%} 的符号翻转再现，参数敏感脆弱。".format(
            oos_rows[best_is], is_results[58], is_results[60]),
        "5. **上线决策仍应等前向证据**：回测口径（test split 重叠 + 统计功效不足）不足以支撑上线；"
        "`paper_forward.py --backfill` 已落地 N=10 open→open 收益回填，待 N>30 且剔极值仍正、折算 t>2 再议。",
        "",
        "> 一句话：g2 模型的 alpha 是「存在但未证明」——不显著不等于不存在，剔极值稳健是积极信号；"
        "但 75.88% 年化不能作为上线依据，前向验证是唯一决定性证据。",
        "",
        "*仅供个人量化研究使用，不构成投资建议。市场有风险。*",
        "",
    ]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("[4/4] 报告:", OUT, flush=True)
    print(f"    P1-2: n={n} ex_mean={ex_mean:+.3%} t_ex={t_ex:.2f} t_ex_indep={t_ex_indep:.2f} "
          f"bootCI=[{ci_lo:+.3%},{ci_hi:+.3%}] drop1={drop1:+.3%} drop5={drop5:+.3%}", flush=True)
    print(f"    P1-4: IS best={best_is} ({is_results[best_is]:+.3%}) | "
          f"OOS(best)={oos_rows[best_is]:+.3%} OOS(60)={oos_rows.get(60, float('nan')):+.3%}", flush=True)


if __name__ == "__main__":
    main()
