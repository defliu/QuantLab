# coding: utf-8
"""生成 ATR 低波动策略回测看板 HTML（Chart.js 净值/回撤 + 指标卡 + 年度收益 + 卖出原因）。"""
import json, pandas as pd

OUT = "D:/QMT_STRATEGIES/backtest_results"
nav = pd.read_csv(f"{OUT}/atr_lowvol_nav.csv", parse_dates=['date']).sort_values('date')
summary = json.load(open(f"{OUT}/atr_lowvol_summary.json", encoding='utf-8'))
trades = pd.read_csv(f"{OUT}/atr_lowvol_trades.csv")

nav['year'] = nav['date'].dt.year
yr = nav.groupby('year').agg(s=('nav', 'first'), e=('nav', 'last'))
yr['ret'] = ((yr['e'] / yr['s'] - 1) * 100).round(2)
years = [int(y) for y in yr.index]
yret = [float(v) for v in yr['ret']]

buys = trades[trades.side == 'BUY']
sells = trades[trades.side == 'SELL']
reasons = sells['reason'].astype(str).str.replace(r'\d+\.\d+%', '', regex=True).str.strip().value_counts()
rnames = list(reasons.index)
rvals = [int(v) for v in reasons.values]

dates = [d.strftime('%Y-%m-%d') for d in nav['date']]
navs = [round(float(v), 2) for v in nav['nav']]
dd = [round(float(v) * 100, 2) for v in nav['drawdown']]

s = summary
cards = [
    ("总收益", f"{s['total_return_pct']}%", f"净值 {s['final_nav']:,.0f}"),
    ("年化收益", f"{s['annual_return_pct']}%", "2023-2026"),
    ("夏普比率", f"{s['sharpe']}", "日频"),
    ("最大回撤", f"{s['max_drawdown_pct']}%", "后复权"),
    ("胜率", f"{s['win_rate_pct']}%", f"{s['n_sells']} 笔卖出"),
    ("交易笔数", f"{s['n_trades']}", f"买{len(buys)}/卖{len(sells)}"),
]

card_html = "".join(
    f'<div class="card"><div class="k">{k}</div><div class="v">{v}</div><div class="sub">{sub}</div></div>'
    for k, v, sub in cards)

year_rows = "".join(
    f"<tr><td>{y}</td><td style='color:{'#c0392b' if r<0 else '#27ae60'}'>{r:+.2f}%</td></tr>"
    for y, r in zip(years, yret))

reason_rows = "".join(
    f"<tr><td>{n}</td><td>{v}</td></tr>" for n, v in zip(rnames, rvals))

html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>ATR 低波动策略 回测看板</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
 body{{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;background:#0f1419;color:#e6e6e6;margin:0;padding:24px}}
 h1{{font-size:22px;margin:0 0 4px}} .meta{{color:#8b98a5;font-size:13px;margin-bottom:20px}}
 .cards{{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:24px}}
 .card{{background:#1a212b;border:1px solid #2a3340;border-radius:10px;padding:14px}}
 .card .k{{color:#8b98a5;font-size:12px}} .card .v{{font-size:24px;font-weight:700;margin:6px 0}}
 .card .sub{{color:#6b7785;font-size:11px}}
 .panel{{background:#1a212b;border:1px solid #2a3340;border-radius:10px;padding:16px;margin-bottom:20px}}
 .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
 table{{width:100%;border-collapse:collapse;font-size:13px}}
 th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #2a3340}}
 th{{color:#8b98a5}}
 .pos{{color:#c0392b}} .neg{{color:#27ae60}}
</style></head>
<body>
<h1>ATR 低波动策略 · 回测看板</h1>
<div class="meta">数据源 E:/astock/daily/stock_daily.parquet ｜ 区间 {s['period'][0]} ~ {s['period'][1]} ｜
参数 ATR&lt;{s['params']['atr_threshold']}% · 换手{s['params']['turnover'][0]}-{s['params']['turnover'][1]}% · 止损{s['params']['stop_loss']*100:.0f}% · 止盈{s['params']['take_profit']*100:.0f}% · 移动止损{s['params']['trailing_stop']*100:.0f}% · 持仓上限{s['params']['max_hold']} · 单边成本{s['params']['cost_oneway']*100:.1f}%</div>
<div class="cards">{card_html}</div>
<div class="panel"><b>净值曲线 & 回撤</b><canvas id="c1" height="90"></canvas></div>
<div class="grid2">
  <div class="panel"><b>年度收益</b><table><tr><th>年份</th><th>收益</th></tr>{year_rows}</table></div>
  <div class="panel"><b>卖出原因分布</b><table><tr><th>原因</th><th>笔数</th></tr>{reason_rows}</table></div>
</div>
<div class="panel" style="font-size:12px;color:#8b98a5">
说明：日频回测，当日收盘算信号、次日开盘成交（防未来函数）；后复权价计算 ATR% 与盈亏；选股按「全市场合格股(ATR%&lt;6+换手1-8%+非ST+有成交)按近5日成交额降序取前3」忠实还原 strategy_atr.py。
局限：未模拟A股跌停板封死无法卖出的情形（真实回撤可能更大）；选股偏向高成交额大市值低波动龙头，非典型「波动最低」选法。
</div>
<script>
const dates={dates!r}, navs={navs!r}, dd={dd!r};
new Chart(document.getElementById('c1'),{{type:'line',
 data:{{labels:dates,datasets:[
  {{label:'净值',data:navs,borderColor:'#4aa3ff',backgroundColor:'rgba(74,163,255,.1)',fill:true,pointRadius:0,yAxisID:'y'}},
  {{label:'回撤%',data:dd,borderColor:'#e74c3c',backgroundColor:'transparent',fill:false,pointRadius:0,yAxisID:'y2'}}
 ]}},
 options:{{responsive:true,interaction:{{mode:'index',intersect:false}},
  scales:{{x:{{ticks:{{maxTicksLimit:12,color:'#6b7785'}},grid:{{color:'#222b36'}}}},
   y:{{position:'left',ticks:{{color:'#6b7785'}},grid:{{color:'#222b36'}}}},
   y2:{{position:'right',ticks:{{color:'#e74c3c'}},grid:{{drawOnChartArea:false}}}}}},
  plugins:{{legend:{{labels:{{color:'#e6e6e6'}}}}}}}}}});
</script>
</body></html>"""

with open(f"{OUT}/atr_lowvol_report.html", "w", encoding="utf-8") as f:
    f.write(html)
print("HTML 看板已生成:", f"{OUT}/atr_lowvol_report.html")
print("年度收益:", dict(zip(years, yret)))
print("卖出原因:", dict(zip(rnames, rvals)))
