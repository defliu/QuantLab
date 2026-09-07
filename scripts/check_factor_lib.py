# coding=utf-8
"""检查因子库结构（QuantLab 扁平布局 factors/）。

2026-08-29 重写：原版检查 `backtest/factors/zoo/{alpha101,gtja191}` 与
`backtest/factors/registry.py`，这三个路径在本仓库从未存在（是 E 盘旧开发机
的布局残留），脚本一跑就 FileNotFoundError。改为检查实际存在的因子库：

  - 因子模块：D:/QuantLab/factors/*.py
  - 基类契约：factors/base.py 的 FactorBase（name / category / compute）
  - 注册表  ：factors/engine.py 的 FactorEngine（register / list_factors）

用法: python scripts/check_factor_lib.py
"""
import importlib
import inspect
import os
import sys

QUANTLAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if QUANTLAB not in sys.path:
    sys.path.insert(0, QUANTLAB)

from factors.base import FactorBase
from factors.engine import FactorEngine

FACTORS_DIR = os.path.join(QUANTLAB, "factors")


def _is_factor_class(obj):
    return (inspect.isclass(obj) and issubclass(obj, FactorBase)
            and obj is not FactorBase)


def main():
    if not os.path.isdir(FACTORS_DIR):
        print("[FAIL] 因子目录不存在: %s" % FACTORS_DIR)
        return 1

    # base/engine 是基础设施（FactorBase / FactorEngine），不是因子模块
    _INFRA = ("base", "engine")
    modules = sorted(f[:-3] for f in os.listdir(FACTORS_DIR)
                     if f.endswith(".py") and not f.startswith("__")
                     and f[:-3] not in _INFRA)
    print("因子模块 %d 个: %s" % (len(modules), ", ".join(modules)))
    print("基础设施   : factors/base.py (FactorBase) / factors/engine.py (FactorEngine)\n")

    engine = FactorEngine()
    problems = []
    rows = []
    util_rows = []

    for mod_name in modules:
        try:
            mod = importlib.import_module("factors." + mod_name)
        except Exception as e:
            problems.append("%s: 导入失败 %s" % (mod_name, e))
            continue

        classes = [c for _, c in inspect.getmembers(mod, _is_factor_class)
                   if c.__module__ == "factors." + mod_name]
        if not classes:
            # 函数式工具模块（如 atr/roe/volatility/fina），不是 FactorBase 子类，
            # 不可注册进 FactorEngine，属正常形态 —— 列出公开函数，不静默跳过。
            pub = sorted(n for n, o in vars(mod).items()
                         if not n.startswith("_") and inspect.isfunction(o)
                         and getattr(o, "__module__", "") == "factors." + mod_name)
            util_rows.append((mod_name, ", ".join(pub) or "无公开函数"))
            continue

        for cls in classes:
            has_compute = callable(getattr(cls, "compute", None))
            # abstractmethod 未被实现时 __abstractmethods__ 非空
            unimplemented = sorted(getattr(cls, "__abstractmethods__", ()))
            try:
                inst = cls()
                name, category = inst.name, inst.category
                engine.register(inst)
            except Exception as e:
                name = category = "-"
                problems.append("%s.%s: 实例化/注册失败 %s" % (mod_name, cls.__name__, e))

            if not has_compute or "compute" in unimplemented:
                problems.append("%s.%s: 未实现 compute()" % (mod_name, cls.__name__))

            rows.append((mod_name, cls.__name__, name, category,
                         "OK" if has_compute and "compute" not in unimplemented else "缺失"))

    print("%-22s %-24s %-22s %-10s %s" % ("模块", "类", "name", "category", "compute"))
    print("-" * 92)
    for r in rows:
        print("%-22s %-24s %-22s %-10s %s" % r)

    if util_rows:
        print("\n函数式工具模块（非 FactorBase 子类，不可注册，属正常形态）:")
        for m, fns in util_rows:
            print("  %-16s %s" % (m, fns))

    print("\n已注册因子 (%d): %s" % (len(engine.list_factors()),
                                     ", ".join(engine.list_factors()) or "无"))

    if problems:
        print("\n问题 %d 项:" % len(problems))
        for p in problems:
            print("  [FAIL] %s" % p)
        return 1

    print("\n[PASS] 因子库结构检查通过：%d 个因子类全部符合 FactorBase 契约并可注册。"
          % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
