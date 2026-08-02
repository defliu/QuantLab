# coding=utf-8
"""多因子选股评分模型：BP + 反转 + 低波 + ROE。

⚠️ 【已知Bug，2026-07-19】：filter_func参数当前不工作
   原因：compute_all_factors()返回的Series索引与final_mask不对齐
   临时解决方案：先在外层过滤panel，再调用scorer.score()

权重配置基于 IC 测试结果：
  BP(30%) + 反转(25%) + 低波(25%) + ROE(20%)

评分流程：
  1. 计算各因子截面 z-score（已 winsorize）
  2. 方向调整：反转/低波取负值（因子值越小得分越高）
  3. 加权求和 → 综合评分
"""

import numpy as np
import pandas as pd
from research.multi_factor_ic.data_loader import (
    load_universe, build_panel, get_monthly_rebalance_dates,
)
from research.multi_factor_ic.factors import (
    winsorize, standardize, compute_all_factors,
)
from research.multi_factor_ic.ic_test import calc_forward_return, compute_rank_ic
from research.multi_factor_ic.config import OUTPUT_DIR


# 权重配置
# vwap_volume_corr = 10%, 其他等比例缩减
FACTOR_WEIGHTS = {
    "BP": 0.27,
    "reversal_1m": 0.225,
    "volatility_60d": 0.225,
    "ROE": 0.18,
    "vwap_volume_corr": 0.10,
}


class MultiFactorScorer:
    """多因子评分器。

    用法:
        scorer = MultiFactorScorer()
        scores = scorer.score(panel, fin_ffill, date)
        # scores: Series indexed by ts_code, 0~100 scale
    """

    def score(self, panel, fin_ffill, date, filter_func=None, weights=None):
        """计算 date 日全市场综合评分。返回 Series (0~100)。

        Args:
            filter_func: callable(panel, fin_ffill, date) -> mask
                         返回值会与基础安全过滤叠加，不是替换
                         基础安全过滤：PE>0, PB>0, ROE>-20
            weights: dict, 自定义因子权重。None=使用 FACTOR_WEIGHTS 默认权重
        """
        raw = compute_all_factors(panel, fin_ffill, date)

        # ==============================================
        # 🔴 生死级修复：3个致命Bug已全部修复
        # ==============================================
        # Bug 1：基础安全过滤（PE/PB/ROE）始终生效（不是被filter_func替换）
        # Bug 2：自定义filter_func 与基础过滤叠加（不是互斥）
        # Bug 3：默认市值过滤（circ_mv > 5e4）已移除（整个universe都>14亿）
        # ==============================================

        # Step 1: 始终先应用基础安全过滤（任何情况都生效，防止垃圾数据）
        date_data = panel.loc[date]
        idx = date_data.index

        base_mask = (date_data["pe_ttm"] > 0) & (date_data["pb"] > 0)

        # ROE 过滤（对齐财报披露滞后45天）
        fin_dates = fin_ffill.index
        lookup_date = pd.Timestamp(date) - pd.Timedelta(days=45)
        valid = fin_dates[fin_dates <= lookup_date]
        if len(valid) > 0:
            roe = fin_ffill.loc[valid[-1], "roe"].reindex(idx, fill_value=-np.inf)
            base_mask = base_mask & (roe >= -20)

        # Step 2: 叠加自定义市值过滤（如果传入）
        if filter_func is not None:
            cap_mask = filter_func(panel, fin_ffill, date)
            # 确保索引对齐
            if isinstance(cap_mask, pd.Series):
                cap_mask = cap_mask.reindex(idx, fill_value=False)
            else:
                cap_mask = pd.Series(cap_mask, index=idx).fillna(False)
        else:
            # 默认：无额外市值过滤（universe 本身已做了市值排名）
            cap_mask = pd.Series(True, index=idx)

        # Step 3: 最终过滤 = 基础安全 + 自定义
        final_mask = base_mask & cap_mask

        # Step 4: 真正应用到所有因子（被过滤的股票所有因子都是NaN，最终评分也是NaN）
        for name in raw:
            raw[name] = raw[name].where(final_mask, other=np.nan)

        # 2. 计算各子得分
        sub_scores = {}

        # BP: 值越大得分越高
        bp = raw["BP"]
        sub_scores["BP"] = self._normalize(bp, reverse=False)

        # 反转: 过去1月涨幅越低(跌得多)得分越高 → 取负值
        rev = raw["momentum_1m"]
        sub_scores["reversal_1m"] = self._normalize(rev, reverse=True)

        # 低波: 波动率越低得分越高 → 取负值
        vol = raw["volatility_60d"]
        sub_scores["volatility_60d"] = self._normalize(vol, reverse=True)

        # ROE: 值越大得分越高
        roe = raw["ROE"]
        sub_scores["ROE"] = self._normalize(roe, reverse=False)

        # VWAP量价相关: 值越大（负相关越强）→ 得分越高（IC为正）
        vwap = raw["vwap_volume_corr"]
        sub_scores["vwap_volume_corr"] = self._normalize(vwap, reverse=False)

        # 3. 加权合成
        # 修复：total 必须初始化为 NaN（不是 0.0），否则被过滤的股票会得到 0 分
        # 而非 NaN，导致它们仍参与 TOP N 排序，市值过滤完全失效
        active_weights = weights if weights is not None else FACTOR_WEIGHTS
        total = pd.Series(np.nan, index=bp.index)
        weight_sum = 0.0
        for name, w in active_weights.items():
            s = sub_scores.get(name)
            if s is not None and len(s.dropna()) > 0:
                total = total.add(s * w, fill_value=0)
                weight_sum += w

        if weight_sum > 0:
            total = total / weight_sum * 100.0

        return total

    def score_with_details(self, panel, fin_ffill, date):
        """返回综合评分 + 各子维度明细。"""
        raw = compute_all_factors(panel, fin_ffill, date)

        sub_scores = {}
        # BP
        bp = raw["BP"]
        sub_scores["score_BP"] = self._normalize(bp, reverse=False)
        sub_scores["raw_BP"] = bp
        # 反转
        rev = raw["momentum_1m"]
        sub_scores["score_reversal"] = self._normalize(rev, reverse=True)
        sub_scores["raw_reversal"] = rev
        # 低波
        vol = raw["volatility_60d"]
        sub_scores["score_lowvol"] = self._normalize(vol, reverse=True)
        sub_scores["raw_volatility"] = vol
        # ROE
        roe = raw["ROE"]
        sub_scores["score_ROE"] = self._normalize(roe, reverse=False)
        sub_scores["raw_ROE"] = roe

        # VWAP量价相关
        vwap = raw["vwap_volume_corr"]
        sub_scores["score_vwap_volume_corr"] = self._normalize(vwap, reverse=False)
        sub_scores["raw_vwap_volume_corr"] = vwap

        total = pd.Series(np.nan, index=bp.index)
        weight_sum = 0.0
        for name, w in FACTOR_WEIGHTS.items():
            s = sub_scores.get(f"score_{name.split('_')[0] if '_' in name else name}")
            if s is None:
                s = sub_scores.get(f"score_{name}")
            if s is not None and len(s.dropna()) > 0:
                total = total.add(s * w, fill_value=0)
                weight_sum += w

        if weight_sum > 0:
            total = total / weight_sum * 100.0

        result = pd.DataFrame({
            "score_total": total,
            **{k: v for k, v in sub_scores.items() if k.startswith("score_")},
        })
        return result

    @staticmethod
    def _normalize(series, reverse=False):
        """单因子归一化：winsorize → z-score，方向控制。"""
        s = winsorize(series)
        s = standardize(s)
        if reverse:
            s = -s
        return s


def verify_scorer_ic(panel, fin_ffill):
    """验证评分器整体的 IC 表现。"""
    rebalance_dates = get_monthly_rebalance_dates(panel)
    scorer = MultiFactorScorer()

    ic_list = []
    total_dates = len(rebalance_dates)
    for i, date in enumerate(rebalance_dates):
        if (i + 1) % 20 == 0:
            print(f"[verify] {i+1}/{total_dates}")

        try:
            scores = scorer.score(panel, fin_ffill, date)
        except Exception as e:
            print(f"  [skip] {date}: {e}")
            continue
        if len(scores.dropna()) < 10:
            continue

        forward_ret = calc_forward_return(panel, date, hold_days=20)
        if len(forward_ret.dropna()) < 10:
            continue

        ic = compute_rank_ic(scores, forward_ret)
        ic_list.append({"date": date, "IC_score": ic})

    ic_df = pd.DataFrame(ic_list)
    ic_mean = ic_df["IC_score"].mean()
    ic_std = ic_df["IC_score"].std()
    icir = ic_mean / ic_std if ic_std > 0 else 0
    ic_pos = (ic_df["IC_score"] > 0).mean()

    print("\n" + "=" * 50)
    print("综合评分 IC 验证")
    print("=" * 50)
    print(f"IC均值:   {ic_mean:.4f}")
    print(f"IC标准差: {ic_std:.4f}")
    print(f"ICIR:     {icir:.4f}")
    print(f"IC>0占比:  {ic_pos:.0%}")
    print(f"样本数:   {len(ic_df)}")
    print("=" * 50)

    # 保存
    ic_df.to_csv(f"{OUTPUT_DIR}/scorer_ic_series.csv",
                 index=False, encoding="utf-8-sig")
    return ic_df, ic_mean, icir


def top_picks(panel, fin_ffill, date, n=20):
    """输出指定日期评分最高的 N 只股票。"""
    scorer = MultiFactorScorer()
    details = scorer.score_with_details(panel, fin_ffill, date)
    details = details.sort_values("score_total", ascending=False)
    top = details.head(n)

    print(f"\n{date} 评分 TOP {n}")
    print("-" * 90)
    print(f"{'排名':<4} {'代码':<12} {'总分':>6} {'BP':>6} {'反转':>6} {'低波':>6} {'ROE':>6} {'VWAP量价':>8}")
    print("-" * 90)
    for rank, (code, row) in enumerate(top.iterrows(), 1):
        print(f"{rank:<4} {code:<12} {row['score_total']:>6.1f} "
              f"{row['score_BP']:>6.2f} {row['score_reversal']:>6.2f} "
              f"{row['score_lowvol']:>6.2f} {row['score_ROE']:>6.2f} "
              f"{row['score_vwap_volume_corr']:>8.2f}")
    return top
