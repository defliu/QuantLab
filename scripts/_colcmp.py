# -*- coding: utf-8 -*-
"""逐列向量化「同组行是否相等」判定（去重安全审计的判定内核）。

为什么需要这个模块：
  朴素写法 `s.astype(str)` 会把 Python None -> 'None'、NaN -> 'nan'，
  于是「一列全是 None」的重复组会被误判成载荷差异（express 实测假阳性）。
  而 `pd.factorize` 虽然能把 None/NaN/pd.NA 统一成一个编码，
  但它把 '1'(str) 和 1(int) 判成不同值，又和旧的 str() 语义不一致。
  故这里采用「NA 统一 + 归一化字符串编码 + 数值兜底」三段式：
    1) 缺失统一：None / NaN / pd.NA / 'nan' / '' -> NA 占位符
    2) 精确比较：对归一化后的值做 factorize 编码比编码（快，且无歧义）
    3) 数值兜底：若该列所有非缺失值都能解析成数字，改用 isclose 比较，
                  消除 '0.22275' vs 0.22275 这类表示差异
  三段取「或」，与旧 payload_equal 的语义对齐，同时保持整块向量化。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(
    0,
    os.path.dirname(os.path.abspath(__file__))
)
# 归一化语义（NA 占位、日期 token 判定）统一由 _keynorm 提供，
# 避免两处各写一份、日后改一处忘另一处。
from _keynorm import (  # noqa: E402
    NA,
    SEP,
    is_date_col,
    norm_date,
    norm_scalar,
)


def _norm_array(s, date_cols, name):
    """返回归一化后的 object 数组（缺失 -> NA）。"""
    if name in date_cols:
        return s.map(norm_date).to_numpy(dtype=object)
    return s.map(norm_scalar).to_numpy(dtype=object)


def _numeric_fallback(norm, na_mask):
    """若所有非缺失值都能解析成数字，返回 float 数组；否则返回 None。

    返回 None 表示该列不适合走数值兜底（调用方退回归一化字符串精确比较）。
    只对非缺失子集做 to_numeric，避免 NA 占位符参与解析。
    """
    out = np.full(len(norm), np.nan, dtype='float64')
    vals = norm[~na_mask]
    if len(vals) == 0:
        return None
    num = pd.to_numeric(pd.Series(vals, dtype=object), errors='coerce')
    if bool(num.isna().any()):
        return None
    out[~na_mask] = num.to_numpy(dtype='float64', copy=False)
    return out


def col_diff_flags(s, base_of_row, date_cols, name):
    """核心：返回该列「每行是否与所在组首行不同」的 bool 数组。

    长度与 s 相同；组内首行恒为 False（不与自己比）。
    """
    n = len(s)
    is_first = base_of_row == np.arange(n)

    if pd.api.types.is_numeric_dtype(s) and name not in date_cols:
        v = s.to_numpy(dtype='float64', copy=False)
        bv = v[base_of_row]
        with np.errstate(invalid='ignore'):
            eq = np.isclose(v, bv, rtol=1e-9, atol=1e-9, equal_nan=True)
        return (~eq) & (~is_first)

    norm = _norm_array(s, date_cols, name)
    na_mask = np.array([x == NA for x in norm], dtype=bool)

    codes, _ = pd.factorize(norm, use_na_sentinel=False)
    eq = codes == codes[base_of_row]

    num = _numeric_fallback(norm, na_mask)
    if num is not None:
        with np.errstate(invalid='ignore'):
            eq = eq | np.isclose(
                num,
                num[base_of_row],
                rtol=1e-9,
                atol=1e-9,
                equal_nan=True,
            )

    # 缺失与非缺失永远不等（数值兜底已用 equal_nan 处理，这里只兜字符串路径）
    return (~eq) & (~is_first)