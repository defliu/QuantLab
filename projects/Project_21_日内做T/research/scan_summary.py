"""Phase 1 full-scan summary: aggregate the six annual signal_scan parquets.

Read-only, one year at a time (peak ~1GB RAM). Writes
results/scan_summary_phase1.md and prints the same table.

Descriptive stats only -- NOT the M1 Go/No-Go evaluation (that needs
forward returns and the locked criteria in config/falsification_criteria.yaml).
"""
import sys
import pandas as pd

YEARS = ['2019', '2020', '2021', '2022', '2023', '2024']
COLS = ['family', 'signal_type', 'trade_date', 'ts_code', 'direction',
        'shares', 'pnl_bps', 'cost_bps', 'holding_bars']
# pre-registered extreme days (locked in specs/证伪线预注册锁定_20260928.md)
EXTREME = [d for d in
           ['20200203', '20200204', '20200205', '20200206', '20200207',
            '20220425', '20220426', '20220427', '20220428', '20220429',
            '20240205', '20240206']]


def one_year(path, year):
    df = pd.read_parquet(path, columns=COLS)
    n = len(df)
    stocks = df['ts_code'].nunique()
    days = df['trade_date'].nunique()
    stock_days = df.groupby(['ts_code', 'trade_date'], observed=True).size()
    zero_sh = int((df['shares'] <= 0).sum())
    ext = df['trade_date'].isin(EXTREME)
    fam = df.groupby('family', observed=True).agg(
        events=('pnl_bps', 'size'),
        win_pct=('pnl_bps', lambda s: 100.0 * (s > 0).mean()),
        mean_pnl_bps=('pnl_bps', 'mean'),
        med_pnl_bps=('pnl_bps', 'median'),
        mean_cost_bps=('cost_bps', 'mean'),
        mean_hold_bars=('holding_bars', 'mean'),
    ).sort_values('events', ascending=False)
    stat = dict(
        year=year, rows=n, stocks=stocks, days=days,
        ev_per_stock_day=n / max(len(stock_days), 1),
        ev_per_100_day=100.0 * n / max(stocks * days, 1),
        zero_share_pct=100.0 * zero_sh / max(n, 1),
        extreme_pct=100.0 * ext.sum() / max(n, 1),
        neg_dir_pct=100.0 * (df['direction'] == -1).mean(),
        mean_pnl=df['pnl_bps'].mean(), med_pnl=df['pnl_bps'].median(),
        win_pct=100.0 * (df['pnl_bps'] > 0).mean(),
    )
    fam.insert(0, 'year', stat['year'])
    del df, stock_days
    return stat, fam.reset_index()


def main():
    stats, fams = [], []
    for y in YEARS:
        p = 'results/signal_scan_{0}0101_{0}1231.parquet'.format(y)
        s, f = one_year(p, y)
        stats.append(s)
        fams.append(f)
        print('{0}: {1:,} events / {2} stocks / {3} days'.format(
            s['year'], s['rows'], s['stocks'], s['days']))
    st = pd.DataFrame(stats)
    fa = pd.concat(fams, ignore_index=True)

    lines = []
    w = lines.append
    w('# Phase 1 全量扫描汇总（2019-2024，F1-F6）')
    w('')
    w('- 生成: `research/scan_summary.py`；产物: 六个 `results/signal_scan_YYYY0101_YYYY1231.parquet`')
    w('- 描述性统计，**非 M1 Go/No-Go 裁决**（M1 需前向收益 + `config/falsification_criteria.yaml` 锁定判据）')
    w('- 极端日 = 预注册剔除清单 2020-02-03~07 / 2022-04-25~29 / 2024-02-05~06')
    w('')
    w('## 逐年')
    w('')
    w('| 年 | 事件数 | 股票数 | 交易日 | 每股日均 | 每百票日均 | 零股跳过% | 极端日% | 方向-1% | 胜率% | 均pnl bps | 中位pnl bps |')
    w('|---|---|---|---|---|---|---|---|---|---|---|---|')
    for _, r in st.iterrows():
        w('| {0} | {1:,} | {2} | {3} | {4:.2f} | {5:.2f} | {6:.2f} | {7:.2f} | {8:.1f} | {9:.2f} | {10:+.1f} | {11:+.1f} |'.format(
            r['year'], r['rows'], r['stocks'], r['days'], r['ev_per_stock_day'],
            r['ev_per_100_day'], r['zero_share_pct'], r['extreme_pct'],
            r['neg_dir_pct'], r['win_pct'], r['mean_pnl'], r['med_pnl']))
    tot = st['rows'].sum()
    w('| **合计** | **{0:,}** | - | - | - | - | - | - | - | - | - | - |'.format(tot))
    w('')
    w('## 按族（全期合计）')
    w('')
    fam_all = fa.groupby('family', observed=True).agg(
        events=('events', 'sum'), win_pct=('win_pct', 'mean'),
        mean_pnl_bps=('mean_pnl_bps', 'mean')).sort_values('events', ascending=False)
    w('| 族 | 事件数 | 占比% | 年均胜率% | 年均pnl bps |')
    w('|---|---|---|---|---|')
    for name, r in fam_all.iterrows():
        w('| {0} | {1:,} | {2:.1f} | {3:.2f} | {4:+.1f} |'.format(
            name, int(r['events']), 100.0 * r['events'] / tot,
            r['win_pct'], r['mean_pnl_bps']))
    w('')
    w('## 逐年 × 族（事件数）')
    w('')
    piv = fa.pivot_table(index='family', columns='year', values='events',
                         aggfunc='sum', fill_value=0)
    w('| 族 | ' + ' | '.join(YEARS) + ' |')
    w('|---|' + '---|' * len(YEARS))
    for name, r in piv.iterrows():
        w('| {0} | '.format(name) + ' | '.join('{0:,}'.format(int(r[y])) for y in YEARS) + ' |')
    w('')
    txt = '\n'.join(lines)
    with open('results/scan_summary_phase1.md', 'w', encoding='utf-8') as fh:
        fh.write(txt + '\n')
    print(txt)


if __name__ == '__main__':
    sys.exit(main() or 0)
