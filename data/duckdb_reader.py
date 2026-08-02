# coding: utf-8
"""DuckDB read-only reader (v0.2 + v0.3 主路径切换 qmt_self_owned).

支持两种 schema（必须显式声明 data_source；不做 auto detector）：

  data_source = "jince_zhisuan"  (v0.2 路径)
    - 表 dat_day, 列 trade_time TIMESTAMP WITH TIME ZONE
    - 同日双时间戳，需要 QUALIFY ROW_NUMBER 去重
    - WAL 检测沿用

  data_source = "qmt_self_owned" (v0.3 主路径)
    - 表 dat_day, 列 trade_date DATE（项目自管 sync 已保证唯一）
    - 上游 (code, trade_date, adjustment, source) 唯一；不做 QUALIFY
    - 默认按 source='xtquant' 过滤；需多源时上层指定

Constraints (SPEC §1, decisions A/C/D/I + Hermes 06/07):
  - access_mode='read_only'
  - 不写、不 ATTACH
  - 不做 auto schema detector，调用方必须显式 data_source
  - 对外输出统一为列：date, open, high, low, close, vol, amount
"""
import datetime as _dt
import logging
import os

import duckdb

log = logging.getLogger(__name__)

JINCE_ZHISUAN = "jince_zhisuan"
QMT_SELF_OWNED = "qmt_self_owned"
SUPPORTED_SOURCES = (JINCE_ZHISUAN, QMT_SELF_OWNED)


class DuckDBDailyReader(object):
    """统一 reader；data_source 决定 schema 路径。

    qmt_self_owned 默认按 adjustment='hfq' / source='xtquant' 过滤；
    如未来需切换，构造时改 default_filters。
    """

    def __init__(self, db_path, data_source=JINCE_ZHISUAN, default_filters=None):
        if data_source not in SUPPORTED_SOURCES:
            raise ValueError(
                "data_source must be one of %s, got: %s" % (
                    SUPPORTED_SOURCES, data_source))
        if not os.path.isfile(db_path):
            raise FileNotFoundError("DuckDB not found: " + db_path)
        self.db_path = db_path
        self.data_source = data_source
        if default_filters is None and data_source == QMT_SELF_OWNED:
            default_filters = {"adjustment": "hfq", "source": "xtquant"}
        self.default_filters = default_filters or {}
        self._conn = duckdb.connect(db_path, read_only=True)
        self._db_mtime = self._read_mtime()
        self.wal_detected = self._check_wal() if data_source == JINCE_ZHISUAN else False
        self._coverage_cache = None

    def _read_mtime(self):
        ts = os.path.getmtime(self.db_path)
        return _dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds")

    def _check_wal(self):
        wal = self.db_path + ".wal"
        if os.path.isfile(wal):
            msg = (u"⚠️ 检测到 quantifydata.duckdb.wal，"
                   u"金策智算可能正在同步数据。"
                   u"本次回测的 data_hash 在同步完成前不稳定，"
                   u"请同步结束后重跑确认。")
            log.warning(msg)
            print(msg)
            self.wal_warning_message = msg
            return True
        self.wal_warning_message = ""
        return False

    def _date_expr(self):
        if self.data_source == QMT_SELF_OWNED:
            return "trade_date"
        return "CAST(trade_time AS DATE)"

    def _filter_clause(self):
        if not self.default_filters:
            return "", []
        keys = sorted(self.default_filters.keys())
        clause = " AND " + " AND ".join("%s = ?" % k for k in keys)
        params = [self.default_filters[k] for k in keys]
        return clause, params

    def load_window(self, codes, start_date, end_date):
        if not codes:
            raise ValueError("codes is empty")
        cov = self.coverage()
        if start_date < cov["min_date"] or end_date > cov["max_date"]:
            raise ValueError(
                "requested range [%s, %s] out of coverage [%s, %s]"
                % (start_date, end_date, cov["min_date"], cov["max_date"]))

        d = self._date_expr()
        flt, flt_params = self._filter_clause()
        placeholders = ",".join(["?"] * len(codes))

        if self.data_source == QMT_SELF_OWNED:
            sql = (
                "SELECT code, trade_date AS date, "
                "       open, high, low, close, vol, amount "
                "FROM dat_day "
                "WHERE code IN (" + placeholders + ") "
                "  AND trade_date BETWEEN ? AND ?"
                + flt +
                " ORDER BY code, date"
            )
        else:
            sql = (
                "SELECT code, " + d + " AS date, "
                "       open, high, low, close, vol, amount "
                "FROM dat_day "
                "WHERE code IN (" + placeholders + ") "
                "  AND " + d + " BETWEEN ? AND ? "
                "QUALIFY ROW_NUMBER() OVER ("
                "    PARTITION BY code, " + d + " "
                "    ORDER BY trade_time DESC) = 1 "
                "ORDER BY code, date"
            )
        params = list(codes) + [start_date, end_date] + flt_params
        df = self._conn.execute(sql, params).fetchdf()
        df["date"] = df["date"].astype(str)
        out = {}
        for code, sub in df.groupby("code"):
            sub = sub.drop(columns=["code"]).reset_index(drop=True)
            out[code] = sub
        return out

    def trading_calendar(self, start_date, end_date):
        d = self._date_expr()
        flt, flt_params = self._filter_clause()
        sql = (
            "SELECT DISTINCT " + d + " AS dd "
            "FROM dat_day "
            "WHERE " + d + " BETWEEN ? AND ?" + flt + " "
            "ORDER BY dd"
        )
        rows = self._conn.execute(sql, [start_date, end_date] + flt_params).fetchall()
        return [str(r[0]) for r in rows]

    def coverage(self, codes=None, start_date=None, end_date=None):
        if self._coverage_cache is None:
            d = self._date_expr()
            flt, flt_params = self._filter_clause()

            if self.data_source == QMT_SELF_OWNED:
                row = self._conn.execute(
                    "SELECT MIN(trade_date), MAX(trade_date), "
                    "       COUNT(DISTINCT code), COUNT(*) "
                    "FROM dat_day WHERE 1=1" + flt,
                    flt_params).fetchone()
                total_raw = self._conn.execute(
                    "SELECT COUNT(*) FROM dat_day WHERE 1=1" + flt,
                    flt_params).fetchone()[0]
                rows_after_dedup = int(row[3])
            else:
                row = self._conn.execute("""
                    WITH dedup AS (
                        SELECT code, CAST(trade_time AS DATE) AS d
                        FROM dat_day
                        QUALIFY ROW_NUMBER() OVER (PARTITION BY code, CAST(trade_time AS DATE)
                                                   ORDER BY trade_time DESC) = 1
                    )
                    SELECT MIN(d), MAX(d), COUNT(DISTINCT code), COUNT(*)
                    FROM dedup
                """).fetchone()
                total_raw = self._conn.execute(
                    "SELECT COUNT(*) FROM dat_day").fetchone()[0]
                rows_after_dedup = int(row[3])

            self._coverage_cache = {
                "data_source": self.data_source,
                "min_date": str(row[0]),
                "max_date": str(row[1]),
                "n_codes": int(row[2]),
                "n_rows_after_dedup": rows_after_dedup,
                "dedup_count": int(total_raw - rows_after_dedup),
                "db_mtime": self._db_mtime,
            }
        cov = dict(self._coverage_cache)
        if codes is not None:
            d = self._date_expr()
            flt, flt_params = self._filter_clause()
            ph = ",".join(["?"] * len(codes))
            sd = start_date or cov["min_date"]
            ed = end_date or cov["max_date"]
            present = self._conn.execute(
                "SELECT DISTINCT code FROM dat_day "
                "WHERE code IN (" + ph + ") "
                "  AND " + d + " BETWEEN ? AND ?" + flt,
                list(codes) + [sd, ed] + flt_params).fetchall()
            present_set = {r[0] for r in present}
            missing = [c for c in codes if c not in present_set]
            cov["universe_coverage"] = {
                "universe_size": len(codes),
                "codes_with_data": len(present_set),
                "codes_missing": missing,
                "missing_count": len(missing),
            }
        return cov

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __del__(self):
        self.close()
