# -*- coding: utf-8 -*-
"""同批次选股相关性诊断 · IO 数据适配层。

职责
----
把真实数据文件读成 corr_diag_core 需要的纯数据形态，再调用 diagnose()。

与核心层的关系
--------------
* core（corr_diag_core）是纯函数、无 IO、无全局可变状态，可脱离数据文件单测；
* 本模块是**唯一**做文件系统访问的地方，每个 loader 一律返回
  ``(data, meta)``，meta 内含 status 与 errors。任何读取异常都留痕，
  交给 core 的 fail-closed 语义处理，**绝不静默返回「正常」**。

实测数据源结构（2026-10-02 核验，勿凭记忆改写）
----------------------------------------------
1. ``D:/astock/index/sw_l1_daily.parquet``
   ``MultiIndex=(trade_date:Timestamp, index_code:'801010.SI')``，
   **没有 trade_date 列**（日期在索引里），行业名在 ``name`` 列，
   成分在 ``con_codes`` 列（numpy 数组）。
   最新截面 2026-08-14：439 行中仅 31 行 con_codes 有效 = 申万一级全集。

2. ``D:/astock/index_weight/index_weight_*.parquet`` 与
   ``index_weight_data_*.parquet``：
   **两族 schema 完全相同**（index_code/con_code/trade_date/weight），
   ``main`` 族 788 文件（每文件单index_code），
   ``data`` 族 1278 文件（单文件含多个 index_code）。
   两族 index_code **有重叠**（000918/000919/399630/399604/000905 各 2 文件），
   因此必须跨族**取最大 trade_date 后并集**，只扫一族会漏成分。
   注意 ``index_weight_*.parquet`` 这个 glob 本身已包含 ``_data_`` 文件，
   不能叠加第二个 glob，否则同一文件被数两次。

3. ``D:/astock/daily/stock_daily.parquet``
   ``MultiIndex=(trade_date, ts_code)``，1465 万行 × 31 列，
   支持 ``filters`` 下推（实测 2 只票全历史 0.30s），**禁止全表读**。
   收益口径固定 ``pct_chg / 100``（不用 close.pct_change()：
   后者有弃用告警且除权口径会扭曲长期收益）。

4. 候选 CSV 编码统一 ``utf-8-sig``。行数并非都是 top10：
   V1.3 selection_full=10 行、G2 top10=9 行、ENS=7 行，
   行内 trade_date 格式也不统一（V1.3 用 ``2026-09-29``，
   G2/ENS 用 ``20260930``）——统一交给 core.parse_compact_date 解析。
"""
from __future__ import absolute_import

import glob
import io
import os
import re

import pandas as pd

import corr_diag_core as core


# ---------------------------------------------------------------------------
# 默认路径（来自 data_config，避免多处硬编码；测试一律显式传参，不走这里）
# ---------------------------------------------------------------------------

def default_paths():
    """返回默认数据路径。import data_config 失败时回退到 D:/astock 常量。

    data_config 位于项目根目录，与本模块（research/）不同级，
    故按相对位置定位而不是依赖 sys.path 顺序。
    """
    astock = "D:/astock"
    project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_cfg = os.path.join(project, "data_config.py")
    if os.path.exists(data_cfg):
        try:
            import importlib.util as _ilu
            spec = _ilu.spec_from_file_location("_p16_data_config", data_cfg)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            astock = getattr(mod, "ASTOCK_DIR", astock)
        except Exception:
            pass
    sel = os.path.join(project, "data", "selections")
    return {
        "sw_l1": os.path.join(astock, "index", "sw_l1_daily.parquet"),
        "index_weight_dir": os.path.join(astock, "index_weight"),
        "main_daily": os.path.join(astock, "daily", "stock_daily.parquet"),
        "sel_v13_dir": sel,
        "sel_g2_dir": os.path.join(sel, "g2"),
        "sel_ens_dir": os.path.join(sel, "g2_ens"),
    }


# ---------------------------------------------------------------------------
# 状态标记（与 core 保持一致的取值，避免 IO 层自造一套）
# ---------------------------------------------------------------------------

_OK = core.STATUS_OK
_WARN = core.STATUS_WARN
_FAIL = core.STATUS_FAIL


def _meta(status=_OK, **kw):
    m = {"status": status, "errors": []}
    m.update(kw)
    return m


def _err(meta, msg):
    meta["errors"].append(msg)
    if meta["status"] == _OK:
        meta["status"] = _WARN
    return meta


def _fail(meta, msg):
    """硬失败：连「部分可用」都谈不上，必须是 FAIL 而不是 WARN。

    与 _err 的区别：_err 用于「拿到了数据但不完美」（如窗口不足、
    个别候选无行情），_fail 用于「压根没拿到可用数据」。若把后者也降级成
    WARN，core 的维度会走 SKIP 分支，最终报告呈现为「一切正常」——
    这正是本模块要杜绝的缺数据静默。
    """
    meta["errors"].append(msg)
    meta["status"] = _FAIL
    return meta


# ---------------------------------------------------------------------------
# 纯工具（无 IO，可离线单测）
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"(\d{8})")


def parse_date_from_filename(path):
    """从候选文件名提取批次日期（YYYYMMDD），失败返回 None。

    实测三种命名都能命中：
      20260930_selection_full.csv / 20260930_g2_top10.csv

    path 允许为 None/空串（调用方可能拿到缺失路径），必须返回 None
    而不是抛 TypeError —— 这里一旦抛错，整条诊断链在日期防御之前就断了。
    """
    if not path:
        return None
    try:
        base = os.path.basename(str(path))
    except Exception:
        return None
    if not base:
        return None
    m = _DATE_RE.search(base)
    if not m:
        return None
    return core.parse_compact_date(m.group(1))


# 交易所后缀按代码前缀判定。前缀必须长前缀在前（688 先于 60），
# 且 920（北交所）先于 900（B股沪市）。
_SUFFIX_BY_PREFIX = (
    ("688", "SH"), ("689", "SH"), ("60", "SH"), ("5", "SH"), ("900", "SH"),
    ("920", "BJ"), ("43", "BJ"), ("83", "BJ"), ("87", "BJ"), ("88", "BJ"),
    ("000", "SZ"), ("001", "SZ"), ("002", "SZ"), ("003", "SZ"),
    ("30", "SZ"), ("20", "SZ"), ("15", "SZ"), ("16", "SZ"), ("18", "SZ"),
)

_STOCK_SUFFIX = ("SH", "SZ", "BJ")


def _guess_exchange(code6):
    for pre, suf in _SUFFIX_BY_PREFIX:
        if code6.startswith(pre):
            return suf
    return None


def normalize_code(x):
    """归一化股票代码为形如 000920.SZ 的字符串，脏值/非股票代码返回 None。

    为什么必须补后缀：行业映射（申万 con_codes）、指数成分（con_code）、
    主行情（ts_code）三处全用带后缀形态做键，而候选 CSV 的 ts_code 列
    未必带后缀。不归一化就会「同一只票在三个数据源里是三个键」，
    行业覆盖率和相关性聚类会静默全空 —— 且不会报错，只会得到干净的空结论。

    数值型输入（CSV 未加 dtype=str 时）会丢前导零，故按 6 位补齐。
    """
    if x is None:
        return None

    if isinstance(x, float):
        if x != x or x in (float("inf"), float("-inf")):
            return None
        s = "%.0f" % x
    else:
        s = str(x).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None

    s = s.upper()
    if "." in s:
        head, _, tail = s.rpartition(".")
        if tail not in _STOCK_SUFFIX or not head.isdigit():
            return None
        return head + "." + tail if len(head) == 6 else None

    if not s.isdigit():
        return None
    if len(s) < 6:
        s = s.zfill(6)
    if len(s) != 6:
        return None
    suf = _guess_exchange(s)
    return s + "." + suf if suf else None


def pick_latest_valid_sw(frame, min_valid=10, industry_col="name",
                        codes_col="con_codes"):
    """从申万一级长表里挑出「最新且 con_codes 足够有效」的截面。

    为什么要回退而不是直接用最新日期：
    实测最新截面 439 行中只有 31 行 con_codes 有效，若哪天某截面只剩
    几个行业有效，直接用会得到残缺映射，从而误判行业集中度。
    故按日期倒序找第一个有效行业数 >= min_valid 的截面，并留痕。

    返回 (截面DataFrame, 选用日期YYYYMMDD, 遍历过的日期列表)
    """
    if frame is None or frame.empty:
        return None, None, []
    # 同时保留原始索引值与归一化日期：真实 parquet 的索引层是
    # pandas.Timestamp，比较必须用原始值，不能用归一化后的 "YYYYMMDD" 字符串。
    raw_by_d8 = {}
    for raw in frame.index.get_level_values(0).unique():
        p = core.parse_compact_date(raw)
        if p and p not in raw_by_d8:
            raw_by_d8[p] = raw
    dates = sorted(raw_by_d8.keys(), reverse=True)

    tried = []
    for d8 in dates:
        raw = raw_by_d8[d8]
        sub = frame[frame.index.get_level_values(0) == raw]
        ok = _valid_codes_mask(sub, codes_col)
        n_valid = int(ok.sum())
        tried.append({"date": d8, "n_rows": int(len(sub)), "n_valid": n_valid})
        if n_valid >= min_valid:
            return sub[ok], d8, tried
    return None, None, tried


def _valid_codes_mask(sub, codes_col):
    """标记 con_codes 为非空序列的行（兼容 list/tuple/np.ndarray）。"""
    mask = []
    for v in sub[codes_col]:
        if v is None or isinstance(v, (str, bytes, float)):
            mask.append(False)
            continue
        try:
            mask.append(len(v) > 0)
        except TypeError:
            mask.append(False)
    return pd.Series(mask, index=sub.index)


def _norm_asof(asof_date, meta):
    """规范化 PIT 基准日，返回 (YYYYMMDD 或 None, 是否可继续)。

    fail-closed 语义：调用方**显式传了** asof_date 却给了解析不出来的值，
    绝不能回落成「不过滤」——那等于静默把未来成分递给回看中的批次，
    正是本模块要消灭的 look-ahead。宁可整段 FAIL。

    只有 None 表示「调用方明确不要 PIT」，保留旧的不过滤行为。
    空串**不算** None：真实调用链里空串几乎都来自「配置项缺失 /
    上游拿不到批次日」，把它当成「不过滤」= 把 look-ahead 又请回来。
    想要不过滤就显式传 None，这是有意的严格。
    """
    if asof_date is None:
        return None, True
    try:
        a = core.parse_compact_date(str(asof_date))
    except Exception:
        a = None
    if not a or len(a) != 8 or not a.isdigit():
        _fail(meta, "asof_date 无法解析为 YYYYMMDD（PIT 基准日非法）：%r；"
                    "为避免把批次日之后的成分混入回看，已拒绝返回成分"
                    "（如需关闭 PIT 请显式传 None，不要传空串）"
                    % (asof_date,))
        return None, False
    return a, True


def merge_index_members(frames_by_file, target_codes, asof_date=None):
    """跨文件族合并指数成分：对每个 index_code 取最大 trade_date 后并集成分。

    frames_by_file : list[(路径, DataFrame)]，每帧含 index_code/con_code/trade_date
    target_codes   : 只关心的 index_code 集合（早过滤，避免把 1657 个指数全读进来）
    asof_date      : PIT 基准日（YYYYMMDD）。给出时只用 trade_date <= asof_date
                     的行，再取其中最大日；None 表示不过滤（向后兼容）。

    返回 (members, meta)
      members : {index_code: set(con_code)}
      meta    : 附带每指数的 max_td / n_members / n_source_files，
                以及「两族重叠」的证据，供人工审计。
                被 PIT 排除的未来行记在 n_future_rows_excluded /
                future_dates_excluded（**不进 errors**：正常排除不是缺陷，
                写 errors 会把状态恒提成 WARN，WARN 就失去告警价值）。
    """
    meta = _meta(n_files=0, per_index={}, cross_family={},
                 n_future_rows_excluded=0, future_dates_excluded=[],
                 asof_date=None, no_pit_snapshot=[])
    want = set(str(c) for c in (target_codes or []))
    if not want:
        return {}, meta

    asof, asof_ok = _norm_asof(asof_date, meta)
    if not asof_ok:
        return {}, meta
    meta["asof_date"] = asof

    acc = {}   # code -> {"td": str, "members": set, "files": set}
    fut_dates = set()
    n_fut = 0
    seen_any = set()   # 在文件里出现过的目标指数（不论是否落在 PIT 窗口内）

    for path, df in frames_by_file:
        if df is None or df.empty:
            continue
        need = [c for c in ("index_code", "con_code", "trade_date") if c in df.columns]
        if len(need) < 3:
            _err(meta, "成分文件缺列，跳过：%s（实际列=%s）"
                 % (os.path.basename(path), list(df.columns)))
            continue
        meta["n_files"] += 1
        sub = df[need]
        codes = sub["index_code"].astype(str)
        hit = codes.isin(want)
        if not hit.any():
            continue
        sub = sub[hit]
        fam = "data" if "_data_" in os.path.basename(path) else "main"
        base = os.path.basename(path)
        sub = sub.copy()
        sub["_td"] = sub["trade_date"].astype(str).str.replace(
            "-", "", regex=False).str.slice(0, 8)
        sub_codes = sub["index_code"].astype(str)
        for code, g in sub.groupby(sub_codes.values):
            c = str(code)
            seen_any.add(c)
            if asof:
                # PIT 排除：晚于基准日的截面是「当时还看不到的成分」，
                # 回看批次时用它就是标准 look-ahead。
                fut = g[g["_td"] > asof]
                if len(fut):
                    n_fut += len(fut)
                    fut_dates.update(x for x in fut["_td"].tolist()
                                     if x and x != "None")
                    g = g[g["_td"] <= asof]
                if g.empty:
                    continue
            td = g["_td"].max()
            if not td or td == "None":
                continue
            rec = acc.setdefault(c, {"td": None, "members": set(), "files": set()})
            rec["files"].add((base, fam))
            if rec["td"] is None or td > rec["td"]:
                rec["td"] = td
                rec["members"] = set(g[g["_td"] == td]["con_code"].astype(str))
            elif td == rec["td"]:
                rec["members"] |= set(g[g["_td"] == td]["con_code"].astype(str))

    meta["n_future_rows_excluded"] = n_fut
    meta["future_dates_excluded"] = sorted(fut_dates)

    members = {}
    for c in sorted(acc):
        rec = acc[c]
        if not rec["members"]:
            _err(meta, "指数 %s 在 %s 无有效成分" % (c, rec["td"]))
            continue
        members[c] = rec["members"]
        fams = sorted(set(f for _, f in rec["files"]))
        meta["per_index"][c] = {
            "max_td": rec["td"],
            "n_members": len(rec["members"]),
            "n_source_files": len(rec["files"]),
            "families": fams,
        }
        if len(rec["files"]) > 1:
            meta["cross_family"][c] = sorted(b for b, _ in rec["files"])

    # 「文件里有、但 PIT 窗口内一个截面都没有」——必须与「文件里根本没有
    # 这个指数」区分开：前者是数据时点问题（可诊断），后者才是缺数据。
    no_pit = sorted((seen_any & want) - set(members)) if asof else []
    meta["no_pit_snapshot"] = no_pit

    missing = sorted(want - set(members))
    if missing:
        # 区分三种情况：全缺=FAIL（维度完全无数据，结论不可信）；
        # 部分缺=WARN（仍可诊断，只是不够完整）。不能一律降级成 WARN。
        msg = "目标指数无成分数据：%s" % ",".join(missing)
        if no_pit and set(missing) == set(no_pit):
            # 请求的指数都在文件里，但 PIT 基准日之前没有任何截面。
            # 这不是读取失败，是「当时确实还没有成分快照」，可诊断。
            _err(meta, msg + "（PIT 基准日 %s 之前无成分快照，"
                 "文件内最早快照晚于基准日：%d/%d）"
                 % (asof, len(no_pit), len(want)))
        elif not members:
            _fail(meta, msg + "（目标 %d 个指数全部缺失）" % len(want))
        else:
            _err(meta, msg + "（命中 %d/%d）" % (len(members), len(want)))
    return members, meta


# ---------------------------------------------------------------------------
# Loader（真实文件访问）
# ---------------------------------------------------------------------------

def load_candidates(path, code_col="ts_code", date_col="trade_date"):
    """读单份候选 CSV，返回 (rows, meta)。

    rows : list[dict]，**保持文件原始行序**（detect_date_clone 依赖顺序一致）
    meta : {file_date, row_trade_dates, n_rows, codes}
    """
    meta = _meta(path=path, file_date=None, row_trade_dates=[],
                 n_rows=0, codes=[])
    if not path or not os.path.exists(path):
        return [], _fail(meta, "候选文件不存在：%s（无候选即无诊断对象）" % path)

    try:
        df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    except Exception as e:
        return [], _fail(meta, "候选文件读取失败：%s（%s）" % (path, e))

    if code_col not in df.columns:
        return [], _fail(meta, "候选文件缺列 %s（实际列=%s）"
                         % (code_col, list(df.columns)))

    rows = df.fillna("").to_dict("records")
    codes = []
    for r in rows:
        c = normalize_code(r.get(code_col))
        r["ts_code"] = c
        codes.append(c)
    rows = [r for r in rows if r["ts_code"]]

    meta["file_date"] = parse_date_from_filename(path)
    meta["n_rows"] = len(rows)
    meta["codes"] = codes
    meta["row_trade_dates"] = [
        r.get(date_col) for r in rows if r.get(date_col) not in (None, "")
    ]
    if not meta["file_date"]:
        _err(meta, "批次日期无法从文件名解析：%s" % os.path.basename(path))
    if not rows:
        return [], _fail(meta, "候选文件无有效代码行：%s" % os.path.basename(path))
    return rows, meta


def load_sw_l1_industry_map(path, min_valid=10):
    """读申万一级最新有效截面，返回 (ind_map, meta)。

    ind_map : {ts_code: 申万一级行业名}
    """
    meta = _meta(path=path, snap_date=None, n_valid=None, n_rows=None,
                 tried=[], index_codes=[])
    if not path or not os.path.exists(path):
        return {}, _fail(meta, "申万一级文件不存在：%s" % path)
    try:
        df = pd.read_parquet(path)
    except Exception as e:
        return {}, _fail(meta, "申万一级读取失败：%s（%s）" % (path, e))

    meta["n_rows"] = int(len(df))
    if df.index.nlevels < 2:
        return {}, _fail(meta, "申万一级表结构异常：index 层级数=%d（期望 >=2，日期在索引里）"
                         % df.index.nlevels)
    if "con_codes" not in df.columns or "name" not in df.columns:
        return {}, _fail(meta, "申万一级缺列（实际列=%s）" % list(df.columns))

    sub, used, tried = pick_latest_valid_sw(df, min_valid=min_valid)
    meta["snap_date"] = used
    meta["tried"] = tried
    if sub is None:
        return {}, _fail(meta, "申万一级无任何有效 con_codes 截面（遍历 %d 个日期）"
                         % len(tried))
    meta["n_valid"] = int(len(sub))
    meta["index_codes"] = sorted(
        set(str(x) for x in sub.index.get_level_values(-1)))

    # 2026-10-02 修复：原写法 sub.loc[(slice(None), code_name)] 在 pandas 2.x
    # 会被解析成「行切片 + 列标签」，转而去 columns 里找名为 801970.SI 的列，
    # 抛 KeyError。改为按位置取行 + dict 去重：
    #   既不依赖「层序必须是 (date, code)」的假设，也容忍同一 code 出现多行。
    lvl_vals = sub.index.get_level_values(-1)
    row_by_code = {}
    for i, cn in enumerate(lvl_vals):
        key = str(cn)
        if key in row_by_code:
            continue
        row_by_code[key] = sub.iloc[i]

    ind_map = {}
    for code_name, row in row_by_code.items():
        name = str(row["name"]).strip()
        try:
            con = row["con_codes"]
        except Exception:
            _err(meta, "申万一级 %s 缺 con_codes 值" % code_name)
            continue
        # 与 _valid_codes_mask 同思路：用 __len__ 兼容 np.ndarray，
        # 避免为一次类型判断在 IO 层引入 numpy 依赖
        if hasattr(con, "__len__") and not isinstance(con, (str, bytes)):
            seq = list(con)
        else:
            seq = [con]
        for c in seq:
            n = normalize_code(c)
            if n:
                ind_map[n] = name
    if not ind_map:
        return {}, _fail(meta, "申万一级截面有效但展开后行业映射为空（date=%s）" % used)
    return ind_map, meta


def _iter_index_weight_files(index_weight_dir):
    """列出全部成分文件（单次 glob，**不要**再叠加 _data_ 的第二个 glob）。"""
    return sorted(glob.glob(os.path.join(index_weight_dir, "index_weight_*.parquet")))


# 成分文件探测缓存：{目录: {(目标指数frozenset, PIT基准日): (members, meta)}}
# 原因：实测探测 2066 个文件要 7.47s，三策略各调一次 = 22s 纯重复 IO。
# 这些文件是月度静态快照，单进程内缓存是安全的；对外暴露 clear_cache()
# 供测试与长驻进程刷新使用。
#
# 【PIT 必须进键】只按目标指数集合做键，会让同一目录下的不同批次日互相
# 复用：先按 20260930 缓存，再问 20260630 却拿到 7 月成分 —— 这是最隐蔽
# 的 look-ahead，因为两次调用都「成功」，报告里看不出任何异常。
_INDEX_MEMBERS_CACHE = {}

# 状态严重度，用于「只升不降」的状态合并（避免后段 WARN 覆盖前段 FAIL）
_STATUS_RANK = {_OK: 0, _WARN: 1, _FAIL: 2}


def _raise_status(meta, new_status):
    """只升不降地提升 meta 状态。

    直接 ``meta['status'] = new`` 会把先前的 FAIL 降成 WARN：读文件失败
    已经发生，后面的「部分命中」不该把它洗成 WARN。
    """
    if _STATUS_RANK.get(new_status, 0) > _STATUS_RANK.get(meta.get("status"), 0):
        meta["status"] = new_status
    return meta


def clear_cache():
    """清空 IO 层进程内缓存（测试隔离 / 长驻进程主动刷新用）。"""
    _INDEX_MEMBERS_CACHE.clear()


def load_index_members(index_weight_dir, target_codes, use_cache=True,
                       asof_date=None):
    """读指数成分，返回 (members, meta)。members={index_code: set(con_code)}。

    实现要点：先用 columns=['index_code'] 轻量筛出命中目标指数的文件，
    再对这些文件读全三列 —— 2066 个文件全量读三列代价过高。

    asof_date : PIT 基准日（YYYYMMDD）。给出时只取 trade_date <= asof_date
                的截面；None 表示不过滤（向后兼容）。
                非法值 fail-closed：返回空成分 + status=FAIL，
                **不写缓存**（否则一次配置笔误会把进程后续所有批次钉死）。
    """
    meta = _meta(dir=index_weight_dir, n_files=0, n_probed=0, per_index={},
                 cross_family={}, n_future_rows_excluded=0,
                 future_dates_excluded=[], asof_date=None, no_pit_snapshot=[])
    want = set(str(c) for c in (target_codes or []))
    if not want:
        return {}, meta
    if not index_weight_dir or not os.path.isdir(index_weight_dir):
        return {}, _fail(meta, "指数成分目录不存在：%s" % index_weight_dir)

    # 基准日先于一切文件 IO 判定：脏日期不该付出 7 秒探测代价，
    # 也不该有机会进入缓存。
    asof, asof_ok = _norm_asof(asof_date, meta)
    if not asof_ok:
        return {}, meta
    meta["asof_date"] = asof

    key = (frozenset(want), asof)
    bucket = _INDEX_MEMBERS_CACHE.setdefault(index_weight_dir, {})
    if use_cache and key in bucket:
        members, cmeta = bucket[key]
        meta["n_files"] = cmeta.get("n_files", 0)
        meta["n_probed"] = 0
        meta["n_files_read"] = cmeta.get("n_files_read", 0)
        meta["per_index"] = cmeta.get("per_index", {})
        meta["cross_family"] = cmeta.get("cross_family", {})
        meta["n_future_rows_excluded"] = cmeta.get("n_future_rows_excluded", 0)
        meta["future_dates_excluded"] = list(
            cmeta.get("future_dates_excluded", []))
        meta["asof_date"] = cmeta.get("asof_date")
        meta["no_pit_snapshot"] = list(cmeta.get("no_pit_snapshot", []))
        meta["errors"] = list(cmeta.get("errors", []))
        meta["status"] = cmeta.get("status", _OK)
        meta["cache_hit"] = True
        return members, meta

    all_files = _iter_index_weight_files(index_weight_dir)
    meta["n_files"] = len(all_files)
    if not all_files:
        return {}, _fail(
            meta, "指数成分目录无 index_weight_*.parquet：%s"
            % index_weight_dir)

    hits = []
    for p in all_files:
        meta["n_probed"] += 1
        try:
            one = pd.read_parquet(p, columns=["index_code"])
        except Exception:
            continue
        if one is None or one.empty:
            continue
        if set(one["index_code"].astype(str)) & want:
            hits.append(p)
    if not hits:
        return {}, _fail(
            meta, "目标指数在 %d 个成分文件中均未命中：%s"
            % (len(all_files), ",".join(sorted(want))))

    frames = []
    for p in hits:
        try:
            frames.append((p, pd.read_parquet(
                p, columns=["index_code", "con_code", "trade_date"])))
        except Exception as e:
            _err(meta, "成分文件读取失败已跳过：%s（%s）" % (os.path.basename(p), e))
    members, mmeta = merge_index_members(frames, want, asof_date=asof)
    meta["n_files_read"] = len(frames)
    meta["per_index"] = mmeta["per_index"]
    meta["cross_family"] = mmeta["cross_family"]
    meta["n_future_rows_excluded"] = mmeta.get("n_future_rows_excluded", 0)
    meta["future_dates_excluded"] = list(mmeta.get("future_dates_excluded", []))
    meta["asof_date"] = mmeta.get("asof_date")
    meta["no_pit_snapshot"] = list(mmeta.get("no_pit_snapshot", []))
    meta["errors"] = list(mmeta.get("errors", []))
    _raise_status(meta, mmeta["status"])
    if not members:
        # 守卫：命中了文件却一个成分都没解析出来（列名不符/全空行），
        # 不能让「有文件」冒充「有数据」。但要排除一种合法情形：
        # 目标指数都在文件里、只是 PIT 基准日之前还没有任何截面 ——
        # 那是「当时确实没有成分」，merge 层已给出 WARN + 可读原因，
        # 在这里再升成 FAIL 就把数据时点问题误报成读取故障了。
        if meta.get("no_pit_snapshot"):
            meta["cache_hit"] = False
            bucket[key] = (members, dict(meta))
            return {}, meta
        return {}, _fail(
            meta, "命中 %d 个成分文件但未解析出任何有效成分：%s"
            % (len(frames), ",".join(sorted(want))))
    meta["cache_hit"] = False
    bucket[key] = (members, dict(meta))
    return members, meta


def market_last_date(main_daily):
    """返回本地主行情最后一个交易日（YYYYMMDD），失败返回 None。

    性能：绝对不能 ``read_parquet(columns=['pct_chg'])`` —— 那会把
    1465 万行整列读进内存（实测 1.74s/次，三策略各调一次就是 5s+）。
    parquet 的行组统计量里带每组 min/max，扫 14 个 row group 只要 0.01s，
    故按「统计量 -> 单列 -> 全表」三级降级，逐级留痕。
    """
    # 1) 行组统计量：最快，命中即返回
    try:
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(main_daily)
        names = list(pf.schema_arrow.names)
        md = pf.metadata
        if "trade_date" in names:
            ci = names.index("trade_date")
            best = None
            for rg in range(md.num_row_groups):
                st = md.row_group(rg).column(ci).statistics
                if st is not None and st.has_min_max:
                    mx = st.max
                    if best is None or str(mx) > str(best):
                        best = mx
            p = core.parse_compact_date(best)
            if p:
                return p
    except Exception:
        pass

    # 2) 只读日期列（trade_date 已是索引层，columns 会返回 0 列但仍走索引）
    for cols in (["trade_date"], ["ts_code"]):
        try:
            d = pd.read_parquet(main_daily, columns=cols)
        except Exception:
            continue
        try:
            lv = d.index.get_level_values(0) if d.index.nlevels >= 1 else d[cols[0]]
        except Exception:
            continue
        parsed = [core.parse_compact_date(x) for x in lv.unique()]
        parsed = [p for p in parsed if p]
        if parsed:
            return max(parsed)
    return None


def load_returns(main_daily, codes, window=60, end_date=None):
    """读最近 window 个交易日的 pct_chg/100，返回 (rets, meta)。

    rets : {ts_code: [float 或 None]}，**按同一日期轴对齐**（升序，长度=window）
           —— core.correlation_clusters 用 zip() 按位置配对，
           若各序列日期轴不同，相关系数会静默算错，故此处必须统一重建日期轴。

    end_date : YYYYMMDD，窗口右端**不晚于**该日（防前视偏差）。
               实测批次日 20260930 晚于行情末日 20260911，
               窗口自然落在批次日之前，无前视。

    meta : {axis, window, n_codes, n_missing_codes, last_date, end_date}
    """
    meta = _meta(window=window, axis=[], n_codes=0, missing_codes=[])
    codes = [c for c in (normalize_code(x) for x in (codes or [])) if c]
    meta["n_codes"] = len(codes)
    if not codes:
        return {}, _fail(meta, "无候选代码，收益序列为空")
    if not main_daily or not os.path.exists(main_daily):
        return {}, _fail(meta, "主行情文件不存在：%s" % main_daily)

    try:
        df = pd.read_parquet(main_daily, columns=["pct_chg"],
                             filters=[("ts_code", "in", codes)])
    except Exception as e:
        return {}, _fail(meta, "主行情读取失败：%s（%s）" % (main_daily, e))
    if df is None or df.empty:
        return {}, _fail(
            meta, "主行情无 %d 只候选的任何记录：%s"
            % (len(codes), ",".join(codes[:5])))

    idx = df.index
    if idx.nlevels >= 2:
        date_lv, code_lv = idx.get_level_values(0), idx.get_level_values(1)
    else:
        return {}, _fail(
            meta, "主行情 index 层级异常：nlevels=%d（期望 >=2）" % idx.nlevels)

    frame = pd.DataFrame({"d": date_lv, "c": code_lv.astype(str),
                          "r": pd.to_numeric(df["pct_chg"], errors="coerce").values})
    frame["d8"] = frame["d"].map(core.parse_compact_date)
    frame = frame[frame["d8"].notna()]

    end8 = core.parse_compact_date(end_date)
    meta["end_date"] = end8
    if end8:
        n_all = int(len(frame))
        keep = frame["d8"] <= end8
        n_keep = int(keep.sum())
        if n_keep == 0:
            # 截断后一条不剩 = 完全没有可用样本（批次日早于行情起点），
            # 这是硬失败：若只记 WARN 就继续，core 会拿空轴去算相关性，
            # 得到「无高相关簇」= 一切正常的假象。
            meta["n_dropped_after_end"] = n_all
            meta["latest_date_in_source"] = (
                max(frame["d8"]) if len(frame) else None)
            return {}, _fail(
                meta, "批次日 %s 之前无任何行情样本（本地行情更新范围之外）"
                % end8)
        # 记录被右端截断的行数：这些是「批次日之后」的样本，
        # 属于潜在前视偏差来源，必须留痕（此前实现恒等，永远不记录）。
        meta["n_dropped_after_end"] = n_all - n_keep
        if n_all - n_keep > 0:
            meta["latest_date_in_source"] = max(frame["d8"])
        frame = frame[keep]

    axis = sorted(set(frame["d8"]))
    axis = axis[-int(window):]
    meta["axis"] = axis
    meta["window_effective"] = len(axis)
    meta["last_date"] = axis[-1] if axis else None
    if not axis:
        return {}, _fail(meta, "重建日期轴失败，无可用交易日")
    if len(axis) < int(window):
        # 窗口不足是「部分数据」：仍能算，只是可信度下降 -> WARN 而非 FAIL
        _err(meta, "可用交易日 %d < 请求窗口 %d（数据覆盖不足）"
             % (len(axis), window))

    pivot = frame.pivot_table(index="c", columns="d8", values="r", aggfunc="last")
    pivot = pivot.reindex(index=codes, columns=axis)
    rets = {}
    for c in codes:
        # 判定「完全无行情」不能只看 index 成员资格：reindex 已把请求但源里
        # 缺失的代码补成全 NaN 行，c in pivot.index 恒为真，missing_codes 会
        # 永远为空（漏报）。正确判据是窗口内是否至少有一个有效收益值。
        if c not in pivot.index:
            meta["missing_codes"].append(c)
            rets[c] = [None] * len(axis)
            continue
        row = pivot.loc[c]
        vals = [None if pd.isna(v) else float(v) / 100.0 for v in row]
        if all(v is None for v in vals):
            # 全 NaN = 该票在窗口内一条有效行情都没有（既可能是文件里
            # 根本没有该票，也可能是该票在本窗口全停牌），两种都不能
            # 参与相关性计算，必须显式标记，否则会被当成「无风险资产」。
            meta["missing_codes"].append(c)
        rets[c] = vals
    meta["missing_codes"] = sorted(set(meta["missing_codes"]))
    if meta["missing_codes"]:
        _err(meta, "%d 只候选完全无行情记录：%s"
             % (len(meta["missing_codes"]),
                ",".join(meta["missing_codes"][:5])))
    return rets, meta


# ---------------------------------------------------------------------------
# 组合：单个批次的完整诊断输入
# ---------------------------------------------------------------------------

BATCH_SPECS = [
    ("v13", "V1.3 多因子", "20260930_selection_full.csv"),
    ("g2", "G2 大QMT桥", "20260930_g2_top10.csv"),
    ("ens", "G2 融合桥", "20260930_g2_top10.csv"),
]

_BATCH_DIR_KEY = {
    "v13": "sel_v13_dir",
    "g2": "sel_g2_dir",
    "ens": "sel_ens_dir",
}


def resolve_batch_path(kind, batch_date, paths=None, filename=None):
    """定位某策略某批次的候选文件路径（不要求存在，交给 loader 判 FAIL）。"""
    p = dict(paths or default_paths())
    # 显式校验：直接 p[_BATCH_DIR_KEY[kind]] 会抛裸 KeyError('g2_ens')，
    # 在集成场景里只看到 KeyError 很难判断是 kind 拼错还是配置缺失。
    if kind not in _BATCH_DIR_KEY:
        raise ValueError(
            "未知策略标识 kind=%r，合法取值：%s"
            % (kind, ",".join(sorted(_BATCH_DIR_KEY))))
    dir_key = _BATCH_DIR_KEY[kind]
    if dir_key not in p:
        raise KeyError("paths 缺少目录键 %s（kind=%s）" % (dir_key, kind))
    d = p[dir_key]
    fn = filename or ("%s_selection_full.csv" % batch_date if kind == "v13"
                      else "%s_g2_top10.csv" % batch_date)
    return os.path.join(d, fn)


def load_batch_inputs(kind, batch_date, paths=None, filename=None,
                      thresholds=None):
    """读入一个批次的全部诊断输入，**不调用 diagnose**（便于分层测试）。

    返回 dict: {kind, batch_date, path, rows, codes, file_date,
                 row_trade_date, signal_date, row_trade_dates, ind_map,
                 style_members, style_members_date, index_members,
                 rets, market_last_date, errors[], io_status}

    PIT 契约：指数成分一律按 signal_date（行内最早 trade_date，缺失
    回落文件名日期）截断，signal_date 不可解析时整段 fail-closed。
    """
    p = dict(paths or default_paths())
    th = dict(core.THRESHOLDS)
    if thresholds:
        th.update(thresholds)
    errs = []
    io_status = _OK

    def _absorb(tag, meta):
        nonlocal io_status
        errs.extend("[%s] %s" % (tag, e) for e in meta.get("errors", []))
        if meta.get("status") == _FAIL:
            io_status = _FAIL
        elif meta.get("status") == _WARN and io_status == _OK:
            io_status = _WARN

    path = resolve_batch_path(kind, batch_date, p, filename)
    rows, m = load_candidates(path)
    _absorb("candidates", m)

    codes = [r["ts_code"] for r in rows]
    union = sorted(set(codes))

    md = market_last_date(p["main_daily"])
    if md is None:
        errs.append("[market] 主行情末日解析失败，日期防御无法生效")
        io_status = _FAIL

    ind_map, m_ind = load_sw_l1_industry_map(p["sw_l1"])
    _absorb("sw_l1", m_ind)

    # PIT 基准日必须在加载成分**之前**算出来：asof 一旦缺位，
    # load_index_members 会按「不过滤」取全局最新截面，历史批次当场
    # 染上 look-ahead，而且症状极隐蔽——成分看着齐全，只是太新。
    rtd, signal_d = core.resolve_signal_date(
        m.get("file_date"), m.get("row_trade_dates"))
    if signal_d is None:
        # fail-closed：传 None 等于显式关闭 PIT，等于把未来成分请回来。
        # 传非法串让 _norm_asof 走 FAIL 分支，成分为空 + 状态 FAIL。
        errs.append("[date] 批次 PIT 基准日无法确定（文件名日期与行内 "
                    "trade_date 均不可解析）：已拒绝加载指数成分，"
                    "避免混入批次日之后的成分")
        all_idx, m_idx = load_index_members(
            p["index_weight_dir"],
            [c for c, _ in core.STYLE_BENCHMARKS] +
            [c for c, _ in core.BROAD_BENCHMARKS], asof_date="")
        io_status = _FAIL
    else:
        all_idx, m_idx = load_index_members(
            p["index_weight_dir"],
            [c for c, _ in core.STYLE_BENCHMARKS] +
            [c for c, _ in core.BROAD_BENCHMARKS], asof_date=signal_d)
    _absorb("index_weight", m_idx)

    style_codes = [c for c, _ in core.STYLE_BENCHMARKS]
    index_codes = [c for c, _ in core.BROAD_BENCHMARKS]
    style_members = dict((c, all_idx[c]) for c in style_codes if c in all_idx)
    index_members = dict((c, all_idx[c]) for c in index_codes if c in all_idx)

    # 风格快照日期取**最旧**（min）而非最新（max）：风格陈旧度要衡量
    # 「有多少基准的数据已经过期」，用 max 会让一个刚更新的基准把
    # 其余全部滞后的基准盖掉，陈旧告警随之失效。
    per_idx = (m_idx.get("per_index") or {}) if isinstance(m_idx, dict) else {}
    _sd = []
    for c in style_codes:
        if c not in style_members:
            continue
        td = core.parse_compact_date(
            (per_idx.get(c) or {}).get("max_td"))
        if td:
            _sd.append(td)
    style_members_date = min(_sd) if _sd else None

    for c in style_codes:
        if c not in style_members:
            errs.append("[index_weight] 风格基准 %s 无成分" % c)
            if io_status == _OK:
                io_status = _WARN
    for c in index_codes:
        if c not in index_members:
            errs.append("[index_weight] 宽基 %s 无成分" % c)
            if io_status == _OK:
                io_status = _WARN

    # 收益窗口：只覆盖本批次候选，且右端截到批次日（防前视）
    batch_d = m.get("file_date") or core.parse_compact_date(batch_date)
    rets, m_ret = load_returns(p["main_daily"], union, window=th["corr_window"],
                               end_date=batch_d)
    _absorb("returns", m_ret)

    return {
        "kind": kind,
        "batch_date": core.parse_compact_date(batch_date),
        "path": path,
        "rows": rows,
        "codes": union,
        "file_date": m.get("file_date"),
        "row_trade_date": rtd,
        "signal_date": signal_d,
        "row_trade_dates": m.get("row_trade_dates", []),
        "market_last_date": md,
        "ind_map": ind_map,
        "style_members": style_members,
        "style_members_date": style_members_date,
        "index_members": index_members,
        "rets": rets,
        "errors": errs,
        "io_status": io_status,
    }


def diagnose_batch(kind, batch_date, paths=None, filename=None,
                   thresholds=None, today=None):
    """端到端：读输入 -> 日期防御 -> core.diagnose。

    today 必须由调用方显式注入（core 约定：模块内不取系统时间，保证可复现）。
    """
    inp = load_batch_inputs(kind, batch_date, paths=paths, filename=filename,
                            thresholds=thresholds)
    date_info = core.resolve_batch_dates(
        file_date=inp["file_date"],
        row_trade_dates=inp["row_trade_dates"],
        market_last_date=inp["market_last_date"],
        today=today)
    result = core.diagnose(
        codes=inp["codes"],
        ind_map=inp["ind_map"],
        style_members=inp["style_members"],
        index_members=inp["index_members"],
        rets=inp["rets"],
        date_info=date_info,
        style_members_date=inp.get("style_members_date"),
        thresholds=thresholds)
    result["io"] = {
        "kind": kind,
        "batch_date": inp["batch_date"],
        "path": inp["path"],
        "n_rows": len(inp["rows"]),
        "signal_date": inp.get("signal_date"),
        "style_members_date": inp.get("style_members_date"),
        "io_status": inp["io_status"],
        "errors": inp["errors"],
    }
    # IO 层自身的异常必须透出，不允许被 core 的 overall 掩盖。
    # WARN 也要提升：否则「某个风格基准无成分」这类数据缺陷会被 core 的
    # 维度 SKIP/OK 吞掉，报告上看起来一切正常 —— 这正是本模块要防的
    # 「缺数据静默误报为正常」。
    io_st = inp["io_status"]
    cur = result.get("overall")
    if io_st == _FAIL and cur != _FAIL:
        result["overall"] = _FAIL
        result["reasons"].append("[io] 数据读取失败，诊断结论不可信")
    elif io_st == _WARN and cur in (_OK, None, _WARN):
        # 只在不会降级既有 FAIL 的前提下提升；SKIP 保持不变（候选数不足
        # 本就不做判定，叠加 WARN 无意义），但要在 reasons 里留痕。
        if cur != _WARN:
            result["overall"] = _WARN
        result["reasons"].append("[io] 数据读取存在告警，诊断结论可信度下降")
    for e in inp["errors"]:
        result.setdefault("io_reasons", []).append(e)
    return result