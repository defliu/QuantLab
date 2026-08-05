# SPEC：风控分时段触发架构 P2 — 接通主循环

## Objective

把 P1 的基础设施（`_get_allowed_sell_layers` / `_is_in_cooling_off` / `triggered_sublayer`）接通到 handlebar 主循环：

- `handlebar` 开头加 cooling-off 守卫
- Layer 1 的 `_check_and_execute_sell` 加 `allowed_layers` 参数；按 layer 白名单 + sublayer 黑名单过滤决策
- 被时段拦截的决策打观测 log（防刷屏）

**本阶段不动 P3 范围**：集合竞价预埋硬止损（9:25-9:30）暂不实现。

## Scope

- `adapters/qmt_wrapper.py` — `_check_and_execute_sell` 加参数、`_handlebar_impl` 调用点改造、cooling-off 守卫、防刷屏 log 全局变量
- `tests/test_risk_timegate_p2.py` — 新增单测

## Implementation

### A. `_check_and_execute_sell` 加 `allowed_layers` 参数（约 line 1769）

```python
# 改前
def _check_and_execute_sell(C, today):
    global _g_sell_engine, _g_pending_sells, _g_last_sell_fingerprint, _g_sell_skip_printed, _g_failed_printed

    if _g_sell_engine is None:
        return []
    ...
    raw_decisions = _g_sell_engine.evaluate(today, _g_my_codes, _g_all_data, positions_data, rt_prices)

    # 防刷屏：卖出决策变化时才打印诊断表
    if raw_decisions:
        fp = '|'.join('%s:%d:%.2f' % (c, int((d.sell_pct or 0) * 100), s) for c, d, s in raw_decisions)
        ...
```

```python
# 改后
def _check_and_execute_sell(C, today, allowed_layers=None):
    global _g_sell_engine, _g_pending_sells, _g_last_sell_fingerprint, _g_sell_skip_printed, _g_failed_printed
    global _g_timegate_skip_printed

    if _g_sell_engine is None:
        return []
    ...
    raw_decisions = _g_sell_engine.evaluate(today, _g_my_codes, _g_all_data, positions_data, rt_prices)

    # ===== P2: 时段路由过滤 =====
    if allowed_layers is not None:
        layers_set = allowed_layers.get('layers', set())
        exclude_subs = allowed_layers.get('exclude_sublayers', set())
        filtered = []
        for code, dec, shares in raw_decisions:
            # 跳过 HOLD 决策（不应到这里，但 evaluate 已过滤；保险）
            # 1) layer 白名单
            if dec.triggered_layer not in layers_set:
                # 防刷屏：相同(code, layer)只打一次
                skey = ('layer', code, dec.triggered_layer)
                if skey not in _g_timegate_skip_printed:
                    _g_timegate_skip_printed.add(skey)
                    print("  [时段拦截] %s reason=%s layer=%s 不在 %s 允许范围"
                          % (code, dec.reason, dec.triggered_layer, sorted(layers_set)))
                continue
            # 2) sublayer 黑名单（如 9:35-9:40 屏蔽 trailing）
            sub = getattr(dec, 'triggered_sublayer', None)
            if sub and sub in exclude_subs:
                skey = ('sublayer', code, sub)
                if skey not in _g_timegate_skip_printed:
                    _g_timegate_skip_printed.add(skey)
                    print("  [时段拦截|sublayer] %s reason=%s sublayer=%s 在 %s 排除列表"
                          % (code, dec.reason, sub, sorted(exclude_subs)))
                continue
            filtered.append((code, dec, shares))
        raw_decisions = filtered

    # 防刷屏：卖出决策变化时才打印诊断表
    if raw_decisions:
        ...
```

**注意**：
- `allowed_layers=None` 是默认值，**保持向后兼容**——任何调用方传 None 或不传都跳过时段过滤（旧行为）
- 调用者负责传 P1 的 `_get_allowed_sell_layers(now)` 返回值
- log 必须防刷屏，handlebar 每秒触发一次，全打会刷屏

### B. 新增防刷屏 set `_g_timegate_skip_printed`（约 line 138 附近）

紧挨 `_g_strategy_start_ts` 后面加：

```python
_g_timegate_skip_printed = set()  # P2: 防刷屏 — 已打印的"时段拦截"事件 (kind, code, key)
```

### C. 日切清空 set（约 line 2980-3000，日切区域）

在 `_g_last_sell_fingerprint = ''` 之后加：

```python
_g_timegate_skip_printed.clear()
```

同时把 `_g_timegate_skip_printed` 加到 `_handlebar_impl` 函数开头的 `global` 声明行（约 line 2967-2970）：

```python
global _g_op_executed, _g_startup_done, _g_all_data, _g_index_data, _g_last_sell_fingerprint
global _g_timegate_skip_printed
```

### D. handlebar 接通 cooling-off + 时段路由（约 line 3041-3045）

```python
# 改前
# Layer 1: 全天卖出监测（仅限交易时段，TEST_MODE不再绕过时间检查）
if _is_trading_time(dt) and now < '1458':
    _check_pending_sells(C, today)
    _check_limitdown_sells(C, today)
    if _g_my_codes:
        _check_and_execute_sell(C, today)
elif now >= '1458':
    ...
```

```python
# 改后
# ===== P2: 启动 cooling-off 守卫 =====
if _is_in_cooling_off():
    # 静默 return；首次进入打一行就够
    if not _g_cooling_printed:
        print("  [%s] 启动 cooling-off 中（60s 内屏蔽所有交易）..." % STRATEGY_NAME)
        _g_cooling_printed = True
    return

# Layer 1: 全天卖出监测（仅限交易时段，TEST_MODE不再绕过时间检查；P2 加时段路由）
if _is_trading_time(dt) and now < '1458':
    _check_pending_sells(C, today)
    _check_limitdown_sells(C, today)
    if _g_my_codes:
        allowed = _get_allowed_sell_layers(now)
        if allowed['layers']:  # 空集表示本时段无 layer 放行，直接跳过
            _check_and_execute_sell(C, today, allowed_layers=allowed)
        # 注：limitdown_sells 不受时段路由限制（已挂出的撤单/重发不阻断）
elif now >= '1458':
    ...
```

**接入点说明**：
- cooling-off 守卫放在 `_handlebar_impl` 开头日切逻辑**之后**，Layer 1 / Layer 2 / SAFEMODE 分支**之前**。这样：日切初始化逻辑不被 cooling-off 跳过（如 `_g_my_codes` 加载）；所有交易分支都被 cooling-off 屏蔽
- 注意：`SAFEMODE` 分支在 cooling-off 之前是否合理？SAFEMODE 是只读模式，不下单，cooling-off 没意义，建议放在 SAFEMODE 之后、Layer 1 之前
- **修正定位**：cooling-off 守卫放在 line 3040 附近（SAFEMODE 分支末尾 `return` 之后、Layer 1 `if _is_trading_time(dt)` 之前）

### E. 新增 `_g_cooling_printed` 全局变量（B 块旁）

```python
_g_cooling_printed = False  # P2: cooling-off 首次提示防刷屏
```

`_g_cooling_printed` 也加到 `_handlebar_impl` 的 `global` 声明，日切时 `_g_cooling_printed = False` 重置。

### F. 不动的部分

- `_check_pending_sells` / `_check_limitdown_sells`：不受 layer 路由限制（已发出去的撤单/重发是事务清理）
- Layer 2（约 line 3068, `if TEST_MODE or ('1440' <= now <= '1457')`）：尾盘换仓全流程，本 SPEC 不动
- `_check_sell`（尾盘强卖）：不动
- DEBUG_MODE / `_check_sell_debug_mode`：不动

## Files

- `D:\QMT_STRATEGIES\adapters\qmt_wrapper.py` — §A、§B、§C、§D、§E
- `D:\QMT_STRATEGIES\tests\test_risk_timegate_p2.py` — 新增单测

## Verification

### 1. 单测：`tests/test_risk_timegate_p2.py`

覆盖：

| # | 测试 | 类型 | 预期 |
|---|------|------|------|
| 1 | mock `_g_sell_engine` 返回 1 个底线层决策 + `allowed_layers={layers:{底线层}}` | 白名单过 | 返回 1 个决策 |
| 2 | mock 返回 1 个预警层决策 + `allowed_layers={layers:{底线层}}` | 白名单拦 | 返回 0；打 [时段拦截] log |
| 3 | mock 返回 1 个 trailing sublayer 决策 + `exclude_sublayers={trailing}` | 黑名单拦 | 返回 0；打 [时段拦截\|sublayer] log |
| 4 | mock 返回 1 个清仓层非 trailing + `exclude_sublayers={trailing}` | 黑名单不拦 | 返回 1 |
| 5 | 不传 `allowed_layers` | 向后兼容 | 不过滤，全返回 |
| 6 | `_get_allowed_sell_layers('0900')` 返回 layers=set() → 调用方应跳过 `_check_and_execute_sell` | 集合竞价 | 验证 handlebar 流程不发单（mock） |
| 7 | cooling-off 守卫：mock `_g_strategy_start_ts = now-30` → `_check_and_execute_sell` 不被调用 | cooling-off | mock 调用次数为 0 |
| 8 | cooling-off 过期：mock `_g_strategy_start_ts = now-120` + now='1000' → `_check_and_execute_sell` 被调用 | cooling-off 过期 | mock 调用次数为 1 |
| 9 | `_g_timegate_skip_printed` 防刷屏：连续 3 次同一拦截 → 只打 1 次 log | 防刷屏 | log 出现 1 次 |
| 10 | 日切清空 `_g_timegate_skip_printed`：mock date 变化 → set 被清空 | 日切 | set 为空 |

**实现技巧**：
- 用 `unittest.mock.patch` mock `_g_sell_engine.evaluate` 返回构造的 `(code, SellDecision, shares)` 列表
- mock `_g_my_codes = {'000001.SZ': {...}}`、`_g_trader`、`_g_all_data`
- 用 `io.StringIO` + `contextlib.redirect_stdout` 捕获 print

### 2. 手动验证

```bash
cd D:/QMT_STRATEGIES
python -m unittest tests.test_risk_timegate_p2 -v
# 期望：ALL PASS

# 简单交互测试
python -c "
from adapters import qmt_wrapper
import time
qmt_wrapper._g_strategy_start_ts = time.time() - 30
print('cooling-off active:', qmt_wrapper._is_in_cooling_off())
qmt_wrapper._g_strategy_start_ts = time.time() - 120
print('cooling-off expired:', qmt_wrapper._is_in_cooling_off())
"
# 期望：
# cooling-off active: True
# cooling-off expired: False
```

### 3. Git diff 范围

```bash
git diff --stat adapters/qmt_wrapper.py tests/test_risk_timegate_p2.py
```

期望：wrapper ~40-50 行净加，新单测文件 ~120 行

### 4. 构建验证（不可省）

```bash
cd D:/QMT_STRATEGIES
python scripts/build_strategy.py
python scripts/validate_qmt_file.py strategy_main.py
```

期望：6 项全 PASS

## Notes

1. **向后兼容**：`_check_and_execute_sell` 的 `allowed_layers` 默认 None，未传时跳过过滤（旧行为）。所有现存调用方不需要改（实际上 handlebar 是唯一调用方，但保留 None 是为了便于单测）。
2. **cooling-off 不豁免 limitdown_sells**：跌停队列是事务清理，启动时也不应运行；但 cooling-off 守卫在 Layer 1 之前 return 整个 handlebar，所以 limitdown 也会被屏蔽 60s。这是接受的代价。
3. **观测 log 防刷屏关键**：`_g_timegate_skip_printed` 用 `(kind, code, key)` 三元组去重，日切清空。如果同一只票同一天同一个 layer 反复被拦，只打一次。
4. **build_strategy 影响**：本次改动新加全局变量 `_g_cooling_printed` / `_g_timegate_skip_printed`，build 时不影响 `--dev` 注入逻辑（只改 TEST_MODE 行）。
5. **不动尾盘 Layer 2**：本 SPEC 范围内 Layer 2（14:40-14:57 `_execute_trade` 主流程）维持原样，预警层 / 确认层 / warning_add 在 09:35+ 已经能触发了，尾盘只是兜底重扫。