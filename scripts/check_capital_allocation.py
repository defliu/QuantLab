#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
资金分配总表校验器（机器可校验的单一事实源）

规则：
  1) sum(各策略 capital_base) <= account.total_capital   （账户总额约束）
  2) 每个策略独立 config 里的 capital_base 必须与本总表一致

运行：
  python scripts/check_capital_allocation.py
退出码：
  0 = 通过；1 = 违反约束；2 = 环境/文件错误
"""
import os
import sys

try:
    import yaml
except ImportError:
    sys.stderr.write("[FAIL] PyYAML 未安装，请先: pip install pyyaml\n")
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ALLOC_FILE = os.path.join(ROOT, "config", "capital_allocation.yaml")


def _load_strategy_capital(cfg_path):
    """从策略独立 config 读 capital_base（兼容 strategy.capital_base 或顶层）。"""
    try:
        with open(cfg_path, encoding="utf-8") as f:
            c = yaml.safe_load(f)
    except Exception as e:
        return None, "读取失败: %s" % e
    if not isinstance(c, dict):
        return None, "格式异常(非 dict)"
    if "strategy" in c and isinstance(c["strategy"], dict):
        return c["strategy"].get("capital_base"), None
    return c.get("capital_base"), None


def main():
    if not os.path.exists(ALLOC_FILE):
        sys.stderr.write("[FAIL] 找不到资金分配总表: %s\n" % ALLOC_FILE)
        sys.exit(2)
    with open(ALLOC_FILE, encoding="utf-8") as f:
        alloc = yaml.safe_load(f)

    account = alloc.get("account", {}) or {}
    total = account.get("total_capital")
    acct_id = account.get("id", "?")
    strategies = alloc.get("strategies", []) or []

    errors = []
    warnings = []

    if total is None:
        warnings.append(
            "account.total_capital 未填 -> 暂不校验上限。"
            "请在国金QMT客户端查「总资产」后填入 config/capital_allocation.yaml"
        )
    else:
        try:
            total = float(total)
        except Exception:
            errors.append("account.total_capital 不是合法数字: %r" % total)
            total = None

    total_alloc = 0.0
    print("账户: %s" % acct_id)
    print("%-30s %12s %12s  状态" % ("策略", "锁定本金", "账户占比"))
    print("-" * 72)
    for s in strategies:
        key = s.get("key", "?")
        name = s.get("name", key)
        cap = s.get("capital_base")
        if cap is None:
            errors.append("策略 %s 缺少 capital_base" % key)
            continue
        try:
            cap = float(cap)
        except Exception:
            errors.append("策略 %s 的 capital_base 不是数字: %r" % (key, cap))
            continue
        total_alloc += cap
        pct_s = ("%.1f%%" % (cap / total * 100)) if total else "N/A"

        cfg_file = s.get("config_file")
        if cfg_file:
            cfg_path = cfg_file if os.path.isabs(cfg_file) else os.path.join(ROOT, cfg_file)
            if os.path.exists(cfg_path):
                real_cap, err = _load_strategy_capital(cfg_path)
                if err:
                    warnings.append("%s: 独立config读取异常 %s" % (key, err))
                elif real_cap is not None and abs(float(real_cap) - cap) > 1e-6:
                    errors.append(
                        "%s: 总表 capital_base=%s 但 %s 里=%s（不一致！）"
                        % (key, cap, cfg_file, real_cap)
                    )
            else:
                warnings.append("%s: 独立config %s 不存在（跳过一致性检查）" % (key, cfg_file))

        print("%-30s %12.0f %12s  OK" % (name[:30], cap, pct_s))

    print("-" * 72)
    print("已分配合计: %.0f 元" % total_alloc)
    if total is not None:
        remain = total - total_alloc
        print("账户总额:   %.0f 元" % total)
        print("剩余可分配: %.0f 元 (%.1f%%)" % (remain, (remain / total * 100) if total else 0))
        if total_alloc > total + 1e-6:
            errors.append(
                "违反账户总额约束: 已分配 %.0f > 账户总额 %.0f" % (total_alloc, total)
            )

    print("")
    if warnings:
        print("[WARN]")
        for w in warnings:
            print("  - %s" % w)
    if errors:
        print("[FAIL] 资金分配校验未通过:")
        for e in errors:
            print("  - %s" % e)
        sys.exit(1)
    print("[PASS] 资金分配总表自洽，未违反账户总额约束。")


if __name__ == "__main__":
    main()
