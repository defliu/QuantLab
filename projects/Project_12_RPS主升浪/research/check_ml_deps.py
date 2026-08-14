# coding: utf-8
"""检测 ML 依赖可用性。"""
mods = ["lightgbm", "sklearn", "numpy", "pandas", "pyarrow"]
for m in mods:
    try:
        mod = __import__(m)
        print("%s: OK (%s)" % (m, getattr(mod, "__version__", "?")))
    except ImportError as e:
        print("%s: MISSING (%s)" % (m, e))
