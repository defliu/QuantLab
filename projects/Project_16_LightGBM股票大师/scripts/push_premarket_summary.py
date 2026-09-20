# coding: utf-8
"""推送集合竞价预判摘要到飞书私聊。"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ)
import qmt_card as QC

ts = time.strftime("%Y-%m-%d %H:%M:%S")

summary = """**大盘**：沪深300 -0.32%，全线低开低走，量比偏低，情绪谨慎。

**G2 候选强弱**（红线60）：
1. **688029 南微医学** +2.14%，F2 +892.6万，医疗器械+0.11%，H1营收+10.4%/拟10派5元 → **最强**
2. **605098 行动教育** -0.60%，F2 +14.5万，量比0.05极低，净利+19.1%/分红93% → 中性偏弱
3. **688528 秦川物联** +0.32%，F2 -14.8万，催化平淡 → 最弱

**持仓预警**：
- 300475 香农芯创 **警惕**：浮亏-5.7%，距-7%止损线仅1.3pp（F2+1347万但股价弱，背离）
- 603332 苏州龙杰 正常：浮亏-3.0%，+0.45%反弹
- 300138 晨光生物 正常：浮亏-3.1%，+0.18%微涨
- 600409 三友化工 正常：浮亏-1.0%

**关键风险**：大盘弱势下688029逆势上涨谨防冲高回落；300475接近止损需9:45重点复核。

> F2主源=Tushare T-1(2026-09-15)，权威口径。"""

card = {
    "schema": "2.0",
    "config": {"width_mode": "default", "update_multi": True},
    "header": {
        "title": {"tag": "plain_text", "content": "集合竞价预判 · 2026-09-16"},
        "subtitle": {"tag": "plain_text", "content": "%s · Project_16 盘前" % ts},
        "template": "blue",
        "icon": {"tag": "standard_icon", "token": "notification_colorful"},
        "text_tag_list": [{"tag": "text_tag", "text": {"tag": "plain_text", "content": "盘前预判"}, "color": "blue"}],
    },
    "body": {
        "direction": "vertical", "padding": "12px 12px 20px 12px", "vertical_spacing": "8px",
        "elements": [
            {"tag": "markdown", "content": summary},
            {"tag": "markdown", "content": "<font color='grey'>%s · 数据来源：腾讯行情+Tushare F2+新浪板块</font>" % ts, "text_size": "notation"},
        ],
    },
}

ok = QC.send_lark_card(card, chat_id=None)  # chat_id=None 强制私聊
print("[SEND] %s" % ("成功" if ok else "失败"))
sys.exit(0 if ok else 2)
