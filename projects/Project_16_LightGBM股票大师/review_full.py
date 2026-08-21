# coding: utf-8
"""完整版复核脚本：TDX 实时数据 + 模型候选 → 完整版选股清单。

输入：
  1) deploy_predict.py 输出的候选 CSV（data/selections/YYYYMMDD_model_topK.csv）
     提供 ts_code / model_prob / SC_F1 / SC_F4 / SC_F6（面板代理分，仅用 F1/F4/F6）
  2) TDX 实时复核数据 data/tdx_review.json（由 AI 通过 TDX MCP 采集后写入）：
     {
       "date": "20260819",
       "stocks": [
         {"ts_code":"603969.SH", "main_net_inflow":-5673766, "liangbi":0.89,
          "industry_pct":-7.0, "quote_pct":-3.66,
          "pe_ttm": 18.1, "turnover": 2.5,
          "catalyst_score":10, "catalyst_note":"半年报净利+40.35%确证利好"}
       ]
     }
     （pe_ttm=实时市盈率，turnover=实时换手率，供 F6 估值打分；缺省则 F6 回退面板分）

逻辑：
  - F2(资金) = f(实时主力净额, 量比)   （Project_15 规则）
  - F3(催化) = 实时 catalyst_score（AI 结合新闻/公告给出 0-10）
  - F5(板块) = f(实时所属行业当日涨幅 HYZAF)
  - F1/F4 沿用 deploy 面板代理分；F6 优先用实时 PE（PE<0 或 >100 硬性扣 1 分），无实时则回退面板 SC_F6
  - 总分 = F1*25%+F2*20%+F3*20%+F4*15%+F5*10%+F6*10%（×10 得百分制）
  - 输出完整版清单 Markdown + CSV（data/selections/）

用法：
  python review_full.py --candidates data/selections/20260814_model_top5.csv
"""
import argparse
import json
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SELECT_DIR = os.path.join(HERE, "data", "selections")
DEFAULT_REVIEW = os.path.join(HERE, "data", "tdx_review.json")

SC_WEIGHTS = {"F1": 0.25, "F2": 0.20, "F3": 0.20, "F4": 0.15, "F5": 0.10, "F6": 0.10}


def score_f2(net_inflow, liangbi):
    """F2 资金认可度（Project_15 规则，实时主力净额 + 量比）。"""
    if np.isnan(net_inflow):
        return 5.0
    if net_inflow > 5e7 and (liangbi > 2.5 if not np.isnan(liangbi) else False):
        return 10.0
    if net_inflow > 1e7 and (liangbi > 1.5 if not np.isnan(liangbi) else False):
        return 8.0
    if net_inflow > 0:
        return 6.0
    if not np.isnan(liangbi) and liangbi >= 2:
        return 5.0  # 净流出但放量 = 分歧
    if net_inflow <= -1e8:
        return 1.0  # 净流出超1亿
    return 2.0  # 净流出缩量


def score_f5(industry_pct):
    """F5 板块β联动（所属行业当日涨幅）。"""
    if np.isnan(industry_pct):
        return 5.0
    if industry_pct > 3:
        return 10.0
    if industry_pct > 1.5:
        return 8.0
    if industry_pct > 0:
        return 5.0
    if industry_pct > -1:
        return 4.0
    return 2.0


def score_f6(pe_ttm, turnover):
    """F6 估值/流动性：用实时 PE-TTM + 实时换手（替代面板 asof PE，修复估值口径偏差）。

    审计修复（2026-08-21）：面板 asof PE 与实时差异大（如朗特实时 PE 789 但面板口径给 7 分），
    导致"评分高但估值畸高"的票入围。改用实时 PE 打分，PE<0 或 PE>100 硬性扣 1 分。
    """
    if np.isnan(pe_ttm):
        return 5.0
    if pe_ttm < 0 or pe_ttm > 100:
        return 1.0   # 亏损或估值畸高：硬性扣分
    if 10 <= pe_ttm <= 30 and (1 <= turnover <= 8 if not np.isnan(turnover) else True):
        return 10.0
    if 5 <= pe_ttm <= 50:
        return 7.0
    return 4.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True, help="deploy_predict 输出的候选 CSV 路径")
    ap.add_argument("--review", default=DEFAULT_REVIEW, help="TDX 实时复核 JSON 路径")
    ap.add_argument("--threshold", type=float, default=58.0, help="评分卡红线")
    args = ap.parse_args()

    cand = pd.read_csv(args.candidates, dtype={"ts_code": str})
    cand["ts_code"] = cand["ts_code"].str.strip()
    review = json.load(open(args.review, encoding="utf-8"))
    rdate = str(review.get("date", "unknown"))
    stocks = {s["ts_code"]: s for s in review["stocks"]}

    print(f"[1/3] 候选 {len(cand)} 只 + TDX 复核 {rdate}")
    rows = []
    for _, r in cand.iterrows():
        code = r["ts_code"]
        s = stocks.get(code)
        if s is None:
            print(f"    !! 候选 {code} 无 TDX 复核数据，跳过")
            continue
        f2 = score_f2(s.get("main_net_inflow", np.nan), s.get("liangbi", np.nan))
        f3 = float(s.get("catalyst_score", 0.0))
        f5 = score_f5(s.get("industry_pct", np.nan))
        pe_ttm = s.get("pe_ttm", np.nan)
        turnover = s.get("turnover", np.nan)
        f1 = float(r["SC_F1"])
        f4 = float(r["SC_F4"])
        f6 = score_f6(pe_ttm, turnover) if not np.isnan(pe_ttm) else float(r["SC_F6"])
        total = sum(SC_WEIGHTS[k] * v for k, v in {"F1": f1, "F2": f2, "F3": f3, "F4": f4, "F5": f5, "F6": f6}.items()) * 10.0
        rows.append({
            "ts_code": code, "name": s.get("name", ""),
            "quote_pct": s.get("quote_pct", np.nan), "model_prob": float(r["model_prob"]),
            "F1": f1, "F2": f2, "F3": f3, "F4": f4, "F5": f5, "F6": f6,
            "total": round(total, 1),
            "pe_ttm": pe_ttm,
            "main_net_inflow": s.get("main_net_inflow", np.nan),
            "liangbi": s.get("liangbi", np.nan),
            "industry_pct": s.get("industry_pct", np.nan),
            "catalyst_note": s.get("catalyst_note", ""),
        })

    out = pd.DataFrame(rows).sort_values("total", ascending=False).reset_index(drop=True)
    print("[2/3] 完整版评分：")
    show = out[["ts_code", "name", "quote_pct", "model_prob", "F1", "F2", "F3", "F4", "F5", "F6", "total"]].copy()
    show["quote_pct"] = show["quote_pct"].round(2)
    show["model_prob"] = show["model_prob"].round(3)
    show["pass"] = np.where(out["total"] >= args.threshold, "✅", "")
    print(show.to_string(index=False))

    print("[3/3] 输出完整版清单 ...")
    date_str = rdate
    os.makedirs(SELECT_DIR, exist_ok=True)
    csv_path = os.path.join(SELECT_DIR, f"{date_str}_selection_full.csv")
    md_path = os.path.join(SELECT_DIR, f"{date_str}_selection_full.md")
    out.to_csv(csv_path, index=False, encoding="utf-8-sig")

    lines = [
        f"# 双轨选股 · 完整版清单（TDX 实时复核）",
        "",
        f"> 生成时间：{rdate} 盘后  |  数据口径：模型分基于面板特征；F2/F3/F5 为 TDX 实时数据；F1/F4/F6 为面板代理分",
        f"> 评分卡红线：{args.threshold:.0f} 分",
        "",
        "| 排名 | 代码 | 名称 | 当日涨跌 | F1位置 | F2资金 | F3催化 | F4技术 | F5板块 | F6估值 | 总分 | 模型分 | 结论 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, (_, r) in enumerate(out.iterrows(), 1):
        pct = f"{r['quote_pct']:.2f}%" if not np.isnan(r["quote_pct"]) else "—"
        p = "✅" if r["total"] >= args.threshold else "⚠️"
        lines.append(
            f"| {i} | {r['ts_code']} | {r['name']} | {pct} | {r['F1']:.0f} | {r['F2']:.0f} | {r['F3']:.0f} "
            f"| {r['F4']:.0f} | {r['F5']:.0f} | {r['F6']:.0f} | {r['total']:.1f} | {r['model_prob']:.3f} | {p} |"
        )
    lines += ["", "## 实时复核明细"]
    for i, (_, r) in enumerate(out.iterrows(), 1):
        net = f"{r['main_net_inflow']/1e4:.0f} 万" if not np.isnan(r["main_net_inflow"]) else "—"
        ind = f"{r['industry_pct']:.2f}%" if not np.isnan(r["industry_pct"]) else "—"
        lb = f"{r['liangbi']:.2f}" if not np.isnan(r["liangbi"]) else "—"
        lines += [
            f"### {i}. {r['ts_code']} {r['name']}（总分 {r['total']:.1f}）",
            f"- F2 资金：今日主力净额 **{net}**，量比 {lb} → {r['F2']:.0f} 分",
            f"- F3 催化：{r['catalyst_note'] or '—'} → {r['F3']:.0f} 分",
            f"- F5 板块：所属行业当日 **{ind}** → {r['F5']:.0f} 分",
            "",
        ]
    lines += [
        "---",
        "> ⚠️ 免责声明：本清单为模型 + TDX 实时数据复核的研究信号，不构成投资建议。",
    ]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("    CSV:", csv_path)
    print("    MD :", md_path)


if __name__ == "__main__":
    main()
