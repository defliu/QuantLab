# coding=utf-8
"""因子引擎 — 注册、计算、IC测试"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional


class FactorEngine:
    """因子引擎"""

    def __init__(self):
        self.factors: Dict[str, 'FactorBase'] = {}

    def register(self, factor):
        """注册因子"""
        self.factors[factor.name] = factor
        return self

    def compute_all(self, panel, fin_ffill, **kwargs):
        """计算所有已注册因子

        Returns:
            pd.DataFrame: 因子面板，index=(date, code), columns=factor_names
        """
        results = {}
        for name, factor in self.factors.items():
            try:
                results[name] = factor.compute(panel, fin_ffill, **kwargs)
            except Exception as e:
                print("[FactorEngine] %s 计算失败: %s" % (name, e))
        return pd.DataFrame(results)

    def compute_ic(self, factor_panel, price_data, forward_days=20):
        """计算因子 IC/ICIR

        Args:
            factor_panel: 因子面板 index=(date, code)
            price_data: 价格数据 index=(date, code), columns=[close]
            forward_days: 前向收益天数

        Returns:
            pd.DataFrame: IC统计结果
        """
        # 计算前向收益
        close = price_data['close'].unstack('code')
        fwd_ret = close.pct_change(forward_days).shift(-forward_days)
        fwd_ret = fwd_ret.stack()
        fwd_ret.name = 'forward_return'

        # 合并
        merged = factor_panel.join(fwd_ret, how='inner')

        # 逐日期计算截面IC
        results = []
        for factor_name in factor_panel.columns:
            daily_ic = []
            for date, group in merged.groupby(level='date'):
                x = group[factor_name].dropna()
                y = group['forward_return'].dropna()
                common = x.index.intersection(y.index)
                if len(common) < 30:
                    continue
                ic = x.loc[common].corr(y.loc[common])
                if not np.isnan(ic):
                    daily_ic.append(ic)

            if len(daily_ic) < 10:
                continue

            ic_series = pd.Series(daily_ic)
            results.append({
                'factor': factor_name,
                'ic_mean': ic_series.mean(),
                'ic_std': ic_series.std(),
                'icir': ic_series.mean() / ic_series.std() if ic_series.std() > 0 else 0,
                'ic_positive_pct': (ic_series > 0).mean(),
                'n_dates': len(daily_ic),
            })

        return pd.DataFrame(results).sort_values('icir', ascending=False)

    def list_factors(self):
        """列出所有已注册因子"""
        return list(self.factors.keys())
