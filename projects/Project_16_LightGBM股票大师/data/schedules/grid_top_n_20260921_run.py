# coding: utf-8
"""P16 持仓只数 TOP 网格临时运行脚本（DE 任务书 2026-09-21，任务完成后可删）。

3 臂（V1.3 / G2 / 融合）× 7 档 TOP ∈ {2,3,4,5,6,8,10}，官方引擎
scan_rotate_cost_real.py 原样调用（不修改引擎任何文件）。

关键实现：
- TOP 只影响 simulate（build_per_day 不读 TOP），故每臂 build_per_day 一次、
  7 档 TOP 循环调用 simulate（ENG.TOP 直接改模块全局，与 env BT_TOP 等价）。
- 口径固定：可执行 open→open（exec_ok=True，审计 R1 基线）、红线 60、
  止损 -7%/止盈 +15%、持有期 N=10、滑点 0.1%/边、EXIT=fixed、区间默认
  2024-07-01 ~ 2026-08-14。
- 指标：n_trades/win_rate/max_dd/daily_excess 直接用引擎 stats()（与官方
  报告同口径）；annual_ret/sharpe 由 daily_ret 计算；turnover 定义为
  日均单边换手率 = n_trades / (TOP × 交易日数)（组合槽位日均替换比例）。
- 分年统计 / 出场原因 / 平均持有天数 / 最差日卖出数 仅打印到 stdout 供
  论证报告引用，CSV 只写任务书规定的 9 列。

输出：D:\\QuantLab\\projects\\Project_16_LightGBM股票大师\\results\\grid_top_n_20260921.csv
"""
import importlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(HERE))  # Project_16_LightGBM股票大师
os.chdir(PROJ)
sys.path.insert(0, PROJ)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

MODELS_DIR = "D:/QuantLab/models"
TOPS = [2, 3, 4, 5, 6, 8, 10]
N_HOLD = 10      # 持有期（BT_N_LIST=10）
SLIP = 0.001     # 单边滑点（BT_SLIPS=0.001）
OUT_CSV = os.path.join(PROJ, "results", "grid_top_n_20260921.csv")

ARMS = [
    ("V1.3", {
        "BT_PANEL": r"data\feature_panel_v3_enh2_n3_bt.parquet",
        "BT_MODEL": MODELS_DIR + r"\lgb_model_v3_enh.txt",
        "BT_META": r"data\features_v3_enh.json",
    }),
    ("G2", {
        "BT_PANEL": r"data\feature_panel_v3_enh2_n3_bt.parquet",
        "BT_MODEL": MODELS_DIR + r"\lgb_model_v3_g2_strong_real_20260825_1964t.txt",
        "BT_META": r"data\features_v3_g2_strong_real_20260825.json",
    }),
    ("融合", {
        "BT_PANEL": r"data\feature_panel_v3_enh2_n3_bt.parquet",
        "BT_MODEL": MODELS_DIR + r"\lgb_model_v3_g2_strong_real_20260825_1964t.txt",
        "BT_META": r"data\features_v3_g2_strong_real_20260825.json",
        "BT_ENSEMBLE": "0.5",
        "BT_MODEL2": MODELS_DIR + r"\lgb_model_v3_enh.txt",
        "BT_META2": r"data\features_v3_enh.json",
    }),
]
BASE_ENV = {
    "BT_THRESHOLD": "60", "BT_STOP": "-0.07", "BT_TP": "0.15",
    "BT_SLIPS": "0.001", "BT_N_LIST": "10", "BT_EXIT": "fixed",
    "BT_POSFILTER": "", "BT_PKOUT": "0",
}


def clear_bt_env():
    for k in list(os.environ.keys()):
        if k.startswith("BT_"):
            del os.environ[k]


def yearly_stats(dates, daily_ret, market_daily):
    """分年收益/超额（同一段 simulate 的 daily_ret，按自然年切分）。"""
    out = {}
    df_ret = list(daily_ret)
    cur_y = None
    buf_r, buf_m = [], []
    for i, d in enumerate(dates):
        y = d.year
        if cur_y is None:
            cur_y = y
        if y != cur_y:
            out[str(cur_y)] = _yr(buf_r, buf_m)
            cur_y, buf_r, buf_m = y, [], []
        buf_r.append(df_ret[i])
        buf_m.append(float(market_daily.iloc[i]))
    if cur_y is not None and buf_r:
        out[str(cur_y)] = _yr(buf_r, buf_m)
    return out


def _yr(buf_r, buf_m):
    nav = 1.0
    for r in buf_r:
        nav *= (1.0 + r)
    mnav = 1.0
    for m in buf_m:
        mnav *= (1.0 + m)
    n = len(buf_r)
    return {
        "days": n,
        "ret": nav - 1.0,
        "mkt": mnav - 1.0,
        "exc_daily": (sum(buf_r) / n - sum(buf_m) / n) if n else None,
    }


def main():
    rows = []
    t_start = time.time()
    for arm_name, arm_env in ARMS:
        clear_bt_env()
        os.environ.update(BASE_ENV)
        os.environ.update(arm_env)
        if "scan_rotate_cost_real" in sys.modules:
            ENG = importlib.reload(sys.modules["scan_rotate_cost_real"])
        else:
            ENG = importlib.import_module("scan_rotate_cost_real")
        print("=" * 70, flush=True)
        print(f"[ARM {arm_name}] env: PANEL={ENG.PANEL} | MODEL={ENG.MODEL} | META={ENG.META} "
              f"| ENS={ENG.ENS_W!r} | TH={ENG.THRESHOLD} STOP={ENG.STOP} TP={ENG.TP} "
              f"EXIT={ENG.EXIT_MODE} POSFILTER={ENG.BT_POSFILTER!r} PKOUT={ENG.PK_OUT}", flush=True)
        t0 = time.time()
        dates, per_day, market_daily, open_map = ENG.build_per_day()
        n_days = len(dates)
        print(f"[ARM {arm_name}] build_per_day done in {time.time()-t0:.0f}s | "
              f"测试期 {dates[0].date()} ~ {dates[-1].date()} | {n_days} 日 | "
              f"market_daily_mean={market_daily.mean():.5f}", flush=True)
        for top in TOPS:
            t1 = time.time()
            ENG.TOP = top
            ENG.EXIT_REASON_CNT.clear()
            trades, daily_ret, n_skip = ENG.simulate(
                dates, per_day, N_HOLD, SLIP, open_map, exec_ok=True)
            s = ENG.stats(trades, daily_ret, market_daily)
            # 年化与夏普（由 daily_ret 计算；与引擎同一天收益序列）
            nav = (1 + daily_ret).cumprod()
            total_ret = float(nav.iloc[-1]) - 1.0
            years = len(daily_ret) / 252.0
            annual = (1.0 + total_ret) ** (1.0 / years) - 1.0 if total_ret > -1 else -1.0
            std = float(daily_ret.std())
            sharpe = float(daily_ret.mean()) / std * (252.0 ** 0.5) if std > 0 else float("nan")
            # 换手率：日均单边换手率 = n_trades / (TOP × 交易日数)（槽位日均替换比例）
            turnover = s["n_trades"] / (top * len(daily_ret))
            rows.append({
                "arm": arm_name, "top": top,
                "annual_ret": round(annual, 6),
                "sharpe": round(sharpe, 4),
                "max_dd": round(s["max_drawdown"], 6),
                "turnover": round(turnover, 6),
                "win_rate": round(s["win_rate"], 6),
                "n_trades": int(s["n_trades"]),
                "daily_excess": round(s["daily_excess"], 6),
            })
            # 诊断（仅供论证报告，不进 CSV）
            hold_days = [t[1] - t[0] for t in trades]
            avg_hold = sum(hold_days) / len(hold_days) if hold_days else float("nan")
            sell_day_cnt = {}
            for t in trades:
                sell_day_cnt[t[1]] = sell_day_cnt.get(t[1], 0) + 1
            max_day_sells = max(sell_day_cnt.values()) if sell_day_cnt else 0
            diag = {
                "arm": arm_name, "top": top,
                "total_ret": total_ret, "final_nav": float(nav.iloc[-1]),
                "avg_hold_days": avg_hold,
                "max_day_sells": max_day_sells,
                "worst_day_slot_turnover": max_day_sells / top,
                "n_skip_exec": n_skip,
                "pl_ratio": s["profit_loss_ratio"],
                "exit_reasons": dict(ENG.EXIT_REASON_CNT),
                "sell_skip_down": ENG.SELL_SKIP_DOWN[0],
                "sell_delist": ENG.SELL_DELIST[0],
                "yearly": yearly_stats(dates, daily_ret, market_daily),
            }
            print(json.dumps(diag, ensure_ascii=False), flush=True)
            print(f"[ARM {arm_name}] TOP={top} done in {time.time()-t1:.0f}s | "
                  f"annual={annual:.2%} sharpe={sharpe:.2f} mdd={s['max_drawdown']:.1%} "
                  f"turnover={turnover:.2%} win={s['win_rate']:.1%} trades={s['n_trades']} "
                  f"exc={s['daily_excess']:.3%}", flush=True)
        del dates, per_day, market_daily, open_map
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    import pandas as pd
    pd.DataFrame(rows).to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print("=" * 70, flush=True)
    print(f"CSV saved: {OUT_CSV} | {len(rows)} rows | total {time.time()-t_start:.0f}s", flush=True)


if __name__ == "__main__":
    main()
