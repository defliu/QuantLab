# coding=utf-8
"""把主号计划镜像成沙箱可执行的计划（2026-10-29 立）。

为什么需要：G2 计划文件里 `account_id=67014907`、股数按 10 万算；执行壳会校验账号必须与自身
一致（防串号），所以沙箱（70180771 / 2 万）**不能直接吃主号计划**。这个脚本只做三件事：

  1. 账号换成沙箱账号；
  2. `capital` 换成沙箱资金池，并把**股数按资金比例缩放到整百**（缩没了就丢腿并留原因）；
  3. 重新落一份 `rebalance_g2_<date>.json` 到沙箱专用目录，供 58611 端口的 hub 转发。

**它不是第二套决策**：价格、方向、腿的构成完全照抄主号计划，不增不减（除缩放归零的腿），
也不做任何选股。沙箱那份**只验证执行链路，不计入前向业绩**。

用法：
  python qmt_signal_mirror.py --src D:/QuantLab/data/rebalance_g2/rebalance_g2_20261029.json \
      --dst-dir D:/QuantLab/data/rebalance_g2/sbx --account 70180771 --capital 20000
"""
from __future__ import print_function

import argparse
import io
import json
import os
import sys


def _scale_legs(orders, ratio):
    kept, dropped = [], []
    for o in orders or []:
        vol = int(o.get("vol") or 0)
        nv = int(vol * ratio // 100) * 100
        if nv <= 0:
            dropped.append({"code": o.get("code"), "reason": "缩放到 0 股", "vol": vol})
            continue
        item = dict(o)
        item["vol"] = nv
        item["reason"] = "%s|sbx-scaled x%.2f" % (o.get("reason", ""), ratio)
        kept.append(item)
    return kept, dropped


def mirror(src_path, dst_dir, account, capital):
    with io.open(src_path, "r", encoding="utf-8") as f:
        plan = json.load(f)
    src_cap = float(plan.get("capital") or 0) or 1.0
    ratio = float(capital) / src_cap
    if ratio <= 0:
        raise SystemExit("[MIRROR-REJECT] 目标资金必须为正")

    orders, dropped = _scale_legs(plan.get("orders") or [], ratio)
    out = dict(plan)
    out["account_id"] = str(account)
    out["capital"] = float(capital)
    out["orders"] = orders
    # 卖腿/买腿只是给 md 报告用的视图，按缩放后重建，避免两处口径打架
    out["sells"] = [{"code": o["code"], "vol": o["vol"], "cost": o.get("price", 0)}
                    for o in orders if str(o.get("action", "")).upper() == "SELL"]
    out["buys"] = [{"code": o["code"], "vol": o["vol"], "price": o.get("price", 0)}
                   for o in orders if str(o.get("action", "")).upper() == "BUY"]
    out["note"] = ("[sbx-mirror] 由 %s 按 %.3f 缩放；仅验证执行链路，不计前向业绩"
                   % (os.path.basename(src_path), ratio))
    out["mirror"] = {
        "from": os.path.basename(src_path),
        "ratio": round(ratio, 4),
        "dropped": dropped,
    }

    if not os.path.isdir(dst_dir):
        os.makedirs(dst_dir)
    name = "rebalance_g2_%s.json" % str(plan.get("date") or "")
    dst = os.path.join(dst_dir, name)
    tmp = dst + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, dst)          # 原子替换：hub 按 mtime 缓存，绝不允许读到半截文件
    return dst, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst-dir",
                    default=r"D:\QuantLab\projects\Project_16_LightGBM股票大师\data\rebalance_g2\sbx")
    ap.add_argument("--account", default="70180771")
    ap.add_argument("--capital", type=float, default=20000.0)
    a = ap.parse_args()
    if not os.path.exists(a.src):
        print("[MIRROR] 源计划不存在，跳过：%s" % a.src)
        return 2
    dst, out = mirror(a.src, a.dst_dir, a.account, a.capital)
    print("[MIRROR] %s -> %s | account=%s capital=%.0f orders=%d dropped=%d"
          % (os.path.basename(a.src), dst, out["account_id"], out["capital"],
             len(out["orders"]), len(out["mirror"]["dropped"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
