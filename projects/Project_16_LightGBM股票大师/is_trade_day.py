# coding: utf-8
"""A股交易日判断（锁死交易时间，防止周末/法定节假日误触发交易、预判、盯盘）。

判断优先级：
  1) 周六/周日 -> 非交易日（A股周末必休；调休补班的周末同样休市）
  2) 日期在 data/ashare_trade_dates.txt（主库真实交易日快照，含增量）-> 交易日
  3) 不在快照（主库周更滞后或未来日期）-> 查 2026 法定节假日休市表：是休市日则非交易日，否则视为交易日

用法：
  python is_trade_day.py              # 判断今天
  python is_trade_day.py 2026-10-05   # 判断指定日期
  python is_trade_day.py --json       # JSON 输出
退出码: 0=交易日, 1=非交易日（供脚本/计划任务判断）

注：交易日历快照由主库生成，主库周更。未来日期依赖节假日表，每年需更新（附录）。
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CAL_FILE = os.path.join(HERE, "data", "ashare_trade_dates.txt")

# 2026 年 A 股法定节假日休市日（来源：沪深北交易所《2026年部分节假日休市安排》）
# 元旦 1/1-1/3；春节 2/15-2/23；清明 4/4-4/6；劳动节 5/1-5/5；端午 6/19-6/21；
# 中秋 9/25-9/27；国庆 10/1-10/7。（周末均休市，不在此重复）
HOLIDAYS = {
    "2026": {
        "2026-01-01", "2026-01-02", "2026-01-03",
        "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
        "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
        "2026-04-04", "2026-04-05", "2026-04-06",
        "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
        "2026-06-19", "2026-06-20", "2026-06-21",
        "2026-09-25", "2026-09-26", "2026-09-27",
        "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
        "2026-10-06", "2026-10-07",
    },
}


def _gen_calendar():
    """从主库 daily + 增量数据重建交易日历（缺日历文件时自动生成）。"""
    import pandas as pd
    import data_config as DC
    main = pd.read_parquet(DC.MAIN_DAILY, columns=[])
    dates = {d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]
             for d in main.index.get_level_values("trade_date")}
    incr_path = os.path.join(DC.LIVE_DIR, "incremental_daily.parquet")
    if os.path.exists(incr_path):
        incr = pd.read_parquet(incr_path, columns=["trade_date"])
        dates.update(d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]
                     for d in incr["trade_date"])
    os.makedirs(os.path.dirname(CAL_FILE), exist_ok=True)
    with open(CAL_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(dates)))
    return dates


def load_calendar():
    if os.path.exists(CAL_FILE):
        with open(CAL_FILE, encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    try:
        return _gen_calendar()
    except Exception as e:  # 主库不可用
        print(f"[warn] 无法生成交易日历: {e}", file=sys.stderr)
        return set()


def is_trade_day(d: datetime.date, cal) -> dict:
    ds = d.isoformat()
    if d.weekday() >= 5:
        return {"is_trade_day": False, "reason": "周末休市", "date": ds}
    if ds in cal:
        return {"is_trade_day": True, "reason": "主库交易日历", "date": ds}
    holidays = HOLIDAYS.get(str(d.year), set())
    if ds in holidays:
        return {"is_trade_day": False, "reason": "法定节假日休市", "date": ds}
    return {"is_trade_day": True, "reason": "默认交易日(主库未覆盖且非节假日)", "date": ds}


def main():
    cal = load_calendar()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        d = datetime.date.fromisoformat(args[0])
    else:
        d = datetime.date.today()
    res = is_trade_day(d, cal)
    if "--json" in sys.argv:
        print(json.dumps(res, ensure_ascii=False))
    else:
        tag = "是" if res["is_trade_day"] else "否"
        print(f"{res['date']} A股交易日: {tag}（{res['reason']}）")
    sys.exit(0 if res["is_trade_day"] else 1)


if __name__ == "__main__":
    main()
