# -*- coding: utf-8 -*-
"""XVAL T-20260928 独立交叉验证 —— 共享评分卡（生产语义的向量化精确复刻）。

逐条复刻生产源码逻辑（源文件:行号）：
  - deploy_predict.py:_score_f1/_score_f3/_score_f4（F1/F3/F4 代理分）
  - review_full.py:score_f2/score_f5/score_f6（F2/F5/F6 真实分）
  - scorecard_real.py:compute_real_scorecard（总分拼分与权重）
只读复刻，未修改任何生产文件。NaN 语义与生产一致（比较含 NaN 一律 False）。
"""
import numpy as np

SC_WEIGHTS = {"F1": 0.25, "F2": 0.20, "F3": 0.20, "F4": 0.15, "F5": 0.10, "F6": 0.10}


def score_f1_vec(pos_250):
    """deploy_predict.py:40-53。"""
    p = np.asarray(pos_250, dtype=float)
    out = np.full(len(p), 8.0)
    out[p >= 0.72] = 9.0
    out[p >= 0.88] = 7.0
    out[p >= 0.95] = 5.0
    out[p >= 0.99] = 3.0
    out[np.isnan(p)] = 5.0
    return out


def score_f2_vec(main_net, volume_ratio):
    """review_full.py:63-77（真实 F2：主力净额元 + 量比）。"""
    mn = np.asarray(main_net, dtype=float)
    vr = np.asarray(volume_ratio, dtype=float)
    nan_mn = np.isnan(mn)
    nan_vr = np.isnan(vr)
    vr25 = (~nan_vr) & (vr > 2.5)
    vr15 = (~nan_vr) & (vr > 1.5)
    vr2 = (~nan_vr) & (vr >= 2.0)
    out = np.full(len(mn), 2.0)
    out[mn <= -1e8] = 1.0
    out[vr2] = 5.0
    out[mn > 0] = 6.0
    out[(mn > 1e7) & vr15] = 8.0
    out[(mn > 5e7) & vr25] = 10.0
    out[nan_mn] = 5.0
    return out


def score_f3_vec(fc_days, ex_days, fc_pchg, dv_sum):
    """deploy_predict.py:70-83（事件催化代理分，NaN 比较一律 False）。"""
    fc = np.asarray(fc_days, dtype=float)
    ex = np.asarray(ex_days, dtype=float)
    pc = np.asarray(fc_pchg, dtype=float)
    dv = np.asarray(dv_sum, dtype=float)
    s = np.full(len(fc), 4.0)
    with np.errstate(invalid="ignore"):
        s[(fc <= 90) | (ex <= 90)] += 3.0
        s[pc > 20] += 2.0
        s[dv > 1.0] += 1.0
    return np.minimum(s, 10.0)


def score_f4_vec(rsi6, macd_hist):
    """deploy_predict.py:86-99。"""
    rsi = np.asarray(rsi6, dtype=float)
    macd = np.asarray(macd_hist, dtype=float)
    nan_any = np.isnan(rsi) | np.isnan(macd)
    out = np.full(len(rsi), 1.0)
    out[(rsi < 40) | (macd < 0)] = 3.0
    out[(rsi >= 40) & (rsi <= 60)] = 5.0
    out[(rsi > 50) & (macd > 0)] = 7.0
    out[(rsi > 65) & (macd > 0)] = 10.0
    out[nan_any] = 5.0
    return out


def score_f5_vec(industry_pct):
    """review_full.py:133-145（真实 F5：行业当日涨幅 %）。"""
    ind = np.asarray(industry_pct, dtype=float)
    out = np.full(len(ind), 2.0)
    out[ind > -1] = 4.0
    out[ind > 0] = 5.0
    out[ind > 1.5] = 8.0
    out[ind > 3] = 10.0
    out[np.isnan(ind)] = 5.0
    return out


def score_f6_vec(pe_ttm, turnover):
    """review_full.py:148-162（真实 F6：PE-TTM + 换手）。"""
    pe = np.asarray(pe_ttm, dtype=float)
    tu = np.asarray(turnover, dtype=float)
    nan_pe = np.isnan(pe)
    nan_tu = np.isnan(tu)
    tu_ok = nan_tu | ((tu >= 1) & (tu <= 8))
    out = np.full(len(pe), 4.0)
    out[(pe >= 5) & (pe <= 50)] = 7.0
    out[(pe >= 10) & (pe <= 30) & tu_ok] = 10.0
    out[(pe < 0) | (pe > 100)] = 1.0
    out[nan_pe] = 5.0
    return out


def total_new_vec(f1, f2, f3, f4, f5, f6):
    """scorecard_real.py:68-69（求和顺序与 sum(...) 生成器一致：F1→F5 顺序累加）。"""
    t = SC_WEIGHTS["F1"] * f1 + SC_WEIGHTS["F2"] * f2
    t = t + SC_WEIGHTS["F3"] * f3
    t = t + SC_WEIGHTS["F4"] * f4
    t = t + SC_WEIGHTS["F5"] * f5
    return t * 10.0 + SC_WEIGHTS["F6"] * f6 * 10.0
