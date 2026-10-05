# -*- coding: utf-8 -*-
"""同批次选股相关性诊断 · 报告层专项测试。

锁 corr_diag_report.py 的语义：
  A. 参数防御：generated_at 必须显式注入、result 类型校验
  B. payload 字段完整性（含 date 段字段名与 core 输出一致）
  C. 确定性：同输入重复输出逐字节一致、dict 键序无关、集合/配对/簇排序稳定
  D. Markdown 渲染：NA 显式输出、缺失维度不崩、声明句存在
  E. 原子写：正常写、覆盖写、目录自建、失败抛出、临时文件不残留
  F. write_reports 端到端：双产物落盘 + 可回读 JSON
  G. 真实批次端到端（真实数据，数据缺失则 SKIP）

与 test_corr_diag.py（核心）/ test_corr_diag_io.py（IO）互补。
"""
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, "..", ".."))
RESEARCH = os.path.join(PROJ, "research")
SRC = os.path.join(RESEARCH, "corr_diag_report.py")

PASS = [0]
FAIL = []
SKIPPED = []


def check(name, cond, extra=""):
    if cond:
        PASS[0] += 1
    else:
        FAIL.append("%s %s" % (name, extra))
        print("[FAIL] %s %s" % (name, extra))


def skip(name, why):
    SKIPPED.append("%s (%s)" % (name, why))
    print("[SKIP] %s -- %s" % (name, why))


if RESEARCH not in sys.path:
    sys.path.insert(0, RESEARCH)

with io.open(SRC, "r", encoding="utf-8") as f:
    _rep_src = f.read()
REP = types.ModuleType("corr_diag_report_under_test")
REP.__file__ = SRC
exec(compile(_rep_src, SRC, "exec"), REP.__dict__)

import corr_diag_core as core  # noqa: E402
import corr_diag_io as cio  # noqa: E402

OK, WARN, FAIL_S, SKIP_S = (core.STATUS_OK, core.STATUS_WARN,
                             core.STATUS_FAIL, core.STATUS_SKIP)

TMP = tempfile.mkdtemp(prefix="corr_diag_report_")
GEN_AT = "2026-10-02T09:00:00"


def _codes(n=8):
    return ["60000%d.SH" % i for i in range(n)]


def mk_result():
    """构造一个覆盖全部维度的 result（结构对齐 core.diagnose + io）。"""
    codes = ["600001.SH", "600002.SH", "600003.SH",
             "000001.SZ", "000002.SZ"]
    return {
        "codes": codes,
        "n_candidates": len(codes),
        "thresholds": dict(core.THRESHOLDS),
        "sections": {
            "industry": {
                "status": WARN,
                "reasons": ["第一大行业 占比超阈值"],
                "n_mapped": 5, "n_missing": 0,
                "missing_ratio": 0.0, "missing_codes": [],
                "n_industries": 2,
                "top1_industry": "银行", "top1_ratio": 0.6, "hhi": 0.4,
                "distribution": {"银行": 3, "电子": 2},
            },
            "style": {
                "status": OK,
                "reasons": [],
                "n_in_any_style": 4, "n_no_style": 1,
                "no_style_codes": ["000002.SZ"],
                "n_buckets": 2, "bucket_top1": "300成长",
                "bucket_top1_ratio": 0.6,
                "bucket_counts": {"300成长": 3, "300价值": 1},
                # 以下为 core.style_concentration 的真实字段，
                # 报告层若不投影，读者无法判断「风格不可用」与「零命中」
                "coverage_ratio": 0.8,
                "data_available": True,
                "n_benchmarks_loaded": 9, "n_benchmarks_missing": 0,
                "missing_benchmarks": [], "extra_benchmarks": [],
                "members_date": "20260630",
                "asof_date": "20260929",
                "stale_days": 91,
            },
            "index": {
                "status": WARN,
                "reasons": ["第一大宽基占比超阈值"],
                "n_indexes": 2,
                "top1_index": "000300.SH",
                "top1_index_name": "沪深300",
                "top1_ratio": 0.8,
                "coverage": {
                    "000300.SH": {"name": "沪深300", "n": 4, "ratio": 0.8},
                    "000905.SH": {"name": "中证500", "n": 1, "ratio": 0.2},
                },
            },
            "correlation": {
                "status": WARN,
                "reasons": ["存在高相关资产簇"],
                "window": 60, "n_input": 5, "n_passed": 4, "n_rejected": 1,
                "n_clusters": 1, "largest_cluster": 2,
                "max_corr": 0.92, "mean_corr": 0.75,
                "clusters": [["600002.SH", "600001.SH"]],
                "quality": [
                    {"code": "600001.SH", "n": 60, "zero_ratio": 0.0,
                     "std": 0.021, "passed": True, "reason": ""},
                    {"code": "000002.SZ", "n": 60, "zero_ratio": 1.0,
                     "std": 0.0, "passed": False,
                     "reason": "零收益占比 100.0%（疑似停牌）"},
                ],
                "pairs": [
                    {"a": "600002.SH", "b": "600001.SH", "n": 60,
                     "corr": 0.92, "high": True, "note": ""},
                    {"a": "000001.SZ", "b": "000002.SZ", "n": 12,
                     "corr": None, "high": False, "note": "样本不足"},
                ],
            },
        },
        "date": {
            "file_date": "20260930",
            "row_trade_date": "20260929",
            "row_trade_dates_all": ["20260929"],
            "signal_date": "20260929",
            "market_last_date": "20260911",
            "today": "20260930",
            "stale": True,
            "status": WARN,
            "reasons": ["批次日 20260930 晚于本地行情末日 20260911"],
        },
        "overall": WARN,
        "section_status": {"date": WARN, "industry": WARN, "style": OK,
                           "index": WARN, "correlation": WARN},
        "reasons": ["[io] 数据读取存在告警，诊断结论可信度下降"],
        "io": {"kind": "v13", "batch_date": "20260930",
               "path": "D:/x/20260930_selection_full.csv", "n_rows": 10,
               "io_status": WARN, "errors": ["1 只候选无行情"]},
        "io_reasons": ["1 只候选无行情"],
    }


print("=" * 74)
print("[A] 参数防御")
print("=" * 74)

_res = mk_result()

try:
    REP.build_payload(_res, None)
    check("A1 generated_at=None 抛 ValueError", False, "未抛异常")
except ValueError:
    check("A1 generated_at=None 抛 ValueError", True)
except Exception as e:  # noqa: BLE001
    check("A1 generated_at=None 抛 ValueError", False,
          "抛了 %s" % type(e).__name__)

try:
    REP.build_payload("not-a-dict", GEN_AT)
    check("A2 result 非 dict 抛 TypeError", False, "未抛异常")
except TypeError:
    check("A2 result 非 dict 抛 TypeError", True)
except Exception as e:  # noqa: BLE001
    check("A2 result 非 dict 抛 TypeError", False,
          "抛了 %s" % type(e).__name__)

try:
    REP.atomic_write("", "x")
    check("A3 atomic_write 空路径抛 ValueError", False, "未抛异常")
except ValueError:
    check("A3 atomic_write 空路径抛 ValueError", True)
except Exception as e:  # noqa: BLE001
    check("A3 atomic_write 空路径抛 ValueError", False,
          "抛了 %s" % type(e).__name__)


print("=" * 74)
print("[B] payload 字段完整性")
print("=" * 74)

_p = REP.build_payload(_res, GEN_AT, kind="v13", batch_date="20260930")

check("B1 schema_version 固定",
      _p.get("schema_version") == REP.SCHEMA_VERSION,
      _p.get("schema_version"))
check("B2 generated_at 透传",
      _p.get("generated_at") == GEN_AT, _p.get("generated_at"))
check("B3 kind/batch_date 透传",
      _p.get("kind") == "v13" and _p.get("batch_date") == "20260930",
      (_p.get("kind"), _p.get("batch_date")))
check("B4 overall/n_candidates 透传",
      _p.get("overall") == WARN and _p.get("n_candidates") == 5,
      (_p.get("overall"), _p.get("n_candidates")))
check("B5 codes 已排序",
      _p.get("codes") == sorted(_p.get("codes") or []), _p.get("codes"))
check("B6 thresholds 完整保留",
      _p.get("thresholds", {}).get("corr_pair") ==
      core.THRESHOLDS["corr_pair"], _p.get("thresholds"))

_secs = _p.get("sections") or {}
check("B7 五个维度齐全",
      set(_secs) == set(["date"] + REP.SECTION_ORDER),
      sorted(_secs))
check("B8 sections 顺序固定",
      list(_secs) == ["date"] + REP.SECTION_ORDER, list(_secs))

# 关键回归：date 段字段名必须与 core.resolve_batch_dates 的输出一致。
# 曾写成 row_trade_dates（复数），与 core 的 row_trade_date 错位 ->
# 日期表永远渲染不出行内快照日，且 stale 标记被丢弃。
_ds = _secs.get("date") or {}
check("B9 date.file_date 命中",
      _ds.get("file_date") == "20260930", _ds.get("file_date"))
check("B10 date.row_trade_date 命中（曾字段错位）",
      _ds.get("row_trade_date") == "20260929", _ds.get("row_trade_date"))
check("B11 date.row_trade_dates_all 命中",
      _ds.get("row_trade_dates_all") == ["20260929"],
      _ds.get("row_trade_dates_all"))
check("B12 date.signal_date 命中",
      _ds.get("signal_date") == "20260929", _ds.get("signal_date"))
check("B13 date.stale 保留",
      _ds.get("stale") is True, _ds.get("stale"))

_ind = _secs.get("industry") or {}
check("B14 industry 关键字段齐全",
      _ind.get("top1_industry") == "银行" and
      _ind.get("n_industries") == 2 and
      _ind.get("distribution", {}).get("银行") == 3,
      _ind)
_sty = _secs.get("style") or {}
check("B15 style 关键字段齐全",
      _sty.get("n_buckets") == 2 and
      _sty.get("no_style_codes") == ["000002.SZ"], _sty)
_idx = _secs.get("index") or {}
check("B16 index.coverage 结构保留 name/n/ratio",
      _idx.get("coverage", {}).get("000300.SH", {}).get("name") == "沪深300",
      _idx.get("coverage"))
_cor = _secs.get("correlation") or {}
check("B17 correlation 关键字段齐全",
      _cor.get("n_clusters") == 1 and
      _cor.get("largest_cluster") == 2 and
      _cor.get("max_corr") == 0.92, _cor)

_io = _p.get("io") or {}
check("B18 io 块透传",
      _io.get("io_status") == WARN and _io.get("n_rows") == 10 and
      _io.get("errors") == ["1 只候选无行情"], _io)
check("B19 io_reasons 透传",
      _p.get("io_reasons") == ["1 只候选无行情"], _p.get("io_reasons"))

# 空输入不应崩：全部维度缺失时也要能出报告
_empty = {}
_pe = REP.build_payload(_empty, GEN_AT)
check("B20 空 result 不崩且 sections 为空",
      isinstance(_pe, dict) and _pe.get("sections") == {},
      _pe.get("sections"))

# 只给部分维度
_partial = {"sections": {"industry": {"status": OK, "reasons": []}}}
_pp = REP.build_payload(_partial, GEN_AT)
check("B21 部分维度时只渲染已有维度",
      list(_pp.get("sections") or {}) == ["industry"],
      list(_pp.get("sections") or {}))


print("=" * 74)
print("[C] 确定性")
print("=" * 74)

_j1 = REP.dump_json(REP.build_payload(mk_result(), GEN_AT,
                                      kind="v13", batch_date="20260930"))
_j2 = REP.dump_json(REP.build_payload(mk_result(), GEN_AT,
                                      kind="v13", batch_date="20260930"))
check("C1 同输入 JSON 逐字节一致", _j1 == _j2,
      "len=%d vs %d" % (len(_j1), len(_j2)))

_m1 = REP.render_markdown(REP.build_payload(mk_result(), GEN_AT))
_m2 = REP.render_markdown(REP.build_payload(mk_result(), GEN_AT))
check("C2 同输入 Markdown 逐字节一致", _m1 == _m2,
      "len=%d vs %d" % (len(_m1), len(_m2)))

# 输入 dict 键序变化不应影响输出
# 注意：基准 _j1 是带 kind/batch_date 生成的，此处必须传同样参数，
# 否则比较的是两个不同 payload（kind=None vs v13），不是键序问题。
_KW = {"kind": "v13", "batch_date": "20260930"}
_alt = mk_result()
_alt2 = dict(reversed(list(_alt.items())))
_j3 = REP.dump_json(REP.build_payload(_alt2, GEN_AT, **_KW))
check("C3 输入顶层键序不影响 JSON", _j1 == _j3,
      "len=%d vs %d" % (len(_j1), len(_j3)))

# sections 内键序变化也不应影响
_alt3 = mk_result()
_alt3["sections"]["correlation"] = dict(
    reversed(list(_alt3["sections"]["correlation"].items())))
_j4 = REP.dump_json(REP.build_payload(_alt3, GEN_AT, **_KW))
check("C4 输入嵌套键序不影响 JSON", _j1 == _j4,
      "len=%d vs %d" % (len(_j1), len(_j4)))

# 输入候选顺序变化 -> payload codes 必须归一化排序
_alt5 = mk_result()
_alt5["codes"] = list(reversed(_alt5["codes"]))
_j5 = REP.dump_json(REP.build_payload(_alt5, GEN_AT, **_KW))
check("C5 输入候选顺序不影响 codes 输出",
      json.loads(_j5)["codes"] == sorted(json.loads(_j1)["codes"]))

# JSON 可回读
_parsed = json.loads(_j1)
check("C6 JSON 可回读且 schema 一致",
      _parsed.get("schema_version") == REP.SCHEMA_VERSION,
      _parsed.get("schema_version"))
check("C7 JSON 中文未转义",
      "沪深300" in _j1, "未找到中文")

# 相关配对排序：corr 降序，None 排最后
_pr = REP._pair_rows(_cor)
check("C8 pair 按 corr 降序（None 最后）",
      _pr[0].get("corr") == 0.92 and _pr[-1].get("corr") is None,
      [(x.get("a"), x.get("corr")) for x in _pr])

# pair 排序稳定性：同 corr 时按 a、b 升序
_same = {"pairs": [
    {"a": "B", "b": "A", "n": 1, "corr": 0.5, "high": False},
    {"a": "A", "b": "B", "n": 1, "corr": 0.5, "high": False},
]}
_sorted_same = REP._pair_rows(_same)
check("C9 同 corr 按代码升序稳定",
      [(x["a"], x["b"]) for x in _sorted_same] ==
      [("A", "B"), ("B", "A")],
      [(x["a"], x["b"]) for x in _sorted_same])

# quality 排序：passed 优先 False 在前（即被剔除的排前面），代码升序
_qr = REP._quality_rows(_cor)
check("C10 quality 按 passed、code 排序",
      [x.get("code") for x in _qr] == ["000002.SZ", "600001.SH"],
      [x.get("code") for x in _qr])

# set 转排序列表（_sorted_deep）
_sd = REP._sorted_deep({"b": 2, "a": {"z", "y", "x"}, "c": (3, 1)})
check("C11 set->sorted list 且 dict 键排序",
      _sd == {"a": ["x", "y", "z"], "b": 2, "c": [3, 1]}, _sd)

# 簇内排序
_clusters = (_cor.get("clusters") or [])
check("C12 簇内元素已排序",
      all(cl == sorted(cl) for cl in _clusters), _clusters)

# 无 set / 无不可序列化对象时 dump 不炸
check("C13 dump_json 输出以换行结尾", _j1.endswith("\n"))


print("=" * 74)
print("[D] Markdown 渲染")
print("=" * 74)

check("D1 标题存在", _m1.startswith("# 同批次选股相关性诊断报告"))
for kw in ("schema", "生成时间", "结论", "候选数"):
    check("D2 含字段 %s" % kw, kw in _m1)
for t in REP.SECTION_TITLE.values():
    check("D3 含板块 %s" % t, ("## %s" % t) in _m1)
check("D4 候选清单渲染",
      "600001.SH" in _m1, "")
check("D5 阈值表渲染", "corr_pair" in _m1)
check("D6 阈值声明句存在（不构成生产交易闸门）",
      "不构成生产交易闸门" in _m1)
check("D7 IO 告警渲染", "1 只候选无行情" in _m1)
check("D8 日期口径渲染行内快照日",
      "row_trade_date" in _m1 and "20260929" in _m1)
check("D9 stale 标记渲染", "stale" in _m1)
check("D10 行业分布表渲染", "银行" in _m1 and "电子" in _m1)
check("D11 风格桶表渲染", "300成长" in _m1)
check("D12 宽基覆盖表渲染", "中证500" in _m1)
check("D13 簇表渲染", "C1" in _m1)
check("D14 相关配对表渲染样本不足",
      "样本不足" in _m1 or "NA" in _m1)
check("D15 quality REJECT 标记", "REJECT" in _m1)

# 缺失数值必须显式 NA，而不是空
_na_res = mk_result()
_na_res["sections"]["index"]["top1_ratio"] = None
_na_res["sections"]["industry"]["hhi"] = None
_na_md = REP.render_markdown(
    REP.build_payload(_na_res, GEN_AT))
check("D16 缺失比例显示 NA", "NA" in _na_md)
check("D17 缺失值不渲染成裸空",
      "| 第一大宽基: None(" not in _na_md, "")

# 空维度不崩
_empty_md = REP.render_markdown(REP.build_payload({}, GEN_AT))
check("D18 空 result 渲染不崩", isinstance(_empty_md, str) and
      len(_empty_md) > 0)
check("D19 空候选显示占位", "（空）" in _empty_md)

# 未知 overall 不崩
_unk = mk_result()
_unk["overall"] = "WEIRD_STATUS"
_unk_md = REP.render_markdown(REP.build_payload(_unk, GEN_AT))
check("D20 未知 overall 原样输出", "WEIRD_STATUS" in _unk_md)


print("=" * 74)
print("[E] 原子写")
print("=" * 74)

_p1 = os.path.join(TMP, "sub", "deep", "a.json")
REP.atomic_write(_p1, "hello\n")
check("E1 目录自建 + 写入成功",
      os.path.isfile(_p1) and
      io.open(_p1, encoding="utf-8").read() == "hello\n")

REP.atomic_write(_p1, "replaced\n")
check("E2 覆盖写生效",
      io.open(_p1, encoding="utf-8").read() == "replaced\n",
      io.open(_p1, encoding="utf-8").read())

# 覆盖写后不应残留临时文件
_leftover = [f for f in os.listdir(os.path.dirname(_p1))
             if f.startswith(".corr_diag_")]
check("E3 写后无临时文件残留", not _leftover, _leftover)

# 写入内容逐字节一致
_p2 = os.path.join(TMP, "b.txt")
REP.atomic_write(_p2, _m1)
with io.open(_p2, "rb") as f:
    _bytes1 = f.read()
check("E4 落盘无 CRLF 抖动",
      b"\r\n" not in _bytes1, "含 CRLF")

# 目标路径是目录 -> 必须抛异常且不留临时文件
_dir_target = os.path.join(TMP, "target_dir")
os.makedirs(_dir_target, exist_ok=True)
try:
    REP.atomic_write(_dir_target, "x")
    check("E5 目标是目录时抛出", False, "未抛异常")
except (OSError, IOError, ValueError):
    check("E5 目标是目录时抛出", True)
except Exception as e:  # noqa: BLE001
    check("E5 目标是目录时抛出", False, "抛了 %s" % type(e).__name__)
_left2 = [f for f in os.listdir(TMP) if f.startswith(".corr_diag_")]
check("E6 写失败后清理临时文件", not _left2, _left2)

# 写失败不得静默返回
_p3 = os.path.join(TMP, "ro", "c.txt")
os.makedirs(os.path.dirname(_p3), exist_ok=True)
io.open(_p3, "w", encoding="utf-8").write("old")
try:
    os.chmod(_p3, stat.S_IREAD)
except OSError:
    pass
try:
    try:
        REP.atomic_write(_p3, "new-ro-test")
    except (OSError, IOError):
        pass
    got = io.open(_p3, encoding="utf-8").read()
    check("E7 只读目标要么抛异常要么内容未损坏",
          got in ("old", "new-ro-test"), got)
except Exception as e:  # noqa: BLE001
    check("E7 只读目标要么抛异常要么内容未损坏", False,
          "抛了 %s" % type(e).__name__)


print("=" * 74)
print("[F] write_reports 端到端")
print("=" * 74)

_out = os.path.join(TMP, "reports")
_jp, _mp = REP.write_reports(_out, "corr_v13_20260930", mk_result(),
                             GEN_AT, kind="v13", batch_date="20260930")
check("F1 JSON 落盘", os.path.isfile(_jp), _jp)
check("F2 MD 落盘", os.path.isfile(_mp), _mp)
check("F3 文件名符合约定",
      _jp.endswith("corr_v13_20260930.json") and
      _mp.endswith("corr_v13_20260930.md"), (_jp, _mp))

with io.open(_jp, encoding="utf-8") as f:
    _disk = json.load(f)
check("F4 落盘 JSON 可回读",
      _disk.get("schema_version") == REP.SCHEMA_VERSION,
      _disk.get("schema_version"))
check("F5 落盘 JSON 含全部维度",
      set(_disk.get("sections") or {}) == set(["date"] + REP.SECTION_ORDER),
      sorted(_disk.get("sections") or {}))

# 重复生成逐字节一致
_jp2, _mp2 = REP.write_reports(_out, "corr_v13_20260930", mk_result(),
                               GEN_AT, kind="v13", batch_date="20260930")
check("F6 重复写 JSON 逐字节一致",
      io.open(_jp, "rb").read() == io.open(_jp2, "rb").read())
check("F7 重复写 MD 逐字节一致",
      io.open(_mp, "rb").read() == io.open(_mp2, "rb").read())

# 无残留
_left3 = [f for f in os.listdir(_out) if f.startswith(".corr_diag_")]
check("F8 报告目录无临时文件残留", not _left3, _left3)

# generated_at 缺失时 write_reports 也必须抛
try:
    REP.write_reports(_out, "x_bad", mk_result(), None)
    check("F9 write_reports 缺 generated_at 抛异常", False, "未抛异常")
except ValueError:
    check("F9 write_reports 缺 generated_at 抛异常", True)
except Exception as e:  # noqa: BLE001
    check("F9 write_reports 缺 generated_at 抛异常", False,
          "抛了 %s" % type(e).__name__)
check("F10 抛异常时不落盘半成品",
      not os.path.isfile(os.path.join(_out, "x_bad.json")),
      "残留文件")


print("=" * 74)
print("[D4] 风格字段投影（覆盖率/基准可用性/数据日期）")
print("=" * 74)

# 背景：core.style_concentration 已把「数据不可用 / 零命中 / 数据可用但陈旧」
# 三种语义分开，但报告层若只投影 n_in_any_style 与桶计数，读者看到
# 「未命中 5 只」时无法区分是风格数据坏了还是候选真的不属于任何风格桶。
_ps = (_p.get("sections") or {}).get("style") or {}
check("D4-1 style.coverage_ratio 投影",
      _ps.get("coverage_ratio") == 0.8, _ps.get("coverage_ratio"))
check("D4-2 style.data_available 投影",
      _ps.get("data_available") is True, _ps.get("data_available"))
check("D4-3 style 基准可用性计数投影",
      _ps.get("n_benchmarks_loaded") == 9 and
      _ps.get("n_benchmarks_missing") == 0,
      (_ps.get("n_benchmarks_loaded"), _ps.get("n_benchmarks_missing")))
check("D4-4 style.missing_benchmarks 投影",
      _ps.get("missing_benchmarks") == [], _ps.get("missing_benchmarks"))
check("D4-5 style.extra_benchmarks 投影",
      _ps.get("extra_benchmarks") == [], _ps.get("extra_benchmarks"))
check("D4-6 style.members_date 投影",
      _ps.get("members_date") == "20260630", _ps.get("members_date"))
check("D4-7 style.asof_date 投影",
      _ps.get("asof_date") == "20260929", _ps.get("asof_date"))
check("D4-8 style.stale_days 投影",
      _ps.get("stale_days") == 91, _ps.get("stale_days"))

# 字段缺失时必须显式 None，不能静默消失：消失会让「未投影」和
# 「真的没有数据」在报告里长得一模一样
_ps_none = REP.build_payload(
    {"sections": {"style": {"status": OK, "reasons": [],
                            "n_in_any_style": 0}}}, GEN_AT)
_pn_sty = (_ps_none.get("sections") or {}).get("style") or {}
check("D4-9 字段缺失时显式 None 而非丢键",
      _pn_sty.get("coverage_ratio") is None and
      _pn_sty.get("stale_days") is None and
      "coverage_ratio" in _pn_sty and
      "stale_days" in _pn_sty,
      sorted(_pn_sty))

# Markdown 必须把「唯一覆盖率」与「基准可用性/快照日期」写出来，
# 否则 G2/ENS 的 0/9 命中会被读成策略失效。
# 注意用「整行包含」而不是子串包含：index 段的 top1_ratio 也是 0.8，
# 纯子串 "80.0%" 会因 index 段渲染而巧合通过——这种假绿最危险，
# 因为它让人以为覆盖率已经投影出去了。
_md4 = REP.render_markdown(_p)
_md4_lines = _md4.splitlines()
check("D4-10 MD 含唯一覆盖率",
      any("唯一覆盖率" in x for x in _md4_lines),
      [x for x in _md4_lines if "覆盖" in x])
check("D4-11 MD 含基准可用性计数",
      any("风格基准" in x and "9/9" in x for x in _md4_lines),
      [x for x in _md4_lines if "基准" in x])
check("D4-12 MD 含风格快照日期",
      any("20260630" in x for x in _md4_lines),
      [x for x in _md4_lines if "快照" in x])
check("D4-13 MD 含陈旧天数",
      any("91" in x for x in _md4_lines if "滞后" in x or "陈旧" in x),
      [x for x in _md4_lines if "滞后" in x or "陈旧" in x])

# 快照日期缺失时不得伪造日期
_md4n = REP.render_markdown(_ps_none)
check("D4-14 快照日期缺失时渲染 NA 而非空白",
      "NA" in _md4n,
      [x for x in _md4n.splitlines() if "快照" in x])

# IO 段新增字段必须透传：排查批次接线问题时，这两个字段是唯一的
# 「PIT 基准日到底取到没有」的自证
_res_io = mk_result()
_res_io["io"] = dict(_res_io["io"])
_res_io["io"]["signal_date"] = "20260929"
_res_io["io"]["style_members_date"] = "20260630"
_pio = REP.build_payload(_res_io, GEN_AT, kind="v13",
                         batch_date="20260930")
check("D4-15 io.signal_date 透传",
      (_pio.get("io") or {}).get("signal_date") == "20260929",
      (_pio.get("io") or {}).get("signal_date"))
check("D4-16 io.style_members_date 透传",
      (_pio.get("io") or {}).get("style_members_date") == "20260630",
      (_pio.get("io") or {}).get("style_members_date"))

# 旧 result（无新字段）不得崩：向后兼容，字段为 None
_pold = REP.build_payload(mk_result(), GEN_AT)
check("D4-17 旧 result 缺新字段不崩",
      (_pold.get("io") or {}).get("signal_date") is None,
      (_pold.get("io") or {}).get("signal_date"))


print("=" * 74)
print("[G] 真实批次端到端")
print("=" * 74)

_g_have = False
try:
    _g_have = cio.default_paths() is not None
except Exception:  # noqa: BLE001
    _g_have = False

# 未知 kind 必须抛可读 ValueError，而不是裸 KeyError('g2_ens')
try:
    cio.resolve_batch_path("g2_ens", "20260930")
    check("G0 未知 kind 抛可读 ValueError", False, "未抛异常")
except ValueError as e:
    check("G0 未知 kind 抛可读 ValueError",
          "kind" in str(e) and "g2_ens" in str(e), str(e))
except Exception as e:  # noqa: BLE001
    check("G0 未知 kind 抛可读 ValueError", False,
          "抛了 %s: %s" % (type(e).__name__, e))

if not _g_have:
    skip("G 真实批次报告", "IO 层不可用")
else:
    # kind 必须是 v13 / g2 / ens（ENS 在 IO 层的合法标识是 "ens"，不是 "g2_ens"）
    _cases = [("v13", "20260930", "20260930_selection_full.csv"),
              ("g2", "20260930", "20260930_g2_top10.csv"),
              ("ens", "20260930", "20260930_g2_top10.csv")]
    for _kind, _bd, _fn in _cases:
        try:
            _real = cio.diagnose_batch(_kind, _bd, filename=_fn, today=_bd)
        except Exception as e:  # noqa: BLE001
            skip("G 真实批次 %s" % _kind,
                 "读取异常 %s: %s" % (type(e).__name__, e))
            continue
        _rp = REP.build_payload(_real, GEN_AT, kind=_kind,
                                batch_date=_bd)
        check("G1 %s 真实报告 payload 可构造" % _kind,
              isinstance(_rp, dict) and "sections" in _rp)
        check("G2 %s 真实报告 overall 非空" % _kind,
              _rp.get("overall") in (OK, WARN, FAIL_S, SKIP_S),
              _rp.get("overall"))
        _rj = REP.dump_json(_rp)
        check("G3 %s 真实 JSON 可回读" % _kind,
              json.loads(_rj).get("schema_version") == REP.SCHEMA_VERSION)
        _rm = REP.render_markdown(_rp)
        check("G4 %s 真实 Markdown 渲染非空" % _kind, len(_rm) > 200)
        check("G5 %s 真实报告带非闸门声明" % _kind,
              "不构成生产交易闸门" in _rm)
        _rj2 = REP.dump_json(REP.build_payload(
            cio.diagnose_batch(_kind, _bd, filename=_fn, today=_bd),
            GEN_AT, kind=_kind, batch_date=_bd))
        check("G6 %s 真实报告确定性" % _kind, _rj == _rj2)
        print("[INFO] %s %s overall=%s codes=%s"
              % (_kind, _bd, _rp.get("overall"), _rp.get("n_candidates")))


shutil.rmtree(TMP, ignore_errors=True)

print("=" * 74)
print("PASS=%d FAIL=%d SKIP=%d" % (PASS[0], len(FAIL), len(SKIPPED)))
if FAIL:
    print("[FAILED LIST]")
    for f in FAIL:
        print("  - %s" % f)
print("[CORR-DIAG-REPORT-TEST-DONE]")
sys.exit(0 if not FAIL else 1)