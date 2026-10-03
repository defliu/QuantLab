# -*- coding: utf-8 -*-
"""同批次选股相关性诊断 · IO 层专项测试。

与 test_corr_diag.py（纯函数核心）互补，这里锁 IO 适配层的语义：
  A. 文件名日期解析 / 代码归一化
  B. 申万最新有效截面选取 + **历史回退**（含 Timestamp 索引层）
  C. 行业映射展开（含 con_codes 非法值 / 重复层值 / 单值标量）
  D. 指数成分跨族并集 / 最大日期 / 缓存与失效
  E. 行情末日（row group 统计量 + 降级路径）
  F. 收益统一日期轴 / 右端截断计数 / 停牌与常量剔除
  G. IO 状态提升（WARN/FAIL 不得被 core OK 掩盖；不得降级既有 FAIL）
  H. 端到端三策略 + 未来日期副本 vs 行情滞后（真实数据，数据缺失则 SKIP）

原则：单元部分用临时 parquet 夹具自造，可离线复现；
集成部分读真实 astock/候选文件，缺数据打印 [SKIP] 而不算失败
（与项目「备用数据源不可用时降级不阻断」的既有约定一致）。
"""
import io
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, "..", ".."))
RESEARCH = os.path.join(PROJ, "research")
SRC = os.path.join(RESEARCH, "corr_diag_io.py")

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


def approx(a, b, tol=1e-9):
    return abs(float(a) - float(b)) <= tol


CRASH = [0]


def call_idx(dirpath, codes, **kw):
    """调用 load_index_members，把 TypeError 转成可断言的失败结果。

    asof_date 形参在生产实现落地前会直接抛 TypeError；若不拦截，整个脚本
    会在第一条 D2 断言处中断，后面所有 PIT 契约都跑不到，红测基线就不完整。
    参考 core 层 call_style 的同款处理。
    """
    try:
        return CIO.load_index_members(dirpath, codes, **kw)
    except TypeError as e:
        CRASH[0] += 1
        return {}, {"status": "CRASH", "errors": ["TypeError: %s" % e],
                    "per_index": {}, "cache_hit": False}


def call_merge(frames, codes, **kw):
    """调用 merge_index_members，同 call_idx 的崩溃聚合处理。"""
    try:
        return CIO.merge_index_members(frames, codes, **kw)
    except TypeError as e:
        CRASH[0] += 1
        return {}, {"status": "CRASH", "errors": ["TypeError: %s" % e],
                    "per_index": {}}


# ---------------------------------------------------------------------------
# 模块加载：IO 内部 import corr_diag_core，必须把 research/ 放进 sys.path，
# 再以真实 ModuleType 执行（保持 core 解析到同一份实例）
# ---------------------------------------------------------------------------
if RESEARCH not in sys.path:
    sys.path.insert(0, RESEARCH)

with io.open(SRC, "r", encoding="utf-8") as f:
    _io_src = f.read()
CIO = types.ModuleType("corr_diag_io_under_test")
CIO.__file__ = SRC
exec(compile(_io_src, SRC, "exec"), CIO.__dict__)

import corr_diag_core as core  # noqa: E402  (需在 sys.path 注入之后)

import pandas as pd  # noqa: E402

OK, WARN, FAIL_S, SKIP_S = (core.STATUS_OK, core.STATUS_WARN,
                             core.STATUS_FAIL, core.STATUS_SKIP)

TMP = tempfile.mkdtemp(prefix="corr_diag_io_")


def mk_sw_parquet(name, snaps, nlevels=2):
    """造申万一级 parquet。

    snaps: [(date_str, {index_code: (industry_name, con_codes)}), ...]
    索引第一层放 date（字符串）；传 pd.Timestamp 可复现真实 parquet 的索引类型。
    """
    rows = []
    idx = []
    for d, mapping in snaps:
        for code_name, (ind, cons) in mapping.items():
            rows.append({"name": ind, "con_codes": list(cons)})
            idx.append((d, code_name))
    df = pd.DataFrame(rows)
    df.index = pd.MultiIndex.from_tuples(idx, names=["trade_date", "index_code"])
    p = os.path.join(TMP, name)
    df.to_parquet(p)
    return p


def mk_weight_parquet(name, rows):
    """造指数成分 parquet。rows: [(index_code, con_code, trade_date, weight)]"""
    df = pd.DataFrame(rows, columns=["index_code", "con_code",
                                     "trade_date", "weight"])
    p = os.path.join(TMP, name)
    df.to_parquet(p)
    return p


def mk_daily_parquet(name, spec):
    """造主行情 parquet。spec: {code: [(date_str, pct_chg), ...]}"""
    rows = []
    idx = []
    for code, series in sorted(spec.items()):
        for d, pc in series:
            rows.append({"pct_chg": float(pc)})
            idx.append((d, code))
    df = pd.DataFrame(rows)
    df.index = pd.MultiIndex.from_tuples(idx, names=["trade_date", "ts_code"])
    p = os.path.join(TMP, name)
    df.to_parquet(p)
    return p


def daterange(n, start_day=1, end_day=28, month=9, year=2026):
    """生成 n 个交易日字符串（2026-09-01 .. 2026-09-28 足够用）。"""
    out = []
    for d in range(start_day, start_day + n):
        out.append("%04d%02d%02d" % (year, month, d))
    return out


print("=" * 74)
print("[A] 文件名日期解析 / 代码归一化")
# ---------------------------------------------------------------------------

check("A1 文件名YYYYMMDD",
      CIO.parse_date_from_filename("20260930_g2_top10.csv") == "20260930")
check("A2 文件名v13式",
      CIO.parse_date_from_filename("20260930_selection_full.csv") == "20260930")
check("A3 文件名无日期", CIO.parse_date_from_filename("top10.csv") is None)
check("A4 文件名空路径", CIO.parse_date_from_filename("") is None)
check("A5 文件名None", CIO.parse_date_from_filename(None) is None)
check("A6 文件名含其他数字不被误取",
      CIO.parse_date_from_filename("g2_top10_2026.csv") is None)

check("A7 code 已是标准",
      CIO.normalize_code("600000.SH") == "600000.SH")
check("A8 code 带交易所后缀",
      CIO.normalize_code("600000") == "600000.SH")
check("A9 code 归一化 SZ",
      CIO.normalize_code("000001") == "000001.SZ")
check("A10 code BJ 归一化",
      CIO.normalize_code("920237") == "920237.BJ")
check("A11 code 带空格",
      CIO.normalize_code("  600000.SH ") == "600000.SH")
check("A12 code 数值型",
      CIO.normalize_code(600000) == "600000.SH")
check("A13 code NaN", CIO.normalize_code(float("nan")) is None)
check("A14 code None", CIO.normalize_code(None) is None)
check("A15 code 空串", CIO.normalize_code("") is None)
check("A16 code 字符串nan",
      CIO.normalize_code("nan") is None)
check("A17 code 字符串none",
      CIO.normalize_code("None") is None)


print("[B] 申万最新有效截面 + 历史回退")
# ---------------------------------------------------------------------------

# B1-B3：最新截面有效，应直接选最新，不回退
p = mk_sw_parquet("sw_ok.parquet", [
    ("20260901", {"801010.SI": ("农林牧渔", ["000001.SZ", "000002.SZ"])}),
    ("20260910", {"801020.SI": ("采掘", ["600000.SH"]),
                  "801030.SI": ("化工", ["600001.SH"]),
                  "801040.SI": ("钢铁", ["600002.SH"]),
                  "801050.SI": ("有色", ["600003.SH"])}),
])
sub, used, tried = CIO.pick_latest_valid_sw(pd.read_parquet(p), min_valid=4)
check("B1 选最新有效截面", used == "20260910", used)
check("B2 只遍历必要日期", len(tried) == 1, tried)
check("B3 截面行数正确", int(len(sub)) == 4, len(sub))

# B4-B6：最新截面无效（有效行业数 < min_valid），必须回退到历史有效截面
# 夹具方向：历史截面比最新截面更完整 —— 这才是回退的真实触发场景
#（最新截面因数据残缺只有 1 个行业有效）。若把历史写成更少，
#  min_valid 一旦大于两者，两边都不满足，只会得到 None，测不到回退。
p = mk_sw_parquet("sw_fallback.parquet", [
    ("20260901", {"801010.SI": ("农林牧渔", ["000001.SZ"]),
                  "801020.SI": ("采掘", ["600000.SH"]),
                  "801030.SI": ("化工", ["600001.SH"])}),
    ("20260910", {"801020.SI": ("采掘", ["600000.SH"])}),
])
sub, used, tried = CIO.pick_latest_valid_sw(pd.read_parquet(p), min_valid=3)
check("B4 最新无效时回退历史", used == "20260901", used)
check("B5 回退留痕 tried 完整", len(tried) == 2, tried)
# tried 按日期倒序追加：tried[0]=最新(无效)，tried[1]=历史(有效)
check("B6 tried 含有效行业数",
      tried[0]["n_valid"] == 1 and tried[1]["n_valid"] == 3, tried)

# B7-B8：全部截面无效 -> 返回 None（不静默给残缺映射）
sub, used, tried = CIO.pick_latest_valid_sw(pd.read_parquet(p), min_valid=99)
check("B7 全无效返回None", sub is None and used is None, (sub, used))
check("B8 全无效仍留痕", len(tried) == 2, tried)

# B9-B10：空表
sub, used, tried = CIO.pick_latest_valid_sw(pd.DataFrame(), min_valid=1)
check("B9 空表返回None", sub is None and used is None)
sub, used, tried = CIO.pick_latest_valid_sw(None, min_valid=1)
check("B10 None表返回None", sub is None and used is None)

# B11-B13：索引层是 pd.Timestamp（真实 parquet 形态）
# 关键回归：旧实现用上一轮循环遗留变量比较，且拿归一化字符串比 Timestamp，
# 会导致「遍历了日期但一个截面都选不出来」。
ts_snaps = [
    (pd.Timestamp("2026-09-01"),
     {"801010.SI": ("农林牧渔", ["000001.SZ", "000002.SZ", "000003.SZ"])}),
    (pd.Timestamp("2026-09-10"),
     {"801020.SI": ("采掘", ["600000.SH"]),
      "801030.SI": ("化工", ["600001.SH"]),
      "801040.SI": ("钢铁", ["600002.SH"])}),
]
p_ts = mk_sw_parquet("sw_ts.parquet", ts_snaps)
sub, used, tried = CIO.pick_latest_valid_sw(pd.read_parquet(p_ts), min_valid=3)
check("B11 Timestamp索引层可选最新", used == "20260910", (used, tried))

# 回退夹具方向必须与 B4-B6 一致：历史截面比最新截面更完整，
# 否则两个截面都不满足 min_valid，正确行为就是 None，测不到回退。
ts_snaps_fb = [
    (pd.Timestamp("2026-09-01"),
     {"801010.SI": ("农林牧渔", ["000001.SZ"]),
      "801020.SI": ("采掘", ["600000.SH"]),
      "801030.SI": ("化工", ["600001.SH"])}),
    (pd.Timestamp("2026-09-10"),
     {"801020.SI": ("采掘", ["600000.SH"])}),
]
p_ts_fb = mk_sw_parquet("sw_ts_fallback.parquet", ts_snaps_fb)
sub2, used2, _ = CIO.pick_latest_valid_sw(
    pd.read_parquet(p_ts_fb), min_valid=3)
check("B12 Timestamp索引层可回退", used2 == "20260901", used2)
check("B13 Timestamp回退内容正确",
      sub2 is not None and
      sorted(sub2.index.get_level_values(-1)) ==
      ["801010.SI", "801020.SI", "801030.SI"],
      None if sub2 is None
      else list(sub2.index.get_level_values(-1)))


print("[C] 行业映射展开")
# ---------------------------------------------------------------------------

# C1-C3：正常展开，代码归一化
# min_valid 必须与夹具规模匹配：默认 10 是按真实申万（31 个有效行业）定的，
# 小夹具只有 2 个行业，若沿用默认值会在进入「展开」逻辑前就判无效。
p = mk_sw_parquet("sw_map.parquet", [
    ("20260910", {"801010.SI": ("农林牧渔", ["000001", "000002.SZ"]),
                  "801020.SI": ("采掘", ["600000"])}),
])
imap, m = CIO.load_sw_l1_industry_map(p, min_valid=2)
check("C1 映射展开正确",
      imap == {"000001.SZ": "农林牧渔", "000002.SZ": "农林牧渔",
               "600000.SH": "采掘"}, imap)
check("C2 映射状态OK", m["status"] == OK, m)
check("C3 快照日期", m["snap_date"] == "20260910", m["snap_date"])

# C4-C5：con_codes 含 None / 空 / 非字符串垃圾，必须跳过而不是崩
# 有效行业 = 农林牧渔 + 化工 = 2（采掘空列表不算有效）
p = mk_sw_parquet("sw_dirty.parquet", [
    ("20260910", {"801010.SI": ("农林牧渔", ["000001.SZ", None]),
                  "801020.SI": ("采掘", []),
                  "801030.SI": ("化工", ["600000.SH"])}),
])
imap, m = CIO.load_sw_l1_industry_map(p, min_valid=2)
check("C4 脏con_codes被跳过",
      imap == {"000001.SZ": "农林牧渔", "600000.SH": "化工"}, imap)
check("C5 脏数据不报错", m["status"] == OK, m)

# C6-C7：重复 index_code（同日多行）不应抛错，取首行
rows = [{"name": "采掘", "con_codes": ["600000.SH"]},
        {"name": "煤炭", "con_codes": ["600001.SH"]}]
df = pd.DataFrame(rows)
df.index = pd.MultiIndex.from_tuples(
    [("20260910", "801020.SI"), ("20260910", "801020.SI")],
    names=["trade_date", "index_code"])
p_dup = os.path.join(TMP, "sw_dup.parquet")
df.to_parquet(p_dup)
imap, m = CIO.load_sw_l1_industry_map(p_dup, min_valid=2)
check("C6 重复层值不抛KeyError", isinstance(imap, dict) and len(imap) >= 1, imap)
check("C7 重复层值状态OK", m["status"] == OK, m)

# C8-C10：结构异常必须 FAIL-closed
imap, m = CIO.load_sw_l1_industry_map(os.path.join(TMP, "nope.parquet"))
check("C8 文件不存在=FAIL", m["status"] == FAIL_S, m)
check("C9 文件不存在错误留痕", len(m["errors"]) > 0, m["errors"])

bad_idx = pd.DataFrame({"name": ["x"], "con_codes": [["000001.SZ"]]})
p_flat = os.path.join(TMP, "sw_flat.parquet")
bad_idx.to_parquet(p_flat)
imap, m = CIO.load_sw_l1_industry_map(p_flat)
check("C10 单层索引=FAIL", m["status"] == FAIL_S, m)

no_code = pd.DataFrame({"name": ["x"]})
no_code.index = pd.MultiIndex.from_tuples([("20260910", "801010.SI")],
                                          names=["trade_date", "index_code"])
p_nc = os.path.join(TMP, "sw_nocode.parquet")
no_code.to_parquet(p_nc)
imap, m = CIO.load_sw_l1_industry_map(p_nc)
check("C11 缺con_codes列=FAIL", m["status"] == FAIL_S, m)


print("[D] 指数成分跨族并集 / 缓存")
# ---------------------------------------------------------------------------

WDIR = os.path.join(TMP, "iw")
os.makedirs(WDIR, exist_ok=True)

# 同族多文件 + 跨族同指数：必须取最大日期、同日期取并集
mk_weight_parquet(os.path.join(WDIR, "index_weight_0001.parquet"),
                  [("000300.SH", "600000.SH", "20260630", 1.0),
                   ("000300.SH", "600001.SH", "20260731", 1.0)])
mk_weight_parquet(os.path.join(WDIR, "index_weight_0002.parquet"),
                  [("000300.SH", "600002.SH", "20260731", 1.0),
                   ("000905.SH", "000001.SZ", "20260731", 1.0)])
mk_weight_parquet(os.path.join(WDIR, "index_weight_data_0003.parquet"),
                  [("000300.SH", "600003.SH", "20260731", 1.0),
                   ("000905.SH", "000002.SZ", "20260731", 1.0)])

CIO.clear_cache()
mem, m = CIO.load_index_members(WDIR, ["000300.SH", "000905.SH"])
check("D1 跨族并集完整",
      mem.get("000300.SH") == {"600001.SH", "600002.SH", "600003.SH"},
      mem.get("000300.SH"))
check("D2 最大日期优先（丢弃旧截面600000）",
      "600000.SH" not in mem.get("000300.SH", set()),
      mem.get("000300.SH"))
check("D3 跨族标注留痕",
      set(m["per_index"]["000300.SH"]["families"]) == {"main", "data"},
      m["per_index"].get("000300.SH"))
check("D4 缓存未命中标记", not m.get("cache_hit"), m.get("cache_hit"))

mem2, m2 = CIO.load_index_members(WDIR, ["000300.SH", "000905.SH"])
check("D5 二次命中缓存", m2.get("cache_hit") is True, m2.get("cache_hit"))
check("D6 缓存结果一致", mem2.get("000300.SH") == mem.get("000300.SH"))
check("D7 缓存命中不重复探测", m2.get("n_probed") == 0, m2.get("n_probed"))

CIO.clear_cache()
mem3, m3 = CIO.load_index_members(WDIR, ["000300.SH", "000905.SH"])
check("D8 clear_cache 后失效", not m3.get("cache_hit"), m3.get("cache_hit"))
check("D9 clear_cache 后结果不变",
      mem3.get("000300.SH") == mem.get("000300.SH"))

# D10：use_cache=False 强制重探
mem4, m4 = CIO.load_index_members(WDIR, ["000300.SH"], use_cache=False)
check("D10 use_cache=False 强制重探", m4.get("n_probed", 0) > 0,
      m4.get("n_probed"))

# D11-D13：空目标 / 目录不存在 / 未命中
mem5, m5 = CIO.load_index_members(WDIR, [])
check("D11 空目标返回空", mem5 == {} and m5["status"] == OK, (mem5, m5))
mem6, m6 = CIO.load_index_members(os.path.join(TMP, "no_dir"), ["000300.SH"])
check("D12 目录不存在=FAIL", m6["status"] == FAIL_S, m6)
CIO.clear_cache()
mem7, m7 = CIO.load_index_members(WDIR, ["999999.SH"])
check("D13 目标未命中=FAIL", m7["status"] == FAIL_S, m7)

# D14：返回的是缓存同一对象，调用方若修改会污染缓存 —— 记录为已知设计，
# 只断言当前行为一致（不静默变化），便于后续若改语义时有明确回归点。
CIO.clear_cache()
a, _ = CIO.load_index_members(WDIR, ["000300.SH"])
b, _ = CIO.load_index_members(WDIR, ["000300.SH"])
check("D14 缓存返回同一对象(记录行为)", a["000300.SH"] is b["000300.SH"])


# ---------------------------------------------------------------------------
print("[D2] 指数成分批次 PIT 对齐（asof_date）")
# ---------------------------------------------------------------------------
# 真实批次 20260930 审核发现的第三个缺口：merge_index_members 对每个指数
# 取的是**该指数所有文件里的全局最大 trade_date**，而不是「<= 批次日的最大
# trade_date」。这在回看历史批次时就是标准 look-ahead——用未来才公布的成分
# 去判断历史批次的风格分布。真实数据上风格成分 max_td=20260731 早于批次
# 20260930，看起来「没问题」，只是因为本地数据恰好没更新到批次之后；
# 一旦离线包补上 8/10 月的成分，回看同一批次就会静默拿到未来成分。
#
# 契约（由本段测试锁定）：
#   1. asof_date=None  -> 保持旧行为（取全局最大日），向后兼容；
#   2. asof_date 给出 -> 只用 trade_date <= asof_date 的行，取其中最大日；
#   3. 未来行必须被排除，且要在**独立审计字段**留痕（见下方 Q2 决议）；
#   4. 同日多文件仍然取并集（不能因为加了 PIT 就退化成单文件）；
#   5. 全部快照都晚于 asof_date -> 该指数**无成分**，但这不是 FAIL：
#      「PIT 窗口内确实没有成分」与「文件读不出来」必须能区分开。
#
# 【Q1 决议】非法 asof_date -> fail-closed，不回落成「不过滤」。
#   回落不过滤 = 静默重新引入 look-ahead，正是本段要消灭的东西；
#   调用方传了脏日期，宁可整段 FAIL 也不能让它拿到未过滤的未来成分。
#   判据：members 为空 + status=FAIL + 原因含 asof。
#
# 【Q2 决议】正常排除未来行**不进 errors**，改用独立审计元数据。
#   _err() 会把 OK 提升成 WARN。真实使用中离线包永远含批次日之后的成分，
#   若每次 PIT 正常排除都写 errors，io_status 将恒为 WARN，WARN 就失去
#   「真的有点不对劲」的信号价值。因此：
#     - n_future_rows_excluded / future_dates_excluded -> 审计，不改状态；
#     - 「请求了但 PIT 窗口内无任何快照」-> 才算 errors（WARN，可诊断）。

PIT_DIR = os.path.join(TMP, "iw_pit")
os.makedirs(PIT_DIR, exist_ok=True)
# 指数 000300.SH：三个截面 20260630 / 20260731 / 20261030
# 指数 000905.SH：只有 20261030 一个截面（完全落在 asof 之后）
mk_weight_parquet(os.path.join(PIT_DIR, "index_weight_0001.parquet"),
                  [("000300.SH", "600000.SH", "20260630", 1.0),
                   ("000300.SH", "600001.SH", "20260731", 1.0),
                   ("000300.SH", "600009.SH", "20261030", 1.0)])
mk_weight_parquet(os.path.join(PIT_DIR, "index_weight_data_0002.parquet"),
                  [("000300.SH", "600002.SH", "20260731", 1.0),
                   ("000905.SH", "000003.SZ", "20261030", 1.0)])

# D2-1：不传 asof_date 保持旧行为（取全局最大日，会拿到未来成分 600009）
CIO.clear_cache()
mem_pit, m_pit = call_idx(PIT_DIR, ["000300.SH"])
check("D2-1 无asof保持全局最大日(含未来行)",
      "600009.SH" in mem_pit.get("000300.SH", set()),
      mem_pit.get("000300.SH"))

# D2-2：asof_date=20260930 -> 必须排除 20261030 那行
CIO.clear_cache()
mem_p, m_p = call_idx(PIT_DIR, ["000300.SH"], asof_date="20260930")
check("D2-2 PIT排除未来截面",
      mem_p.get("000300.SH") == {"600001.SH", "600002.SH"},
      mem_p.get("000300.SH"))
_pi_p = (m_p.get("per_index") or {}).get("000300.SH") or {}
check("D2-3 PIT max_td=批次前最后截面",
      _pi_p.get("max_td") == "20260731", _pi_p)

# D2-4：同日跨族并集不能因为加了 PIT 而退化
check("D2-4 PIT下同日跨族仍并集",
      len(mem_p.get("000300.SH", set())) == 2, mem_p.get("000300.SH"))

# D2-5：不同 asof_date 必须给出不同成分（否则 PIT 形同虚设）
CIO.clear_cache()
mem_q, m_q = call_idx(PIT_DIR, ["000300.SH"], asof_date="20260630")
check("D2-5 更早asof取更早截面",
      mem_q.get("000300.SH") == {"600000.SH"}, mem_q.get("000300.SH"))

# D2-6：缓存键必须含 asof_date —— 同目录不同批次日复用彼此结果是最隐蔽的
# look-ahead：第一次按 20260930 缓存，第二次问 20260630 却拿到 7 月成分。
CIO.clear_cache()
m1, _ = call_idx(PIT_DIR, ["000300.SH"], asof_date="20260930")
m2, cm2 = call_idx(PIT_DIR, ["000300.SH"], asof_date="20260630")
check("D2-6 不同asof不共享缓存",
      cm2.get("cache_hit") is False, cm2.get("cache_hit"))
check("D2-7 不同asof结果不同",
      m1.get("000300.SH") != m2.get("000300.SH"),
      (m1.get("000300.SH"), m2.get("000300.SH")))

# D2-8：同一 asof 二次调用才允许命中缓存
CIO.clear_cache()
_, c1 = call_idx(PIT_DIR, ["000300.SH"], asof_date="20260930")
_, c2 = call_idx(PIT_DIR, ["000300.SH"], asof_date="20260930")
check("D2-8 同asof二次命中缓存", c2.get("cache_hit") is True,
      c2.get("cache_hit"))
_pi_c2 = (c2.get("per_index") or {}).get("000300.SH") or {}
check("D2-9 缓存命中max_td不回退",
      _pi_c2.get("max_td") == "20260731", _pi_c2)

# D2-10（Q2）：正常排除未来行走独立审计字段，且**不得**污染状态。
# 若把正常 PIT 排除写进 errors，_err 会把 OK 提成 WARN，真实批次下
# io_status 将恒为 WARN，WARN 就失去告警价值。
check("D2-10 排除未来行记入审计字段",
      (m_p.get("n_future_rows_excluded") or 0) >= 1,
      m_p.get("n_future_rows_excluded"))
check("D2-10b 未来日期清单可审计",
      "20261030" in (m_p.get("future_dates_excluded") or []),
      m_p.get("future_dates_excluded"))
check("D2-10c 正常PIT排除不产生 errors",
      not m_p.get("errors"), m_p.get("errors"))
check("D2-10d 正常PIT排除状态仍为OK",
      m_p.get("status") == OK, m_p.get("status"))

# D2-11：全部快照都晚于 asof -> 该指数在 PIT 窗口内无成分。
# 这是「窗口内没有」而不是「数据坏了」，两者必须可区分：
# 前者可诊断（WARN + 可读原因），后者才 FAIL。
CIO.clear_cache()
mem_z, m_z = call_idx(PIT_DIR, ["000905.SH"], asof_date="20260930")
check("D2-11 全部晚于asof则该指数无成分", "000905.SH" not in mem_z, mem_z)
check("D2-12 全部晚于asof不是IO读取失败",
      m_z.get("status") != FAIL_S, m_z.get("status"))
check("D2-13 全部晚于asof给出可读原因",
      any("000905.SH" in str(e) for e in m_z.get("errors", [])), m_z.get("errors"))

# D2-14：部分指数有 PIT 成分、部分没有 -> 整体仍可诊断（WARN 而非 FAIL）
CIO.clear_cache()
mem_mix, m_mix = call_idx(
    PIT_DIR, ["000300.SH", "000905.SH"], asof_date="20260930")
check("D2-14 部分命中仍返回成分", "000300.SH" in mem_mix, list(mem_mix))
check("D2-15 部分命中状态WARN", m_mix.get("status") == WARN, m_mix.get("status"))

# D2-16（Q1）：非法 asof_date 必须 fail-closed，不得回落成「不过滤」。
# 回落不过滤等于静默把未来成分递给调用方，比报错危险得多。
CIO.clear_cache()
mem_bad, m_bad = call_idx(PIT_DIR, ["000300.SH"], asof_date="not-a-date")
check("D2-16 非法asof不崩溃", bool(m_bad) is not None, m_bad)
check("D2-16b 非法asof拒绝返回成分",
      not mem_bad.get("000300.SH"), mem_bad.get("000300.SH"))
check("D2-16c 非法asof为FAIL",
      m_bad.get("status") == FAIL_S, m_bad.get("status"))
check("D2-17 非法asof有可读原因",
      any("asof" in str(e).lower() for e in m_bad.get("errors", [])),
      m_bad.get("errors"))

# D2-18：merge_index_members 直调也要支持 asof（不经文件层）
_frames = [(os.path.join(PIT_DIR, "index_weight_0001.parquet"),
            pd.read_parquet(os.path.join(PIT_DIR, "index_weight_0001.parquet"))),
           (os.path.join(PIT_DIR, "index_weight_data_0002.parquet"),
            pd.read_parquet(os.path.join(PIT_DIR, "index_weight_data_0002.parquet")))]
_m, _mm = call_merge(_frames, ["000300.SH"], asof_date="20260930")
check("D2-18 merge层直调也走PIT",
      _m.get("000300.SH") == {"600001.SH", "600002.SH"}, _m.get("000300.SH"))
_m2, _ = call_merge(_frames, ["000300.SH"])
check("D2-19 merge层默认不过滤",
      "600009.SH" in _m2.get("000300.SH", set()), _m2.get("000300.SH"))

# D2-20：merge 层非法 asof 同样 fail-closed（两层语义必须一致）
_mb, _mbm = call_merge(_frames, ["000300.SH"], asof_date="not-a-date")
check("D2-20 merge层非法asof也拒绝成分",
      not _mb.get("000300.SH"), _mb.get("000300.SH"))
check("D2-21 merge层非法asof为FAIL",
      _mbm.get("status") == FAIL_S, _mbm.get("status"))

# D2-22：空串必须 fail-closed，不能与 None 同义。
# 空串在真实调用链里的来源几乎都是「配置项缺失 / 上游拿不到批次日」，
# 此时回落成「不过滤」= 静默把全局最新成分（含批次日之后的截面）递给
# 回看中的批次，正是本模块要消灭的 look-ahead。
# 唯一应当表达「不要 PIT」的方式是显式传 None。
CIO.clear_cache()
_mem_e, _m_e = call_idx(PIT_DIR, ["000300.SH"], asof_date="")
check("D2-22 空串asof拒绝返回成分",
      not _mem_e.get("000300.SH"), _mem_e.get("000300.SH"))
check("D2-23 空串asof为FAIL",
      _m_e.get("status") == FAIL_S, _m_e.get("status"))
_mem_e2, _m_e2 = call_merge(_frames, ["000300.SH"], asof_date="")
check("D2-24 merge层空串asof同样FAIL",
      _m_e2.get("status") == FAIL_S and not _mem_e2.get("000300.SH"),
      (_m_e2.get("status"), _mem_e2.get("000300.SH")))

# D2-25：非法 asof 不能污染缓存 —— 失败结果不写入桶，后续合法调用仍要真读。
# 否则一次配置笔误会把整个进程后续所有批次钉死在「拒绝返回」。
CIO.clear_cache()
call_idx(PIT_DIR, ["000300.SH"], asof_date="not-a-date")
_mem_after, _m_after = call_idx(PIT_DIR, ["000300.SH"], asof_date="20260930")
check("D2-25 非法asof不污染缓存",
      _mem_after.get("000300.SH") == {"600001.SH", "600002.SH"},
      _mem_after.get("000300.SH"))


print("[E] 行情末日")
# ---------------------------------------------------------------------------

days = daterange(5)
spec = {"600000.SH": [(d, 1.0) for d in days],
        "000001.SZ": [(d, -1.0) for d in days]}
p = mk_daily_parquet("daily_small.parquet", spec)
md = CIO.market_last_date(p)
check("E1 行情末日正确", md == days[-1], (md, days[-1]))

# E2-E4：降级路径（无 pyarrow 时走 read_parquet 列）
md2 = CIO.market_last_date(p)
check("E2 重复调用确定性", md == md2, (md, md2))
check("E3 文件不存在返回None",
      CIO.market_last_date(os.path.join(TMP, "no_daily.parquet")) is None)

# E5：末行日期非单调（源文件乱序），必须取最大值而非最后一行
spec_u = {"600000.SH": [(days[3], 1.0), (days[0], 1.0), (days[4], 1.0)]}
p_u = mk_daily_parquet("daily_unsorted.parquet", spec_u)
check("E5 乱序取最大日期",
      CIO.market_last_date(p_u) == days[4], CIO.market_last_date(p_u))


print("[F] 收益统一轴 / 截断 / 剔除")
# ---------------------------------------------------------------------------

rdays = daterange(10)
codes = ["600000.SH", "000001.SZ", "000002.SZ"]
spec = {
    codes[0]: [(d, 1.0) for d in rdays],
    codes[1]: [(d, 2.0) for d in rdays],
    codes[2]: [(d, 3.0) for d in rdays],
}
p = mk_daily_parquet("daily_ret.parquet", spec)

rets, m = CIO.load_returns(p, codes, window=5, end_date=rdays[6])
check("F1 窗口长度正确", len(m["axis"]) == 5, m["axis"])
check("F2 轴右端截到end_date", m["axis"][-1] == rdays[6], m["axis"])
check("F3 所有序列共享同一轴", len(set(len(v) for v in rets.values())) == 1,
      dict((k, len(v)) for k, v in rets.items()))
check("F4 无缺失代码", m.get("missing_codes") == [], m.get("missing_codes"))
check("F5 截断计数正确", m.get("n_dropped_after_end") == 3 * 3,
      m.get("n_dropped_after_end"))
check("F6 源内最晚日期留痕",
      m.get("latest_date_in_source") == rdays[-1],
      m.get("latest_date_in_source"))

# F7：职责边界 —— IO loader 只负责「统一轴 + 读取 + 标记缺失」，
# 不负责剔除常量/停牌序列（那是 core.screen_series_quality 的职责）。
# 故此处用两组断言分别锁两侧语义，避免把 loader 的职责写进 core 或反之。
rets2, m2 = CIO.load_returns(p, codes, window=5, end_date=rdays[6])
check("F7a IO 层不擅自剔除序列",
      set(rets2.keys()) == set(codes) and
      len(m2["axis"]) == len(rets2[codes[0]]),
      dict((k, len(v)) for k, v in rets2.items()))

# 构造两类坏序列，验证 core 质量筛查的两条不同分支：
#   全零收益   -> 命中「零收益占比过高（疑似停牌）」
#   非零常量   -> 命中「标准差过低（常量序列）」
# 两条分支顺序固定在 core.screen_series_quality 内，测试必须分别锁住。
# 注意：core 的 corr_min_obs 默认 30，而本夹具窗口只有 5，必须显式传
# thresholds 覆盖，否则三条断言都会先命中「有效样本不足」分支。
QTH = {"corr_min_obs": 5}
codes_q = ["600000.SH", "000001.SZ", "000002.SZ", "000003.SZ"]
_wave_a = [1.0, -2.0, 3.0, -1.0, 2.5, -3.0, 1.5, -0.5, 2.0, -2.5]
_wave_b = [-1.0, 2.0, -3.0, 1.5, -2.0, 2.5, -1.0, 3.0, -2.5, 1.0]
p_zero = mk_daily_parquet("daily_zero_ret.parquet", {
    codes_q[0]: list(zip(rdays, _wave_a)),   # 正常波动
    codes_q[1]: [(d, 0.0) for d in rdays],   # 全零 = 停牌
    codes_q[2]: [(d, 1.0) for d in rdays],   # 非零常量
    codes_q[3]: list(zip(rdays, _wave_b)),   # 正常波动（与 A 负相关）
})
rz, mz = CIO.load_returns(p_zero, codes_q, window=5, end_date=rdays[6])
q_susp = core.screen_series_quality(rz, codes_q[1], thresholds=QTH)
check("F7b 全零收益命中停牌分支",
      q_susp["passed"] is False and "停牌" in (q_susp["reason"] or ""),
      q_susp)
q_const = core.screen_series_quality(rz, codes_q[2], thresholds=QTH)
check("F7c 非零常量命中常量分支",
      q_const["passed"] is False and "常量" in (q_const["reason"] or ""),
      q_const)
q_ok = core.screen_series_quality(rz, codes_q[0], thresholds=QTH)
check("F7d 正常波动序列通过筛查", q_ok["passed"] is True, q_ok)

# F7e：常量/停牌序列不得进入相关性聚类
cl = core.correlation_clusters(
    rz, codes_q, thresholds={"corr_min_obs": 5, "corr_window": 5,
                             "corr_cluster_min": 2})
cl_codes = set()
for cl_item in (cl.get("clusters") or []):
    cl_codes |= set(cl_item.get("codes") or [])
check("F7e 坏序列不进入聚类",
      codes_q[1] not in cl_codes and codes_q[2] not in cl_codes,
      (cl_codes, cl.get("clusters")))

# F7f：样本不足分支（窗口 5 < corr_min_obs 30）必须先于停牌/常量判定
q_short = core.screen_series_quality(rz, codes_q[1])
check("F7f 样本不足优先于停牌判定",
      q_short["passed"] is False and "样本" in (q_short["reason"] or ""),
      q_short)

# F8：完全无有效样本 -> FAIL
p_empty = mk_daily_parquet("daily_empty_ret.parquet",
                           {"600000.SH": [(d, 1.0) for d in rdays]})
rets3, m3 = CIO.load_returns(p_empty, ["600000.SH"], window=5,
                             end_date="20200101")
check("F8 窗口外无样本=FAIL", m3["status"] == FAIL_S, m3)

# F9：部分代码缺数据
rets4, m4 = CIO.load_returns(p, codes + ["600999.SH"], window=5,
                             end_date=rdays[6])
check("F9 缺数据代码入 missing", "600999.SH" in m4.get("missing_codes", []),
      m4.get("missing_codes"))

# F10：pct_chg 单位口径 = /100（1.0 -> 0.01），不得当作 1.0
vals = rets.get(codes[0]) or []
check("F10 收益口径为pct_chg/100",
      bool(vals) and all(abs(abs(v) - 0.01) < 1e-12 for v in vals),
      vals[:5])


print("[G] IO 状态提升")
# ---------------------------------------------------------------------------

# G1-G4：核心返回 OK，IO WARN 必须把 overall 提升为 WARN
sel_dir = os.path.join(TMP, "sel")
os.makedirs(sel_dir, exist_ok=True)
csv_path = os.path.join(sel_dir, "20260910_g2_top10.csv")
with io.open(csv_path, "w", encoding="utf-8") as f:
    f.write("ts_code,score,trade_date\n")
    for i in range(6):
        f.write("60000%d.SH,1.0,20260910\n" % i)

p_sw_bad = mk_sw_parquet("sw_g.parquet", [
    ("20260910", {"801010.SI": ("农林牧渔", ["600000.SH"])}),
])

base_paths = {
    "sw_l1": p_sw_bad,
    "index_weight_dir": WDIR,
    "main_daily": p,
    "sel_v13_dir": sel_dir,
    "sel_g2_dir": sel_dir,
    "sel_ens_dir": sel_dir,
}
r = CIO.diagnose_batch("g2", "20260910", paths=base_paths,
                       thresholds={"min_candidates": 5}, today="20260930")
check("G1 端到端可跑出结果", isinstance(r.get("overall"), str), r.get("overall"))
check("G2 结果含 io 段", "io" in r, list(r.keys()))

# G3：IO FAIL 时 overall 必须 FAIL（构造：主行情文件损坏）
bad_daily = os.path.join(TMP, "daily_corrupt.parquet")
with io.open(bad_daily, "wb") as f:
    f.write(b"NOT_A_PARQUET")
paths_fail = dict(base_paths)
paths_fail["main_daily"] = bad_daily
r_fail = CIO.diagnose_batch("g2", "20260910", paths=paths_fail,
                            thresholds={"min_candidates": 5},
                            today="20260930")
check("G3 IO读失败=>overall=FAIL",
      r_fail["io"]["io_status"] == FAIL_S, r_fail["io"]["io_status"])
check("G4 IO FAIL 提升到 overall",
      r_fail["overall"] == FAIL_S, r_fail["overall"])
check("G5 IO FAIL 原因留痕",
      any("[io]" in x for x in r_fail["reasons"]), r_fail["reasons"])

# G6：已有 FAIL 不被 WARN 路径改写
paths_missing = dict(base_paths)
paths_missing["sel_g2_dir"] = os.path.join(TMP, "empty_dir")
os.makedirs(paths_missing["sel_g2_dir"], exist_ok=True)
r_miss = CIO.diagnose_batch("g2", "20260910", paths=paths_missing,
                            thresholds={"min_candidates": 5},
                            today="20260930")
check("G6 候选文件缺失=FAIL",
      r_miss["io"]["io_status"] == FAIL_S, r_miss["io"]["io_status"])

# G7：候选数为 0 时不得声称正常
check("G7 空候选不报OK",
      r_miss["overall"] in (WARN, FAIL_S), r_miss["overall"])


print("[H] 端到端真实数据集成（缺数据则 SKIP）")
# ---------------------------------------------------------------------------

REAL = CIO.default_paths()
missing = [k for k in sorted(REAL) if not os.path.exists(REAL[k])]
if missing:
    skip("H0 真实数据路径", "缺失 %s" % missing)
    print("[SKIP] H 段全部跳过")
else:
    md = CIO.market_last_date(REAL["main_daily"])
    check("H1 真实行情末日可解析", bool(md), md)
    if md:
        check("H2 行情末日格式合法", len(md) == 8 and md.isdigit(), md)

    imap, m_ind = CIO.load_sw_l1_industry_map(REAL["sw_l1"])
    check("H3 真实申万映射非空", len(imap) > 0, len(imap))
    check("H4 真实申万状态OK", m_ind["status"] == OK, m_ind["errors"][:2])
    check("H5 真实申万快照日期", bool(m_ind["snap_date"]), m_ind["snap_date"])

    CIO.clear_cache()
    mem, m_idx = CIO.load_index_members(
        REAL["index_weight_dir"],
        [c for c, _ in core.STYLE_BENCHMARKS] +
        [c for c, _ in core.BROAD_BENCHMARKS])
    check("H6 真实成分命中12个基准", len(mem) == 12, len(mem))
    # 注意：这里不能再 clear_cache()，否则等于清掉缓存后又期待命中。
    # H6 已经 clear 过，本次是同 key 的第二次调用，必须 cache_hit=True。
    _, m_idx2 = CIO.load_index_members(
        REAL["index_weight_dir"],
        [c for c, _ in core.STYLE_BENCHMARKS] +
        [c for c, _ in core.BROAD_BENCHMARKS])
    check("H7 真实成分缓存命中", m_idx2.get("cache_hit") is True,
          m_idx2.get("cache_hit"))

    # 三策略实际批次
    TODAY = "20261002"
    BATCH = "20260930"
    fn = {"v13": "20260930_selection_full.csv",
          "g2": "20260930_g2_top10.csv",
          "ens": "20260930_g2_top10.csv"}
    for kind in ("v13", "g2", "ens"):
        pth = CIO.resolve_batch_path(kind, BATCH, REAL, fn[kind])
        if not os.path.exists(pth):
            skip("H8 %s 实际批次" % kind, "候选文件不存在 %s" % os.path.basename(pth))
            continue
        rows, m = CIO.load_candidates(pth)
        check("H8 %s 候选行数" % kind, m["n_rows"] > 0, m["n_rows"])
        hit = [c for c in m["codes"] if c in imap]
        check("H9 %s 行业覆盖" % kind,
              len(hit) == len(m["codes"]), "%d/%d" % (len(hit), len(m["codes"])))

        r = CIO.diagnose_batch(kind, BATCH, REAL, fn[kind], today=TODAY)
        check("H10 %s 端到端有结论" % kind,
              r["overall"] in (OK, WARN, FAIL_S, SKIP_S), r["overall"])
        check("H11 %s IO状态非FAIL" % kind,
              r["io"]["io_status"] != FAIL_S, r["io"]["errors"][:2])
        check("H12 %s 行业维度有结论" % kind,
              r.get("section_status", {}).get("industry") in
              (OK, WARN, FAIL_S, SKIP_S),
              r.get("section_status", {}).get("industry"))
        # 确定性：同参数两次调用结论一致
        r2 = CIO.diagnose_batch(kind, BATCH, REAL, fn[kind], today=TODAY)
        check("H13 %s 端到端确定性" % kind, r["overall"] == r2["overall"],
              (r["overall"], r2["overall"]))

    # 未来日期副本：必须 FAIL，且不能被行情滞后 WARN 混淆
    fut = CIO.resolve_batch_path("g2", "20261008", REAL,
                                 "20261008_g2_top10.csv")
    if os.path.exists(fut):
        rf = CIO.diagnose_batch("g2", "20261008", REAL,
                                "20261008_g2_top10.csv", today=TODAY)
        check("H14 未来日期副本=FAIL",
              rf.get("section_status", {}).get("date") == FAIL_S,
              rf.get("section_status", {}).get("date"))
        check("H15 未来副本原因留痕",
              any("未来日期副本" in x for x in rf["reasons"]), rf["reasons"][:3])
    else:
        skip("H14 未来日期副本", "20261008 候选文件不存在")

    # 批次文件不存在 -> FAIL（不是静默OK）
    rnf = CIO.diagnose_batch("g2", "19990101", REAL, today=TODAY)
    check("H16 不存在批次=FAIL", rnf["io"]["io_status"] == FAIL_S,
          rnf["io"]["io_status"])


# ---------------------------------------------------------------------------
print("[D3] 批次输入接线（asof 透传 + style_members_date 聚合）")
# ---------------------------------------------------------------------------
# 这一段锁的是「底层 PIT 做对了，批次层有没有真的用上」：
#   load_index_members 已支持 asof_date，但 load_batch_inputs 不传 ->
#   回看历史批次照样拿全局最新成分（look-ahead 复活）；
#   per_index 里已有每个风格基准的 max_td，但没人把它汇总成
#   style_members_date 交给 core -> 风格快照陈旧度永远算不出来。
#
# 夹具的日期梯度是刻意设计的，能区分「取 min」与「取 max」两种聚合：
#   399371.SZ 停在 20260630（距批次日 72 天，超 45 天上限）
#   其余风格基准更新到 20260831（距批次日 41 天，不超限）
# 若实现取 max，stale_days=41 -> 风格段不告警 -> D3-2/D3-6 双双变红。
PITB_DIR = os.path.join(TMP, "iw_pit_batch")
os.makedirs(PITB_DIR, exist_ok=True)

_all_bench = [c for c, _ in core.STYLE_BENCHMARKS] + \
             [c for c, _ in core.BROAD_BENCHMARKS]

# 文件1（main 族）：全部 12 个基准的 20260630 初始截面
mk_weight_parquet(
    os.path.join(PITB_DIR, "index_weight_0001.parquet"),
    [("000918.CSI", "600001.SH", "20260630", 1.0),
     ("000919.CSI", "600002.SH", "20260630", 1.0),
     ("399630.SZ", "600003.SH", "20260630", 1.0),
     ("399604.SZ", "600004.SH", "20260630", 1.0),
     ("399371.SZ", "600005.SH", "20260630", 1.0),
     ("399372.SZ", "600005.SH", "20260630", 1.0),
     ("000300.SH", "600001.SH", "20260630", 1.0),
     ("000905.SH", "600002.SH", "20260630", 1.0),
     ("000852.SH", "600003.SH", "20260630", 1.0),
     ("000016.SH", "600004.SH", "20260630", 1.0),
     ("399006.SZ", "600005.SH", "20260630", 1.0),
     ("000688.SH", "600005.SH", "20260630", 1.0)])

# 文件2（main 族）：20260831 更新截面。
# 399372.SZ 换成 600007.SH —— 用于 D3-9 区分 asof 到底取了哪一天。
mk_weight_parquet(
    os.path.join(PITB_DIR, "index_weight_0002.parquet"),
    [("000918.CSI", "600001.SH", "20260831", 1.0),
     ("000919.CSI", "600002.SH", "20260831", 1.0),
     ("399630.SZ", "600003.SH", "20260831", 1.0),
     ("399604.SZ", "600004.SH", "20260831", 1.0),
     ("399372.SZ", "600007.SH", "20260831", 1.0),
     ("000300.SH", "600001.SH", "20260831", 1.0),
     ("000905.SH", "600002.SH", "20260831", 1.0)])

# 文件3（data 族）：20261030 未来截面，**必须**被 PIT 排除
mk_weight_parquet(
    os.path.join(PITB_DIR, "index_weight_data_0003.parquet"),
    [(c, "600099.SH", "20261030", 1.0) for c in _all_bench])

# 申万：10 个行业（min_valid=10 的下限），覆盖全部候选
p_sw_pit = mk_sw_parquet("sw_pit.parquet", [(
    "20260910",
    dict(("8010%02d.SI" % i, ("行业%02d" % i, ["6000%02d.SH" % i]))
         for i in range(1, 11)),
)])

# 主行情：10 只候选，末日 20260910（>= 批次日，不触发行情滞后告警）。
# 交易日数必须 >= corr_window(默认 60)：窗口不足时 load_returns 会按设计
# 报「数据覆盖不足」并把 io_status 提升为 WARN，那样 D3-8 断言的
# 「正常 PIT 排除不提升状态」就被夹具噪声污染，测不出 PIT 真实语义。
# pct_chg 也必须逐日变动：常量序列方差为 0，相关性无定义，
# screen_series_quality 会判为停牌/常量而剔除，风格段跟着失真。
def _wave(i, j):
    """确定性伪随机波形：同票跨日有方差，跨票相位不同（相关性非平凡）。"""
    return (((i * 7 + j * 13) % 11) - 5) * 0.37 + (((i + j) % 5) - 2) * 0.11


# 用真实工作日序列，而不是手工拼 "2026-06-31" 这类不存在的日期：
# 宽松解析虽然不会崩，但会让「窗口天数」与「有效交易日数」悄悄不等，
# 夹具噪声一旦混进红测基线，后面就无法区分是生产缺口还是测试造错。
_pit_days = [t.strftime("%Y%m%d") for t in
            pd.bdate_range("2026-06-01", "2026-09-10")]
_cand = ["6000%02d.SH" % i for i in range(1, 11)]
p_daily_pit = mk_daily_parquet("daily_pit.parquet", {
    c: [(d, _wave(i, j)) for j, d in enumerate(_pit_days)]
    for i, c in enumerate(_cand)
})
assert _pit_days[-1] == "20260910" and len(_pit_days) >= 60, \
    (len(_pit_days), _pit_days[-1])

sel_pit = os.path.join(TMP, "sel_pit")
os.makedirs(sel_pit, exist_ok=True)
_csv_pit = os.path.join(sel_pit, "20260910_g2_top10.csv")
with io.open(_csv_pit, "w", encoding="utf-8") as f:
    f.write("ts_code,score,trade_date\n")
    for c in _cand:
        f.write("%s,1.0,20260910\n" % c)

pit_paths = {
    "sw_l1": p_sw_pit,
    "index_weight_dir": PITB_DIR,
    "main_daily": p_daily_pit,
    "sel_v13_dir": sel_pit,
    "sel_g2_dir": sel_pit,
    "sel_ens_dir": sel_pit,
}

CIO.clear_cache()
inp_pit = CIO.load_batch_inputs("g2", "20260910", paths=pit_paths)

check("D3-1 返回含 style_members_date",
      "style_members_date" in inp_pit, sorted(inp_pit.keys()))
# 聚合口径 = min(风格基准 max_td)：399371.SZ 停在 20260630，
# 其余更新到 20260831，取 min 才不会掩盖 72 天的陈旧快照。
check("D3-2 style_members_date 取最旧快照",
      inp_pit.get("style_members_date") == "20260630",
      inp_pit.get("style_members_date"))

_all_mem = set()
for _s in (inp_pit.get("style_members") or {}).values():
    _all_mem |= set(_s)
for _s in (inp_pit.get("index_members") or {}).values():
    _all_mem |= set(_s)
check("D3-3 未来成分已被PIT排除", "600099.SH" not in _all_mem,
      sorted(_all_mem))
check("D3-4 宽基成分按批次日截断",
      (inp_pit.get("index_members") or {}).get("000300.SH") == {"600001.SH"},
      (inp_pit.get("index_members") or {}).get("000300.SH"))

r_pit = CIO.diagnose_batch("g2", "20260910", paths=pit_paths,
                           today="20260930")
_st = (r_pit.get("sections") or {}).get("style") or {}
check("D3-5 端到端风格段拿到快照日期",
      _st.get("stale_days") == 72, _st.get("stale_days"))
check("D3-6 陈旧超限产生WARN",
      _st.get("status") == WARN
      and any("滞后" in x for x in (_st.get("reasons") or [])),
      (_st.get("status"), _st.get("reasons")))

# D3-7：正常 PIT 排除未来行不是缺陷，**不得**进 errors。
# 若实现把排除写进 errors，io_status 会被恒定提升为 WARN，
# 于是所有真实批次都带告警 —— 告警就失去筛选价值。
check("D3-7 正常PIT排除不进errors",
      not any("未来" in x for x in inp_pit.get("errors", [])),
      inp_pit.get("errors"))
check("D3-8 正常PIT排除不提升io状态",
      inp_pit.get("io_status") == OK, inp_pit.get("io_status"))

# D3-9：asof 基准日必须是 signal_date（行内 trade_date 最小值），
# 不是 file_date。行内快照日 20260630 早于文件名 20260910，
# 若按 file_date 取 asof，20260831 的截面会被误当成「当时已知」。
sel_pit2 = os.path.join(TMP, "sel_pit2")
os.makedirs(sel_pit2, exist_ok=True)
_csv_pit2 = os.path.join(sel_pit2, "20260910_g2_top10.csv")
with io.open(_csv_pit2, "w", encoding="utf-8") as f:
    f.write("ts_code,score,trade_date\n")
    for c in _cand:
        f.write("%s,1.0,20260630\n" % c)
pit_paths2 = dict(pit_paths)
pit_paths2["sel_g2_dir"] = sel_pit2
CIO.clear_cache()
inp_pit2 = CIO.load_batch_inputs("g2", "20260910", paths=pit_paths2)
check("D3-9 asof取signal_date而非file_date",
      (inp_pit2.get("style_members") or {}).get("399372.SZ") == {"600005.SH"},
      (inp_pit2.get("style_members") or {}).get("399372.SZ"))

# D3-10：全部风格基准在 PIT 窗口内无快照 -> style_members_date=None。
# 不允许拿「全局最大日」凑一个日期出来（那就是 look-ahead）。
NOPIT_DIR = os.path.join(TMP, "iw_pit_nopit")
os.makedirs(NOPIT_DIR, exist_ok=True)
mk_weight_parquet(
    os.path.join(NOPIT_DIR, "index_weight_0001.parquet"),
    [(c, "600001.SH", "20261030", 1.0) for c in _all_bench])
pit_paths3 = dict(pit_paths)
pit_paths3["index_weight_dir"] = NOPIT_DIR
CIO.clear_cache()
inp_pit3 = CIO.load_batch_inputs("g2", "20260910", paths=pit_paths3)
check("D3-10 无PIT快照时style_members_date为None",
      inp_pit3.get("style_members_date") is None,
      inp_pit3.get("style_members_date"))
check("D3-11 无PIT快照时风格成分为空",
      not inp_pit3.get("style_members"), inp_pit3.get("style_members"))

# D3-12/13：批次日无法确定时，**不得**把 asof 悄悄退化成 None。
# asof_date=None 在 load_index_members 里的语义是「显式关闭 PIT」，
# 会取全局最新截面（含 20261030 的 600099.SH）—— 也就是把 look-ahead
# 原样请回来。所以这里必须 fail-closed：不给成分，而不是给最新成分。
# 用文件名无日期 + 行内无 trade_date 制造「批次日不可解析」。
sel_pit3 = os.path.join(TMP, "sel_pit_nodate")
os.makedirs(sel_pit3, exist_ok=True)
_csv_pit3 = os.path.join(sel_pit3, "top10.csv")   # 文件名里没有 8 位日期
with io.open(_csv_pit3, "w", encoding="utf-8") as f:
    f.write("ts_code,score\n")                   # 行内也没有 trade_date 列
    for c in _cand:
        f.write("%s,1.0\n" % c)
pit_paths4 = dict(pit_paths)
pit_paths4["sel_g2_dir"] = sel_pit3
CIO.clear_cache()
inp_pit4 = CIO.load_batch_inputs("g2", "20260910", paths=pit_paths4,
                                 filename="top10.csv")
_mem4 = set()
for _s in (inp_pit4.get("style_members") or {}).values():
    _mem4 |= set(_s)
for _s in (inp_pit4.get("index_members") or {}).values():
    _mem4 |= set(_s)
check("D3-12 批次日不可解析时不给未来成分", "600099.SH" not in _mem4,
      sorted(_mem4))
check("D3-13 批次日不可解析时fail-closed",
      inp_pit4.get("io_status") == FAIL_S, inp_pit4.get("io_status"))


# ---------------------------------------------------------------------------
print("=" * 74)
# 崩溃兜底不能变成假阳性来源：call_idx/call_merge 捕获 TypeError 后返回的
# 空 dict + CRASH 状态，会让「不应出现某文案」「成员为空」这类断言误判为通过。
# 这里把崩溃本身统一记为失败。
check("Z1 PIT调用无签名崩溃", CRASH[0] == 0, CRASH[0])
print("PASS=%d FAIL=%d SKIP=%d" % (PASS[0], len(FAIL), len(SKIPPED)))
if SKIPPED:
    print("跳过清单:")
    for s in SKIPPED:
        print("  - %s" % s)
if FAIL:
    print("失败清单:")
    for f in FAIL:
        print("  - %s" % f)
print("[CORR-DIAG-IO-TEST-DONE]")
raise SystemExit(1 if FAIL else 0)