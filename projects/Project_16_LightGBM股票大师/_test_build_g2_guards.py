# coding: utf-8
"""build_g2_daily / 面板合并 数据口径回归。

钉住 2026-10-31 的三处修复，防止被静默回退：
  ① target 取「主库/增量库较新者」——增量库断链时不能把主库已更新的交易日截掉
  ② amount 统一到千元（训练面板口径）——主库千元 vs 增量库元，差 1000 倍
  ③ 训练宇宙对齐——universe_all_a enabled ∩ is_st==0 ∩ 非 688 ∩ 非 4/8 开头
另钉住慢变量 asof 目标日（防回填前视）与 F5 走合并行情（防增量库缺席时静默回退）。

运行：python _test_build_g2_guards.py
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PASS = []
FAIL = []


def _src(name):
    return io.open(os.path.join(HERE, name), encoding="utf-8").read()


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("PASS" if cond else "FAIL", name, (" | " + detail) if detail else ""))


def main():
    g2 = _src("build_g2_daily.py")

    # ① target
    ok("g2.target_max_main_incr", "target = max(main_max, incr_max)" in g2)
    ok("g2.no_incr_only_target",
       'target = pd.Timestamp(args.date) if args.date else incr["trade_date"].max()' not in g2)
    ok("g2.incr_optional", "if os.path.exists(INCR):" in g2)

    # ② amount 千元
    ok("g2.amount_qian", 'incr["amount"].astype(float) / 1000.0' in g2)
    ok("g2.amount_comment", "千元" in g2)

    # ③ 宇宙
    ok("g2.universe_loaded", "uni_codes" in g2 and "DC.UNIVERSE" in g2)
    ok("g2.exclude_688_bj", 'startswith("688")' in g2 and 'startswith(("4", "8"))' in g2)
    ok("g2.exclude_st", 'main_df["is_st"] == 0' in g2)

    # 慢变量 asof 目标日（防前视）
    ok("g2.main_last_asof", 'main_df["trade_date"] <= target' in g2)
    ok("g2.no_full_last", 'main_df.sort_values(["ts_code", "trade_date"]).groupby("ts_code").tail(1)' not in g2)

    # F5 走合并行情
    ok("g2.f5_from_merged", 'day_df = merged[merged["trade_date"] == target]' in g2)
    ok("g2.f5_preclose", 'day_df["preClose"]' in g2)

    # 验证出口
    ok("g2.out_arg", 'ap.add_argument("--out"' in g2)

    # 面板合并同口径
    pv = _src("refresh_panel_v3.py")
    ok("panel.amount_qian", 'incr["amount"].astype(float) / 1000.0' in pv)
    ok("panel.incr_after_main", 'incr = incr[incr["trade_date"] > main_max]' in pv)

    ml = _src("merge_live_features.py")
    ok("merge.amount_qian", 'incr["amount"].astype(float) / 1000.0' in ml)
    ok("merge.incr_after_main", 'incr = incr[incr["trade_date"] > main_max]' in ml)

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
