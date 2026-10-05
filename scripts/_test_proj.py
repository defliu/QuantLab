# coding=utf-8
"""隔离验证：日线 is_st/listed_days 补洞的键列投影是否可用。

只读 D:\astock\daily\stock_daily.parquet，不写任何文件。
"""
import sys
import pandas as pd
import pyarrow.dataset as pads
import pyarrow.compute as pc

sys.path.insert(0, r'D:\QuantLab\scripts')
import migrate_astock_full_v2 as M

D = r'D:\astock\daily\stock_daily.parquet'
DATE_COL = M.DAILY_DATE_COL
KEYS = M.DAILY_KEYS
VALS = M.DAILY_VALUE_COLS

print('DATE_COL=%s  KEYS=%s' % (DATE_COL, KEYS))

# --- 旧写法（预期报错）：投影里没有 trade_date ---
old_cols = [c for c in VALS if c in pads.dataset(D, format='parquet').schema.names]
try:
    ed = pads.dataset(D, format='parquet')
    cols = [c for c in VALS if c in ed.schema.names]
    s = ed.to_table(columns=cols + ['ts_code'],
                    filter=pc.field(DATE_COL) >= pd.Timestamp('20260101')).to_pandas()
    M.to_flat(s, KEYS)
    print('旧写法: 竟然成功了（需复查）')
except Exception as exc:
    print('旧写法: 如期失败 -> %s: %s' % (type(exc).__name__, exc))

# --- 新写法（本次修复）---
ed = pads.dataset(D, format='parquet')
cols = [c for c in VALS if c in ed.schema.names]
proj = [DATE_COL] + [c for c in cols if c != DATE_COL] + ['ts_code']
print('投影列(%d): %s' % (len(proj), proj))
sub = ed.to_table(columns=proj,
                  filter=pc.field(DATE_COL) >= pd.Timestamp('20260101')).to_pandas()
flat = M.to_flat(sub, KEYS)
print('新写法: OK  raw=%d  flat=%d  列=%d' % (len(sub), len(flat), flat.shape[1]))
print('键列在位: %s' % all(k in flat.columns for k in KEYS))
print('日期范围: %s ~ %s' % (flat[KEYS[0]].min(), flat[KEYS[0]].max()))

for c in M.DAILY_NULL_COLS:
    if c in flat.columns:
        s = flat[flat[KEYS[0]] >= pd.Timestamp('20260901')]
        print('  %s: 总空=%d  202609起空=%d'
              % (c, int(flat[c].isna().sum()), int(s[c].isna().sum())))