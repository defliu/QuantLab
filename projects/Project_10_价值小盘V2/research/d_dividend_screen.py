# coding=utf-8
"""股息率截断消融 —— V2a(纯BP z1.0) + TTM现金股息率合格域过滤
来源: 果仁网策略「高利高息-周调10股」(高盈利 x 高股息 x 低价格) 拆解出红利维度 (2026-08-17)

设计 (与用户已证伪结论对齐):
  基线   = V2a 存档 (纯BP z1.0, 质量排雷, 80只, 双月, R0风控)  -> 复现 16.2%/200.9%
  变体   = V2a + TTM现金股息率 >= 阈值 (只做合格域过滤, 不改变排序)
  红线   = 不做"红利排序/加权"——用户已在 ATR 低波证伪"质量/红利/ML 排序等加法式聪明筛选",
          股息率仅作为合格域 filter (与 ROE>0 质量排雷同类), 不参与 z-score 加权
口径:
  股息率(d) = Σ(已实施分红 cash_div_tax, ex_date∈(d-365, d]) / close(d)
  PIT 安全 = 只用 div_proc=='实施' 且 ex_date<=d 的分红 (除息后才真实获得)
否决性判据 (项目惯例): 2024+ 净超额劣于基线即不通过

输出: results/d_dividend_screen.txt
"""
import sys, os, time
sys.path.insert(0, r"D:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
sys.path.insert(0, r"D:\QuantLab")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_grid_validation as rgv
import pandas as pd
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(HERE, "results")
os.makedirs(RES, exist_ok=True)

log = []
def p(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    log.append(s)


def build_ttm_div_yield():
    """构造 TTM 股息率宽表 (index=全部交易日, columns=ts_code, value=股息率)"""
    t0 = time.time()
    div = pd.read_parquet(r"E:/astock/finance/dividend.parquet")
    div = div[div["div_proc"].astype(str).str.strip() == "实施"].copy()  # 仅已实施
    div = div[div["ex_date"].notna()].copy()                            # 有除息日
    div["ex_date"] = pd.to_datetime(div["ex_date"])
    div = div[div["cash_div_tax"].notna() & (div["cash_div_tax"] > 0)].copy()
    # 同一天多笔分红累加 (cash_div_tax=每股税前派息, 已用茅台验证)
    div_wide = div.pivot_table(index="ex_date", columns="ts_code",
                               values="cash_div_tax", aggfunc="sum").sort_index().fillna(0.0)
    cum = div_wide.cumsum()
    cum = cum.set_axis(pd.DatetimeIndex(cum.index).as_unit("ns"))  # pandas3.0 us/ns reindex 不匹配修复
    all_dates = pd.DatetimeIndex(sorted(rgv.panel.index.get_level_values("trade_date").unique()))
    cum_now = cum.reindex(all_dates).ffill().fillna(0.0)
    cum_365 = cum.reindex(all_dates - pd.Timedelta(days=365)).ffill().fillna(0.0).set_axis(all_dates)
    ttm = cum_now - cum_365
    close_wide = rgv.panel["close"].unstack("ts_code").reindex(all_dates)
    dy = ttm / close_wide.replace(0, np.nan)
    p("TTM股息率宽表完成: shape=%s 用时 %.1fs" % (str(dy.shape), time.time() - t0))
    return dy


def make_run_div(dy_wide):
    """返回一个带股息率阈值过滤的 run_variant (阈值在调用时传入)"""
    _orig_get_candidates = rgv.get_candidates
    _state = {"thr": 0.0}

    def get_candidates_with_div(d, dd):
        cand = _orig_get_candidates(d, dd)
        thr = _state["thr"]
        if thr > 0:
            dy_row = dy_wide.loc[pd.Timestamp(d)] if pd.Timestamp(d) in dy_wide.index else None
            if dy_row is not None:
                cand = [c for c in cand if (dy_row.get(c) or 0.0) >= thr]
        return cand

    def run(thr, scorer, risk):
        _state["thr"] = thr
        rgv._cand_cache.clear()  # 避免跨阈值缓存污染
        rgv.get_candidates = get_candidates_with_div
        try:
            return rgv.run_variant(scorer, risk)
        finally:
            rgv.get_candidates = _orig_get_candidates

    return run


if __name__ == "__main__":
    p("============ D: 股息率合格域截断消融 (V2a, 2026-08-17) ============")
    p("运行时间:", time.strftime("%Y-%m-%d %H:%M:%S"))

    dy_wide = build_ttm_div_yield()
    run = make_run_div(dy_wide)

    p("\n============ 基准 ============")
    base = rgv.run_base()
    b_cum = (1 + base["ret"]).cumprod() - 1
    b_years = (base.index[-1] - base.index[0]).days / 365.25
    p("基准: 累计=%6.1f%% 年化=%6.1f%%" % (b_cum.iloc[-1] * 100,
                                          ((1 + b_cum.iloc[-1]) ** (1 / b_years) - 1) * 100))

    variants = [
        ("V2a基线(无股息率过滤)", 0.00),
        ("+股息率>=0.5%", 0.005),
        ("+股息率>=1.0%", 0.010),
        ("+股息率>=2.0%", 0.020),
        ("+股息率>=3.0%", 0.030),
    ]

    p("\nvariant                  年化     回撤     换手   超额[全期     2024+     2026]   (用时)")
    summary = {}
    for name, thr in variants:
        t1 = time.time()
        scorer = rgv.make_scorer("bp", 1.0, 0.0)  # V2a 纯BP
        risk = rgv.make_risk("d_thr%03d" % int(thr * 1000), atr_stop=False, tiered=False)
        res = run(thr, scorer, risk)
        st = rgv.summarize(res, base)
        if st is None:
            p("%-24s 无结果" % name)
            continue
        summary[name] = st
        p("%-24s %+6.1f%% %+7.1f%%  %.2f   %+8.1f%% %+7.1f%% %+6.1f%%   (%.0fs)" % (
            name, st["年化"] * 100, st["最大回撤"] * 100, st["平均换手"],
            (st["超额全期"] or 0) * 100, (st["超额2024+"] or 0) * 100,
            (st["超额2026"] or 0) * 100, time.time() - t1))

    # 自检: 基线应复现 V2a 存档 (年化16.2% / 回撤-29.7% / 超额全期+200.9% / 换手0.91)
    p("\n自检判据: V2a基线应约=存档 (年化+16.2% 回撤-29.7% 超额全期+200.9% 换手0.91)")

    # 否决性判据: 2024+ 劣于基线即不通过
    b0 = summary.get("V2a基线(无股息率过滤)", {}).get("超额2024+")
    if b0 is not None:
        p("\n否决性判据 (2024+ 不得劣于基线 %+.1f%%):" % (b0 * 100))
        for name, st in summary.items():
            if name == "V2a基线(无股息率过滤)":
                continue
            e24 = st["超额2024+"]
            verdict = "通过" if (e24 is not None and e24 >= b0 - 1e-9) else "不通过(2024+劣化)"
            p("  %-24s 2024+=%+.1f%%  -> %s" % (name, (e24 or 0) * 100, verdict))

    # 过滤强度抽查 (2024-01 调仓日候选数 vs 基线)
    p("\n过滤强度抽查 (2024-01 调仓日候选数 vs 基线):")
    d_probe = pd.Timestamp("2024-01-02")
    if d_probe in dy_wide.index:
        dd = rgv.panel.loc[d_probe]
        base_cand = rgv.get_candidates(d_probe, dd)
        dy_row = dy_wide.loc[d_probe]
        for thr, label in [(0.005, ">=0.5%"), (0.01, ">=1%"), (0.02, ">=2%"), (0.03, ">=3%")]:
            n = sum(1 for c in base_cand if (dy_row.get(c) or 0.0) >= thr)
            p("  %s: %d/%d 候选 (%.0f%%)" % (label, n, len(base_cand), 100.0 * n / max(1, len(base_cand))))

    # 原子替换写结果 (红线: 禁止直接 open(path,'w') 改原文件)
    out_path = os.path.join(RES, "d_dividend_screen.txt")
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    os.replace(tmp_path, out_path)
    print("结果已写入:", out_path)

    # 清理临时 state 文件 (红线: 带 state_file 的回测入口跑完清理)
    for fn in os.listdir(RES):
        if fn.startswith("grid_state_d_") and fn.endswith(".json"):
            os.remove(os.path.join(RES, fn))
    print("临时状态文件已清理")