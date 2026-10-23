# -*- coding: utf-8 -*-
"""Project_23 每日模拟盘 —— 主入口。

两种模式：
  replay  : 从 start 回放到 end（用于验证基础设施、补齐历史）
  step    : 只推进最新一天（供定时任务每日调用）

用法：
  python run_paper.py --mode replay --start 2026-08-19
  python run_paper.py --mode replay --start 2026-08-19 --strategy g2
  python run_paper.py --mode step
  python run_paper.py --mode report        # 只重算指标出报告

设计约束：
  - 幂等：已推进过的日期自动跳过（engine 层拦截）
  - fail-safe：任一策略异常不阻断另一个策略
"""
import argparse
import json
import os
import sys
import traceback

import pandas as pd
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from paper.data import QuotePanel          # noqa: E402
from paper.engine import PaperEngine       # noqa: E402
from paper.metrics import compute_metrics  # noqa: E402
from paper import signals as SIG           # noqa: E402

CONFIG = os.path.join(HERE, "config", "paper.yaml")
RESULTS = os.path.join(HERE, "results")


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_engine(name, cfg, g, panel):
    return PaperEngine(name, cfg, g, panel)


def cmd_replay(args):
    conf = load_config()
    g = conf["global"]
    names = [args.strategy] if args.strategy else \
        [k for k, v in conf["strategies"].items() if v.get("enabled", True)]

    # 确定回放区间：取各策略信号最早日期
    starts = []
    for n in names:
        ds = SIG.available_dates(conf["strategies"][n])
        if ds:
            starts.append(ds[0])
    if not starts:
        print("[FAIL] 无任何信号文件")
        return 1
    start = pd.Timestamp(args.start) if args.start else min(starts)
    end = pd.Timestamp(args.end) if args.end else pd.Timestamp.today().normalize()

    print("[1/3] 载入行情面板 %s ~ %s ..." % (start.date(), end.date()))
    panel = QuotePanel(start, end)
    dates = panel.trading_dates(start, end)
    print("      交易日 %d 个" % len(dates))

    rc = 0
    for n in names:
        cfg = conf["strategies"][n]
        eng = build_engine(n, cfg, g, panel)
        print("\n[2/3] %s —— %s" % (n, cfg["display_name"]))
        st = eng.load_state()
        done = 0
        for d in dates:
            if st.get("last_date") and pd.Timestamp(st["last_date"]) >= d:
                continue
            try:
                st, rec = eng.step(d, st=st)
                done += 1
                if args.verbose:
                    print("      %s  total=%.2f  cash=%.2f  pos=%d"
                          % (rec["date"], rec["total_asset"], rec["cash"], rec["n_positions"]))
            except Exception as e:
                print("      [ERROR] %s: %s" % (d.date(), e))
                traceback.print_exc()
                rc = 1
                break
        print("      推进 %d 天" % done)

    print("\n[3/3] 计算指标并出报告 ...")
    write_report(conf, g, names)
    return rc


def cmd_step(args):
    """推进到「行情数据已有的最新交易日」（定时任务用）。

    设计要点：
      1. 以行情库最新交易日为准，不读 datetime.now（避免设备时钟问题）；
      2. 窗口回看 max_gap_days 天，停机数日后可一次性补齐中间交易日（逐日循环）；
      3. 每个策略独立幂等：已推进到的日期自动跳过；
      4. 无新交易日 → exit 0（非交易日属正常，不告警）；
         数据源长期未更新 → exit 2（基础设施故障，必须告警）。
    """
    conf = load_config()
    g = conf["global"]
    names = [args.strategy] if args.strategy else \
        [k for k, v in conf["strategies"].items() if v.get("enabled", True)]

    # 用较大回看窗口，保证停机后能补齐
    end = pd.Timestamp.today().normalize()
    start = end - pd.Timedelta(days=int(g.get("max_gap_days", 30)))
    panel = QuotePanel(start, end)
    dates = panel.trading_dates(start, end)
    if not dates:
        print("[FAIL] 回看 %d 天内无任何行情数据" % int(g.get("max_gap_days", 30)))
        return 2
    latest = dates[-1]

    # 数据新鲜度检查：最新交易日距今超过 stale_days 视为数据源停更
    stale_days = int(g.get("stale_days", 5))
    gap = (end - latest).days
    if gap > stale_days:
        print("[ALERT] 行情最新交易日 %s 距今 %d 天 > %d 天阈值，疑似数据源停更"
              % (latest.date(), gap, stale_days))

    rc = 0
    for n in names:
        cfg = conf["strategies"][n]
        eng = build_engine(n, cfg, g, panel)
        st = eng.load_state()
        last = st.get("last_date")
        todo = [d for d in dates
                if not last or pd.Timestamp(last) < d]
        if not todo:
            print("[SKIP] %s 已推进到 %s（无新交易日）" % (n, last))
            continue
        print("[RUN] %s 待推进 %d 天：%s ~ %s"
              % (n, len(todo), todo[0].date(), todo[-1].date()))
        for d in todo:
            try:
                st, rec = eng.step(d, st=st)
                print("      %s total=%.2f cash=%.2f pos=%d"
                      % (rec["date"], rec["total_asset"], rec["cash"], rec["n_positions"]))
            except Exception as e:
                print("[ERROR] %s %s: %s" % (n, d.date(), e))
                traceback.print_exc()
                rc = 1
                break
    write_report(conf, g, names)
    return rc


def cmd_report(args):
    conf = load_config()
    names = [args.strategy] if args.strategy else \
        [k for k, v in conf["strategies"].items() if v.get("enabled", True)]
    write_report(conf, conf["global"], names)
    return 0


def write_report(conf, g, names):
    os.makedirs(RESULTS, exist_ok=True)
    lines = ["# Project_23 每日模拟盘 —— 净值报告", "",
             "> 生成：%s" % pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
             "> 口径：信号来自 P16 外部信号产物；T-1 盘后信号 → T 日开盘撮合（无前视）；实盘费率。",
             ""]
    all_metrics = {}
    for n in names:
        nav_path = os.path.join(RESULTS, "%s_nav.csv" % n)
        if not os.path.isfile(nav_path):
            lines.append("- **%s**: 无 NAV 数据" % n)
            continue
        nav = pd.read_csv(nav_path, encoding="utf-8-sig")
        m = compute_metrics(nav, g["start_capital"])
        all_metrics[n] = m
        lines.append("## %s —— %s" % (n, conf["strategies"][n]["display_name"]))
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("|---|---|")
        lines.append("| 区间 | %s ~ %s（%d 个交易日） |" % (m["start"], m["end"], m["n_days"]))
        lines.append("| 期初 → 期末 | %.2f → %.2f |" % (m["start_capital"], m["end_asset"]))
        lines.append("| 累计收益 | %+.2f%% |" % (m["total_return"] * 100))
        lines.append("| 年化（CAGR） | %s |" % ("%.2f%%" % (m["cagr"] * 100) if m["cagr"] is not None else "N/A"))
        lines.append("| 年化波动 | %s |" % ("%.2f%%" % (m["vol_annual"] * 100) if m["vol_annual"] else "N/A"))
        lines.append("| 夏普 | %s |" % (m["sharpe"] if m["sharpe"] is not None else "N/A"))
        lines.append("| 最大回撤 | %s |" % ("%.2f%%" % (m["max_drawdown"] * 100) if m["max_drawdown"] is not None else "N/A"))
        lines.append("")
        trade_path = os.path.join(RESULTS, "%s_trades.csv" % n)
        if os.path.isfile(trade_path):
            tr = pd.read_csv(trade_path, encoding="utf-8-sig")
            sells = tr[tr["side"] == "SELL"]
            lines.append("- 成交笔数：买 %d / 卖 %d" % (
                int((tr["side"] == "BUY").sum()), int((tr["side"] == "SELL").sum())))
            if len(sells):
                win = (sells["pnl"] > 0).mean()
                lines.append("- 卖出胜率：%.1f%%" % (win * 100))
                lines.append("- 出场原因分布：%s" % sells["reason"].value_counts().to_dict())
            lines.append("")

    out = os.path.join(RESULTS, "paper_report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(os.path.join(RESULTS, "paper_metrics.json"), "w", encoding="utf-8") as f:
        json.dump(all_metrics, f, ensure_ascii=False, indent=2)
    print("    报告: %s" % out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["replay", "step", "report"], default="replay")
    ap.add_argument("--strategy", default=None, help="g2 / v13，缺省全部")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.mode == "replay":
        return cmd_replay(args)
    if args.mode == "step":
        return cmd_step(args)
    return cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
