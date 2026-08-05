# QMT 交易看板 HTML 生成器 v2 — Task SPEC

> **版本**: v2.0
> **日期**: 2026-06-30
> **交付对象**: CC（Claude Code）
> **验收人**: Hermes

---

## 一、Objective（策略目标）

基于QMT每日导出的标准CSV文件 + 历史策略日志，生成一个交互式HTML交易看板。

**核心数据源**（按优先级）：
1. `D:\qmt_pool\成交明细_YYYYMMDD.csv` — 每日成交记录（GBK编码）
2. `D:\qmt_pool\持仓明细_YYYYMMDD.csv` — 每日持仓快照（GBK编码）
3. `D:\qmt_pool\资金概况_YYYYMMDD.csv` — 每日账户概况（GBK编码）
4. `D:\QMT_POOL\strategy_log_YYYYMMDD.txt` — 历史策略日志（GBK编码，补充净值走势）
5. `D:\QMT_POOL\endofday_sell_state_beat.json` — 历史卖出状态（UTF-8/GBK）
6. `\\192.168.31.131\qmt_pool\` — 远程共享的最新CSV数据

---

## 二、Commands（开发指令）

### 2.1 文件位置

- 脚本: `D:\hermes\scripts\qmt_dashboard_v2.py`
- 输出: `D:\QMT_STRATEGIES\trade_reports\dashboard_v2_YYYY-MM-DD.html`
- 同时输出 `dashboard_v2_latest.html`

### 2.2 开发约束

- Python 3.13 标准库（仅用 os/re/json/csv/io/datetime/collections）
- Chart.js 通过 CDN 引用（`https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js`）
- 脚本本身 UTF-8 编码，CSV/日志文件用 GBK 解码

---

## 三、Structure（策略结构）

### 3.1 CSV文件字段说明

**成交明细CSV列名**（实际列名以文件为准，用csv.DictReader读取）：
资金账号,成交日期,成交时间,交易所,证券代码,证券名称,买卖标记,成交数量,成交价格,成交金额,手续费,成交编号,合同编号,订单编号,任务编号,投资备注,账号备注,分支机构,投资备注1,股东号

- 买卖标记值: "限价买入"或"限价卖出"（含"买入"/"卖出"关键字）
- 证券代码: 纯数字（如600397），无.SH/.SZ后缀

**持仓明细CSV列名**：
资金账号,交易所,证券代码,证券名称,当前拥股,可用数量,冻结数量,成本价,最新价,持仓盈亏,浮动盈亏,盈亏比例,当日涨幅,市值,持仓成本,股东账号,市场名称,资产占比,市值占比,状态,分支机构,非流通股,当日盈亏

- 盈亏比例: 百分比数值（如9.06表示9.06%）
- 持仓盈亏: 金额
- 当前拥股=0且成本价=0的行表示已清仓但有盈亏记录的，也要保留

**资金概况CSV列名**：
资金账号,账号名称,账号备注,登录状态,操作,总资产,净资产,总负债,总市值,可用金额,冻结金额,持仓盈亏,手续费,可取金额,股票总市值,基金总市值,债券总市值,回购总市值,报撤单比,分支机构,资金余额,今日账号盈亏

### 3.2 数据合并逻辑

**净值走势**：
1. 从资金概况CSV取总资产+股票总市值
2. 从策略日志取总资产+持仓市值
3. 按日期合并，CSV数据优先覆盖日志数据

**股票盈亏明细**：
1. 从 endofday_sell_state_beat.json（本地+远程）读取每只股票的完整生命周期
2. 股票名称从成交明细CSV、持仓明细CSV、selected.txt 三个来源补充
3. CSV中的证券代码无后缀（如600397），sell_state中的key有后缀（如600397.SH），名称映射时两种格式都要存

**成交记录**：
1. 从成交明细CSV读取所有日期的数据
2. 按日期+时间倒序排列

**最新持仓**：
1. 从持仓明细CSV读取，取最新日期的数据
2. 当前拥股>0或成本价>0的行都要显示

### 3.3 脚本结构

```
qmt_dashboard_v2.py
├── read_gbk(path) — 安全读取GBK文件
├── parse_csv_rows(text) — CSV解析（DictReader）
├── collect_trade_csvs() — 收集所有成交明细CSV
├── collect_position_csvs() — 收集所有持仓明细CSV
├── collect_fund_csvs() — 收集所有资金概况CSV
├── collect_strategy_logs() — 收集策略日志（本地+远程）
├── collect_sell_state() — 收集卖出状态JSON（本地+远程）
├── build_all_data() — 合并所有数据
└── generate_html() — 生成HTML看板
```

---

## 四、Code Style（代码风格）

### 4.1 HTML看板要求

**配色**：深色主题
- 背景: #0f172a
- 卡片: #1e293b
- 边框: #334155
- 文字: #e2e8f0

**A股颜色风格**（关键！）：
- 盈利/上涨 → 红色 (#f87171)
- 亏损/下跌 → 绿色 (#4ade80)
- 买入标签 → 红底 (#450a0a 背景, #f87171 文字)
- 卖出标签 → 绿底 (#064e3b 背景, #4ade80 文字)
- 总资产线 → 红色 (#ef4444)
- 持仓市值线 → 紫色 (#a78bfa)

**功能模块**：

1. **顶部统计卡片**（6个）：总资产、持仓市值、区间收益、股票胜率、最大回撤、已清仓盈亏
2. **时间筛选器**：两个 `<input type="date">` + 确认/重置按钮，日期格式 YYYY-MM-DD ↔ YYYYMMDD 转换
3. **净值走势图**：Chart.js 折线图，双Y轴（左=总资产，右=持仓市值）
4. **最新持仓表**：代码、名称、成本价、现价、持仓数量、市值、盈亏%、当日涨幅
5. **股票盈亏明细表**：代码、名称、成本价、最高价、盈亏%、盈亏额、状态、持有天数、清仓日、卖出原因
6. **成交记录表**：日期、时间、方向、代码、名称、价格、数量、金额、手续费

**数据筛选逻辑**：
- 所有数据嵌入HTML的JSON中
- 前端JavaScript按时间范围筛选
- 筛选后重新计算所有统计指标

### 4.2 股票名称补全（关键！）

CSV中的证券代码无后缀（如600397），但卖出状态JSON的key有后缀（如600397.SH）。名称映射必须同时存两种格式：

```python
# CSV读取时
stock_names['600397'] = '江钨装备'
stock_names['600397.SH'] = '江钨装备'
stock_names['600397.SZ'] = '江钨装备'

# 从selected.txt补充（格式：代码\t名称\t...）
# 600397	江钨装备	2026-06-30	...
```

如果所有来源都找不到名称，回退显示纯数字代码（如"600397"）。

---

## 五、Testing（回测验证）

### 5.1 验收标准

1. ✅ 脚本运行成功，生成HTML文件
2. ✅ 股票名称正确显示中文名（如"江钨装备"、"华润微"），不是代码数字
3. ✅ 净值走势图包含所有历史日期（从2023年11月到最新）
4. ✅ 股票盈亏明细包含所有历史股票（不仅是当天的）
5. ✅ 时间筛选器工作正常
6. ✅ A股颜色风格正确（红涨绿跌）
7. ✅ CSV文件GBK解码正确
8. ✅ 策略日志GBK解码正确

### 5.2 验证命令

```bash
cd D:/QMT_STRATEGIES
python D:/hermes/scripts/qmt_dashboard_v2.py
# 检查HTML中的股票名称
grep -o '"code":"[^"]*","name":"[^"]*"' D:/QMT_STRATEGIES/trade_reports/dashboard_v2_latest.html
```

---

## 六、Boundaries（边界约束）

### 6.1 不做的事

- ❌ 不修改 D:\qmt_pool\ 下的任何文件
- ❌ 不修改 D:\QMT_POOL\ 下的任何文件
- ❌ 不修改 D:\hermes\scripts\qmt_dashboard.py（v1保留不动）
- ❌ 不使用外部Python依赖（仅标准库）
- ❌ 不做git操作

### 6.2 注意事项

- 文件编码：脚本UTF-8，CSV/日志GBK
- 卖出状态JSON编码不确定（UTF-8或GBK），需要自动探测
- 远程共享 \\192.168.31.131\qmt_pool\ 可能不可达，需要try/except保护
- 历史数据可能不全，空数据时显示友好提示
