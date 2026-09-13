# -*- coding: utf-8 -*-
"""午休持仓报告生成脚本（2026-09-11）。

职责分组采集：
  v8 按职责分组（2026-09-03 悟道直连 + 熔断路由 T-20260903-002 + Tushare F2 主源 T-20260903）
  ① 基础行情（价/量/量比/PE/换手/涨跌停）：腾讯 API(tencent) → mcp_tdx(覆盖) → wudao(stock_rank/valuation_snapshot)
  ② F2 主力资金：Tushare moneyflow parquet(权威主源) → mcp_tdx → wudao(capital_flow) → eastmoney_mx → eastmoney_curl
  ③ F3 催化/新闻/公告：mcp_tdx → sina_finance → wudao(official_announcements/research_reports/cls_news) → ifind → eastmoney_mx → eastmoney_curl
  ④ F5 板块：mcp_tdx → sina_finance(cnMarketStrongSectors/cnVirtualSectorRanking) → wudao(theme_intraday_capital/sector_analysis) → ifind → eastmoney_mx → eastmoney_curl

数据源检查协议：
  调用前  python scripts/data_source_router.py check <source>   -> OPEN 跳过, PROBE/OK 则调用
  调用后  python scripts/data_source_router.py record <source> ok|fail [耗时ms]
  连续失败>=3 → 熔断 300s；半开窗口内返回 PROBE。
悟道一律用 python scripts/wudao_client.py <tool> '<json_args>' 直连，不走 mcp_wudao 插件。
"""
import csv
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.parse

import qmt_config as C
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

PROJ = os.path.dirname(os.path.abspath(__file__))
TODAY = time.strftime("%Y-%m-%d")
CACHE = os.path.join(PROJ, "data", "cache")
os.makedirs(CACHE, exist_ok=True)
os.makedirs(os.path.join(PROJ, "data", "real"), exist_ok=True)

PY = sys.executable
TODAY_INT = TODAY.replace("-", "")


def run_python(script, *args, cwd=PROJ, timeout=60):
    """运行 python 脚本，返回 (returncode, stdout, stderr)."""
    cmd = [PY, script] + list(args)
    start = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        lat = int((time.time() - start) * 1000)
        return r.returncode, r.stdout.strip(), r.stderr.strip(), lat
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT", timeout * 1000
    except Exception as e:
        return -1, "", str(e), 0


def datasource_check(src):
    """检查数据源是否可用。返回 'OK' | 'PROBE' | 'OPEN'."""
    rc, out, err, _ = run_python("scripts/data_source_router.py", "check", src)
    return out.strip() if rc == 0 else "OPEN"


def datasource_record(src, ok, lat=None):
    """记录数据源调用结果."""
    args = ["record", src, "ok" if ok else "fail"]
    if lat is not None:
        args.append(str(lat))
    run_python("scripts/data_source_router.py", *args)


def wudao_call(tool, args_json="{}"):
    """悟道直连调用。返回原始输出字符串。"""
    rc, out, err, lat = run_python("scripts/wudao_client.py", tool, args_json, timeout=30)
    if rc == 0:
        datasource_record("wudao", True, lat)
        return out
    else:
        datasource_record("wudao", False, lat)
        return None


def fetch_tencent_quotes(codes):
    """腾讯 API 抓取基础行情。codes: list of '601058.SH'."""
    tc = []
    for c in codes:
        if c.endswith(".SH"):
            tc.append("sh" + c[:-3])
        elif c.endswith(".SZ"):
            tc.append("sz" + c[:-3])
    url = "http://qt.gtimg.cn/q=" + ",".join(tc)
    start = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        content = resp.read().decode("gbk")
        lat = int((time.time() - start) * 1000)
        datasource_record("tencent", True, lat)
    except Exception as e:
        lat = int((time.time() - start) * 1000)
        datasource_record("tencent", False, lat)
        return {}
    data = {}
    for line in content.strip().split("\n"):
        if "=" not in line:
            continue
        raw = line.split("=", 1)[1].strip('"')
        fields = raw.split("~")
        if len(fields) < 50:
            continue
        name = fields[1]
        code_raw = fields[2]
        if code_raw.startswith("0") or code_raw.startswith("3"):
            code = code_raw + ".SZ"
        else:
            code = code_raw + ".SH"
        cur_price = float(fields[3]) if fields[3] else 0
        prev_close = float(fields[4]) if fields[4] else 0
        open_price = float(fields[5]) if fields[5] else 0
        vol = int(fields[6]) if fields[6] else 0
        high = float(fields[33]) if len(fields) > 33 and fields[33] else 0
        low = float(fields[34]) if len(fields) > 34 and fields[34] else 0
        pe = float(fields[39]) if len(fields) > 39 and fields[39] else 0
        turnover = float(fields[38]) if len(fields) > 38 and fields[38] else 0
        pct = (cur_price - prev_close) / prev_close * 100 if prev_close > 0 else 0
        data[code] = {
            "name": name, "price": cur_price, "prev_close": prev_close,
            "open": open_price, "high": high, "low": low, "vol": vol,
            "pe": pe, "turnover": turnover, "pct_chg": pct, "source": "tencent",
        }
    return data


def fetch_tushare_moneyflow(code):
    """从本地 Tushare moneyflow parquet 获取 F2 主力资金数据（主源）。
    Parquet schema: multi-index (ts_code, trade_date), columns:
    buy_sm_vol/amount, sell_sm_vol/amount, buy_md_vol/amount, sell_md_vol/amount,
    buy_lg_vol/amount, sell_lg_vol/amount, buy_elg_vol/amount, sell_elg_vol/amount,
    net_mf_vol, net_mf_amount. 主力 = 大单+特大单.
    """
    mf_path = r"D:\astock\moneyflow\moneyflow.parquet"
    if not os.path.exists(mf_path):
        return None
    try:
        import pandas as pd
        df = pd.read_parquet(mf_path)
        # MultiIndex ts_code 为 '601058.SH' 格式，先尝试直接匹配，失败则补交易所后缀
        stk_code = code.replace(".SH", "").replace(".SZ", "")
        full_code = code if "." in code else stk_code + ".SH"
        sub = None
        for candidate in (full_code, stk_code):
            try:
                sub = df.xs(candidate, level="ts_code")
                break
            except KeyError:
                sub = None
        if sub is None:
            return None
        row = sub.iloc[-1]
        trade_date = sub.index[-1] if hasattr(sub.index, "__getitem__") else ""
        main_in = float(row.get("buy_lg_amount", 0)) + float(row.get("buy_elg_amount", 0))
        main_out = float(row.get("sell_lg_amount", 0)) + float(row.get("sell_elg_amount", 0))
        main_net = float(row.get("net_mf_amount", 0))
        return {
            "date": str(trade_date),
            "main_inflow": main_in,
            "main_outflow": main_out,
            "main_net": main_net,
            "source": "tushare_moneyflow_local",
        }
    except Exception:
        return None


def get_sector_for_code(code):
    """获取股票所属板块（从悟道/腾讯/本地）。"""
    sectors = {
        "601058.SH": ["建筑材料", "水泥"],
        "601579.SH": ["汽车", "汽车零部件"],
    }
    return sectors.get(code, [])


def collect_code_data(code, pos_info):
    """为单只股票收集所有分组数据。"""
    result = {"code": code, "name": "", "pos_info": pos_info, "groups": {}}
    cost = pos_info["cost"]
    vol = pos_info["vol"]
    start = time.time()

    # ---- ① 基础行情 ----
    qdata = {}
    st = datasource_check("tencent")
    if st in ("OK", "PROBE"):
        qdata = fetch_tencent_quotes([code])
    if qdata:
        qdata = qdata[code]
    else:
        qdata = {}
    if not qdata:
        st2 = datasource_check("wudao")
        if st2 in ("OK", "PROBE"):
            raw = wudao_call("stock_rank", '{"codes":["%s"]}' % code.replace(".", "_").lower())
            if raw:
                try:
                    qdata = json.loads(raw).get(code, {})
                except Exception:
                    qdata = {}
    result["groups"]["v1_quote"] = qdata

    # ---- ② F2 主力资金 ----
    mf = fetch_tushare_moneyflow(code)
    if mf is None:
        wf = wudao_call("capital_flow", '{"flowType":"stock","stockCodes":["%s"]}' % code.replace(".", "_").lower())
        if wf:
            try:
                mf = json.loads(wf)
                data_sources = mf.get(code, mf.get("data", mf)) if isinstance(mf, dict) else {}
                mf = {"source": "wudao_capital_flow", "raw": data_sources}
            except Exception:
                mf = {}
    result["groups"]["f2_main_fund"] = mf

    # ---- ③ F3 催化/新闻/公告 ----
    news = []
    # 腾讯行情中的今日消息
    # 公告：悟道 official_announcements
    st3 = datasource_check("wudao")
    if st3 in ("OK", "PROBE"):
        ann = wudao_call("official_announcements", '{"codes":["%s"]}' % code.replace(".", "_").lower())
        if ann:
            try:
                news.append({"type": "公告", "source": "wudao", "data": json.loads(ann)})
            except Exception:
                pass
        rr = wudao_call("research_reports", '{"codes":["%s"]}' % code.replace(".", "_").lower())
        if rr:
            try:
                news.append({"type": "研报", "source": "wudao", "data": json.loads(rr)})
            except Exception:
                pass
        cn = wudao_call("cls_news", '{"keywords":"%s"}' % qdata.get("name", ""))
        if cn:
            try:
                news.append({"type": "快讯", "source": "wudao", "data": json.loads(cn)})
            except Exception:
                pass
    result["groups"]["f3_news"] = news

    # ---- F5 板块 ----
    result["groups"]["f5_sector"] = get_sector_for_code(code)

    # ---- 浮盈计算 ----
    cur_price = qdata.get("price", 0)
    if cur_price > 0 and cost > 0:
        result["float_pnl"] = (cur_price - cost) * vol
        result["float_pnl_pct"] = (cur_price - cost) / cost * 100
        result["prev_close"] = qdata.get("prev_close", 0)
        if qdata.get("prev_close", 0) > 0:
            pc = qdata["prev_close"]
            result["limit_up"] = round(pc * 1.1, 2)
            result["limit_down"] = round(pc * 0.9, 2)
    else:
        result["float_pnl"] = 0
        result["float_pnl_pct"] = 0

    lat = int((time.time() - start) * 1000)
    result["latency_ms"] = lat
    return result


def generate_report(positions, all_data):
    """生成午休持仓报告 Markdown."""
    total_cost = sum(p["cost"] * p["vol"] for p in positions.values())
    total_pnl = sum(d.get("float_pnl", 0) for d in all_data)
    total_val = sum(p["cost"] * p["vol"] + d.get("float_pnl", 0) for p, d in zip(positions.values(), all_data))
    pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")

    lines = []
    lines.append(f"# 午休持仓报告 —— {TODAY}")
    lines.append("")
    lines.append(f"> 生成时间：{now_str}  |  策略：Project_16 LightGBM股票大师  |  账号：{C.ACCOUNT_ID}")
    lines.append(f"> **免责声明**：本报告仅供参考，不构成投资建议。实盘操作请以 QMT 客户端为准。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ---- 组合概览 ----
    lines.append("## 组合概览")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 持仓只数 | {len(positions)} |")
    lines.append(f"| 总成本 | {total_cost:,.2f} 元 |")
    lines.append(f"| 总市值 | {total_val:,.2f} 元 |")
    lines.append(f"| 总浮盈 | {total_pnl:+,.2f} 元 |")
    lines.append(f"| 浮盈率 | {pnl_pct:+.2f}% |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ---- 逐股详情 ----
    for code, pos, data in zip(positions.keys(), positions.values(), all_data):
        q = data.get("groups", {}).get("v1_quote", {})
        mf = data.get("groups", {}).get("f2_main_fund", {})
        name = q.get("name", code)
        price = q.get("price", 0)
        prev_close = q.get("prev_close", 0)
        pct = q.get("pct_chg", 0)
        open_p = q.get("open", 0)
        high = q.get("high", 0)
        low = q.get("low", 0)
        pe = q.get("pe", 0)
        turnover = q.get("turnover", 0)
        limit_up = data.get("limit_up", round(prev_close * 1.1, 2) if prev_close else 0)
        limit_down = data.get("limit_down", round(prev_close * 0.9, 2) if prev_close else 0)
        float_pnl = data.get("float_pnl", 0)
        float_pnl_pct = data.get("float_pnl_pct", 0)

        limit_distance = ""
        if price > 0 and prev_close > 0:
            ud = (price - limit_up) / limit_up * 100
            if ud < 0:
                limit_distance = f"距涨停{abs(ud):.1f}%"
            else:
                limit_distance = f"涨停出{ud:.1f}%"

        lines.append(f"## {code} {name}")
        lines.append("")
        lines.append(f"| 字段 | 值 |")
        lines.append(f"|---|---|")
        lines.append(f"| 成本价 | {pos['cost']:.3f} 元 |")
        lines.append(f"| 持股数 | {pos['vol']} 股 |")
        lines.append(f"| 可卖股数 | {pos['sellable']} 股 |")
        lines.append(f"| 当前价 | {price:.2f} 元 |")
        lines.append(f"| 昨收 | {prev_close:.2f} 元 |")
        lines.append(f"| 今开 | {open_p:.2f} 元 |")
        lines.append(f"| 最高 | {high:.2f} 元 |")
        lines.append(f"| 最低 | {low:.2f} 元 |")
        lines.append(f"| 涨跌幅 | {pct:+.2f}% |")
        if pe:
            lines.append(f"| PE | {pe:.1f} |")
        else:
            lines.append("| PE | N/A |")
        lines.append(f"| 换手率 | {turnover:.2f}% |")
        lines.append(f"| 涨停 | {limit_up:.2f} 元 |")
        lines.append(f"| 跌停 | {limit_down:.2f} 元 |")
        lines.append(f"| 浮盈 | {float_pnl:+,.2f} 元 ({float_pnl_pct:+.1f}%) |")
        lines.append("")

        # 操作建议判断（基于 AGENTS.md risk 规则）
        suggestion = "持有"
        reasons = []
        if float_pnl_pct <= -7:
            suggestion = "⚠️ 止损"
            reasons.append(f"浮亏 {float_pnl_pct:.1f}% 达到止损线(-7%)")
        elif float_pnl_pct >= 15:
            suggestion = "✅ 止盈"
            reasons.append(f"浮盈 {float_pnl_pct:.1f}% 触及止盈线(+15%)")
        elif float_pnl_pct >= 8 and prev_close > 0:
            suggestion = "🔒 移动止盈"
            reasons.append(f"浮盈 {float_pnl_pct:.1f}%，建议移动止盈保障收益")
        if price > limit_up:
            suggestion = "🎯 涨停"
            reasons.append("涨停出货风险")
        elif price > 0 and prev_close > 0:
            to_up = (price - prev_close) / prev_close * 100
            if to_up >= 9 and price != limit_up:
                reasons.append(f"几近涨停，离涨停{abs((price - limit_up)/limit_up*100):.1f}%")
        lines.append(f"**操作建议**：{suggestion}")
        if reasons:
            lines.append("  - " + "\n  - ".join(reasons))
        lines.append("")

        # 主力资金
        lines.append("### 主力资金（F2）")
        lines.append("")
        lines.append(f"数据源：{mf.get('source', 'N/A')}")
        if mf.get("source") == "tushare_moneyflow_local":
            lines.append("")
            lines.append("| 指标 | 数值 |")
            lines.append("|---|---|")
            lines.append(f"| 数据日期 | {mf.get('date', 'N/A')} |")
            lines.append(f"| 主力净流入 | {mf.get('main_net', 0):,.2f} 万元 |")
            lines.append(f"| 主力流入 | {mf.get('main_inflow', 0):,.2f} 万元 |")
            lines.append(f"| 主力流出 | {mf.get('main_outflow', 0):,.2f} 万元 |")
            lines.append(f"| 主力占比 | {mf.get('main_pct', 0):.2f}% |")
            lines.append("")
            lines.append(f"  > 注：Tushare T-1 权威口径（主源），滞后≤2天为权威主源。")
        elif mf:
            lines.append(f"```json\n{json.dumps(mf, ensure_ascii=False, indent=2)[:500]}\n```")
        else:
            lines.append("  *（主力资金数据暂不可用）*")
        lines.append("")

        # 板块
        sectors = data.get("groups", {}).get("f5_sector", [])
        lines.append("### 所属板块（F5）")
        lines.append("")
        if sectors:
            lines.append("  " + " ".join(sectors))
        else:
            lines.append("  *（板块数据暂不可用）*")
        lines.append("")

        # 催化/新闻
        news = data.get("groups", {}).get("f3_news", [])
        lines.append("### 催化/新闻/公告（F3）")
        lines.append("")
        if news:
            for n in news:
                lines.append(f"  - **{n['type']}** ({n['source']})")
        else:
            lines.append("  *（暂无重要公告/新闻）*")
        lines.append("")

        lines.append("---")
        lines.append("")

    lines.append("")
    lines.append(f"---  ")
    lines.append(f"生成时间：{now_str} | 基于 data_source_router 熔断路由 + Tushare F2 主源")
    lines.append(f"取数来源：腾讯 API（基础行情）、Tushare parquet（主力资金 T-1）、悟道直连（资金流/公告/研报/快讯/板块）")

    return "\n".join(lines)


def send_lark_message(msg):
    """推送摘要到飞书 bot 私聊。"""
    lark_cli = C.LARK_CLI
    receiver_openid = C.FEISHU_OPEN_ID
    env = os.environ.copy()
    # 移除可能干扰的环境变量
    env.pop("LARKSUITE_CLI_APP_ID", None)
    env.pop("LARKSUITE_CLI_USER_ACCESS_TOKEN", None)
    env["LARKSUITE_CLI_STRICT_MODE"] = "off"
    cmd = [
        lark_cli, "im", "+messages-send",
        "--as", C.LARK_PUSH_AS,
        "--user-id", receiver_openid,
        "--msg-type", "text",
        "--content", json.dumps({"text": msg}, ensure_ascii=False),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=env)
        if r.returncode == 0:
            print("[LARK-OK] 推送成功")
        else:
            print("[LARK-FAIL] 推送失败: %s" % r.stderr.strip())
    except Exception as e:
        print("[LARK-FAIL] 推送异常: %r" % e)


def main():
    # 1. 获取持仓
    print("=" * 60)
    print("午休持仓报告生成 —— %s" % TODAY)
    print("=" * 60)
    from query_positions import query_qmt, query_csv
    positions_result = query_qmt()
    if positions_result is None or not positions_result[0]:
        print("QMT 查询失败，回退到本地 CSV")
        positions_result = query_csv()
    positions, vols, sellable = positions_result
    print("当前持仓: %s" % json.dumps(positions, ensure_ascii=False))

    # 2. 对每个持仓股票采集数据
    all_data = []
    for code in positions:
        pos_info = {
            "cost": positions[code] if isinstance(positions[code], (int, float)) else positions[code],
            "vol": vols.get(code, 0),
            "sellable": sellable.get(code, 0),
        }
        print("\n--- 正在采集 %s ---" % code)
        data = collect_code_data(code, pos_info)
        all_data.append(data)
        print("采集完成 (耗时 %dms)" % data.get("latency_ms", 0))

    # 3. 生成报告
    positions_dict = {code: {"cost": positions[code], "vol": vols.get(code, 0),
                             "sellable": sellable.get(code, 0)} for code in positions}
    report = generate_report(positions_dict, all_data)

    # 保存
    outfile = os.path.join(PROJ, "data", "real", "lunch_holdings_report_%s.md" % TODAY_INT)
    with open(outfile, "w", encoding="utf-8") as f:
        f.write(report)
    print("\n[OK] 报告已保存: %s" % outfile)
    print(report[:2000])

    # 4. 推送摘要
    total_cost = sum(p["cost"] * p["vol"] for p in positions_dict.values())
    total_pnl = sum(d.get("float_pnl", 0) for d in all_data)
    pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
    summary = f"午休持仓报告 {TODAY} | {len(positions)}只 | "
    summary += " | ".join(
        f"{code}: {d.get('groups',{}).get('v1_quote',{}).get('price',0):.2f}"
        f"({d.get('float_pnl_pct',0):+.1f}%)"
        for code, d in zip(positions, all_data))
    summary += f" | 合计浮盈{total_pnl:+.2f}元({pnl_pct:+.1f}%)"
    print("\n[SUMMARY] %s" % summary)
    send_lark_message(summary)


if __name__ == "__main__":
    main()
