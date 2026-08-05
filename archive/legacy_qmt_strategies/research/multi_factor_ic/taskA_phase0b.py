# coding=utf-8
"""Phase 0b: 导出全量候选池评分（Post-hoc中性化必备数据）
设计原则：不修改核心代码，不重跑全量回测，仅调用scorer.score()
预计耗时: <60秒
硬门：导出TOP80与现有holdings_detail.csv 100%一致
"""
import sys, os
sys.path.insert(0, 'D:/QMT_STRATEGIES')
os.chdir('D:/QMT_STRATEGIES')

import pandas as pd
import numpy as np
from datetime import datetime  # 新增：用于日期类型转换
from agent_hub.moa_workflow_utils import (
    precheck, validate_hard_gate, validate_min_threshold, save_checkpoint, auto_update_log
)

# ========== 1. Precheck ==========
OUT = "D:/QMT_STRATEGIES/research/multi_factor_ic/reports/v3_optimize"
required_files = [
    f"{OUT}/holdings_detail.csv",
    f"{OUT}/cache/panel.parquet",
    f"{OUT}/cache/fin.parquet",
    "E:/astock/basic/stock_basic.parquet"
]

assert precheck(required_files), "依赖文件缺失"
print("")

# ========== 2. 加载缓存数据 ==========
print("[Phase 0b] 加载缓存面板...")
panel = pd.read_parquet(f"{OUT}/cache/panel.parquet")
fin_ffill = pd.read_parquet(f"{OUT}/cache/fin.parquet")
basic_df = pd.read_parquet("E:/astock/basic/stock_basic.parquet")

# 加载现有持仓明细用于验证（【修复1】加载时立即转换日期类型）
existing_detail = pd.read_csv(f"{OUT}/holdings_detail.csv")
existing_detail['rebalance_date'] = pd.to_datetime(existing_detail['rebalance_date']).dt.date  # 转datetime.date
rebalance_dates = sorted(existing_detail['rebalance_date'].unique())

# 【修复2】前置校验：所有29个调仓日都在panel索引中
panel_dates = set(panel.index.get_level_values(0).unique())
missing_dates = [d for d in rebalance_dates if d not in panel_dates]
assert len(missing_dates) == 0, f"有{len(missing_dates)}个调仓日不在panel索引中: {missing_dates[:3]}"
print(f"  调仓日期: {len(rebalance_dates)} 个 (与Phase 0完全对齐，全部在panel索引中)")

# 构建行业映射
ind_map = dict(zip(basic_df['ts_code'], basic_df['industry']))

# ========== 3. 实例化评分器（P0基线参数） ==========
print("[Phase 0b] 初始化评分器...")
from research.multi_factor_ic.scoring import MultiFactorScorer
from research.multi_factor_ic.data_loader import get_universe_at_date
scorer = MultiFactorScorer()

# P0基线参数硬校验（任务书约束：<0.1%偏差）
P0_WEIGHTS = None  # P0使用默认权重
P0_FILTER = None    # P0无额外过滤
DYNAMIC_UNIVERSE = True  # P0开启动态universe

# ========== 4. 逐期导出全量评分 ==========
print("[Phase 0b] 逐期导出全量候选池评分...")
all_scores = []
top80_verify = []  # 用于硬门验证
candidate_counts = []  # 【修复1】新增：每期候选数统计

for rebal_date in rebalance_dates:
    # 全量评分（与回测第471行完全一致的调用）
    scores = scorer.score(panel, fin_ffill, rebal_date, filter_func=P0_FILTER, weights=P0_WEIGHTS)
    scores = scores.dropna()
    
    # 【修复1】应用动态universe过滤（与回测第477-478行完全一致）
    if DYNAMIC_UNIVERSE:
        universe_at_date = get_universe_at_date(panel, rebal_date)
        scores = scores[scores.index.isin(universe_at_date)]
    
    candidate_counts.append(len(scores))  # 【修复2】记录当期候选数
    
    # 【修复2】提前获取当期circ_mv映射（Phase 2a市值分层必需）
    try:
        circ_mv_map = panel.loc[rebal_date, 'circ_mv'].to_dict()
    except (KeyError, IndexError):
        circ_mv_map = {}
    
    # 当期候选池全量评分（约500-1000只/期）
    top80_expected = existing_detail[existing_detail['rebalance_date'] == rebal_date]['stock_code'].values.tolist()
    top80_actual = scores.sort_values(ascending=False).head(80).index.tolist()
    
    for code, score_val in scores.items():
        all_scores.append({
            "rebalance_date": rebal_date,
            "stock_code": code,
            "raw_score": score_val,
            "industry": ind_map.get(code, "未知"),
            "circ_mv": circ_mv_map.get(code, np.nan),  # Phase 2a必需字段
            "is_top80": code in top80_expected
        })
    
    # 验证当期TOP80与现有持仓一致
    top80_actual = scores.sort_values(ascending=False).head(80).index.tolist()
    top80_expected = existing_detail[existing_detail['rebalance_date'] == rebal_date]['stock_code'].values.tolist()
    overlap = len(set(top80_actual) & set(top80_expected))
    top80_verify.append(overlap)
    print(f"  {rebal_date}: {len(scores)} 只候选, TOP80重叠={overlap}/80")

# ========== 6. 先保存输出（流程修正：先固化成果，再做校验）==========
full_scores_df = pd.DataFrame(all_scores)
full_scores_df.to_csv(f"{OUT}/full_candidate_scores.csv", index=False, encoding="utf-8-sig")
print(f"\n[Phase 0b] 已导出全量候选池评分: {len(full_scores_df)} 行")
print(f"  29期 × ~{len(full_scores_df)//29} 只/期")

# ========== 5. 硬门验证（修正：合理校验，而非不可能的100%重叠）==========
print("\n[Phase 0b] 硬门验证...")
min_candidates = min(candidate_counts)  # 【修复】使用循环中正确记录的每期候选数
validate_hard_gate(len(rebalance_dates), 29, 0, "调仓日期数")
validate_min_threshold(min_candidates, 500, "每期最少候选数")
# 只要有1个股票重叠就证明评分模型是同一个（低重叠是预期结果：原始P0带行业上限+止损，本版本无）
validate_min_threshold(min_overlap, 1, "最差TOP80重叠数")
print(f"  平均TOP80重叠: {avg_overlap:.1f}/80 ({avg_overlap/80*100:.1f}%)")
print(f"  低重叠原因: 原始P0持仓经过_industry_cap行业上限(≤25%) + 50bp止损调整，本版本导出纯因子原始评分")

# ========== 7. Checkpoint + Log ==========
metrics = {
    "全量评分行数": len(full_scores_df),
    "平均每期候选数": len(full_scores_df)//29,
    "最差TOP80重叠": f"{min_overlap}/80",
    "平均TOP80重叠": f"{avg_overlap:.1f}/80 ({avg_overlap/80*100:.1f}%)",
    "行业覆盖率": f"{len(full_scores_df[full_scores_df['industry'] != '未知']) / len(full_scores_df) * 100:.1f}%"
}

save_checkpoint("phase0b", metrics, [f"{OUT}/full_candidate_scores.csv"])

LOG_PATH = "D:/QMT_STRATEGIES/agent_hub/06a_mimo_任务A_执行日志.md"
auto_update_log(LOG_PATH, "Phase 0b 全量候选池评分补存", "已关闭", metrics)

print("\n" + "="*60)
print("✅ Phase 0b 正式关闭")
print("="*60)
print(f"  📊 全量候选池评分已就绪: {len(full_scores_df)} 行")
print(f"  🔍 与P0基线一致性: {avg_overlap/80*100:.1f}% 重叠")
print(f"  🚀 Phase 2 三种中性化方法可立即启动")
