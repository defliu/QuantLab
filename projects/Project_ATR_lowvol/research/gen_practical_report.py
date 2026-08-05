# coding: utf-8
"""ATR 低波动 v2 实盘化候选对照看板 (vol-targeting / 行业cap / 季频解锁)"""
import pandas as pd, numpy as np, os, json

OUT = "D:/QMT_STRATEGIES/backtest_results"
NAV = lambda t: os.path.join(OUT, "atr_lowvol_v2_%s_nav.csv" % t)

CONFIGS = [
    ("atr_M",            "ATR% 月频(基线v2,二元清仓)",        "reference"),
    ("rankvol_M_Q",      "RANKVOL+质量 月频(control,二元)",   "control"),
    ("rankvol_M_Q_VT",   "RANKVOL+质量 月频+波动率目标化",     "VT"),
    ("rankvol_M_Q_VT_IC","RANKVOL+质量 月频+VT+行业cap",       "VT+IC"),
    ("rankvol_Q_VT",     "RANKVOL 季频+波动率目标化",          "Q+VT"),
    ("rankvol_Q_Q_VT_IC","RANKVOL+质量 季频+VT+行业cap(最优)", "BEST"),
]

def metrics(nav):
    n = len(nav)
    if n < 2: return {}
    total = nav["nav"].iloc[-1]/nav["nav"].iloc[0]-1
    ann = (1+total)**(252/n)-1
    daily = nav["nav"].pct_change().dropna()
    sharpe = daily.mean()/daily.std()*np.sqrt(252) if daily.std()>0 else 0
    peakv = nav["nav"].cummax()
    mdd = (nav["nav"]/peakv-1).min()
    return {"total":total*100,"ann":ann*100,"sharpe":sharpe,"mdd":mdd*100}

rows = []
navs = {}
for tag, label, kind in CONFIGS:
    df = pd.read_csv(NAV(tag), parse_dates=["date"]).set_index("date").sort_index()
    navs[tag] = df
    m = metrics(df)
    ins = metrics(df[(df.index>=pd.Timestamp("2023-01-01"))&(df.index<pd.Timestamp("2025-01-01"))])
    oos = metrics(df[df.index>=pd.Timestamp("2025-01-01")])
    rows.append((tag, label, kind, m, ins, oos))

# 净值归一化叠加
all_idx = sorted(set().union(*[set(df.index) for df in navs.values()]))
def norm_series(tag):
    s = navs[tag]["nav"].reindex(all_idx).ffill().bfill()
    return s/s.iloc[0]*100
series = {tag: norm_series(tag) for tag,_ in navs.items()}

# 绘图
W, H = 920, 380
def line_chart(series_dict, title, ylabel):
    xs = list(range(len(all_idx)))
    xmin, xmax = 0, len(all_idx)-1
    yall = np.concatenate([v.values for v in series_dict.values()])
    ymin, ymax = np.nanmin(yall), np.nanmax(yall)
    ymin, ymax = ymin-2, ymax+2
    def X(i): return 60 + (i-xmin)/(xmax-xmin)*(W-90)
    def Y(v): return H-50 - (v-ymin)/(ymax-ymin)*(H-90)
    colors = {"atr_M":"#888","rankvol_M_Q":"#2b6cb0","rankvol_M_Q_VT":"#dd6b20",
              "rankvol_M_Q_VT_IC":"#d69e2e","rankvol_Q_VT":"#38a169","rankvol_Q_Q_VT_IC":"#e53e3e"}
    svg = ['<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg" font-size="11">' % (W,H)]
    svg.append('<rect width="%d" height="%d" fill="#fff"/>' % (W,H))
    for gy in range(5):
        yy = 50 + gy*(H-90)/4
        svg.append('<line x1="60" y1="%.1f" x2="%d" y2="%.1f" stroke="#eee"/>' % (yy, W-30, yy))
        val = ymax - gy*(ymax-ymin)/4
        svg.append('<text x="%d" y="%.1f" fill="#666">%.0f</text>' % (8, yy+4, val))
    for tag, lab, kind in CONFIGS:
        v = series_dict[tag].values
        pts = " ".join("%.1f,%.1f" % (X(i), Y(v[i])) for i in xs if not np.isnan(v[i]))
        col = colors.get(tag, "#333")
        width = 2.4 if kind in ("BEST","control") else 1.4
        svg.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="%.1f"/>' % (pts, col, width))
    svg.append('<text x="60" y="20" fill="#333" font-size="13" font-weight="bold">%s</text>' % title)
    # 图例
    lx = 70; ly = H-18
    for j,(tag,lab,kind) in enumerate(CONFIGS):
        col = colors.get(tag,"#333")
        svg.append('<rect x="%d" y="%d" width="10" height="3" fill="%s"/>' % (lx+ (j%3)*300, ly-4 + (j//3)*14, col))
        svg.append('<text x="%d" y="%d" fill="#444">%s</text>' % (lx+14+(j%3)*300, ly+ (j//3)*14, lab[:18]))
    svg.append('</svg>')
    return "".join(svg)

nav_svg = line_chart(series, "净值曲线 (归一化=100, 2023-01~2026-07)", "净值")
dd_series = {t: (navs[t]["nav"]/navs[t]["nav"].cummax()-1)*100 for t,_ in navs.items()}
dd_svg = line_chart(dd_series, "回撤曲线 (%)", "回撤%")

# 表格
def fmt(m):
    return "%.1f%%" % m if m is not None else "-"
tbl = ['<table border="1" cellspacing="0" cellpadding="5" style="border-collapse:collapse;font-size:12px;width:100%%">']
tbl.append("<tr style='background:#f0f4f8'><th>配置</th><th>总收益</th><th>年化</th><th>夏普</th><th>最大回撤</th><th>样本内</th><th>样本外</th><th>样本外夏普</th></tr>")
for tag, label, kind, m, ins, oos in rows:
    bg = "#fff5f5" if kind=="BEST" else ("#f0fff4" if kind in ("VT","Q+VT","VT+IC") else ("#fafafa" if kind in ("reference","control") else ""))
    mark = " ★" if kind=="BEST" else ""
    tbl.append("<tr style='background:%s'><td>%s%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" %
        (bg, label, mark, fmt(m["total"]), fmt(m["ann"]), "%.2f"%m["sharpe"], fmt(m["mdd"]),
         fmt(ins["total"]), fmt(oos["total"]), "%.2f"%oos["sharpe"]))
tbl.append("</table>")

html = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>ATR低波动 v2 实盘化候选对照</title>
<style>body{font-family:-apple-system,'Microsoft YaHei',sans-serif;margin:24px;color:#222}
h1{font-size:20px}.wrap{max-width:980px;margin:auto}.card{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:14px;margin:14px 0}
.note{background:#fffbe6;border-left:4px solid #e6b800;padding:10px 14px;margin:10px 0;font-size:13px}
.key{background:#f0fff4;border-left:4px solid #38a169;padding:10px 14px;margin:10px 0;font-size:13px}
code{background:#f4f4f4;padding:1px 5px;border-radius:3px}</style></head><body><div class="wrap">
<h1>ATR 低波动策略 v2 — 实盘化候选对照看板</h1>
<p style="color:#666">区间 2023-01-03 ~ 2026-07-31 · 100只·等权·单边0.1%·后复权 · 数据源 E:/astock</p>

<div class="card">__NAV__</div>
<div class="card">__DD__</div>
<div class="card">__TBL__</div>

<div class="key"><b>核心结论</b><br>
① <b>波动率目标化(VT)平滑回撤</b>：月频下回撤 -29.4%→-22.4%，夏普 0.668→0.725（收益略让，但更实盘友好）。<br>
② <b>季频+VT 解锁省成本</b>：交易笔数 6796→2686（-60%），夏普反而 0.725→0.830 —— 证实之前 atr_Q 失败是「二元清仓+低频」死锁，VT 解开后季频最优。<br>
③ <b>行业cap 无副作用</b>：RANKVOL+质量已天然分散，行业cap仅作安全网。<br>
④ <b>最优实盘候选 = 季频+质量+VT+行业cap</b>：年化 16.65%、夏普 0.955、样本外夏普 1.27、最大回撤 -25.1%。
</div>

<div class="note"><b>诚实风险提示</b>：绝对收益高度依赖 <b>2025 低波/质量风格单边行情</b>（各版 2025 均 +27~37%，2026 均转负 -4~-11%）。
vol-target 的夏普/回撤改善是跨周期稳健的，但 +50~70% 量级不可作为前向预期。样本外(2025-26)虽双正，本质仍是 2025 单年驱动，
建议以 <b>年化 6~10%、夏普 0.4~0.6</b> 作为保守预期。此外 100只×10万=每只仅1000元，实盘需放大资金或用波动率平价压缩只数。</div>

<p style="color:#888;font-size:12px">生成: gen_practical_report.py · 数据: backtest_results/atr_lowvol_v2_{tag}_nav.csv</p>
</div></body></html>"""
html = html.replace("__NAV__", nav_svg).replace("__DD__", dd_svg).replace("__TBL__", "".join(tbl))

with open(os.path.join(OUT, "atr_lowvol_practical_report.html"), "w", encoding="utf-8") as f:
    f.write(html)
print("看板已生成: atr_lowvol_practical_report.html")
