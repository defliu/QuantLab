# coding: utf-8
"""Strategy Registry —— QuantLab 扁平版（最简装饰器，不引入插件框架）。

策略模块位于 strategy/<name>.py，用 @register_strategy("<name>") 在模块顶层
注册 evaluate_day。autodiscover 自动扫描 strategy/ 包下所有模块并触发注册，
但跳过已知非引擎模块（base/registry/schedule 及 legacy multi_factor 等），
且每个模块的导入都包在 try/except 中，单模块失败不影响整体。
"""
import importlib
import logging
import pkgutil

log = logging.getLogger(__name__)

_REGISTRY = {}

# 已知非 target-weight 引擎模块：autodiscover 跳过，避免重依赖/慢导入。
_AUTODISCOVER_SKIP = {
    "registry", "schedule", "base", "__init__",
    "multi_factor", "ml_strategy", "portfolio",
}


def register_strategy(name):
    """装饰器：在策略模块顶层注册 evaluate_day。"""
    def _wrap(evaluate_fn):
        if name in _REGISTRY:
            raise ValueError("strategy already registered: " + name)
        _REGISTRY[name] = evaluate_fn
        return evaluate_fn
    return _wrap


def get_strategy(name):
    if name not in _REGISTRY:
        raise KeyError(
            "strategy not found: " + name +
            "; registered: " + ",".join(sorted(_REGISTRY.keys()))
        )
    return _REGISTRY[name]


def list_strategies():
    return sorted(_REGISTRY.keys())


def get_strategy_diag(decision, strategy_name, key, default=None):
    """从 decision.diagnostics.strategy_specific.{short_name}.{key} 取值。"""
    short = strategy_name.split("/")[-1] if strategy_name else ""
    return (decision.get("diagnostics", {})
                    .get("strategy_specific", {})
                    .get(short, {})
                    .get(key, default))


import contextlib


def register_test_spy(name, fn):
    """测试辅助：直接向 registry 注入临时 fn，返回前一版本供还原。"""
    old = _REGISTRY.get(name)
    _REGISTRY[name] = fn
    return old


@contextlib.contextmanager
def strategy_spy(name, fn=None):
    """测试辅助：上下文管理器，临时替换 registry 中 name 对应的 evaluate_day。

    用法 1（替换已注册策略，spy 包装原 fn）:
        with strategy_spy("atr_lowvol") as (real_fn, spy_fn): ...
    用法 2（注入新临时策略 fn，退出时删除）:
        with strategy_spy("test/my_spy", fn=my_evaluate_day) as (None, my_evaluate_day): ...
    """
    existed = name in _REGISTRY
    real = _REGISTRY.get(name)
    injected = fn if fn is not None else real
    if injected is None:
        raise KeyError("strategy_spy: name=%r not registered and fn not given" % name)
    captured = [None]

    def _spy(*args, **kwargs):
        captured[0] = args
        return injected(*args, **kwargs)

    _REGISTRY[name] = _spy
    try:
        yield real, _spy
    finally:
        if existed:
            _REGISTRY[name] = real
        else:
            _REGISTRY.pop(name, None)


def _autodiscover():
    """扫描 strategy/ 包触发所有 @register_strategy 装饰器。"""
    try:
        pkg = importlib.import_module("strategy")
    except Exception as e:
        log.warning("strategy autodiscover: cannot import 'strategy' pkg: %s", e)
        return
    for _, modname, ispkg in pkgutil.iter_modules(pkg.__path__):
        if ispkg:
            continue
        if modname in _AUTODISCOVER_SKIP:
            continue
        try:
            importlib.import_module("strategy." + modname)
        except Exception as e:
            log.warning("strategy autodiscover skip %s: %s", modname, e)


_autodiscover()
