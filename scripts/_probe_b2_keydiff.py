# coding=utf-8
"""stock_daily 三方键级差异审计（只读）。

FULL(供应商所谓全量) / INCR(供应商增量) / MAIN(D盘生产主库)
输出：
  1. 三方行数、唯一键数、重复键数
  2. FULL vs MAIN 键集合三分类：共有 / 仅MAIN / 仅FULL
  3. 仅MAIN 的 27562 行按 trade_date、ts_code 分布
  4. INCR 能否解释 MAIN 独有键
  5. 共有键是否有数值冲突
"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

FULL = r'E:\temp\行情数据\个股行情全量\stock_daily_full.parquet'
INCR = r'E:\temp\行情数据\stock_daily.parquet'
MAIN = r'D:\astock\daily\stock_daily.parquet'

KEYS = ['ts_code', 'trade_date']


def hr(t):
    print('')
    print('=' * 70)
    print(t)
    print('=' * 70)


def load_keys(path, tag):
    """只读 ts_code/trade_date 两列，返回规范化后的 DataFrame。"""
    if not os.path.exists(path):
        print('%-10s 不存在: %s' % (tag, path))
        return None
    f = pq.ParquetFile(path)
    t = f.read(columns=KEYS).to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    t['trade_date'] = pd.to_datetime(
        t['trade_date'], format='mixed', errors='coerce'
    )
    n_raw = len(t)
    n_bad = int(t['trade_date'].isna().sum())
    n_dup = int(t.duplicated(subset=KEYS).sum())
    u = t.drop_duplicates(subset=KEYS, keep='first').copy()
    print(
        '%-10s 原始行=%9d  唯一键=%9d  重复键=%6d  坏日期=%5d  '
        '%.2f MiB'
        % (
            tag,
            n_raw,
            len(u),
            n_dup,
            n_bad,
            os.path.getsize(path) / 1024.0 / 1024.0
        )
    )
    return u


def mib(n):
    return n / 1024.0 / 1024.0


def main():
    hr('1. 规模与唯一键')
    fu = load_keys(FULL, 'FULL')
    iu = load_keys(INCR, 'INCR')
    mu = load_keys(MAIN, 'MAIN')
    if fu is None or mu is None:
        print('关键文件缺失，终止')
        return 1

    hr('2. FULL vs MAIN 键集合三分类')
    fk = set(map(tuple, fu[KEYS].values))
    mk = set(map(tuple, mu[KEYS].values))
    both = fk & mk
    only_m = mk - fk
    only_f = fk - mk
    print('共有键            %9d' % len(both))
    print('仅 MAIN 有(FULL缺) %9d' % len(only_m))
    print('仅 FULL 有(MAIN缺) %9d' % len(only_f))
    print(
        '净差 MAIN-FULL    %9d  (原始行数差 %d)'
        % (len(mk) - len(fk), 0)
    )

    hr('3. 仅 MAIN 独有的键 —— 按 trade_date 分布 (Top 20)')
    if only_m:
        om = pd.DataFrame(sorted(only_m), columns=KEYS)
        vc = om['trade_date'].value_counts().sort_index()
        print('涉及交易日数: %d' % len(vc))
        print('')
        print('%-12s %8s %10s' % ('trade_date', '行数', '累计占比'))
        cum = 0.0
        for d, n in vc.items():
            cum += n
            print(
                '%-12s %8d %9.2f%%'
                % (str(pd.Timestamp(d).date()), n, cum / len(om) * 100)
            )
        print('')
        print('涉及股票数: %d' % om['ts_code'].nunique())
        print('每只股票平均缺失天数: %.2f' % (len(om) / om['ts_code'].nunique()))
        print('')
        print('缺失最多的 15 只股票:')
        tvc = om['ts_code'].value_counts().head(15)
        for c, n in tvc.items():
            sub = om[om['ts_code'] == c]['trade_date']
            print(
                '  %-10s %5d 天   %s ~ %s'
                % (
                    c,
                    n,
                    str(pd.Timestamp(sub.min()).date()),
                    str(pd.Timestamp(sub.max()).date())
                )
            )
        print('')
        print('按年份分布:')
        om['yr'] = pd.to_datetime(om['trade_date']).dt.year
        yv = om['yr'].value_counts().sort_index()
        for y, n in yv.items():
            print('  %d  %7d  %5.2f%%' % (y, n, n / len(om) * 100))

    hr('4. 仅 FULL 独有的键 —— 按 trade_date 分布 (Top 20)')
    if only_f:
        of = pd.DataFrame(sorted(only_f), columns=KEYS)
        vc2 = of['trade_date'].value_counts().sort_index()
        print('涉及交易日数: %d  涉及股票数: %d'
              % (len(vc2), of['ts_code'].nunique()))
        for d, n in vc2.head(20).items():
            print('  %-12s %6d' % (str(pd.Timestamp(d).date()), n))
    else:
        print('无 —— MAIN 完全覆盖 FULL 的键集合')

    hr('5. INCR 能否解释 MAIN 独有键')
    if iu is not None and only_m:
        ik = set(map(tuple, iu[KEYS].values))
        print('INCR 唯一键: %d  日期范围: %s ~ %s'
              % (len(ik),
                 str(pd.Timestamp(iu["trade_date"].min()).date()),
                 str(pd.Timestamp(iu["trade_date"].max()).date())))
        cov = only_m & ik
        print('MAIN 独有键 中被 INCR 覆盖: %d / %d  (%.2f%%)'
              % (len(cov), len(only_m), len(cov) / len(only_m) * 100))
        rest = only_m - ik
        print('MAIN 独有键 中 INCR 无法解释: %d' % len(rest))
        if rest:
            rr = pd.DataFrame(sorted(rest), columns=KEYS)
            print('  涉及股票数: %d' % rr['ts_code'].nunique())
            rc = rr['trade_date'].value_counts().sort_index()
            print('  涉及交易日数: %d' % len(rc))
            print('  日期范围: %s ~ %s'
                  % (str(pd.Timestamp(rr["trade_date"].min()).date()),
                     str(pd.Timestamp(rr["trade_date"].max()).date())))
            print('  明细(最多30行):')
            for _, r in rr.head(30).iterrows():
                print('    %-10s %s' % (r['ts_code'],
                                       str(pd.Timestamp(r['trade_date']).date())))

    hr('6. 共有键数值冲突检查（收盘价/成交量）')
    cm = ['ts_code', 'trade_date', 'close', 'vol', 'amount']
    fs = pq.ParquetFile(FULL).schema_arrow.names
    use = [c for c in cm if c in fs]
    print('使用列: %s' % use)
    f2 = pq.ParquetFile(FULL).read(columns=use).to_pandas()
    d2 = pq.ParquetFile(MAIN).read(columns=use).to_pandas()
    for d in (f2, d2):
        if isinstance(d.index, pd.MultiIndex):
            d = d.reset_index()
        d['trade_date'] = pd.to_datetime(
            d['trade_date'], format='mixed', errors='coerce'
        )
    f2 = f2.drop_duplicates(subset=KEYS, keep='first')
    d2 = d2.drop_duplicates(subset=KEYS, keep='first')
    mg = f2.merge(d2, on=KEYS, how='inner', suffixes=('_f', '_d'))
    print('可比共有键: %d' % len(mg))
    for c in use[2:]:
        a = pd.to_numeric(mg[c + '_f'], errors='coerce')
        b = pd.to_numeric(mg[c + '_d'], errors='coerce')
        both2 = a.notna() & b.notna()
        n_both = int(both2.sum())
        if n_both == 0:
            print('  %-8s 无共同非空值' % c)
            continue
        av, bv = a[both2], b[both2]
        scale = bv.abs().median()
        if scale is None or pd.isna(scale) or scale == 0:
            scale = 1.0
        absdiff = (av - bv).abs()
        print('  %-8s 共同非空=%9d  完全相同=%6.2f%%  '
              '中位绝对差=%.6g  最大绝对差=%.6g'
              % (c, n_both,
                 float((absdiff == 0).mean() * 100),
                 float(absdiff.median()),
                 float(absdiff.max())))

    hr('结论输入')
    print('FULL 唯一键   : %d' % len(fk))
    print('MAIN 唯一键   : %d' % len(mk))
    print('FULL 缺失键   : %d' % len(only_m))
    print('FULL 多余键   : %d' % len(only_f))
    return 0


if __name__ == '__main__':
    sys.exit(main())
