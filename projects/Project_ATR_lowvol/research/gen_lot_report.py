# -*- coding: utf-8 -*-
"""生成 持仓数×资金约束 对照看板: 净值叠加 + 对照表(含有效持仓/建仓率/资金规模)."""
import pandas as pd, numpy as np, json, os

OUT_DIR = "D:/QMT_STRATEGIES/backtest_results"
# (TAG, 标签, 资金, 目标只数, 权重, 约束)
CONFIGS = [
    ("rankvol_M_N100_Q_VT_IC",            "A 等权N100 无约束",        100000, 100, "equal",    "无"),
    ("rankvol_M_N100_Q_VT_IC_VP",         "B VP-N100 无约束",         100000, 100, "volparity","无"),
    ("rankvol_M_N50_Q_VT_IC_VP",          "C VP-N50 无约束",          100000, 50,  "volparity","无"),
    ("rankvol_M_N30_Q_VT_IC_VP",          "D VP-N30 无约束",          100000, 30,  "volparity","无"),
    ("rankvol_M_N100_Q_VT_IC_LOT",        "E 等权N100 整数手(10万)", 100000, 100, "equal",    "LOT"),
    ("rankvol_M_N100_Q_VT_IC_VP_LOT",     "F VP-N100 整数手(10万)",  100000, 100, "volparity","LOT"),
    ("rankvol_M_N30_Q_VT_IC_VP_LOT",      "G VP-N30 整数手(10万) ★", 100000, 30,  "volparity","LOT"),
    ("rankvol_M_N20_Q_VT_IC_VP_LOT",      "H VP-N20 整数手(10万)",   100000, 20,  "volparity","LOT"),
    ("rankvol_M_N50_Q_VT_IC_VP_LOT",      "K VP-N50 整数手(10万)",   100000, 50,  "volparity","LOT"),
    ("rankvol_M_N100_Q_VT_IC_LOT_C500000","I 等权N100 整数手(50万)", 500000, 100, "equal",    "LOT"),
    ("rankvol_M_N100_Q_VT_IC_LOT_C1000000","J 等权N100 整数手(100万)",1000000,100, "equal",    "LOT"),
]
COLORS = ["#2563eb","#0891b2","#059669","#16a34a","#ca8a04","#dc2626",
          "#db2777","#9333ea","#7c3aed","#ea580c","#0d9488"]

def load_nav(tag):
    p = os.path.join(OUT_DIR, "atr_lowvol_v2_%s_nav.csv" % tag)
    if not os.path.exists(p): return None, None
    df = pd.read_csv(p, parse_dates=["date"]).set_index("date")["nav"]
    norm = df / df.iloc[0] * 100
    return norm

def load_summary(tag):
    p = os.path.join(OUT_DIR, "atr_lowvol_v2_%s_summary.json" % tag)
    if not os.path.exists(p): return None
    with open(p) as f: return json.load(f)

series, rows = [], []
for (tag, label, cap, nh, w, lot) in CONFIGS:
    nav = load_nav(tag)
    s = load_summary(tag)
    if nav is None or s is None:
        print("MISSING", tag); continue
    series.append((label, nav, COLORS[len(series) % len(COLORS)]))
    f = s["full"]
    eff = s.get("eff_hold_avg", nh)
    fill = s.get("lot_fill_rate")
    rows.append({
        "label": label, "cap": cap, "nh": nh, "w": w, "lot": lot,
        "total": f["total_ret"], "ann": f["annual"], "sharpe": f["sharpe"],
        "mdd": f["max_dd"], "eff": eff, "fill": fill,
    })

# ---- 净值 SVG ----
W, H = 920, 360
xs = series[0][1].index
x0, x1 = 0, len(xs)-1
ymin = min(s[1].min() for s in series); ymax = max(s[1].max() for s in series)
ymin, ymax = min(ymin, 100)*0.98, ymax*1.02
def tx(i): return 50 + (i-x0)/(x1-x0)*(W-70)
def ty(v): return 20 + (ymax-v)/(ymax-ymin)*(H-50)
nav_svg = ['<svg viewBox="0 0 %d %d" width="100%%" style="max-width:920px">' % (W,H)]
nav_svg.append('<line x1="50" y1="%.1f" x2="%d" y2="%.1f" stroke="#ccc"/>' % (ty(100),W-20,ty(100)))
nav_svg.append('<text x="52" y="%.1f" fill="#888" font-size="11">100</text>' % (ty(100)-3))
for (label, nav, color) in series:
    pts = " ".join("%.1f,%.1f" % (tx(i), ty(v)) for i, v in enumerate(nav.values))
    nav_svg.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="1.6"/>' % (pts, color))
nav_svg.append('</svg>')
nav_svg = "".join(nav_svg)

# ---- 图例 ----
legend = "".join('<span style="display:inline-block;margin:2px 8px;font-size:12px">'
                 '<span style="display:inline-block;width:11px;height:11px;background:%s;margin-right:4px"></span>%s</span>'
                 % (c, lb) for (lb,_,c) in series)

# ---- 对照表 ----
def fmt_fill(fill): return "%.1f%%" % fill if fill is not None else "—"
tbl = ['<table style="border-collapse:collapse;width:100%%;font-size:13px">']
tbl.append('<tr style="background:#f1f5f9"><th>配置</th><th>资金</th><th>目标只数</th><th>权重</th>'
           '<th>约束</th><th>总收益</th><th>年化</th><th>夏普</th><th>回撤</th><th>有效持仓</th><th>建仓率</th></tr>')
for r in rows:
    star = "★" in r["label"]
    bg = ' style="background:#fffbe6"' if star else ''
    tbl.append('<tr%s><td>%s</td><td>%d万</td><td>%d</td><td>%s</td><td>%s</td>'
               '<td>%+.1f%%</td><td>%+.1f%%</td><td>%.3f</td><td>%+.1f%%</td><td>%.1f</td><td>%s</td></tr>'
               % (bg, r["label"], r["cap"]//10000, r["nh"], r["w"], r["lot"],
                  r["total"], r["ann"], r["sharpe"], r["mdd"], r["eff"], fmt_fill(r["fill"])))
tbl.append('</table>')
tbl = "".join(tbl)

html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>ATR低波动 v2 — 持仓数×资金约束对照</title>
<style>body{{font-family:-apple-system,'Microsoft YaHei',sans-serif;margin:24px;color:#222}}
.wrap{{max-width:980px;margin:auto}} .card{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:14px;margin:14px 0}}
.note{{background:#fffbe6;border-left:4px solid #e6b800;padding:10px 14px;margin:10px 0;font-size:13px}}
.key{{background:#f0fff4;border-left:4px solid #38a169;padding:10px 14px;margin:10px 0;font-size:13px}}
code{{background:#f4f4f4;padding:1px 5px;border-radius:3px}}</style></head><body><div class="wrap">
<h1>ATR 低波动策略 v2 — 持仓数 × 资金约束 对照看板</h1>
<p style="color:#666">区间 2023-01-03 ~ 2026-07-31 · 底座 RANKVOL+质量(ROE)+VT+行业cap · 单边0.1% · 后复权 · 数据源 E:/astock</p>
<div class="card"><div>{legend}</div><div>{nav_svg}</div></div>
<div class="card">{tbl}</div>
<div class="key"><b>核心结论</b><br>
① <b>10万资金下 100只完全不可行</b>：等权/vol-parity 单只目标≈950元 &lt; 1500元门槛 → 建仓率≈0%（E/F 失效）。<br>
② <b>vol-parity + 压缩只数 是 10万正解</b>：G(VP-N30,★) 有效持仓15.6、建仓率51.6%、夏普<b>0.843(最高)</b>、回撤<b>-14.98%(很低)</b>；
   K(VP-N50) 回撤最低-8.2%但收益薄；H(VP-N20) 建仓率最高但收益最低。<br>
③ <b>放大资金解锁等权100只</b>：50万→建仓率75.8%收益+28.7%；100万→建仓率91.2%收益+35.2%（接近无约束+40.8%）。<b>资金规模是根本约束</b>。<br>
④ <b>实盘建议</b>：资金10万→用 VP-N30（接受~16只有效持仓）；资金50万+→等权100只更优且分散完整；极端厌恶回撤→VP-N50(回撤-8.2%)。
</div>
<div class="note"><b>诚实风险提示</b>：绝对收益高度依赖 2025 低波/质量单边行情（各版2025均+8~27%，2026均转负）。
vol-parity 在10万下实际仅持~16只（一半目标股因资金不足1手被跳过），分散度有限但回撤控制好。
前向预期应锚定年化 6~10%、夏普 0.4~0.6。LOT 门槛 MIN_LOT_VALUE=1500元（防手续费占比过高），可调。</div>
<p style="color:#888;font-size:12px">生成: gen_lot_report.py · 数据: backtest_results/atr_lowvol_v2_{{tag}}_nav.csv</p>
</div></body></html>"""

with open(os.path.join(OUT_DIR, "atr_lowvol_lot_report.html"), "w", encoding="utf-8") as f:
    f.write(html)
print("看板已生成: atr_lowvol_lot_report.html  共 %d 个配置" % len(rows))
