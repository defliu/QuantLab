import pandas as pd
import numpy as np
import os
import gc
from pathlib import Path
from tqdm import tqdm

DATA_DIR = Path("d:/astock/min_1min")
BASIC_FILE = Path("d:/astock/basic/stock_basic.parquet")
OUTPUT_DIR = Path("d:/QuantLab/projects/Project_21_日内做T/results")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def load_stock_basic():
    """加载股票基础信息"""
    df = pd.read_parquet(BASIC_FILE)
    return df.set_index('ts_code')

def get_parquet_files():
    """获取所有 parquet 文件"""
    files = list(DATA_DIR.glob("*.parquet"))
    print(f"发现 {len(files)} 个股票文件")
    return files

def compute_daily_stats(df_1min):
    """从 1min 数据计算日级统计量"""
    df = df_1min.reset_index()
    df['trade_date'] = pd.to_datetime(df['trade_date'], format='ISO8601')
    
    daily = df.groupby('trade_date').agg(
        open=('open', 'first'),
        high=('high', 'max'),
        low=('low', 'min'),
        close=('close', 'last'),
        vol=('vol', 'sum'),
        amount=('amount', 'sum'),
        n_bars=('close', 'count')
    ).reset_index()
    
    daily['amplitude_pct'] = (daily['high'] - daily['low']) / daily['open'] * 100
    daily['change_pct'] = (daily['close'] - daily['open']) / daily['open'] * 100
    daily['gap_pct'] = (daily['open'] - daily.groupby('trade_date')['close'].shift(1)) / daily.groupby('trade_date')['close'].shift(1) * 100
    daily['limit_up'] = daily['change_pct'] >= 9.5
    daily['limit_down'] = daily['change_pct'] <= -9.5
    daily['tradable_at_limit'] = daily.apply(
        lambda r: (r['vol'] > 0) if (r['limit_up'] or r['limit_down']) else True, axis=1
    )
    return daily

def process_one_stock(filepath, stock_basic):
    """处理单只股票，返回汇总统计"""
    try:
        df = pd.read_parquet(filepath)
        ts_code = filepath.stem
        
        if ts_code not in stock_basic.index:
            return None
            
        list_date = stock_basic.loc[ts_code, 'list_date']
        if pd.isna(list_date):
            return None
        list_date = pd.to_datetime(str(list_date), format='ISO8601')
        
        daily = compute_daily_stats(df)
        daily = daily[daily['trade_date'] >= list_date]
        
        if len(daily) < 20:
            return None
            
        stats = {
            'ts_code': ts_code,
            'name': stock_basic.loc[ts_code, 'name'] if 'name' in stock_basic.columns else '',
            'industry': stock_basic.loc[ts_code, 'industry'] if 'industry' in stock_basic.columns else '',
            'market': stock_basic.loc[ts_code, 'market'] if 'market' in stock_basic.columns else '',
            'list_date': list_date,
            'n_days': len(daily),
            'amplitude_median': daily['amplitude_pct'].median(),
            'amplitude_mean': daily['amplitude_pct'].mean(),
            'amplitude_p90': daily['amplitude_pct'].quantile(0.9),
            'amplitude_p95': daily['amplitude_pct'].quantile(0.95),
            'amplitude_p99': daily['amplitude_pct'].quantile(0.99),
            'amplitude_max': daily['amplitude_pct'].max(),
            'turnover_median': (daily['amount'] / 1e8).median(),  # 亿元
            'turnover_mean': (daily['amount'] / 1e8).mean(),
            'vol_median': (daily['vol'] / 1e4).median(),  # 万手
            'vol_mean': (daily['vol'] / 1e4).mean(),
            'limit_up_days': daily['limit_up'].sum(),
            'limit_down_days': daily['limit_down'].sum(),
            'limit_up_tradable': daily[daily['limit_up']]['tradable_at_limit'].sum() if daily['limit_up'].any() else 0,
            'limit_down_tradable': daily[daily['limit_down']]['tradable_at_limit'].sum() if daily['limit_down'].any() else 0,
            'gap_abs_mean': daily['gap_pct'].abs().mean(),
            'gap_abs_median': daily['gap_pct'].abs().median(),
        }
        return stats
    except Exception as e:
        print(f"Error processing {filepath.name}: {e}")
        return None

def main():
    print("=" * 60)
    print("Project_21 Phase 1: 数据体检 - 全市场中波股票池扫描")
    print("=" * 60)
    
    stock_basic = load_stock_basic()
    print(f"股票基础信息: {len(stock_basic)} 只")
    
    files = get_parquet_files()
    
    all_stats = []
    batch_size = 100
    
    for i in tqdm(range(0, len(files), batch_size), desc="处理批次"):
        batch_files = files[i:i+batch_size]
        batch_results = []
        
        for f in batch_files:
            res = process_one_stock(f, stock_basic)
            if res:
                batch_results.append(res)
        
        all_stats.extend(batch_results)
        
        if (i // batch_size + 1) % 10 == 0:
            gc.collect()
            print(f"  已处理 {min(i+batch_size, len(files))}/{len(files)} 只股票")
    
    df_stats = pd.DataFrame(all_stats)
    print(f"\n有效股票数: {len(df_stats)}")
    
    # 保存明细
    detail_path = OUTPUT_DIR / "data_health_check_detail.parquet"
    df_stats.to_parquet(detail_path, index=False)
    print(f"明细已保存: {detail_path}")
    
    # 汇总统计
    print("\n" + "=" * 60)
    print("全市场汇总统计")
    print("=" * 60)
    
    print(f"\n【振幅分布】")
    print(f"  中位数: {df_stats['amplitude_median'].median():.2f}%")
    print(f"  均值:   {df_stats['amplitude_mean'].mean():.2f}%")
    print(f"  P90:    {df_stats['amplitude_p90'].median():.2f}%")
    print(f"  P95:    {df_stats['amplitude_p95'].median():.2f}%")
    print(f"  P99:    {df_stats['amplitude_p99'].median():.2f}%")
    print(f"  最大值: {df_stats['amplitude_max'].max():.2f}%")
    
    print(f"\n【换手率/成交额(亿元)】")
    print(f"  中位数: {df_stats['turnover_median'].median():.2f} 亿")
    print(f"  均值:   {df_stats['turnover_mean'].mean():.2f} 亿")
    
    print(f"\n【成交量(万手)】")
    print(f"  中位数: {df_stats['vol_median'].median():.0f} 万手")
    print(f"  均值:   {df_stats['vol_mean'].mean():.0f} 万手")
    
    print(f"\n【涨跌停可成交性】")
    print(f"  涨停天数总计: {df_stats['limit_up_days'].sum()}")
    print(f"  涨停可成交:   {df_stats['limit_up_tradable'].sum()} ({df_stats['limit_up_tradable'].sum()/max(1,df_stats['limit_up_days'].sum())*100:.1f}%)")
    print(f"  跌停天数总计: {df_stats['limit_down_days'].sum()}")
    print(f"  跌停可成交:   {df_stats['limit_down_tradable'].sum()} ({df_stats['limit_down_tradable'].sum()/max(1,df_stats['limit_down_days'].sum())*100:.1f}%)")
    
    print(f"\n【隔夜跳空】")
    print(f"  绝对值均值: {df_stats['gap_abs_mean'].mean():.2f}%")
    print(f"  绝对值中位: {df_stats['gap_abs_median'].median():.2f}%")
    
    # 按市场分组
    print(f"\n【分市场统计】")
    for mkt in ['主板', '创业板', '科创板', '北交所']:
        sub = df_stats[df_stats['market'] == mkt]
        if len(sub) > 0:
            print(f"  {mkt}: {len(sub)}只, 振幅中位={sub['amplitude_median'].median():.2f}%, 成交额中位={sub['turnover_median'].median():.2f}亿")
    
    # 候选中波池筛选预览
    print(f"\n【中波候选池预览（振幅中位 2.5%-8%、日均成交额>0.5亿）】")
    candidates = df_stats[
        (df_stats['amplitude_median'] >= 2.5) & 
        (df_stats['amplitude_median'] <= 8.0) & 
        (df_stats['turnover_median'] >= 0.5)
    ].sort_values('turnover_median', ascending=False)
    print(f"  候选数量: {len(candidates)}")
    if len(candidates) > 0:
        print(candidates[['ts_code', 'name', 'market', 'amplitude_median', 'turnover_median', 'vol_median']].head(20).to_string(index=False))
    
    # 保存候选池
    cand_path = OUTPUT_DIR / "midvol_candidates_preview.parquet"
    candidates.to_parquet(cand_path, index=False)
    print(f"\n候选池预览已保存: {cand_path}")
    
    # 输出关键分位数供阈值定义参考
    print(f"\n【关键分位数（用于定义阈值）】")
    for col in ['amplitude_median', 'turnover_median', 'vol_median']:
        for q in [0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99]:
            print(f"  {col} P{int(q*100)}: {df_stats[col].quantile(q):.4f}")
    
    print("\n" + "=" * 60)
    print("数据体检完成")
    print("=" * 60)

if __name__ == "__main__":
    main()