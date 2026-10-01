# -*- coding: utf-8 -*-
"""2026-09-30 集合竞价预判摘要 → 飞书 bot 私聊推送。

复用项目内 lark_push（唯一推送出口，已收敛 CLI 路径探测 / 凭据清理 /
长文本分片 / 严格成功判定），避免重复实现踩「任务跑了但没消息」的坑。
"""
import io
import json
import os
import sys

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import lark_push  # noqa: E402

UID = "ou_bd13444d8ea53c28249c669f43f3eeff"

raw = json.load(open(
    os.path.join(HERE, "data", "premarket_20260930_raw.json"),
    encoding="utf-8"))
ana = json.load(open(
    os.path.join(HERE, "data", "cache", "analysis_20260930.json"),
    encoding="utf-8"))

TEXT = u"""【集合竞价预判】2026-09-30 · Project_16 G2（子账户 70180771）

■ 交易日门禁：是
■ G2候选：复用夜间 20260930_g2_top10.csv（9只，均≥60红线），未重算
  top3：688155先惠技术74.5 / 002759 ST天际72.5 / 688612威迈斯64.0
  相对强势：603033三维股份(+0.92%, 主力+22万同向)、920237力佳科技(+1.72%但资金背离)
  风险：ST股3只(002759/603021/603378)，买入前须确认可执行性
  量比：东财扩展接口熔断，10/10 缺失（竞价强度无法评估，最大缺口）

■ 持仓预警等级：🟡 警惕
  无标的触及 -7% 止损，但两只浮亏接近阈值 + 当日主力资金全线流出
  605098 行动教育 成本54.89 现52.97  -3.50%  距止损仅3.5pp、已持10日 ← 盯盘第一优先
  000607 华媒控股 成本3.92  现4.04   +3.06%  昨建仓新仓，主力净流出448万
  002510 天汽模   成本5.835 现5.68   -2.66%  主力净流出86万
  口径：仅认G2独立账本3只，未误纳管共享账户其他5只

■ 关键催化/风险
  催化：8.96亿资金流入有色金属（利好600547/600988）；先惠技术三高管拟斥8000万增持
  风险：通信/光通信集体回调（603083、600345同板块）；002237恒邦集团拟减持≤1%；300592股东质押8.76%

■ 取数来源
  F2主源=Tushare T-1(20260929)  ← 本地moneyflow parquet，权威口径，滞后1天(新鲜)
  基础行情=腾讯 22/22（09:35盘中实时，非严格竞价快照）
  板块=东财行业 22/22；F3新闻=东财 22/22
  x TDX MCP 熔断（两次调用 list tools failed）→ 降级 miniQMT 10/10
  x 东财扩展接口熔断 → 量比/换手率全缺
  x 板块第二源不可用 → 板块双源对拍 0/10
  - 悟道 09:15-10:30 盘中限制跳过（非失败，未记 fail）

■ 交叉验证
  F2：10/10 标注 Tushare T-1
  行情：腾讯 vs miniQMT 9/10 通过；002436兴森科技 价差1.273% 超阈值
  板块：0/10（第二源缺失，全部 ok=false）

■ 偏差披露
  今日09:15 V1.3候选未生成（20260930_model_top10.csv 不存在），
  步骤3.5/3.6 实际使用 09-28 旧 D_model 候选，未冒充今日候选。
  建议排查 09:15 候选预生成任务链路。
  QMT bridge 心跳 09:25:02 正常，pending=0，9:45 复核链路可用。

报告：data/real/20260930_集合竞价预判.md
"""

ok = lark_push.send_text(
    TEXT, as_ident="bot", uid=UID, title="集合竞价预判摘要")
print("[PUSH] result=%s" % ok)
sys.exit(0 if ok else 1)
