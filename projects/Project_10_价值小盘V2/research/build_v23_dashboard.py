# coding=utf-8
"""v2.3 看板构建 (阶段2): 读 v23_dashboard_data.json + 现有消融/排雷/窗口结果, 生成 Chart.js HTML。
用法: python research/build_v23_dashboard.py
产物: reports/回测看板_V2a_v2.3.html (自包含, Chart.js 走 CDN)"""
import os, json
import pandas as pd

PROJ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(PROJ_DIR, "results")
REP = os.path.join(PROJ_DIR, "reports")
os.makedirs(REP, exist_ok=True)

data = json.load(open(os.path.join(RES, "v23_dashboard_data.json"), encoding="utf-8"))
hits = pd.read_csv(os.path.join(RES, "a_delisting_screen_hits.csv"))

V = data["variants"]
meta = data["meta"]
st_base, st_buf, st_v23 = V["baseline"]["stats"], V["buffer160"]["stats"], V["v23"]["stats"]

NAME = {"benchmark": "基准(合格域等权)", "baseline": "V2a基线(全量重建)",
        "buffer160": "buffer160(无排雷)", "v23": "v2.3(buffer160+退市排雷)"}
COLOR = {"benchmark": "#8b949e", "baseline": "#d29922", "buffer160": "#bc8cff", "v23": "#3fb950"}

def pct(x, nd=1):
    return "-" if x is None else ("%+.*f%%" % (nd, x * 100))

def f2(x):
    return "-" if x is None else ("%.2f" % x)

# ---------- 卡片 (v2.3) ----------
cards = [
    ("年化收益", pct(st_v23["ann"]), "vs 基线 %s" % pct(st_base["ann"])),
    ("全期超额", pct(st_v23["ex_full"]), "基线 %s" % pct(st_base["ex_full"])),
    ("最大回撤", pct(st_v23["max_dd"]), "与基线持平"),
    ("夏普比率", f2(st_v23["sharpe"]), "双月期频"),
    ("平均换手", "%.2f" % st_v23["turnover"], "基线 %.2f" % st_base["turnover"]),
    ("超额2024+", pct(st_v23["ex_2024"]), "基线 %s" % pct(st_base["ex_2024"])),
]
cards_html = "".join(
    '<div class="card"><div class="k">%s</div><div class="v">%s</div><div class="sub">%s</div></div>'
    % (k, v, s) for k, v, s in cards)

# ---------- 变体对比表 ----------
def vrow(label, st, hl=False):
    cls = ' class="hl"' if hl else ""
    return ("<tr%s><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%.2f</td><td>%s</td><td>%s</td><td>%s</td></tr>") % (
        cls, label, pct(st["ann"]), pct(st["total"]), pct(st["max_dd"]), f2(st["sharpe"]),
        f2(st["calmar"]), "%.0f%%" % (st["win_rate"] * 100), st["turnover"],
        pct(st["ex_full"]), pct(st["ex_2024"]), pct(st["ex_2026"]))

variant_rows = vrow(NAME["baseline"], st_base) + vrow(NAME["buffer160"], st_buf) + vrow(NAME["v23"], st_v23, hl=True)

# ---------- buffer 消融表 (b_buffer_ablation.txt 存档) ----------
ablation = [
    ("全量重建(基线)", 16.3, -29.7, 0.91, 201.6, 35.2, 0.4, False),
    ("buffer 1.5x(120)", 17.2, -29.7, 0.83, 225.3, 46.8, 0.0, False),
    ("buffer 2.0x(160)", 18.0, -29.7, 0.80, 244.9, 50.1, 0.8, True),
    ("buffer 2.5x(200)", 17.7, -29.7, 0.79, 237.6, 50.9, 0.3, False),
]
abl_rows = "".join(
    ('<tr class="hl">' if r[7] else "<tr>") +
    "<td>%s</td><td>+%.1f%%</td><td>%.1f%%</td><td>%.2f</td><td>+%.1f%%</td><td>+%.1f%%</td><td>%+.1f%%</td><td>%s</td></tr>"
    % (r[0], r[1], r[2], r[3], r[4], r[5], r[6], "★采纳" if r[7] else "")
    for r in ablation)

# ---------- 退市排雷命中表 (组件A) ----------
def board_cn(b):
    return {"MAIN": "主板", "GEM_STAR": "创业/科创", "BJ": "北交所"}.get(b, b)
hit_rows = "".join(
    "<tr><td>%s</td><td>%s</td><td>%s</td><td>%.1f亿</td><td>%d</td><td>%s</td><td>%s</td></tr>" % (
        r.d, r.ts_code, board_cn(r.board), r.total_mv / 10000.0, r.bp_rank,
        "是" if r.hit_r1_mv else "否", "是" if r.hit_r2_delist else "否")
    for r in hits.itertuples())

# ---------- 年度收益表 ----------
yearly = data["yearly"]
years = sorted({int(y) for y in set(list(yearly["benchmark"].keys()) + list(yearly["v23"].keys()))})
def ycell(v):
    if v is None:
        return "<td>-</td>"
    return '<td style="color:%s">%+.1f%%</td>' % ("#3fb950" if v >= 0 else "#f85149", v)
year_rows = "".join(
    "<tr><td>%d</td>%s%s%s</tr>" % (y, ycell(yearly["benchmark"].get(str(y))),
                                    ycell(yearly["baseline"].get(str(y))), ycell(yearly["v23"].get(str(y))))
    for y in years)

payload = json.dumps(data, ensure_ascii=False)

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#c9d1d9;font-family:'Segoe UI','Microsoft YaHei',system-ui,sans-serif;padding:24px}
.header{text-align:center;padding:16px 0 26px;border-bottom:1px solid #30363d;margin-bottom:26px}
.header h1{font-size:26px;color:#58a6ff}
.header .subtitle{color:#8b949e;margin-top:8px;font-size:13px}
.section{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px;margin-bottom:22px}
.section h2{font-size:17px;color:#58a6ff;margin-bottom:14px;border-bottom:1px solid #30363d;padding-bottom:9px}
.cards{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:22px}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;text-align:center}
.card .k{color:#8b949e;font-size:12px;margin-bottom:6px}
.card .v{font-size:23px;font-weight:700;color:#3fb950}
.card .sub{color:#6b7785;font-size:11px;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#21262d;color:#58a6ff;padding:9px 11px;text-align:left;border-bottom:2px solid #30363d}
td{padding:7px 11px;border-bottom:1px solid #21262d}
tr.hl{background:#1f6feb1c}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.tag-good{background:#3fb95033;color:#3fb950;border:1px solid #3fb95055}
.tag-warn{background:#d2992233;color:#d29922;border:1px solid #d2992255}
.highlight{background:#1f6feb22;border-left:3px solid #58a6ff;padding:12px 16px;margin:10px 0;border-radius:0 5px 5px 0;line-height:1.7}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.chartbox{position:relative;height:340px}
.note{color:#8b949e;font-size:12px;margin-top:8px;line-height:1.6}
.footer{text-align:center;color:#8b949e;font-size:12px;margin-top:36px;padding-top:18px;border-top:1px solid #30363d}
ul{margin:6px 0 6px 22px}
"""

JS = """
const D = __DATA__;
const C = __COLOR__;
const NM = __NAME__;
function lineDataset(key, field, color, dashed){
  return {label:NM[key], data:D.variants[key][field], borderColor:color, backgroundColor:color,
    borderWidth:2, pointRadius:0, tension:0.15, borderDash: dashed?[6,4]:[]};
}
const optBase = {responsive:true, maintainAspectRatio:false,
  interaction:{mode:'index',intersect:false},
  plugins:{legend:{labels:{color:'#c9d1d9',font:{size:12}}},
    tooltip:{backgroundColor:'#161b22',borderColor:'#30363d',borderWidth:1}},
  scales:{x:{ticks:{color:'#8b949e',maxTicksLimit:12,maxRotation:0},grid:{color:'#21262d33'}},
    y:{ticks:{color:'#8b949e'},grid:{color:'#21262d55'}}}};

// 净值曲线
new Chart(document.getElementById('navChart'), {type:'line',
  data:{labels:D.variants.v23.dates, datasets:[
    lineDataset('baseline','nav',C.baseline,false),
    lineDataset('buffer160','nav',C.buffer160,true),
    lineDataset('v23','nav',C.v23,false)]},
  options:Object.assign({},optBase,{plugins:Object.assign({},optBase.plugins,
    {title:{display:true,text:'净值曲线 (期初=1, 双月调仓)',color:'#c9d1d9'}})})});

// 回撤曲线
new Chart(document.getElementById('ddChart'), {type:'line',
  data:{labels:D.variants.v23.dates, datasets:[
    lineDataset('baseline','drawdown',C.baseline,false),
    lineDataset('v23','drawdown',C.v23,false)]},
  options:Object.assign({},optBase,{plugins:Object.assign({},optBase.plugins,
    {title:{display:true,text:'回撤曲线 (%)',color:'#c9d1d9'}})})});

// 换手曲线
new Chart(document.getElementById('toChart'), {type:'line',
  data:{labels:D.variants.v23.dates, datasets:[
    lineDataset('baseline','turnover',C.baseline,false),
    lineDataset('v23','turnover',C.v23,false)]},
  options:Object.assign({},optBase,{plugins:Object.assign({},optBase.plugins,
    {title:{display:true,text:'每期单边换手 (基线 vs v2.3)',color:'#c9d1d9'}})})});

// 年度收益
const yrs = __YEARS__;
new Chart(document.getElementById('yearChart'), {type:'bar',
  data:{labels:yrs, datasets:[
    {label:NM.benchmark, data:__YB__, backgroundColor:C.benchmark},
    {label:NM.baseline, data:__YBASE__, backgroundColor:C.baseline},
    {label:NM.v23, data:__YV23__, backgroundColor:C.v23}]},
  options:Object.assign({},optBase,{plugins:Object.assign({},optBase.plugins,
    {title:{display:true,text:'年度收益 (%)',color:'#c9d1d9'}})})});
"""

JS = (JS.replace("__DATA__", payload)
        .replace("__COLOR__", json.dumps(COLOR))
        .replace("__NAME__", json.dumps(NAME, ensure_ascii=False))
        .replace("__YEARS__", json.dumps(years))
        .replace("__YB__", json.dumps([yearly["benchmark"].get(str(y)) for y in years]))
        .replace("__YBASE__", json.dumps([yearly["baseline"].get(str(y)) for y in years]))
        .replace("__YV23__", json.dumps([yearly["v23"].get(str(y)) for y in years])))

html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>V2a v2.3 回测看板</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>__CSS__</style></head>
<body>

<div class="header">
  <h1>价值小盘 V2a · v2.3 回测看板</h1>
  <div class="subtitle">纯行业中性BP + 质量排雷 + 退市排雷 + buffer降换手 ｜ __RANGE__ ｜ 双月调仓·80只·等权 ｜ 生成 __GEN__</div>
</div>

<div class="section">
  <h2>核心结论</h2>
  <div class="highlight">
    <strong>v2.3（buffer160 + 退市排雷）并入 V2a 生产：</strong>年化 <strong>16.3% → 18.0%</strong>、
    全期超额 <strong>+201.6% → +244.9%</strong>、换手 <strong>0.91 → 0.80</strong>，回撤持平，
    2024+ 超额 <strong>+35.2% → +50.1%</strong>（否决项全过）。buffer=0 精确复现存档 +16.2%/+200.9%。
    <ul>
      <li><span class="tag tag-good">buffer 2.0x(160)</span> 为采纳档位：年化最高 18.0%，2024+ 显著占优</li>
      <li><span class="tag tag-good">退市排雷</span> 拦下 4 只真实退市股（3 只 BP 排名第 1），纯收益无损</li>
      <li><span class="tag tag-warn">风控断路器</span> 已证伪分层降仓：危机窗口二者表现完全相同，维持 15% 一刀清仓</li>
    </ul>
  </div>
</div>

<div class="cards">__CARDS__</div>

<div class="section"><h2>净值曲线</h2><div class="chartbox"><canvas id="navChart"></canvas></div>
  <div class="note">基准=合格域(市值&lt;30亿·pe/pb&gt;0·非ST非停牌)等权。buffer160(无排雷) 与 v2.3 净值几乎重合，说明退市排雷对纯收益无损。</div></div>

<div class="grid2">
  <div class="section"><h2>回撤</h2><div class="chartbox"><canvas id="ddChart"></canvas></div></div>
  <div class="section"><h2>每期换手</h2><div class="chartbox"><canvas id="toChart"></canvas></div></div>
</div>

<div class="section"><h2>年度收益</h2><div class="chartbox"><canvas id="yearChart"></canvas></div></div>

<div class="section"><h2>变体指标对比</h2>
  <table><tr><th>变体</th><th>年化</th><th>全期收益</th><th>最大回撤</th><th>夏普</th><th>Calmar</th><th>胜率</th><th>换手</th><th>超额全期</th><th>超额2024+</th><th>超额2026</th></tr>
  __VARIANT_ROWS__</table>
  <div class="note">夏普/Calmar 基于双月期频收益；超额相对合格域等权基准。</div></div>

<div class="section"><h2>组件B · buffer 档位消融（存档）</h2>
  <table><tr><th>档位</th><th>年化</th><th>回撤</th><th>换手</th><th>超额全期</th><th>超额2024+</th><th>超额2026</th><th>结论</th></tr>
  __ABLATION_ROWS__</table>
  <div class="note">来源 results/b_buffer_ablation.txt。否决判据：2024+ 不得劣于基线 +35.2%，三档全过。2.0x(160) 年化与全期超额最高，采纳。</div></div>

<div class="section"><h2>组件A · 退市排雷命中明细</h2>
  <table><tr><th>调仓日</th><th>代码</th><th>板块</th><th>总市值</th><th>BP排名</th><th>市值红线</th><th>退市临近</th></tr>
  __HIT_ROWS__</table>
  <div class="note">全期命中 4 只次，全部落在 TOP80（3 只 BP 排名第 1）；单期命中 ≤1 只，远低于升级判据(≥3)。
  规则：已退市剔除·距退市日≤30天剔除·主板总市值&lt;7.5亿/创业科创&lt;4.5亿剔除(红线×1.5缓冲)·北交所不适用市值红线。</div></div>

<div class="section"><h2>组件C · 2024危机窗口压力对比（存档）</h2>
  <div class="highlight">
    <strong>分层降仓 ≡ 断路器：</strong>2024-01~02 微盘危机窗口，V2a 口径下两机制逐期收益完全一致
    （危机期均 -12.18% / 超额 +7.23%，2024 全年均 +25.5%~25.9%）。
    机制：双月调仓只在换仓日查风控，暴跌一步到位直接触发最高档清仓，中间档形同虚设。
    <strong>结论：维持 15% 一刀清仓断路器，分层降仓永不启用（已登记研究总览"已证伪"）。</strong>
  </div>
  <div class="note">来源 results/c_window_stress_comparison.txt。自检复现存档：V1口径 R0 +15.3%/-29.1%/+178.0%、R2 +15.2%/-32.7%/+176.4%。</div></div>

<div class="section"><h2>年度收益明细</h2>
  <table><tr><th>年份</th><th>基准</th><th>V2a基线</th><th>v2.3</th></tr>__YEAR_ROWS__</table></div>

<div class="section"><h2>数据来源</h2>
  <table>
    <tr><th>项目</th><th>值</th></tr>
    <tr><td>行情数据</td><td>E:/astock/daily/stock_daily.parquet（含 total_mv）</td></tr>
    <tr><td>财务数据</td><td>E:/astock/finance/fina_indicator.parquet（PIT 按 ann_date）</td></tr>
    <tr><td>退市信息</td><td>E:/astock/basic/stock_basic.parquet → D:/QMT_POOL/delist_info.csv</td></tr>
    <tr><td>回测引擎</td><td>projects/Project_10_价值小盘V2/runner.py（v2.3：delist_screen + buffer_keep=160）</td></tr>
    <tr><td>交易成本</td><td>单边千一（佣金+印花税+滑点合并口径）</td></tr>
    <tr><td>风控</td><td>8%止损 / 60天持有上限 / 15%组合回撤断路器</td></tr>
  </table></div>

<div class="footer">
  <p>QuantLab · Project_10 价值小盘V2 ｜ v2.3 讨论室拆解吸收（诚哥批准）｜ commit 9e000be</p>
  <p>状态：已集成退市排雷+buffer，待模拟盘验证 ｜ 生成时间 __GEN__</p>
</div>

<script>__JS__</script>
</body></html>
"""

html = (html.replace("__CSS__", CSS)
            .replace("__JS__", JS)
            .replace("__CARDS__", cards_html)
            .replace("__VARIANT_ROWS__", variant_rows)
            .replace("__ABLATION_ROWS__", abl_rows)
            .replace("__HIT_ROWS__", hit_rows)
            .replace("__YEAR_ROWS__", year_rows)
            .replace("__RANGE__", meta["range"])
            .replace("__GEN__", meta["gen_time"]))

out = os.path.join(REP, "回测看板_V2a_v2.3.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("已生成:", out, os.path.getsize(out), "bytes")
