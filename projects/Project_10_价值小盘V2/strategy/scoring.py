# coding=utf-8
"""V2 评分模块：行业中性BP(z-score) + 历史分位BP(hp), 权重0.8/0.2"""
import pandas as pd
import numpy as np


class V2Scorer:
    """V2 评分器：行业中性BP + 历史分位BP"""

    def __init__(self, ind_map, z_weight=0.8, hp_weight=0.2, hp_window=36, hp_min=12):
        self.ind_map = ind_map
        self.z_weight = z_weight
        self.hp_weight = hp_weight
        self.hp_window = hp_window
        self.hp_min = hp_min
        self._bp_hist_cache = {}
        self._score_cache = {}

    def compute_bp_monthly(self, pb_wide):
        """从宽表PB计算月度BP序列"""
        bp_wide = 1.0 / pb_wide.replace(0, np.nan)
        bp_monthly = bp_wide.resample("ME").last()
        self._month_dates = list(bp_monthly.index)
        self._bp_monthly = bp_monthly
        return bp_monthly

    def bp_hist_pct(self, date):
        """BP 历史分位：当前BP在过去hp_window月中的分位数"""
        d = pd.Timestamp(date)
        if d in self._bp_hist_cache:
            return self._bp_hist_cache[d]
        w = [m for m in self._month_dates if m <= d][-self.hp_window:]
        if len(w) < self.hp_min:
            self._bp_hist_cache[d] = None
            return None
        sub = self._bp_monthly.loc[w]
        r = (sub <= sub.iloc[-1]).mean(axis=0)
        self._bp_hist_cache[d] = r
        return r

    def score(self, date, candidates, pb_series):
        """计算候选股V2评分"""
        cache_key = (date, tuple(sorted(candidates)) if hasattr(candidates, '__len__') else None)
        if cache_key in self._score_cache:
            return self._score_cache[cache_key]

        bp = 1.0 / pb_series.loc[candidates].replace(0, np.nan)
        inds = pd.Series(candidates, index=candidates).map(self.ind_map)
        t = pd.DataFrame({"bp": bp, "ind": inds}).dropna()

        # 行业中性 z-score
        z = t.groupby("ind")["bp"].transform(
            lambda x: (x - x.mean()) / (x.std() + 1e-9)
        )

        # 历史分位
        hp = self.bp_hist_pct(date)

        # 加权合成
        if hp is None:
            r = z
        else:
            r = z * self.z_weight + (hp.reindex(candidates) * self.hp_weight)

        self._score_cache[cache_key] = r
        return r

    def clear_cache(self):
        self._bp_hist_cache.clear()
        self._score_cache.clear()
