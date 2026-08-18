# coding=utf-8
"""A1+A2 联合因子 IC Kill Test：大盘域 BP + 股息率（果仁 P0/P1 验证）
来源: 通宵批次任务书 T-20260817-004

域: PIT circ_mv 前500（主口径）+ 前1000（敏感性）; 对照=后30%（小盘域）
因子: BP = 1/pb (daily, PIT via available-at-date); 股息率 TTM (dividend.parquet, ex_date口径, PIT)
内容: 月频 rankIC 序列 (2019-01~2026-06), 五分位分组累计, 多空净值; 分段 2019-2022 / 2023-2026
判定(预注册):
  A1: 主口径全样本 rankIC均值≥0.03 且 2023-2026段≥0.02 且全样本ICIR≥0.3 -> A3放行; 否则记录归档,果仁P0关闭
  A2: 2023-2026段 rankIC≥0.02 且五分位单调(Q5>Q3>Q1) -> A3放行; 否则归档
锚:
  A-BP量级: 小盘域BP对照组 rankIC应落0.05~0.10
  A-股息量级: 股息率全样本rankIC应落0.00~0.08
"""
import sys, os, time
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\QuantLab")

START = "2019-01-01"
END = "2026-06-30"
MIN_IC_N = 30  # 每期最少样本
REBAL_FREQ = "M"  # 月频IC

DAILY_PATH = r"E:/astock/daily/stock_daily.parquet"
DIV_PATH = r"E:/astock/finance/dividend.parquet"
BASIC_PATH = r"E:/astock/basic/stock_basic.parquet"

OUT_DIR = r"D:\QuantLab\projects\Project_10_价值小盘V2\results"
os.makedirs(OUT_DIR, exist_ok=True)

_log = []
def log(*args):
    s = " ".join(str(a) for a in args)
    print(s, flush=True)
    _log.append(s)


def load_daily():
    """加载日线数据，返回 panel (MultiIndex: trade_date, ts_code)"""
    t0 = time.time()
    df = pd.read_parquet(DAILY_PATH)
    df = df.reset_index()
    df = df[(df['trade_date'] >= START) & (df['trade_date'] <= END)]
    df = df.set_index(['trade_date', 'ts_code'])
    log("daily panel: shape=%s, %.1fs" % (df.shape, time.time() - t0))
    return df


def build_universe_by_date(panel, top_n_list=(500, 1000)):
    """构建 PIT circ_mv 域快照: {date: {top_n: [codes]}}
    每月首日取 circ_mv 前 top_n, 后30% 作为小盘对照
    """
    t0 = time.time()
    circ = panel['circ_mv'].dropna()
    circ = circ[circ > 0]

    dates = sorted(circ.index.get_level_values('trade_date').unique())
    # 月度采样
    monthly = pd.DatetimeIndex(dates).to_period('M').drop_duplicates()
    snap_dates = []
    for p in monthly:
        candidates = [d for d in dates if pd.Timestamp(d).to_period('M') == p]
        if candidates:
            snap_dates.append(candidates[0])

    result = {}
    for d in snap_dates:
        try:
            day_circ = circ.loc[pd.Timestamp(d)] if pd.Timestamp(d) in circ.index.get_level_values(0) else None
        except KeyError:
            continue
        if day_circ is None:
            # try iloc
            day_data = circ.xs(pd.Timestamp(d), level=0, drop_level=False)
            if len(day_data) == 0:
                continue
            day_circ = day_data.droplevel(0)
        day_circ = day_circ.sort_values(ascending=False)
        n_total = len(day_circ)
        entry = {}
        for top_n in top_n_list:
            if n_total >= top_n:
                entry[f'top{top_n}'] = day_circ.head(top_n).index.tolist()
        # 后30%小盘
        bot30_n = int(n_total * 0.3)
        if bot30_n > 0:
            entry['bot30'] = day_circ.tail(bot30_n).index.tolist()
        if entry:
            result[pd.Timestamp(d)] = entry

    log("universe snapshots: %d dates, %.1fs" % (len(result), time.time() - t0))
    return result, snap_dates


def build_div_yield_ttm(panel):
    """构造 TTM 股息率 (除息日口径, PIT安全)"""
    t0 = time.time()
    div = pd.read_parquet(DIV_PATH)
    div = div[div['div_proc'].astype(str).str.strip() == '实施'].copy()
    div = div[div['ex_date'].notna()].copy()
    div['ex_date'] = pd.to_datetime(div['ex_date'])
    div = div[div['cash_div_tax'].notna() & (div['cash_div_tax'] > 0)].copy()

    div_wide = div.pivot_table(index='ex_date', columns='ts_code',
                               values='cash_div_tax', aggfunc='sum').sort_index().fillna(0.0)
    cum = div_wide.cumsum()
    cum = cum.set_axis(pd.DatetimeIndex(cum.index).as_unit('ns'))

    all_dates = sorted(panel.index.get_level_values('trade_date').unique())
    all_dates_dt = pd.DatetimeIndex(all_dates)
    cum_now = cum.reindex(all_dates_dt).ffill().fillna(0.0)
    cum_365 = cum.reindex(all_dates_dt - pd.Timedelta(days=365)).ffill().fillna(0.0).set_axis(all_dates_dt)
    ttm = cum_now - cum_365

    close_wide = panel['close'].unstack('ts_code').reindex(all_dates_dt)
    dy = ttm / close_wide.replace(0, np.nan)

    log("TTM股息率: shape=%s, %.1fs" % (dy.shape, time.time() - t0))
    return dy


def compute_rank_ic(factor_vals, fwd_ret):
    """Spearman rank IC"""
    common = factor_vals.dropna().index.intersection(fwd_ret.dropna().index)
    if len(common) < MIN_IC_N:
        return None
    ic = factor_vals.loc[common].rank().corr(fwd_ret.loc[common].rank())
    return ic if not np.isnan(ic) else None


def ic_stats(ic_series):
    if ic_series is None or len(ic_series) < 3:
        return None
    m = ic_series.mean()
    s = ic_series.std()
    return {
        'n': len(ic_series),
        'ic_mean': float(m),
        'ic_std': float(s),
        'icir': float(m / s) if s > 0 else 0.0,
        'positive_pct': float((ic_series > 0).mean()),
    }


def run_ic_test(panel, universe_map, factor_name, factor_fn, rebal_dates):
    """运行因子IC测试: 月频rankIC + 五分位分组 + 多空"""
    trade_dates = sorted(panel.index.get_level_values('trade_date').unique())
    ti = {pd.Timestamp(x): k for k, x in enumerate(trade_dates)}

    ic_records = []
    quintile_returns = {q: [] for q in range(1, 6)}  # Q1=低 Q5=高
    long_short = []

    for i in range(len(rebal_dates) - 1):
        d = pd.Timestamp(rebal_dates[i])
        d_next = pd.Timestamp(rebal_dates[i + 1])

        # 获取当日数据
        try:
            dd = panel.loc[d]
        except KeyError:
            continue
        if len(dd) < MIN_IC_N:
            continue

        # 前向收益
        d_next_idx = ti.get(d_next)
        d_idx = ti.get(d)
        if d_idx is None or d_next_idx is None or d_next_idx <= d_idx:
            continue
        # T+1 开盘买入 -> 下期 T+1 开盘卖出
        e_idx = d_idx + 1
        x_idx = d_next_idx + 1
        if e_idx >= len(trade_dates) or x_idx >= len(trade_dates):
            continue
        e_date = trade_dates[e_idx]
        x_date = trade_dates[x_idx]

        # 获取域内股票
        # 找最近的 universe snapshot <= d
        snap_dates_sorted = sorted(universe_map.keys())
        snap_d = None
        for sd in snap_dates_sorted:
            if sd <= d:
                snap_d = sd
            else:
                break
        if snap_d is None:
            continue
        codes_in_universe = universe_map[snap_d]

        # 因子值
        factor_vals = factor_fn(d, dd, codes_in_universe)
        if factor_vals is None or len(factor_vals.dropna()) < MIN_IC_N:
            continue

        # 计算前向收益 (close对close, 用T日close到下期T日close)
        try:
            ret_now = panel.loc[d, 'close']
            ret_next = panel.loc[d_next, 'close']
        except KeyError:
            continue
        common = factor_vals.dropna().index.intersection(
            ret_now.dropna().index).intersection(ret_next.dropna().index)
        if len(common) < MIN_IC_N:
            continue
        fwd_ret = ret_next.loc[common] / ret_now.loc[common] - 1.0

        # rankIC
        ic = compute_rank_ic(factor_vals.loc[common], fwd_ret)
        if ic is not None:
            ic_records.append({'date': d, 'ic': ic})

        # 五分位分组
        ranked = factor_vals.loc[common].rank(pct=True)
        for q in range(1, 6):
            q_lo = (q - 1) / 5
            q_hi = q / 5
            mask = (ranked > q_lo) & (ranked <= q_hi)
            if mask.any():
                q_ret = fwd_ret.loc[mask.values].mean()
                if not np.isnan(q_ret):
                    quintile_returns[q].append(q_ret)

        # 多空 = Q5 - Q1
        r5 = quintile_returns[5][-1] if quintile_returns[5] else None
        r1 = quintile_returns[1][-1] if quintile_returns[1] else None
        if r5 is not None and r1 is not None:
            long_short.append(r5 - r1)

    return ic_records, quintile_returns, long_short


def main():
    t_start = time.time()
    log("======== A1+A2 因子 IC Kill Test (通宵批次 T-20260817-004) ========")
    log("运行时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))

    # 1. 加载数据
    panel = load_daily()
    universe_map, snap_dates = build_universe_by_date(panel)
    dy_wide = build_div_yield_ttm(panel)

    # 2. 月度调仓日
    trade_dates = sorted(panel.index.get_level_values('trade_date').unique())
    monthly = pd.DatetimeIndex(trade_dates).to_period('M').drop_duplicates()
    rebal_dates = []
    for p in monthly:
        candidates = [d for d in trade_dates if pd.Timestamp(d).to_period('M') == p]
        if candidates:
            rebal_dates.append(candidates[0])

    log("月度调仓日: %d" % len(rebal_dates))

    # 3. 定义因子函数
    def bp_factor(d, dd, codes):
        """BP = 1/pb (当日截面)"""
        try:
            pb = dd.loc[codes, 'pb']
        except KeyError:
            pb = dd.reindex(codes)['pb']
        pb = pb.replace(0, np.nan)
        bp = 1.0 / pb
        return bp.dropna()

    def div_yield_factor(d, dd, codes):
        """股息率 TTM (除息日口径)"""
        dts = pd.Timestamp(d)
        if dts not in dy_wide.index:
            return None
        dy_row = dy_wide.loc[dts]
        vals = dy_row.reindex(codes)
        return vals.dropna()

    # 4. 运行所有域×因子组合
    domains = {
        'top500': '大盘域(PIT circ_mv前500)',
        'top1000': '大盘域(PIT circ_mv前1000)',
        'bot30': '小盘域(后30%, 对照)',
    }
    factors = {
        'BP': ('BP(1/pb)', bp_factor),
        'DIV': ('股息率TTM', div_yield_factor),
    }

    all_results = {}

    for domain_key, domain_name in domains.items():
        # 构建该域的universe_map
        domain_universe = {}
        for d, entry in universe_map.items():
            if domain_key in entry:
                domain_universe[d] = entry[domain_key]
        if len(domain_universe) < 12:
            log("域 %s 快照不足12, 跳过" % domain_key)
            continue

        for factor_key, (factor_name, factor_fn) in factors.items():
            combo_name = "%s_%s" % (factor_key, domain_key)
            log("运行 %s: %s × %s ..." % (combo_name, domain_name, factor_name))
            t1 = time.time()
            ic_recs, q_rets, ls = run_ic_test(panel, domain_universe, factor_name, factor_fn, rebal_dates)
            elapsed = time.time() - t1

            ic_ser = pd.Series([r['ic'] for r in ic_recs],
                               index=pd.DatetimeIndex([r['date'] for r in ic_recs]))

            # 分段统计
            full = ic_stats(ic_ser)
            early = ic_stats(ic_ser[ic_ser.index < '2023-01-01']) if len(ic_ser) > 0 else None
            late = ic_stats(ic_ser[ic_ser.index >= '2023-01-01']) if len(ic_ser) > 0 else None

            all_results[combo_name] = {
                'domain': domain_name,
                'factor': factor_name,
                'ic_series': ic_ser,
                'full': full,
                'early': early,
                'late': late,
                'quintile': q_rets,
                'long_short': ls,
            }
            log("  IC均值=%.4f ICIR=%.3f n=%d 用时%.0fs" % (
                full['ic_mean'] if full else 0,
                full['icir'] if full else 0,
                full['n'] if full else 0,
                elapsed))

    # 5. 生成报告
    lines = []
    lines.append("# A1+A2 因子 IC Kill Test（果仁 P0/P1 验证）\n")
    lines.append("> 通宵批次任务书 T-20260817-004")
    lines.append("> 运行时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("> 数据: E:/astock, PIT circ_mv 域快照, 月频 rankIC")
    lines.append("> 区间: %s ~ %s\n" % (START, END))

    # 数据预检结果
    lines.append("## T0 数据预检\n")
    lines.append("- dividend.parquet: 有 ex_date/ann_date/cash_div_tax, PIT安全可行")
    lines.append("- 无 base_share/cash_dv, 用 cash_div_tax 代替(与P10同口径)")
    lines.append("- BP: daily pb列, BP=1/pb; fina_indicator有bps+ann_date做PIT交叉")
    lines.append("- circ_mv: 每日可得, Top500门槛约230-350亿(年度波动)")
    lines.append("- circ_mv PIT季度快照2019-2025全期可得(2026-01-02空窗,不影响2019-2026主区间)\n")

    # IC汇总表
    lines.append("## 一、rankIC 汇总\n")
    lines.append("| 组合 | 域 | 因子 | 全样本IC | 全样本ICIR | 全样本正值% | 2019-2022 IC | 2023-2026 IC | n(全) |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for combo, res in sorted(all_results.items()):
        f = res['full'] or {}
        e = res['early'] or {}
        l = res['late'] or {}
        lines.append("| %s | %s | %s | %.4f | %.3f | %.0f%% | %.4f | %.4f | %d |" % (
            combo, res['domain'], res['factor'],
            f.get('ic_mean', 0), f.get('icir', 0), f.get('positive_pct', 0) * 100,
            e.get('ic_mean', 0), l.get('ic_mean', 0), f.get('n', 0)))

    # 判定
    lines.append("\n## 二、预注册判定\n")

    # A1 BP判定
    bp500 = all_results.get('BP_top500', {})
    bp500_late = bp500.get('late', {})
    bp500_full = bp500.get('full', {})
    lines.append("### A1 大盘域 BP Kill Test\n")
    lines.append("判定规则: 主口径(top500)全样本 rankIC均值≥0.03 且 2023-2026段≥0.02 且全样本ICIR≥0.3")
    a1_ic = bp500_full.get('ic_mean', 0) if bp500_full else 0
    a1_late = bp500_late.get('ic_mean', 0) if bp500_late else 0
    a1_ir = bp500_full.get('icir', 0) if bp500_full else 0
    c1 = a1_ic >= 0.03
    c2 = a1_late >= 0.02
    c3 = a1_ir >= 0.3
    lines.append("- 全样本 rankIC均值 = %.4f (≥0.03? %s)" % (a1_ic, "PASS" if c1 else "FAIL"))
    lines.append("- 2023-2026段 rankIC = %.4f (≥0.02? %s)" % (a1_late, "PASS" if c2 else "FAIL"))
    lines.append("- 全样本 ICIR = %.3f (≥0.3? %s)" % (a1_ir, "PASS" if c3 else "FAIL"))
    a1_pass = c1 and c2 and c3
    lines.append("**A1判定: %s** → %s\n" % (
        "PASS(A3放行)" if a1_pass else "FAIL(归档,果仁P0关闭)",
        "BP在大盘域有预测力" if a1_pass else "BP在大盘域无预测力"))

    # A-BP锚检查
    bp_bot30 = all_results.get('BP_bot30', {})
    bp_bot30_full = bp_bot30.get('full', {})
    bp_bot30_ic = bp_bot30_full.get('ic_mean', 0) if bp_bot30_full else 0
    lines.append("### 锚 A-BP量级\n")
    lines.append("小盘域BP对照组 rankIC = %.4f (应落0.05~0.10)" % bp_bot30_ic)
    anchor_bp_pass = 0.05 <= bp_bot30_ic <= 0.10
    lines.append("锚检查: %s\n" % ("PASS" if anchor_bp_pass else "WARN-出区间,查因子构造/宇宙截断"))

    # A2 股息率判定
    div500 = all_results.get('DIV_top500', {})
    div500_late = div500.get('late', {})
    lines.append("### A2 大盘域股息率 Kill Test\n")
    lines.append("判定规则: 2023-2026段 rankIC≥0.02 且五分位单调(Q5>Q3>Q1)")
    a2_late = div500_late.get('ic_mean', 0) if div500_late else 0
    lines.append("- 2023-2026段 rankIC = %.4f (≥0.02? %s)" % (a2_late, "PASS" if a2_late >= 0.02 else "FAIL"))

    # 五分位单调检查
    q5 = all_results.get('DIV_top500', {}).get('quintile', {})
    q5_avg = {q: np.mean(rets) if rets else 0 for q, rets in q5.items()}
    monotone = q5_avg.get(5, 0) > q5_avg.get(3, 0) > q5_avg.get(1, 0)
    lines.append("- 五分位均值: Q1=%.2f%% Q3=%.2f%% Q5=%.2f%% (单调Q5>Q3>Q1? %s)" % (
        q5_avg.get(1, 0) * 100, q5_avg.get(3, 0) * 100, q5_avg.get(5, 0) * 100,
        "PASS" if monotone else "FAIL"))
    a2_pass = a2_late >= 0.02 and monotone
    lines.append("**A2判定: %s** → %s\n" % (
        "PASS(A3放行)" if a2_pass else "FAIL(归档)",
        "股息率在大盘域有预测力" if a2_pass else "股息率在大盘域无预测力"))

    # A-股息锚检查
    div_full = all_results.get('DIV_top500', {}).get('full', {})
    div_full_ic = div_full.get('ic_mean', 0) if div_full else 0
    lines.append("### 锚 A-股息量级\n")
    lines.append("股息率全样本 rankIC = %.4f (应落0.00~0.08)" % div_full_ic)
    anchor_div_pass = 0.0 <= div_full_ic <= 0.08
    lines.append("锚检查: %s\n" % ("PASS" if anchor_div_pass else "WARN-出区间,查构造"))

    # 五分位分组明细
    lines.append("## 三、五分位分组明细\n")
    for combo, res in sorted(all_results.items()):
        q = res.get('quintile', {})
        if not q:
            continue
        lines.append("### %s (%s × %s)\n" % (combo, res['domain'], res['factor']))
        lines.append("| 分位 | 月均收益 | 样本月数 |")
        lines.append("|---|---|---|")
        for qi in range(1, 6):
            rets = q.get(qi, [])
            if rets:
                lines.append("| Q%d(低) | %.3f%% | %d |" % (qi, np.mean(rets) * 100, len(rets)))
            else:
                lines.append("| Q%d | — | 0 |" % qi)
        ls = res.get('long_short', [])
        if ls:
            lines.append("| 多空(Q5-Q1) | %.3f%% | %d |\n" % (np.mean(ls) * 100, len(ls)))

    # 多空累计净值
    lines.append("## 四、多空累计净值曲线（数值表）\n")
    for combo, res in sorted(all_results.items()):
        ls = res.get('long_short', [])
        if not ls:
            continue
        nav = np.cumprod([1 + r for r in ls])
        # 只输出首/尾/关键点
        lines.append("### %s\n" % combo)
        lines.append("- 起始=1.0, 终值=%.3f, 月均多空=%.3f%%" % (nav[-1], np.mean(ls) * 100))

    # 总用时
    lines.append("\n## 五、执行信息\n")
    lines.append("- 总用时: %.0fs" % (time.time() - t_start))
    lines.append("- Python: %s" % sys.executable)

    report_text = "\n".join(lines)

    # 写报告
    out_path = os.path.join(OUT_DIR, "A1A2_BP_DIV_KillTest_20260818.md")
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    os.replace(tmp_path, out_path)
    log("报告: %s" % out_path)

    # 汇总报告也放 D:/QuantLab/reports/
    rpt_dir = r"D:\QuantLab\reports"
    os.makedirs(rpt_dir, exist_ok=True)
    rpt_path = os.path.join(rpt_dir, "A1A2_BP_DIV_KillTest_20260818.md")
    with open(rpt_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    # 也在控制台打印判定结论
    log("\n======== 判定结论 ========")
    log("A1 BP(top500): %s (IC=%.4f, late=%.4f, ICIR=%.3f)" % (
        "PASS" if a1_pass else "FAIL", a1_ic, a1_late, a1_ir))
    log("A2 DIV(top500): %s (late_IC=%.4f, 单调=%s)" % (
        "PASS" if a2_pass else "FAIL", a2_late, monotone))
    log("锚 A-BP(bot30): %.4f (%s)" % (bp_bot30_ic, "OK" if anchor_bp_pass else "WARN"))
    log("锚 A-股息(top500全样本): %.4f (%s)" % (div_full_ic, "OK" if anchor_div_pass else "WARN"))

    log("\n总用时 %.0fs" % (time.time() - t_start))


if __name__ == "__main__":
    main()
