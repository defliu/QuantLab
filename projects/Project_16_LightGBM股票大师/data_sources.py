# -*- coding: utf-8 -*-
"""多数据源实时行情获取模块。

优先级（由高到低）：
  1. miniQMT（本地 xtdata，实时 tick/日线，最优先）
  2. TDX（通过 MCP 调用，扩展指标最全）
  3. 腾讯财经免费 API（https://qt.gtimg.cn，基础行情兜底）
  4. 其他（回退标注）

哪个数据源通就用哪个；基础行情（最新价/开盘价/成交量/涨跌幅）优先 miniQMT/腾讯，
扩展指标（量比/主力资金/PE/换手率/板块涨幅）只有 TDX 能提供，TDX 不通时标注缺失。
"""
import os
import sys
import time
import urllib.request
import numpy as np
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# miniQMT
# ---------------------------------------------------------------------------
def _load_minqmt():
    sys.path.insert(0, HERE)
    import qmt_config as C
    sys.path.append(C.XTPACK)
    from xtquant import xtdata
    return xtdata


def fetch_minqmt(codes: List[str], timeout: float = 1.5) -> Dict[str, dict]:
    """通过 miniQMT xtdata 获取实时 tick 数据。返回 {code: {...}}。"""
    try:
        xtdata = _load_minqmt()
    except Exception as e:
        return {"_error": f"miniQMT 加载失败: {e}"}

    for code in codes:
        try:
            xtdata.subscribe_quote(code, period="tick", count=-1)
        except Exception:
            pass
    time.sleep(timeout)

    ticks = xtdata.get_full_tick(codes)
    results = {}
    for code in codes:
        t = ticks.get(code) or {}
        last = t.get("lastPrice")
        if not last:
            continue
        open_ = t.get("open") or last
        close = t.get("lastClose") or last
        vol = t.get("volume") or 0
        results[code] = {
            "source": "miniqmt",
            "last": float(last),
            "open": float(open_),
            "close": float(close),
            "volume": int(vol),
            "timestamp": t.get("time"),
        }
    return results


# ---------------------------------------------------------------------------
# 腾讯财经免费 API
# ---------------------------------------------------------------------------
def _std_to_tencent(codes: List[str]) -> List[str]:
    """将标准代码 ts_code 转换为腾讯接口格式。"""
    out = []
    for c in codes:
        base = c[:6]
        if c.endswith(".SH"):
            out.append(f"sh{base}")
        elif c.endswith(".SZ"):
            out.append(f"sz{base}")
        else:
            out.append(c)
    return out


def _tencent_to_std(raw_code: str) -> str:
    """腾讯接口返回的纯数字代码转回标准格式。"""
    if raw_code.startswith("6"):
        return f"{raw_code}.SH"
    return f"{raw_code}.SZ"


def fetch_tencent(codes: List[str], timeout: float = 10.0) -> Dict[str, dict]:
    """通过腾讯财经免费 API 获取实时行情。返回 {code: {...}}。"""
    q = ",".join(_std_to_tencent(codes))
    url = f"https://qt.gtimg.cn/q={q}"
    try:
        resp = urllib.request.urlopen(url, timeout=timeout).read().decode("gbk")
    except Exception as e:
        return {"_error": f"腾讯 API 请求失败: {e}"}

    results = {}
    for line in resp.split(";"):
        line = line.strip()
        if not line or "v_" not in line:
            continue
        try:
            parts = line.split('"')[1].split("~")
            std_code = _tencent_to_std(parts[2])
            results[std_code] = {
                "source": "tencent",
                "name": parts[1],
                "last": float(parts[3]),
                "close": float(parts[4]),
                "open": float(parts[5]),
                "volume": int(parts[6]),
                "timestamp": parts[30] if len(parts) > 30 else None,
            }
        except Exception:
            continue
    return results


# ---------------------------------------------------------------------------
# TDX（通过 MCP 调用——由外部 AI 采集后写入文件，本模块只负责读取）
# ---------------------------------------------------------------------------
def fetch_tdx_from_file(codes: List[str], filepath: str) -> Dict[str, dict]:
    """从 AI 已采集的 TDX 复核 JSON 中读取数据。"""
    import json
    if not os.path.exists(filepath):
        return {"_error": f"TDX 复核文件不存在: {filepath}"}
    try:
        data = json.load(open(filepath, encoding="utf-8"))
    except Exception as e:
        return {"_error": f"TDX 复核文件读取失败: {e}"}

    stocks = {s["ts_code"]: s for s in data.get("stocks", [])}
    results = {}
    for code in codes:
        s = stocks.get(code)
        if not s:
            continue
        results[code] = {
            "source": "tdx",
            "last": s.get("last", np.nan),
            "open": s.get("open", np.nan),
            "close": s.get("close", np.nan),
            "volume": s.get("volume", np.nan),
            "quote_pct": s.get("quote_pct", np.nan),
            "main_net_inflow": s.get("main_net_inflow", np.nan),
            "liangbi": s.get("liangbi", np.nan),
            "industry_pct": s.get("industry_pct", np.nan),
            "pe_ttm": s.get("pe_ttm", np.nan),
            "turnover": s.get("turnover", np.nan),
            "catalyst_score": s.get("catalyst_score", 0.0),
            "catalyst_note": s.get("catalyst_note", ""),
        }
    return results


# ---------------------------------------------------------------------------
# 统一入口：按优先级获取
# ---------------------------------------------------------------------------
SOURCE_PRIORITY = ["miniqmt", "tencent", "tdx"]


def fetch_all(codes: List[str],
              tdx_filepath: Optional[str] = None,
              miniqmt_timeout: float = 1.5,
              tencent_timeout: float = 10.0) -> Dict[str, dict]:
    """
    按优先级获取多数据源实时行情。

    返回格式:
      {
        "<ts_code>": {
          "source": "miniqmt|tencent|tdx",
          "last": float,      # 最新价
          "open": float,      # 今开（集合竞价）
          "close": float,     # 昨收
          "volume": int,      # 成交量（手）
          # TDX 扩展字段（仅 tdx 来源有）:
          "quote_pct": float,          # 涨跌幅
          "main_net_inflow": float,    # 主力净流入
          "liangbi": float,            # 量比
          "industry_pct": float,       # 板块涨幅
          "pe_ttm": float,             # 实时 PE
          "turnover": float,           # 换手率
          "catalyst_score": float,     # 新闻催化分
          "catalyst_note": str,
        },
        "_errors": [str, ...]  # 各数据源错误信息
      }
    """
    all_results: Dict[str, dict] = {}
    errors: List[str] = []

    # 1) miniQMT（最优先，基础行情）
    mq = fetch_minqmt(codes, timeout=miniqmt_timeout)
    if "_error" in mq:
        errors.append(mq.pop("_error"))
    else:
        all_results.update(mq)

    # 2) 腾讯 API（补充 miniQMT 未覆盖的）
    missing = [c for c in codes if c not in all_results]
    if missing:
        tq = fetch_tencent(missing, timeout=tencent_timeout)
        if "_error" in tq:
            errors.append(tq.pop("_error"))
        else:
            all_results.update(tq)

    # 3) TDX 文件（扩展指标，覆盖/补充）
    if tdx_filepath:
        tdx = fetch_tdx_from_file(codes, tdx_filepath)
        if "_error" in tdx:
            errors.append(tdx.pop("_error"))
        else:
            for code, s in tdx.items():
                if code not in all_results:
                    all_results[code] = s
                else:
                    # 合并：基础行情保留高优先级来源，扩展指标从 TDX 补充
                    for k in ("quote_pct", "main_net_inflow", "liangbi",
                              "industry_pct", "pe_ttm", "turnover",
                              "catalyst_score", "catalyst_note"):
                        if not np.isnan(s.get(k, np.nan)):
                            all_results[code][k] = s[k]
                    # 标记多来源
                    all_results[code]["source"] = all_results[code].get("source", "") + "+tdx"

    if errors:
        all_results["_errors"] = errors
    return all_results


def write_review_json(codes: List[str], outpath: str, **kwargs) -> None:
    """
    调用 fetch_all 获取数据，并输出为 review_full.py 期望的 tdx_review.json 格式。
    **kwargs 传给 fetch_all（如 tdx_filepath）。
    """
    import json
    data = fetch_all(codes, **kwargs)
    errors = data.pop("_errors", [])
    stocks = []
    for code, s in data.items():
        stocks.append({
            "ts_code": code,
            "name": s.get("name", ""),
            "last": s.get("last"),
            "open": s.get("open"),
            "close": s.get("close"),
            "volume": s.get("volume"),
            "quote_pct": s.get("quote_pct"),
            "main_net_inflow": s.get("main_net_inflow"),
            "liangbi": s.get("liangbi"),
            "industry_pct": s.get("industry_pct"),
            "pe_ttm": s.get("pe_ttm"),
            "turnover": s.get("turnover"),
            "catalyst_score": s.get("catalyst_score", 0.0),
            "catalyst_note": s.get("catalyst_note", ""),
            "source": s.get("source", ""),
        })
    payload = {
        "date": time.strftime("%Y%m%d"),
        "stocks": stocks,
        "errors": errors,
    }
    with open(outpath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    print(f"  [data_sources] 已写入 {outpath} | 共 {len(stocks)} 只 | 来源: {set(s.get('source','') for s in stocks)} | 错误: {errors}")


if __name__ == "__main__":
    # 自测：拉取 4 只策略持仓
    test_codes = ["001378.SZ", "002237.SZ", "300916.SZ", "601865.SH"]
    result = fetch_all(test_codes)
    errors = result.pop("_errors", [])
    for code, s in result.items():
        print(f"{code} ({s.get('source')}): last={s.get('last')} open={s.get('open')} close={s.get('close')} vol={s.get('volume')}")
    if errors:
        print(f"errors: {errors}")
