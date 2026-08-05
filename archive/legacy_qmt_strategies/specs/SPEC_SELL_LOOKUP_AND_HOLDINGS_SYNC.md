# SPEC: 卖出反查兜底 + 持仓强制同步

任务编号：T-20260625-006
日期：2026-06-25
负责：CC（规划）+ MIMO（执行）

## 背景

2026-06-25 实盘模拟端 603618 杭电股份暴露两个组合 bug：

1. 10:09 策略挂 C1 高位长上影减仓卖单 + 换仓卖单（限价 56.28），QMT 已回 order stat=2（已报），但 `_check_pending_sells` 用 `get_trade_detail_data('order')` 反查不到订单，误判"委托失败、按失败处理"，`_g_my_codes` 没清、卖出引擎 `current_shares` 没归零。
2. 14:30 该卖单实际成交（56.37），账户 603618 归零。但策略侧仍认为持有 500 股。
3. 次日开盘策略从 `INTRADAY_HOLD_FILE` 读回 `_g_my_codes`，603618 残留，占第 3 个名额，`可买数量=3-3=0`，命中"仓位已满（3/3），无可买目标"，不买新票。

证据日志（已存档）：
- `F:\backtest_workspace\QMT日志\userdata\log\XtClient_FormulaOutput_20260625.log`
- `F:\backtest_workspace\QMT日志\userdata\log\XtClient_20260625.log`

## 目标

修三处，让"卖出反查失败 + 实盘已成交"不再导致持仓不同步、不再卡住后续买入。不改选股池、不改评分、不改 C1/C2/B1/B2 风控信号本身、不改买入窗口 10:00-10:10。

## 决策（CC 已定，MIMO 不许自改）

1. **换仓卖单未成交**：保留限价单等成交，窗口结束不撤换仓卖单。换仓补买等成交确认后由现有 `_check_pending_orders` 路径处理；窗口内跑不完不强制。
2. **卖出反查失败兜底**：反查不到订单时，先查 `_g_trader.get_position(code)` 的实际 volume/can_use，若已减少按已成交处理；不要直接判失败。
3. **持仓强制同步时机**：开盘首帧 + 买入前双保险。
   - 开盘首帧（`_handlebar_impl` 里 `today != _g_last_date` 当日首次）用实际账户持仓强制同步 `_g_my_codes`，已清的 pop，并在卖出引擎里标 cleared。
   - 买入前（`_execute_trade` 开头）再校验一次。
4. **可买数量**：用同步后实际有持仓（volume>0）的票数算，不靠会抖动的 `get_account_holdings` 集合。

## 必做（MIMO 执行）

### TASK-0 预检

```bash
cd D:/QMT_STRATEGIES
git status --short adapters/qmt_wrapper.py tests/ strategy_main.py config/global_config.yaml
git log -1 --oneline
```

`adapters/qmt_wrapper.py` 若有非本任务 dirty → 停。

### TASK-1 修改 `adapters/qmt_wrapper.py`

#### 1.1 新增持仓强制同步函数

在 `get_account_holdings` 附近新增：

```python
def _sync_holdings_from_account(C, today):
    """用实际账户持仓强制同步 _g_my_codes 与卖出引擎 current_shares。

    - 实际 volume==0 的 _g_my_codes 票：pop 掉，卖出引擎标 cleared
    - 实际有 volume 但 _g_my_codes 没有的：不自动加入（避免误纳手动仓），只打印诊断
    返回同步后实际有持仓的 code set。
    """
    global _g_my_codes
    if _g_trader is None:
        return set()
    removed = []
    for code in list(_g_my_codes.keys()):
        pos = _g_trader.get_position(code)
        vol = pos.get('volume', 0) if pos else 0
        if vol <= 0:
            _g_my_codes.pop(code, None)
            removed.append(code)
            if _g_sell_engine is not None:
                state = _g_sell_engine._states.get(code)
                if state is not None and not state.cleared:
                    state.cleared = True
                    state.cleared_date = today
                    state.current_shares = 0
    if removed:
        print("  [持仓同步] 移除已清仓 %d 只: %s" % (len(removed), sorted(removed)))
        write_holdings_file(INTRADAY_HOLD_FILE, _g_my_codes)
        if _g_sell_engine is not None:
            _g_sell_engine.save_state()
    held = set(c for c in _g_my_codes.keys()
               if (_g_trader.get_position(c) or {}).get('volume', 0) > 0)
    return held
```

注意：
- 用 `_g_trader.get_position`（按 `get_trade_detail_data('position')` 取，volume 字段），不用 `get_account_holdings` 的集合差。
- Python 3.6 兼容，不用 f-string/新语法。
- 涉及的 `SellPositionState` 字段 `cleared/cleared_date/current_shares` 已存在（见 `_confirm_engine_clear` 用法），照搬。

#### 1.2 开盘首帧接入强制同步

在 `_handlebar_impl` 的 `if today != _g_last_date:` 块里，`_g_my_codes = read_holdings_file(INTRADAY_HOLD_FILE)` 之后、cooling-off 之前，加：

```python
        # 开盘首帧强制同步实际账户持仓（修 603618 类残留）
        _sync_holdings_from_account(C, today)
```

注意：这步在 cooling-off 内也会被 cooling-off return 之前执行到吗？要放在 cooling-off 守卫之前，确保开盘就同步一次。若 `_g_trader` 尚未 init（SAFEMODE/极早），函数内已有 None 守卫返回空。

#### 1.3 买入前再校验一次

在 `_execute_trade` 开头 `_refresh_trade_data(C)` 之后、"持仓同步"段之前，加：

```python
    # 买入前再校验实际持仓，防止盘中已清仓的票残留占名额
    _sync_holdings_from_account(C, today)
```

#### 1.4 卖出反查失败兜底（`_check_pending_sells`）

当前 `found_order is None` 分支（约 2113 行）逻辑：

```python
pos = _g_trader.get_position(code)
if pos is None or pos.get('volume', 0) == 0:
    _finish_pending_sell(...)   # 全部成交
```

改为：先比对实际 volume 与委托前 volume，判断是否部分/全部成交：

```python
pos = _g_trader.get_position(code)
actual_vol = pos.get('volume', 0) if pos else 0
ordered_vol = info.get('volume', 0)
prev_vol = ordered_vol + info.get('already_traded', 0)  # 委托前持仓估算
if actual_vol <= 0:
    # 全部成交
    _finish_pending_sell(C, code, info, ordered_vol)
    print("  [卖出确认] %s 全部成交 (反查失败但持仓归零)" % code)
    _confirm_engine_clear(code, today, info)
elif actual_vol < prev_vol:
    # 部分成交：按已减部分确认，剩余继续等
    traded = prev_vol - actual_vol
    if traded > 0:
        sell_price = info.get('sell_price', 0)
        cost_price = info.get('cost', 0)
        if cost_price > 0 and sell_price > 0:
            realized = (sell_price - cost_price) * traded
            _g_cumulative_pnl += realized
            write_nav_file(INTRADAY_NAV_FILE, _g_cumulative_pnl)
        _append_trade_record(C, '卖出', code, sell_price, traded,
                             profit_pct=info.get('pct', 0),
                             profit_amount=realized if cost_price > 0 and sell_price > 0 else 0)
        print("  [卖出确认] %s 反查失败但部分成交 %d/%d 股 (持仓确认)" % (code, traded, ordered_vol))
        info['volume'] = ordered_vol - traded
        info['already_traded'] = traded
        _g_pending_sells[code] = info  # 保留剩余继续等
        # 不撤单、不重试，等下一帧再查
        continue
    else:
        # 走原撤单重试
        _g_trader.cancel_order(info['order_id'], code)
        ...原重试逻辑...
else:
    # actual_vol >= prev_vol，确实没成交，走原撤单重试
    _g_trader.cancel_order(info['order_id'], code)
    ...原重试逻辑...
```

关键约束：
- 反查失败但持仓已减少 → 判成交，不撤单、不重试，避免把活着的限价单撤掉。
- 反查失败且持仓没减少 → 走原撤单重试。
- `info['already_traded']` 是新字段，用于累计；首次为 0。
- 不要碰 `_check_limitdown_sells`、`_finish_pending_sell`、`_retry_pending_sell` 的现有签名。

#### 1.5 可买数量用同步后实际持仓算

`_execute_trade` 里（约 2659 行）：

```python
可买数量 = max(0, MAX_HOLD - len(账户持仓))
```

改为用 `_sync_holdings_from_account` 返回的实际持仓 set：

```python
actual_held = _sync_holdings_from_account(C, today)  # 1.3 已调一次，这里再取最新
可买数量 = max(0, MAX_HOLD - len(actual_held))
```

同时 `already_held` 的计算（约 2644 行）也用 `actual_held` 替换 `账户持仓`：

```python
already_held = actual_held | set(_g_my_codes.keys()) | 对方持仓集 | set(_g_pending_buys.keys())
```

注意 `_g_my_codes` 在同步后应与 `actual_held` 基本一致，两者取并集兜底。

#### 1.6 换仓卖单不撤

换仓卖出路径（约 2700 行 `_should_swap` 段）的卖单挂出后进 `_g_pending_sells`，由 1.4 兜底保护：反查失败不撤、等成交。不要在这里加窗口结束撤单逻辑。窗口结束撤单只针对买入（已有 `BUY_WINDOW_END` 段处理 `_g_pending_buys`），不动卖出。

### TASK-2 新增/扩展测试 `tests/test_holdings_sync.py`

至少覆盖：

1. `_sync_holdings_from_account`：mock `_g_trader.get_position` 返回某 code volume=0，断言该 code 被 pop、卖出引擎 state.cleared=True、写文件被调用。
2. `_sync_holdings_from_account`：volume>0 的保留。
3. `_check_pending_sells` 反查失败 + 持仓归零 → 走全部成交分支，不撤单、不重试。
4. `_check_pending_sells` 反查失败 + 持仓减少 → 走部分成交分支，保留剩余。
5. `_check_pending_sells` 反查失败 + 持仓未减 → 走原撤单重试。

mock 模式参考 `tests/test_risk_timegate_p2.py`、`tests/test_pending_sell_and_close_mode.py` 里对 `_g_trader`/`_g_sell_engine`/`_g_my_codes` 的保存恢复写法。

### TASK-3 测试

```bash
cd D:/QMT_STRATEGIES
py -3.10 -m pytest tests/test_holdings_sync.py tests/test_buy_window.py tests/test_risk_timegate_p1.py tests/test_risk_timegate_p2.py tests/test_fix_sync_my_codes.py tests/test_sell_retry.py tests/test_safemode.py -q
```

全 PASS 才继续。已知 `tests/test_pending_sell_and_close_mode.py` 里 close-mode 滑点 3 个失败是历史遗留无关项，不在本任务范围，不要去修它，也不要因为它 fail 停下（但要在回执里如实记录）。

### TASK-4 构建 + QMT 校验

```bash
python scripts/build_strategy.py
python scripts/validate_qmt_file.py strategy_main.py
```

validate 6/6 ALL PASS。

### TASK-5 静态确认

```bash
git diff --stat -- adapters/qmt_wrapper.py tests/test_holdings_sync.py strategy_main.py config/global_config.yaml
```

确认：
- `config/global_config.yaml` 无 diff
- `POOL_PATH` 未改
- `BUY_WINDOW_*` 未改
- `strategy_main.py` 含 `_sync_holdings_from_account`、反查兜底分支
- `TEST_MODE = False`

### TASK-6 commit

只 stage：

```bash
git add adapters/qmt_wrapper.py tests/test_holdings_sync.py strategy_main.py specs/SPEC_SELL_LOOKUP_AND_HOLDINGS_SYNC.md
git commit -m "fix(qmt): 卖出反查兜底+持仓强制同步" -m "- 反查失败先查实际持仓再判成败，避免误撤活着的限价卖单" -m "- 开盘首帧+买入前双保险强制同步 _g_my_codes 与卖出引擎" -m "- 可买数量用同步后实际持仓算，修 603618 类残留占名额" -m "- 换仓卖单保留限价单等成交，窗口结束不撤卖单" -m "- rebuild strategy_main.py and validate QMT file"
```

## 严禁

- 禁止改 `config/global_config.yaml`
- 禁止改 `_load_pool`/`_parse_pool_line`/`POOL_PATH`
- 禁止改 C1/C2/B1/B2 风控信号判定
- 禁止改 `BUY_WINDOW_*` 和买入窗口逻辑
- 禁止改评分
- 禁止改 `strategy_allday.py` 全天调试路径
- 禁止 `git add .`
- 禁止 push
- 禁止把 `tests/test_pending_sell_and_close_mode.py` 的历史失败当本任务范围去修

## 停手条件

- TASK-1 任一定位字符串非唯一/匹配不到 → 停
- TASK-3 任一新测试 FAIL → 停（历史 close-mode 3 失败除外，记录不停）
- TASK-4 validate 非 6/6 → 停
- 累计 > 150 分钟 → 停

遇异常必停贴回执。

## 回执

贴：git log -1 --stat、测试结果（含 close-mode 历史失败如实记录）、build/validate 结果、目标文件 git status。
