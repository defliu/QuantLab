# coding: utf-8
"""沪深300 大盘风控 T 档判定（确定性脚本，2026-09-14 立）。

背景：开盘实时复核任务（15868a74）步骤2 原由 agent 读指数行情手工判断 T 值，
受大模型能力影响（读错数字/算错区间 → 错定仓位）。本脚本把 T 值判断完全下沉为
确定性计算：复用 rebalance_g2.py 的 load_hs300_pct()（腾讯行情实时取价）+ _calc_tier()
（三档门控），agent 只念结果，不参与判断。

T 档（对齐 G2/V1.3 口径，T-20260904-004 / 2026-09-11）：
  T=0：HS300 <= -1.5%  → 空仓（--top 0）
  T=1：HS300  (-1.5%, -1.0%) → 半仓（--top 1、金额 50%）
  T=2：HS300 >= -1.0%  → 正常（--top 2）
数据缺失（腾讯取不到）→ fail-safe 按 T=1 半仓 + 醒目提示（刹车数据缺失时降速不裸奔）。

【语义澄清 2026-09-23，防「TOP2 误读」】
  输出的 tier/top 两个字段都是「T 档位」（0/1/2），**不是目标持仓只数**。
  上游把 --top 传给 rebalance_daily.py 后，脚本内部会归一化（T-20260922-003）：
    eff_top = args.top if args.top < 2 else TOP_N（TOP_N=5）
  即 T=2 传 --top 2 实际按 TOP_N=5 只持仓执行，不会截断成 2 只。
  看到 "top": 2 不代表持仓上限=2；持仓只数上限以 rebalance_daily.TOP_N 为准。

用法：
  python market_tier.py                 # 取实时行情判定，输出一行摘要 + exit 0
  python market_tier.py --json          # 输出 JSON 供脚本消费
  G2_HS300_PCT=-2.0 python market_tier.py   # 注入涨跌幅（测试/演练）
"""
import argparse
import json
import os
import sys

import rebalance_g2 as RBG   # 复用 load_hs300_pct / _calc_tier / g2_config 常量

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出 JSON（供脚本消费）")
    ap.add_argument("--top", action="store_true", help="附加输出 --top 参数建议")
    args = ap.parse_args()

    hs = RBG.load_hs300_pct()
    tier = RBG._calc_tier(hs) if hs is not None else None

    # 数据缺失 fail-safe：按 T=1 半仓（对齐 g2_config 注释 + rebalance_g2:412-414）
    missing = hs is None
    if missing:
        tier = 1
        hs_disp = "N/A"
    else:
        hs_disp = "%+.4f%%" % hs

    top = {0: 0, 1: 1, 2: 2}[tier]
    deploy_note = {0: "空仓（只卖不买）", 1: "半仓（金额50%）", 2: "正常（满仓）"}[tier]

    if args.json:
        out = {
            "hs300_pct": hs,
            "tier": tier,
            "top": top,
            "missing": missing,
            "note": deploy_note,
            "source": "tencent_qt.gtimg.cn" if not missing else "MISSING(fail-safe T=1)",
            # 语义澄清（2026-09-23）：tier/top 均为 T 档位(0/1/2)，非目标持仓只数。
            # rebalance_daily.py 收到 --top>=2 后会归一到 TOP_N(=5)，见 eff_top。
            "semantics": "tier/top are TIER codes (0=clear,1=half,2=full), NOT position counts; rebalance_daily normalizes top>=2 to TOP_N",
        }
        print(json.dumps(out, ensure_ascii=False))
    else:
        if missing:
            print("沪深300 实时行情获取失败 → fail-safe T=1 半仓（勿当正常满仓）")
        else:
            print("沪深300 当日 %s → T=%d %s" % (hs_disp, tier, deploy_note))
        if args.top:
            print("建议参数: --top %d （T 档位，非持仓只数；rebalance_daily 内部会归一到 TOP_N）" % top)
    sys.exit(0)


if __name__ == "__main__":
    main()
