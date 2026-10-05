# -*- coding: utf-8 -*-
"""键归一化共享模块（只读工具，无副作用）。

存在的理由：astock 升级审计涉及 5+ 个脚本，每个都要做同样的事情——
  1. 判断某列是不是日期列；
  2. 把混合格式的日期（YYYYMMDD / ISO / 带时分秒）归一到同一个字符串；
  3. 把 NaN 安全地编码进复合键。

这些逻辑一旦各写一份，很容易出现「A 脚本归一化了、B 脚本没归一化」，
导致同一个问题在别处复现、且结论互相矛盾（历史已发生：naive 键让
dividend 的 E 新增从 97 虚增到 942）。所以收敛到一处。

约定：
  - 日期列判定按 '_' 分词，不做子串匹配。'update_flag' 含 'date'（upDATE）
    会被误判成日期列，进而让 norm_date 对 '1'/'0' 做无意义处理；未来若某
    标志位取到 8 位数字（如 '20240427'）还会被错误改写。
  - 非日期列一律按 str().strip() 比较，NaN 统一编码为固定占位符，
    避免 Python 元组比较与 Pandas NaN 匹配语义不一致。
  - SEP 用 \\u0001：不会出现在股票代码/日期/枚举值里。
"""
import re

import numpy as np
import pandas as pd

SEP = '\u0001'
NA = '<NA>'
RE_DIGITS = re.compile(r'\d')
RE_DATE_TOKEN = re.compile(r'(?:^|_)(?:date|month|dt|day)(?:_|$)')

NA_STRINGS = ('nan', 'none', 'nat', 'null', '')


def isna_scalar(v):
    """标量缺失判定；pd.isna 对容器会返回数组，故包 try 降级为 False。"""
    try:
        return bool(pd.isna(v))
    except (ValueError, TypeError):
        return False


def is_date_col(name):
    """按 '_' 分词判断是否日期列。"""
    return bool(RE_DATE_TOKEN.search(str(name).lower()))


def norm_date(v):
    """把日期值归一为 YYYYMMDD 字符串；无法识别时原样返回去空格字符串。"""
    if isna_scalar(v):
        return NA
    if isinstance(v, pd.Timestamp):
        return v.strftime('%Y%m%d')
    s = str(v).strip()
    if s.lower() in NA_STRINGS:
        return NA
    if s.isdigit() and len(s) == 8:
        return s
    digits = RE_DIGITS.findall(s)
    # 只取前 8 位：'2024-04-27 00:00:00' -> '20240427'，避免把时分秒也拼进来
    return ''.join(digits[:8]) if len(digits) >= 8 else s


def norm_scalar(v):
    """非日期列归一化。"""
    return NA if isna_scalar(v) else str(v).strip()


def key_strings(df, keys, date_cols=None):
    """按给定主键列构造 NaN 安全的复合键数组。

    date_cols 为 None 时按列名自动判定日期列。
    """
    if date_cols is None:
        date_cols = set(k for k in keys if is_date_col(k))
    out = None
    for k in keys:
        col = df[k].map(norm_date) if k in date_cols else df[k].map(norm_scalar)
        out = col.to_numpy() if out is None else out + SEP + col.to_numpy()
    return out


def unique_keys(df, keys, date_cols=None):
    """返回该表归一化后的唯一键集合。"""
    return set(pd.unique(key_strings(df, keys, date_cols)).tolist())