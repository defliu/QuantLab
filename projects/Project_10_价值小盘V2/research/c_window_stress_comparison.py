# coding=utf-8
"""组件 C 补测: 分层降仓 vs 断路器 —— 2024-01~02 微盘危机窗口对比
讨论室 Round 1 裁决执行 (2026-08-06, 诚哥批准 C->A->B)

背景:
  P1-1 网格 (grid_validation.txt) 已证伪分层降仓: 全期回撤 -32.7% vs 断路器 -29.1%,
  但存档窗口最细只到 2024+。分层降仓唯一理论翻盘点 = 流动性危机窗口避免底部一把清仓,
  本脚本对 2024-01-15 ~ 2024-02-29 危机窗口做精确切片对比。

口径 (与 P1-1 完全一致, 复用 run_grid_validation.py):
  评分 = V1 基线 (0.8z+0.2hp, P1-1 存档口径) + V2a 纯BP (当前默认口径) 双口径交叉
  风控 = R0 断路器 (15% 一刀清仓) vs R2 分层降仓 (10%->70% / 15%->40% / 20%->清仓)

自检: V1 口径全期指标必须复现 grid_validation.txt 存档值
  (R0: 年化+15.3% 回撤-29.1% 超额全期+178.0%; R2: 年化+15.2% 回撤-32.7% 超额+176.4%)

输出: results/c_window_stress_comparison.txt
"""
import sys, os, time

# E->D 迁移路径修正: run_grid_validation.py 内 sys.path 指向 E:\QuantLab (已迁空),
# 先插入 D 盘真实路径, 使 research.multi_factor_ic 可解析
_P10 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"D:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
sys.path.insert(0, r"D:\QuantLab")
sys.path.insert(0, _P10)

import run_grid_validation as rgv  # 触发数据加载 (~3min)
import pandas as pd

HERE = os.path.dirname(os.path.abspath(os.path.dirname(__file__)))
RES = os.path.join(HERE, "results")

log = []
def p(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    log.append(s)

p("============ 组件C: 危机窗口压力对比 (2024-01~02) ============")
p("运行时间:", time.strftime("%Y-%m-%d %H:%M:%S"))

base = rgv.run_base()

combos = [
    ("V1评分xR0断路器",  "bp", 0.8, 0.2, False, "c_R0_v1"),
    ("V1评分xR2分层降仓", "bp", 0.8, 0.2, True,  "c_R2_v1"),
    ("V2a评分xR0断路器", "bp", 1.0, 0.0, False, "c_R0_v2a"),
    ("V2a评分xR2分层降仓", "bp", 1.0, 0.0, True, "c_R2_v2a"),
]

runs = {}
p("\n============ 1. 自检: 全期复现 (对照 grid_validation.txt) ============")
for name, method, zw, hw, tiered, tag in combos:
    t1 = time.time()
    scorer = rgv.make_scorer(method, zw, hw)
    risk = rgv.make_risk(tag, atr_stop=False, tiered=tiered)
    res = rgv.run_variant(scorer, risk, signal=False)
    res = res.set_index("date") if "date" in res.columns else res
    st = rgv.summarize(res, base)
    runs[name] = (res, st)
    p("%-20s 年化=%+6.1f%% 回撤=%+6.1f%% 超额[全期=%+7.1f%% 2024+=%+6.1f%%] (%.0fs)" % (
        name, st["年化"] * 100, st["最大回撤"] * 100,
        (st["超额全期"] or 0) * 100, (st["超额2024+"] or 0) * 100, time.time() - t1))

p("\n自检判据: V1口径两行应与存档一致 (R0 +15.3%/-29.1%/+178.0%, R2 +15.2%/-32.7%/+176.4%)")

p("\n============ 2. 危机窗口逐期明细 (2023-12 ~ 2024-07) ============")
for name, (res, st) in runs.items():
    p("\n--- %s ---" % name)
    seg = res[(res.index >= "2023-12-01") & (res.index <= "2024-07-31")]
    for d, r in seg.iterrows():
        p("  %s  期收益=%+7.2f%%  nav=%6.3f  持仓n=%3d  卖=%3d 买=%3d" % (
            d.strftime("%Y-%m-%d"), r["period_return"] * 100, r["nav"], r["n"], r["sells"], r["buys"]))

p("\n============ 3. 窗口统计 ============")
windows = [
    ("危机期 2024-01~03", "2024-01-01", "2024-04-01"),
    ("反弹期 2024-03~07", "2024-03-01", "2024-07-01"),
    ("2024 全年",          "2024-01-01", "2025-01-01"),
]
for label, s, e in windows:
    p("\n--- %s ---" % label)
    br, _ = rgv.nav_ret(base["ret"], s, e)
    p("  基准(合格域等权): %+.2f%%" % ((br or 0) * 100))
    for name, (res, st) in runs.items():
        sr, n = rgv.nav_ret(res["period_return"], s, e)
        if sr is None:
            p("  %-20s 窗口内无期数据(空仓)" % name)
        else:
            p("  %-20s 策略=%+7.2f%%  超额=%+7.2f%%  (n=%d期)" % (
                name, sr * 100, (sr - br) * 100 if br is not None else 0, n))

p("\n============ 4. 空仓期检测 (断路器清仓后踏空检查) ============")
for name, (res, st) in runs.items():
    # 2024 年内持仓数序列; 断路器清仓->该期无行或后续 n 重建
    seg24 = res[(res.index >= "2024-01-01") & (res.index <= "2024-12-31")]
    if len(seg24) == 0:
        p("%-20s 2024 全年无持仓记录" % name)
        continue
    p("%-20s 2024 期数=%d  持仓n轨迹=%s" % (
        name, len(seg24), [int(x) for x in seg24["n"].values]))

p("\n总用时 %.0fs" % (time.time() - rgv.t0))
out_path = os.path.join(RES, "c_window_stress_comparison.txt")
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("结果已写入:", out_path)

# 清理本脚本产生的临时风控状态文件 (不污染 P1-1 存档)
for fn in os.listdir(RES):
    if fn.startswith("grid_state_c_") and fn.endswith(".json"):
        os.remove(os.path.join(RES, fn))
print("临时状态文件已清理")
