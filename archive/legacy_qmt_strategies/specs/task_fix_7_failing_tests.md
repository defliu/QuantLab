# Task: Fix 7 Failing Tests

## Current State
- Total: 160 passed, 7 failed
- All 7 failures are environment/test-infrastructure issues, NOT strategy logic bugs

## The 7 Failures

### Failure 1: test_default_datasource_is_xtquant
**File**: tests/test_run_backtest.py :: TestDatasource :: test_default_datasource_is_xtquant
**Error**: `TypeError: 'NoneType' object is not iterable` at `run_backtest.py:646`
**Root cause**: `xtdata.get_market_data_ex()` returns None when data hasn't been downloaded first. xtquant requires `download_history_data()` before `get_market_data_ex()`.

**Fix approach**: Make the test robust by one of:
- Option A: Add `download_history_data()` call before `get_market_data_ex()` in the test/function
- Option B: Use pytest.mark.skipif to skip if MiniQMT is not connected (check if `xtdata.connect()` succeeds)
- Option C: Add a try/except around xtquant data fetch, fall back to mootdx gracefully

Prefer Option B (skip if no MiniQMT) since this test verifies the datasource switching logic, not xtquant itself. The xtquant connection depends on MiniQMT being running, which is a runtime condition.

### Failures 2-7: 6 SAFEMODE tests (order-dependent flaky)
**File**: tests/test_safemode.py
**Tests affected**:
- TestTraderSafemodeInterception::test_buy_returns_mock_order_id
- TestTraderSafemodeInterception::test_sell_returns_mock_order_id
- TestTraderSafemodeInterception::test_direct_passorder_asserts
- TestSafemodeLogger::test_log_trade_blocked_creates_csv
- TestSafemodeLogger::test_log_signal_creates_csv
- TestSafemodeLogger::test_log_trade_blocked_append

**Root cause**: These tests run fine in isolation (`pytest tests/test_safemode.py -v` → 13/13 pass), but fail during full suite run (`pytest tests/`). Reason: other tests pollute the module-level SAFEMODE_ENABLED flag via import side-effects, or the mock_context fixture gets consumed/shared incorrectly.

**Fix approach**:
1. Add `conftest.py` fixture (or modify existing) with `@pytest.fixture(autouse=True)` that resets SAFEMODE state before each test
2. In each SAFEMODE test that needs it, explicitly verify SAFEMODE_ENABLED is True as a precondition, or use `monkeypatch.setattr` to force it
3. Make the logger tests self-contained by cleaning up after themselves with `tmp_path` or using proper teardown

**Specific fix for logger tests**: 
- The `test_log_trade_blocked_creates_csv` and `test_log_signal_creates_csv` tests that create/check files: use `tmp_path` fixture to create files in a temp directory instead of hardcoded `D:/QMT_POOL/safemode_logs/`, or use `monkeypatch` to change SAFEMODE_LOG_DIR to a temp dir.

## Files to Modify

| File | Change |
|------|--------|
| `tests/test_run_backtest.py` | Add skipif for xtquant test, or fix data download order |
| `tests/test_safemode.py` | Add monkeypatch/autouse fixtures to ensure SAFEMODE state is predictable |
| `tests/conftest.py` (if exists) | Add global fixtures for SAFEMODE state reset |

## Verification

```bash
# Must pass - run 3 times to confirm no flakiness
cd D:\QMT_STRATEGIES
C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/ -v --tb=short 2>&1 | findstr "FAILED"
# Expected: 0 failures after fix

# Run safemode tests in isolation - must still pass
C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/test_safemode.py -v --tb=short 2>&1 | findstr "FAILED"
# Expected: 0 failures

# Run 3 consecutive full suite runs to verify no flakiness
```

## Constraints
- DO NOT modify the business logic in `adapters/qmt_wrapper.py` or `scripts/run_backtest.py` — this is a test-only fix
- All existing passing tests must continue to pass
- The fix should make the 6 safemode tests order-independent (run fine whether run alone or as part of full suite)
