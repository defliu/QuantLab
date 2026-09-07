# QMT 委托未成交撤单 / 重新委托 方案调研

> 调研日期：2026-09-01
> 调研范围：迅投官方文档（dict.thinktrader.net / docs.thinktrader.net）+ CSDN 实战文 + GitHub 开源项目 + 本项目源码
> 一句话结论：**本项目撤单写法全网查无实据，另有一处状态码误判 bug；业界已有成熟范式可直接抄。**

---

## 一、结论速览

| # | 问题 | 本项目现状 | 建议 | 优先级 |
|---|---|---|---|---|
| 1 | 撤单 API | `passorder(24,1101,...,order_id,0,...)` 民间写法，官方文档/开源项目均无支撑 | 改用官方 `cancel()` + `can_cancel_order()` 预检 | **P0** |
| 2 | 状态码判定 | `_lookup_recent_order_id` 排除 `(54,55,57)`，**55=部成是活跃态被误当终态** | 改为排除 `(53,54,57)` | **P0** |
| 3 | 撤单确认 | 发出撤单指令后**立刻**重下，无确认环节 | 引入 `cancel_and_wait` 轮询确认到终态 | P1 |
| 4 | 状态持久化 | pending / 重试次数全在内存，策略重启即丢 | 重试计数写进委托 remark；启动时撤未成交单 + 柜台对账 | P1 |
| 5 | 卖出兜底价 | 重试用「最新价」市价单（prType=5） | 改用**对手方最优价**（prType 14 / 44，xtquant `MARKET_PEER_PRICE_FIRST`） | P2 |

---

## 二、官方 API 权威结论

### 2.1 QMT 内置 Python（本项目所用环境）

| 函数 | 签名 | 说明 |
|---|---|---|
| **cancel** | `cancel(orderId, accountId, accountType, ContextInfo)` | **官方撤单唯一入口** |
| can_cancel_order | `can_cancel_order(orderId, accountId, accountType)` | 撤单前预检是否可撤 |
| get_last_order_id | `get_last_order_id(accountId, accountType, 'ORDER')` | 取最新一笔委托号 |
| on_cancel_error | 回调 `on_cancel_error(cancel_error)` | 撤单被拒时触发，含 `order_id` / `error_msg` |
| passorder | `passorder(opType, orderType, ...)` | **只负责下单，opType 枚举中无撤单值** |

`passorder` 的 opType 官方枚举（节选）：

- 股票/ETF/可转债：**23 买入、24 卖出**
- 融资融券：27~34
- 组合交易：25/26/35/36
- 期权：50~59　ETF 申赎：60/61　新股申购：92
- 可转债转股/回售：80~83

> 官方文档原话：「`can_cancel_order`、`cancel_task`、`cancel` 和 `do_order` 交易函数在回测模式中无实际意义」——即**实盘/模拟盘生效**。
> ⚠️ `cancel()` 返回成功仅代表**撤单指令已送达柜台**，不等于已撤掉，必须再通过 `order_callback` 或查委托状态确认。

### 2.2 miniQMT / xtquant

```python
xt_trader.cancel_order_stock(account, order_id)                      # 同步，0=成功 / -1=失败
xt_trader.cancel_order_stock_async(account, order_id)                # 异步，返回 seq / -1
xt_trader.cancel_order_stock_sysid_async(account, market, order_sysid)  # 按柜台合同号撤单
xt_trader.query_stock_orders(account, cancelable_only=True)          # 只查可撤委托
```

- 按 `order_sysid`（柜台合同号）+ `market` 撤单可规避 `CTP:无效的ExchangeID字段` 报错。
- 撤单失败通过 `on_cancel_order_stock_async_response` / `on_cancel_error` 回调返回。

### 2.3 订单状态码标准表（务必对齐）

| 码值 | 含义 | 是否活跃 |
|---|---|---|
| 48 | 未报 | ✅ |
| 49 | 待报 | ✅ |
| 50 | 已报 | ✅ |
| 51 | 已报待撤 | ✅ |
| 52 | 部成待撤 | ✅ |
| 53 | 部撤 | ❌ 终态 |
| 54 | 已撤 | ❌ 终态 |
| **55** | **部成** | **✅ 活跃** |
| 56 | 全成 | ❌ 终态 |
| 57 | 废单 | ❌ 终态 |
| 255 | 未知 | — |

**活跃态集合 = {48, 49, 50, 51, 52, 55}**

### 2.4 三个高频易错点

1. **委托号用哪个**：实战文多用 `m_strOrderSysID`（柜台合同号），项目里用的是 `m_nOrderID`。两者在不同券商/版本上表现可能不同，**必须先实测**。
2. **撤单不是同步的**：发指令 → 柜台处理 → 状态回落，有延迟。立刻重下 = 重复挂单。
3. **部成 ≠ 完结**：55 状态下的单子还活着，撤它、等它、或按剩余量重挂，三选一，不能当它不存在。

---

## 三、本项目现状盘点

### 3.1 代码地图

| 文件 | 角色 |
|---|---|
| `adapters/qmt_wrapper.py`（4230 行） | **逻辑唯一源**，撤单/重试全在这里 |
| `deploy/strategy_main.py` / `strategy_dev.py` | `scripts/build_strategy.py` 生成的 GBK 构建产物，同源，**改逻辑必须改 adapters 再重建** |
| `release/v1.0/strategy_main.py` | 旧版快照，勿参考 |
| `atr_lowvol/strategy_atr.py` | ATR 策略独立轻量实现，**无 cancel 调用** |
| `tests/test_sell_retry.py`、`test_pending_sell_and_close_mode.py`、`test_order_lookup.py` | FakeTrader mock，本机不用起 QMT 即可跑 |

### 3.2 卖出侧机制（撤单必达，非跌停不放弃）

核心三件套：`_check_pending_sells`（L2646）、`_retry_pending_sell`（L2881）、`_check_limitdown_sells`（L2772）。

下单后用 `_lookup_recent_order_id`（L460）轮询反查 order_id（15 次 × 0.2s = 3 秒），拿到才登记 `_g_pending_sells`（修的是 0630 bug：即时反查撞 QMT ~100ms 异步分配窗口，误判失败不登记，结果单子真成交了策略不知道）。

每帧核对 `m_nVolumeTraded`，四分支：

| 状态 | 动作 |
|---|---|
| 全部成交 | `_finish_pending_sell` → 结算、pop pending、写持仓文件、同步风控引擎 |
| 部分成交 | 撤残单 → 结算已成交部分 → 用剩余量重挂 |
| 未成交 | 撤单 → `retries+1` 重发 |
| 查不到订单 | **先查持仓差分再判成败**（L2687），持仓归零当全成、减少当部成，都没变才撤单重试 |

`_retry_pending_sell`：`retries>=1` 自动切市价单（L2907）；跌停移入 `_g_pending_limitdown_sells`（打开即卖、超 5 天强卖）；`MAX_SELL_RETRIES=5`（L177）放弃。

### 3.3 买入侧机制（3 次封顶）

`_check_pending_orders`（L3065）：未成交 → 撤单 → 按现价重算股数重下；`retries>=3` 撤单转补买候选 `_try_buy_replacement`（L2934）。涨停不追直接替补；委托失败进 `_g_retry_queue` 下一轮续排（L3002，重下前先撤旧单）。

反查失败时有额外诊断：`Trader.buy` 翻订单簿看状态，**9=废单**（不重试转补买）、**2=已报排队**（保留 order_id 继续等）。

### 3.4 时点清场（两条批量撤单）

- **14:58 收盘**（L4107-4121）：遍历 pending buys/sells 全部撤单，清空队列，跑一次卖出 + 打持仓报告
- **14:50 买入窗口结束**（L4155-4163）：只清买入侧 pending 和重试队列

### 3.5 前置防废单

`_normalize_sell_volume_for_board`（L2462，科创板 200 股门槛、清仓走零股）、`Trader.buy`（科创板 <200 股跳过）、`_is_limit_up` 不追涨停、`_is_limit_down`（L1691，主板 -9.5% / 创业板科创板 -19.5% / 北交 -29.5%）、卖出失败 60 秒冷却 `_g_sell_fail_cooldown`（L2582）。

### 3.6 ATR 策略是另一套路子

`atr_lowvol/strategy_atr.py` **完全没有 cancel_order 调用**，靠 pending 超时兜底：

- `_lookup_order`（L567）：volume ±10% 容差、排除状态 54/55/57
- 环境无 `get_trade_detail_data` 时返回 `'OPTIMISTIC'` 走乐观确认（避免 pending 超时误回滚持仓）
- `_check_pending_orders`（L624）：30 秒超时——卖单超时放弃、买单超时回滚持仓
- 60 秒卖出冷却 `_g_sell_cooldown`

---

## 四、发现的问题

### 4.1 P0｜`passorder` 撤单写法查无实据

```python
# adapters/qmt_wrapper.py:451
def cancel_order(self, order_id, stock_code):
    passorder(24, 1101, self.acct, stock_code, 5, order_id, 0,
              "%s|撤单" % self.strategy_name, 2, "", self.C)
```

即「opType=24 卖出 + price 位塞委托号 + volume=0」。官方 opType 表里 24 是**股票卖出**，没有撤单语义；官方文档、CSDN 实战文、GitHub 开源项目**全部使用 `cancel()`**。

**处理建议**：不要直接删（万一它在本环境确实生效），先在国金模拟端做一次对照实验——挂一笔不会成交的限价单，分别用两种写法撤，看委托状态是否落到 53/54；同时确认传 `m_nOrderID` 还是 `m_strOrderSysID`。

### 4.2 P0｜状态码 55（部成）被误当终态

```python
# _lookup_recent_order_id, L504
if status in (54, 55, 57):
    continue
```

55 = 部成，是**活跃态**。后果：限价单刚挂出去就部分成交时，反查会跳过它 → 反查失败 → 买入侧误判"废单/未到交易所"转补买、卖出侧误走"订单未找到"直接撤单重试。

**修复**：改为 `if status in (53, 54, 57): continue`。

### 4.3 P1｜撤单未确认即重下

现在流程是「发撤单指令 → 立刻重新下单」。撤单指令送达柜台到状态回落有延迟，这期间重下有重复挂单风险（尤其买入侧会双份成交）。

**修复**：引入 `cancel_and_wait`（见第六节）。

### 4.4 P1｜重试计数仅存内存

`_g_pending_sells` / `_g_pending_buys` / `_g_retry_queue` 全是模块级全局变量。策略重启 → pending 全丢 → 已挂在柜台的单子变成"孤儿单"，既不管也不撤。

**修复**：两步——重试计数编码进委托 remark 末段（格式 `策略名,代码,订单ID,挂单次数`）；启动时自动撤销当日本策略所有未成交单，再用柜台成交记录对账重建状态。

### 4.5 P2｜卖出兜底用「最新价」而非对手方最优

`_retry_pending_sell` 里 `use_market=True` → `price_type=5`（最新价）。更稳的做法是**对手方最优价**（QMT prType=14 对手价 / 44 对手方最优价格委托；xtquant `MARKET_PEER_PRICE_FIRST`），既保证成交又有保护限价。

### 4.6 附带发现（文档不一致）

- `adapters/qmt_wrapper.py` 文件头注释写「移除卖出 retries>=3 放弃」，但常量 `MAX_SELL_RETRIES = 5`（L177）——以常量为准，建议顺手改注释。
- `deploy/strategy_atr_lowvol_equalweight.py` **已不在本项目**，现位于 `D:\QuantLab\projects\Project_ATR_lowvol\build\`（含多个 .bak）。

---

## 五、GitHub 现成方案

### 5.1 方案对比

| 仓库 | License | 环境 | 核心机制 | 可借鉴度 |
|---|---|---|---|---|
| **lotey/lite-qmt-executor** | MIT | miniQMT | `cancel_and_wait` + max_retry 循环重挂 + WAL 灾备自愈 | ★★★★★ |
| **imbian/JoinQuantAutoOrderQMT** | — | miniQMT | 30s 轮询撤单重挂 + remark 编码重试次数 + 强制卖出档 | ★★★★ |
| **atorber/qmt-trading-skill** | — | HTTP bridge | 撤单前强制列明细 + 人工确认 | ★★★ |
| **IvanMao714/xtquant-grpc** | MIT | miniQMT | 完整状态码表 + gRPC 跨语言 | ★★★ |
| **AMOG2023/EasyXT** | — | miniQMT | xtquant 二次封装库 | ★★ |
| **chenjie222/qmt-http-service** | — | miniQMT | FastAPI，**默认预演模式，`confirm:true` 才真下单** | ★★ |
| **shaohan0228/qmt-docs** | — | — | 迅投官方文档离线镜像 | ★★ |

### 5.2 lotey/lite-qmt-executor（最对口）

**撤单封装**（`app/core/broker.py`）：

```python
def cancel_and_wait(self, order_id, timeout_seconds=3.0):
    """撤单并同步等待撤单完成或成交确认（部撤/已撤/已成），每 0.1 秒轮询"""
    self.cancel(order_id)
    for _ in range(int(timeout_seconds / 0.1)):
        time.sleep(0.1)
        o = self.get_order(order_id)
        if o and o.order_status in (53, 54, 56):
            return o
    return self.get_order(order_id)
```

**重挂循环**（`app/strategy/default/accumulate_buy.py`）：

1. 发单 → `sleep(1.0)` 查状态
2. `FILLED` → 记录返回；`PARTIAL_FILLED` → 再 `sleep(1.5)` 复查
3. 仍未全成 → `cancel_and_wait(order_id, 2.0)`
4. 撤单前已有成交 → 从剩余额度扣除 `traded_volume * traded_price`
5. 复查撤单状态：非 `CANCELED`/`FILLED` → **停止重试**（防重复下单）
6. 刷新行情 → 超过 `BUY_ONESHOT_CHASE_LIMIT` 追高限制则退回"佛系挂单"不再追
7. 用最新价重算价量 → 重新下单，最多 `max_retry=3`

**WAL 灾备**（本项目最缺的一环）：

- 所有买入信号与阶段状态以 `.jsonl` 追加写入 `data/buy_signals_YYYYMMDD.jsonl`
- 引擎启动读当日 WAL，超时（`BUY_RECOVERY_MAX_AGE_MINS`，默认 10min）的信号作废
- **接管前自动向柜台撤销当日所有未成交买单**
- 调用策略 `on_recover` 钩子，从柜台成交历史对账重建状态，无缝接管
- 启动时自动清理 7 天前旧 WAL

**状态归一化**：`is_active()`（48,49,50,51,52,55）/ `is_canceled()`（53,54）/ `is_partial_filled()`（55）/ `is_rejected()`（57）——这套封装比散落的魔数判断健壮得多。

### 5.3 imbian/JoinQuantAutoOrderQMT

- 30 秒定时轮询 `run_order_trader_func`
- **未成交判定**：`委托状态 ∈ [49, 50, 51, 52]`（注释特别说明 57 是废单，不重试）
- **撤单确认**：`cancel_order()` 后 `sleep(5)`，重查委托簿——若 remark 仍在未成交列表**或**返回值 ≠ 0，判定撤单未成功，等下一轮继续撤
- **重试次数编码进 remark**：格式 `策略名,股票代码,订单ID,挂单次数`，去重键取前 3 段 → **重启不丢计数**
- `max_retry=20` 封顶；卖出 `times >= forced_sell(8)` 时切换 `MARKET_PEER_PRICE_FIRST` 对手方最优价强制卖出
- 保护：集合竞价不撤单、非交易时间不处理、涨跌停不撤单
- 部分成交处理：拆成整手 + 零股两笔限价单分别卖出

### 5.4 atorber/qmt-trading-skill

把撤单做成可人工干预的流程：

```bash
python scripts/list_orders.py --cancelable-only          # 只读列出可撤委托
python scripts/cancel_orders.py --sysid 411494 --stock 688676.SH --execute --confirm
```

规程：默认只读展示 → 撤单前列出 stock_code/方向/价格/剩余量/order_sysid → 用户确认后才执行 → 撤单后再查 orders 核对状态。禁止未经确认批量全撤。

---

## 六、可直接落地的代码

替换 `adapters/qmt_wrapper.py:451` 的 `cancel_order`（Python 3.6 兼容，符合项目红线：无 f-string / 无 walrus / 无类型注解 / 无 `dict[str,...]`）：

```python
# ===== 常量（放在参数常量区，L177 附近）=====
CANCEL_WAIT_TIMEOUT = 3.0        # 撤单确认等待上限（秒）
CANCEL_POLL_INTERVAL = 0.1       # 轮询间隔（秒）
ORDER_STATUS_ACTIVE = (48, 49, 50, 51, 52, 55)
ORDER_STATUS_DONE = (53, 54, 56, 57)   # 部撤 / 已撤 / 全成 / 废单


def cancel_order(self, order_id, stock_code, wait=True):
    """撤单 + 轮询确认（官方 cancel 版）。

    wait=True 时阻塞直到订单落到终态或超时，返回是否"已撤/已完结"，
    调用方只有拿到 True 才允许重新下单，避免重复挂单。
    """
    if order_id is None:
        return False

    # 1) 预检：是否可撤
    try:
        if not can_cancel_order(order_id, self.acct, self.acct_type):
            print("  [撤单] %s 订单%s 当前不可撤" % (stock_code, order_id))
            return False
    except Exception as e:
        print("  [撤单] 可撤查询异常 %s: %s" % (order_id, e))
        # 查询异常不阻断，继续尝试撤单

    # 2) 发撤单指令（官方 cancel；返回成功只代表指令送达柜台）
    try:
        cancel(order_id, self.acct, self.acct_type, self.C)
    except Exception as e:
        print("  [撤单] %s 订单%s 失败: %s" % (stock_code, order_id, e))
        return False

    if not wait:
        return True

    # 3) 轮询确认落到终态
    loops = int(CANCEL_WAIT_TIMEOUT / CANCEL_POLL_INTERVAL)
    for _ in range(loops):
        time.sleep(CANCEL_POLL_INTERVAL)
        st = self._query_order_status(order_id)
        if st in ORDER_STATUS_DONE:
            return st in (53, 54, 56)
    print("  [撤单超时] %s 订单%s %.1fs 未确认，本轮不重下"
          % (stock_code, order_id, CANCEL_WAIT_TIMEOUT))
    return False


def _query_order_status(self, order_id):
    """查单笔委托状态码，查不到返回 None。"""
    try:
        orders = get_trade_detail_data(self.acct, self.acct_type, 'order') or []
        for o in orders:
            if getattr(o, 'm_nOrderID', None) == order_id:
                return getattr(o, 'm_nOrderStatus', 0)
    except Exception:
        pass
    return None
```

**同时修改 `_lookup_recent_order_id`（L504）**：

```python
# 改前
if status in (54, 55, 57):
    continue
# 改后：55=部成是活跃态，不能排除
if status in (53, 54, 57):
    continue
```

**调用方改造示例**（`_retry_pending_sell` 前）：

```python
# 改前
_g_trader.cancel_order(info['order_id'], code)
_retry_pending_sell(code, dict(info, retries=info.get('retries', 0) + 1), C)

# 改后：撤单确认后才重下，否则保留 pending 等下一帧
if _g_trader.cancel_order(info['order_id'], code):
    _retry_pending_sell(code, dict(info, retries=info.get('retries', 0) + 1), C)
else:
    info['retries'] = info.get('retries', 0) + 1
    _g_pending_sells[code] = info
```

---

## 七、行动清单

| 序 | 动作 | 涉及文件 | 验证方式 |
|---|---|---|---|
| 1 | 国金模拟端对照实验：挂限价单 → 分别用 `passorder` 写法和官方 `cancel()` 撤单 → 看状态是否落 53/54；确认传 `m_nOrderID` 还是 `m_strOrderSysID` | 临时脚本 | 委托簿状态 + QMT 日志 |
| 2 | 修 `_lookup_recent_order_id` 状态码 `(54,55,57)` → `(53,54,57)` | `adapters/qmt_wrapper.py` L504 | `pytest tests/test_order_lookup.py` |
| 3 | 新增 `cancel_and_wait` 式撤单确认，调用方改为确认后才重下 | `adapters/qmt_wrapper.py` L451 / L2726 / L2740 / L2756 / L3042 / L3114-3121 / L4111 / L4119 / L4158 | `pytest tests/test_sell_retry.py tests/test_pending_sell_and_close_mode.py` |
| 4 | 重试计数编码进委托 remark，重启可恢复 | `adapters/qmt_wrapper.py` | 手工杀死策略再拉起，看计数是否延续 |
| 5 | 启动自愈：撤销当日本策略未成交单 + 柜台成交对账重建 pending | `adapters/qmt_wrapper.py` | 盘中重启策略 |
| 6 | 卖出兜底改对手方最优价（prType 14/44） | `adapters/qmt_wrapper.py` `Trader.sell` | 模拟端跌速快的票 |
| 7 | 改完重建并校验产物 | `scripts/build_strategy.py` → `scripts/validate_qmt_file.py deploy/strategy_main.py` | 6/6 通过 |

> 改造原则（见 `AGENTS.md`）：只改 `adapters/` 源码再重建，**不要手改 `deploy/` 构建产物**；QMT 运行产物必须 GBK + `# coding=gbk` 头 + Python 3.6 兼容。

---

## 八、参考来源

**官方文档**
- 迅投 QMT 交易函数（passorder / cancel / can_cancel_order）：http://docs.thinktrader.net/pages/d0dd26/
- 迅投字典 · 交易下单函数：https://dict.thinktrader.net/innerApi/trading_function.html
- 迅投字典 · 系统函数（VBA 侧 cancel）：https://dict.thinktrader.net/VBA/system_function.html

**实战教程**
- QMT 撤单 API 详解（cancel + can_cancel_order 完整示例）：https://iris.findtruman.io/ai/tool/ai-quantitative-trading/e/qmt/cancel-order-api-explained
- QMT 超时自动撤单策略模板：https://iris.findtruman.io/ai/tool/ai-quantitative-trading/e/qmt/cancel-order-by-sys-id
- miniqmt 撤单报「CTP:无效的ExchangeID」的正确撤单方法：https://iris.findtruman.io/ai/tool/ai-quantitative-trading/e/qmt/qmt-miniqmt-cancel-order-invalid-exchangeid-error-solution
- QMT 量化超时撤单（CSDN，`cancel` + `passorder` 重委托完整范式）：https://blog.csdn.net/pengxuan/article/details/130130409
- QMT/miniQMT API 差异对比（含撤单函数对照）：https://miniqmt.com/pages/home.html
- xtquant 快速入门指南（order_stock / cancel_order_stock）：https://qmt.hxquant.com/?id=37

**GitHub**
- lotey/lite-qmt-executor：https://github.com/lotey/lite-qmt-executor
- imbian/JoinQuantAutoOrderQMT：https://github.com/imbian/JoinQuantAutoOrderQMT
- atorber/qmt-trading-skill：https://github.com/atorber/qmt-trading-skill
- IvanMao714/xtquant-grpc：https://github.com/IvanMao714/xtquant-grpc
- AMOG2023/EasyXT：https://github.com/AMOG2023/EasyXT
- chenjie222/qmt-http-service：https://github.com/chenjie222/qmt-http-service
- shaohan0228/qmt-docs：https://github.com/shaohan0228/qmt-docs

---

## 九、待核实

⚠️ 有券商营销文提到「2025 新规：每秒申报撤单不超过 300 笔、单日不超过 20000 笔」。该说法**来源为营销软文、非监管原文**，不要直接当作硬约束，建议向国金经理确认后再纳入限流设计。
