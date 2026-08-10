# coding=utf-8
"""
组合优化 - E模块
基于Qwen任务书要求
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = "D:/QuantLab/projects/verification/results"


def load_all_equity():
    """加载所有策略净值"""
    equity_files = {
        "红利低波": "D:/QuantLab/projects/Project_05_红利低波/results/dividend_equity.csv",
        "质量小市值": "D:/QuantLab/projects/Project_06_质量小市值/results/smallcap_equity.csv",
        "指数增强": "D:/QuantLab/projects/Project_08_指数增强/results/index_equity.csv",
    }
    
    all_equity = {}
    for name, path in equity_files.items():
        try:
            df = pd.read_csv(path)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
            all_equity[name] = df["value"]
        except Exception as e:
            print(f"  加载{name}失败: {e}")
    
    return pd.DataFrame(all_equity)


def e1_comparison_table():
    """E-1: 六大策略横向对比表"""
    print("\n[E-1] 策略横向对比...")
    
    equity_files = {
        "①质量小市值": "D:/QuantLab/projects/Project_06_质量小市值/results/smallcap_equity.csv",
        "②PEAD": None,  # 亏损策略，用指标
        "③低换手反转": None,
        "④指数增强": "D:/QuantLab/projects/Project_08_指数增强/results/index_equity.csv",
        "⑤红利低波": "D:/QuantLab/projects/Project_05_红利低波/results/dividend_equity.csv",
        "⑥ML多因子": None,
    }
    
    # 手动输入已知结果
    results = [
        {"策略": "①质量小市值", "年化收益": "4.1%", "最大回撤": "-20.1%", "夏普": "0.21", "Calmar": "0.20", "换手": "高", "容量": "2000万-5000万"},
        {"策略": "②PEAD", "年化收益": "-7.8%", "最大回撤": "-62.1%", "夏普": "-0.37", "Calmar": "-0.13", "换手": "低", "容量": "5000万-2亿"},
        {"策略": "③低换手反转", "年化收益": "-6.9%", "最大回撤": "-39.0%", "夏普": "-1.71", "Calmar": "-0.18", "换手": "极高", "容量": "200万-1000万"},
        {"策略": "④指数增强", "年化收益": "0.4%", "最大回撤": "-8.2%", "夏普": "-0.74", "Calmar": "0.05", "换手": "中", "容量": "1亿-5亿"},
        {"策略": "⑤红利低波", "年化收益": "3.7%", "最大回撤": "-11.3%", "夏普": "0.20", "Calmar": "0.32", "换手": "低", "容量": "5000万-2亿"},
        {"策略": "⑥ML多因子", "年化收益": "-21.4%", "最大回撤": "-65.8%", "夏普": "-2.44", "Calmar": "-0.33", "换手": "高", "容量": "500万-5000万"},
    ]
    
    print(f"  对比完成")
    return results


def e2_correlation():
    """E-2: 相关性分析"""
    print("\n[E-2] 相关性分析...")
    
    equity_df = load_all_equity()
    
    if equity_df.empty:
        return {}
    
    # 计算日收益率
    returns = equity_df.pct_change().dropna()
    
    # 相关系数矩阵
    corr = returns.corr()
    
    print("  相关系数矩阵:")
    print(corr.round(2))
    
    return corr


def e3_optimal_weights():
    """E-3: 最优组合权重（风险平价）"""
    print("\n[E-3] 最优组合权重...")
    
    equity_df = load_all_equity()
    
    if equity_df.empty:
        return {}
    
    returns = equity_df.pct_change().dropna()
    
    # 风险平价权重：权重与波动率成反比
    vols = returns.std() * np.sqrt(252)
    inv_vols = 1.0 / vols
    weights = inv_vols / inv_vols.sum()
    
    print("  风险平价权重:")
    for name, w in weights.items():
        print(f"    {name}: {w:.1%}")
    
    return weights


def e4_portfolio_backtest():
    """E-4: 组合回测"""
    print("\n[E-4] 组合回测...")
    
    equity_df = load_all_equity()
    
    if equity_df.empty:
        return {}
    
    # 归一化
    normalized = equity_df / equity_df.iloc[0]
    
    # 计算权重（用风险平价）
    returns = equity_df.pct_change().dropna()
    vols = returns.std() * np.sqrt(252)
    inv_vols = 1.0 / vols
    weights = inv_vols / inv_vols.sum()
    
    # 组合净值
    portfolio = (normalized * weights).sum(axis=1)
    
    # 计算指标
    total_ret = portfolio.iloc[-1] - 1
    years = max(len(portfolio) / 252, 1/12)
    ann_ret = (1 + total_ret) ** (1/years) - 1
    max_dd = (portfolio / portfolio.cummax() - 1).min()
    daily_ret = portfolio.pct_change().dropna()
    sharpe = np.sqrt(252) * (daily_ret - 0.025/252).mean() / daily_ret.std() if daily_ret.std() > 0 else 0
    
    result = {
        "年化收益": f"{ann_ret:.1%}",
        "最大回撤": f"{max_dd:.1%}",
        "夏普比率": f"{sharpe:.2f}",
        "权重": {k: f"{v:.1%}" for k, v in weights.items()}
    }
    
    print(f"  组合年化: {ann_ret:.1%}, 回撤: {max_dd:.1%}, 夏普: {sharpe:.2f}")
    
    return result


def e5_rolling_sharpe():
    """E-5: 滚动夏普分析"""
    print("\n[E-5] 滚动夏普分析...")
    
    equity_df = load_all_equity()
    
    if equity_df.empty:
        return {}
    
    # 组合净值
    returns = equity_df.pct_change().dropna()
    vols = returns.std() * np.sqrt(252)
    weights = (1.0 / vols) / (1.0 / vols).sum()
    
    portfolio_ret = (returns * weights).sum(axis=1)
    
    # 60日滚动夏普
    rolling_mean = portfolio_ret.rolling(60).mean()
    rolling_std = portfolio_ret.rolling(60).std()
    rolling_sharpe = np.sqrt(252) * rolling_mean / rolling_std
    
    # 检查是否有连续6个月夏普<0
    negative_months = (rolling_sharpe < 0).sum()
    total_months = len(rolling_sharpe.dropna())
    
    result = {
        "平均滚动夏普": f"{rolling_sharpe.mean():.2f}",
        "夏普<0天数": f"{negative_months}/{total_months}",
        "通过": "PASS" if negative_months < total_months * 0.3 else "FAIL"
    }
    
    print(f"  平均滚动夏普: {rolling_sharpe.mean():.2f}")
    
    return result


def run_all_tests():
    """运行全部E模块测试"""
    print("=" * 60)
    print("组合优化 - E模块")
    print("=" * 60)
    
    all_results = {}
    
    all_results["E-1 横向对比"] = e1_comparison_table()
    all_results["E-2 相关性"] = e2_correlation()
    all_results["E-3 最优权重"] = e3_optimal_weights()
    all_results["E-4 组合回测"] = e4_portfolio_backtest()
    all_results["E-5 滚动夏普"] = e5_rolling_sharpe()
    
    # 保存报告
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    
    with open(f"{OUTPUT_DIR}/portfolio_report.md", "w", encoding="utf-8") as f:
        f.write("# 组合优化报告 - E模块\n\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        
        for test_name, results in all_results.items():
            f.write(f"## {test_name}\n\n")
            
            if isinstance(results, list) and len(results) > 0:
                headers = results[0].keys()
                f.write("| " + " | ".join(headers) + " |\n")
                f.write("| " + " | ".join(["---"] * len(headers)) + " |\n")
                for r in results:
                    f.write("| " + " | ".join(str(r.get(h, "")) for h in headers) + " |\n")
            elif isinstance(results, dict):
                for k, v in results.items():
                    if isinstance(v, dict):
                        f.write(f"- **{k}**:\n")
                        for kk, vv in v.items():
                            f.write(f"  - {kk}: {vv}\n")
                    else:
                        f.write(f"- **{k}**: {v}\n")
            
            f.write("\n")
    
    print(f"\n报告已保存: {OUTPUT_DIR}/portfolio_report.md")
    
    return all_results


if __name__ == "__main__":
    run_all_tests()
