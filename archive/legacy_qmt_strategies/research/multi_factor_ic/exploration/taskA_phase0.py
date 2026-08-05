# coding=utf-8
"""
任务A Phase 0: 补存逐股持仓明细
严格对齐P0基线参数，偏差<0.1%即为通过
"""
import sys, os
sys.path.insert(0, 'D:/QMT_STRATEGIES')
os.chdir('D:/QMT_STRATEGIES')
import warnings; warnings.filterwarnings('ignore')
import pandas as pd

from research.multi_factor_ic.data_loader import load_universe, build_panel
from research.multi_factor_ic.backtest import backtest_stop_loss
from research.multi_factor_ic.config import BASIC_PATH

print("=" * 70)
print("任务A Phase 0: 补存逐股持仓明细")
print("=" * 70)

# ============ 100%对齐P0基线参数 ============
P0_PARAMS = {
    'top_n': 80,
    'freq': '2M',
    'tx_cost': 0.002,
    'stop_loss': -0.12,
    'dynamic_universe': True,
    'mv_min': 0,
    'mv_max': 300000,
}

# 预期基线
EXPECTED = {
    '年化收益': 15.5,
    '夏普比率': 0.50,
    '最大回撤': -20.5,
    '调仓次数': 29,
}

print(f"P0基线参数: {P0_PARAMS}")
print(f"预期基线: 年化{EXPECTED['年化收益']}% 夏普{EXPECTED['夏普比率']} 回撤{EXPECTED['最大回撤']}% 调仓{EXPECTED['调仓次数']}次")
print("")

# ============ 加载数据 ============
print("[数据] 加载universe...")
codes = load_universe()
print(f"[数据] 构建面板 (约需20秒)...")
panel, fin = build_panel(codes)
print(f"[数据] 面板维度: {panel.shape}, 交易日={panel.index.get_level_values('trade_date').nunique()}")

basic_df = pd.read_parquet(BASIC_PATH)
print(f"[数据] 基础信息表: {len(basic_df)} 只股票, 行业数={basic_df['industry'].nunique()}")

# ============ 市值过滤函数（与P0完全一致） ============
MV = lambda p, f, d: (p.loc[d]['circ_mv'] > P0_PARAMS['mv_min']) & (p.loc[d]['circ_mv'] < P0_PARAMS['mv_max'])

print("")
print("[回测] 启动补存...")
# ============ 执行回测（补存明细） ============
eq, td, sl, met = backtest_stop_loss(
    panel, fin,
    top_n=P0_PARAMS['top_n'],
    freq=P0_PARAMS['freq'],
    tx_cost=P0_PARAMS['tx_cost'],
    dynamic_universe=P0_PARAMS['dynamic_universe'],
    stop_loss=P0_PARAMS['stop_loss'],
    filter_func=MV,
    save_details=True,
    basic_df=basic_df,
)

print("")
print("=" * 70)
print("Phase 0 基线完整性校验（硬门：偏差<0.1%）")
print("=" * 70)

# 解析实际值
actual = {}
for k in ['年化收益', '最大回撤']:
    v = met.get(k, '')
    if isinstance(v, str) and '%' in v:
        actual[k] = float(v.rstrip('%'))
    else:
        actual[k] = float(v)
for k in ['夏普比率']:
    actual[k] = float(met.get(k, 0))
actual['调仓次数'] = int(met.get('调仓次数', len(eq)))

# 逐项校验
checks = []
for k in EXPECTED:
    diff = abs(actual[k] - EXPECTED[k])
    passed = diff < 0.1
    status = "✅" if passed else "❌"
    checks.append(passed)
    print(f"{status} {k}: 预期={EXPECTED[k]}, 实际={actual[k]}, 偏差={diff:.2f}")

print("")
all_passed = all(checks)
print(f"{'✅ Phase 0 硬门校验通过' if all_passed else '❌ Phase 0 硬门校验不通过'}")
print("")

# 验证明细文件
detail_path = "D:/QMT_STRATEGIES/research/multi_factor_ic/reports/v3_optimize/holdings_detail.csv"
if os.path.exists(detail_path):
    df = pd.read_csv(detail_path)
    print(f"持仓明细文件: {len(df)} 行, {df['rebalance_date'].nunique()} 个调仓日")
    print(f"行业覆盖: {df['industry'].nunique()} 个行业")
    print(f"市值分布: min={df['circ_mv'].min():.0f}万, max={df['circ_mv'].max():.0f}万")
else:
    print("❌ 持仓明细文件不存在！")

print("")
print("Phase 0 完成。下一步: 执行 Phase 1/2/3 行业中性化与显著性分析。")
