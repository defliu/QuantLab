# coding: utf-8
"""由 G2 信号内产版源码生成 ENS 源码（spec §4.3：逐字同构，仅标识/参数不同）。

替换清单（行为相关全部显式列出，防止遗漏）：
  标识: P16G2->P16ENS / bridge 目录 / G2_CODES->ENS_CODES / 文件头
  参数: 信号时点 09:50->09:31（ENS 换仓时点）/ deadline 09:58->09:39
        TOP_N 3->5（g2_ens_config）/ HOLD_DAYS 15->10（g2_ens_config）
  注释: g2_config.->g2_ens_config. / rebalance_g2.->rebalance_ens.
用法: python gen_ens_bridge_src.py
"""
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
G2 = os.path.join(HERE, "strategy", "strategy_p16_g2_bridge_src.py")
ENS = os.path.join(HERE, "strategy", "strategy_p16_ens_bridge_src.py")

# (old, new) 顺序敏感：参数行在前，标识在后
REPLACEMENTS = [
    # ---- 文件头 ----
    ("g2 大QMT 文件桥 · 内置执行器", "融合(ensemble) 大QMT 文件桥 · 内置执行器"),
    # ---- 信号参数（g2_ens_config 权威值）----
    ('SIGNAL_HM = "09:50"', 'SIGNAL_HM = "09:31"'),
    ('SIGNAL_DEADLINE_HM = "09:58"', 'SIGNAL_DEADLINE_HM = "09:39"'),
    ('SIGNAL_TOP_N = 3                  # g2_config.TOP_N（等权目标只数）',
     'SIGNAL_TOP_N = 5                  # g2_ens_config.TOP_N（等权目标只数，2026-09-21 拍板 2->5）'),
    ('SIGNAL_HOLD_DAYS = 15              # g2_config.HOLD_DAYS 持有期',
     'SIGNAL_HOLD_DAYS = 10              # g2_ens_config.HOLD_DAYS 持有期（对齐回测 N=10）'),
    ("# 信号时点（HH:MM，对齐原 09:50 G2 换仓任务）与执行窗口",
     "# 信号时点（HH:MM，对齐原 09:31 ENS 换仓任务）与执行窗口"),
    ("#   方案 §4.2 Step2：信号引擎内移到 handlebar 时间闸门（09:50 对齐原换仓时点），",
     "#   方案 §4.2 Step2 + §4.3：信号引擎内移到 handlebar 时间闸门（09:31 对齐原换仓时点），"),
    # ---- 注释中的口径引用（全局替换，覆盖 build_plan/_calc_tier/_count_trade_days 各处）----
    ("g2_config.", "g2_ens_config."),
    ("rebalance_g2.", "rebalance_ens."),
    # ---- 持仓白名单常量（ENS 实际白名单，g2_ens_config 口径）----
    ('G2_CODES = ("605098.SH", "000607.SZ")',
     'ENS_CODES = ("002841.SZ", "688758.SH", "600691.SH")'),
    ("G2_CODES 归一化", "ENS_CODES 归一化"),
    ("G2_CODES 白名单为空且 L3 账本无持票", "ENS_CODES 白名单为空且 L3 账本无持票"),
    ("position 接口返回 list + G2_CODES 白名单非空", "position 接口返回 list + ENS_CODES 白名单非空"),
    ("2) 白名单归一化（G2_CODES；空 = 完全被动）", "2) 白名单归一化（ENS_CODES；空 = 完全被动）"),
    ("for c in G2_CODES:", "for c in ENS_CODES:"),
    ("白名单合并 G2_CODES+L3 hold_dates", "白名单合并 ENS_CODES+L3 hold_dates"),
    # ---- 桥目录 / 日志前缀 / 模式标签 ----
    ('BRIDGE_DIR = "D:/QMT_POOL/g2_bridge"', 'BRIDGE_DIR = "D:/QMT_POOL/p16_ensemble_bridge"'),
    ("mode=自闭环信号内产(零文件) bridge=", "mode=自闭环信号内产(零文件) bridge="),
    # ---- ENS 候选口径说明（融合 rank 分数沙箱内不可得，按拍板用 B21 prob）----
    ("# 简化红线（拍板：内移版只带 prob + 简化红线；F2/F5/F6 沙箱内不可得）",
     "# 简化红线（拍板：内移版只带 prob + 简化红线；F2/F5/F6 沙箱内不可得）\n"
     "# ★ ENS 臂口径说明：外部管道的融合分 ens = rank(v3_enh 模型) 与 rank(G2) 均值，\n"
     "#   其中 v3_enh 43 特征含 22 个沙箱外特征（资金流/北向/板块），不可内移；\n"
     "#   按拍板「内移版只带 prob + 简化红线」，ENS 内移版同样用 B21 prob 排序。\n"
     "#   与外部融合分的差异由 s4-5e 信号对齐 diff 量化。"),
]

# 最后统一做前缀替换（P16G2 -> P16ENS）
PREFIX_REPL = [("P16G2", "P16ENS")]


def main():
    with io.open(G2, "r", encoding="utf-8") as f:
        src = f.read()
    miss = []
    for old, new in REPLACEMENTS:
        if old not in src:
            miss.append(old)
            continue
        src = src.replace(old, new)
    if miss:
        raise SystemExit("[ENS] 以下替换项在 G2 源中未找到（同步清单过期，须复核）:\n  " +
                         "\n  ".join(miss))
    for old, new in PREFIX_REPL:
        src = src.replace(old, new)
    # 防呆：残留 G2 专属标识
    for token in ("P16G2", "g2_bridge", "G2_CODES", "ENS_HOLDS"):
        if token in src:
            raise SystemExit("[ENS] 残留 G2 专属标识: %s" % token)
    with io.open(ENS, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("[ENS] 写出 %s  (%d bytes, %d lines)" % (
        ENS, len(src.encode("utf-8")), src.count("\n") + 1))
    print("[ENS] 参数: SIGNAL_HM=%s TOP_N=5 HOLD_DAYS=10" % "09:31")


if __name__ == "__main__":
    main()
