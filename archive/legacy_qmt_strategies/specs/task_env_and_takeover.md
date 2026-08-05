# Task: Environment Report + Scripts Takeover

## Objective
Two-part task:
1. **Part A**: Review and report how the current development/test/backtest environment was set up (what exists, what works, what's missing)
2. **Part B**: Take over two scripts that were created outside the standard workflow and integrate them properly

---

## Part A — Environment Status Report

Read the project thoroughly and answer:

1. **Python Environment**: What Python versions are installed, what packages are available (pytest, pandas, numpy, mootdx, xtquant, pyyaml), how to run tests
2. **Test Framework**: How many tests exist, which pass/fail, how to run the full suite
3. **Data Sources**: What data sources are configured (mootdx/TDX, xtquant/MiniQMT, 腾讯, 同花顺), how to verify each
4. **Backtest Engine**: How `scripts/run_backtest.py` works, what parameters it accepts, how to run a backtest
5. **Build Pipeline**: How `scripts/build_strategy.py` generates `strategy_main.py`
6. **MiniQMT**: Is the simulation client connected? How to verify? What's the xtquant connection status?
7. **SAFEMODE**: What is it, how does it work, what's blocked
8. **Known Issues**: What test failures exist, what's broken

Output a clear report to `D:\QMT_STRATEGIES\environment_report.md`.

---

## Part B — Scripts Takeover & Integration

Two "rogue" scripts were written directly by Hermes (violating workflow rules). Your job is to:

### B1 — `scripts/diagnose_dimension6plus2.py`
**Current state**: Runs ScoreCalculator6Plus2 on pool stocks using mootdx data. Dumps CSV and prints table. Works but was written ad-hoc without tests.

**Requirements**:
1. Read the existing script to understand its logic
2. Create a proper version that:
   - Uses the same data pipeline pattern as `scripts/diagnose_score_8d.py` (reference file)
   - Has proper error handling (graceful on missing pool file, partial download failures, scoring errors)
   - Uses mootdx for data (same pattern as diagnose_score_8d.py: `client.bars(symbol=code6, category=4, offset=req_bars)`)
   - Reuses `_strip_suffix`/`_add_suffix`/`read_pool` from diagnose_score_8d.py pattern
   - Outputs a ranked score table + CSV
3. Create a small pytest test: `tests/test_diagnose_dimension6plus2.py` that:
   - Tests the data pipeline functions work with minimal/synthetic data
   - Validates column handling works
4. Run the script against the actual pool file and report top 5 results

### B2 — `scripts/backtest_dimension6plus2.py`
**Current state**: Simple daily-scoring-then-top3-buy-next-day-sell backtest. Runs on mootdx data. Has no test, no config, no parameter validation.

**Requirements**:
1. Read the existing script to understand its logic
2. Refactor it into a proper structure:
   - Extract configurable parameters (BACKTEST_DAYS, TOP_N, HOLD_DAYS) to function arguments or a config dict
   - Use the same data pipeline as diagnose scripts
   - Add a `def run_backtest(pool_path, backtest_days=60, top_n=3, hold_days=1)` entry point
   - Keep command-line usage working (`python scripts/backtest_dimension6plus2.py`)
3. Add parameters for:
   - `--days` (how many trading days to backtest, default 60)
   - `--top` (how many stocks to pick daily, default 3)
   - `--hold` (holding days, default 1)
   - `--pool` (pool file path, default D:/QMT_POOL/selected.txt)
4. Create a small pytest test: `tests/test_backtest_dimension6plus2.py` that:
   - Tests run_backtest function with synthetic/minimal data
   - Validates output format (correct columns, finite values)
5. Run the script with default parameters and report the headline results

### B3 — Cleanup
1. After both scripts are validated and working, mark the CLAUDE.md "known issues" items 3 and 4 as resolved
2. Run `pytest tests/ -v` and report total pass/fail count

---

## Files & Locations

| Item | Path |
|------|------|
| Task Spec (this file) | `D:\QMT_STRATEGIES\specs\task_env_and_takeover.md` |
| CC project memory | `D:\QMT_STRATEGIES\CLAUDE.md` (already created, do not modify) |
| Environment report | `D:\QMT_STRATEGIES\environment_report.md` (create) |
| Diagnose script (target) | `D:\QMT_STRATEGIES\scripts\diagnose_dimension6plus2.py` |
| Backtest script (target) | `D:\QMT_STRATEGIES\scripts\backtest_dimension6plus2.py` |
| Diagnose test | `D:\QMT_STRATEGIES\tests\test_diagnose_dimension6plus2.py` (create) |
| Backtest test | `D:\QMT_STRATEGIES\tests\test_backtest_dimension6plus2.py` (create) |
| Reference: 8D diagnose | `D:\QMT_STRATEGIES\scripts\diagnose_score_8d.py` |
| Reference: 8D backtest | `D:\QMT_STRATEGIES\scripts\run_backtest.py` |
| Workflow pipeline | `D:\QMT_STRATEGIES\docs\workflows\WORKFLOW-pipeline.md` |
| Scoring module | `D:\QMT_STRATEGIES\core\scoring\dimension6plus2.py` |
| Config | `D:\QMT_STRATEGIES\config\global_config.yaml` |

## Constraints

- **DO NOT modify `CLAUDE.md`** — it is the project's permanent memory file
- **DO NOT modify existing working tests** (test_dimension6plus2.py has 17 passing tests, don't break them)
- Python 3.10 compatible
- UTF-8 encoding
- All new scripts must handle pool file not existing gracefully
- For mootdx: use `client.bars(symbol=code6, category=4, offset=800)` for daily data
- For xtquant: MiniQMT simulation client is at D:\国金QMT交易端模拟\, connected on port 58610
- All PEs default to dynamic_pe=20.0, static_pe=25.0 in scoring

## Verification

1. Run: `cd D:\QMT_STRATEGIES && C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/test_diagnose_dimension6plus2.py -v` — must pass
2. Run: `cd D:\QMT_STRATEGIES && C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/test_backtest_dimension6plus2.py -v` — must pass
3. Run: `cd D:\QMT_STRATEGIES && C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/ -v` — must NOT decrease the pass count
4. Run: `cd D:\QMT_STRATEGIES && C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe scripts\diagnose_dimension6plus2.py` — must produce a valid score table
5. Run: `cd D:\QMT_STRATEGIES && C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe scripts\backtest_dimension6plus2.py` — must produce valid backtest results
