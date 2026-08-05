# SPEC：风控分时段触发架构 P1 — 基础设施层

## Objective

为 v0.2 设计稿的时段路由做准备，先加基础设施字段和辅助函数。

- `SellDecision` 新增 `triggered_sublayer` 字段，清仓层 pure trailing 决策打标记
- 新增 `_get_allowed_sell_layers(now)` 返回时段路由表
- 新增 `_is_in_cooling_off()` + `_g_strategy_start_ts` 全局守卫
- **不接通主循环**（P2 再做），纯加函数／字段，零行为变更

## Scope

- `core/risk_manager.py` — `SellDecision` 字段 + `_check_clear_level` sublayer 标记
- `adapters/qmt_wrapper.py` — 3 个纯函数 / 变量新增（不调用）
- `tests/test_risk_timegate_p1.py` — 新增单测文件

## Implementation

### A. SellDecision.triggered_sublayer 字段（`core/risk_manager.py:72-93`）

```python
class SellDecision:
    """分层决策结果"""
    def __init__(self, action=Action.HOLD, code='', sell_pct=0.0, reason='',
                 triggered_layer='', triggered_signals=None, triggered_sublayer=None):
        self.action = action
        self.code = code
        self.sell_pct = sell_pct
        self.reason = reason
        self.triggered_layer = triggered_layer
        self.triggered_signals = triggered_signals if triggered_signals is not None else []
        self.triggered_sublayer = triggered_sublayer   # 新增：'trailing' 或 None

    @staticmethod
    def hold():
        return SellDecision()

    @staticmethod
    def reduce(code, pct, reason, layer, signals=None):
        return SellDecision(Action.REDUCE, code, pct, reason, layer, signals or [])

    @staticmethod
    def clear(code, reason, layer, signals=None, sublayer=None):
        return SellDecision(Action.CLEAR, code, 1.0, reason, layer, signals or [], triggered_sublayer=sublayer)
```

⚠ 注意：
- `triggered_sublayer` 放到 `__init__` 参数末尾，**在 `triggered_signals` 之后**，保持与已有调用兼容（所有已有调用都是 positional args，位置不变不影响）。
- `SellDecision.hold()` / `reduce()` / `clear()` 已有调用方全部不用改——default `None` 保证向后兼容。
- `clear()` 加 `sublayer=None` 参数，转发到 `triggered_sublayer=sublayer`。

### B. _check_clear_level 内 trailing 标记（`core/risk_manager.py` `_check_clear_level` 函数，约 line 562-570）

```python
# 改前：
if not (state.warning_reduced and not state.confirm_reduced):
    if self._check_trailing_profit(close, high, low, state):
        signals.append("移动止盈")

if signals:
    return SellDecision.clear(
        code, " | ".join(signals), "清仓层", signals
    )
return SellDecision.hold()
```

```python
# 改后：
trailing_sublayer = None
if not (state.warning_reduced and not state.confirm_reduced):
    if self._check_trailing_profit(close, high, low, state):
        signals.append("移动止盈")

if signals:
    # 纯 trailing 信号（只有一个 reason 且是移动止盈）→ triggered_sublayer='trailing'
    if len(signals) == 1 and signals[0] == "移动止盈":
        trailing_sublayer = 'trailing'
    return SellDecision.clear(
        code, " | ".join(signals), "清仓层", signals,
        sublayer=trailing_sublayer
    )
return SellDecision.hold()
```

**规则**：只有 `_check_clear_level` 内**唯一触发的信号是"移动止盈"**时，才打 `sublayer='trailing'`。若 `_check_clear_level` 同时触发 A2/A3/C1 等 + 移动止盈，`sublayer=None`（混合信号按普通清仓层处理，不走 P2 的 trailing 排除逻辑）。

### C. qmt_wrapper.py 新增辅助函数和全局变量

#### C1. 全局变量（约 line 138 附近，已有 `_g_candidate_queue = []` 之后）

```python
_g_strategy_start_ts = None   # 策略启动时间戳（cooling-off 用，P1 只声明不读取）
```

#### C2. init() 末尾赋值（约 line 2910, `_g_init_done = True` 之前）

```python
global _g_strategy_start_ts
_g_strategy_start_ts = time.time()
```

注意：
- 把 `_g_strategy_start_ts` 加到 init() 函数开头的 `global` 声明行（约 line 2875-2878 `global _g_init_done, ...`）。
- 赋值位置在 `_g_init_done = True` 之前（初始化流程最后一步）。

#### C3. 新增 `_get_allowed_sell_layers` 函数

放在 `_check_and_execute_sell` 之前（约 line 1725-1728），靠近其他顶层辅助函数：

```python
def _get_allowed_sell_layers(now):
    """根据当前时点决定本轮允许哪些 sell decision triggered_layer 通过。

    返回 dict:
        {'layers': set[str],           # 允许的 layer 白名单
         'exclude_sublayers': set[str]} # 排除的 sublayer 黑名单
    layers 为空集表示本时段不允许任何卖出。
    """
    if now < '0925':
        return {'layers': set(), 'exclude_sublayers': set()}
    if now < '0930':
        # 集合竞价锁定区：只走预埋硬止损路径（P3 实现），主循环不放行
        return {'layers': set(), 'exclude_sublayers': set()}
    if now < '0935':
        return {'layers': {'底线层'}, 'exclude_sublayers': set()}
    if now < '0940':
        return {'layers': {'底线层', '清仓层'}, 'exclude_sublayers': {'trailing'}}
    if now < '1440':
        return {'layers': {'底线层', '清仓层', '预警层', '确认层', 'warning_add'},
                'exclude_sublayers': set()}
    if now < '1458':
        return {'layers': {'底线层', '清仓层', '预警层', '确认层', 'warning_add'},
                'exclude_sublayers': set()}
    return {'layers': set(), 'exclude_sublayers': set()}
```

#### C4. 新增 `_is_in_cooling_off` 函数

放在 `_get_allowed_sell_layers` 旁边：

```python
def _is_in_cooling_off():
    """策略启动后 60 秒内屏蔽所有交易。
    P1 只声明不调用，P2 接通 handlebar。
    """
    if _g_strategy_start_ts is None:
        return False
    return (time.time() - _g_strategy_start_ts) < 60
```

## Files

- `D:\QMT_STRATEGIES\core\risk_manager.py` — §A、§B
- `D:\QMT_STRATEGIES\adapters\qmt_wrapper.py` — §C1、§C2、§C3、§C4
- `D:\QMT_STRATEGIES\tests\test_risk_timegate_p1.py` — 新增单测

## Verification

### 1. 单测通过：`tests/test_risk_timegate_p1.py`

覆盖以下场景：

| # | 测试 | 类型 | 预期 |
|---|------|------|------|
| 1 | `SellDecision.clear(...)` 不传 sublayer | 向后兼容 | `triggered_sublayer == None` |
| 2 | `SellDecision.clear(..., sublayer='trailing')` 传 sublayer | 新字段读写 | `triggered_sublayer == 'trailing'` |
| 3 | `SellDecision.hold()` 不传 sublayer | 向后兼容 | `triggered_sublayer == None` |
| 4 | `SellDecision.reduce(...)` 不传 sublayer | 向后兼容 | `triggered_sublayer == None` |
| 5 | `_get_allowed_sell_layers('0900')` | 集合竞价前 | `{'layers': set(), 'exclude_sublayers': set()}` |
| 6 | `_get_allowed_sell_layers('0930')` | 开盘冲击区 | `{'layers': {'底线层'}, 'exclude_sublayers': set()}` |
| 7 | `_get_allowed_sell_layers('0937')` | 早盘缓冲区 | `{'layers': {'底线层', '清仓层'}, 'exclude_sublayers': {'trailing'}}` |
| 8 | `_get_allowed_sell_layers('0945')` | 盘中全开 | 含底线层+清仓层+预警层+确认层+warning_add |
| 9 | `_get_allowed_sell_layers('1445')` | 尾盘兜底 | 同上 |
| 10 | `_get_allowed_sell_layers('1458')` | 收盘撤单区 | `{'layers': set(), 'exclude_sublayers': set()}` |
| 11 | `_is_in_cooling_off()` 刚启动 < 60s | cooling-off | `True` |
| 12 | mock `_g_strategy_start_ts` 为 120s 前 | cooling-off 过期 | `False` |

### 2. Git diff 范围校验

```bash
git diff --stat core/risk_manager.py adapters/qmt_wrapper.py tests/test_risk_timegate_p1.py
```

期望：不超过 risk_manager ~10 行改 + qmt_wrapper ~50 行加 + 新单测文件

### 3. 手动验证

```bash
cd D:/QMT_STRATEGIES
python -c "from core.risk_manager import SellDecision; d = SellDecision.clear('000001', 'trailing', '清仓层', ['移动止盈'], sublayer='trailing'); print(d.triggered_sublayer)"
# 输出: trailing
```

```bash
cd D:/QMT_STRATEGIES
python -c "from adapters import qmt_wrapper; r = qmt_wrapper._get_allowed_sell_layers('0937'); print(r['exclude_sublayers'])"
# 输出: {'trailing'}
```

## Notes

1. **零行为变更**：P1 所有新增代码都不被调用。`_g_strategy_start_ts` 只在 init 赋值（不做读取）。`_get_allowed_sell_layers` 和 `_is_in_cooling_off` 纯声明。
2. **`time.time()` 已在 qmt_wrapper.py 多处使用**（如 line 1756 `now_ts = time.time()`），不需要额外 import。
3. **SellDecision.__init__** 参数 `triggered_sublayer` 在 `triggered_signals` 之后，使用 default `None`，所有现有调用方不受影响（全部 positional args）。
4. 单测文件编码：**GBK**（`# coding=gbk`），与项目一致。