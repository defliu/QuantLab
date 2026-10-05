# -*- coding: utf-8 -*-
"""部署比例三档对照（T-20261003-005）：BT_DEPLOY_PCT = 0.95 / 0.98 / 1.00。

背景：实盘三策略 RESERVE_CASH_PCT=0.05（总仓 95%），用户问收紧到 98%/100% 的收益差异。
回测引擎 scan_rotate_cost_real.simulate 的部署比例已改为 BT_DEPLOY_PCT 可配（默认 0.95 不变）。
本脚本复用 jq_metrics 的官方三配置（G2 / 融合 / V1.4），每策略 build 一次、三档 simulate，
0.95 档应复现 jq_metrics 官方数字（正确性校验）。

用法：在项目根目录运行
  python research/deploy_pct_compare.py
产出：research/results/deploy_pct_compare_<date>.json + 控制台对照表
"""
import os
import sys
import json
import datetime
import importlib

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, "research"))

import pandas as pd  # noqa: E402
import jq_metrics as JM  # noqa: E402  # 复用 CONFIGS / metrics / SC

LEVELS = ["0.95", "0.98", "1.00"]
KEEP = ["total_return", "annual_return", "volatility", "max_drawdown",
        "sharpe", "calmar", "win_rate", "n_trades", "excess_annual"]


def run_level(dates, per_day, market_daily, open_map, cfg, level):
    os.environ["BT_DEPLOY_PCT"] = level
    trades, daily_ret, n_skip = JM.SC.simulate(dates, per_day, cfg["N"], 0.001,
                                               open_map, exec_ok=True)
    idx = pd.to_datetime(dates)
    strat = pd.Series(daily_ret.values, index=idx).fillna(0.0)
    bench = pd.Series(market_daily.values, index=idx).fillna(0.0)
    m = JM.metrics(trades, strat, bench)
    m["n_skip_exec"] = int(n_skip)
    return m


def main():
    only = sys.argv[1].split(",") if len(sys.argv) > 1 and sys.argv[1] else None  # 如 "V1.4"
    lvls = sys.argv[2].split(",") if len(sys.argv) > 2 and sys.argv[2] else LEVELS
    out = {"meta": {"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "levels": lvls, "note": "BT_DEPLOY_PCT 对照，其余配置=jq_metrics 官方口径；"
                    "单源结论（仅 astock），相对对照可用，绝对值定稿需备用源交叉验证"},
           "strategies": {}}
    for name, cfg in JM.CONFIGS.items():
        if only and name not in only:
            continue
        print(f"[{name}] {cfg['tag']} build_per_day ...", flush=True)
        for k in ("BT_MODEL2", "BT_META2", "BT_ENSEMBLE", "BT_POSFILTER",
                  "BT_TOP", "BT_START", "BT_END"):
            os.environ.pop(k, None)
        for k, v in cfg["env"].items():
            os.environ[k] = v
        os.environ["BT_TOP"] = str(cfg["TOP"])
        importlib.reload(JM.SC)
        dates, per_day, market_daily, open_map = JM.SC.build_per_day()
        res = {}
        for lvl in lvls:
            m = run_level(dates, per_day, market_daily, open_map, cfg, lvl)
            res[lvl] = {k: m[k] for k in KEEP}
            res[lvl]["n_skip_exec"] = m["n_skip_exec"]
            print(f"  deploy={lvl}: 总收益 {m['total_return']*100:7.2f}% | "
                  f"年化 {m['annual_return']*100:7.2f}% | 夏普 {m['sharpe']:.2f} | "
                  f"回撤 {m['max_drawdown']*100:6.1f}% | 胜率 {m['win_rate']*100:.1f}% | "
                  f"交易 {m['n_trades']} | 跳空剔除 {m['n_skip_exec']}", flush=True)
        out["strategies"][name] = {"tag": cfg["tag"], "levels": res}
    res_dir = os.path.join(PROJ, "research", "results")
    os.makedirs(res_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    suffix = "_" + "-".join(only) if only else ""
    out_path = os.path.join(res_dir, f"deploy_pct_compare_{stamp}{suffix}_{'-'.join(lvls)}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("已写入", out_path)


if __name__ == "__main__":
    main()
