# coding: utf-8
"""build_benchmark_index.py — 构建 D:/astock/benchmark/benchmark_index.duckdb。

benchmark_reader.BenchmarkIndexReader 需要的 DuckDB 表：
    index_daily(code TEXT, source TEXT, trade_date TEXT, close DOUBLE)
回测引擎默认路径（backtest/engine.py DEFAULT_BENCHMARK_DB）：
    D:/astock/benchmark/benchmark_index.duckdb

数据源（二选一，自动探测）：
  * xtdata（miniQMT / QMT 本地数据）—— 默认首选，需 QMT 客户端已下载指数日线
  * Tushare Pro —— 需环境变量 TUSHARE_TOKEN

注意：
  * 本文件只被研究侧（backtest engine）调用，不需要兼容 QMT 的 Python 3.6。
  * 不会删除既有表，重复运行会按 (code, source, trade_date) 去重覆盖。
  * 默认覆盖指数：沪深300 / 中证500 / 上证指数 / 深证成指 / 创业板指 /
    上证50 / 中证1000 / 中小板指。可用 --codes 自定义。

用法：
    python data/build_benchmark_index.py                 # 自动探测源
    python data/build_benchmark_index.py --method tushare
    python data/build_benchmark_index.py --method xtdata --start 2009-01-01

双环境说明（本仓库现状）：
    xtdata 仅在 miniQMT venv（Python 3.11）可用；duckdb 仅在系统 Python（3.13）可用。
    因此分两步：
      1) venv 拉取：  miniqmt\\Scripts\\python.exe data\\_fetch_bench_xtdata.py D:/astock/benchmark/_bench_raw.json
      2) 系统写库：  python data/build_benchmark_index.py --from-json D:/astock/benchmark/_bench_raw.json
"""
import argparse
import json
import os
import sys

DEFAULT_DB = os.environ.get(
    "BENCHMARK_DB_PATH", "D:/astock/benchmark/benchmark_index.duckdb")
DEFAULT_CODES = [
    "000300.SH",  # 沪深300（回测默认基准）
    "000905.SH",  # 中证500
    "000001.SH",  # 上证指数
    "399001.SZ",  # 深证成指
    "399006.SZ",  # 创业板指
    "000016.SH",  # 上证50
    "000852.SH",  # 中证1000
    "399005.SZ",  # 中小板指
]


def _norm_code(c):
    # xtdata 用 000300.SH；Tushare 也用 000300.SH —— 一致，无需转换
    return c.strip().upper()


def fetch_xtdata(codes, start, end):
    """返回 {code: [(trade_date_str, close_float), ...]} 升序。"""
    try:
        import xtquant.xtdata as xtdata
    except Exception as e:  # pragma: no cover
        print("[xtdata] 导入失败：%s" % e)
        return {}
    # 确保本地有数据（若 QMT 未下载，这里会拉取一次）
    for c in codes:
        try:
            xtdata.download_history_data(c, "1d", start, end)
        except Exception as e:
            print("[xtdata] 下载 %s 失败：%s" % (c, e))
    field = "close"
    raw = xtdata.get_local_data(
        field_list=[field], stock_list=codes, period="1d",
        start_time=start, end_time=end, count=-1)
    out = {}
    for c in codes:
        df = raw.get(c)
        if df is None or len(df) == 0:
            print("[xtdata] %s 无数据（请确认 QMT 已下载该指数日线）" % c)
            continue
        # df index 为 datetime，columns 含 'close'
        series = df["close"] if hasattr(df, "columns") else df
        rows = []
        for dt, v in series.items():
            if v is None:
                continue
            # xtdata 返回的索引是整数 YYYYMMDD，而非 datetime 对象
            dint = int(dt)
            ds = "%s-%s-%s" % (str(dint)[:4], str(dint)[4:6], str(dint)[6:8])
            rows.append((ds, float(v)))
        out[c] = sorted(rows, key=lambda x: x[0])
    return out


def fetch_tushare(codes, start, end):
    """返回 {code: [(trade_date_str, close_float), ...]} 升序。"""
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        print("[tushare] 未设置环境变量 TUSHARE_TOKEN，跳过")
        return {}
    try:
        import tushare as ts
    except Exception as e:  # pragma: no cover
        print("[tushare] 导入失败：%s" % e)
        return {}
    ts.set_token(token)
    pro = ts.pro_api()
    out = {}
    for c in codes:
        try:
            df = pro.index_daily(ts_code=c, start_date=start, end_date=end)
        except Exception as e:
            print("[tushare] %s 拉取失败：%s" % (c, e))
            continue
        if df is None or len(df) == 0:
            print("[tushare] %s 无数据" % c)
            continue
        df = df.sort_values("trade_date")
        out[c] = [
            (str(r["trade_date"]), float(r["close"]))
            for _, r in df.iterrows()
        ]
    return out


def write_duckdb(db_path, source, data):
    import duckdb
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = duckdb.connect(db_path, read_only=False)
    con.execute(
        "CREATE TABLE IF NOT EXISTS index_daily ("
        "code TEXT, source TEXT, trade_date TEXT, close DOUBLE)")
    total = 0
    for code, rows in data.items():
        if not rows:
            continue
        # 删除该 code+source 既有行后重写（幂等）
        con.execute(
            "DELETE FROM index_daily WHERE code = ? AND source = ?",
            [code, source])
        con.executemany(
            "INSERT INTO index_daily VALUES (?, ?, ?, ?)",
            [(code, source, d, c) for d, c in rows])
        total += len(rows)
        print("  写入 %s  %s 行 (%s ~ %s)" % (
            code, len(rows), rows[0][0], rows[-1][0]))
    con.close()
    print("[duckdb] 已写入 %s 共 %s 行 -> %s" % (source, total, db_path))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--codes", nargs="*", default=DEFAULT_CODES)
    ap.add_argument("--source", default="xtquant")
    ap.add_argument("--start", default="2009-01-01")
    ap.add_argument("--end", default="2099-12-31")
    ap.add_argument("--method", default="auto",
                    choices=["auto", "xtdata", "tushare"])
    ap.add_argument("--from-json", default=None,
                    help="从 _fetch_bench_xtdata.py 产出的 JSON 直接写库"
                         "（用于 duckdb 与 xtdata 不在同一 Python 环境时）")
    args = ap.parse_args()
    codes = [_norm_code(c) for c in args.codes]

    if args.from_json:
        with open(args.from_json, encoding="utf-8") as f:
            data = json.load(f)
        if not data:
            print("[build] JSON 为空，未写入。")
            sys.exit(2)
        write_duckdb(args.db, args.source, data)
        print("[build] 完成（--from-json）。")
        return

    method = args.method
    if method == "auto":
        try:
            import xtquant.xtdata  # noqa
            method = "xtdata"
        except Exception:
            try:
                import tushare  # noqa
                if os.environ.get("TUSHARE_TOKEN"):
                    method = "tushare"
                else:
                    method = "xtdata"  # 退而求其次，让它报错提示
            except Exception:
                method = "xtdata"

    print("[build] 方法=%s  指数数=%d  区间=%s~%s" % (
        method, len(codes), args.start, args.end))
    if method == "xtdata":
        data = fetch_xtdata(codes, args.start, args.end)
    else:
        data = fetch_tushare(codes, args.start, args.end)

    if not data:
        print("[build] 未获取到任何指数数据，未写入。请检查数据源连接/凭证。")
        sys.exit(2)
    write_duckdb(args.db, args.source, data)
    print("[build] 完成。")


if __name__ == "__main__":
    main()
