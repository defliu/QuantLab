# coding: utf-8
"""T-20260815-001 看板 HTML 生成器：vol_target 无杠杆扫描结果。

读 5 个 report 目录（equity_curve.csv / summary.json / positions.csv），
渲染自包含离线看板（内嵌 SVG 图表，无 CDN 依赖）。
产物：projects/Project_ATR_lowvol/results/voltarget_dashboard_20260815.html
"""
import json
import os

import pandas as pd

ROOT = "D:/QuantLab"
REPORTS = [
    ("VT=0(基线)", 0.0, "reports/20260815_110119_ccfb82_atr_10w_price50", "#2563eb"),
    ("VT=0.10", 0.10, "reports/20260815_111420_d8164f_atr_10w_price50_vt0.1", "#10b981"),
    ("VT=0.15", 0.15, "reports/20260815_112755_3aa944_atr_10w_price50_vt0.15", "#f59e0b"),
    ("VT=0.18", 0.18, "reports/20260815_114058_212113_atr_10w_price50_vt0.18", "#ef4444"),
    ("VT=0.20", 0.20, "reports/20260815_115409_c96ed1_atr_10w_price50_vt0.2", "#8b5cf6"),
]
OUT = ROOT + "/projects/Project_ATR_lowvol/results/voltarget_dashboard_20260815.html"


def load():
    rows = []
    for name, vt, rel, color in REPORTS:
        base = ROOT + "/" + rel
        with open(base + "/summary.json", "r", encoding="utf-8") as f:
            perf = json.load(f)["performance"]
        eq = pd.read_csv(base + "/equity_curve.csv")
        pos = pd.read_csv(base + "/positions.csv")
        eq["date"] = pd.to_datetime(eq["date"])
        eq["year"] = eq["date"].dt.year
        # 净值曲线（起点=100）
        nav0 = eq["total_asset"].iloc[0]
        nav = (eq["total_asset"] / nav0 * 100.0).values
        dates = eq["date"]
        # 回撤曲线
        run_max = pd.Series(nav).cummax()
        dd = (nav / run_max.values - 1.0) * 100.0
        # 分年度收益
        yearly = {y: g["total_asset"].iloc[-1] / g["total_asset"].iloc[0] - 1.0
                  for y, g in eq.groupby("year")}
        # 平均持仓 + 资金利用率
        pos["month"] = pd.to_datetime(pos["date"]).dt.to_period("M")
        n_pos = pos.groupby("month")["code"].nunique()
        hold = eq[eq["market_value"] > 100]
        util = (1 - hold["cash"] / hold["total_asset"]).mean()
        rows.append({
            "name": name, "vt": vt, "color": color,
            "perf": perf, "yearly": yearly,
            "n_pos": (n_pos.mean(), n_pos.max(), n_pos.min()),
            "util": util,
            "dates": dates, "nav": nav, "dd": dd,
        })
    return rows


# ---------------- SVG 工具 ----------------
def svg_line(x1, y1, x2, y2, color="#e5e7eb", width=1, dash=None):
    d = ' stroke-dasharray="%s"' % dash if dash else ""
    return ('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" '
            'stroke="%s" stroke-width="%s"%s/>' % (x1, y1, x2, y2, color, width, d))


def svg_text(x, y, s, size=12, fill="#6b7280", anchor="middle", weight=400):
    return ('<text x="%.2f" y="%.2f" font-size="%d" fill="%s" text-anchor="%s" '
            'font-weight="%d" font-family="inherit">%s</text>'
            % (x, y, size, fill, anchor, weight, s))


def poly_points(xs, ys):
    return " ".join("%.2f,%.2f" % (x, y) for x, y in zip(xs, ys))


def make_equity_svg(rows, W=1120, H=430, PL=64, PR=20, PT=18, PB=42):
    years = [2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
    all_dates = [r["dates"] for r in rows]
    start = min(d.min() for d in all_dates)
    end = max(d.max() for d in all_dates)
    span = (end - start).days or 1
    vmax = max(float(r["nav"].max()) for r in rows) * 1.04
    vmin = min(float(r["nav"].min()) for r in rows) * 0.95
    iw, ih = W - PL - PR, H - PT - PB

    def X(date):
        return PL + (date - start).days / span * iw

    def Y(v):
        import math
        lo, hi = math.log10(max(vmin, 50)), math.log10(vmax)
        return PT + ih - (math.log10(v) - lo) / (hi - lo) * ih

    parts = []
    # 网格 + Y 轴（对数刻度：50/80/120/200/320）
    ticks = []
    v = 50
    while v <= vmax:
        ticks.append(v)
        v = v * 1.5 if v < 90 else v * 1.6
    for t in ticks:
        y = Y(t)
        parts.append(svg_line(PL, y, PL + iw, y))
        parts.append(svg_text(PL - 10, y + 4, str(int(t)), anchor="end", size=11))
    # X 轴年份刻度
    for yy in years:
        d = pd.Timestamp("%d-01-01" % yy)
        if start <= d <= end:
            x = X(d)
            parts.append(svg_text(x, H - 16, str(yy), size=11))
    # 折线 + 面积渐变
    for r in rows:
        nav = r["nav"]
        ds = r["dates"]
        # 下采样到 ≤420 点
        step = max(1, len(nav) // 420)
        idx = range(0, len(nav), step)
        xs = [X(ds.iloc[i]) for i in idx]
        ys = [Y(nav[i]) for i in idx]
        base = PT + ih
        pts = poly_points(xs, ys)
        area = '%.2f,%.2f %s %.2f,%.2f' % (xs[0], base, pts, xs[-1], base)
        gid = "g_%s" % r["name"].replace("=", "").replace("(", "").replace(")", "").replace(".", "")
        parts.append(
            '<defs><linearGradient id="%s" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="%s" stop-opacity="0.18"/>'
            '<stop offset="1" stop-color="%s" stop-opacity="0.0"/>'
            '</linearGradient></defs>' % (gid, r["color"], r["color"]))
        parts.append('<polygon points="%s" fill="url(#%s)" stroke="none"/>' % (area, gid))
        parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2.2" '
                     'stroke-linejoin="round" stroke-linecap="round"/>' % (pts, r["color"]))
        # 终点标签
        lx, ly = xs[-1], ys[-1]
        parts.append(svg_text(min(lx + 8, W - 6), ly - 8, "%s %.0f" % (r["name"].replace("VT", ""), nav[-1]),
                              size=11, fill=r["color"], anchor="start", weight=600))
    parts.append(svg_text(PL, PT - 6, "策略净值（起点 100，对数刻度）", size=13, fill="#111827", anchor="start", weight=600))
    return '<svg viewBox="0 0 %d %d" width="100%%" preserveAspectRatio="xMidYMid meet" role="img">%s</svg>' % (W, H, "".join(parts))


def make_drawdown_svg(rows, W=1120, H=300, PL=64, PR=20, PT=18, PB=36):
    years = [2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
    all_dates = [r["dates"] for r in rows]
    start = min(d.min() for d in all_dates)
    end = max(d.max() for d in all_dates)
    span = (end - start).days or 1
    vmin = min(float(r["dd"].min()) for r in rows)
    vmin = (vmin // 5 - 1) * 5  # 向下取整到 5 的倍数
    iw, ih = W - PL - PR, H - PT - PB

    def X(date):
        return PL + (date - start).days / span * iw

    def Y(v):
        return PT + (1 - (v - vmin) / (0 - vmin)) * ih

    parts = []
    for t in range(int(vmin), 1, 5):
        y = Y(float(t))
        parts.append(svg_line(PL, y, PL + iw, y))
        parts.append(svg_text(PL - 10, y + 4, "%d%%" % t, anchor="end", size=11))
    parts.append(svg_line(PL, Y(0), PL + iw, Y(0), color="#9ca3af", width=1.2))
    for yy in years:
        d = pd.Timestamp("%d-01-01" % yy)
        if start <= d <= end:
            x = X(d)
            parts.append(svg_text(x, H - 12, str(yy), size=11))
    for r in rows:
        dd = r["dd"]
        ds = r["dates"]
        step = max(1, len(dd) // 420)
        idx = range(0, len(dd), step)
        xs = [X(ds.iloc[i]) for i in idx]
        ys = [Y(dd[i]) for i in idx]
        pts = poly_points(xs, ys)
        parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="1.8" '
                     'stroke-linejoin="round" stroke-linecap="round"/>' % (pts, r["color"]))
        # 最低点标注
        mi = int(dd.argmin()) if len(dd) else 0
        parts.append(svg_text(min(X(ds.iloc[mi]) + 8, W - 10), Y(dd[mi]) - 6,
                              "%s %.1f%%" % (r["name"].replace("VT", ""), dd[mi]),
                              size=11, fill=r["color"], anchor="start", weight=600))
    parts.append(svg_text(PL, PT - 6, "策略回撤（%）", size=13, fill="#111827", anchor="start", weight=600))
    return '<svg viewBox="0 0 %d %d" width="100%%" preserveAspectRatio="xMidYMid meet" role="img">%s</svg>' % (W, H, "".join(parts))


def make_yearly_svg(rows, W=1120, H=360, PL=64, PR=20, PT=18, PB=40):
    years = sorted(set().union(*[set(r["yearly"].keys()) for r in rows]))
    iw, ih = W - PL - PR, H - PT - PB
    all_vals = [r["yearly"].get(y, 0) * 100 for r in rows for y in years]
    vmax = max(all_vals) * 1.1
    vmin = min(all_vals) * 1.1
    if vmin > 0:
        vmin = -5
    group_w = iw / len(years)
    n = len(rows)
    bw = group_w / (n + 1) * 0.8

    def Y(v):
        return PT + ih - (v - vmin) / (vmax - vmin) * ih

    def X0(yi):
        return PL + yi * group_w + group_w / 2

    zero_y = Y(0)
    parts = []
    # 网格
    for t in range(int(vmin // 10) * 10, int(vmax) + 1, 10):
        y = Y(float(t))
        parts.append(svg_line(PL, y, PL + iw, y))
        parts.append(svg_text(PL - 10, y + 4, "%d%%" % t, anchor="end", size=11))
    parts.append(svg_line(PL, zero_y, PL + iw, zero_y, color="#9ca3af", width=1.4))
    # 柱
    for yi, y in enumerate(years):
        cx = X0(yi)
        for ri, r in enumerate(rows):
            v = r["yearly"].get(y, 0) * 100
            y1 = Y(max(v, 0))
            y2 = Y(min(v, 0))
            x = cx - bw * n / 2 + ri * bw
            parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" rx="2" fill="%s" '
                         'opacity="0.88"><title>%s %d年: %+.1f%%</title></rect>'
                         % (x, y1, bw - 1, max(y2 - y1, 0.5), r["color"], r["name"], y, v))
        parts.append(svg_text(cx, H - 16, str(y), size=11))
    parts.append(svg_text(PL, PT - 6, "分年度收益（%）", size=13, fill="#111827", anchor="start", weight=600))
    return '<svg viewBox="0 0 %d %d" width="100%%" preserveAspectRatio="xMidYMid meet" role="img">%s</svg>' % (W, H, "".join(parts))


def make_radar_svg(rows, W=560, H=560, cx=None, cy=None, R=210):
    cx = cx or W / 2
    cy = cy or H / 2 + 10
    # 指标：年化 / 夏普 / 卡玛 / 回撤(-越小越好→用回撤减幅)/ 胜率
    def norm(v, lo, hi):
        return max(0.0, min(1.0, (v - lo) / (hi - lo)))
    perfs = [r["perf"] for r in rows]
    annuals = [p["annual_return"] * 100 for p in perfs]
    sharpes = [p["sharpe"] for p in perfs]
    calmars = [p["calmar"] for p in perfs]
    dds = [p["max_drawdown"] * -100 for p in perfs]
    winr = [p["win_rate"] * 100 for p in perfs]
    axes = [
        ("年化收益", [norm(v, min(annuals) * 0.9, max(annuals) * 1.1) for v in annuals]),
        ("夏普", [norm(v, min(sharpes) * 0.8, max(sharpes) * 1.2) for v in sharpes]),
        ("卡玛", [norm(v, min(calmars) * 0.9, max(calmars) * 1.2) for v in calmars]),
        ("回撤(越大越好)", [norm(v, min(dds) * 1.05, max(dds) * 1.1) for v in dds]),
        ("胜率", [norm(v, min(winr), max(winr)) for v in winr]),
    ]
    nax = len(axes)
    parts = []
    # 网格多边形 + 环线
    for ring in (0.25, 0.5, 0.75, 1.0):
        pts = []
        for k in range(nax):
            ang = -90 + k * 360 / nax
            pts.append("%.1f,%.1f" % (cx + R * ring * __cos(ang), cy + R * ring * __sin(ang)))
        parts.append('<polygon points="%s" fill="none" stroke="#e5e7eb" stroke-width="1"/>' % " ".join(pts))
    for k in range(nax):
        ang = -90 + k * 360 / nax
        x, y = cx + R * __cos(ang), cy + R * __sin(ang)
        parts.append(svg_line(cx, cy, x, y, color="#e5e7eb"))
    # 轴标签
    for k, (label, vals) in enumerate(axes):
        ang = -90 + k * 360 / nax
        lx, ly = cx + (R + 34) * __cos(ang), cy + (R + 34) * __sin(ang)
        parts.append(svg_text(lx, ly, label, size=13, fill="#374151", weight=600))
    # 各策略多边形
    for ri, r in enumerate(rows):
        vals = [axes[k][1][ri] for k in range(nax)]
        pts = []
        for k, v in enumerate(vals):
            ang = -90 + k * 360 / nax
            pts.append("%.1f,%.1f" % (cx + R * v * __cos(ang), cy + R * v * __sin(ang)))
        gid = "radar_%d" % ri
        parts.append('<defs><linearGradient id="%s" x1="0" y1="0" x2="0" y2="1">'
                     '<stop offset="0" stop-color="%s" stop-opacity="0.35"/>'
                     '<stop offset="1" stop-color="%s" stop-opacity="0.06"/>'
                     '</linearGradient></defs>' % (gid, r["color"], r["color"]))
        parts.append('<polygon points="%s" fill="url(#%s)" stroke="%s" stroke-width="2"/>'
                     % (" ".join(pts), gid, r["color"]))
        # 顶点
        for k, v in enumerate(vals):
            ang = -90 + k * 360 / nax
            px, py = cx + R * v * __cos(ang), cy + R * v * __sin(ang)
            parts.append('<circle cx="%.1f" cy="%.1f" r="3" fill="%s"/>' % (px, py, r["color"]))
    parts.append(svg_text(W / 2, H - 4, "各策略相对画像（归一化，越大越好）", size=12, fill="#6b7280"))
    return '<svg viewBox="0 0 %d %d" width="100%%" preserveAspectRatio="xMidYMid meet" role="img">%s</svg>' % (W, H, "".join(parts))


def __cos(a):
    import math
    return math.cos(math.radians(a))


def __sin(a):
    import math
    return math.sin(math.radians(a))


def fmt_pct(v):
    return "%+.2f%%" % (v * 100)


def build_html(rows):
    equity = make_equity_svg(rows)
    drawdown = make_drawdown_svg(rows)
    yearly = make_yearly_svg(rows)
    radar = make_radar_svg(rows)
    # 指标卡
    cards = []
    for r in rows:
        p = r["perf"]
        is_base = "vt" in r and r["vt"] == 0.0
        cards.append("""
        <div class="card %s" style="--accent:%s">
          <div class="card-head"><span class="vt-name">%s</span>
            <span class="pill">%s</span></div>
          <div class="metric"><span class="k">年化收益</span><span class="v">%s</span></div>
          <div class="metric"><span class="k">最大回撤</span><span class="v neg">%s</span></div>
          <div class="metric"><span class="k">夏普</span><span class="v">%.3f</span></div>
          <div class="metric"><span class="k">卡玛</span><span class="v">%.2f</span></div>
          <div class="metric"><span class="k">总收益</span><span class="v">%s</span></div>
          <div class="metric"><span class="k">胜率 / 交易</span><span class="v">%.1f%% / %d</span></div>
          <div class="metric"><span class="k">平均持仓</span><span class="v">%.1f 只</span></div>
          <div class="metric"><span class="k">资金利用率</span><span class="v">%.1f%%</span></div>
        </div>""" % (
            "base" if is_base else "", r["color"], r["name"],
            "部署基线" if is_base else "",
            fmt_pct(p["annual_return"]), fmt_pct(p["max_drawdown"]),
            p["sharpe"], p["calmar"], fmt_pct(p["total_return"]),
            p["win_rate"] * 100, p["n_trades"], r["n_pos"][0], r["util"] * 100))
    cards_html = "\n".join(cards)
    # 表格
    trs = []
    for r in rows:
        p = r["perf"]
        trs.append("""
        <tr class="%s">
          <td><span class="dot" style="background:%s"></span>%s</td>
          <td>%s</td><td>%s</td><td>%s</td><td>%.3f</td><td>%.2f</td>
          <td>%.1f%%</td><td>%d</td><td>%.1f</td><td>%.1f%%</td>
        </tr>""" % ("row-base" if r["vt"] == 0.0 else "", r["color"], r["name"],
                    fmt_pct(p["total_return"]), fmt_pct(p["annual_return"]),
                    fmt_pct(p["max_drawdown"]), p["sharpe"], p["calmar"],
                    p["win_rate"] * 100, p["n_trades"], r["n_pos"][0], r["util"] * 100))
    table = "\n".join(trs)
    # 图例
    legend = "".join(
        '<span class="lg"><i style="background:%s"></i>%s</span>' % (r["color"], r["name"])
        for r in rows)
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>ATR 10万/8只+价<50 · vol_target 无杠杆扫描看板 · 2026-08-15</title>
<style>
  :root { --ink:#111827; --sub:#6b7280; --line:#e5e7eb; --bg:#f4f6fb; --card:#ffffff; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
         background:var(--bg); color:var(--ink); line-height:1.5; }
  .wrap { max-width:1180px; margin:0 auto; padding:28px 22px 60px; }
  header.hero { background:linear-gradient(135deg,#1e3a8a 0%,#2563eb 55%,#3b82f6 100%);
    color:#fff; border-radius:16px; padding:28px 32px; box-shadow:0 10px 30px rgba(37,99,235,.25); }
  header.hero h1 { margin:0 0 6px; font-size:24px; letter-spacing:.5px; }
  header.hero .sub { opacity:.92; font-size:14px; }
  .badges { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; }
  .badge { background:rgba(255,255,255,.16); border:1px solid rgba(255,255,255,.28);
    padding:4px 12px; border-radius:999px; font-size:12.5px; }
  .badge.red { background:rgba(239,68,68,.85); border-color:transparent; font-weight:600; }
  h2.sec { margin:34px 0 14px; font-size:19px; display:flex; align-items:center; gap:10px; }
  h2.sec::before { content:""; width:5px; height:20px; border-radius:3px; background:#2563eb; }
  .cards { display:grid; grid-template-columns:repeat(5,1fr); gap:14px; }
  @media (max-width:1000px){ .cards{grid-template-columns:repeat(2,1fr);} }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px;
    padding:14px 16px; box-shadow:0 2px 8px rgba(17,24,39,.05); position:relative; }
  .card.base { border:2px solid var(--accent); box-shadow:0 4px 14px rgba(37,99,235,.18); }
  .card-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }
  .vt-name { font-weight:700; font-size:15px; }
  .pill { font-size:11px; background:var(--accent); color:#fff; padding:2px 8px;
    border-radius:999px; opacity:.92; }
  .metric { display:flex; justify-content:space-between; padding:4px 0;
    border-bottom:1px dashed #f0f0f4; font-size:13px; }
  .metric:last-child { border-bottom:none; }
  .metric .k { color:var(--sub); }
  .metric .v { font-weight:600; }
  .metric .v.neg { color:#dc2626; }
  .panel { background:var(--card); border:1px solid var(--line); border-radius:14px;
    padding:18px 20px 12px; box-shadow:0 2px 8px rgba(17,24,39,.05); margin-bottom:18px; }
  .legend { display:flex; flex-wrap:wrap; gap:16px; margin:4px 2px 12px; font-size:13px; color:#374151; }
  .lg { display:inline-flex; align-items:center; gap:6px; }
  .lg i { width:16px; height:4px; border-radius:2px; display:inline-block; }
  table { width:100%; border-collapse:collapse; font-size:13.5px; }
  th, td { padding:9px 10px; text-align:right; border-bottom:1px solid #f0f0f4; }
  th:first-child, td:first-child { text-align:left; }
  th { color:var(--sub); font-weight:600; font-size:12.5px; white-space:nowrap; }
  tbody tr:hover { background:#f8fafc; }
  tr.row-base td { background:#eff6ff; }
  .dot { width:10px; height:10px; border-radius:50%; display:inline-block; margin-right:8px; }
  .callout { border-left:4px solid #ef4444; background:#fef2f2; border-radius:8px;
    padding:14px 18px; font-size:14px; margin:14px 0; }
  .callout b { color:#b91c1c; }
  .callout.good { border-left-color:#059669; background:#ecfdf5; }
  .callout.good b { color:#047857; }
  ul { margin:8px 0; padding-left:20px; }
  li { margin:5px 0; font-size:14px; }
  .foot { margin-top:34px; color:var(--sub); font-size:12px; text-align:center; }
  .grid2 { display:grid; grid-template-columns:1.6fr 1fr; gap:18px; }
  @media (max-width:1000px){ .grid2{grid-template-columns:1fr;} }
</style>
</head>
<body>
<div class="wrap">
  <header class="hero">
    <h1>ATR 低波动 · 10万/8只 + 真实价&lt;50 · vol_target 无杠杆扫描看板</h1>
    <div class="sub">区间 2019-01-01 ~ 2026-06-30 · 全A PIT · next_open · 真实整数手/涨跌停/停牌约束 · 季频 · 止损-8% · 换手1-8% · 动量/ROE门控 · 无杠杆（target_leverage=1.0 硬上限，vol_target 只能向下缩敞口）</div>
    <div class="badges">
      <span class="badge">本金 10 万</span><span class="badge">持仓 8 只等权</span>
      <span class="badge">真实价 &lt; 50 元</span><span class="badge">配置 atr_10w_price50.yaml</span>
      <span class="badge">2026-08-15 生成</span>
      <span class="badge red">结论：不值得实现 VT</span>
    </div>
  </header>

  <h2 class="sec">一、五版指标总览</h2>
  <div class="cards">__CARDS__</div>

  <h2 class="sec">二、净值曲线（起点 100，对数刻度）</h2>
  <div class="panel"><div class="legend">__LEGEND__</div>__EQUITY__</div>

  <div class="grid2">
    <div>
      <h2 class="sec">三、回撤曲线</h2>
      <div class="panel">__DRAWDOWN__</div>
    </div>
    <div>
      <h2 class="sec">四、策略相对画像</h2>
      <div class="panel">__RADAR__</div>
    </div>
  </div>

  <h2 class="sec">五、分年度收益</h2>
  <div class="panel">__YEARLY__</div>

  <h2 class="sec">六、明细对照表</h2>
  <div class="panel">
    <table>
      <thead><tr><th>版本</th><th>总收益</th><th>年化</th><th>最大回撤</th><th>夏普</th>
        <th>卡玛</th><th>胜率</th><th>交易数</th><th>平均持仓</th><th>资金利用率</th></tr></thead>
      <tbody>__TABLE__</tbody>
    </table>
  </div>

  <h2 class="sec">七、结论</h2>
  <div class="panel">
    <div class="callout"><b>不值得给 8只 无杠杆部署实现 vol_target（证伪）。</b>无杠杆下 VT 是纯降敞口工具（平均仓位 90.3%→35.9%~74.2%），
    五档夏普 0.831/0.797/0.880/0.868 全部低于基线 0.894，卡玛 0.64~1.00 全部低于基线 1.17——风险调整指标无一改善。</div>
    <ul>
      <li><b>「0.18 甜点」不可移植</b>：那是 2023-2026/100万/50只/vol_parity/1.5x 两融 下靠恢复暴露回补收益的结论；本扫描无杠杆时 VT 越大越接近基线（0.20 年化 22.26% 仍比基线少 6.7pp）。</li>
      <li><b>回撤压降是真的但代价高昂</b>：最优档 0.18 回撤 -24.80%→-21.05%（仅压 3.75pp）代价年化 8.64pp（28.99→20.35），每压 1pp 回撤 ≈ 花 2.3pp 年化。</li>
      <li><b>削的是最好的年份</b>：2020 +9.7%→+0.5%~+3.7%、2025 +49.3%→≤43.5%，复利主力时段被削；熊市 2022 VT 无保护甚至略差，仅 2026 微降亏损。</li>
      <li><b>若要控回撤</b>：优先移植 MA200 大盘门控（P10 方向5 实证 -29.7%→-15.8% 仅 -4.4pp，效率约为 VT 的 7 倍），而非 VT。</li>
    </ul>
    <div class="callout good"><b>建议</b>：ATR 8只 部署维持现配置（无 VT，等权 8 只 + 真实价&lt;50 + R9 ROE空结果fail-open），
    -24.8% 回撤按既定预期接受；如后续要降回撤，上大盘 MA200 门控而不是 VT。</div>
  </div>

  <div class="foot">数据源 reports/voltarget_10w_price50_scan.json + reports/20260815_*_atr_10w_price50* · 脚本 research/{scan,report}_10w_price50_voltarget.py · 详细报告 results/voltarget扫描报告_20260815.md</div>
</div>
</body>
</html>"""
    for tok, val in [("__CARDS__", cards_html), ("__LEGEND__", legend),
                     ("__EQUITY__", equity), ("__DRAWDOWN__", drawdown),
                     ("__RADAR__", radar), ("__YEARLY__", yearly),
                     ("__TABLE__", table)]:
        html = html.replace(tok, val)
    return html


def main():
    rows = load()
    html = build_html(rows)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print("dashboard -> %s (%d KB)" % (OUT, os.path.getsize(OUT) // 1024))


if __name__ == "__main__":
    main()
