import urllib.request, json, sys

# Fetch Tencent auction data for G2 candidates
stocks = ['603272.SH','003029.SZ','000610.SZ','688093.SH','600691.SH','600180.SH','603377.SH']
markets = ['1' if s.endswith('SH') else '0' for s in stocks]
codes = [s.split('.')[0] for s in stocks]
url = 'https://qt.gtimg.cn/q=' + ','.join(markets + codes)
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    data = urllib.request.urlopen(req, timeout=10).read().decode('gbk')
    print("=== TENCENT_AUCTION ===")
    print(data)
except Exception as e:
    print(f"TENCENT_ERROR: {e}")

# Fetch indices
url2 = 'https://hq.sinajs.cn/list=sh000001,sz399001'
req2 = urllib.request.Request(url2, headers={'Referer': 'https://finance.sina.com.cn', 'User-Agent': 'Mozilla/5.0'})
try:
    data2 = urllib.request.urlopen(req2, timeout=10).read().decode('gbk')
    print("=== SINA_INDEX ===")
    print(data2)
except Exception as e:
    print(f"SINA_ERROR: {e}")

# Fetch Wudao sector data
try:
    wurl = 'https://stock.quicktiny.cn/api/mcp'
    wdata = json.dumps({'method': 'call', 'params': {'tool': 'theme_intraday_capital', 'args': {}}}).encode()
    wreq = urllib.request.Request(wurl, data=wdata, headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    wresp = urllib.request.urlopen(wreq, timeout=15).read().decode('utf-8')
    print("=== WUDAO_SECTOR ===")
    print(wresp[:2000])
except Exception as e:
    print(f"WUDAO_ERROR: {e}")
