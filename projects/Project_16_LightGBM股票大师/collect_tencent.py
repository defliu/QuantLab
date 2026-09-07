# -*- coding: utf-8 -*-
import urllib.request
import json
import time
import os

os.makedirs('data/cache', exist_ok=True)

positions = {
    '003005.SZ': {'vol': 2300, 'cost': 19.10, 'sellable': 2300},
    '300413.SZ': {'vol': 2000, 'cost': 21.99, 'sellable': 2000},
    '601999.SH': {'vol': 6600, 'cost': 6.90, 'sellable': 0},
    '601579.SH': {'vol': 1900, 'cost': 23.97, 'sellable': 0},
}

codes = list(positions.keys())
tc_codes = []
for code in codes:
    if code.endswith('.SZ'):
        tc_codes.append('sz' + code[:-3])
    elif code.endswith('.SH'):
        tc_codes.append('sh' + code[:-3])

start = time.time()
url = 'http://qt.gtimg.cn/q=' + ','.join(tc_codes)
req = urllib.request.Request(url)
response = urllib.request.urlopen(req, timeout=10)
content = response.read().decode('gbk')
latency = int((time.time() - start) * 1000)

data = {}
for line in content.strip().split('\n'):
    if '=' not in line:
        continue
    raw = line.split('=')[1].strip('"')
    fields = raw.split('~')
    if len(fields) < 50:
        continue
    name = fields[1]
    code_raw = fields[2]
    if code_raw.startswith('0') or code_raw.startswith('3'):
        code = code_raw + '.SZ'
    else:
        code = code_raw + '.SH'
    cur_price = float(fields[3]) if fields[3] else 0
    prev_close = float(fields[4]) if fields[4] else 0
    open_price = float(fields[5]) if fields[5] else 0
    vol = int(fields[6]) if fields[6] else 0
    high = float(fields[33]) if fields[33] else 0
    low = float(fields[34]) if fields[34] else 0
    pe = float(fields[39]) if len(fields) > 39 and fields[39] else 0
    turnover = float(fields[38]) if len(fields) > 38 and fields[38] else 0
    
    pct_chg = (cur_price - prev_close) / prev_close * 100 if prev_close > 0 else 0
    
    data[code] = {
        'name': name,
        'price': cur_price,
        'prev_close': prev_close,
        'open': open_price,
        'high': high,
        'low': low,
        'vol': vol,
        'pe': pe,
        'turnover': turnover,
        'pct_chg': pct_chg,
        'source': 'tencent'
    }

print('TENCENT_OK latency=%dms' % latency)
for code, info in data.items():
    pos = positions.get(code, {})
    cost = pos.get('cost', 0)
    vol = pos.get('vol', 0)
    float_pnl = (info['price'] - cost) * vol if cost > 0 else 0
    float_pnl_pct = (info['price'] - cost) / cost * 100 if cost > 0 else 0
    limit_up = info['prev_close'] * 1.10 if info['prev_close'] > 0 else 0
    limit_down = info['prev_close'] * 0.90 if info['prev_close'] > 0 else 0
    is_limit_up = abs(info['price'] - limit_up) < 0.01 if limit_up > 0 else False
    is_limit_down = abs(info['price'] - limit_down) < 0.01 if limit_down > 0 else False
    print('%s %s: price=%.2f close=%.2f chg=%.2f%% high=%.2f low=%.2f vol=%d turnover=%.2f%% pe=%.1f' % (
        code, info['name'], info['price'], info['prev_close'], info['pct_chg'],
        info['high'], info['low'], info['vol'], info['turnover'], info['pe']))
    print('  cost=%.2f vol=%d float_pnl=%.0f(%.1f%%) limit_up=%.2f limit_down=%.2f limit_up=%s limit_down=%s' % (
        cost, vol, float_pnl, float_pnl_pct, limit_up, limit_down, str(is_limit_up), str(is_limit_down)))

with open('data/cache/tencent_quotes.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print('Saved to data/cache/tencent_quotes.json')

# Record success
import sys
sys.path.append('.')
from scripts.data_source_router import record
record('tencent', True, latency)
print('recorded tencent ok')