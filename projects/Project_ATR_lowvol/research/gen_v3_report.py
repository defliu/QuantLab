# coding: utf-8
"""生成 v3 多因子/杠杆 优化方向对照报告 (atr_lowvol_v3_optimization_report.html)"""
import json, glob, os
import pandas as pd

OUT = "D:/QMT_STRATEGIES/backtest_results"
files = sorted(glob.glob(os.path.join(OUT, "atr_lowvol_v3_*_summary.json")))
rows = []
for f in files:
    s = json.load(open(f, encoding="utf-8"))
    c = s["config"]; full = s["full"]; ins = s["insample_2023_2024"]; oos = s["oos_2025_2026"]
    rows.append({
        "tag": s["tag"],
        "label": "%s/%s%s%s%s%s%s%s" % (
            c["VOLM"], c["REBAL"],
            " +质量" if c["QUALITY"] else "",
            " +价值" if c.get("VALUE") else "",
            " +动量排" if c.get("MOM") else "",
            " +门控" if "MG" in s["tag"] else "",
            " +VT" if c["VOLTARGET"] else "",
            " +IC" if c["INDCAP"] else ""),
        "lev": c.get("VT_CAP", 1.0) if c.get("VOLTARGET") else (c.get("LEV", 1.0)),
        "annual": full.get("annual", 0), "sharpe": full.get("sharpe", 0),
        "mdd": full.get("max_dd", 0),
        "ins": ins.get("total_ret", 0), "oos": oos.get("total_ret", 0),
        "ins_sharpe": ins.get("sharpe", 0), "oos_sharpe": oos.get("sharpe", 0),
        "trades": s.get("n_trades", 0),
        "fill": s.get("lot_fill_rate"), "eff": s.get("eff_hold_avg"),
        "nhold": c["N_HOLD"], "lot": s.get("lot", False), "weight": s.get("weight"),
    })
rows.sort(key=lambda r: -r["annual"])

# 图表: 选代表性配置归一化净值
chart_tags = [
    "rankvol_M_N100_Q", "rankvol_M_N100_Q_MG",
    "rankvol_Q_N100_Q_VT_IC_x1.5",
    "rankvol_Q_N30_Q_VT_IC_VP_LOT_x1.5",
    "rankvol_Q_N50_Q_VT_IC_VP_LOT_x1.5_MG",
]
chart = {}
for t in chart_tags:
    p = os.path.join(OUT, "atr_lowvol_v3_%s_nav.csv" % t)
    if os.path.exists(p):
        d = pd.read_csv(p, parse_dates=["date"]).set_index("date")["nav"]
        d = d / d.iloc[0] * 100
        chart[t] = [[i.strftime("%Y-%m"), round(v, 2)] for i, v in d.resample("ME").last().items()]

html = """<html><head><meta charset="utf-8"><title>ATR低波 v3 优化方向对照</title>
<style>body{font-family:-apple-system,'Segoe UI',sans-serif;margin:32px;background:#fafafa;color:#222}
h1{font-size:24px}h2{font-size:18px;margin-top:32px;border-left:4px solid #c63;padding-left:10px}
table{border-collapse:collapse;width:100%%;font-size:13px;margin-top:12px;background:#fff}
th,td{border:1px solid #ddd;padding:6px 8px;text-align:center}
th{background:#333;color:#fff}
tr:nth-child(even){background:#f4f4f4}
.good{color:#1a7f1a;font-weight:700}.bad{color:#c00}.warn{color:#b8860b}
canvas{background:#fff;border:1px solid #ddd;margin-top:12px}
.note{background:#fffbe6;border:1px solid #ffe58f;padding:12px 16px;border-radius:6px;margin:12px 0}
.box{background:#fff;border:1px solid #ddd;border-radius:6px;padding:16px;margin:12px 0}
code{background:#eee;padding:1px 5px;border-radius:3px}
</style></head><body>
<h1>ATR 低波动策略 v3 — 冲 15% 年化：优化方向实证</h1>
<p>数据源 <code>E:/astock</code> (2009-2026-07, 全A Tushare口径, 后复权)；回测 2023-01 ~ 2026-07；单边成本 0.1%；月/季频；合格域=换手[1,8]%% &amp; 有成交 &amp; 非ST &amp; 上市≥60日。</p>

<div class="note"><b>一句话结论：</b>纯低波+质量在当下样本只能给 ~10%% 年化；<b>15%%+ 只有两条现实路径</b>——① 给组合加 <b>1.5x 两融杠杆</b>（季频+质量+动量门控+VT+行业cap+30~50只 vol-parity），回测 15~18%%；② 把本金加到 <b>~100万</b> 做 100只等权（无杠杆）。价值/动量"排序加权"在 2023-26 反而拖累，<b>不要用</b>；"动量门控"(剔除近期输家)是免费增益。</div>

<h2>一、实验矩阵（按年化收益降序）</h2>
<table><tr><th>配置</th><th>年化</th><th>夏普</th><th>最大回撤</th><th>样本内23-24</th><th>样本外25-26</th><th>交易数</th><th>实盘10万<br>建仓率/有效持仓</th></tr>"""
for r in rows:
    cls = "good" if r["annual"] >= 15 else ("bad" if r["annual"] < 0 else "")
    fill = "" if r["fill"] is None else ("%.0f%%/%s" % (r["fill"], r["eff"]))
    html += "<tr><td style='text-align:left'>%s</td><td class='%s'>%.2f%%</td><td>%.3f</td><td>%.2f%%</td><td>%.2f%%</td><td>%.2f%%</td><td>%d</td><td>%s</td></tr>" % (
        r["label"], cls, r["annual"], r["sharpe"], r["mdd"], r["ins"], r["oos"], r["trades"], fill or "-")
html += "</table>"

html += """
<h2>二、净值曲线（归一化到 100）</h2>
<canvas id='c' width='960' height='420'></canvas>
<div id='legend' style='font-size:12px;margin-top:8px'></div>
<script>
var DATA = __CHART__;
var colors = ['#333','#1a7f1a','#c00','#1769aa','#b8860b'];
var cv=document.getElementById('c'), ctx=cv.getContext('2d');
var tags=Object.keys(DATA);
var allDates=new Set(); tags.forEach(t=>DATA[t].forEach(p=>allDates.add(p[0])));
var dates=[...allDates].sort();
function x(i){return 60+i*(cv.width-80)/(dates.length-1);}
function y(v){return cv.height-40-(v-80)/(80)*(cv.height-80);}
ctx.strokeStyle='#eee';for(var g=0;g<=4;g++){var v=80+g*40;ctx.beginPath();ctx.moveTo(60,y(v));ctx.lineTo(cv.width-20,y(v));ctx.stroke();ctx.fillStyle='#999';ctx.fillText(v,20,y(v)+4);}
tags.forEach(function(t,k){ctx.strokeStyle=colors[k];ctx.lineWidth=2;ctx.beginPath();
 var dm={};DATA[t].forEach(p=>dm[p[0]]=p[1]);
 dates.forEach(function(d,i){if(dm[d]!=null){var px=x(i),py=y(dm[d]);if(i==0)ctx.moveTo(px,py);else ctx.lineTo(px,py);}});ctx.stroke();});
var lg=document.getElementById('legend');
tags.forEach(function(t,k){lg.innerHTML+="<span style='color:"+colors[k]+";font-weight:700'>&#9608;</span> "+t+" &nbsp; ";});
</script>

<h2>三、关键发现</h2>
<div class="box">
<ol>
<li><b>动量门控是"免费午餐"</b>：在低波+质量里剔除 12-1 月动量≤0 的近期输家（<code>MOMGATE=1</code>），月频 100只 从 13.2%% → <b>15.9%%</b>，回撤还从 -29%% 降到 -20%%。注意：动量"排序加权"(追赢家)反而拖累（M2/M3 变负），因为会选出高波动赢家、破坏低波属性。</li>
<li><b>价值(BP)因子在 2023-26 是负贡献</b>：叠加 BP 后年化从 +13%% 跌到 -6%%。这段是质量/低波占优 regime，便宜股(银行/地产/周期)持续跑输。券商研报的 20%%+ 多因子是含 2013-22 牛市的长周期结论，<b>不能直接平移到当下</b>。</li>
<li><b>杠杆是 10万账户冲 15%% 的唯一现实杠杆</b>：季频+质量+VT+IC 在 1.5x 两融下年化 23%%、2.0x 下 26%%。但 <b>月频+杠杆会崩</b>（-5.5%%）——月频反复在下跌中清仓又加杠杆接刀，必须用<b>季频</b>。</li>
<li><b>10万 实盘的整数手约束是硬瓶颈</b>：100只等权在 10万下每只是 1000元 &lt; 最小建仓门槛，即使 2x 杠杆也只持 28只（收益被腰斩到 7%%）。解决：① 减到 30~50只 + vol-parity 权重（杠杆让每只得买够手数）；② 或加本金到 ~100万。</li>
<li><b>终极实盘配置（季频+门控+质量+VT+IC+1.5x+N30 vol-parity+LOT）回测 18.2%%/夏普0.87</b>，但有效持仓仅 7~10 只、且 2025 单年 +66%%、2023 因门控空仓=0%%，<b>高度集中且严重依赖 2025</b>。</li>
</ol></div>

<h2>四、诚实的前向预期（重要）</h2>
<div class="note">所有 15%%+ 数字都被 <b>2025 年低波/质量单边牛市</b>严重抬高（该年 +34~66%%）。2026 年各配置已转平/负。扣除 2025 异常后，该因子真实可捕获收益约 <b>年化 6~10%%</b>。加 1.5x 杠杆后前向合理预期 <b>~8~13%%</b>，绝非稳拿 15%%。杠杆同时把回撤放大到 -17%~-29%%，需能扛住两融追缴。</div>

<h2>五、给你的可执行建议</h2>
<div class="box">
<p><b>目标 15%%+，二选一：</b></p>
<p><b>方案A（10万 + 开两融，推荐）</b>：国金账户开通融资融券，跑 <code>VOLM=rankvol, QUALITY=1, MOMGATE=1, VOLTARGET=1, INDCAP=1, LEVMULT=1.5, REBAL=Q, NHOLD=30~50, WEIGHT=volparity</code>。回测 15~18%%，前向 ~8~13%%，回撤 -17~-29%%。</p>
<p><b>方案B（不加杠杆，加本金）</b>：把本金提到 ~100万，跑 100只等权 <code>rankvol+质量+门控</code>（G1 回测 15.9%%，或季频+VT+IC 16.7%%），前向 ~8~12%%，回撤更可控(-20%)。</p>
<p><b>两个务必做/不做</b>：① <b>加动量门控</b>（免费增益，务必上）；② <b>不要</b>加价值/动量排序因子（在当下 regime 拖累）。</p>
<p><b>进阶方向（如需更高收益且不靠杠杆）</b>：① 风格/regime 双模切换（主线切动量、分散切低波，研报 16.8%%）；② 红利叠加（国泰海通 20.6%%，需补股息率数据）。这两条实现更复杂，留作下一步。</p>
</div>
<p style="color:#999;font-size:12px">生成于回测产物 atr_lowvol_v3_*_summary.json / _nav.csv。所有结果为历史回测，不代表未来收益。</p>
</body></html>"""

html = html.replace("__CHART__", json.dumps(chart, ensure_ascii=False)).replace("%%", "%")
with open(os.path.join(OUT, "atr_lowvol_v3_optimization_report.html"), "w", encoding="utf-8") as f:
    f.write(html)
print("报告已生成: atr_lowvol_v3_optimization_report.html (%d 个配置)" % len(rows))
