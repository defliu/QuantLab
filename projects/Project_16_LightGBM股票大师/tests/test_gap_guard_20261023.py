# -*- coding: utf-8 -*-
"""gap_guard 回归测试（T-20261023-007 时间归一化量比改造）。

覆盖：
  A. gap_guard 四分支：极端低开拒 / 低开放量放行 / 低开缩量拒 / 正常放行 / 缺数据 fail-open
  B. elapsed_session_min：开盘前 0 / 09:50 -> 20 / 午休 120 / 收盘后 240
  C. gap_volume_ratio：量纲正确性（当日按常态节奏成交 -> vr≈1.0）
  D. 阈值单调性：同一低开幅度，量比越大越容易通过
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import position_filter as PF

fails = []


def chk(name, cond, detail=""):
    if cond:
        print("  [PASS] %s %s" % (name, detail))
    else:
        print("  [FAIL] %s %s" % (name, detail))
        fails.append(name)


print("== A. gap_guard 四分支 ==")
# pre_close=10.0
# 极端低开 -6%：open=9.4
skip, why = PF.gap_guard("000001.SZ", 9.4, 10.0, 9.4, 99.0)
chk("A1 极端低开-6% 即使量比99也拒", skip and "极端低开" in why, why)

# 低开 -4%：open=9.6，量比 3.5（>=3.3）→ 放行
skip, why = PF.gap_guard("000001.SZ", 9.6, 10.0, 9.6, 3.5)
chk("A2 低开-4% 量比3.5 放行", not skip, str(why))

# 低开 -4%：量比 2.0（<3.3）→ 拒
skip, why = PF.gap_guard("000001.SZ", 9.6, 10.0, 9.6, 2.0)
chk("A3 低开-4% 量比2.0 拒", skip and "量比不足" in why, why)

# 正常开盘 +1%：放行（不看量比）
skip, why = PF.gap_guard("000001.SZ", 10.1, 10.0, 10.1, 0.1)
chk("A4 正常开盘+1% 放行", not skip, str(why))

# 边界 -3.0%（等于 GAP_OPEN_PCT）→ 进入量比判定
skip, why = PF.gap_guard("000001.SZ", 9.7, 10.0, 9.7, None)
chk("A5 边界-3.0% 量比NA 拒(fail-closed 于该档)", skip, why)
skip, why = PF.gap_guard("000001.SZ", 9.7, 10.0, 9.7, 3.3)
chk("A6 边界-3.0% 量比3.3 放行", not skip, str(why))

# 边界 -5.0%（等于 GAP_HARD_PCT）→ 极端低开
skip, why = PF.gap_guard("000001.SZ", 9.5, 10.0, 9.5, 99.0)
chk("A7 边界-5.0% 极端低开拒", skip and "极端低开" in why, why)

# 缺行情数据 → fail-open
skip, why = PF.gap_guard("000001.SZ", 9.6, None, None, None)
chk("A8 pre_close 缺失 fail-open", not skip, str(why))

print("== B. elapsed_session_min ==")
for hm, exp in (("09:00", 0.0), ("09:30", 0.0), ("09:31", 1.0), ("09:50", 20.0),
                ("11:30", 120.0), ("12:30", 120.0), ("13:01", 121.0),
                ("14:57", 237.0), ("15:00", 240.0), ("20:00", 240.0)):
    got = PF.elapsed_session_min(hm)
    chk("B %s" % hm, abs(got - exp) < 1e-9, "got=%.1f exp=%.1f" % (got, exp))

print("== C. gap_volume_ratio 量纲 ==")
# 构造：前5日均量 240 万手=全天；09:50 已开盘 20 分钟，按常态节奏应成交 20 万手 -> vr=1.0
PF.recent_avg_vol = lambda code, n=5: 2400000.0
vr = PF.gap_volume_ratio("000001.SZ", 200000.0, now_hm="09:50")
chk("C1 常态节奏 vr≈1.0", vr is not None and abs(vr - 1.0) < 1e-6, "vr=%.4f" % (vr if vr else -1))
vr = PF.gap_volume_ratio("000001.SZ", 660000.0, now_hm="09:50")
chk("C2 3.3倍放量 vr≈3.3", vr is not None and abs(vr - 3.3) < 1e-6, "vr=%.4f" % (vr if vr else -1))
vr = PF.gap_volume_ratio("000001.SZ", 100000.0, now_hm="09:50")
chk("C3 缩量一半 vr≈0.5", vr is not None and abs(vr - 0.5) < 1e-6, "vr=%.4f" % (vr if vr else -1))

print("== D. 边界/降级 ==")
chk("D1 开盘前(09:00) vol 无意义 -> None", PF.gap_volume_ratio("000001.SZ", 100000.0, now_hm="09:00") is None)
chk("D2 vol<=0 -> None", PF.gap_volume_ratio("000001.SZ", 0.0, now_hm="09:50") is None)
PF.recent_avg_vol = lambda code, n=5: None
chk("D3 均量缺失 -> None(fail-open)", PF.gap_volume_ratio("000001.SZ", 100000.0, now_hm="09:50") is None)

print("== E. 阈值单调性（低开-4%） ==")
res = []
for v in (0.5, 1.0, 2.0, 3.0, 3.3, 4.0, 6.0):
    s, _ = PF.gap_guard("000001.SZ", 9.6, 10.0, 9.6, v)
    res.append((v, s))   # s=True 表示被拒
mono = all(not (res[i][1] is False and res[i + 1][1] is True) for i in range(len(res) - 1))
chk("E1 被拒率随量比单调不增", mono, str(res))
chk("E2 分界在 3.0 拒 / 3.3 放行", [v for v, s in res if s] == [0.5, 1.0, 2.0, 3.0], str(res))

print()
if fails:
    print("FAILED %d 项: %s" % (len(fails), fails))
    sys.exit(1)
print("ALL PASS")
