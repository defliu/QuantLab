# -*- coding: utf-8 -*-
"""astock_kit 在线补充数据每日定时更新脚本

把 a-stock-data 的当日/实时在线数据(全市场一版)拉取落地到:
    data/astock_kit_data/YYYYMMDD/
    margin.json     全市场当日融资融券
    holder.json     全市场最新一期股东户数
    block.json      全市场当日大宗交易
    lockup.json     全市场未来90天限售解禁
    lhb.json        全市场当日龙虎榜
    zt_pool.json    当日涨停/炸板/跌停池
    industry.json   行业涨幅排名 + 行业板块资金流
    news.json       财联社电报 + 东财全球资讯 + 同花顺强势股题材
    reports.json    全市场当日研报
    manifest.json   汇总(时间/各文件条数/状态/耗时)

交易日判断: 周一~周五(内置); 节假日由数据源自然返回空, 不阻塞。
用法:
  python scripts/update_astock_kit.py            # 正常执行(当日)
  python scripts/update_astock_kit.py --date 2026-08-31
  python scripts/update_astock_kit.py --force    # 周末也跑
  python scripts/update_astock_kit.py --dry      # 只打印不落盘
退出码: 有任一类成功落地=0; 全部失败=1。
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import astock_kit as ak

DATA_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "astock_kit_data")


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def is_weekday(d):
    return d.weekday() < 5


def _save_json(directory, name, data):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return len(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD, 默认今天")
    ap.add_argument("--force", action="store_true", help="周末也执行")
    ap.add_argument("--dry", action="store_true", help="只打印不落盘")
    args = ap.parse_args()

    day = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now()
    if not args.force and not is_weekday(day):
        log("[SKIP] %s 是周末, 非交易日。--force 可强制。" % day.strftime("%Y-%m-%d"))
        return 0

    date_str = day.strftime("%Y-%m-%d")
    date_compact = day.strftime("%Y%m%d")
    log("=== astock_kit 定时更新 %s ===" % date_str)
    _t_start = time.time()

    out_dir = os.path.join(DATA_ROOT, date_compact)
    if not args.dry:
        os.makedirs(out_dir, exist_ok=True)

    tasks = [
        ("margin", lambda: ak.daily_margin_market(), {}),
        ("holder", lambda: ak.latest_holder_market(), {}),
        ("block", lambda: ak.daily_block_market(), {}),
        ("lockup", lambda: ak.upcoming_lockup_market(date_str, 90), {}),
        ("lhb", lambda: ak.daily_dragon_tiger(date_str), {"stocks": "stocks"}),
        ("zt_pool", lambda: {
            "date": date_compact,
            "zt": ak.em_zt_pool(date_compact),
            "zb": ak.em_zb_pool(date_compact),
            "dt": ak.em_dt_pool(date_compact),
        }, {}),
        ("industry", lambda: {
            "rank": ak.industry_comparison(30),
            "fund_flow": ak.board_fund_flow("industry", "today", 20),
        }, {}),
        ("news", lambda: {
            "cls": ak.cls_telegraph(50),
            "global": ak.eastmoney_global_news(50),
            "hot": ak.ths_hot_reason(date_str),
        }, {}),
        ("reports", lambda: ak.today_reports_market(date_str), {}),
    ]

    manifest = {"date": date_str, "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "files": {}, "ok_count": 0, "fail_count": 0}
    any_ok = False
    for name, fn, _ in tasks:
        t0 = time.time()
        try:
            data = fn()
            n = len(data)
            if args.dry:
                log("[DRY] %-10s 拉取 %d 条 (%.1fs)" % (name, n, time.time() - t0))
            else:
                n = _save_json(out_dir, name + ".json", data)
                log("[OK  ] %-10s 落地 %d 条 (%.1fs)" % (name, n, time.time() - t0))
            manifest["files"][name] = n
            manifest["ok_count"] += 1
            any_ok = True
        except Exception as e:
            log("[FAIL] %-10s %r" % (name, e))
            manifest["files"][name] = "FAIL: %r" % e
            manifest["fail_count"] += 1

    manifest["finished"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    manifest["duration_s"] = round(time.time() - _t_start, 1)
    if not args.dry:
        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
    log("=== 完成: ok=%d fail=%d, 快照=%s ===" % (manifest["ok_count"], manifest["fail_count"], out_dir))
    return 0 if any_ok else 1


if __name__ == "__main__":
    sys.exit(main())
