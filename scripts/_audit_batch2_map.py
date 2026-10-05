# coding=utf-8
"""为 8 个疑似丢失的下游因子字段，在 E 侧找同族承接列。

方法：对每个目标列 t，在 E 侧候选列里找与 D 的 t 数值高度相关的列。
  判据：双方都非空的行上，corr 高 + 符号一致 + 值域重叠。
只读，不写生产文件。
"""
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

E_ROOT = r'E:\temp\行情数据'
E_FI = os.path.join(E_ROOT, 'fina_indicator.parquet')
D_FI = r'D:\astock\finance\fina_indicator.parquet'

TARGETS = ['q_gsprofit_margin', 'q_netprofit_margin', 'q_sales_qoq',
           'q_netprofit_qoq', 'q_dtprofit_to_profit',
           'dtprofit_to_profit', 'ocf_to_profit', 'ocf_to_or']

KEYS = ['ts_code', 'end_date', 'ann_date']

# 同族语义候选（按 Tushare 字段族命名习惯预置，再由相关性验证）
HINT = {
    'q_gsprofit_margin': ['gsprofit_margin', 'q_gsprofit_margin', 'grossprofit_margin'],
    'q_netprofit_margin': ['netprofit_margin', 'q_netprofit_margin', 'npm'],
    'q_sales_qoq': ['sales_qoq', 'q_sales_qoq', 'or_qoq', 'q_or_qoq', 'revenue_qoq'],
    'q_netprofit_qoq': ['netprofit_qoq', 'q_netprofit_qoq', 'np_qoq'],
    'q_dtprofit_to_profit': ['dtprofit_to_profit', 'q_dtprofit_to_profit'],
    'dtprofit_to_profit': ['dtprofit_to_profit', 'dtp_to_profit'],
    'ocf_to_profit': ['ocf_to_profit', 'ocfps_to_profit', 'q_ocf_to_profit'],
    'ocf_to_or': ['ocf_to_or', 'ocf_to_revenue', 'q_ocf_to_or'],
}


def num(a):
    return pd.to_numeric(a, errors='coerce').astype('float64')


print('=' * 78)
print('载入 E/D fina_indicator（只取键 + 目标列 + E 数值列）')
print('=' * 78)

pf_e = pq.ParquetFile(E_FI)
e_names = pf_e.schema_arrow.names
pf_d = pq.ParquetFile(D_FI)
d_names = set(pf_d.schema_arrow.names)

tgt_in_d = [t for t in TARGETS if t in d_names]
tgt_in_e = [t for t in TARGETS if t in e_names]
print('目标列 %d 个；E 侧存在 %d 个，D 侧存在 %d 个'
      % (len(TARGETS), len(tgt_in_e), len(tgt_in_d)))

# 候选：E 侧全部数值列（排除键列）
# 注意：__index_level_* 是 pandas 落盘时的物理索引层名，
# to_pandas() 会把它还原成索引而不是普通列，必须排除，
# 否则后续 E.loc[common, c] 必然 KeyError。
cand = [c for c in e_names
        if c not in KEYS
        and c not in tgt_in_e
        and not c.startswith('__index_level_')]
print('E 侧候选数值列 %d 个（已排除 %d 个 pandas 物理索引层）'
      % (len(cand), sum(1 for c in e_names
                        if c.startswith('__index_level_'))))

ecols = list(dict.fromkeys(KEYS + tgt_in_e + cand))
E = pf_e.read(columns=ecols).to_pandas()
D = pf_d.read(columns=KEYS + tgt_in_d).to_pandas()
print('E %d 行 / D %d 行' % (len(E), len(D)))

# 防御性校验：读回来后必须是普通列，否则说明还有别的索引层被误当候选
lost = [c for c in cand if c not in E.columns]
if lost:
    raise RuntimeError('候选列在 to_pandas 后不再是普通列: %s' % lost[:20])
lost_t = [c for c in tgt_in_e if c not in E.columns]
if lost_t:
    raise RuntimeError('目标列在 to_pandas 后不再是普通列: %s' % lost_t)

E = E.drop_duplicates(subset=KEYS, keep='last')
D = D.drop_duplicates(subset=KEYS, keep='last')
print('按 FULL KEY 去重后：E %d 行 / D %d 行' % (len(E), len(D)))

E = E.set_index(KEYS).sort_index()
D = D.set_index(KEYS).sort_index()
common = E.index.intersection(D.index)
print('共同 FULL KEY：%d' % len(common))

print()
print('=' * 78)
print('逐目标列找 E 侧承接列')
print('=' * 78)

result = {}
for t in tgt_in_d:
    dt = num(D.loc[common, t])
    mask_d = dt.notna()
    if mask_d.sum() < 50:
        print('\n[%s] D 非空仅 %d 行，跳过' % (t, int(mask_d.sum())))
        continue
    rows = []
    for c in cand:
        ev = num(E.loc[common, c])
        both = mask_d & ev.notna()
        n = int(both.sum())
        if n < 200:
            continue
        dv = dt[both].to_numpy()
        av = ev[both].to_numpy()
        if not np.isfinite(dv).all() or not np.isfinite(av).all():
            continue
        sd = dv.std()
        sa = av.std()
        if sd == 0 or sa == 0:
            continue
        # 值域相似度（防止量纲不同的列被误判）
        rng = min(sd / sa, sa / sd) if sa > 0 else 0.0
        cc = float(np.corrcoef(dv, av)[0, 1])
        if not np.isfinite(cc):
            continue
        if cc >= 0.90 and rng >= 0.5:
            rows.append((c, n, cc, rng))
    rows.sort(key=lambda x: (-x[2], -x[3]))
    hinted = set(HINT.get(t, []))
    print('\n[%s]  D非空=%d' % (t, int(mask_d.sum())))
    if not rows:
        print('    未找到强相关承接列（corr>=0.90 且量纲相近）')
    for c, n, cc, rng in rows[:6]:
        mark = '  <== 预置同族名' if c in hinted else ''
        print('    %-30s 重合=%-8d corr=%.4f 量纲比=%.2f%s'
              % (c, n, cc, rng, mark))
    result[t] = [(c, n, round(cc, 4), round(rng, 2)) for c, n, cc, rng in rows[:6]]

print()
print('=' * 78)
print('汇总：8 个字段的承接结论')
print('=' * 78)
for t in TARGETS:
    r = result.get(t) or []
    if r:
        print('%-24s -> %s' % (t, r[0][0]))
    else:
        print('%-24s -> 【未找到承接列，需人工定规则】' % t)
