# SPEC：风控分时段触发架构 P3 — 集合竞价 09:25-09:29:59 预埋硬止损

## Objective

在 09:25 集合竞价撮合后、09:30 开盘前，对已确认重大风险的持仓**预埋硬止损卖单**（限价单），避开 09:30:00 全市场抢跑。

- 仅覆盖**底线层**（硬止损：累计亏损 / 单日跌幅），不处理预警/确认/清仓层。
- 按 grade 分档（G0-G3）决定动作。
- 默认运行模式 `G3_ONLY`（最保守）：仅在已触发硬止损常量时才预埋；G2 主动档需手动切换 `G2_AND_G3` 开启。
- 防重入：单交易日只跑一次。

**本阶段范围**：不动 P1（基础设施）、不动 P2（接通主循环）的任何代码与行为。

---

## Scope

- `adapters/qmt_wrapper.py`
  - 新增 module-level 常量 `PREMARKET_HARD_STOP_MODE`
  - 新增全局变量 `_g_premarket_check_done`、`_g_premarket_orders`
  - 新增函数 `_check_pre_market_hard_stop(C, today, now)`
  - `_handlebar_impl` 在 cooling-off 之后、Layer 1 `if _is_trading_time(dt)` 之前接入 09:25-09:29:59 分支
  - 日切清空 `_g_premarket_check_done` / `_g_premarket_orders`
- `tests/test_risk_timegate_p3.py` — 新增单测

**不动**：
- `core/risk_manager.py`
- `_check_and_execute_sell` / `_get_allowed_sell_layers` / `_is_in_cooling_off`
- Layer 1 / Layer 2 / SAFEMODE / DEBUG_MODE / TEST_MODE 分支

---

## Grade 表（与 v0.2 §四.5 一致；阈值口径以代码常量为准，见 §Notes.1）

设：
- `prev_close` = 前一交易日收盘价
- `ref_price` = 集合竞价撮合参考价（盘前取 `lastPrice` 或 `close` 字段，见 §实现.D）
- `cost_price` = 持仓成本价（取自 `_g_sell_engine._states[code].cost_price`，与 §C 卖出引擎一致）
- `daily_drop = (ref_price - prev_close) / prev_close`
- `cum_pnl = (ref_price - cost_price) / cost_price`
- 硬止损阈值常量：`HARD_LOSS = BOTTOM_LINE_LOSS_PCT = -0.05`、`HARD_DAILY = BOTTOM_LINE_DAILY_DROP_PCT = -0.07`

| Grade | 触发条件 | 动作 |
|-------|---------|------|
| **G0 无风险** | `daily_drop > -0.03` | 不预埋，记 log |
| **G1 警戒** | `-0.05 < daily_drop <= -0.03` **且** `cum_pnl > HARD_LOSS` | 不预埋，记 log "G1 警戒" |
| **G2 主动预埋** | `daily_drop <= -0.05` **且** `cum_pnl <= HARD_LOSS + 0.02`（即累计接近硬止损 2 个百分点内） | **`G2_AND_G3` 模式下**预埋限价卖单 `price = ref_price * 0.99` |
| **G3 强制预埋** | `cum_pnl <= HARD_LOSS` **或** `daily_drop <= HARD_DAILY` | 预埋限价单 `price = prev_close * 0.91`（约跌停 -9%，避免市价单滑点失控） |

**模式开关 `PREMARKET_HARD_STOP_MODE`（module-level 常量）**：
- `'OFF'` — 关闭，所有 grade 仅记 log 不下单
- `'G3_ONLY'` —（默认）仅 G3 下单，G2 仅记 log
- `'G2_AND_G3'` — G2 + G3 都下单

---

## Implementation

### A. 新增常量与全局变量

紧挨 `_g_strategy_start_ts = None`（约 line 139）下方加：

```python
# ===== P3: 集合竞价预埋硬止损 =====
PREMARKET_HARD_STOP_MODE = 'G3_ONLY'  # 'OFF' / 'G3_ONLY' / 'G2_AND_G3'
_g_premarket_check_done = False       # 单日跑一次的防重入 flag（日切清空）
_g_premarket_orders = {}              # code -> {'order_id', 'grade', 'price', 'shares', 'ref_price'}
```

### B. 新增 `_check_pre_market_hard_stop(C, today, now)` 函数

放在 `_is_in_cooling_off`（约 line 1764）之后、`_check_and_execute_sell`（约 line 1771）之前：

```python
def _check_pre_market_hard_stop(C, today, now):
    """09:25-09:29:59 集合竞价锁定区扫描持仓，按 grade 决定是否预埋硬止损单。
    单日只跑一次，由 _g_premarket_check_done 守护。
    """
    global _g_premarket_check_done, _g_premarket_orders

    if _g_premarket_check_done:
        return
    if PREMARKET_HARD_STOP_MODE == 'OFF':
        _g_premarket_check_done = True
        print("  [%s] 集合竞价预埋: 模式 OFF, 跳过" % STRATEGY_NAME)
        return
    if _g_sell_engine is None or not _g_my_codes:
        _g_premarket_check_done = True
        return

    HARD_LOSS = -0.05
    HARD_DAILY = -0.07

    print("  [%s] 集合竞价预埋扫描 (mode=%s) ..." % (STRATEGY_NAME, PREMARKET_HARD_STOP_MODE))

    for code in list(_g_my_codes.keys()):
        try:
            ref_price, prev_close = _get_premarket_ref_price(C, code)
        except Exception as e:
            print("    [预埋扫描] %s 取参考价异常: %s" % (code, e))
            continue
        if not ref_price or not prev_close or ref_price <= 0 or prev_close <= 0:
            continue

        state = _g_sell_engine._states.get(code)
        cost_price = state.cost_price if state else 0.0
        if cost_price <= 0:
            cost_price = _g_my_codes.get(code, 0) or 0.0  # 兜底从持仓字典取
        if cost_price <= 0:
            continue

        daily_drop = (ref_price - prev_close) / prev_close
        cum_pnl = (ref_price - cost_price) / cost_price

        # Grade 判断（优先级 G3 > G2 > G1 > G0）
        if cum_pnl <= HARD_LOSS or daily_drop <= HARD_DAILY:
            grade = 'G3'
        elif daily_drop <= -0.05 and cum_pnl <= HARD_LOSS + 0.02:
            grade = 'G2'
        elif daily_drop <= -0.03:
            grade = 'G1'
        else:
            grade = 'G0'

        pos = _g_trader.get_position(code)
        shares = (pos.get('volume', 0) if pos else 0)
        print("    [预埋扫描] %s grade=%s ref=%.2f prev=%.2f drop=%.2f%% pnl=%.2f%% shares=%d"
              % (code, grade, ref_price, prev_close, daily_drop * 100, cum_pnl * 100, shares))

        if shares < 100:
            continue
        if grade in ('G0', 'G1'):
            continue
        if grade == 'G2' and PREMARKET_HARD_STOP_MODE != 'G2_AND_G3':
            continue

        # 计算预埋价
        if grade == 'G3':
            limit_price = round(prev_close * 0.91, 2)
        else:
            limit_price = round(ref_price * 0.99, 2)

        # 下单
        try:
            order_id = _g_trader.sell_limit_price(code, shares, limit_price,
                                                  remark='预埋%s' % grade)
        except AttributeError:
            order_id = _g_trader._passorder(_g_trader.SELL_CODE, code, shares,
                                            '预埋%s' % grade, price_type=0, price=limit_price)
        if order_id is not None:
            _g_premarket_orders[code] = {
                'order_id': order_id, 'grade': grade,
                'price': limit_price, 'shares': shares,
                'ref_price': ref_price,
            }
            print("    [预埋下单] %s grade=%s %d股@%.2f order=%s"
                  % (code, grade, shares, limit_price, order_id))
            _append_log('集合竞价预埋: %s grade=%s %d股@%.2f' % (code, grade, shares, limit_price))
        else:
            print("    [预埋失败] %s grade=%s" % (code, grade))

    _g_premarket_check_done = True
    print("  [%s] 集合竞价预埋扫描完成 (下单 %d 只)"
          % (STRATEGY_NAME, len(_g_premarket_orders)))
```

### C. 新增 `_get_premarket_ref_price(C, code)` 辅助函数

放在 `_check_pre_market_hard_stop` 上方：

```python
def _get_premarket_ref_price(C, code):
    """取集合竞价撮合参考价 + 前一交易日收盘价。
    返回 (ref_price, prev_close)；任一取不到返回 (None, None)。
    """
    if C is None:
        return None, None
    # 取最近 2 根日 K：[-2]=前一交易日收盘, [-1]=今日（集合竞价撮合后的 close 字段）
    data = C.get_market_data_ex(['close'], [code], period='1d', count=2)
    if not data or code not in data:
        return None, None
    df = data[code]
    if df is None or len(df) < 2:
        return None, None
    prev_close = float(df['close'].iloc[-2])
    ref_price = float(df['close'].iloc[-1])
    if ref_price <= 0 or prev_close <= 0:
        return None, None
    return ref_price, prev_close
```

**注意**：集合竞价撮合后 QMT 是否立即在日 K 的 close 字段反映撮合价、还是要等首笔成交，**实盘验证**确认（见 §Verification.5）。若不行，回退用 `C.get_market_data(['lastPrice'], [code], ...)` 取实时价。

### D. handlebar 接入点

在 `_handlebar_impl` cooling-off 之后（约 line 3076）、Layer 1 `if _is_trading_time(dt) and now < '1458':`（约 line 3078）之前，加：

```python
        # ===== P3: 09:25-09:29:59 集合竞价预埋硬止损 =====
        if '0925' <= now < '0930':
            _check_pre_market_hard_stop(C, today, now)
            return  # 集合竞价区不走 Layer 1 / Layer 2
```

**为什么 return**：09:25-09:30 之间不应进入 Layer 1（`_is_trading_time` 会返回 False 也拦得住，但 return 显式）。

### E. 日切清空全局变量

`_handlebar_impl` 函数开头的 `global` 声明行加 `_g_premarket_check_done` / `_g_premarket_orders`：

```python
global _g_premarket_check_done, _g_premarket_orders
```

日切区域（约 line 2980-3000，紧挨 `_g_timegate_skip_printed.clear()`）加：

```python
_g_premarket_check_done = False
_g_premarket_orders = {}
```

### F. trader 是否需要新增 `sell_limit_price` 方法？

§B 的 `_check_pre_market_hard_stop` 优先调 `_g_trader.sell_limit_price(code, shares, price, remark=)`，AttributeError 时回退到 `_passorder(SELL_CODE, ..., price_type=0, price=limit_price)`。

**P3 范围内**：不强制加 `sell_limit_price` 方法（用 `_passorder` 回退路径就能跑）。如未来要加，作为单独 commit。

---

## Verification

### 1. 单测：`tests/test_risk_timegate_p3.py`

**编码 GBK，`# coding=gbk`。**

| # | 测试 | 预期 |
|---|------|------|
| 1 | `PREMARKET_HARD_STOP_MODE='OFF'` → `_check_pre_market_hard_stop` 跳过任何下单 | trader.sell 调用次数 0 |
| 2 | `G3_ONLY` 模式，mock 单只持仓 `cum_pnl=-0.10`（触发 G3） | 下 1 笔限价单 price≈`prev_close*0.91` |
| 3 | `G3_ONLY` 模式，mock 单只持仓 `daily_drop=-0.06, cum_pnl=-0.04`（触发 G2） | 不下单（G2 仅 log） |
| 4 | `G2_AND_G3` 模式，同 #3 → G2 触发 | 下 1 笔限价单 price≈`ref_price*0.99` |
| 5 | `G3_ONLY` 模式，mock `daily_drop=-0.04, cum_pnl=-0.02`（G1） | 不下单 |
| 6 | `G3_ONLY` 模式，mock `daily_drop=-0.01, cum_pnl=0.05`（G0） | 不下单 |
| 7 | 防重入：连续调 2 次 → 第 2 次直接 return | trader 调用次数仍为 1 |
| 8 | `shares < 100` 跳过 | 不下单 |
| 9 | `cost_price <= 0` → 兜底从 `_g_my_codes` 取，再不行跳过 | 不下单 |
| 10 | mock `_get_premarket_ref_price` 返回 (None, None) → 跳过该 code | 不下单 |
| 11 | 日切清空 `_g_premarket_check_done` → 下一日可重新执行 | 第 2 次 trader 调用次数 1（新一日） |

**实现技巧**：
- `unittest.mock.patch.object(qmt_wrapper, 'PREMARKET_HARD_STOP_MODE', 'G3_ONLY')` 切换模式
- `MagicMock` mock `C.get_market_data_ex` 返回构造 DataFrame 模拟 close 序列
- `mock _g_trader.get_position(code)` 返回 `{'volume': 200, 'cost': 12.0}`
- `mock _g_sell_engine._states` 字典塞 `SellPositionState`
- `setUp` / `tearDown` 保存/恢复 `_g_my_codes` / `_g_sell_engine` / `_g_trader` / `_g_premarket_check_done` / `_g_premarket_orders` / `_g_strategy_start_ts`（避开 cooling-off 干扰）
- 用 `io.StringIO + contextlib.redirect_stdout` 捕获 print 验证 grade log

### 2. 手动验证

```bash
cd D:/QMT_STRATEGIES
python -m unittest tests.test_risk_timegate_p3 -v
python -m unittest tests.test_risk_timegate_p2 -v
python -m unittest tests.test_risk_timegate_p1 -v
# 期望: P3 11 + P2 10 + P1 14 全 PASS
```

### 3. Git diff 范围

```bash
git diff --stat adapters/qmt_wrapper.py tests/test_risk_timegate_p3.py
```

期望：wrapper ~80-100 行净加（含新函数 + 接入点 + 日切 + global 行），新单测 ~150 行。

### 4. 构建验证（不可省）

```bash
cd D:/QMT_STRATEGIES
python scripts/build_strategy.py
python scripts/validate_qmt_file.py strategy_main.py
```

期望：6 项全 PASS。

### 5. 实盘 / 模拟端验证（落地后第一日）

- 09:25 之后观察 `[集合竞价预埋扫描]` log 是否出现
- 验证 `_get_premarket_ref_price` 取到的 `ref_price` 与同花顺 / QMT 行情板上的"竞价撮合价"是否一致
  - 若不一致：可能 QMT 日 K close 在 09:25 还未反映撮合价；改用 `get_market_data(['lastPrice'], ...)`，新写一个 commit
- 观察是否有 G3 误触发（如冷门股 ref_price 取不到导致 daily_drop 算错）
- 09:30 开盘后观察预埋单成交情况

---

## Notes

1. **阈值口径**：v0.2 设计稿引用的 `-8%/-7%` 是早期范本，**实际代码** `BOTTOM_LINE_LOSS_PCT = -0.05` / `BOTTOM_LINE_DAILY_DROP_PCT = -0.07`。本 SPEC 一律以代码常量为准（HARD_LOSS = -0.05、HARD_DAILY = -0.07）。设计稿的 grade 表会随之微调（G2 阈值"接近硬止损 2 个百分点"在新口径下为 cum_pnl <= -0.03）。
2. **预埋单价格选择 G3 用 `prev_close * 0.91`**：跌停价是 `prev_close * 0.90`（ST 股 0.95），用 0.91 留一档余地避免直接挂跌停被涨跌停限制规则拦掉。若需用涨跌停限价请单独 commit。
3. **集合竞价取数据风险**：实盘验证 §Verification.5。回退路径用 `lastPrice`。
4. **防重入 + 日切**：`_g_premarket_check_done` 在日切清空，确保跨天可重新执行；同一日多次 handlebar 进入 09:25-09:30 区只跑一次。
5. **不豁免 cooling-off**：若策略 09:24:30 启动，09:25:30 仍在 cooling-off 内 → handlebar 在 cooling-off 守卫处直接 return，P3 那 30 秒之内不会扫描。**这是接受的代价**——避免启动时账户初始化未完成就盲发预埋单。若要豁免，需单独 SPEC 讨论。
6. **G2 默认仅 log**：模式默认 `G3_ONLY`。诚哥实盘观察 1-2 周后，确认 G3 预埋行为符合预期，再切 `G2_AND_G3`。模式切换是改源码常量 + 重新 build，不走配置文件（避免运行时人为切换造成混乱）。
7. **build_strategy 影响**：新增 module-level 常量 `PREMARKET_HARD_STOP_MODE` 不影响 `--dev` 注入逻辑（只改 TEST_MODE 行）。
8. **不动 Layer 1 / Layer 2 / SAFEMODE**：本 SPEC 严格范围限定 cooling-off 与 Layer 1 之间的新分支。
