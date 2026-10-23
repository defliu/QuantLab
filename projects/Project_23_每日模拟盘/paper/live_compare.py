# -*- coding: utf-8 -*-
"""纸面 vs 实盘 对照 —— 用同一批信号，量化「执行损耗」。

核心问题：模拟盘（纸面）按理想规则成交，实盘会遇到
  未成交 / 部分成交 / 滑点 / 手数取整 / 资金不足 / 人工干预
本模块把两边对齐后，拆出差异来源。

实盘口径：
  - V1.3 : data/qmt_trade_log.csv（138 笔，含 63 笔 price=0 的历史缺口）
  - G2   : D:/QMT_POOL/g2_bridge/cmd/orders_*_done_*.json + 资金池 note
纸面口径：
  - Project_23 results/{strategy}_trades.csv

输出：results/live_vs_paper.md
"""
import glob
import json
import os

import numpy as np
import pandas as pd

from .live_nav import compare_nav

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../Project_23_每日模拟盘/paper
PROJ23 = os.path.dirname(_HERE)                             # .../Project_23_每日模拟盘
ROOT = os.path.dirname(os.path.dirname(PROJ23))             # d:/QuantLab
RESULTS = os.path.join(PROJ23, "results")
P16 = os.path.join(ROOT, "projects", "Project_16_LightGBM股票大师")

V13_TRADE_LOG = os.path.join(P16, "data", "qmt_trade_log.csv")
V13_CAPITAL = os.path.join(P16, "data", "strategy_capital.json")
G2_BRIDGE = "D:/QMT_POOL/g2_bridge"
G2_CAPITAL = os.path.join(G2_BRIDGE, "g2_strategy_capital.json")


def load_v13_live():
    """读 V1.3 实盘成交。返回 DataFrame，含 time/code/side/vol/price/score/reason/is_valid。"""
    if not os.path.isfile(V13_TRADE_LOG):
        return None
    t = pd.read_csv(V13_TRADE_LOG, encoding="utf-8-sig", dtype={"code": str})
    t["time"] = pd.to_datetime(t["time"])
    t["date"] = t["time"].dt.strftime("%Y-%m-%d")
    # price=0 是历史账本缺口（2026-08-20~08-24），不能当真实成交价
    t["is_valid"] = t["price"].astype(float) > 0
    # score 列双语义：数值=买入评分，字符串=卖出归因
    t["reason"] = t["score"].astype(str).where(t["score"].astype(str).str.isalpha(), "")
    return t


def load_v13_capital():
    if not os.path.isfile(V13_CAPITAL):
        return None
    with open(V13_CAPITAL, encoding="utf-8") as f:
        return json.load(f)


def load_g2_live():
    """读 G2 桥的下单回执（cmd/orders_*_done_*.json）。

    实际结构：{"date","generated_at","seq","orders":[{"action","code","vol","price","reason",...}]}
    action = BUY / SELL；reason 是卖出归因（中文描述）。
    """
    rows = []
    for fp in glob.glob(os.path.join(G2_BRIDGE, "cmd", "orders_*_done_*.json")):
        try:
            with open(fp, encoding="utf-8-sig") as f:
                d = json.load(f)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        day = str(d.get("date") or "")
        for it in (d.get("orders") or []):
            if not isinstance(it, dict):
                continue
            rows.append({
                "file": os.path.basename(fp),
                "date": day,
                "time": str(d.get("generated_at") or ""),
                "code": it.get("code") or "",
                "side": str(it.get("action") or "").upper(),
                "vol": it.get("vol"),
                "price": it.get("price"),
                "reason": it.get("reason") or "",
                "order_id": it.get("strategy_order_id") or "",
            })
    df = pd.DataFrame(rows)
    if len(df):
        # 同一 strategy_order_id 可能因重试多次落盘，去重保留最后
        df = df.drop_duplicates(subset=["order_id"], keep="last").reset_index(drop=True)
        df["is_valid"] = df["price"].astype(float) > 0
    return df


def load_g2_capital():
    if not os.path.isfile(G2_CAPITAL):
        return None
    with open(G2_CAPITAL, encoding="utf-8") as f:
        return json.load(f)


def load_paper(name):
    fp = os.path.join(RESULTS, "%s_trades.csv" % name)
    if not os.path.isfile(fp):
        return None
    return pd.read_csv(fp, encoding="utf-8-sig")


def compare(name, live_df, paper_df, start_cap):
    """对比实盘与纸面的成交结构。返回 dict。"""
    out = {"strategy": name, "start_capital": start_cap}

    if live_df is None or len(live_df) == 0:
        out["live_available"] = False
        return out
    out["live_available"] = True
    out["live_n"] = int(len(live_df))
    out["live_buy"] = int((live_df["side"] == "BUY").sum())
    out["live_sell"] = int((live_df["side"] == "SELL").sum())

    if "is_valid" in live_df.columns:
        valid = live_df[live_df["is_valid"]]
        out["live_invalid_px"] = int((~live_df["is_valid"]).sum())
        out["live_valid_n"] = int(len(valid))
    else:
        valid = live_df
        out["live_invalid_px"] = 0
        out["live_valid_n"] = int(len(live_df))

    if paper_df is not None and len(paper_df):
        out["paper_available"] = True
        out["paper_n"] = int(len(paper_df))
        out["paper_buy"] = int((paper_df["side"] == "BUY").sum())
        out["paper_sell"] = int((paper_df["side"] == "SELL").sum())

        # 重合度分两个口径统计：
        #   全口径（买卖都算）：反映两边的股票池交集
        #   买入口径（只比 BUY）：反映选股信号是否一致，是核心指标
        l_all = set(live_df["code"].astype(str))
        p_all = set(paper_df["code"].astype(str))
        out["code_overlap"] = len(l_all & p_all)
        out["code_live_only"] = len(l_all - p_all)
        out["code_paper_only"] = len(p_all - l_all)

        lb = set(live_df[live_df["side"].astype(str).str.upper() == "BUY"]["code"].astype(str))
        pb = set(paper_df[paper_df["side"].astype(str).str.upper() == "BUY"]["code"].astype(str))
        out["buy_overlap"] = len(lb & pb)
        out["buy_live_only"] = len(lb - pb)
        out["buy_paper_only"] = len(pb - lb)
        out["buy_overlap_pct"] = round(len(lb & pb) / max(len(lb | pb), 1) * 100, 1)
    else:
        out["paper_available"] = False
    return out


def write_report(rows, nav_rows=None):
    os.makedirs(RESULTS, exist_ok=True)
    L = ["# 纸面 vs 实盘 对照报告", "",
         "> 生成：%s" % pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
         "> 纸面 = Project_23 按理想规则撮合；实盘 = 券商真实成交。",
         ""]

    # ---- V1.3 ----
    cap = load_v13_capital()
    if cap:
        L += ["## V1.3 实盘资金（真实来源：strategy_capital.json）", "",
              "| 项 | 值 |", "|---|---|",
              "| 账号 | %s |" % cap.get("account_id"),
              "| 起始资金 | %.2f |" % cap.get("start", 0),
              "| 已实现盈亏 | %+.2f |" % cap.get("realized", 0),
              "| 浮动盈亏 | %+.2f |" % cap.get("float_pnl", 0),
              "| 当前资金池 | %.2f |" % cap.get("capital", 0),
              "| 更新于 | %s |" % cap.get("updated_at"), ""]
        start = float(cap.get("start", 100000))
        cur = float(cap.get("capital", 0))
        L.append("**实盘累计收益：%+.2f%%**" % ((cur / start - 1) * 100))
        L.append("")

    # ---- G2 ----
    gcap = load_g2_capital()
    if gcap:
        L += ["## G2 实盘资金（真实来源：g2_strategy_capital.json）", "",
              "| 项 | 值 |", "|---|---|",
              "| 账号 | %s |" % gcap.get("account_id"),
              "| 当前资金池 | %.2f |" % gcap.get("capital", 0),
              "| 备注 | %s |" % gcap.get("note"),
              "| 更新于 | %s |" % gcap.get("updated_at"), ""]
        gstart = 100000.0
        L.append("**实盘累计收益：%+.2f%%**（起始 10 万）" % ((gcap.get("capital", 0) / gstart - 1) * 100))
        L.append("")

    # ---- 对照表 ----
    L += ["## 纸面 vs 实盘 成交结构对照", "",
          "| 策略 | 实盘笔数 | 实盘有效 | 实盘无效价 | 纸面笔数 | 代码重合(全) | 买入重合 | 买入重合率 |",
          "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if not r.get("live_available"):
            L.append("| %s | 无实盘数据 | - | - | - | - | - | - |" % r["strategy"])
            continue
        L.append("| %s | %d | %d | %d | %s | %s | %s | %s |" % (
            r["strategy"], r["live_n"], r.get("live_valid_n", 0), r.get("live_invalid_px", 0),
            r.get("paper_n", "-"), r.get("code_overlap", "-"),
            r.get("buy_overlap", "-"),
            ("%.1f%%" % r["buy_overlap_pct"]) if r.get("buy_overlap_pct") is not None else "-"))
    L.append("")
    L += ["> **买入重合率** = |实盘买入∩纸面买入| / |实盘买入∪纸面买入|，是选股信号一致性的核心指标。",
          "> 全口径重合会把卖出股票算进去，数值偏高，不作为判断依据。", ""]

    # ---- NAV 曲线对比（实盘重建）----
    if nav_rows:
        infos = [x for x in nav_rows if "_detail" not in x and "_live_only" not in x]
        details = {x["_strategy"]: x["_detail"] for x in nav_rows if "_detail" in x}
        if infos:
            L += ["## 实盘 NAV 重建 与 纸面 NAV 逐日对比", "",
                  "| 策略 | 精度 | 区间 | 纸面 | 实盘 | 跟踪差 | 最大偏离 | 平均偏离 | 相关性 |",
                  "|---|---|---|---|---|---|---|---|---|"]
            for i in infos:
                corr = i.get("corr")
                L.append("| %s | %s | %s~%s | %+.2f%% | %+.2f%% | **%+.2f pp** | %.2f | %.2f | %s |" % (
                    i["strategy"], i.get("precision", "-"), i["start"][5:], i["end"][5:],
                    i["paper_ret"], i["live_ret"], i["tracking_diff_pp"],
                    i["max_abs_diff_pp"], i["mean_abs_diff_pp"],
                    ("%.3f" % corr) if corr is not None and not pd.isna(corr) else "N/A"))
            L.append("")
            L += ["> **相关性**是判断纸面能否代表实盘的关键指标：接近 1 说明纸面可信，接近 0 说明两条曲线无关。", ""]
            for i in infos:
                L += ["### %s 数据来源与精度" % i["strategy"], "",
                      "- 来源：`%s`" % i.get("source", "-"),
                      "- 精度：%s" % i.get("precision", "-"), ""]
                ex = i.get("excluded") or []
                if ex:
                    # 按原因分组汇总，避免逐条罗列 80+ 行
                    by_reason = {}
                    for e in ex:
                        by_reason.setdefault(e.get("exclude_reason", "other"), []).append(e)
                    L += ["**已剔除的非策略/异常记录（共 %d 笔）**：" % len(ex), "",
                          "| 原因 | 笔数 | 说明 |", "|---|---|---|"]
                    desc = {
                        "debug_blacklist": "300684.SZ 全票剔除（用户确认该票交易为调试错误）",
                        "amount_overflow": "单笔金额 > 本金×3 的兜底",
                        "pre_strategy_cleanup": "策略建仓前的账户历史持仓清理（55 只股票，策略后续买回 0 笔）",
                    }
                    for reason, items in by_reason.items():
                        L.append("| `%s` | %d | %s |" % (
                            reason, len(items), desc.get(reason, "-")))
                    amt = sum(float(e.get("amount") or 0) for e in ex)
                    L += ["", "剔除金额合计：%.0f 元（不参与净值计算）。" % amt, ""]
                if i.get("n_invalid"):
                    L += ["**⚠️ 数据不可信区间：%s ~ %s**" % (
                        i.get("start_untrusted") or "-", i.get("untrusted_until") or "-"), "",
                        "该区间有 %d 笔 price=0 的卖出成交（账本早期未记录成交价），" % i["n_invalid"],
                        "卖出无法计入现金 → 现金被扣成负数 → 该区间盈亏不可信。",
                        "**本报告的对比起点已自动调整为可信起点 %s。**" % (i.get("trusted_from") or "-"), ""]
                d = details.get(i["strategy"])
                if d is not None:
                    L += ["<details><summary>逐日明细（归一化 1.0）</summary>", "",
                          "| 日期 | 纸面 | 实盘 | 偏离(pp) |", "|---|---|---|---|"]
                    for _, r in d.iterrows():
                        L.append("| %s | %.4f | %.4f | %+.2f |" % (
                            r["date"], r["paper_n"], r["live_n"], r["diff_pp"]))
                    L += ["", "</details>", ""]

    # ---- 差异归因 ----
    L += ["## 差异归因（读法）", "",
          "实盘与纸面的差异主要来自四层，逐层排查：", "",
          "1. **信号层**：纸面用 T-1 盘后产物，实盘实际执行时刻可能用的是更早/更晚的版本；",
          "2. **成交层**：纸面假设开盘全额成交，实盘有一字板买不进、部分成交、撤单重下；",
          "3. **手数层**：纸面按 100 股整手理论值，实盘受可用资金、最低委托金额约束；",
          "4. **人为层**：手动干预、临时停牌、涨跌停顺延。", "",
          "> 若纸面明显好于实盘，优先查第 2 层（成交假设过乐观）；",
          "> 若纸面明显差于实盘，查第 1 层（信号日期错配）与第 4 层（人工干预）。", ""]

    # ---- 已知缺口 ----
    L += ["## 已知数据缺口（禁止猜测回填）", "",
          "- V1.3 `qmt_trade_log.csv` 中 2026-08-20 ~ 08-24 有 **63 笔 price=0** 记录，",
          "  属早期账本未记录成交价。按 AGENTS.md 红线「历史缺口如实留空」，不做插值。",
          "- 该区间无法计算精确盈亏；08-25 之后记录完整可用。",
          "- G2 桥回执的字段随版本变化，代码已按宽松解析；若字段缺失会显示为空。", ""]

    L += ["## 对照有效性说明", ""]

    # V1.3 信号源说明
    both = [r for r in rows if r["strategy"] == "v13"]
    if both:
        r = both[0]
        ov = r.get("code_overlap", 0)
        if ov == 0:
            L += ["### V1.3：对照**不成立**（重合度 0）", "",
                  "纸面信号源与实盘执行链路不一致。需确认 v13 的 signal_pattern 是否指向",
                  "实盘当期真正使用的那条链路（`_selection_full.csv` 旧链 vs `_final_list.csv` 新链）。",
                  ""]
        else:
            L += ["### V1.3：对照**成立**（买入重合率 %.1f%%）" % r.get("buy_overlap_pct", 0), "",
                  "**信号源**：`<date>_selection_full.csv`（旧链 09:45 重排，与实盘 08-20~09-30 同链路）",
                  "  - 过滤：`total >= 58`（REDLINE），按 total 降序取 Top5；",
                  "  - 纸面按 T-1 盘后信号 → T 日开盘成交（无前视）。", "",
                  "**剩余差异来自执行层**，可逐项核对：", "",
                  "| 差异项 | 说明 |", "|---|---|",
                  "| 纸面买入 %d 笔 vs 实盘买入 %d 笔 | 纸面每日补仓至 Top5，实盘受资金/持仓约束 |" % (
                      r.get("paper_buy", 0), r.get("live_buy", 0)),
                  "| 两边都买 %d 只 | 信号一致的部分 |" % r.get("buy_overlap", 0),
                  "| 仅实盘买 %d 只 | 实盘买了纸面未选的票（信号日期错配 / 人工干预 / 纸面已满仓） |" % r.get("buy_live_only", 0),
                  "| 仅纸面买 %d 只 | 纸面选但实盘未买（资金不足 / 一字板买不进 / 实盘已持有） |" % r.get("buy_paper_only", 0),
                  ""]
            if r.get("live_invalid_px"):
                L += ["**实盘数据缺口**：%d 笔 price=0（2026-08-20~08-24 早期账本），" % r["live_invalid_px"],
                      "这批无法参与价格对比，但已计入笔数与代码重合统计。", ""]

    # G2 对照的下单 vs 成交问题
    g2r = [r for r in rows if r["strategy"] == "g2"]
    if g2r and g2r[0].get("live_available"):
        L += ["### G2：实盘侧是「下单意图」，不是「成交回报」", "",
              "`cmd/orders_*_done_*.json` 记录的是桥**发出的委托**（含 price/vol），",
              "**没有成交价与成交数量**。因此本对照只能比「下单结构」，不能比「成交结果」。", "",
              "要拿到真实成交，需要在 QMT 端导出 `get_trade_detail_data(...,'DEAL',...)`。",
              "当前 G2 精度：委托层可比，成交层缺失。", ""]

    out = os.path.join(RESULTS, "live_vs_paper.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("    对照报告: %s" % out)
    return out


def main():
    rows = []
    for name in ("v13", "g2"):
        if name == "v13":
            live = load_v13_live()
            start = 100000.0
        else:
            live = load_g2_live()
            start = 100000.0
        paper = load_paper(name)
        rows.append(compare(name, live, paper, start))
    nav_rows = build_nav_section()
    write_report(rows, nav_rows)
    for r in rows:
        print("  %s: %s" % (r["strategy"], {k: v for k, v in r.items() if k != "strategy"}))
    for n in nav_rows:
        if "_detail" in n or "_live_only" in n:
            continue
        print("  NAV %s: %s" % (n.get("strategy", "g2"),
                                {k: v for k, v in n.items() if k != "strategy"}))
    return 0


def build_nav_section():
    """实盘 NAV 重建 + 与纸面 NAV 逐日对比（G2 / V1.3）。"""
    from . import live_nav as LN
    out = []

    # ---- G2 ----
    cap = LN.load_capital(LN.G2_CAPITAL)
    if cap is not None:
        live_nav = LN.build_g2_nav(end_capital=cap.get("capital"))
        if live_nav is not None:
            os.makedirs(RESULTS, exist_ok=True)
            live_nav.to_csv(os.path.join(RESULTS, "g2_live_nav.csv"),
                            index=False, encoding="utf-8-sig")
            info, detail = compare_nav(os.path.join(RESULTS, "g2_nav.csv"), live_nav, "g2")
            if info:
                info["precision"] = "中（有每日持仓账本，但 fills 不全）"
                info["source"] = "cmd/positions_cfg_*.json + state/fills_*.json"
                out.append(info)
                out.append({"_detail": detail, "_strategy": "g2"})

    # ---- V1.3 ----
    vcap = LN.load_capital(LN.V13_CAPITAL)
    vnav = LN.build_v13_nav(end_capital=(vcap or {}).get("capital"))
    if vnav is not None:
        os.makedirs(RESULTS, exist_ok=True)
        vnav.to_csv(os.path.join(RESULTS, "v13_live_nav.csv"),
                    index=False, encoding="utf-8-sig")
        # 对比起点用「可信区间起点」——无价成交污染过的日期会算出负现金，不能参与对比
        trusted_from = vnav.attrs.get("trusted_from")
        info, detail = compare_nav(os.path.join(RESULTS, "v13_nav.csv"), vnav, "v13",
                                   start_from=trusted_from)
        if info:
            info["precision"] = "低（仅成交流水，无持仓快照）"
            info["source"] = "data/qmt_trade_log.csv + strategy_capital.json"
            info["excluded"] = vnav.attrs.get("excluded_orders", [])
            info["n_invalid"] = vnav.attrs.get("n_invalid_fills", 0)
            info["trusted_from"] = trusted_from
            info["untrusted_until"] = vnav.attrs.get("untrusted_until")
            info["start_untrusted"] = str(vnav["date"].iloc[0])
            out.append(info)
            out.append({"_detail": detail, "_strategy": "v13"})
    return out


if __name__ == "__main__":
    raise SystemExit(main())
