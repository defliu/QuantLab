# -*- coding: utf-8 -*-
"""同批次选股相关性诊断 · 核心纯函数层。

目的
----
识别单次选股批次是否「同质化」，即组合是否过度集中暴露于：
  1. 同一行业（申万一级）
  2. 同一风格（有限风格基准指数成员身份）
  3. 同一指数成分簇（主要宽基）
  4. 高相关资产簇（近 N 日收益相关性）

设计约束
--------
· 本模块 **纯函数、无 IO、无全局可变状态**，可脱离 astock 数据文件单测。
· 阈值集中在 THRESHOLDS，未经回测/生产验证，仅为「同质化预警」而非交易闸门。
· 任何输入异常一律 fail-closed（返回 status=SKIP/FAIL 并给 reason），
  绝不静默给出「正常」结论。
"""
from __future__ import absolute_import

import datetime as dt
import math

# ---------------------------------------------------------------------------
# 阈值（集中声明；来源=2026-09-30 批次实测分布 + 常规集中度经验值）
# ---------------------------------------------------------------------------

THRESHOLDS = {
    # 批次规模
    "min_candidates": 5,            # 少于该数量不做诊断（相关性样本不足）
    # 行业集中度（申万一级）
    "industry_top1_ratio": 0.40,    # 单一行业占比告警线
    "industry_hhi": 0.25,           # 行业 HHI 告警线
    "industry_miss_ratio": 0.20,    # 行业分类缺失占比告警线
    # 风格集中度（有限风格基准）
    "style_top1_ratio": 0.60,       # 单一风格桶占比告警线
    "style_effective_buckets": 1,   # 有效风格桶数下限
    # 风格覆盖率下限：唯一命中任一风格基准的候选占比。
    # 真实批次 20260930 实测 V1.3 仅 1/10 命中却判 OK（审核发现的缺口之一），
    # 说明低覆盖率必须进入诊断结论。默认值 0.50 为**占位值**，
    # 尚未经真实批次 + 历史样本人工审核，不得当作生产闸门。
    "style_min_coverage": 0.50,
    # 风格成分快照最大滞后天数：真实批次 20260930 但成分 max_td=20260731
    # （滞后 61 天），日期陈旧必须被说出来。默认值 45 为**占位值**，
    # 同样待人工审核后定稿。
    "style_member_max_stale_days": 45,
    # 指数成分重叠
    "index_top1_ratio": 0.50,       # 单一宽基成分占比告警线
    # 收益相关性
    "corr_window": 60,              # 相关性窗口（交易日）
    "corr_min_obs": 30,             # 每票最少有效收益样本
    "corr_max_zero_ratio": 0.50,    # 零收益占比上限（停牌剔除）
    "corr_min_std": 1e-9,           # 标准差下限（常量序列剔除）
    "corr_pair": 0.70,              # 高相关配对阈值
    "corr_cluster_min": 3,          # 成簇最小规模
}

# 有限且稳定的风格基准集合（探针四确认：以下 6 个在 index_weight 中确有成分，
# 最新截面 20260731；399370/000923/932000 无成分故不纳入，避免误告警）
STYLE_BENCHMARKS = [
    ("000918.CSI", "300成长"),
    ("000919.CSI", "300价值"),
    ("399630.SZ", "1000成长"),
    ("399604.SZ", "中小价值"),
    ("399371.SZ", "国证价值"),
    ("399372.SZ", "大盘成长"),
]

# 主要宽基（用于指数成分簇诊断）
BROAD_BENCHMARKS = [
    ("000300.SH", "沪深300"),
    ("000905.SH", "中证500"),
    ("000852.SH", "中证1000"),
    ("000016.SH", "上证50"),
    ("399006.SZ", "创业板指"),
    ("000688.SH", "科创50"),
]

STATUS_OK = "OK"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"
STATUS_SKIP = "SKIP"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _ratio(num, den):
    """安全除法：分母为 0 返回 None（调用方据此判缺数据），不返回 0 假装正常。"""
    if den is None or den <= 0:
        return None
    return float(num) / float(den)


def _round(x, nd=4):
    if x is None:
        return None
    try:
        if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
            return None
    except TypeError:
        return None
    return round(float(x), nd)


def parse_compact_date(text):
    """把 YYYYMMDD / YYYY-MM-DD / YYYYMMDDHHMMSS / 日期对象统一为 YYYYMMDD 字符串。

    解析失败返回 None（由调用方 fail-closed），不做任何猜测性补全。

    **必须支持日期对象**（2026-10-02 实测修复）：
      parquet 的 MultiIndex（astock 主行情 / 申万一级）日期层是
      ``pandas.Timestamp``，``str(Timestamp('2026-09-30'))`` 得到
      ``'2026-09-30 00:00:00'`` —— 含空格与时间尾，纯字符串清洗后
      ``isdigit()`` 恒为 False，于是**静默返回 None**，
      会让行业映射、行情末日、收益日期轴同时失效，且不报任何错。
      故先按「有 strftime 即日期对象」处理，再走字符串路径。
      用鸭子类型而非 isinstance(datetime)，避免为本模块引入依赖。
    """
    if text is None:
        return None
    # 1) 日期/时间对象（datetime.date / datetime / pd.Timestamp / np.datetime64）
    fmt = getattr(text, "strftime", None)
    if callable(fmt):
        try:
            s = fmt("%Y%m%d")
        except Exception:
            s = None
        if s and len(s) == 8 and s.isdigit():
            return s
    # 2) 字符串 / 数字
    s = str(text).strip()
    if not s:
        return None
    # 砍掉时间部分：'2026-09-30 00:00:00' / '2026-09-30T00:00:00' / '20260930 10:30'
    for sep in (" ", "T"):
        if sep in s:
            head = s.split(sep, 1)[0].strip()
            if head:
                s = head
            break
    s = s.replace("-", "").replace("/", "").replace(".", "")
    if s.isdigit() and len(s) >= 8:
        return s[:8]
    return None


# ---------------------------------------------------------------------------
# 1. 批次日期语义 + 未来日期/日期副本防御
# ---------------------------------------------------------------------------

def parse_row_trade_dates(row_trade_dates):
    """把行内 trade_date 列表解析成去重升序列表（不可解析项丢弃）。

    单独抽出来是因为 signal_date 的语义必须全局唯一：core 的日期防御
    和 IO 的 PIT 成分加载都要用「同一份最早行内日」，两处各写一遍
    迟早漂移，漂移的后果是成分按哪一天截断变得不可解释。
    """
    out = []
    for x in (row_trade_dates or []):
        p = parse_compact_date(x)
        if p and p not in out:
            out.append(p)
    out.sort()
    return out


def resolve_signal_date(file_date, row_trade_dates):
    """返回 (row_trade_date, signal_date)，任一不可解析时为 None。

    语义（与 resolve_batch_dates 完全一致，抽出来只为复用）：
      row_trade_date = 有效行内 trade_date 中**最早**值
      signal_date    = row_trade_date，缺失时回落 file_date

    取最早值而非文件名日：候选文件里的快照日可能早于文件名日期，
    用文件名日会把「当时还看不见的较新成分」放进 PIT 窗口。
    """
    parsed_rows = parse_row_trade_dates(row_trade_dates)
    rtd = parsed_rows[0] if parsed_rows else None
    return rtd, (rtd or parse_compact_date(file_date))


def resolve_batch_dates(file_date, row_trade_dates, market_last_date, today=None):
    """解析批次的三个日期语义并做防御性检查。

    参数
    ----
    file_date        : 文件名批次日期（YYYYMMDD 或 None）
    row_trade_dates  : 行内 trade_date 列表（可含 None）
    market_last_date : 本地行情可用的最后交易日（YYYYMMDD 或 None）
    today            : 判定「未来日期」的基准日（YYYYMMDD 或 None）；
                       由调用方显式注入而非内部取系统时间，保证可复现、可单测

    返回
    ----
    dict，含 file_date / row_trade_date / signal_date / market_last_date /
         stale / status / reasons

    语义约定（对应 probe3/4 发现的 V1.3 与 G2/ENS 现象）：
      · file_date      = 文件名批次日期，**权威批次标识**
      · row_trade_date = 行内 trade_date 快照日（可能早于 file_date，属正常）
      · signal_date    = 策略有效信号日，缺行内值时回落 file_date

    两类日期异常必须严格区分（v1 曾混淆，导致合法批次被误判）：
      1. **未来日期副本**（FAIL）：批次日/快照日晚于 `today`。
         G2/ENS 的 20261008 就是此类——相对今天 20261002 尚未发生，
         却已存在与 20260930 完全同内容、仅改日期的选股文件。
      2. **行情数据滞后**（WARN）：批次日/快照日晚于本地行情末日。
         20260930 晚于行情末日 20260911，但它是过去的合法交易日，
         只是 D:/astock 未更新到该日 —— 属数据新鲜度问题，不是造假。
         判据锚定 `today`（日历日）而非 `market_last_date`（数据新鲜度）。
    """
    out = {
        "file_date": parse_compact_date(file_date),
        "row_trade_date": None,
        "signal_date": None,
        "market_last_date": parse_compact_date(market_last_date),
        "today": parse_compact_date(today),
        "stale": False,
        "status": STATUS_OK,
        "reasons": [],
    }
    parsed_rows = parse_row_trade_dates(row_trade_dates)
    rtd = parsed_rows[0] if parsed_rows else None
    out["row_trade_date"] = rtd
    out["row_trade_dates_all"] = parsed_rows
    out["signal_date"] = rtd or out["file_date"]

    def _escalate(new_status):
        rank = {STATUS_OK: 0, STATUS_SKIP: 1, STATUS_WARN: 2, STATUS_FAIL: 3}
        if rank.get(new_status, 0) > rank.get(out["status"], 0):
            out["status"] = new_status

    if out["file_date"] is None:
        _escalate(STATUS_FAIL)
        out["reasons"].append("批次日期无法从文件名解析")
    if rtd is None:
        _escalate(STATUS_WARN)
        out["reasons"].append("行内 trade_date 缺失或不可解析")
    if len(parsed_rows) > 1:
        _escalate(STATUS_WARN)
        out["reasons"].append(
            "同一候选文件内存在多个行内 trade_date：%s（应只有单一快照日）"
            % ",".join(parsed_rows))
    if out["file_date"] and rtd and rtd != out["file_date"]:
        # 不直接判错：可能是上一交易日特征快照，但要留痕
        out["reasons"].append(
            "行内 trade_date=%s 与文件批次日期=%s 不一致（按上一交易日快照处理）"
            % (rtd, out["file_date"]))

    today = out["today"]
    mkt = out["market_last_date"]

    # 1) 未来日期副本：晚于今天 = 尚未发生却已有产物
    for label, val in (("文件批次日期", out["file_date"]),
                       ("行内 trade_date", rtd)):
        if val and today and val > today:
            _escalate(STATUS_FAIL)
            out["reasons"].append(
                "%s=%s 晚于今天=%s，疑似未来日期副本" % (label, val, today))

    # 2) 行情数据滞后：批次日晚于本地行情末日（数据新鲜度，非造假）
    #    注意：**只比较批次自身的日期**（file_date / 行内快照日），绝不能把 `today`
    #    混进来。`today` 是「日历日」，本地 astock 落后于日历日是常态（离线数据
    #    本来就有更新延迟），与本批次是否新鲜无关；混进来会把每一个合法批次
    #    都误判成 stale。
    if mkt:
        latest = max([x for x in (out["file_date"], rtd) if x] or [None])
        if latest and latest > mkt:
            out["stale"] = True
            _escalate(STATUS_WARN)
            out["reasons"].append(
                "行情数据滞后：本地行情末日=%s 早于批次相关日=%s，"
                "收益窗口与相关性基于 %s 及之前数据计算"
                % (mkt, latest, mkt))
    return out


def detect_date_clone(base_rows, other_rows, ignore_cols=("trade_date",)):
    """判定 other 是否为 base 的「仅改日期」副本。

    base_rows / other_rows：list[dict]，每行至少含 ts_code。
    判定：ts_code 序列完全一致，且除 ignore_cols 外所有字段逐行相同。
    仅日期不同的副本必须被识别，避免把同一批选股当成两个独立批次统计。
    """
    if not base_rows or not other_rows:
        return False
    if len(base_rows) != len(other_rows):
        return False
    base_codes = [str(r.get("ts_code", "")) for r in base_rows]
    other_codes = [str(r.get("ts_code", "")) for r in other_rows]
    if base_codes != other_codes:
        return False
    for b, o in zip(base_rows, other_rows):
        keys = set(b.keys()) & set(o.keys())
        for k in keys:
            if k in ignore_cols:
                continue
            if str(b.get(k)) != str(o.get(k)):
                return False
    return True


# ---------------------------------------------------------------------------
# 2. 行业集中度（申万一级）
# ---------------------------------------------------------------------------

def industry_concentration(codes, ind_map, thresholds=None):
    """行业集中度诊断。

    codes   : 候选 ts_code 列表
    ind_map : {ts_code: 申万一级行业名}；未覆盖的票不在表中
    """
    th = dict(THRESHOLDS)
    if thresholds:
        th.update(thresholds)

    n = len(codes)
    res = {
        "n_candidates": n,
        "n_mapped": 0,
        "n_missing": 0,
        "missing_ratio": None,
        "missing_codes": [],
        "distribution": {},
        "top1_industry": None,
        "top1_ratio": None,
        "hhi": None,
        "n_industries": 0,
        "status": STATUS_OK,
        "reasons": [],
    }
    if n <= 0:
        res["status"] = STATUS_SKIP
        res["reasons"].append("候选为空")
        return res

    dist = {}
    missing = []
    for c in codes:
        name = ind_map.get(str(c))
        if not name:
            missing.append(str(c))
            continue
        dist[name] = dist.get(name, 0) + 1
    res["n_mapped"] = sum(dist.values())
    res["n_missing"] = len(missing)
    res["missing_codes"] = sorted(missing)
    res["missing_ratio"] = _ratio(len(missing), n)
    res["n_industries"] = len(dist)
    res["distribution"] = dict(sorted(dist.items(), key=lambda kv: (-kv[1], kv[0])))

    if dist:
        top_name = sorted(dist.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        res["top1_industry"] = top_name[0]
        res["top1_ratio"] = _ratio(top_name[1], n)
        res["hhi"] = _round(sum((float(v) / n) ** 2 for v in dist.values()), 4)

    if res["missing_ratio"] is not None and res["missing_ratio"] > th["industry_miss_ratio"]:
        res["status"] = STATUS_WARN
        res["reasons"].append(
            "行业分类缺失占比 %.1f%% > 阈值 %.1f%%（缺失 %d 只：%s）"
            % (res["missing_ratio"] * 100, th["industry_miss_ratio"] * 100,
               len(missing), ",".join(sorted(missing)[:5])))
    if res["top1_ratio"] is not None and res["top1_ratio"] > th["industry_top1_ratio"]:
        res["status"] = STATUS_WARN
        res["reasons"].append(
            "单一行业 %s 占比 %.1f%% > 阈值 %.1f%%"
            % (res["top1_industry"], res["top1_ratio"] * 100,
               th["industry_top1_ratio"] * 100))
    if res["hhi"] is not None and res["hhi"] > th["industry_hhi"]:
        res["status"] = STATUS_WARN
        res["reasons"].append(
            "行业 HHI %.4f > 阈值 %.4f" % (res["hhi"], th["industry_hhi"]))
    return res


# ---------------------------------------------------------------------------
# 3. 风格集中度（有限风格基准）
# ---------------------------------------------------------------------------

def _stale_days(members_date, asof_date):
    """成分快照相对批次日滞后多少自然日。

    任一侧缺失或不可解析一律返回 None（无从判断，不猜）。
    单边缺失（只有批次日、或只有成分日）同样返回 None——否则会
    把「数据缺失」误报成「数据新鲜」，或凭空造出一个滞后天数。
    """
    md = parse_compact_date(members_date)
    ad = parse_compact_date(asof_date)
    if not md or not ad:
        return None
    try:
        d1 = dt.date(int(md[:4]), int(md[4:6]), int(md[6:8]))
        d2 = dt.date(int(ad[:4]), int(ad[4:6]), int(ad[6:8]))
    except (TypeError, ValueError):
        return None
    # 成分快照晚于批次日属于未来数据（IO 侧 PIT 未生效的信号），
    # 负数原样透出，让上层能看见异常，而不是夹成 0 当正常。
    return (d2 - d1).days


def style_concentration(codes, style_members, thresholds=None,
                         members_date=None, asof_date=None):
    """风格集中度诊断。

    codes         : 候选 ts_code 列表
    style_members : {index_code: set(con_code)}，仅需传入 STYLE_BENCHMARKS 子集
    members_date  : 成分快照交易日（YYYYMMDD），用于新鲜度判断
    asof_date     : 批次交易日（YYYYMMDD），新鲜度比较的基准

    三类语义必须分开，不能互相冒充：
      1. 风格成分数据完全不可用（无任何非空成分集） -> SKIP，原因点名「不可用」
      2. 数据可用但候选零命中                        -> SKIP，原因说「未命中」
      3. 数据可用、命中了，但覆盖率/新鲜度有问题      -> WARN

    风格维度只做预警，**永不返回 FAIL**：它是同质化提示，不是交易闸门。
    """
    th = dict(THRESHOLDS)
    if thresholds:
        th.update(thresholds)

    n = len(codes)

    # 基准可用性统计必须相对官方 STYLE_BENCHMARKS 全表，而不是入参键数：
    # IO 只在成功加载时写入 members 键，缺失的基准根本不会出现在入参里，
    # 按入参统计会让 missing 在生产里恒为 0，字段形同虚设。
    official = [c for c, _ in STYLE_BENCHMARKS]
    loaded = []
    empty_keys = []
    for idx_code in official:
        members = (style_members or {}).get(idx_code)
        if members:
            loaded.append(idx_code)
        else:
            empty_keys.append(idx_code)
    # 入参里出现但不在官方全表中的键也要留痕（数据源异常时能看出来）
    extra_keys = sorted(k for k in (style_members or {})
                        if k not in set(official))

    res = {
        "n_candidates": n,
        "n_in_any_style": 0,
        "n_no_style": 0,
        "no_style_codes": [],
        "coverage_ratio": None,
        "data_available": bool(loaded),
        "n_benchmarks_loaded": len(loaded),
        "n_benchmarks_missing": len(official) - len(loaded),
        "missing_benchmarks": sorted(empty_keys),
        "extra_benchmarks": extra_keys,
        "members_date": parse_compact_date(members_date),
        "asof_date": parse_compact_date(asof_date),
        "stale_days": None,
        "bucket_counts": {},
        "bucket_top1": None,
        "bucket_top1_ratio": None,
        "n_buckets": 0,
        "status": STATUS_OK,
        "reasons": [],
    }

    # 新鲜度判断与命中无关：即使零命中，成分快照陈旧也是数据问题，
    # 应当单独留痕（否则「数据不可用」会把这层信息一起吞掉）。
    sd = _stale_days(members_date, asof_date)
    res["stale_days"] = sd
    stale_limit = th.get("style_member_max_stale_days")
    if sd is not None and stale_limit is not None and sd > stale_limit:
        res["status"] = STATUS_WARN
        res["reasons"].append(
            "风格成分快照 %s 滞后批次日 %s 共 %d 天（上限 %d），风格归属可信度下降"
            % (res["members_date"], res["asof_date"], sd, stale_limit))

    if n <= 0:
        res["status"] = STATUS_SKIP
        res["reasons"].append("候选为空")
        return res

    if not res["data_available"]:
        # 情形 1：所有官方风格基准都没有拿到非空成分集。
        # 真实批次审核发现这里过去与「零命中」共用同一句文案，
        # 导致 G2/ENS 的 0/9 命中被误读成数据坏了。
        res["status"] = STATUS_SKIP
        res["reasons"].append(
            "风格成分数据不可用（%d/%d 个官方风格基准均无成分）"
            % (res["n_benchmarks_loaded"], len(official)))
        return res

    code_set = [str(c) for c in codes]
    dist = {}
    covered = set()
    for idx_code in sorted(style_members or {}):
        members = style_members.get(idx_code)
        if not members:
            continue
        hit = [c for c in code_set if c in members]
        if hit:
            dist["%s|%s" % (idx_code, _style_name(idx_code))] = len(hit)
            covered.update(hit)

    res["n_in_any_style"] = len(covered)
    res["n_no_style"] = n - len(covered)
    res["no_style_codes"] = sorted(set(code_set) - covered)
    res["n_buckets"] = len(dist)
    res["bucket_counts"] = dict(sorted(dist.items(), key=lambda kv: (-kv[1], kv[0])))

    # 唯一覆盖率：与 bucket_counts 的合计不同——一只票可同时落进多个风格桶，
    # 所以桶计数之和可能大于 n_in_any_style（真实 V1.3 批次 n_in=1/桶合计=2）。
    res["coverage_ratio"] = _round(_ratio(len(covered), n))

    if dist:
        top_k = sorted(dist.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        res["bucket_top1"] = top_k[0]
        res["bucket_top1_ratio"] = _ratio(top_k[1], n)

    if res["n_in_any_style"] == 0:
        # 情形 2：数据可用，候选确实没命中任何风格基准。
        # 文案绝不能出现「不可用」——那会把风格漂移误报成数据故障。
        res["status"] = STATUS_SKIP
        res["reasons"].append(
            "候选未命中任何风格基准（%d/%d 个风格基准数据可用，"
            "候选可能落在基准覆盖之外或存在风格漂移）"
            % (res["n_benchmarks_loaded"], len(official)))
        return res

    if res["n_buckets"] < th["style_effective_buckets"] + 1:
        res["status"] = STATUS_WARN
        res["reasons"].append(
            "仅命中 %d 个风格桶（下限 %d），风格暴露过于单一"
            % (res["n_buckets"], th["style_effective_buckets"] + 1))

    if res["bucket_top1_ratio"] is not None and \
            res["bucket_top1_ratio"] > th["style_top1_ratio"]:
        res["status"] = STATUS_WARN
        res["reasons"].append(
            "单一风格桶 %s 占比 %.1f%% > 阈值 %.1f%%"
            % (res["bucket_top1"], res["bucket_top1_ratio"] * 100,
               th["style_top1_ratio"] * 100))

    # 覆盖率告警：桶级判据只能看见「命中的那部分怎么分布」，
    # 看不见「九成候选根本没被任何风格基准覆盖」——真实 V1.3 批次
    # 1/10 覆盖却判 OK 就是漏在这里。
    cov = res["coverage_ratio"]
    cov_limit = th.get("style_min_coverage")
    if cov is not None and cov_limit is not None and cov < cov_limit:
        res["status"] = STATUS_WARN
        res["reasons"].append(
            "风格基准唯一覆盖率 %.1f%%（%d/%d 只命中）低于下限 %.1f%%，"
            "风格诊断对该批次代表性不足"
            % (cov * 100, res["n_in_any_style"], n, cov_limit * 100))

    return res


def _build_name_map(pairs):
    """由 (code, name) 列表建不可变名称表（模块加载期一次性生成，运行期只读）。"""
    return dict((str(c), str(n)) for c, n in pairs)


# 运行期只读映射：风格桶 + 宽基的名称表。
# 刻意不用 register_style_names() 之类的可变缓存——那会让本模块
# 出现隐式全局状态，使纯函数层的单测结果依赖导入顺序。
_STYLE_NAMES = _build_name_map(STYLE_BENCHMARKS)
_INDEX_NAMES = _build_name_map(BROAD_BENCHMARKS)


def _style_name(idx_code):
    key = str(idx_code)
    if key in _STYLE_NAMES:
        return _STYLE_NAMES[key]
    return _INDEX_NAMES.get(key, key)


# ---------------------------------------------------------------------------
# 4. 指数成分重叠
# ---------------------------------------------------------------------------

def index_overlap(codes, index_members, thresholds=None):
    """批次与主要宽基的成分重叠度。

    codes         : 候选 ts_code 列表
    index_members : {index_code: set(con_code)}
    """
    th = dict(THRESHOLDS)
    if thresholds:
        th.update(thresholds)

    n = len(codes)
    res = {
        "n_candidates": n,
        "coverage": {},
        "top1_index": None,
        "top1_index_name": None,
        "top1_ratio": None,
        "n_indexes": 0,
        "status": STATUS_OK,
        "reasons": [],
    }
    if n <= 0:
        res["status"] = STATUS_SKIP
        res["reasons"].append("候选为空")
        return res

    code_set = [str(c) for c in codes]
    dist = {}
    for idx_code, members in sorted(index_members.items()):
        if not members:
            continue
        hit = sum(1 for c in code_set if c in members)
        if hit:
            dist[idx_code] = hit

    res["n_indexes"] = len(dist)
    res["coverage"] = dict(
        (k, {"name": _style_name(k), "n": v, "ratio": _round(_ratio(v, n), 4)})
        for k, v in sorted(dist.items(), key=lambda kv: (-kv[1], kv[0])))

    if dist:
        top_k = sorted(dist.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        res["top1_index"] = top_k[0]
        res["top1_index_name"] = _style_name(top_k[0])
        res["top1_ratio"] = _round(_ratio(top_k[1], n), 4)

    if not dist:
        res["status"] = STATUS_SKIP
        res["reasons"].append("无候选命中任何宽基成分（成分数据不可用）")
        return res

    if res["top1_ratio"] is not None and res["top1_ratio"] > th["index_top1_ratio"]:
        res["status"] = STATUS_WARN
        res["reasons"].append(
            "批次 %.1f%% 集中在 %s(%s)（阈值 %.1f%%）"
            % (res["top1_ratio"] * 100, res["top1_index"],
               res["top1_index_name"], th["index_top1_ratio"] * 100))
    return res


def pairwise_batch_overlap(codes_a, codes_b):
    """两批候选的成分重叠（供跨策略比较用）。无信号即返回 None。"""
    a = set(str(c) for c in codes_a)
    b = set(str(c) for c in codes_b)
    if not a or not b:
        return None
    inter = len(a & b)
    return {
        "n_a": len(a),
        "n_b": len(b),
        "n_common": inter,
        "ratio_of_a": _round(_ratio(inter, len(a)), 4),
        "ratio_of_b": _round(_ratio(inter, len(b)), 4),
        "jaccard": _round(_ratio(inter, len(a | b)), 4),
    }


# ---------------------------------------------------------------------------
# 5. 高相关资产簇
# ---------------------------------------------------------------------------

def screen_series_quality(rets, code, thresholds=None):
    """单票收益序列质量筛查：样本数 / 零收益占比 / 标准差。

    rets : {code: [float 或 None]}，已按时间升序
    """
    th = dict(THRESHOLDS)
    if thresholds:
        th.update(thresholds)
    vals = [v for v in rets.get(code, []) if v is not None]
    n = len(vals)
    if n == 0:
        return {"code": code, "n": 0, "zero_n": 0, "zero_ratio": None,
                "std": None, "passed": False,
                "reason": "无有效收益样本"}
    zeros = sum(1 for v in vals if abs(v) < 1e-12)
    zero_ratio = float(zeros) / n
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    std = math.sqrt(var)
    passed = True
    reason = ""
    if n < th["corr_min_obs"]:
        passed = False
        reason = "有效样本 %d < %d" % (n, th["corr_min_obs"])
    elif zero_ratio > th["corr_max_zero_ratio"]:
        passed = False
        reason = "零收益占比 %.1f%% > %.1f%%（疑似停牌）" % (
            zero_ratio * 100, th["corr_max_zero_ratio"] * 100)
    elif std < th["corr_min_std"]:
        passed = False
        reason = "收益标准差 %.3e 过低（常量序列）" % std
    return {"code": code, "n": n, "zero_n": zeros, "zero_ratio": _round(zero_ratio, 4),
            "std": _round(std, 6), "passed": passed, "reason": reason}


def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sxx = syy = 0.0
    for a, b in zip(xs, ys):
        da = a - mx
        db = b - my
        sxy += da * db
        sxx += da * da
        syy += db * db
    if sxx <= 0.0 or syy <= 0.0:
        return None
    return sxy / math.sqrt(sxx * syy)


def correlation_clusters(rets, codes=None, thresholds=None):
    """高相关资产簇诊断（纯 Python 实现，不依赖 numpy，便于单测与口径固定）。

    rets  : {code: [float 或 None]}，按时间升序且各序列长度一致
    codes : 参与诊断的代码；缺省用 rets 的键排序

    流程：质量筛查 -> 成对相关 -> 阈值筛高相关对 -> 并查集聚簇
    """
    th = dict(THRESHOLDS)
    if thresholds:
        th.update(thresholds)

    code_list = sorted(str(c) for c in (codes if codes is not None else rets.keys()))
    res = {
        "n_input": len(code_list),
        "window": th["corr_window"],
        "quality": [],
        "n_passed": 0,
        "n_rejected": 0,
        "pairs": [],
        "clusters": [],
        "n_clusters": 0,
        "largest_cluster": 0,
        "max_corr": None,
        "mean_corr": None,
        "status": STATUS_OK,
        "reasons": [],
    }
    if len(code_list) < 2:
        res["status"] = STATUS_SKIP
        res["reasons"].append("候选不足 2 只，无法计算相关性")
        return res

    passed = []
    for c in code_list:
        q = screen_series_quality(rets, c, th)
        res["quality"].append(q)
        if q["passed"]:
            passed.append(c)
    res["n_passed"] = len(passed)
    res["n_rejected"] = len(code_list) - len(passed)
    if res["n_rejected"]:
        res["reasons"].append(
            "质量筛查剔除 %d 只：%s" % (
                res["n_rejected"],
                ",".join("%s(%s)" % (q["code"], q["reason"])
                         for q in res["quality"] if not q["passed"])[:200]))

    if len(passed) < 2:
        res["status"] = STATUS_SKIP
        res["reasons"].append("通过质量筛查的样本不足 2 只")
        return res

    # 成对相关（按位置对齐，None 视为缺失；样本不足返回 None）
    all_corrs = []
    for i in range(len(passed)):
        for j in range(i + 1, len(passed)):
            ca, cb = passed[i], passed[j]
            xs, ys = [], []
            for va, vb in zip(rets[ca], rets[cb]):
                if va is None or vb is None:
                    continue
                xs.append(float(va))
                ys.append(float(vb))
            if len(xs) < th["corr_min_obs"]:
                res["pairs"].append({"a": ca, "b": cb, "n": len(xs),
                                     "corr": None, "high": False,
                                     "note": "样本不足"})
                continue
            r = _pearson(xs, ys)
            res["pairs"].append({"a": ca, "b": cb, "n": len(xs),
                                 "corr": _round(r, 4),
                                 "high": bool(r is not None and r >= th["corr_pair"]),
                                 "note": ""})
            if r is not None:
                all_corrs.append(r)

    if all_corrs:
        res["max_corr"] = _round(max(all_corrs), 4)
        res["mean_corr"] = _round(sum(all_corrs) / len(all_corrs), 4)

    high = [p for p in res["pairs"] if p["high"]]
    clusters = _union_find_clusters(passed, high)
    res["clusters"] = clusters
    res["n_clusters"] = len(clusters)
    res["largest_cluster"] = max([len(c) for c in clusters]) if clusters else 0

    if res["largest_cluster"] >= th["corr_cluster_min"]:
        res["status"] = STATUS_WARN
        biggest = max(clusters, key=len)
        res["reasons"].append(
            "高相关资产簇规模 %d（阈值 %d）：%s"
            % (len(biggest), th["corr_cluster_min"], ",".join(biggest)))
    return res


def _union_find_clusters(codes, high_pairs):
    """并查集：把高相关配对连成簇，只返回 size>=2 的簇。"""
    parent = dict((c, c) for c in codes)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in high_pairs:
        ra, rb = find(p["a"]), find(p["b"])
        if ra != rb:
            parent[ra] = rb

    groups = {}
    for c in codes:
        groups.setdefault(find(c), []).append(c)
    out = [sorted(v) for v in groups.values() if len(v) >= 2]
    out.sort(key=lambda g: (-len(g), g[0]))
    return out


# ---------------------------------------------------------------------------
# 6. 汇总
# ---------------------------------------------------------------------------

_SECTION_KEYS = ("industry", "style", "index", "correlation")

_SEVERITY = {STATUS_OK: 0, STATUS_SKIP: 1, STATUS_WARN: 2, STATUS_FAIL: 3}


def aggregate_status(statuses):
    """把多个分项状态聚合成单一结论。

    语义（关键，勿改）：
      · FAIL / WARN 取最严重档；
      · **全部分项都是 SKIP（数据缺失）时返回 SKIP，绝不返回 OK**——
        「没查成」不等于「查过了没问题」，否则缺数据会被静默读成正常。
      · 混合 OK 与 SKIP 时返回 WARN：部分维度没结论，整体结论不可全信。
    """
    seen = [s for s in statuses if s]
    if not seen:
        return STATUS_SKIP
    if all(s == STATUS_SKIP for s in seen):
        return STATUS_SKIP
    # WARN/FAIL 优先：只要有任一分项查出了实质问题，整体就按它定级，
    # 不能让另一个分项的 SKIP（数据缺失）把它压成 WARN/OK。
    hard = [s for s in seen if s in (STATUS_WARN, STATUS_FAIL)]
    if hard:
        return max(hard, key=lambda s: _SEVERITY.get(s, 0))
    # 剩下的情形只含 OK / SKIP：部分维度「没查成」不足以宣称整体正常，
    # 降级为 WARN 提示结论不完整。
    if STATUS_OK in seen and STATUS_SKIP in seen:
        return STATUS_WARN
    return STATUS_OK


def diagnose(codes, ind_map=None, style_members=None, index_members=None,
             rets=None, date_info=None, thresholds=None,
             style_members_date=None):
    """汇总四类同质化维度为单一诊断结论。

    date_info : resolve_batch_dates() 的返回值（可为 None）。
                传入时其 status/reasons 参与 overall 聚合——
                日期异常（未来副本/数据滞后）不允许被四维度的 OK 掩盖。
    style_members_date : 风格成分快照交易日（YYYYMMDD），由 IO 的
                per_index 元数据聚合而来。批次日不再要求调用方重复传入，
                直接取 date_info['signal_date']（行内 trade_date，回退文件批次日），
                避免「两个日期各传一次、传错一个」的口径漂移。
                date_info 缺失时不推断 asof_date（宁可不算滞后，
                也不凭空造一个基准日）。
    """
    codes = [str(c) for c in codes]
    th = dict(THRESHOLDS)
    if thresholds:
        th.update(thresholds)

    out = {
        "n_candidates": len(codes),
        "codes": sorted(codes),
        "thresholds": th,
        "sections": {},
    }
    if date_info:
        out["date"] = date_info

    if len(codes) < th["min_candidates"]:
        out["sections"]["_skip"] = {
            "status": STATUS_SKIP,
            "reasons": ["候选数 %d < 最少诊断数量 %d，不做同质化判定"
                        % (len(codes), th["min_candidates"])],
        }
        st = STATUS_SKIP
        if date_info and date_info.get("status") in (STATUS_FAIL, STATUS_WARN):
            st = date_info["status"]
        out["overall"] = st
        out["reasons"] = list(out["sections"]["_skip"]["reasons"])
        if date_info:
            out["reasons"] += ["[date] %s" % r for r in date_info.get("reasons", [])]
        return out

    out["sections"]["industry"] = industry_concentration(
        codes, ind_map or {}, th)
    out["sections"]["style"] = style_concentration(
        codes, style_members or {}, th,
        members_date=style_members_date,
        asof_date=(date_info or {}).get("signal_date"))
    out["sections"]["index"] = index_overlap(
        codes, index_members or {}, th)
    out["sections"]["correlation"] = correlation_clusters(
        rets or {}, codes, th)

    reasons = []
    statuses = []
    keys = list(_SECTION_KEYS)
    if date_info:
        keys = ["date"] + keys
    for key in keys:
        if key == "date":
            sec = date_info or {}
        else:
            sec = out["sections"].get(key) or {}
        st = sec.get("status", STATUS_SKIP)
        statuses.append(st)
        for r in sec.get("reasons", []):
            reasons.append("[%s] %s" % (key, r))

    out["overall"] = aggregate_status(statuses)
    out["section_status"] = dict(
        (k, (date_info or {}).get("status") if k == "date"
         else (out["sections"].get(k) or {}).get("status", STATUS_SKIP))
        for k in keys)
    out["reasons"] = reasons
    return out
