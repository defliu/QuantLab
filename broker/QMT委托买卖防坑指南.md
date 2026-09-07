# QMT 委托买卖防坑指南（实战审计固化）

> 位置：`broker/qmt_order.py`（独立可复用模块）+ 本文档
> 背景：2026-08-04 对 `deploy/strategy_atr_lowvol_equalweight.py` 的"没有委托成交"问题做委托交易代码审计，对比 6+2 生产写法（`strategy_main.py`）后定位 3 个致命/高危坑。本文档把结论固化为规范，后续任何 QMT 策略都**必须复用** **`broker/qmt_order.py`，禁止裸调** **`C.passorder`**。

***

## 一、问题现象

ATR 低波等权策略部署到国金模拟端（`67014907`）后，**日志无任何成交记录、账户无持仓变化**，但选股/再平衡逻辑正常跑（日志有"筛选完成 X 只"）。审计根因不在选股，而在**委托下单代码本身**。

***

## 二、三个坑（按严重度）

### 坑1【P0 致命】passorder 第 6 / 第 7 参数颠倒（价格 ↔ 股数）

`passorder` 的正确签名（6+2 生产写法 `strategy_main.py` 已验证、本仓库 `scripts/test_passorder.py` 一致）：

```python
# passorder(操作类型, 下单方式, 账号, 代码, 选价类型, 价格, 数量, ContextInfo)
passorder(23, 1101, account_id, code, 11, price, volume, C)
#                                      ^第6位=价格   ^第7位=股数
```

ATR 等权版三处全写反了（买入 / 卖出 / 调仓卖出），把第 6 位传成**股数**、第 7 位传成**价格**：

```python
# ❌ 错误：price 和 volume 位置颠倒
passorder(23, 1101, account_id, code, 11, volume, price, C)
```

后果：QMT 收到"价格=股数、数量=价格"的非法委托 → **必废单** → 这是"没有委托成交"的直接根因。

**修复 / 规范**：一律改用 `broker/qmt_order.py` 的 `send_limit_order(side, code, price, volume)` / `send_market_order(...)`，模块内部已按正确签名封装，调用方只管传 `(code, price, volume)`，从根上杜绝写反。

***

### 坑2【P1】miniQMT 本地端无 `get_trade_detail_data` → 反查必失败 → 持仓状态错乱

本地 miniQMT（`E:\国金QMT交易端模拟`）的 `C` 对象**没有** **`get_trade_detail_data`** **方法**。原策略的 `_lookup_order` 每次调用都抛 `AttributeError` 返回 `None` → 买入被误判为"未成交 pending"。

连锁反应：

1. 买入反查失败 → 登记为 pending；
2. 30 秒超时 `_check_pending_orders` 把刚建的 `ledger` 删掉（"回滚持仓"）；
3. 实际上账户已成交，但策略以为没持仓 → 下次再平衡**重复买入 / 持仓失联**。

**修复 / 规范**：`QmtOrderExecutor.lookup_order()` 检测 `getattr(C, 'get_trade_detail_data', None) is None` 时返回 `('OPTIMISTIC', None)`，调用方走"乐观确认"分支（写/删 ledger、不进 pending 死循环）。真实 QMT 环境有该方法则走精确反查。

***

### 坑3【P1】买入 pending 超时误删 ledger

`_check_pending_orders` 买入 pending 超时分支原逻辑 `del _g_my_codes[code]` 回滚 —— 在乐观模式下会**误删已成交持仓**。

**修复 / 规范**：`QmtOrderExecutor.check_pending_orders()` 中，买入侧超时**只清 pending、保留 ledger**（保守当成交处理）；仅卖出侧超时走 `on_rollback` 回滚。

***

***

### 坑4【P1】买入 pending 超时即放弃、无重试机制

原策略买单进入 pending 后，**30 秒超时直接撤单并删除 pending**，不再重新下单。导致：

* 流动性差/涨停/价格偏离时，挂单不成交 → 30 秒后彻底放弃该票

* 盘中波动大、QMT 反查延迟时，误判"未成交" → 永不补单

**2026-08-25 实盘案例**：浙能电力 (600023.SH) 买单挂单不成，30 秒超时撤单后无重报，该票永久丢失。

**修复 / 规范（ATR EW 2026-08-25 已落地）**：

* 超时从 **30 秒 → 300 秒 (5 分钟)**

* **最多重试 3 次**：撤单 → 重新 `passorder`（取最新价） → 更新 `pending['time']` + `pending['retry']` → 继续跟踪

* 3 次重试（约 15 分钟）仍不成交 → 打印「pending放弃」并彻底放弃该票

* 间际止损 / 日终对账仍会兜底核对

```python
# 简化逻辑
retry = pending.get('retry', 0)
if now - pending['time'] > 300:
    if retry >= 3:
        print("pending放弃"); del _g_pending_buys[code]; continue
    oid, _ = _lookup_order(...); _cancel_order(...) if oid
    new_oid = passorder(23, ..., code, 5, -1, pending['shares'], 'ATR_EW重报', 2, '', C)
    pending['time'] = now; pending['retry'] = retry + 1; continue
```

### 坑5【P0 致命】passorder 代码格式必须 `600522.SH`（数字在前），禁止 `SH.600522`

`passorder` 的第 4 参（orderCode）**只接受** **`600522.SH`（数字在前）或裸码** **`600522`**，**不接受** **`SH.600522`**。

* **现象**（2026-08-31 大QMT 迁移实锤，T-20260831-002）：passorder 返回 `ret=0`（异步接口受理），但委托**从未进入通道**——QMT 委托界面无记录、`get_trade_detail_data(acct,'stock','order')` 查无此单、`users\<acct>\XtTradeData` 委托库 0 字节。策略端一直 PENDING-NO-ORDER 空转 5 分钟重试，全是废单。

* **根因**：策略内部把 `600522.SH` 翻转成 `SH.600522` 再传 passorder。QMT 主日志（`userdata\log\XtClient_<date>.log`）实锤：

  ```
  [TC::COrderFuncService::parserParam] func:passorder, opType:23, orderType:1101, accountID:70180771, orderCode:600522SH, prType:5, modelPrice:35.14
  [msg service] msg: [函数交易]　函数: passorder, 下单代码 [600522SH] 不合法!
  ```

  QMT 把 `SH.600522` 解析成 `600522SH`（数字尾挂交易所）→ 校验不合法 → 静默废单。

* **规范**：下单、撤单、反查**全部统一** **`600522.SH`** **格式**（数字在前，与 `get_stock_list_in_sector` / `m_strInstrumentID` 返回一致）；需要裸码时 `code.split('.')[0]` 取 6 位数字，**禁止用** **`split('.')[-1]`**（取到的是交易所后缀）。

* **判断委托是否真进通道**：QMT 主日志出现 `CTradeClient::order [order] ... acc: 2_..._70180771` 才算提交成功；仅有 `parserParam` 且紧接「下单代码不合法」就是废单。

## 三、标准用法（复制即用）

```python
from broker.qmt_order import QmtOrderExecutor

# 在 init() 里构造一次（C 为 ContextInfo，account_id 同 config）
executor = QmtOrderExecutor(C, account_id='70180771', safemode=False, pending_timeout=30.0)

# 买入（限价）：价格在第2参、股数在第3参，模块内部保证正确签名
r = executor.send_limit_order('BUY', code, price, volume)
if r.ok():
    executor.register_pending(code, volume, 'BUY')

# 卖出（限价）
r = executor.send_limit_order('SELL', code, price, volume)
if r.ok():
    executor.register_pending(code, volume, 'SELL')

# 每个 handlebar 帧结算 pending（乐观确认 / 安全回滚）
def _on_confirm(code, vol):
    _g_my_codes[code] = vol   # 写/修正 ledger
def _on_rollback(code, vol):
    _g_my_codes.pop(code, None)
executor.check_pending_orders(_on_confirm, _on_rollback)
```

SAFEMODE 演练：构造时传 `safemode=True`，只打印不真正下单，用于灰度验证流程。

***

## 四、对接 QMT 单文件构建（broker/qmt\_builder.py）

`qmt_builder.py` 把策略逻辑打包为 GBK 单文件。本模块为纯 Python 3.6 语法（无 f-string / 无 typing / 无 walrus），**可直接被构建器合并进生产策略**，无需改造。

建议：所有 Project 的 `build/strategy_*.py` 统一 `from broker.qmt_order import QmtOrderExecutor`，删除原策略里手写的 `passorder` 调用与手写反查，回归到本模块。

***

## 五、自检清单（部署前必过）

* [ ] 全仓搜索 `C.passorder`，确认只剩 `broker/qmt_order.py` 内部一处（无裸调）

* [ ] `passorder` 调用第 6 位为价格、第 7 位为股数

* [ ] **第 4 位代码为** **`600522.SH`** **格式（数字在前）；全仓无** **`SH.600522`** **翻转逻辑**

* [ ] 反查逻辑含 `get_trade_detail_data is None -> OPTIMISTIC` 分支

* [ ] 买入 pending 超时分支**不含** `del ledger`

* [ ] 部署后首帧日志出现 `[QMT_ORDER][买入确认?] ... 返回值:0` 或 `[乐观确认]`

* [ ] 实盘首单后查 QMT 主日志确认出现 `CTradeClient::order`（委托真进通道，非「下单代码不合法」废单）

***

## 六、一句话纪律

> **QMT 下单只走** **`broker/qmt_order.py`；第6是价、第7是量；代码必须** **`600522.SH`** **数字在前；本地端走乐观确认；买入超时绝不回滚 ledger。**

