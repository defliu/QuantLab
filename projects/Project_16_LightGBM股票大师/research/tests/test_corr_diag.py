# -*- coding: utf-8 -*-
"""同批次选股相关性诊断 · 专项测试（纯函数层，不触碰 astock 数据）。

覆盖项 4 的核心判定语义：
  A. 批次日期三语义 + 未来日期副本防御
  B. 仅改日期的候选副本识别
  C. 行业集中度（单一行业告警 / 正常分布 / 分类缺失）
  D. 风格集中度（单桶告警 / 多桶正常 / 成分不可用 SKIP）
  E. 指数成分重叠
  F. 高相关资产簇（成簇告警 / 正常分散 / 质量筛查剔除）
  G. 汇总与确定性输出
  H. 阈值可覆盖

不依赖 pandas/numpy/astock，全部用纯 Python 桩驱动。
"""
import datetime as dt
import io
import math
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(PROJ, "research", "corr_diag_core.py")

PASS = [0]
FAIL = []
CRASH = [0]


def check(name, cond, extra=""):
    if cond:
        PASS[0] += 1
    else:
        FAIL.append("%s %s" % (name, extra))
        print("[FAIL] %s %s" % (name, extra))


def approx(a, b, tol=1e-6):
    # 任一侧为 None（字段缺失/显式 NA）一律判不通过，而不是抛 TypeError：
    # 专项测试的价值是「一次性暴露全部不满足的断言」，不是第一个就崩。
    if a is None or b is None:
        return False
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def call_style(codes, members, thresholds=None, **kw):
    """调用 style_concentration，把 TypeError 转成可断言的失败结果。

    新增的 members_date / asof_date 形参在生产实现落地前会直接抛
    TypeError。若不兜住，专项测试会在第一条断言就中断，
    看不到后面全部失败项——红测的价值恰恰是「一次列全」。
    """
    try:
        return CD.style_concentration(codes, members, thresholds, **kw)
    except TypeError as e:
        CRASH[0] += 1
        return {"status": "CRASH", "reasons": ["TypeError: %s" % e]}


def load_module():
    """加载核心模块为独立 ModuleType（与同项目其他专项测试一致）。
    模块级只读常量（_STYLE_NAMES 等）才能按真实语义解析。
    """
    with io.open(SRC, "r", encoding="utf-8") as f:
        src = f.read()
    mod = types.ModuleType("corr_diag_core_under_test")
    mod.__file__ = SRC
    exec(compile(src, SRC, "exec"), mod.__dict__)
    return mod


CD = load_module()


def math_sin(i):
    return math.sin(i * 0.7) * 0.02


def mk_returns(n_days, patterns):
    """构造 {code: [ret]}，patterns = {code: 'a'|'b'|'noise'|[float,...]|callable}。

    'noise' 模式必须**按代码区分**：早期版本只依赖日期索引 i，导致不同股票
    拿到逐位相同的序列，两只票之间相关系数恰为 1.0，凭空多出一个高相关簇
    ——那是夹具造出来的假结构，不是被测代码的判定问题。

    显式支持 list/tuple 透传：常量序列（全零 / 非零）必须按字面注入。
    早期版本没有这一支，传列表会掉进 else 兜底被填成全 0，于是
    「非零常量」测试实际拿到的是全零，意图与行为脱节。
    """
    out = {}
    for code, pat in patterns.items():
        seed = sum(ord(ch) * (k + 1) for k, ch in enumerate(str(code))) or 1
        if isinstance(pat, (list, tuple)):
            series = [float(v) for v in pat]
        else:
            series = []
            for i in range(n_days):
                if pat == "a":
                    v = [0.01, -0.02, 0.015, 0.005, -0.01, 0.02][i % 6]
                elif pat == "b":
                    v = [-0.01, 0.02, -0.015, -0.005, 0.01, -0.02][i % 6]
                elif pat == "noise":
                    v = ((i * 7919 + seed * 104729) % 23 - 11) / 1000.0
                elif callable(pat):
                    v = pat(i)
                else:
                    v = 0.0
                series.append(v)
        out[code] = series
    return out


# ---------------------------------------------------------------------------
print("=" * 70)
print("[A] 批次日期三语义 + 未来副本/行情滞后区分")

TODAY = "20261002"      # 判定「未来日期」的日历基准（测试显式注入，不取系统时间）
MKT_LAST = "20260911"  # 本地 astock 行情末日

# A1-A5：V1.3 真实形态——文件 20260930 / 行内快照 20260929。
# 20260930 晚于行情末日 20260911，但早于今天 20261002：
# 属「行情数据滞后」(WARN)，**不是**未来日期副本(FAIL)。
r = CD.resolve_batch_dates("20260930", ["20260929"], MKT_LAST, TODAY)
check("A1 文件日期解析", r["file_date"] == "20260930", r)
check("A2 行内日期独立保留", r["row_trade_date"] == "20260929", r)
check("A3 信号日=行内日期", r["signal_date"] == "20260929", r)
check("A4 合法批次不判 FAIL", r["status"] != CD.STATUS_FAIL, r)
check("A5 判为行情滞后 WARN", r["status"] == CD.STATUS_WARN, r)
check("A6 stale 标志置位", r["stale"] is True, r)
check("A7 快照差异留痕", any("不一致" in x for x in r["reasons"]), r["reasons"])
check("A8 滞后原因留痕", any("行情数据滞后" in x for x in r["reasons"]), r["reasons"])
check("A9 滞后不等于未来副本",
      not any("未来日期副本" in x for x in r["reasons"]), r["reasons"])

# A10-A12：G2/ENS 未来日期副本——20261008 晚于今天 20261002。
r = CD.resolve_batch_dates("20261008", ["20261008"], MKT_LAST, TODAY)
check("A10 未来日期副本 FAIL", r["status"] == CD.STATUS_FAIL, r)
check("A11 未来副本原因留痕", any("未来日期副本" in x for x in r["reasons"]),
      r["reasons"])
check("A12 文件批次未来被点名",
      any("文件批次日期" in x and "未来日期副本" in x for x in r["reasons"]),
      r["reasons"])

# A13-A14：行内日期晚于今天（文件日期正常，行内是未来）同样 FAIL。
r = CD.resolve_batch_dates("20260930", ["20261008"], MKT_LAST, TODAY)
check("A13 行内未来 FAIL", r["status"] == CD.STATUS_FAIL, r)
check("A14 行内未来被点名",
      any("行内 trade_date" in x and "未来日期副本" in x for x in r["reasons"]),
      r["reasons"])

# A15-A17：today 晚于行情末日时，合法批次不产生滞后告警。
r = CD.resolve_batch_dates("20260910", ["20260910"], MKT_LAST, TODAY)
check("A15 齐平批次无滞后", r["stale"] is False, r)
check("A16 齐平批次 status=OK", r["status"] == CD.STATUS_OK, r)
check("A17 齐平批次无滞后原因",
      not any("行情数据滞后" in x for x in r["reasons"]), r["reasons"])

# A18-A20：行内日期缺失 -> WARN + 回落文件日期。
r = CD.resolve_batch_dates("20260930", [None], MKT_LAST, TODAY)
check("A18 行内日期缺失有留痕", any("缺失" in x for x in r["reasons"]), r["reasons"])
check("A19 信号日回落文件日期", r["signal_date"] == "20260930", r)
check("A20 缺失判 WARN 非 FAIL", r["status"] == CD.STATUS_WARN, r)

# A21-A22：非法批次日期 -> FAIL。
r = CD.resolve_batch_dates("bad-date", ["20260930"], MKT_LAST, TODAY)
check("A21 非法批次日期 FAIL", r["status"] == CD.STATUS_FAIL, r)
check("A22 非法批次原因留痕", any("无法从文件名解析" in x for x in r["reasons"]),
      r["reasons"])

# A23-A24：同文件内多个不同 trade_date -> WARN（副本/拼接痕迹）。
r = CD.resolve_batch_dates("20260930", ["20260929", "20260930"], MKT_LAST, TODAY)
check("A23 多快照日 WARN", r["status"] == CD.STATUS_WARN, r)
check("A24 多快照日留痕", any("多个行内 trade_date" in x for x in r["reasons"]),
      r["reasons"])

# A25-A27：缺 today 时不做未来判定（不猜系统时间）。
r = CD.resolve_batch_dates("20261008", ["20261008"], MKT_LAST, None)
check("A25 无 today 不判未来副本",
      not any("未来日期副本" in x for x in r["reasons"]), r["reasons"])
check("A26 无 today 仍判滞后", r["status"] == CD.STATUS_WARN, r)
check("A27 无 today stale 置位", r["stale"] is True, r)

# A28-A31：日期解析工具
check("A28 parse 归一化", CD.parse_compact_date("2026-09-30") == "20260930")
check("A29 parse 长串截断", CD.parse_compact_date("20260930150000") == "20260930")
check("A30 parse 空值", CD.parse_compact_date("") is None)
check("A31 parse 非法", CD.parse_compact_date("abc") is None)

# A32-A41：日期对象与含时间字符串
# 2026-10-02 实测根因：astock parquet 的 MultiIndex（主行情 / 申万一级）
# 日期层是 pandas.Timestamp，str() 得到 '2026-09-30 00:00:00'，
# 旧实现只做字符串清洗 -> isdigit() 恒 False -> 静默返回 None，
# 行业映射/行情末日/收益轴同时失效且不报错。
# 核心模块不 import pandas，故用「有 strftime 的鸭子类型桩」覆盖同一分支，
# 再用标准库 datetime 交叉验证。
class _DateStub(object):
    """模拟 pandas.Timestamp：str() 带时间尾，但有 strftime。"""

    def __init__(self, y, m, d):
        self._y, self._m, self._d = y, m, d

    def strftime(self, fmt):
        if fmt == "%Y%m%d":
            return "%04d%02d%02d" % (self._y, self._m, self._d)
        raise ValueError("unsupported fmt")

    def __str__(self):
        return "%04d-%02d-%02d 00:00:00" % (self._y, self._m, self._d)


check("A32 parse 鸭子日期对象", CD.parse_compact_date(_DateStub(2026, 9, 30)) == "20260930")
check("A33 parse datetime.date", CD.parse_compact_date(dt.date(2026, 9, 30)) == "20260930")
check("A34 parse datetime.datetime", CD.parse_compact_date(dt.datetime(2026, 9, 30)) == "20260930")
check("A35 parse 空格时间串", CD.parse_compact_date("2026-09-30 00:00:00") == "20260930")
check("A36 parse ISO-T 时间串", CD.parse_compact_date("2026-09-30T00:00:00") == "20260930")
check("A37 parse 带空格紧凑串", CD.parse_compact_date("20260930 10:30") == "20260930")
check("A38 parse 年月日斜杠", CD.parse_compact_date("2026/09/30") == "20260930")
check("A39 parse 整数年月日", CD.parse_compact_date(20260930) == "20260930")
check("A40 parse 短串不误判", CD.parse_compact_date("2026") is None)
check("A41 parse None 不炸", CD.parse_compact_date(None) is None)

# A42：strftime 抛异常时必须退回字符串路径，而不是整体炸掉
class _BadStub(object):
    def strftime(self, fmt):
        raise ValueError("boom")

    def __str__(self):
        return "2026-09-30"


check("A42 parse strftime 异常回退", CD.parse_compact_date(_BadStub()) == "20260930")

# A43-A46：日期对象必须能驱动 resolve_batch_dates 的三条判定
# （与真实 IO 调用链同构：申万索引层 / 行情末日都是 Timestamp）
r = CD.resolve_batch_dates(dt.date(2026, 10, 8), [_DateStub(2026, 10, 8)], "20260911", "20261002")
check("A43 日期对象行内日判未来副本",
      any("未来日期副本" in x for x in r["reasons"]), r["reasons"])
check("A44 日期对象未来副本为 FAIL", r["status"] == CD.STATUS_FAIL, r)
r = CD.resolve_batch_dates(dt.date(2026, 9, 30), [_DateStub(2026, 9, 29)], "20260911", "20261002")
check("A45 日期对象行内日判滞后",
      r["status"] == CD.STATUS_WARN and r["stale"] is True, r)
check("A46 日期对象行内日不覆盖批次日", r["row_trade_date"] == "20260929", r)


# ---------------------------------------------------------------------------
print("[B] 仅改日期的候选副本识别")

base = [{"ts_code": "A", "score": "1.0", "trade_date": "2026-09-30"},
        {"ts_code": "B", "score": "2.0", "trade_date": "2026-09-30"}]
clone = [{"ts_code": "A", "score": "1.0", "trade_date": "2026-10-08"},
         {"ts_code": "B", "score": "2.0", "trade_date": "2026-10-08"}]
diff = [{"ts_code": "A", "score": "9.9", "trade_date": "2026-10-08"},
        {"ts_code": "B", "score": "2.0", "trade_date": "2026-10-08"}]
reorder = [{"ts_code": "B", "score": "2.0", "trade_date": "2026-10-08"},
           {"ts_code": "A", "score": "1.0", "trade_date": "2026-10-08"}]
short = [{"ts_code": "A", "score": "1.0", "trade_date": "2026-10-08"}]

check("B1 识别日期副本", CD.detect_date_clone(base, clone) is True)
check("B2 分数不同不算副本", CD.detect_date_clone(base, diff) is False)
check("B3 顺序不同不算副本", CD.detect_date_clone(base, reorder) is False)
check("B4 行数不同不算副本", CD.detect_date_clone(base, short) is False)
check("B5 空输入不算副本", CD.detect_date_clone([], clone) is False)
check("B6 空基准不算副本", CD.detect_date_clone(base, []) is False)


# ---------------------------------------------------------------------------
print("[C] 行业集中度")

codes10 = ["S%d" % i for i in range(10)]
ind_single = dict(("S%d" % i, "电子") for i in range(8))
ind_single["S8"] = "银行"
ind_single["S9"] = "食品饮料"
r = CD.industry_concentration(codes10, ind_single)
check("C1 单行业占比正确", approx(r["top1_ratio"], 0.8), r)
check("C2 单一行业告警", r["status"] == CD.STATUS_WARN, r)
check("C3 行业数正确", r["n_industries"] == 3, r)
check("C4 HHI 高", r["hhi"] > CD.THRESHOLDS["industry_hhi"], r)

ind_spread = {}
for i in range(10):
    ind_spread["S%d" % i] = "行业%d" % i
r = CD.industry_concentration(codes10, ind_spread)
check("C5 分散批次正常", r["status"] == CD.STATUS_OK, r)
check("C6 分散 top1=0.1", approx(r["top1_ratio"], 0.1), r)

ind_gap = dict(ind_spread)
ind_gap.pop("S9")
r = CD.industry_concentration(codes10, ind_gap)
check("C7 缺失 10% 不告警", r["status"] == CD.STATUS_OK, r)
check("C8 缺失数正确", r["n_missing"] == 1, r)
check("C9 缺失码记录", r["missing_codes"] == ["S9"], r)

ind_gap2 = dict(ind_spread)
for k in ["S7", "S8", "S9"]:
    ind_gap2.pop(k)
r = CD.industry_concentration(codes10, ind_gap2)
check("C10 缺失 30% 告警", r["status"] == CD.STATUS_WARN, r)
check("C11 缺失留痕", any("缺失占比" in x for x in r["reasons"]), r["reasons"])

r = CD.industry_concentration([], {})
check("C12 空候选 SKIP", r["status"] == CD.STATUS_SKIP, r)

r = CD.industry_concentration(codes10, {})
check("C13 全缺失 FAIL-safe 有留痕", r["n_missing"] == 10, r)
check("C14 全缺失不报正常", r["status"] != CD.STATUS_OK, r)

# C15：阈值覆盖生效。该分布同时触发「单一行业占比」和「HHI」两条线，
#      验证阈值必须能把**两条都**放宽才判 OK——只放宽一条不算覆盖生效。
r = CD.industry_concentration(codes10, ind_single,
                              thresholds={"industry_top1_ratio": 0.95,
                                          "industry_hhi": 1.0})
check("C15 阈值覆盖生效", r["status"] == CD.STATUS_OK, r)
# C15b：只放宽占比、HHI 仍按默认 0.25 判 -> 必须仍是 WARN（证明覆盖是逐项生效的）
r = CD.industry_concentration(codes10, ind_single,
                              thresholds={"industry_top1_ratio": 0.95})
check("C15b 未覆盖的 HHI 仍告警", r["status"] == CD.STATUS_WARN, r)


# ---------------------------------------------------------------------------
print("[D] 风格集中度")

# 名称表已在模块加载期由 STYLE_BENCHMARKS/BROAD_BENCHMARKS 建好（只读），
# 无需注册；此处直接验证名称解析可用。
check("D0 风格名可解析", CD._style_name("000918.CSI") == "300成长",
      CD._style_name("000918.CSI"))
check("D0b 宽基名可解析", CD._style_name("000300.SH") == "沪深300",
      CD._style_name("000300.SH"))
check("D0c 未知代码回落原值", CD._style_name("999999.XX") == "999999.XX",
      CD._style_name("999999.XX"))

sm = {
    "000918.CSI": set(["S0", "S1", "S2", "S3"]),
    "000919.CSI": set(["S4", "S5"]),
    "399630.SZ": set(["S6"]),
}
r = CD.style_concentration(codes10, sm)
check("D1 命中风格数正确", r["n_in_any_style"] == 7, r)
check("D2 无风格票数正确", r["n_no_style"] == 3, r)
check("D3 桶数正确", r["n_buckets"] == 3, r)
check("D4 分散风格正常", r["status"] == CD.STATUS_OK, r)

sm_single = {"000918.CSI": set(codes10)}
r = CD.style_concentration(codes10, sm_single)
check("D5 单风格桶告警", r["status"] == CD.STATUS_WARN, r)
check("D6 单桶占比 1.0", approx(r["bucket_top1_ratio"], 1.0), r)
check("D7 桶名含中文", "300成长" in (r["bucket_top1"] or ""), r)

r = CD.style_concentration(codes10, {})
check("D8 无成分数据 SKIP", r["status"] == CD.STATUS_SKIP, r)

r = CD.style_concentration(codes10, {"000918.CSI": set(), "000919.CSI": set(["S0"])})
check("D9 空成分集被忽略", r["n_buckets"] == 1, r)

# --- D10-D37：风格覆盖率、基准可用性与成分快照日期 ------------------------
# 真实批次审核发现三个实质缺口：
#   (1) V1.3 仅 1/10 命中风格桶却判 OK —— 覆盖率没有进入诊断结论；
#   (2) G2/ENS 零命中的原因文案写成「成分数据不可用」，但探针证明
#       6 个风格基准全部加载成功 —— 「无数据」和「有数据但没命中」被混淆；
#   (3) 风格成分 max_td=20260731 早于批次 20260930，但日期没有进入诊断。
# 阈值一律显式注入，避免在测试里偷偷决定生产默认值
# （默认阈值需真实批次 + 历史样本人工审核后才可定稿）。

# D10 夹具：4/10 命中、两桶各 2 只、top1=0.2 —— 除覆盖率外不触发任何告警，
#     所以「当前实现判 OK、修复后应 WARN」可被干净地隔离出来。
sm_low_cov = {
    "000918.CSI": set(["S0", "S1"]),
    "000919.CSI": set(["S2", "S3"]),
}
r = call_style(codes10, sm_low_cov)
check("D10 低覆盖桶分布不误触桶级告警",
      r["n_buckets"] == 2 and approx(r["bucket_top1_ratio"], 0.2), r)

# D11 覆盖率必须被显式算出并可被阈值比较
r = call_style(codes10, sm_low_cov, {"style_min_coverage": 0.5})
check("D11 覆盖率字段存在", approx(r.get("coverage_ratio"), 0.4), r)
check("D12 低覆盖判 WARN", r.get("status") == CD.STATUS_WARN, r)
check("D13 低覆盖原因留痕", any("覆盖率" in x for x in r.get("reasons")), r.get("reasons"))

# D14 阈值可注入是硬要求：同一个夹具在更宽松阈值下必须回到 OK，
#     否则等于把某个生产默认值焊死在测试里。
r = call_style(codes10, sm_low_cov, {"style_min_coverage": 0.3})
check("D14 覆盖率阈值可注入（宽松阈值不告警）", r.get("status") == CD.STATUS_OK, r)
check("D15 宽松阈值下不残留覆盖率原因",
      not any("覆盖率" in x for x in r.get("reasons")), r.get("reasons"))

# D16 风格诊断只允许 OK/WARN/SKIP，绝不允许 FAIL —— 它是同质化预警，
#     不是交易闸门；这条断言防止后续有人把覆盖率改成硬拦截。
r = call_style(codes10, sm_low_cov, {"style_min_coverage": 1.1})
check("D16 低覆盖永不 FAIL", r.get("status") in (CD.STATUS_OK, CD.STATUS_WARN), r)

# D17 数据可用但零命中：原因必须说「未命中」，不得说「不可用」
r = call_style(codes10, {"000918.CSI": set(["X1", "X2"])})
check("D17 数据可用标志为真", r.get("data_available") is True, r)
check("D18 零命中仍 SKIP", r.get("status") == CD.STATUS_SKIP, r)
check("D19 零命中原因不含不可用",
      not any("不可用" in x for x in r.get("reasons")), r.get("reasons"))
check("D20 零命中原因明确未命中",
      any("未命中任何风格基准" in x for x in r.get("reasons")), r.get("reasons"))

# D21 数据完全不可用：原因必须点名「不可用」，与零命中区分开
r = call_style(codes10, {})
check("D21 全缺标志为假", r.get("data_available") is False, r)
check("D22 全缺原因点名不可用",
      any("不可用" in x for x in r.get("reasons")), r.get("reasons"))

# D23 全部基准都是空成分集 == 数据不可用（不能冒充「有数据但零命中」）
r = call_style(codes10, {"000918.CSI": set(), "000919.CSI": set()})
check("D23 空成分集全缺判不可用", r.get("data_available") is False, r)
check("D24 空成分集全缺原因点名不可用",
      any("不可用" in x for x in r.get("reasons")), r.get("reasons"))

# D25 部分基准缺失：可用/缺失都要能被审计看到。
#     口径：missing 相对**官方 STYLE_BENCHMARKS 全表**统计，而不是「传入的键数」。
#     原因：IO 只在成功加载时写入 members 键，缺失的基准根本不会出现在入参里；
#     若按入参统计，生产里 missing 永远是 0，字段形同虚设。
r = call_style(
    codes10, {"000918.CSI": set(codes10), "000919.CSI": set()})
check("D25 可用基准数正确", r.get("n_benchmarks_loaded") == 1, r)
check("D26 缺失基准按官方全表统计",
      r.get("n_benchmarks_missing") == len(CD.STYLE_BENCHMARKS) - 1, r)
check("D26b 缺失基准可具名审计",
      "000919.CSI" in (r.get("missing_benchmarks") or []),
      r.get("missing_benchmarks"))
check("D27 部分缺失仍可诊断", r.get("data_available") is True, r)
# 缺失只留痕、不升级状态：上面 D1-D9 的夹具本身就是 6 个基准的子集，
# 若「缺基准」判 WARN，这批历史用例会被无声改成 WARN，等于用告警掩盖夹具事实。
check("D27b 基准缺失本身不升级状态",
      r.get("status") in (CD.STATUS_OK, CD.STATUS_WARN), r)

# D28 候选为空时不做覆盖率比较，也不编造覆盖率
r = call_style([], sm_low_cov)
check("D28 空候选 SKIP", r.get("status") == CD.STATUS_SKIP, r)
check("D29 空候选覆盖率为 NA",
      r.get("coverage_ratio", "MISSING") is None, r)

# D30-D34：风格成分快照日期。真实批次 20260930 但成分 max_td=20260731
#     （滞后 61 天），诊断结论必须把这一点显式说出来而不是默认可信。
#     滞后天数同样靠阈值注入，不在测试里钉死默认值。
STALE_TH = {"style_min_coverage": 0.3, "style_member_max_stale_days": 60}
r = call_style(
    codes10, sm_low_cov, STALE_TH,
    members_date="20260731", asof_date="20260930")
check("D30 成分快照日期被记录", r.get("members_date") == "20260731", r)
check("D30b 滞后天数被算出", r.get("stale_days") == 61, r)
check("D31 陈旧成分判 WARN", r.get("status") == CD.STATUS_WARN, r)
check("D32 陈旧成分原因留痕",
      any("滞后" in x for x in r.get("reasons") or []), r.get("reasons"))

# D33 滞后阈值可注入：同一天滞后 61 天，宽阈值下不应告警
r = call_style(
    codes10, sm_low_cov,
    {"style_min_coverage": 0.3, "style_member_max_stale_days": 90},
    members_date="20260731", asof_date="20260930")
check("D33 成分滞后阈值可注入", r.get("status") == CD.STATUS_OK, r)

# D34 新鲜成分（同日）不产生滞后告警
r = call_style(
    codes10, sm_low_cov, STALE_TH,
    members_date="20260930", asof_date="20260930")
check("D34 同日成分不告警滞后", r.get("status") == CD.STATUS_OK, r)

# D35 未提供成分日期 = 无从判断，不得编造滞后结论，也不得假装已验证新鲜
r = call_style(codes10, sm_low_cov, STALE_TH)
check("D35 缺日期不产生滞后原因",
      not any("滞后" in x for x in r.get("reasons") or []), r.get("reasons"))
check("D36 缺日期时 members_date 为 NA", r.get("members_date") is None, r)
check("D36b 缺日期时 stale_days 为 NA", r.get("stale_days") is None, r)

# D37 只给批次日、没给成分日 -> 同样不判定（避免单边推断）
r = call_style(
    codes10, sm_low_cov, STALE_TH, asof_date="20260930")
check("D37 单边日期不判定滞后",
      not any("滞后" in x for x in r.get("reasons") or []), r.get("reasons"))

# --- D38-D42：陈旧快照与 SKIP 分支同时命中时的状态优先级 --------------------
# 真实批次 20260930 的 G2/ENS 正是这个组合：成分快照 20260731（滞后 61 天）
# 且候选零命中。若实现让 WARN 覆盖 SKIP，读者会误以为「风格诊断已完成、
# 只是数据偏旧」，从而忽略真正该看的事实——这批候选根本没进任何风格基准。
# 约定：SKIP 优先于 WARN（SKIP = 本批次根本没有有效风格结论，更根本），
# 但陈旧原因必须同时保留，不能被 SKIP 分支吞掉。
STALE_TH_TIGHT = {"style_min_coverage": 0.3,
                  "style_member_max_stale_days": 10}

# D38 零命中 + 陈旧：状态取 SKIP
r = call_style(
    codes10, {"000918.CSI": set(["ZZZ"])}, STALE_TH_TIGHT,
    members_date="20260731", asof_date="20260930")
check("D38 零命中优先于陈旧告警", r.get("status") == CD.STATUS_SKIP, r)
# D39 陈旧原因不得被 SKIP 吞掉（否则快照偏旧这条信息就永久丢了）
check("D39 零命中+陈旧：陈旧原因保留",
      any("滞后" in x for x in r.get("reasons") or []), r.get("reasons"))
check("D40 零命中+陈旧：未命中原因也在",
      any("未命中任何风格基准" in x for x in r.get("reasons") or []),
      r.get("reasons"))
check("D41 零命中+陈旧：滞后天数仍记录",
      r.get("stale_days") == 61, r)

# D42 数据完全不可用 + 陈旧：同样 SKIP 优先，原因同样不吞
r = call_style(
    codes10, {}, STALE_TH_TIGHT,
    members_date="20260731", asof_date="20260930")
check("D42 不可用优先于陈旧告警", r.get("status") == CD.STATUS_SKIP, r)
check("D42b 不可用+陈旧：两条原因都在",
      any("不可用" in x for x in r.get("reasons") or []) and
      any("滞后" in x for x in r.get("reasons") or []), r.get("reasons"))


# ---------------------------------------------------------------------------
print("[E] 指数成分重叠")

im = {
    "000300.SH": set(["S0", "S1", "S2", "S3", "S4", "S5", "S6"]),
    "000905.SH": set(["S3", "S4", "S5", "S6", "S7"]),
    "000852.SH": set(["S0", "S8", "S9"]),
}
r = CD.index_overlap(codes10, im)
check("E1 覆盖统计正确", r["coverage"]["000300.SH"]["n"] == 7, r)
check("E2 占比计算正确", approx(r["coverage"]["000300.SH"]["ratio"], 0.7), r)
check("E3 top1 指数正确", r["top1_index"] == "000300.SH", r)
check("E4 高重叠告警", r["status"] == CD.STATUS_WARN, r)

im_low = {"000300.SH": set(["S0", "S1"]), "000905.SH": set(["S2", "S3"])}
r = CD.index_overlap(codes10, im_low)
check("E5 低重叠正常", r["status"] == CD.STATUS_OK, r)

r = CD.index_overlap(codes10, {})
check("E6 无成分 SKIP", r["status"] == CD.STATUS_SKIP, r)

ov = CD.pairwise_batch_overlap(["A", "B", "C"], ["B", "C", "D"])
check("E7 跨批重叠交集", ov["n_common"] == 2, ov)
check("E8 Jaccard 正确", approx(ov["jaccard"], 0.5), ov)
check("E9 空输入返回 None", CD.pairwise_batch_overlap([], ["A"]) is None)


# ---------------------------------------------------------------------------
print("[F] 高相关资产簇")

# F1：三只完全同向 -> 成簇
rets = mk_returns(60, {"A": "a", "B": "a", "C": "a", "D": "noise", "E": "noise"})
r = CD.correlation_clusters(rets)
check("F1 成簇告警", r["status"] == CD.STATUS_WARN, r)
check("F2 最大簇规模 3", r["largest_cluster"] == 3, r)
check("F3 簇数正确", r["n_clusters"] == 1, r)
check("F4 最大相关接近 1", r["max_corr"] is not None and r["max_corr"] > 0.99, r)
check("F5 全部通过筛查", r["n_passed"] == 5, r)

# F2：完全独立 -> 正常
rets = mk_returns(60, {"A": lambda i: math_sin(i), "B": lambda i: math_sin(i + 1.3),
                       "C": lambda i: math_sin(i + 2.6), "D": math_sin,
                       "E": lambda i: math_sin(i + 4.2)})
r = CD.correlation_clusters(rets)
check("F6 分散批次无大簇", r["largest_cluster"] < 3, r)
check("F7 分散批次 OK", r["status"] == CD.STATUS_OK, r)

# F3：全零序列（停牌）-> 质量剔除。
# 注意：全零序列同时满足「零收益占比 100%」和「标准差为 0」两个条件，
# 质量筛查按优先级先报零收益比例（停牌）——这是设计内行为。
# 因此 std 分支必须用一个**非零常量**序列单独验证。
rets = mk_returns(60, {"A": "a", "B": "a", "C": [0.0] * 60, "D": "noise"})
r = CD.correlation_clusters(rets)
check("F8 常量序列剔除", r["n_rejected"] == 1, r)
check("F9 全零序列报停牌",
      any("停牌" in q["reason"] for q in r["quality"] if not q["passed"]),
      r["quality"])
check("F10 剔除后仍可诊断", r["n_passed"] == 3, r)

# F3b：非零常量序列（零收益占比 0%，但标准差为 0）-> 走 std 分支报「常量」
rets = mk_returns(60, {"A": "a", "B": "a", "C": [0.01] * 60, "D": "noise"})
r = CD.correlation_clusters(rets)
check("F8b 非零常量剔除", r["n_rejected"] == 1, r)
check("F9b 非零常量报常量",
      any("常量" in q["reason"] for q in r["quality"] if not q["passed"]),
      r["quality"])
check("F10b 非零常量零收益占比为 0",
      any(approx(q["zero_ratio"], 0.0) for q in r["quality"] if q["code"] == "C"),
      r["quality"])

# F4：样本不足 -> 剔除
short = {"A": [0.01, -0.02] * 5, "B": [0.02, -0.01] * 5, "C": "x"}
rets = {"A": short["A"], "B": short["B"],
        "C": mk_returns(60, {"C": "noise"})["C"]}
r = CD.correlation_clusters(rets)
check("F11 样本不足剔除", r["n_rejected"] == 2, r)
check("F12 剔除原因含样本", any("样本" in q["reason"]
                              for q in r["quality"] if not q["passed"]), r["quality"])

# F5：零收益占比高（停牌）
rets = mk_returns(60, {"A": "a", "B": "a"})
rets["C"] = [0.0] * 45 + [0.01, -0.02, 0.015, 0.005, -0.01,
                          0.02, 0.01, -0.01, 0.02, -0.02,
                          0.01, 0.01, -0.01, 0.01, -0.01]
r = CD.correlation_clusters(rets)
check("F13 高零收益剔除", r["n_rejected"] == 1, r)
check("F14 停牌原因", any("停牌" in q["reason"]
                          for q in r["quality"] if not q["passed"]), r["quality"])

# F6：含 None 缺口
rets = mk_returns(60, {"A": "a", "B": "a", "C": "noise"})
rets["A"][3] = None
rets["A"][7] = None
r = CD.correlation_clusters(rets)
check("F15 None 缺口仍可算", r["n_passed"] == 3, r)
check("F16 缺口不误剔除", r["status"] in (CD.STATUS_OK, CD.STATUS_WARN), r)

# F7：单只票
r = CD.correlation_clusters(mk_returns(60, {"A": "noise"}))
check("F17 单只票 SKIP", r["status"] == CD.STATUS_SKIP, r)

# F8：质量阈值覆盖
rets = mk_returns(60, {"A": "a", "B": "a", "C": "a"})
r = CD.correlation_clusters(rets, thresholds={"corr_pair": 1.5})
check("F19 阈值放宽后不成簇", r["largest_cluster"] < 3, r)

# F9：成簇规模阈值
r = CD.correlation_clusters(rets, thresholds={"corr_cluster_min": 5})
check("F20 簇规模阈值生效", r["status"] != CD.STATUS_WARN, r)

# F10：确定性
r1 = CD.correlation_clusters(mk_returns(60, {"A": "a", "B": "a", "C": "a", "D": "noise"}))
r2 = CD.correlation_clusters(mk_returns(60, {"A": "a", "B": "a", "C": "a", "D": "noise"}))
check("F21 重复调用确定性", r1["pairs"] == r2["pairs"], "")
check("F22 簇输出稳定", r1["clusters"] == r2["clusters"], "")


# ---------------------------------------------------------------------------
print("[G] 汇总 diagnose")

def staggered(i):
    def _f(j):
        return ((j * 37 + i * 11) % 17 - 8) / 1000.0
    return _f


full = dict(
    ind_map=ind_spread,
    style_members=sm,
    index_members=im_low,
    rets=mk_returns(60, dict(("S%d" % i, staggered(i)) for i in range(10))),
)
r = CD.diagnose(codes10, **full)
check("G1 汇总含四维度", set(["industry", "style", "index", "correlation"]).issubset(
    set(r["sections"].keys())), list(r["sections"].keys()))
check("G2 overall 存在", r["overall"] in
      (CD.STATUS_OK, CD.STATUS_WARN, CD.STATUS_FAIL, CD.STATUS_SKIP), r["overall"])
check("G3 阈值随输出", r["thresholds"]["corr_window"] == 60, r)

r = CD.diagnose(["A", "B"], ind_map={}, style_members={}, index_members={}, rets={})
check("G4 候选不足 SKIP", r["overall"] == CD.STATUS_SKIP, r)
check("G5 不足时不产出四维度", "_skip" in r["sections"], list(r["sections"].keys()))

bad = CD.diagnose(codes10, ind_map=ind_single, style_members=sm_single,
                  index_members=im, rets=mk_returns(
                      60, dict((c, "a") for c in codes10)))
check("G6 全面告警= WARN", bad["overall"] == CD.STATUS_WARN, bad["overall"])
check("G7 告警原因分维度", any(x.startswith("[industry]") for x in bad["reasons"]),
      bad["reasons"])
check("G8 告警原因含风格", any(x.startswith("[style]") for x in bad["reasons"]),
      bad["reasons"])
check("G9 告警原因含相关", any(x.startswith("[correlation]") for x in bad["reasons"]),
      bad["reasons"])

r1 = CD.diagnose(codes10, **full)
r2 = CD.diagnose(codes10, **full)
check("G10 汇总确定性", r1["sections"] == r2["sections"], "")

# G11-G15：状态聚合语义（缺数据不得静默判正常）
check("G11 全 OK -> OK",
      CD.aggregate_status([CD.STATUS_OK] * 4) == CD.STATUS_OK)
check("G12 全 SKIP -> SKIP（缺数据≠正常）",
      CD.aggregate_status([CD.STATUS_SKIP] * 4) == CD.STATUS_SKIP)
check("G13 OK+SKIP 混合 -> WARN",
      CD.aggregate_status([CD.STATUS_OK, CD.STATUS_SKIP,
                           CD.STATUS_OK, CD.STATUS_OK]) == CD.STATUS_WARN)
check("G14 FAIL 压过 WARN",
      CD.aggregate_status([CD.STATUS_FAIL, CD.STATUS_WARN,
                           CD.STATUS_SKIP, CD.STATUS_OK]) == CD.STATUS_FAIL)
check("G15 空输入 -> SKIP", CD.aggregate_status([]) == CD.STATUS_SKIP)

# G16-G20：日期状态并入整体结论
r = CD.diagnose(codes10, date_info=CD.resolve_batch_dates(
    "20260930", ["20260929"], MKT_LAST, TODAY), **full)
check("G16 日期段进入结论", r["overall"] in
      (CD.STATUS_OK, CD.STATUS_WARN), r["overall"])
check("G17 section_status 含 date", "date" in r.get("section_status", {}),
      list(r.get("section_status", {}).keys()))
check("G18 日期原因带前缀", any(x.startswith("[date]") for x in r["reasons"]),
      r["reasons"])

# 日期 FAIL 必须压过四维度全部 OK
r = CD.diagnose(codes10, date_info=CD.resolve_batch_dates(
    "20261008", ["20261008"], MKT_LAST, TODAY), **full)
check("G19 未来副本日期压过四维度 OK", r["overall"] == CD.STATUS_FAIL,
      r["overall"])
check("G20 未来副本原因透出",
      any("未来日期副本" in x for x in r["reasons"]), r["reasons"])

# 候选不足时，日期 FAIL 仍须透出（不能被 _skip 吞掉）
r = CD.diagnose(["A", "B"], date_info=CD.resolve_batch_dates(
    "20261008", ["20261008"], MKT_LAST, TODAY))
check("G21 候选不足+日期FAIL=FAIL", r["overall"] == CD.STATUS_FAIL, r["overall"])
check("G22 候选不足仍留日期原因",
      any("未来日期副本" in x for x in r["reasons"]), r["reasons"])

# 无 date_info 时不影响原有四维度结论
r = CD.diagnose(codes10, **full)
check("G23 无 date_info 不引入 date 段",
      "date" not in r.get("section_status", {}), list(r.get("section_status", {}).keys()))

# --- G24-G29：风格成分日期经 diagnose() 透传到 style 段 --------------------
# 这是真实批次审核缺口的收口点：core 的 style_concentration 已能算覆盖率与
# 滞后天数，但若 diagnose() 不把日期传下去，运行器永远拿不到这两个字段，
# 报告里就会静默缺失（此前正是这个症状）。
STYLE_DIAG_TH = {"min_candidates": 5, "style_min_coverage": 0.3,
                 "style_member_max_stale_days": 10}

# G24 diagnose 传了成分日期 -> 风格段能读到 members_date
# 用 dict(full, ...) 覆盖 style_members：full 自带 sm，直接并列传会撞键。
_diag_low_cov = dict(
    full, style_members=sm_low_cov, thresholds=STYLE_DIAG_TH,
    style_members_date="20260731",
    date_info=CD.resolve_batch_dates(
        "20260930", ["20260930"], MKT_LAST, TODAY))
r = CD.diagnose(codes10, **_diag_low_cov)
sty = r.get("sections", {}).get("style") or {}
check("G24 成分日期透传到风格段",
      sty.get("members_date") == "20260731", sty)
# G25 批次日取自 date_info.signal_date，调用方无需重复传
check("G25 批次日自动取自 signal_date",
      sty.get("asof_date") == "20260930", sty)
check("G26 滞后天数在聚合层算出", sty.get("stale_days") == 61, sty)
check("G27 陈旧在整体结论生效", r["overall"] == CD.STATUS_WARN, r["overall"])
check("G28 陈旧原因带 [style] 前缀",
      any(x.startswith("[style]") and "滞后" in x for x in r["reasons"]),
      r["reasons"])

# G29 不传成分日期时不得凭空推断（stale_days 保持 NA，不得造数）
_diag_no_mdate = dict(
    full, style_members=sm_low_cov, thresholds=STYLE_DIAG_TH,
    date_info=CD.resolve_batch_dates(
        "20260930", ["20260930"], MKT_LAST, TODAY))
r = CD.diagnose(codes10, **_diag_no_mdate)
sty = r.get("sections", {}).get("style") or {}
check("G29 未传成分日期则滞后为 NA", sty.get("stale_days") is None, sty)

# G30 有 date_info 但无成分日期：asof_date 仍记录（批次日是已知事实），
#     只是无法计算滞后——两者不能混为一谈。
check("G30 无成分日期时 asof_date 仍记录",
      sty.get("asof_date") == "20260930", sty)
check("G31 无成分日期不产生滞后原因",
      not any("滞后" in x for x in sty.get("reasons") or []),
      sty.get("reasons"))


# ---------------------------------------------------------------------------
print("=" * 70)
# 崩溃兜底不能变成假阳性来源：call_style 捕获 TypeError 后返回的 CRASH 结果里
# reasons 不含「滞后」等关键词，D35/D36/D36b/D37 这类「不应出现某文案」的断言
# 会在崩溃状态下被误判为通过。这里把崩溃本身统一记为失败。
check("Z0 风格调用无崩溃", CRASH[0] == 0, CRASH[0])
print("PASS=%d FAIL=%d" % (PASS[0], len(FAIL)))
if FAIL:
    print("失败清单:")
    for f in FAIL:
        print("  - %s" % f)
print("[CORR-DIAG-TEST-DONE]")
raise SystemExit(1 if FAIL else 0)
