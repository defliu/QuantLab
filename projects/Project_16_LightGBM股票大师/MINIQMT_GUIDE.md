# MiniQMT（xtquant）操作指导书

> 适用项目：Project_16 LightGBM股票大师（`D:\trae_workspace\projects\Project_16_LightGBM股票大师`）
> 目的：把 miniQMT 的委托交易、持仓/资产/委托查询、行情获取的用法沉淀成可复用手册，新会话/新人照着即可操作，不必重新踩坑。
> 所有代码基于本项目实际跑通的经验，参数均来自 xtquant 官方 API 在本机（Windows + 国金 QMT 模拟盘）的实测。

---

## 1. MiniQMT 是什么

miniQMT 是券商提供的量化交易终端。它带一个 `xtquant` Python 包，可让你用代码完成：下单、撤单、查持仓、查资产、查委托/成交、订阅实时行情。本项目的交易层（`qmt_trader.py`、`qmt_monitor.py`、`rebalance_daily.py`）全部基于它。

两个核心对象：

| 模块 | 对象 | 负责 |
|---|---|---|
| `xttrader` | `XtQuantTrader` | 交易通道：连接、下单、持仓/资产/委托查询 |
| `xtdata` | `xtdata` | 行情通道：订阅并读取实时行情、下载历史数据 |

`XtQuantTrader` 通过本地端口连接**已登录的 QMT 客户端**，不直接联网；客户端必须开着并登录，代码才能连上。

---

## 2. 环境准备

### 2.1 前置条件

1. 安装并登录 QMT 客户端（本项目用国金模拟盘，路径 `E:\国金QMT交易端模拟`）。
2. 确认目录结构：
   - `userdata_mini`：xtquant 连接用的数据目录
   - `bin.x64\Lib\site-packages`：`xtquant` 包所在位置

### 2.2 关键路径配置（集中在 `qmt_config.py`）

```python
QMT_PATH = r"E:\国金QMT交易端模拟"
USERDATA = os.path.join(QMT_PATH, "userdata_mini")                    # 连接路径
XTPACK   = os.path.join(QMT_PATH, "bin.x64", "Lib", "site-packages")  # xtquant 包位置
ACCOUNT_ID = "70180771"                                               # 资金账号
```

### 2.3 引入 xtquant 的坑（numpy 版本冲突）

QMT 自带的 `xtquant` 依赖 numpy 1.19（Python 3.6 编译），与本机 Python 3.10 的 numpy 2.x 冲突。**必须把 `sys.path.append(XTPACK)` 放在所有 import 的最后**，让 Python 优先用本环境的 numpy，否则启动即崩：

```python
import qmt_config as C
import sys
sys.path.append(C.XTPACK)   # 务必放末尾
from xtquant import xttrader  # 之后再导入 xtquant
```

---

## 3. 连接交易通道

```python
from xtquant import xttrader, xttype

trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))  # session_id 用时间戳唯一
trader.start()
if trader.connect() != 0:
    raise RuntimeError("连接 miniQMT 失败，请确认客户端已启动并登录")
account = xttype.StockAccount(C.ACCOUNT_ID)
trader.subscribe(account)      # 订阅账户后才有持仓/资产数据
time.sleep(3)                  # 等异步数据就绪（持仓/资产查询前务必等待）
```

要点：
- `connect()` 返回 0 表示连接成功，非 0 失败（客户端未登录/未启动）。
- `subscribe(account)` 后**等 3~5 秒**再查询持仓/资产，否则返回空。
- 用完后 `trader.stop()` 释放连接。

---

## 4. 委托交易（下单/卖出）

### 4.1 order_stock 签名

```python
order_id = trader.order_stock(
    account,          # StockAccount
    code,             # 如 "600519.SH"
    order_type,       # xtconstant.STOCK_BUY 买入 / STOCK_SELL 卖出
    volume,           # 股数，必须是 100 的整数倍（A股 1 手 = 100 股）
    price_type,       # xtconstant.FIX_PRICE 限价 / LATEST_PRICE 最新价(市价)
    price,            # 限价时的价格；市价传 0
    strategy_name,    # 策略名，如 "traework_rebalance"
    order_remark,     # 备注，如 "planA_pk_out"
)
```

### 4.2 完整示例（买入 / 卖出）

```python
from xtquant import xtconstant

# 买入：限价=现价（可+1档提高成交概率）
oid = trader.order_stock(account, "600519.SH", xtconstant.STOCK_BUY, 100,
                         xtconstant.FIX_PRICE, 1300.00, "my_strategy", "buy_signal")
# 卖出：市价（最快成交）
oid = trader.order_stock(account, "600519.SH", xtconstant.STOCK_SELL, 100,
                         xtconstant.LATEST_PRICE, 0.0, "my_strategy", "sell_signal")
```

### 4.3 判断是否成功

- `order_id > 0`：委托已受理，order_id 即委托编号。
- `order_id <= 0`：委托失败（常见：非交易时段、数量不足 1 手、资金不足、T+1 锁定）。

### 4.4 价类型选择

| 类型 | 常量 | 用法 | 适用 |
|---|---|---|---|
| 限价 | `FIX_PRICE` | 传具体价格，如现价 +0.5% | 模拟盘/想控制成交价 |
| 市价 | `LATEST_PRICE` | 传 0，按最新价撮合 | 盯盘止损止盈，要快 |

### 4.5 交易时段与 T+1

- 非交易时段（9:30 前、11:30-13:00、15:00 后）委托会被柜台拒绝或挂起（`已报` 不成交），开盘前下单无意义。
- A 股 **T+1**：当日买入的股票当日不可卖，卖出会成**废单**。卖出的数量要用 `can_use_volume`（今日可卖）。

---

## 5. 持仓查询

```python
positions = trader.query_stock_positions(account)
for p in positions:
    code   = getattr(p, "stock_code", "")
    vol    = int(getattr(p, "volume", 0))            # 持仓总量
    avail  = int(getattr(p, "can_use_volume", 0))    # 今日可卖数量（T+1 后解锁部分）
    cost   = float(getattr(p, "open_price", 0))      # 成本价
```

### 5.1 两个必须懂的坑

1. **孤儿持仓**：股票卖光（volume=0）后，QMT 持仓列表**仍会显示它**，一般第二天才清掉。判断"是否持有"必须看 `volume > 0`，不能只看列表里有。
2. **非交易时段查询不稳**：早盘前/收盘后 `query_stock_positions` 可能返回空或 0（这是 xtquant 特性，不是代码错）。脚本要内置重试；拿不到时回退本地成交记录（见第 8 节）。

### 5.2 建议的持仓读取模板（本项目 `rebalance_daily.py` 同款）

```python
for p in positions:
    code = getattr(p, "stock_code", "")
    v = int(getattr(p, "can_use_volume", 0) or getattr(p, "volume", 0))
    if not code or v <= 0:
        continue   # 过滤孤儿持仓（volume=0）
    result[code] = {"cost": float(getattr(p, "open_price", 0) or 0), "avail": v}
```

---

## 6. 资产与委托/成交查询

### 6.1 资产

```python
asset = trader.query_stock_asset(account)
total_asset = float(asset.total_asset)   # 总资产
cash        = float(asset.cash)          # 可用资金
frozen      = float(asset.frozen_cash)   # 冻结资金（挂单占用）
```

### 6.2 委托查询（判断单子状态）

```python
orders = trader.query_stock_orders(account)
for o in orders:
    # o.order_id, o.stock_code, o.order_volume, o.traded_volume,
    # o.order_status(48=已报/49=部分成交/50=已成/52=废单), o.price,
    # o.order_type(23=买/24=卖), o.strategy_name(来源), o.order_remark
    ...
```

通过 `strategy_name` / `order_remark` 可以判断每笔委托是谁下的（本项目用 `traework_monitor`、`traework_rebalance`、`traework_clear` 区分盯盘/换仓/清仓）。

### 6.3 成交查询

```python
trades = trader.query_stock_trades(account)  # 已成交明细：traded_price、traded_volume、traded_time
```

---

## 7. 行情获取（xtdata）

### 7.1 实时行情

```python
from xtquant import xtdata
codes = ["600519.SH", "000001.SZ"]
xtdata.subscribe_quote(codes, period="tick", count=-1)  # 订阅
time.sleep(0.5)                                          # 等数据
ticks = xtdata.get_full_tick(codes)                      # {code: tick}
for code, t in ticks.items():
    last = float(t.get("lastPrice", 0))   # 最新价
    high = float(t.get("high", 0))        # 当日最高
    vol  = float(t.get("volume", 0))      # 成交量
```

### 7.2 历史/增量数据（本地缓存落后时先下载）

```python
xtdata.download_history_data(code, period="1d", start_time="20260801", end_time="20260820")
df = xtdata.get_market_data_ex([], [code], period="1d", start_time="20260801", end_time="20260820")
```

### 7.3 日期解析坑

xtdata 返回的日期索引是 `YYYYMMDD` 整数，直接 `pd.to_datetime` 会解析成 1970 年。必须转字符串再解析：

```python
dates = pd.to_datetime(df.index.astype(str), format="%Y%m%d")
```

---

## 8. 交易记录管理（qmt_trade_log.csv）

所有成交（脚本买入/卖出）追加写 `data/qmt_trade_log.csv`，列：

```
time, code, side, vol, price, score, order_id
```

用途：
- 记录策略买入成本（盯盘补成本用）。
- 计算策略已实现盈亏（FIFO 配对 BUY/SELL）。
- 判断"策略持仓"：BUY 过的代码才算（账户历史持仓不算）。

### 8.1 一个坑：SELL 价格写 0

市价卖单有时 `price` 回填为 0（成交价未取到），会污染盈亏计算。`strategy_capital.py` 已跳过 `price <= 0` 的 SELL。写记录时尽量回填真实成交价。

---

## 9. 常见坑与避坑速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `import xtquant` 后启动即崩/版本错 | QMT 自带 numpy 1.19 与 Python 3.10 的 numpy 2.x 冲突 | `sys.path.append(XTPACK)` 放所有 import 末尾 |
| 持仓列表有已卖掉的股票 | QMT 孤儿持仓，volume=0 次日才清 | 判断"是否持有"只看 `volume > 0` |
| 非交易时段持仓查询为空/为 0 | xtquant 特性 | 重试；失败回退本地成交记录 |
| 开盘前下单 order_id <= 0 | 非交易时段柜台拒绝 | 只在交易时段下单 |
| 当日买入的票卖出成废单 | T+1 | 卖出数量用 `can_use_volume` |
| 撤单非交易时段不生效 | 柜台不处理 | 交易时段内撤 |
| 日期解析成 1970 年 | YYYYMMDD 整数被当时间戳 | 转字符串 + `format="%Y%m%d"` |
| 连接成功但查不到数据 | 订阅后没等异步就绪 | `subscribe` 后 `time.sleep(3~5)` |
| 委托状态判断 | order_status 数值 | 48 已报 / 49 部分成交 / 50 已成 / 52 废单 |

---

## 10. 项目现成工具（直接用，别重写）

| 脚本 | 功能 | 常用命令 |
|---|---|---|
| `qmt_config.py` | 所有路径/账号/风控参数集中配置 | 改它即可 |
| `qmt_trader.py` | 选股结果 → 下单 | `python qmt_trader.py --plan data/selections/<日>_selection_full.csv --top-k 2 --total 100000 --equal --live`（默认 dry-run） |
| `qmt_monitor.py` | 盯盘止损/止盈/移动止盈 + 可选自动卖 | `python qmt_monitor.py --once --auto-sell` |
| `qmt_clear.py` | 清仓指定持仓之外的票 | 按提示传保留持仓 |
| `rebalance_daily.py` | 方案A每日换仓（卖被PK+买新晋） | `python rebalance_daily.py --date <日> --top 2 --live`（默认 dry-run） |
| `strategy_capital.py` | 计算策略资金池（初始+已实现+浮盈） | `python strategy_capital.py` |

安全约定：所有脚本默认 dry-run（只打印计划不下单），加 `--live` 才真实委托。新手先 dry-run 确认计划，再上 `--live`。

---

## 11. 新手 3 步上手

1. **跑通连接**：确认 QMT 客户端已启动并登录，运行 `qmt_mini_test.py` 或任一脚本，看到"连接成功 / 账户资产"即通。
2. **dry-run 看计划**：跑 `rebalance_daily.py`（不加 `--live`），核对"卖什么、买什么、数量、金额"是否合理。
3. **上 --live**：确认无误后加 `--live` 真实执行，然后看 `qmt_trade_log.csv` 和飞书推送确认成交。

---

## 附录：一次完整交易查询示例（可直接复用）

```python
# coding: utf-8
import sys, time
import qmt_config as C
sys.path.append(C.XTPACK)  # 放末尾
from xtquant import xttrader, xttype

trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
trader.start()
assert trader.connect() == 0, "连接失败，检查客户端"
acct = xttype.StockAccount(C.ACCOUNT_ID)
trader.subscribe(acct)
time.sleep(5)

asset = trader.query_stock_asset(acct)
print(f"总资产 {asset.total_asset:.2f} | 可用 {asset.cash:.2f}")
for p in trader.query_stock_positions(acct):
    if getattr(p, "volume", 0) > 0:
        print(f"持仓 {p.stock_code} 总量{p.volume} 可卖{p.can_use_volume} 成本{p.open_price:.2f}")
for o in trader.query_stock_orders(acct):
    print(f"委托 {o.order_id} {o.stock_code} {o.order_volume}股 状态{o.order_status} {o.strategy_name}")
trader.stop()
```
