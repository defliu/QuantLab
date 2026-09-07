# coding: utf-8
"""打印 data_live/incremental_daily.parquet 最新交易日（供 run_scheduled.ps1 调用）。

替代 PS 内联 `& $py -c "..."` 写法——PS 5.1 无法解析其中 `columns=['trade_date']`
这类单引号 + 数组下标序列（2026-09-01 16:30 daily 失败根因），独立脚本最稳。
文件不存在/读取失败 -> 打印空行（调用方 $latest 为空即跳过 merge/deploy）。
"""
import os

LIVE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_live", "incremental_daily.parquet")


def main():
    try:
        import pandas as pd
        d = pd.read_parquet(LIVE, columns=["trade_date"])
        print(d["trade_date"].max().strftime("%Y-%m-%d"))
    except Exception as e:
        print("", flush=True)


if __name__ == "__main__":
    main()
