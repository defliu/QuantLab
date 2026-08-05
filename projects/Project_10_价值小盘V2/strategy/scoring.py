# coding=utf-8
"""V2 评分模块：行业中性BP(z-score) + 历史分位BP(hp)

P0-1 (2026-08-04): 新增 method 参数化:
  - "bp"    : 行业中性BP z + hp 历史分位 (原始 V2 行为, 默认)
  - "ep"    : 纯 EP(=1/PE) 行业中性 z-score (Wharton: EP 优于 BP)
  - "bp_ep" : BP z + EP z 复合
  - "multi" : BP z + EP z + 附加因子帧 (P0-2: ATR%/换手率 rank)
P0-2 (2026-08-04): 新增 attach_factor() 支持多因子加权。
所有模式保持质量排雷在候选层处理, 评分层不变。
"""
import pandas as pd
import numpy as np

_METHOD_ALIASES = {
    "bp_industry_neutral_hp": "bp",  # 旧配置名兼容
    "bp": "bp",
    "ep": "ep",
    "bp_ep": "bp_ep",
    "multi": "multi",
}


class V2Scorer:
    """V2 评分器：行业中性BP + 历史分位BP, 支持 EP / 多因子扩展"""

    def __init__(self, ind_map, z_weight=0.8, hp_weight=0.2, hp_window=36, hp_min=12,
                 method="bp", ep_weight=0.0, factor_weights=None):
        self.ind_map = ind_map
        self.z_weight = z_weight
        self.hp_weight = hp_weight
        self.hp_window = hp_window
        self.hp_min = hp_min
        self.method = _METHOD_ALIASES.get(method, "bp")
        self.ep_weight = ep_weight
        self.factor_weights = dict(factor_weights or {})
        self._factor_frames = {}
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
        if not hasattr(self, "_month_dates"):
            return None  # 未调 compute_bp_monthly 时安全回退
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

    def attach_factor(self, name, wide_frame):
        """注册附加因子评分帧 (index=date, columns=code, 值已预处理为截面得分)"""
        self._factor_frames[name] = wide_frame

    def _industry_z(self, values, candidates):
        """截面行业中性 z-score, values 为 candidates 对齐的 Series"""
        inds = pd.Series(candidates, index=candidates).map(self.ind_map)
        t = pd.DataFrame({"v": values, "ind": inds}).dropna()
        return t.groupby("ind")["v"].transform(
            lambda x: (x - x.mean()) / (x.std() + 1e-9)
        )

    def score(self, date, candidates, pb_series, pe_series=None):
        """计算候选股V2评分

        Args:
            date: 调仓日
            candidates: 候选代码
            pb_series: 当日 PB 截面
            pe_series: 当日 PE_TTM 截面 (EP/bp_ep/multi 模式必需, 其他模式忽略)
        """
        cache_key = (date, tuple(sorted(candidates)) if hasattr(candidates, '__len__') else None)
        if cache_key in self._score_cache:
            return self._score_cache[cache_key]

        method = self.method
        r = None

        # ---- BP 行业中性 z-score ----
        if method in ("bp", "bp_ep", "multi"):
            bp = 1.0 / pb_series.loc[candidates].replace(0, np.nan)
            z = self._industry_z(bp, candidates)
            if method == "bp":
                # 原始 V2: hp 缺失时直接用未加权 z (与原实现一致)
                hp = self.bp_hist_pct(date)
                if hp is None:
                    r = z
                else:
                    r = z * self.z_weight + (hp.reindex(candidates) * self.hp_weight)
            else:
                r = z * self.z_weight

        # ---- EP 行业中性 z-score ----
        if method in ("ep", "bp_ep", "multi"):
            if pe_series is None:
                raise ValueError("method=%s 需要 pe_series" % method)
            pe = pe_series.reindex(candidates)
            ep = (1.0 / pe).where(pe > 0)
            z_ep = self._industry_z(ep, candidates)
            if method == "ep":
                r = z_ep
            else:
                r = r.add(z_ep * self.ep_weight) if r is not None else z_ep * self.ep_weight

        # ---- 附加因子帧 (P0-2: ATR%/换手率 rank) ----
        if method == "multi" and self.factor_weights:
            d = pd.Timestamp(date)
            for name, w in self.factor_weights.items():
                frame = self._factor_frames.get(name)
                if frame is None or w == 0:
                    continue
                if d not in frame.index:
                    continue
                row = frame.loc[d].reindex(candidates)
                r = r.add(row * w) if r is not None else row * w

        # ---- multi 模式可选叠加 hp ----
        if method == "multi" and self.hp_weight > 0:
            hp = self.bp_hist_pct(date)
            if hp is not None:
                r = r.add(hp.reindex(candidates) * self.hp_weight)

        self._score_cache[cache_key] = r
        return r

    def clear_cache(self):
        self._bp_hist_cache.clear()
        self._score_cache.clear()
