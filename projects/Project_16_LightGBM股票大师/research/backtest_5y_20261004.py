# -*- coding: utf-8 -*-
"""近 5 年回测（2021-08-16 ~ 2026-08-14）：三策略官方配置，deploy 0.95/0.98 双档。

⚠️ 口径警示（T-20261004-001）：模型训练切分 = train 2020-01~2023-06 / valid 2023-07~2024-06
   / test 2024-07+（train_g2.py L13 与 train_optuna.py L148-150 全项目统一约定）。
   本窗口 2021-08 起覆盖训练样本与验证样本，结果按 IS/VALID/OOS 三段分段报告，
   全窗口合计数字仅供参考，不得当作样本外成绩引用。官方 OOS 口径 = 2024-07-01 起
   （见 jq_metrics / deploy_pct_compare_20261003）。

用法（项目根目录）：
  python research/backtest_5y_20261004.py "G2,融合" "0.95,0.98"
  python research/backtest_5y_20261004.py "V1.4" "0.95,0.98"
产出：research/results/backtest_5y_<stamp>_<strategy>.json（全指标 + 逐日净值 + 分年 + 分段）
"""
import os
import sys
import json
import datetime
import importlib

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, "research"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import jq_metrics as JM  # noqa: E402

BT_START = "2021-08-16"
BT_END = "2026-08-14"
SEGMENTS = [
    ("IS_train", "2021-08-16", "2023-06-30"),      # 训练样本（成绩虚高，仅供对照）
    ("VALID", "2023-07-01", "2024-06-30"),          # 调参/早停窗口（半样本内）
    ("OOS", "2024-07-01", "2026-08-14"),            # 样本外（官方口径）
]
RF_D = 0.02 / 252.0


def seg_metrics(strat, bench, s, e, trades, dates):
    m = (strat.index >= s) & (strat.index <= e)
    sub, b = strat[m], bench[m]
    n = len(sub)
    if n == 0:
        return None
    nav = (1 + sub).cumprod()
    total = float(nav.iloc[-1] - 1)
    ann = float((1 + total) ** (252 / n) - 1) if total > -1 else -1.0
    mdd = float((nav / nav.cummax() - 1).min())
    sharpe = float((sub.mean() - RF_D) / sub.std(ddof=1) * np.sqrt(252)) if sub.std() > 0 else None
    bnav = (1 + b).cumprod()
    # 分段交易数：buy_i 落在段内
    idx = list(strat.index)
    n_tr = sum(1 for t in trades if pd.Timestamp(s) <= idx[t[0]] <= pd.Timestamp(e))
    return {"n_days": n, "n_trades": n_tr, "total_return": total, "annual_return": ann,
            "max_drawdown": mdd, "sharpe": sharpe,
            "bench_total": float(bnav.iloc[-1] - 1)}


def run_level(dates, per_day, market_daily, open_map, cfg, level):
    os.environ["BT_DEPLOY_PCT"] = level
    trades, daily_ret, n_skip = JM.SC.simulate(dates, per_day, cfg["N"], 0.001,
                                               open_map, exec_ok=True)
    idx = pd.to_datetime(dates)
    strat = pd.Series(daily_ret.values, index=idx).fillna(0.0)
    bench = pd.Series(market_daily.values, index=idx).fillna(0.0)
    m = JM.metrics(trades, strat, bench)
    m["n_skip_exec"] = int(n_skip)
    # 分年收益（从 monthly 复利合成）
    yearly = {}
    for y in sorted({k[:4] for k in m["monthly"]}):
        rs = [v for k, v in m["monthly"].items() if k.startswith(y)]
        yearly[y] = float(np.prod([1 + r for r in rs]) - 1) if rs else None
    m["yearly_return"] = yearly
    # IS/VALID/OOS 分段
    m["segments"] = {name: seg_metrics(strat, bench, s, e, trades, dates)
                     for name, s, e in SEGMENTS}
    return m


def main():
    only = sys.argv[1].split(",") if len(sys.argv) > 1 and sys.argv[1] else None
    lvls = sys.argv[2].split(",") if len(sys.argv) > 2 and sys.argv[2] else ["0.95", "0.98"]
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    for name, cfg in JM.CONFIGS.items():
        if only and name not in only:
            continue
        print(f"[{name}] {cfg['tag']}  window {BT_START}~{BT_END}  build_per_day ...", flush=True)
        for k in ("BT_MODEL2", "BT_META2", "BT_ENSEMBLE", "BT_POSFILTER",
                  "BT_TOP", "BT_START", "BT_END"):
            os.environ.pop(k, None)
        for k, v in cfg["env"].items():
            os.environ[k] = v
        os.environ["BT_TOP"] = str(cfg["TOP"])
        os.environ["BT_START"] = BT_START
        os.environ["BT_END"] = BT_END
        importlib.reload(JM.SC)
        dates, per_day, market_daily, open_map = JM.SC.build_per_day()
        res = {}
        for lvl in lvls:
            m = run_level(dates, per_day, market_daily, open_map, cfg, lvl)
            res[lvl] = m
            s_oos = m["segments"]["OOS"]
            print(f"  deploy={lvl}: 全窗 {m['total_return']*100:8.1f}% / 年化 {m['annual_return']*100:6.1f}%"
                  f" | 夏普 {m['sharpe']:.2f} | 回撤 {m['max_drawdown']*100:6.1f}%"
                  f" | 交易 {m['n_trades']} | OOS段 年化 {s_oos['annual_return']*100:6.1f}%"
                  f" / 回撤 {s_oos['max_drawdown']*100:5.1f}% / 交易 {s_oos['n_trades']}", flush=True)
        out = {"meta": {"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "window": f"{BT_START} ~ {BT_END}", "tag": cfg["tag"],
                        "deploy_levels": lvls,
                        "split_convention": "train 2020-01~2023-06 / valid 2023-07~2024-06 / test 2024-07+",
                        "warning": "2021-08~2024-06 覆盖训练/验证样本，IS/VALID 段成绩虚高，"
                                   "样本外结论只看 OOS 段；单源（仅 astock）"},
               "levels": res}
        res_dir = os.path.join(PROJ, "research", "results")
        os.makedirs(res_dir, exist_ok=True)
        out_path = os.path.join(res_dir, f"backtest_5y_{stamp}_{name}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("已写入", out_path, flush=True)


if __name__ == "__main__":
    main()
