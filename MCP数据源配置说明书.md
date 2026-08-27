# MCP 数据源配置说明书

> 适用范围：QuantLab 全部项目，重点是 Project_16_LightGBM股票大师 的实盘链路
>
> 数据依据：2026-08-26 全量连通性实测校准（38 个股市 MCP server 逐个调用验证，非凭记忆）
>
> 维护约定：数据源行为发生变化时，先实测后更新本文档，禁止凭记忆改写

---

## 一、总览：已装数据源清单与定位

| 数据源 | 接入方式 | 状态 | 实测速度（2026-08-27 实测） | 定位 |
|---|---|---|---|---|
| `mcp_tdx` | TRAE 全局挂载的自建 TDX 服务 | 可用·一级主力 | 约 40ms | 批量行情首选，免 Key |
| 腾讯 `qt.gtimg.cn` | 免费 HTTP API，无需 Key | 可用·二级补充 | 约 291ms（历史 156ms） | 补齐量比/PE/PB/流通市值/涨跌停价 |
| iFind（同花顺）MCP | 远程 HTTP 型 MCP（Bearer 鉴权） | 可用·三级金融主力 | 约 1-2 秒（实时快照 5 指标双股） | 主力资金/财务/历史K线/公告/指数/智能选股，7 服务全覆盖 |
| 东方财富 mx（`mx-ds-mcp`） | 远程 MCP | 可用·四级金融主力 | 约 1-2 秒（个股多日多指标） | A股/港美股/基金/债券/宏观全量金融，字段最全，单次500只 |
| 东方财富 `push2.eastmoney.com` | 免费 HTTP API，需伪装请求头 | 可用·五级专用 | 约 174ms（历史 308ms） | 仅主力资金流备选（iFind/mx 取不到时） |
| full-link 内置 TDX | full-link-stock-analysis 插件自带 | 可用·六级冗余 | 约 1-2 秒（单股全字段） | 单股字段最全的冗余位 |
| `mcp_plugin_TDX_tdx`（独立 TDX 插件） | TDX 插件独立实例 | 可用·七级冗余 | 秒级（lookup/quotes 均通） | 2026-08-26 实测已恢复可用，完整行情+估值 |
| miniQMT / ashare-mcp | 本地客户端 + HTTP 桥接 | 可用·八级兜底 | 视场景 | 交易通道与兜底行情 |
| `mcp_wudao`（悟道） | 远程 HTTP 型 MCP，Bearer 鉴权 | 可用·特色源 | 未单独计时 | 新闻热榜/涨停梯队/龙虎榜/题材热度等信息面 |
| 万得 Wind（CLI） | wind-mcp-skill 官方 CLI | 可用·备用 | 约 952ms（含分钟线大返回） | 港美股/债券/宏观/公告专业深研，单标的耗积分 |
| TuShare | 远程 MCP（220+ 接口） | 可用·备用 | 视接口（免费版限频：trade_cal 1次/小时） | 沪深股票/指数/财务/资金流/龙虎榜，需 Token |
| AKShare | Python CLI（24 预置接口） | 可用·备用 | 约 2.1s（A股日K含进程启动；历史接口秒级） | 免费免 Key 的历史数据补充（A股日K/宏观/期货/基金/ETF/债券），东财实时接口受限 |
| 盈米 `yingmi` | 远程 MCP（67 工具） | 可用·备用 | 秒级（基金完整诊断） | 基金/组合/资产配置诊断 |
| `mcp_tradingagents` | 本地分析服务 | 可用·备用 | 秒级 | 个股分析报告生成/回顾 |

### 统一优先级体系（2026-08-27 实测定版 v4）

取数顺序固定为八级，前一级失败或字段缺失时才降级到下一级：

1. `mcp_tdx` —— 最快、免 Key、支持批量，承担全部基础行情
2. 腾讯 API —— 补充 mcp_tdx 缺失的独有字段（量比/PE/PB/流通市值/涨跌停价）
3. iFind —— 主力资金流/财务/历史K线/公告/指数/智能选股的专业补充
4. 东方财富 mx —— 重要金融数据补充：A股/港美股/基金/债券/宏观全量，字段最全、自然语言问句、单次500只；含主力资金流（可替代东财 curl），iFind 取不到资金时优先用它
5. 东财 curl —— 主力资金流备选（iFind 与 mx 都取不到时），避免高频触发限频
6. full-link 内置 TDX —— 冗余校验位，字段最全但不支持批量
7. 独立 `mcp_plugin_TDX_tdx` —— 冗余位（已恢复可用，勿再标记禁用）
8. miniQMT —— 兜底，注意红线（见第八章）
9. 其他来源 —— 原则上不使用

`mcp_wudao` 不参与行情优先级排序，作为信息面/情绪面的并行特色补充，与行情链路独立。

---

## 二、mcp_tdx（一级主力源）

### 接入方式

由 TRAE 客户端全局挂载的自建 TDX 数据服务，会话内直接以工具名调用，**不需要**也不应该写进项目的 `.mcp.json`（项目内 `.mcp.json` 只保留 metasearch 与 ashare-mcp 两项）。重装系统或换机时需重新在 TRAE 的 MCP 管理界面添加该服务后再验证。

### tdx_get_quote 批量实时行情

**代码格式是本工具最大的坑，必须纯六位代码、逗号分隔，不带交易所后缀：**

```
正确：tdx_get_quote(codes="300684,600519")
错误：tdx_get_quote(codes="300684.SZ,600519.SH")
      → Error executing tool tdx_get_quote: invalid code: '300684.sz'
```

交易所由服务端按六位代码段自动识别（6 开头沪市、0/3 开头深市），无需人工指定。

返回字段（2026-08-25 实测 300684/600519 双股批量）：

| 字段 | 含义 | 单位 |
|---|---|---|
| `last_price` | 最新价 | 元 |
| `open` / `high` / `low` | 今开/最高/最低 | 元 |
| `last_close_price` | 昨收 | 元 |
| `total_hand` | 成交量 | 手 |
| `amount` | 成交额 | 元 |
| `inside_dish` / `outer_disc` | 内盘/外盘 | 手 |
| `buy_levels` / `sell_levels` | 五档买盘/卖盘 | 价元量手 |
| `call_auction_amount` | **集合竞价成交额** | 元 |
| `call_auction_rate` | **集合竞价涨幅** | % |
| `server_time` | 行情时间戳 | - |

另有 `*_milli` 后缀的同名字段为毫单位版本，换算时注意。

**call_auction 两个字段是隐藏亮点**：9:25 集合竞价结束后即反映当日竞价结果，9:25 定时任务可直接从普通行情快照读取竞价成交额与竞价涨幅，无需额外接口。

### 已知缺陷（必须记住）

- 不含 PE、量比、PB、流通市值、涨跌停价 —— 这五类字段走腾讯 API 补齐（或 iFind/full-link）
- `rate`（涨速）字段深市数值异常不可信 —— 禁止作为决策输入

### 配套工具速查

| 工具 | 关键参数 | 用途 |
|---|---|---|
| `tdx_get_index_quote` | `code="000300"` | 大盘风控读沪深300指数快照 |
| `tdx_get_auction_0925` | `code`、`date` | 从历史逐笔中定位当日 9:25 竞价 tick |
| `tdx_get_turnover` | 六位代码 | 个股换手率 |
| `tdx_get_kline` / `tdx_get_kline_all` | 代码、周期 | K线历史 |
| `tdx_get_minute` / `tdx_get_trade_minute_kline` | 代码 | 分钟线 |
| `tdx_get_trades` / `tdx_get_trades_all` | 代码 | 逐笔成交 |
| `tdx_get_call_auction` | 代码 | 竞价数据 |
| `tdx_get_gbbq` / `tdx_get_xdxr` | 代码 | 股本变迁/除权除息 |
| `tdx_get_equity` / `tdx_get_finance` | 代码 | 股本/财务 |
| `tdx_get_block_info` / `tdx_get_code_list` | - | 板块/代码列表 |

---

## 三、腾讯免费 API（二级补充源）

唯一使命：补齐 mcp_tdx 缺失的五类字段——**量比、PE(TTM)、PB、流通市值、涨跌停价**。

```
GET https://qt.gtimg.cn/q=sz300684,sh600519
```

- 前缀规则：深市 `sz`、沪市 `sh`，多股逗号分隔，支持批量
- 无需任何 Key，实测约 156ms
- 返回为 GBK 编码的 `~` 分隔文本，按位解析（约第 39 位起为 PE(TTM)/PB/涨跌停价区，量比在第 49 位附近，实装时以实际返回核对）

---

## 四、iFind 同花顺 MCP（三级金融主力补充源）

2026-08-26 实测连通（连接方式为远程 HTTP 型 MCP，凭据由客户端注入 `IFIND_AUTH_TOKEN`，项目内无需手写配置）。在 4 个插件（iFind、earnings-interpretation、full-link-stock-analysis、industry-researcher）下重复挂载了同一后端，共 28 个实例，功能完全一致，任选其一即可，无需重复调用。

### 七个服务面（全部实测可用）

| 服务 | 代表性工具 | 覆盖数据 |
|---|---|---|
| 股票 stock | `stock_highfreq_quotes` / `get_stock_performance` / `get_stock_financials` / `search_stocks` | 实时快照(最新价/涨跌幅/量比/PE/PB/换手/市值)、1分钟高频K线、**主力资金净流入额及占比**、历史日K OHLCV、涨跌停状态/连板/创新高、龙虎榜、融资融券、全部财务指标、智能选股 |
| 指数 index | `index_data` / `index_highfreq_quotes` / `sector_data` | 指数行情/技术指标、实时快照含上涨/下跌家数（市场宽度）、板块涨跌幅 |
| 新闻 news | `search_news` / `search_notice` / `search_trending_news` | 新闻资讯与公告语义检索（带日期区间与 size，返回片段+URL） |
| 宏观 edb | `get_edb_data` / `search_edb` | GDP/CPI/PPI 等宏观与行业经济指标（月度/季度序列） |
| 基金 fund | `get_fund_profile` / `fund_highfreq_quotes` 等 8 工具 | 基金资料/行情/持仓/业绩 |
| 债券 bond | `bond_basic_info` 等 5 工具 | 债券基本信息/行情/财务/评级 |
| 港美股 global | `global_stock_profile` / `global_stock_quotes` / `global_stock_financials` / `global_stock_events` | 港美股基本资料/行情/财务/事件 |

### 关键取数要点

- **主力资金流**：`get_stock_performance(query="股票A、B在 2026-08-25至2026-08-26 的主力资金净流入额、主力净流入占比")` —— 可直接替代东财 f62/f184
- **财务**：`get_stock_financials(query="股票A在 2026-06-30 的ROE、净利润率、资产负债率")`
- **历史K线**：`get_stock_performance(query="股票A在 20260617-20260717 的开盘价、最高价、最低价、收盘价、成交量")`（注意用起止日期，避免"过去20日"这类相对描述）
- **实时快照**：`stock_highfreq_quotes(symbols="001378.SZ,002237", indicators="最新价,涨跌幅,成交量,成交额,换手率,量比,市盈率TTM,市净率,流通市值,总市值", data_mode="real_time")` —— 单次最多 10 主体、10 指标
- **大盘/宽度**：`index_highfreq_quotes(symbols="000300.SH", indicators="最新价,涨跌幅,上涨家数,下跌家数", data_mode="real_time")`
- **智能选股**：`search_stocks(query="市值大于100亿且ROE大于15%的股票")`；涨停梯队可用 `search_stocks(query="今天涨停且连续涨停天数大于2的股票")`
- **公告/新闻**：`search_notice(query=..., time_start=..., time_end=..., size=N)`，区间过窄可能返回空，放宽时间即可

### 使用边界与限流

- **高频行情仅支持交易日日内**，不含历史分钟线；历史日频走 `get_stock_performance`
- **新闻返回片段而非全文**，自带 URL 可溯源
- **免费额度并发上限约 2 请求/秒**，多请求时偶发 429"请求过于频繁"——串行调用、失败重试即恢复，非故障
- 不覆盖集合竞价量/涨幅（call_auction 保留 mcp_tdx）、不覆盖题材热榜/情绪温度（保留悟道）

---

## 五、东方财富免费 API（五级资金流备选）

只用于主力资金流两个字段，防止其他高频用途触发限频（iFind 与东方财富 mx 均可查资金流后，本源降为第三顺位备选）：

```powershell
curl.exe -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" `
  -e "https://quote.eastmoney.com/" `
  "https://push2.eastmoney.com/api/qt/stock/get?secid=0.300684&fields=f62,f184"
```

三条铁律：

- 必须带 `-A`（User-Agent）和 `-e`（Referer）两个头，PowerShell 直连会被服务端拒绝连接
- `secid` 规则：深市 `0.`、沪市 `1.`，例如 `0.300684`、`1.601865`
- `f62` 为主力净流入额，`f184` 为主力净占比；实测约 308ms

---

## 六、full-link 内置 TDX（六级冗余源）

来自 full-link-stock-analysis 插件自带的 TDX 连接，server 名含 `full-link-stock-analysis` 字样（如 `mcp_plugin_full-link-stock-analysis_tdx`）。

- 代表工具 `tdx_quotes` / `tdx_security_deep_info` / `tdx_kline` / `wenda_news_query`
- **字段优势**：单股信息最全，含 LB（量比）、SYL（市盈率）、ZTPrice（涨停价）、DTPrice（跌停价）、ZSZ（总市值）、盘口、CwInfo（财务摘要）
- **限制**：不支持多股批量，只能单股逐个查询，因此只做冗余校验位，不做批量主力

### 与独立实例的关系（重要更新）

独立的 `mcp_plugin_TDX_tdx` 与 full-link 内置的 TDX 是**两套独立连接**。2026-08-26 实测：**独立实例的 `tdx_lookup_stock` 与 `tdx_quotes` 均已恢复可用**（此前记录"全接口返空、需禁用"已过时）。两套均可作为冗余位，工具名与参数一致（`code` 纯数字 + `setcode` 市场代码）。

---

## 七、mcp_wudao 悟道 MCP（信息面特色源）

### 接入配置

远程 HTTP 型 MCP，共 63 个工具。接入模板（添加到 TRAE 的 MCP 管理或等效配置处）：

```json
{
  "mcpServers": {
    "wudao": {
      "type": "http",
      "url": "https://stock.quicktiny.cn/api/mcp",
      "headers": {
        "Authorization": "Bearer <你的悟道Key>"
      }
    }
  }
}
```

当前生产环境 Key 已在 TRAE 全局挂载中生效，本文档不落盘明文 Key；需要轮换时在服务商后台重置后同步更新全局挂载。

### 故障识别特征

- **manifest 能正常拉取、但 initialize 握手返回 500** —— 判定为服务端故障，等待自愈即可，不要反复重试也不要改动本地配置
- 服务端恢复后无需重启会话，直接重试原调用

### REST 降级通道

MCP 通道不可用时，可走同域 REST 接口 `/api/openclaw`（同样携带 Bearer 头），仅建议临时救急。

### 核心工具参数速查（经实测校准）

| 工具 | 关键参数 | 备注 |
|---|---|---|
| `news_hotlist` | `category`、`limit`、`platform` | **无日期参数**，只反映当前热榜 |
| `market_overview` | `date`（可选） | 市场宽度/大盘概况，交叉验证大盘风控 |
| `limit_up_ladder` | `date`、`detailLevel` | 涨停梯队/连板高度 |
| `dragon_tiger` | `date` 或 `startDate`+`endDate`；`stockCode` 可定向查个股 | 龙虎榜席位构成 |
| `theme_stocks` | `themeCode` 或 `themeName` | 题材成分与热度 |
| `official_announcements` | `limit`（常用 40） | 公告流，盘后公告评估次日影响 |

其他常用工具面：`kline`、`minute_data`、`stock_rank`、`trading_calendar`、`capital_flow`、`sector_analysis`、`smart_hotlist`、`research_reports`、`briefings`、`auction_data`、`short_term_emotion`、`theme_intraday_capital`、`intraday_main_flow`、`northbound_holdings`、`margin_trading`、`unlock_events`、`macro_calendar`、`index_market`、`etf_market`、`valuation_snapshot`、`financial_summary`、`watchlist` 系列等，共 63 个。

---

## 八、miniQMT / ashare-mcp（八级兜底）

- 项目内桥接：`.mcp.json` 中的 `ashare-mcp`，指向 `http://localhost:8000/mcp`，由 `mcp/ashare-mcp/mcp_server.py` 提供，底层走 xtquant
- 直连模式：Project_16 内 `qmt_trader.py`、`qmt_monitor.py` 等，交易动作一律走本地 QMT 客户端

**三条红线（违反会导致崩溃或阻塞）：**

- 禁止调用 `download_financial_data`、`download_all_sector_data` —— 会触发 C 层崩溃
- 非交易时段禁止查成交明细 —— 调用会永久阻塞
- 持仓判断一律用 `volume > 0`，不能用市值或成本是否为零判断

---

## 九、备用数据源速查（2026-08-27 更新）

以下数据源已实测连通，作为 iFind/mcp_tdx 之外的补充入口。其中东方财富 mx 已提升为**四级金融主力**参与行情优先级排序（见第一章），其余按备用用途使用：

| 数据源 | 覆盖 | 代表工具 | 实测验证 | 备注 |
|---|---|---|---|---|
| 东方财富 mx | A股/港美股/基金/债券/宏观 | `mx_ashare_finance_data`、`mx_stocks_screener`、`mx_index_block_finance_data`、`mx_finance_search_news` 等 11 工具 | 选股(股价>500筛12只)、指数板块(沪深300/中证500 5日)、新闻研报(中信半导体观点)、个股金融(茅台行情+财务) | 自然语言问句式、单次最多500标的、字段最全(量比/PE/PB/市值/换手)，**已纳入四级金融主力** |
| 万得 Wind | 港美股/基金/债券/宏观/公告 | CLI 7 大类 41 工具（`stock_data`/`fund_data`/`index_data`/`bond_data`/`financial_docs`/`economic_data`/`analytics_data`） | `get_stock_quote` 返回贵州茅台 1 分钟级全量行情 | **非 MCP server**，走 `wind-mcp-skill` 的 `scripts/cli.mjs`（PowerShell 下 JSON 需反斜杠转义）；单次单标的、耗积分，仅深研场景用 |
| 盈米 yingmi | 基金/组合/资产配置 | `SearchFunds`、`GetFundDiagnosis`、`GetFundsCorrelation`、`GetFundsBackTest`、`MonteCarloSimulate` 等 67 工具 | 基金搜索(易方达蓝筹)、完整诊断(净值/风险/行业分布/业绩/夏普/市场温度) | **纯基金/组合方向，不提供个股行情**；可补基金分析空白 |
| TuShare | 沪深股票 220+ 接口 | `trade_cal`、`daily`、`daily_basic`、`income`、`moneyflow`、`top_list` 等 | 交易日历查询成功 | 需 Token；覆盖行情/财务/资金流/龙虎榜/宏观 |
| AKShare | A股历史/宏观/期货/基金/ETF/债券 | CLI 24 个预置接口（`a-stock-history`/`china-cpi`/`futures-main-history`/`china-lpr`/`etf-history` 等） | 茅台日K 18日、CPI 223 个月度全序列、螺纹钢主力 4230 行(2009至今) 均成功 | 免费免 Key；**东财系实时快照/个股资料接口被反爬断连**，仅历史数据可靠；定位为 TuShare 之外的历史数据补充源 |
| tradingagents | 个股分析报告 | `analyze_stock`、`list_reports`、`get_report` | 报告列表查询成功 | 本地服务，生成/回顾分析报告 |

### 东方财富 mx 使用要点（四级金融主力）

- 代表用法：`mx_ashare_finance_data(query="贵州茅台最新价、涨跌幅、换手率")` 查个股；`mx_stocks_screener(query="股价大于500元的A股")` 筛选；`mx_index_block_finance_data(query="沪深300过去5个交易日涨跌幅")` 查指数/板块；`mx_finance_search_news(query="券商观点 行业")` 查研报新闻
- 与 iFind 高度重叠：两者都是全量金融数据入口，mx 字段更全（含量比/PE/PB/市值/换手），iFind 更快且覆盖竞价/高频。**资金流取数顺序：iFind → 东方财富 mx → 东财 curl**
- 免费额度未限频时秒级返回；单次问句建议只查一个主体，多主体拆多次

### 万得 Wind 使用要点（备用深研）

- 调用方式（非 MCP）：先 `cd` 到 `wind-mcp-skill` 目录，再 `node scripts/cli.mjs call <server_type> <tool_name> '<params_json>'`；PowerShell 下 JSON 用反斜杠转义引号（如 `'{\"windcode\":\"600519.SH\"}'`）
- 只支持单标的（`windcode` 单字符串），多标的需拆多次调用；`indexes` 指标名须逐字来自 `references/indicators.md`
- Key 判定以 CLI 返回为准（用户全局配置 > Skill 本地配置 > `WIND_API_KEY` 环境变量）
- 典型场景：港美股深研、债券估值、宏观 EDB 指标、公告检索、1 分钟级分钟行情

### 盈米使用要点（备用基金源）

- 纯基金/组合方向：搜索基金 `SearchFunds`、单只诊断 `GetFundDiagnosis`、组合相关性/回测 `GetFundsCorrelation`/`GetFundsBackTest`、资产配置 `GetAssetAllocationPlan`、基金筛选排雷 `filterBondFundByCreditRating` 等
- 可经 `yingmi-skill-cli mcp list` 查实时工具清单，`yingmi-skill-cli mcp schema <tool>` 查入参

### AKShare 使用要点（历史数据补充源）

- 接入：`python3 -m pip install --user akshare`（清华镜像），CLI 位于 `akshare-finance` 技能的 `scripts/akshare_cli.py`；`python3 scripts/akshare_cli.py presets` 看 24 个预置接口，`describe <接口>` 看参数
- 调用：`python3 scripts/akshare_cli.py call a-stock-history --params '{"symbol":"600519","period":"daily","start_date":"20250101","end_date":"20251231","adjust":""}'`；PowerShell 下 JSON 引号用反斜杠转义（同 Wind CLI 写法）；日期 YYYYMMDD、A股代码六位
- **可靠性边界**：东财源实时接口（全市场快照 `a-stock-spot`、个股资料 `a-stock-profile`）在本机被反爬断连（RemoteDisconnected，与东财 curl 直连被断同源）；历史日K（东财源）、宏观指标、期货主力历史（新浪源）稳定可用
- **Windows 兼容说明**：CLI 硬超时依赖 `signal.SIGALRM`，Windows 不支持——已给 `hard_timeout` 打补丁退化为无硬超时（由终端控制时长），升级插件后如报"Hard timeouts are unavailable"需重新打该补丁
- 定位：免费免 Key 的历史数据补充（宏观/期货/基金/债券/A股日K），**不参与实时行情优先级队列**；接口可能因上游改版失效，仅研究参考

---

## 十、定时任务集成现状

四个活跃定时任务的数据源编排（prompt 中固化了第一章优先级骨架）：

| 任务 | 时间 | 状态 |
|---|---|---|
| 集合竞价增强预判 | 交易日 09:25 | 已生效：mcp_tdx 打头，直读 call_auction 字段 + `tdx_get_auction_0925`，悟道四工具组合判情绪 |
| 开盘实时复核 | 交易日 09:45 | 已生效：mcp_tdx 批量采集八字段 + 悟道热榜/公告，大盘风控指数源改为 `tdx_get_index_quote("000300")` |
| 午休持仓报告 | 交易日 11:35 | 已生效：mcp_tdx 批量 + 腾讯补字段 + 资金流(iFind→东财mx→东财curl) + 悟道午间热榜/公告 |
| 盘后持仓复盘 | 交易日 15:40 | 已生效：mcp_tdx 批量 + 腾讯补字段 + 资金流(iFind→东财mx→东财curl) + 悟道盘后公告/龙虎榜/梯队复盘 |

### 大盘风控 T 值规则（各任务共用）

以沪深300当日涨跌幅为准：小于等于 -1.5% 时 T=0（空仓）；-1.5% 至 -1.0% 之间 T=1（半仓）；大于 -1.0% 时 T=2（满仓）。买入预算 = 策略资金池 × 95% ÷ T，资金池以 `strategy_capital.json` 为准滚动计算。

---

## 十一、ZCode 共享接入落点（2026-08-27 配置）

本说明书所列数据源已在 ZCode 客户端配置为**用户级共享 MCP**，所有 ZCode 会话自动连接（不写在项目 `.mcp.json`，遵守"项目 `.mcp.json` 只保留 metasearch 与 ashare-mcp 两项"的约定）。

配置位置：`C:\Users\Administrator\.zcode\cli\config.json` → `mcp.servers`

| 服务 | 类型 | 接入 | 备注 |
|---|---|---|---|
| `ashare-mcp` | http | `http://localhost:8000/mcp` | 原有，八级兜底 |
| `mcp_tdx` | stdio | `...\Python312\Scripts\tdx-mcp.exe` | 一级主力；与 TRAE 共用同一 exe，由 ZCode 以子进程拉起 |
| `wudao` | http | `https://stock.quicktiny.cn/api/mcp` + `Authorization: Bearer ${WUDAO_API_KEY}` | 悟道信息面；**Key 走环境变量 `${WUDAO_API_KEY}`，不落盘明文**，运行环境需 `setx WUDAO_API_KEY <Key>` |
| `cn_free_quote` | stdio | `Python312\python.exe D:\QuantLab\mcp\cn_free_quote\server.py` | 新建封装，含 `tencent_quote`（二级补充）/ `eastmoney_main_flow`（五级资金流） |
| `ifind` | http | `https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-global-stock-mcp` | iFind 同花顺，三级金融主力（股票/指数/资金/财务/选股）；按 TRAE 同款 headers 空，需鉴权时设 `IFIND_AUTH_TOKEN` |
| `ifind_news` | http | `https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-news-mcp` | iFind 新闻/公告语义检索 |
| `tdx_http` | http | `https://txmcp.tdx.com.cn:3001/traemcp` | TDX 远程 HTTP 端点（独立 TDX 实例，已恢复可用，冗余位）；TCP 端口实测可达 |
| `tradingagents` | stdio | `D:\Python311\python.exe D:\trae_workspace\tradingagents_mcp_server.py` | 本地分析服务；env 需 `ARK_API_KEY` / `TRADINGAGENTS_BACKEND_URL`（火山方舟） |

### 生效与排查
- 改 `cli/config.json` 后须**重启 ZCode / 新开会话**才会加载新 MCP 工具（当前会话不会热刷新）。
- `wudao` 未设 `WUDAO_API_KEY` 时连接返回 401；设置后新会话即生效。
- `cn_free_quote` 的东财工具直连被 `push2.eastmoney.com` 反爬断连；**本机 7897 代理已实测可用**，设 `EASTMONEY_PROXY=http://127.0.0.1:7897`（或系统 `https_proxy`）即返回 `f62/f184`。腾讯工具直连正常。
- `mcp_tdx` 为 stdio 子进程，ZCode 拉起即连；TDX 行情源允许多客户端，与 TRAE 实例并存无冲突。
- 本地核对腾讯字段：`python -c "import json,sys; sys.path.insert(0,r'D:\QuantLab\mcp\cn_free_quote'); import server; print(server.tencent_quote('sz300684'))"`
- 东方财富 mx / TuShare / 盈米：本机 TRAE 日志未找到可注册的 URL 端点，暂未配置；提供各服务 MCP URL 与 Token 后可补入。AKShare / 万得 Wind 为 CLI 接入，非 MCP，不注册。
- `ifind` / `tradingagents` 若服务端要求鉴权，需在运行环境设 `IFIND_AUTH_TOKEN` / `ARK_API_KEY`、`TRADINGAGENTS_BACKEND_URL` 等变量（不落盘明文）。

---

## 十二、策略数据覆盖矩阵（2026-08-27 核对）

策略数据需求分两层：**离线训练/回测层**（本地 E 盘存量库）与**实时生产层**（MCP 降级链 + 信息面）。逐项核对结论：**无任何"需要但无源"的数据缺口，当前配置可完全覆盖**。

### 离线训练/回测层

训练/回测数据全部来自本地 `E:\astock` 买断库（Tushare 口径、Parquet 格式），**不依赖任何 MCP 实时源**，离线侧零缺口：

| 需求 | 数据源 | 状态 |
|---|---|---|
| 历史日线 OHLCV | `E:\astock\daily\stock_daily.parquet`（2009 至今，5518+ 只，1.3GB） | ✅ 本地仓 |
| 财务 6 表（指标 167 字段/预告/快报/三大表） | `E:\astock\finance\*.parquet`（2005Q1~2026Q1） | ✅ 本地仓 |
| 股票基础信息 | `E:\astock\basic\stock_basic.parquet` | ✅ 本地仓 |
| 股票池 | `D:\QuantLab\data\universe_all_a.csv` | ✅ 本地文件 |
| 分钟线（1/5/15min） | `E:\astock\minute\`（5793 只逐股 parquet） | ✅ 本地仓 |
| 补充面（龙虎榜/资金流/北向/筹码/板块/竞价） | `E:\astock\lhb\` `moneyflow\` `northbound\` `chip\` `board\` `auction\` | ✅ 本地仓 |

### 实时生产层（4 个定时任务共用）

实时侧每个字段都有**主源 + 至少一层备源**，无单点依赖：

| 需求 | 主源 | 备源 | 状态 |
|---|---|---|---|
| 基础行情（现价/开高低/量额） | mcp_tdx | 腾讯 | ✅ |
| 量比/PE/PB/流通市值/涨跌停价 | 腾讯 | full-link | ✅ |
| 主力资金净流入 | iFind | 东财mx → 东财curl | ✅ 三重冗余 |
| 板块涨幅 | iFind sector_data | 东财mx | ✅ |
| 集合竞价量/涨幅 | mcp_tdx call_auction | tdx_get_auction_0925 | ✅ |
| 沪深300大盘风控 | mcp_tdx index | 腾讯/full-link/iFind | ✅ 四重通道 |
| 新闻催化 | 悟道 | 东财mx/iFind | ✅ |
| 龙虎榜/涨停梯队/题材热度 | 悟道 | iFind | ✅ |
| 公告 | 悟道 | iFind/东财mx | ✅ |
| 选股/财务补充 | iFind | 东财mx | ✅ |

### 双层闭环结构

当前 14 个数据源构成双层闭环：

- **离线层**：`E:\astock` 本地仓（训练/回测/因子构建）
- **实时层**：`mcp_tdx → 腾讯 → iFind → 东财mx → 东财curl → full-link → 独立TDX → miniQMT` 八级降级链 + 悟道信息面 + 备用深研源（万得/盈米/TuShare/AKShare/tradingagents）

### 已知边界（非缺口，需留意）

1. **离线仓时效**：财务数据截至 2026Q1、数据仓整理于 2026-06-26；历史数据滞后由实时 MCP 补最新，不影响训练
2. **30min/60min 分钟线为空**：`E:\astock\minute\` 下这两档暂无数据；当前策略只用日线 + 实时快照，不构成实际缺口
3. **TuShare 免费版限频**（trade_cal 1 次/小时）：只适合离线补历史，不适合盘中采集
4. **AKShare 东财实时接口受限**（反爬断连）：仅历史数据可靠，不参与实时队列
