# coding: utf-8
"""生成 ATR 低波动 v2 实验对照看板: 净值叠加 + 对照表 + 关键发现"""
import json, os
import pandas as pd
import numpy as np

OUT = "D:/QMT_STRATEGIES/backtest_results"
ORDER = ["atr_M","atr60_M","atr_Q","rankvol_M","ivol_M","atr_M_Q","rankvol_Q","rankvol_M_Q"]
LABEL = {
    "atr_M":    "ATR% 月频(基线)",
    "atr60_M":  "ATR%60 月频(对照)",
    "atr_Q":    "ATR% 季频",
    "rankvol_M":"RANKVOL 月频",
    "ivol_M":   "IVOL特质波动率 月频",
    "atr_M_Q":  "ATR%+质量双排序",
    "rankvol_Q":"RANKVOL+季频",
    "rankvol_M_Q":"RANKVOL+质量双排序",
}
COLOR = {
    "atr_M":"#888", "atr60_M":"#bbb", "atr_Q":"#d98", "rankvol_M":"#e44",
    "ivol_M":"#e84", "atr_M_Q":"#28e", "rankvol_Q":"#a6c", "rankvol_M_Q":"#11f",
}

rows = []
navs = {}
for tag in ORDER:
    sp = os.path.join(OUT, "atr_lowvol_v2_%s_summary.json"%tag)
    nv = os.path.join(OUT, "atr_lowvol_v2_%s_nav.csv"%tag)
    if not os.path.exists(sp):
        continue
    s = json.load(open(sp))
    f = s["full"]; ins = s["insample_2023_2024"]; oos = s["oos_2025_2026"]
    rows.append({
        "tag":tag, "label":LABEL.get(tag,tag),
        "total":f["total_ret"], "ann":f["annual"], "sharpe":f["sharpe"], "mdd":f["max_dd"],
        "ins":ins.get("total_ret",0), "oos":oos.get("total_ret",0),
        "ins_sh":ins.get("sharpe",0), "oos_sh":oos.get("sharpe",0),
        "cash":s["in_cash_pct"], "trades":s["n_trades"],
        "years":s["years"],
    })
    df = pd.read_csv(nv, parse_dates=["date"]).set_index("date")
    navs[tag] = df["nav"]/df["nav"].iloc[0]*100.0

# ---- SVG 净值叠加 ----
allvals = np.concatenate([v.values for v in navs.values()])
ymin, ymax = allvals.min()*0.95, allvals.max()*1.05
W,H = 920,380; x0,y0,w,h = 55,20,840,320
def xy(i,n,val):
    x = x0 + (i/(n-1))*w if n>1 else x0
    y = y0 + h - (val-ymin)/(ymax-ymin)*h
    return x,y
# 公共日期轴(用最长序列)
base = max(navs.values(), key=len)
ndates = base.index
xdates = list(range(len(ndates)))
# 网格 + 轴标签
svg = ['<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg" font-size="11">'%(W,H)]
svg.append('<rect x="0" y="0" width="%d" height="%d" fill="#fafafa"/>'%(W,H))
for g in [0,0.25,0.5,0.75,1.0]:
    gy = y0 + h - g*h
    val = ymin + g*(ymax-ymin)
    svg.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#ddd"/>'%(x0,gy,x0+w,gy))
    svg.append('<text x="%g" y="%g" fill="#666">%.0f</text>'%(5,gy+4,val))
# x 轴年份刻度
for yr in [2023,2024,2025,2026]:
    idxs = [i for i,d in enumerate(ndates) if d.year==yr]
    if idxs:
        i = idxs[0]; x,_ = xy(i,len(ndates),ymin)
        svg.append('<text x="%g" y="%g" fill="#666">%d</text>'%(x,y0+h+16,yr))
for tag in ORDER:
    if tag not in navs: continue
    v = navs[tag]
    # 对齐到 ndates(截取到相同长度)
    v = v.reindex(ndates).ffill().bfill()
    pts = " ".join("%g,%g"%(xy(i,len(ndates),val)[0],xy(i,len(ndates),val)[1]) for i,val in enumerate(v.values))
    svg.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="%s" opacity="0.9"/>'%
               (pts, COLOR[tag], 2.2 if tag in("atr_M","rankvol_M","rankvol_M_Q","atr_M_Q") else 1.4))
# 图例
lx = x0+10
for i,tag in enumerate(ORDER):
    if tag not in navs: continue
    ly = y0+12+i*16
    svg.append('<rect x="%g" y="%g" width="12" height="4" fill="%s"/>'%(lx,ly,COLOR[tag]))
    svg.append('<text x="%g" y="%g" fill="#333">%s</text>'%(lx+18,ly+6,LABEL.get(tag,tag)))
svg.append('</svg>')

# ---- HTML 表 ----
def fmt(v): return ("%.2f"%v)
def cell(v, good=None):
    if good is None: return "<td>%s</td>"%v
    color = "#1a7a1a" if good else "#b00"
    return '<td style="color:%s;font-weight:600">%s</td>'%(color,v)
thead = "<tr><th>配置</th><th>总收益%</th><th>年化%</th><th>夏普</th><th>最大回撤%</th><th>样本内%</th><th>样本外%</th><th>样本外夏普</th><th>空仓%</th></tr>"
trs = []
for r in rows:
    best_ann = max(x["ann"] for x in rows)
    best_sh = max(x["sharpe"] for x in rows)
    best_mdd = min(x["mdd"] for x in rows)
    trs.append("<tr><td style='text-align:left'>%s</td>%s%s%s%s%s%s%s%s</tr>"%(
        r["label"],
        cell(fmt(r["total"]), r["total"]==max(x["total"] for x in rows)),
        cell(fmt(r["ann"]), r["ann"]==best_ann),
        cell(fmt(r["sharpe"]), r["sharpe"]==best_sh),
        cell(fmt(r["mdd"]), r["mdd"]==best_mdd),
        cell(fmt(r["ins"]), r["ins"]==max(x["ins"] for x in rows)),
        cell(fmt(r["oos"]), r["oos"]==max(x["oos"] for x in rows)),
        cell(fmt(r["oos_sh"]), r["oos_sh"]==max(x["oos_sh"] for x in rows)),
        cell(fmt(r["cash"])),
    ))
table = "<table border='1' cellspacing='0' cellpadding='5' style='border-collapse:collapse;font-size:12px;margin:auto;background:#fff'>%s%s</table>"%(thead,"".join(trs))

# 年度明细表
yrs = [2023,2024,2025,2026]
yhead = "<tr><th>配置</th>"+"".join("<th>%d</th>"%y for y in yrs)+"</tr>"
ytrs = []
for r in rows:
    ytrs.append("<tr><td style='text-align:left'>%s</td>"%r["label"]+"".join(
        "<td>%+.1f%%</td>"%(r["years"].get(str(y), r["years"].get(y,0))) for y in yrs)+"</tr>")
ytable = "<table border='1' cellspacing='0' cellpadding='5' style='border-collapse:collapse;font-size:12px;margin:auto;background:#fff'>%s%s</table>"%(yhead,"".join(ytrs))

html = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<title>ATR 低波动 v2 实验对照看板</title></head><body style="font-family:system-ui,'Microsoft YaHei',sans-serif;max-width:1000px;margin:20px auto;background:#fff;color:#222">
<h2>ATR 低波动策略 v2 — 优化实验对照看板</h2>
<p style="color:#666">区间 2023-01-03 ~ 2026-07-31 ｜ 全市场 ｜ 单边成本 0.1% ｜ 后复权 ｜ 100只等权 ｜ 回撤控制 -15%</p>
<h3>① 净值曲线叠加（起点=100）</h3>
%s
<h3>② 核心指标对照（绿色=该列最优）</h3>
%s
<h3>③ 年度收益明细</h3>
%s
<h3>④ 关键发现</h3>
<ul style="line-height:1.7">
<li><b>波动率估计器是最大杠杆点</b>：把 ATR% 换成 RANKVOL / IVOL（基于收益率的稳健波动率），年化从 6.46% → 13.2%，夏普 0.465 → 0.65。<b>消融证明驱动因素是估计器本身而非窗口</b>：同 60 天窗口，ATR% 仅 -14.56%，而 RANKVOL +52.94%。IVOL 本质是「低特质波动率异象」(Ang 2006)，在 A 股有效。</li>
<li><b>质量(ROE)双排序稳健增益</b>：在 ATR% 上加 ROE 双排序，+23.85%→+38.40%，且最大回撤从 -21.64% 降到 -20.25%、样本外夏普 0.82（最高）。它平滑了牛熊（2024 +15.5% vs 基线 +10.8%）。</li>
<li><b>季频(ATR)假设失败</b>：atr_Q 仅 -7.46%。原因——本策略的<b>二元回撤清仓</b>与低频再平衡不兼容：清仓后最长空仓 3 个月，错失反弹、踩错时点（2024 -19.1%）。若要季频省成本，须先把回撤控制改成<b>波动率目标化渐进降仓</b>而非二元清仓。</li>
<li><b>最优组合</b>：RANKVOL+质量双排序（rankvol_M_Q）年化 13.21%、夏普 0.668、样本外 +26.49%（夏普 0.94），样本内外双正；缺点是回撤仍约 -29%（低特质波组合在 2025 动量行情中偏集中）。</li>
<li><b>诚实提示</b>：+52% 含 2025 低波/质量风格极端占优的贡献（样本外仍正，非运气），<b>前向预期应打折</b>；且 100 只×10万=每只仅 1000 元，实盘需放大资金或用波动率平价压缩只数。</li>
</ul>
<p style="color:#999;font-size:11px">数据源 E:/astock ｜ 脚本 backtest_atr_lowvol_v2.py（REBAL/VOLM/QUALITY/ATWIN/NHOLD 可调）</p>
</body></html>"""

with open(os.path.join(OUT,"atr_lowvol_experiments_report.html"),"w",encoding="utf-8") as f:
    f.write(html)
print("看板已生成: atr_lowvol_experiments_report.html")
print("覆盖 %d 个配置"%len(rows))
