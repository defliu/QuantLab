# coding=utf-8
"""网格交易计算器 — 解套辅助工具

用法:
    python research/grid_calculator.py

交互式输入股票现价、成本价、持仓量、可用资金，输出网格分层方案。
"""

import math


def calc_grid(current_price, cost_price, shares_held, extra_capital,
              grid_depth=0.15, layers=6, spacing_mode="percent"):
    """计算网格分层方案。

    Args:
        current_price: 当前股价
        cost_price: 持仓成本价
        shares_held: 当前持仓股数
        extra_capital: 可用于网格的额外资金
        grid_depth: 网格下沿相对当前价的深度 (如 0.15 = 向下15%)
        layers: 网格层数
        spacing_mode: 间距模式 "percent"(等比例) 或 "equal"(等金额差)
    """
    bottom_price = current_price * (1 - grid_depth)
    top_price = current_price  # 网格上沿为当前价

    # 计算每层价格
    if spacing_mode == "percent":
        # 等比间距
        ratio = (bottom_price / top_price) ** (1 / (layers - 1))
        prices = [top_price * (ratio ** i) for i in range(layers)]
    else:
        # 等差间距
        step = (top_price - bottom_price) / (layers - 1)
        prices = [top_price - step * i for i in range(layers)]

    prices = [round(p, 2) for p in prices]

    # 计算每层资金和股数
    capital_per_layer = extra_capital / layers
    shares_per_layer = max(100, int(capital_per_layer / prices[0] / 100) * 100)

    total_cost = sum(p * shares_per_layer for p in prices)
    if total_cost > extra_capital:
        # 资金不够，缩减每层股数
        shares_per_layer = max(100, int(extra_capital / layers / prices[-1] / 100) * 100)

    need_capital = sum(p * shares_per_layer for p in prices)

    # 计算解套价：当股价回升到成本价时，网格累计利润能抵消多少浮亏
    total_hold_value = shares_held * current_price
    total_cost_value = shares_held * cost_price
    floating_loss = total_cost_value - total_hold_value

    total_grid_capital = shares_per_layer * sum(prices)
    total_shares = shares_held + shares_per_layer * layers

    # 每完成一轮买卖的利润估算
    # 假设每层买在买入价，卖在上一层价格（反弹卖出）
    one_cycle_profit = 0
    for i in range(layers - 1):
        buy_price = prices[i]
        sell_price = prices[i + 1]
        one_cycle_profit += (sell_price - buy_price) * shares_per_layer

    # 输出
    print("\n" + "=" * 55)
    print("网格交易方案")
    print("=" * 55)
    print(f"当前股价:       {current_price:.2f}")
    print(f"持仓成本:       {cost_price:.2f}")
    print(f"持仓股数:       {shares_held}")
    print(f"浮亏幅度:       {(cost_price/current_price - 1)*100:.1f}%")
    print(f"浮亏金额:       {floating_loss:.0f}")
    print(f"网格资金:       {extra_capital:.0f}")
    print(f"网格下沿:       {bottom_price:.2f}")
    print(f"网格层数:       {layers}")
    print(f"每层股数:       {shares_per_layer}")
    print(f"每层资金:       {prices[-1]*shares_per_layer:.0f} ~ "
          f"{prices[0]*shares_per_layer:.0f}")
    print(f"网格总占用:     {need_capital:.0f}")
    if need_capital < extra_capital:
        print(f"剩余备用:       {extra_capital - need_capital:.0f}")
    else:
        print(f"资金缺口:       {need_capital - extra_capital:.0f} !!!")

    print("\n" + "-" * 55)
    print(f"{'层':>3} {'买入价':>8} {'反弹卖价':>8} {'数量':>6} {'买入金额':>8} "
          f"{'单层利润':>8}")
    print("-" * 55)
    # 网格逻辑：价格越往下跌越买，反弹到上一层卖出
    # 按从高到低排列
    for i in range(layers):
        buy_p = prices[i]
        # 卖出价为上一层（更高）价格；最上层卖价为买入价+2%
        sell_p = prices[i - 1] if i > 0 else prices[0] * 1.02
        profit = (sell_p - buy_p) * shares_per_layer if sell_p > buy_p else 0
        amt = buy_p * shares_per_layer
        print(f"{i+1:>3} {buy_p:>8.2f} {sell_p:>8.2f} {shares_per_layer:>6} "
              f"{amt:>8.0f} {profit:>+8.0f}")

    print("-" * 55)

    total_grid_shares = shares_per_layer * layers
    avg_grid_cost = sum(prices) / layers * shares_per_layer * layers / total_grid_shares

    # 一轮完整买卖：所有层都买过，反弹时全部卖出
    one_cycle_profit = 0
    for i in range(layers):
        buy_p = prices[i]
        sell_p = prices[i - 1] if i > 0 else prices[0] * 1.02
        if sell_p > buy_p:
            one_cycle_profit += (sell_p - buy_p) * shares_per_layer

    print(f"\n一轮完整买卖利润: {one_cycle_profit:.0f}")
    print(f"网格股数合计:     {total_grid_shares}")
    print(f"综合成本(摊薄后): {(total_cost_value + need_capital) / (shares_held + total_grid_shares):.2f}")

    # 解套所需涨幅（不靠网格，纯等反弹）
    break_even_price = cost_price
    print(f"\n解套价(不操作):  {break_even_price:.2f} (+{(break_even_price/current_price-1)*100:.1f}%)")

    # 解套所需涨幅（靠网格降低综合成本后）
    new_cost = (total_cost_value + need_capital) / (shares_held + total_grid_shares)
    print(f"解套价(含网格):  {new_cost:.2f} (+{(new_cost/current_price-1)*100:.1f}%)")

    # 提醒
    print("\n风险提示:")
    print("  - 跌破网格下沿时停止补仓，重新评估")
    print("  - 个股基本面恶化时不要死扛网格")
    print("  - 建议优先用于 ETF，个股需设硬止损")

    return {
        "prices": prices,
        "shares_per_layer": shares_per_layer,
        "total_grid_shares": total_grid_shares,
        "need_capital": need_capital,
        "one_cycle_profit": one_cycle_profit,
    }


def interactive():
    print("网格交易计算器")
    print("=" * 55)
    try:
        current = float(input("当前股价: "))
        cost = float(input("持仓成本: "))
        shares = int(input("持仓股数: "))
        capital = float(input("可用于网格的资金: "))
        depth = float(input("网格下沿深度(%, 默认15): ") or "15")

        calc_grid(current, cost, shares, capital,
                  grid_depth=depth / 100, layers=6)
    except ValueError as e:
        print(f"输入有误: {e}")
    except KeyboardInterrupt:
        print("\n已取消")


def demo():
    """演示：股价20，成本23，持有1000股，额外5万资金。"""
    print("=" * 55)
    print("演示场景: 现价20, 成本23, 持有1000股, 网格资金5万")
    print("=" * 55)
    calc_grid(20, 23, 1000, 50000, grid_depth=0.15, layers=6)


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        demo()
    else:
        interactive()
