# QMT 委托买卖防坑指南（实战审计固化）

> 位置：`broker/qmt_order.py`（独立可复用模块）+ 本文档
> 背景：2026-08-04 对 `deploy/strategy_atr_lowvol_equalweight.py` 的"没有委托成交"问题做委托交易代码审计，对比 6+2 生产写法（`strategy_main.py`）后定位 3 个致命/高危坑。本文档把结论固化为规范，后续任何 QMT 策略都**必须复用 `broker/qmt_order.py`，禁止裸调 `C.passorder`**。

---

## 一、问题现象

ATR 低波等权策略部署到国金模拟端（`67014907`）后，**日志无任何成交记录、账户无持仓变化**，但选股/再平衡逻辑正常跑（日志有"筛选完成 X 只"）。审计根因不在选股，而在**委托下单代码本身**。

---

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

---

### 坑2【P1】miniQMT 本地端无 `get_trade_detail_data` → 反查必失败 → 持仓状态错乱

本地 miniQMT（`E:\国金QMT交易端模拟`）的 `C` 对象**没有 `get_trade_detail_data` 方法**。原策略的 `_lookup_order` 每次调用都抛 `AttributeError` 返回 `None` → 买入被误判为"未成交 pending"。

连锁反应：
1. 买入反查失败 → 登记为 pending；
2. 30 秒超时 `_check_pending_orders` 把刚建的 `ledger` 删掉（"回滚持仓"）；
3. 实际上账户已成交，但策略以为没持仓 → 下次再平衡**重复买入 / 持仓失联**。

**修复 / 规范**：`QmtOrderExecutor.lookup_order()` 检测 `getattr(C, 'get_trade_detail_data', None) is None` 时返回 `('OPTIMISTIC', None)`，调用方走"乐观确认"分支（写/删 ledger、不进 pending 死循环）。真实 QMT 环境有该方法则走精确反查。

---

### 坑3【P1】买入 pending 超时误删 ledger

`_check_pending_orders` 买入 pending 超时分支原逻辑 `del _g_my_codes[code]` 回滚 —— 在乐观模式下会**误删已成交持仓**。

**修复 / 规范**：`QmtOrderExecutor.check_pending_orders()` 中，买入侧超时**只清 pending、保留 ledger**（保守当成交处理）；仅卖出侧超时走 `on_rollback` 回滚。

---

## 三、标准用法（复制即用）

```python
from broker.qmt_order import QmtOrderExecutor

# 在 init() 里构造一次（C 为 ContextInfo，account_id 同 config）
executor = QmtOrderExecutor(C, account_id='67014907', safemode=False, pending_timeout=30.0)

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

---

## 四、对接 QMT 单文件构建（broker/qmt_builder.py）

`qmt_builder.py` 把策略逻辑打包为 GBK 单文件。本模块为纯 Python 3.6 语法（无 f-string / 无 typing / 无 walrus），**可直接被构建器合并进生产策略**，无需改造。

建议：所有 Project 的 `build/strategy_*.py` 统一 `from broker.qmt_order import QmtOrderExecutor`，删除原策略里手写的 `passorder` 调用与手写反查，回归到本模块。

---

## 五、自检清单（部署前必过）

- [ ] 全仓搜索 `C.passorder`，确认只剩 `broker/qmt_order.py` 内部一处（无裸调）
- [ ] `passorder` 调用第 6 位为价格、第 7 位为股数
- [ ] 反查逻辑含 `get_trade_detail_data is None -> OPTIMISTIC` 分支
- [ ] 买入 pending 超时分支**不含** `del ledger`
- [ ] 部署后首帧日志出现 `[QMT_ORDER][买入确认?] ... 返回值:0` 或 `[乐观确认]`

---

## 六、一句话纪律

> **QMT 下单只走 `broker/qmt_order.py`；第6是价、第7是量；本地端走乐观确认；买入超时绝不回滚 ledger。**
