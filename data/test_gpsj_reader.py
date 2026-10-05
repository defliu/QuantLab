# -*- coding: utf-8 -*-
"""T-20260827-003: gpsj_reader is_st 语义反转归一化测试。

背景（全局复利与踩坑日志 32b / 研究总览踩坑#12）：
  gpsj "ST" 列语义与 astock/tushare 相反（gpsj 0=该股是 ST、1=正常），
  data/gpsj_reader.py 原先把 "ST" 直接映射为 is_st 未反转 → 凡用 gpsj_reader
  的 is_st 做 ST 过滤，结果反转（正常股被剔、ST 股被放行）。
  2026-10-05 修复：_sql_cols() 对 "ST" 生成 (1 - "ST") AS is_st，
  归一化为 astock 口径（1=ST、0=正常）；"ST" 为 NULL 时 is_st 亦为 NULL(NaN)。

三层测试（按依赖强度递增，缺依赖自动 skip 并注明，不静默跳过）：
  1. test_sql_cols_inverts_is_st      SQL 文本断言（无 duckdb 也能跑，stub import）
  2. test_load_window_is_st_normalized 内存 duckdb 假库行为测试（需 duckdb 模块；
                                      当前 miniqmt/Py311 env 均无 duckdb → skip，
                                      装上后自动生效）
  3. test_is_st_matches_astock        与 astock 逐日对拍（需 gpsj 备用源可用；
                                      E 盘缺失期间 → skip，恢复后自动生效，
                                      AGENTS 降级规则：skip 须显式标注）

跑法（miniqmt env）：
  python -m pytest data/test_gpsj_reader.py -v
  python data/test_gpsj_reader.py          # 脚本式 runner，同断言
"""
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ASTOCK_DAILY = "D:/astock/daily/stock_daily.parquet"


def _load_gpsj_reader():
    """import data.gpsj_reader；duckdb 模块缺失时用 stub 顶替（仅 SQL 生成逻辑，
    不触库），用完从 sys.modules 移除 stub，避免污染后续测试的 importorskip 判断。"""
    stubbed = False
    if "duckdb" not in sys.modules:
        try:
            import duckdb  # noqa: F401
        except ImportError:
            sys.modules["duckdb"] = types.ModuleType("duckdb")
            stubbed = True
    try:
        import data.gpsj_reader as gr
        return gr
    finally:
        if stubbed:
            sys.modules.pop("duckdb", None)


# ---------------------------------------------------------------- test 1
def test_sql_cols_inverts_is_st():
    """SQL 必须对 "ST" 反转（(1 - "ST") AS is_st），其余列照常透传。"""
    gr = _load_gpsj_reader()
    sql = gr.GpsjDuckDBReader._sql_cols()
    assert '(1 - "ST") AS is_st' in sql, "is_st 未反转归一化: %s" % sql
    assert '"ST" AS is_st' not in sql, "旧透传写法残留: %s" % sql
    assert '"不复权_收盘价" AS close' in sql
    assert '"复权因子" AS adj_factor' in sql
    # 每列恰好一个 " AS "，列数与 _COL_MAP 一致
    assert sql.count(" AS ") == len(gr._COL_MAP), (
        "SELECT 列数(%d) != _COL_MAP 条目数(%d)" % (sql.count(" AS "), len(gr._COL_MAP)))


# ---------------------------------------------------------------- test 2
def _make_fake_gpsj_db(db_dir):
    """构造与真库同构的假 gpsj.duckdb（catalog 名=文件 stem 'gpsj'，
    使 reader 的 gpsj.daily_data."日线数据" 三段式引用可解析）。
    插入 3 只股票：正常股(gpsj ST=1)、ST 股(gpsj ST=0)、ST 为 NULL 股。"""
    import duckdb
    db_path = os.path.join(str(db_dir), "gpsj.duckdb")
    con = duckdb.connect(db_path)
    try:
        con.execute('CREATE SCHEMA daily_data')
        gr = _load_gpsj_reader()
        cols = ['"股票代码" VARCHAR', '"交易日期" DATE']
        for g in gr._COL_MAP:
            cols.append('"%s" DOUBLE' % g)
        con.execute('CREATE TABLE daily_data."日线数据" (%s)' % ", ".join(cols))
        dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
        for code, st in [("000001.SZ", 1.0),   # gpsj 语义：正常
                         ("000003.SZ", 0.0),   # gpsj 语义：ST
                         ("000004.SZ", None)]:  # 缺失
            for d in dates:
                vals = ["'%s'" % code, "DATE '%s'" % d]
                for g in gr._COL_MAP:
                    if g == "ST":
                        vals.append("NULL" if st is None else repr(float(st)))
                    elif g == "不复权_收盘价":
                        vals.append("10.0")
                    elif g == "复权因子":
                        vals.append("1.0")
                    else:
                        vals.append("1.0")
                con.execute('INSERT INTO daily_data."日线数据" VALUES (%s)'
                            % ", ".join(vals))
    finally:
        con.close()
    return db_path


def test_load_window_is_st_normalized(tmp_path=None):
    """行为测试：load_window 返回的 is_st 必须是 astock 口径。
    gpsj ST=1(正常)→is_st=0；ST=0(ST)→is_st=1；ST=NULL→is_st=NaN。
    其他列（close/adj_factor）原样透传不受反转影响。"""
    duckdb = pytest.importorskip(
        "duckdb",
        reason="duckdb 模块不可用（miniqmt/Py311 env 均未装）——行为测试待 "
               "duckdb 可用后自动生效；SQL 层断言见 test_sql_cols_inverts_is_st")
    if tmp_path is None:
        import tempfile
        tmp_path = tempfile.mkdtemp(prefix="gpsj_test_")
    db_path = _make_fake_gpsj_db(tmp_path)
    gr = _load_gpsj_reader()
    reader = gr.GpsjDuckDBReader(db_path=db_path)  # adjustment 默认 raw
    try:
        win = reader.load_window(
            ["000001.SZ", "000003.SZ", "000004.SZ"], "2024-01-02", "2024-01-05")
    finally:
        reader.close_conn()
    assert set(win.keys()) == {"000001.SZ", "000003.SZ", "000004.SZ"}
    normal, st_stk, null_stk = win["000001.SZ"], win["000003.SZ"], win["000004.SZ"]
    assert (normal["is_st"] == 0).all(), "gpsj ST=1(正常) 应回转成 is_st=0"
    assert (st_stk["is_st"] == 1).all(), "gpsj ST=0(ST) 应回转成 is_st=1"
    assert normal["is_st"].isna().sum() == 0
    assert null_stk["is_st"].isna().all(), "gpsj ST=NULL 应回转成 is_st=NaN(不猜测缺省)"
    # 透传不受影响
    assert abs(normal["close"].iloc[0] - 10.0) < 1e-9
    assert abs(normal["adj_factor"].iloc[0] - 1.0) < 1e-9


# ---------------------------------------------------------------- test 3
def test_is_st_matches_astock():
    """与 astock 逐日对拍：随机 1 个自然年 × 随机 100 只，is_st 一致率须 >=99%
    （同源 tushare，理论上全等；1% 余量防个别快照修订差异）。
    gpsj 备用源不可用期间按 AGENTS 降级规则 skip 并显式标注。"""
    pytest.importorskip(
        "duckdb",
        reason="duckdb 模块不可用——对拍测试待 duckdb 可用后自动生效")
    import random

    import pandas as pd

    gr = _load_gpsj_reader()
    if not gr.is_available():
        pytest.skip("[SKIP] gpsj 备用源不可用(%s)——is_st 对拍未执行，"
                    "恢复后本测试自动生效（AGENTS 降级规则：显式标注不静默）"
                    % gr.GPSJ_DB_PATH)

    random.seed(42)
    year = random.choice([2019, 2020, 2021, 2022, 2023, 2024, 2025])
    start, end = "%d-01-01" % year, "%d-12-31" % year
    print("[info] 对拍区间: %d 全年" % year)

    # astock 侧
    ad = pd.read_parquet(ASTOCK_DAILY, columns=["ts_code", "trade_date", "is_st"])
    sub = ad[(ad["trade_date"] >= pd.Timestamp(start))
             & (ad["trade_date"] <= pd.Timestamp(end))]
    assert not sub.empty, "astock %d 年无数据" % year
    codes = sorted(sub["ts_code"].unique().tolist())
    codes = random.sample(codes, min(100, len(codes)))
    sub = sub[sub["ts_code"].isin(set(codes))]
    a_map = {}
    for r in sub.itertuples(index=False):
        a_map[(r.ts_code, r.trade_date.strftime("%Y-%m-%d"))] = float(r.is_st)

    # gpsj 侧
    reader = gr.GpsjDuckDBReader()  # raw
    try:
        win = reader.load_window(codes, start, end)
    finally:
        reader.close_conn()

    match = mismatch = null_cnt = 0
    diffs = []
    for code, df in win.items():
        for r in df.itertuples(index=False):
            av = a_map.get((code, r.date))
            gv = r.is_st
            if av is None or pd.isna(av) or pd.isna(gv):
                null_cnt += 1
                continue
            if av == float(gv):
                match += 1
            else:
                mismatch += 1
                if len(diffs) < 10:
                    diffs.append((code, r.date, av, float(gv)))
    total = match + mismatch
    print("[info] 逐日对拍: 一致=%d 不一致=%d 任一侧缺失=%d" % (match, mismatch, null_cnt))
    assert total >= 1000, "可对拍样本不足(%d)，扩大抽样后重试" % total
    rate = match / float(total)
    print("[info] 一致率: %.4f" % rate)
    if diffs:
        print("[warn] 差异样本(前10): %s" % diffs)
    assert rate >= 0.99, "is_st 逐日对拍一致率 %.4f < 99%%，差异样本: %s" % (rate, diffs)


# ---------------------------------------------------------------- 脚本式 runner
def _run_all():
    try:
        import pytest
    except ImportError:
        print("[FATAL] 需要 pytest（miniqmt env 自带）")
        return 1
    tests = [
        ("test_sql_cols_inverts_is_st", test_sql_cols_inverts_is_st),
        ("test_load_window_is_st_normalized", test_load_window_is_st_normalized),
        ("test_is_st_matches_astock", test_is_st_matches_astock),
    ]
    n_pass = n_skip = n_fail = 0
    for name, fn in tests:
        try:
            fn()
            print("[PASS] %s" % name)
            n_pass += 1
        except BaseException as e:  # noqa: BLE001 - runner 需分类 Skipped/Failed
            if type(e).__name__ == "Skipped":
                print("[SKIP] %s: %s" % (name, e))
                n_skip += 1
            else:
                import traceback
                traceback.print_exc()
                print("[FAIL] %s" % name)
                n_fail += 1
    print("=" * 60)
    print("合计: PASS=%d SKIP=%d FAIL=%d" % (n_pass, n_skip, n_fail))
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_all())
