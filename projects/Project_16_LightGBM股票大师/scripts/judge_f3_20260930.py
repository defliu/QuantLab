# -*- coding: utf-8 -*-
"""F3 催化评分判定 - 2026-09-30（V1.3 / 账号 67014907）

按 review_full.py 设计：F3 = AI 结合新闻/公告给出 0-10。
评分只依据 data/cache/f3_news_20260930.json 实际采到的新闻，每只票写 catalyst_basis 便于审计。
沿用 2026-09-29 口径：0-2 明确利空 / 4 无个股事件仅噪音 / 5 中性 / 6 题材或行业正向 / 8+ 强确证事件。
不采到新闻的票不写分数（留 None，交 review_full 回退 Tushare 业绩公告兜底），严禁编造。
"""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REVIEW = os.path.join(HERE, "data", "tdx_review.json")

JUDGE = {
    "000920.SZ": (4.0, "无个股事件；6条全为'XX家公司获机构调研(附名单)'市场汇总类噪音(09-21~09-30)，无法确认本票在名单内，视为无催化"),
    "003026.SZ": (5.0, "09-22 龙虎榜(机构席位买入/卖出详情)+周内换手率超100%筹码大换手；机构关注与极端换手并存，投机过热中性偏谨慎"),
    "603882.SH": (6.0, "肝炎概念/细胞免疫治疗概念多次主力资金净流入(09-14/09-17/09-21)+突破半年线；今日所属行业+4.16%领涨，属题材级正向，无个股公告"),
    "000155.SZ": (5.0, "09-04/09-05 拟1亿元参股四川能源核产业(真实公司事件但已逾25天)；09-28 公用事业行业资金流入榜为板块级，非个股公告"),
    "001378.SZ": (5.0, "09-17/18 筹码集中+股东户数降逾一成+融资客加仓(机构吸筹信号，但为名单类且已逾12天)；无个股公告"),
    "600547.SH": (5.0, "黄金板块回购主题(09-29 多家A股提议回购)；'千亿黄金巨头董事长提议回购+昨日跌停'未确认含本票(本票市值不足千亿)，只作板块级参考"),
    "603162.SH": (4.0, "09-29 交通运输板块主力资金撤离2.22亿(板块级利空)；油轮运价暴涨四倍为09-17/18旧闻，方向已过时"),
    "300531.SZ": (4.0, "拼多多概念多空交替(09-29涨2.27%/09-23跌2.36%)+跨境支付概念资金流出，题材噪音无个股事件"),
    "002237.SZ": (3.0, "09-22 恒邦集团拟减持公司不超1%股份——明确个股级利空(规模小)；金属铅/黄金/锌板块资金净流出为行业背景"),
    "603083.SH": (3.0, "09-28 CPO概念跌6.56%主力净流出189股、F5G概念跌6.65%净流出34股，所属板块资金大幅撤离；百元股统计为噪音，个股无事件"),
}

with open(REVIEW, "r", encoding="utf-8") as f:
    d = json.load(f)

hit = 0
for s in d["stocks"]:
    j = JUDGE.get(s["ts_code"])
    if not j:
        continue
    s["catalyst_score"], s["catalyst_basis"] = j[0], j[1]
    s["f3_source"] = "eastmoney_search_news+analyst_judgement"
    hit += 1

tmp = REVIEW + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
os.replace(tmp, REVIEW)

print("[OK] F3 评分回填 %d/%d -> %s" % (hit, len(d["stocks"]), REVIEW))
for s in d["stocks"]:
    print("  %-11s %-8s F3=%-4s %s" % (s["ts_code"], (s.get("name") or "")[:6],
                                       s.get("catalyst_score"), (s.get("catalyst_basis") or "")[:60]))
