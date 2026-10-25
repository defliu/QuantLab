# -*- coding: utf-8 -*-
"""G2 持有期 N15 vs N10 回测 OOS 对照（2026-10-25，opencode，只读）。

同 harness（scan_rotate_cost_real.py）、同参数（live 模型/TOP2/deploy0.98/SC60/
live_trail/slip0.1%），唯一变量 = simulate() 持有期 10 vs 15，按半年分段跑 5 年。
产出：results/G2持有期N15vsN10_5年回测_20261025.md / .json
用法：python research/g2_hold_n10n15_backtest_20261025.py
"""
import importlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ)
sys.path.insert(0, HERE)
os.chdir(PROJ)

import pandas as pd

import data_config as DC

DATA = DC.DATA_DIR
OUT_MD = os.path.join(PROJ, "results", "G2持有期N15vsN10_5年回测_20261025.md")
OUT_JSON = os.path.join(PROJ, "results", "G2持有期N15vsN10_5年回测_20261025.json")

with open(os.path.join(DATA, "g2_live_model.json"), encoding="utf-8") as f:
    LIVE = json.load(f)
MODEL, META = LIVE["model_path"], LIVE["meta_path"]
PANEL = os.path.join(DATA, "feature_panel_v3_enh2_n3_bt.parquet")

SEGMENTS = [
    ("2021H2", "2021-07-01", "2021-12-31"),
    ("2022H1", "2022-01-01", "2022-06-30"),
    ("2022H2", "2022-07-01", "2022-12-31"),
    ("2023H1", "2023-01-01", "2023-06-30"),
    ("2023H2", "2023-07-01", "2023-12-31"),
    ("2024H1", "2024-01-01", "2024-06-30"),
    ("2024H2", "2024-07-01", "2024-12-31"),
    ("2025H1", "2025-01-01", "2025-06-30"),
    ("2025H2", "2025-07-01", "2025-12-31"),
    ("2026H1", "2026-01-01", "2026-06-30"),
    ("2026Q3+", "2026-07-01", "2026-10-23"),
]
HOLDS = [10, 15]


def _clear_env():
    for k in ("BT_MODEL2", "BT_META2", "BT_ENSEMBLE", "BT_POSFILTER", "BT_TOP",
              "BT_START", "BT_END", "BT_PANEL", "BT_MODEL", "BT_META",
              "BT_THRESHOLD", "BT_EXIT", "BT_SLIPS", "BT_N_LIST", "BT_DEPLOY_PCT"):
        os.environ.pop(k, None)


def run_seg(hold, start, end):
    _clear_env()
    os.environ.update({
        "BT_PANEL": PANEL, "BT_MODEL": MODEL, "BT_META": META,
        "BT_EXIT": "live_trail", "BT_SLIPS": "0.001", "BT_N_LIST": "1",
        "BT_START": start, "BT_END": end, "BT_TOP": "2",
        "BT_DEPLOY_PCT": "0.98", "BT_THRESHOLD": "60.0",
    })
    import scan_rotate_cost_real as SC
    importlib.reload(SC)
    dates, per_day, market_daily, open_map = SC.build_per_day()
    if not dates:
        return None
    trades, daily_ret, _ = SC.simulate(dates, per_day, hold, 0.001, open_map, exec_ok=True)
    strat = pd.Series(daily_ret.values, index=pd.to_datetime(dates)).fillna(0.0)
    if not len(strat):
        return None
    nav = (1 + strat).cumprod()
    mdd = float((nav / nav.cummax() - 1).min())
    rets = [t[2] for t in trades]
    return {"days": len(strat), "trades": len(trades),
            "total_return": round(float(nav.iloc[-1] - 1), 4),
            "max_drawdown": round(mdd, 4),
            "win_rate": round(float((pd.Series(rets) > 0).mean()), 4) if rets else None}


def main():
    if "--from-json" in sys.argv and os.path.exists(OUT_JSON):
        rows = json.load(open(OUT_JSON, encoding="utf-8"))["rows"]
        print("[--from-json] 复用已有回测结果，仅重建报告")
        write_md(rows)
        return
    rows = []
    for seg, start, end in SEGMENTS:
        line = {"seg": seg, "start": start, "end": end}
        for h in HOLDS:
            try:
                print(">>> %s N%d ..." % (seg, h), end="", flush=True)
                t0 = time.time()
                r = run_seg(h, start, end)
                line["N%d" % h] = r
                print(" %s (%.0fs)" % (
                    ("%+.2f%%" % (r["total_return"] * 100)) if r else "无数据",
                    time.time() - t0))
            except Exception as e:
                line["N%d" % h] = None
                print(" 异常 %r" % (e,))
        rows.append(line)

    json.dump({"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": rows},
              open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    write_md(rows)


def write_md(rows):
    lines = [
        "# G2 持有期 N15 vs N10 · 5 年分段回测对照",
        "",
        "> 生成 %s | live 模型 `%s`" % (time.strftime("%Y-%m-%d %H:%M"), os.path.basename(MODEL)),
        "> 同 harness（scan_rotate_cost_real.py）同参数（TOP2/deploy0.98/SC60/live_trail/slip0.1%），唯一变量=持有期",
        "> 段内为总收益（段短不年化）；回撤按段内净值",
        "",
        "> ⚠️ **口径更正（2026-10-25 审计）**：本表**不是纯 OOS**。live 模型训练切分 = "
        "train 2020-01~2023-06 / valid 2023-07~2024-06 / test 2024-07~"
        "（`train_g2.py:214-217`），故 2021H2~2024H1 共 6 段属样本内/验证内，仅 2024H2 起 5 段是 test。",
        "> 另：面板 `feature_panel_v3_enh2_n3_bt.parquet` 截止 **2026-08-14**，末段 `2026Q3+` 实际仅覆盖 07-01~08-14（32 个交易日），非至 10-23。",
        "> 且 N10/N15 因周转节奏不同会选中**不同的票**（非「同票不同持有期」配对），段差含选股路径分叉成分。",
        "",
        "| 分段 | 窗口类型 | N10 总收益 | N10 回撤 | N10 胜率 | N15 总收益 | N15 回撤 | N15 胜率 | 差（N10−N15） |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    win = {"2021H2": "train", "2022H1": "train", "2022H2": "train", "2023H1": "train",
           "2023H2": "valid", "2024H1": "valid",
           "2024H2": "test", "2025H1": "test", "2025H2": "test",
           "2026H1": "test", "2026Q3+": "test(仅到08-14)"}
    n10_win = n15_win = 0
    for r in rows:
        a, b = r.get("N10"), r.get("N15")
        if a and b:
            diff = (a["total_return"] - b["total_return"]) * 100
            if a["total_return"] > b["total_return"]:
                n10_win += 1
            else:
                n15_win += 1
            lines.append("| %s | %s | %+.2f%% | %.2f%% | %.0f%% | %+.2f%% | %.2f%% | %.0f%% | **%+.2fpp** |" % (
                r["seg"], win.get(r["seg"], ""), a["total_return"] * 100, a["max_drawdown"] * 100,
                (a["win_rate"] or 0) * 100, b["total_return"] * 100,
                b["max_drawdown"] * 100, (b["win_rate"] or 0) * 100, diff))
        else:
            lines.append("| %s | %s | %s | | | %s | | | |" % (
                r["seg"], win.get(r["seg"], ""),
                ("%+.2f%%" % (a["total_return"] * 100)) if a else "无数据",
                ("%+.2f%%" % (b["total_return"] * 100)) if b else "无数据"))
    lines += ["", "N10 段胜 %d / N15 段胜 %d（共 %d 段）" % (n10_win, n15_win, n10_win + n15_win), ""]

    # 按训练切分聚合（审计新增）：只有 test 段才算样本外
    import numpy as _np
    seg = {r["seg"]: r for r in rows}

    def _grp(name, segs):
        d = [(seg[s]["N10"]["total_return"] - seg[s]["N15"]["total_return"]) * 100
             for s in segs if seg.get(s) and seg[s].get("N10") and seg[s].get("N15")]
        if not d:
            return
        mean = float(_np.mean(d))
        t = float("nan")
        if len(d) >= 3 and _np.std(d, ddof=1) > 0:
            t = mean / (_np.std(d, ddof=1) / _np.sqrt(len(d)))
        lines.append("- %s：%d 段，均值 %+.2fpp（N10−N15，负=利好 N15），t=%s"
                     % (name, len(d), mean, ("%+.2f" % t) if t == t else "n/a"))

    lines.append("### 按训练切分聚合")
    _grp("train 2021H2~2023H1", ["2021H2", "2022H1", "2022H2", "2023H1"])
    _grp("valid 2023H2~2024H1", ["2023H2", "2024H1"])
    _grp("test 2024H2~2026Q3+", ["2024H2", "2025H1", "2025H2", "2026H1", "2026Q3+"])
    _grp("test 剔除 2026H1", ["2024H2", "2025H1", "2025H2", "2026Q3+"])
    _grp("全 11 段", [r["seg"] for r in rows])
    lines.append("")
    lines.append("> 判读：任何分窗的配对 t 均未过 |t|>2；且 test 段本身是模型/参数选型（110.08% 锚点、TOP2/SC60/deploy0.98/live_trail）的依据，属「被使用过的 test 集」，非干净 OOS。")
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(lines))
    print("[输出]", OUT_MD)


if __name__ == "__main__":
    main()
