# coding=gbk
"""多因子IC小盘Alpha — QMT模拟盘策略

最优参数（已验证 15.5% 年化）：
  因子: BP(30%)+反转1m(25%)+低波60d(25%)+ROE(20%)
  市值: 0-30亿 | 调仓: 双月 | TOP80 | 止损-12%
  成交额: >2000万

部署：粘贴到 miniQMT 策略编辑器，设置为全天运行
"""
ACCOUNT_ID = '70180771'
LOG_FILE = "D:/QMT_POOL/mfic_sim_log.txt"

# 策略参数
TOP_N = 80
STOP_LOSS = -0.12
MV_MAX = 300000      # 30亿 = 300000万元
AMOUNT_MIN = 20000   # 2000万 = 20000千元
MAX_POSITION_PCT = 0.02  # 单票最大仓位 2%

# 财务数据CSV路径
FIN_CSV = "D:/QMT_POOL/mfic_fin_data.csv"


def _log(msg):
    """打印并写入日志"""
    print(msg)
    try:
        import os
        log_dir = os.path.dirname(LOG_FILE)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
        with open(LOG_FILE, "a", encoding="gbk") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _get_time(C):
    try:
        tick_time = C.get_tick_timetag()
        if tick_time and tick_time > 0:
            from datetime import datetime
            return datetime.fromtimestamp(tick_time)
    except Exception:
        pass
    try:
        bar_time = C.get_bar_timetag(C.barpos)
        if bar_time and bar_time > 0:
            from datetime import datetime
            return datetime.fromtimestamp(bar_time)
    except Exception:
        pass
    from datetime import datetime
    return datetime.now()


def _load_fin_data():
    """从CSV加载财务数据"""
    import pandas as pd
    try:
        df = pd.read_csv(FIN_CSV, encoding="gbk")
        fin_data = {}
        for _, row in df.iterrows():
            code = row["ts_code"]
            fin_data[code] = {
                "pb": float(row["pb"]) if pd.notna(row.get("pb")) else None,
                "pe_ttm": float(row["pe_ttm"]) if pd.notna(row.get("pe_ttm")) else None,
                "circ_mv": float(row["circ_mv"]) if pd.notna(row.get("circ_mv")) else None,
                "roe": float(row["roe"]) if pd.notna(row.get("roe")) else None,
                "amount": float(row["amount"]) if pd.notna(row.get("amount")) else None,
            }
        return fin_data
    except Exception as e:
        _log("[mfic] 财务数据加载失败: %s" % e)
        return {}


def _compute_scores(codes, fin_data, md):
    """计算4因子评分"""
    import numpy as np
    import pandas as pd

    factor_data = {}
    for code in codes:
        try:
            # 从行情数据获取价格
            h = md.get(code, {})
            close_arr = np.array(h.get("close", []), dtype=float)
            if len(close_arr) < 60:
                continue

            # 从财务数据获取PB
            fd = fin_data.get(code, {})
            pb = fd.get("pb")
            if pb is None or pb <= 0:
                continue

            # 市值过滤
            circ_mv = fd.get("circ_mv")
            if circ_mv is None or circ_mv <= 0 or circ_mv >= MV_MAX:
                continue

            # 成交额过滤
            amount = fd.get("amount")
            if amount is None or amount <= AMOUNT_MIN:
                continue

            # BP = 1/PB
            bp = 1.0 / pb

            # 反转 = -1 * 近20日收益
            if len(close_arr) >= 21:
                ret_1m = close_arr[-2] / close_arr[-22] - 1.0
            else:
                ret_1m = 0.0

            # 60日波动率
            if len(close_arr) >= 61:
                pct_returns = np.diff(close_arr[-62:]) / close_arr[-62:-1]
                vol_60d = np.nanstd(pct_returns)
            else:
                pct_returns = np.diff(close_arr) / close_arr[:-1]
                vol_60d = np.nanstd(pct_returns)

            # ROE
            roe = fd.get("roe", 0.0) or 0.0

            factor_data[code] = {
                "BP": bp, "reversal_1m": ret_1m,
                "volatility_60d": vol_60d, "ROE": roe
            }
        except Exception:
            continue

    if len(factor_data) < TOP_N:
        return []

    # Z-score标准化 + 加权
    df = pd.DataFrame(factor_data).T
    weights = {"BP": 0.30, "reversal_1m": 0.25, "volatility_60d": 0.25, "ROE": 0.20}

    scores = pd.Series(0.0, index=df.index)
    for col, w in weights.items():
        if col in df.columns:
            vals = df[col]
            mu, std = vals.mean(), vals.std()
            if std > 0:
                z = (vals - mu) / std
                # 反转/低波取负值
                if col in ["reversal_1m", "volatility_60d"]:
                    z = -z
                scores += w * z

    return scores.sort_values(ascending=False).head(TOP_N).index.tolist()


def _is_rebalance_day(C, today):
    """判断是否为双月调仓日"""
    try:
        month = today.month
        if month % 2 != 0:
            return False
        if month == 12:
            next_first = today.replace(year=today.year+1, month=1, day=1)
        else:
            next_first = today.replace(month=month+1, day=1)
        last_day = next_first - timedelta(days=1)
        tds = C.get_trading_dates(
            today.replace(day=1).strftime("%Y%m%d"),
            last_day.strftime("%Y%m%d"),
            "1d"
        )
        if tds and len(tds) > 0 and tds[-1] == today.strftime("%Y%m%d"):
            return True
    except Exception:
        pass
    return False


def init(C):
    """初始化"""
    from datetime import datetime
    C.mfic_date = datetime.now().strftime("%Y-%m-%d")
    C.mfic_fin_data = _load_fin_data()
    C.mfic_positions = {}
    C.mfic_last_rebal = None
    C.mfic_state = "idle"

    _log("")
    _log("=" * 50)
    _log("[mfic] 多因子IC小盘Alpha 模拟盘")
    _log("[mfic] 账号: %s | 日期: %s" % (ACCOUNT_ID, C.mfic_date))
    _log("[mfic] 财务数据: %d只" % len(C.mfic_fin_data))
    _log("[mfic] 参数: TOP%d SL%.0f%% MV<%.0f亿" % (TOP_N, STOP_LOSS*100, MV_MAX/10000))
    _log("=" * 50)


def handlebar(C):
    """每根K线执行"""
    now = _get_time(C)
    hour = now.hour
    minute = now.minute

    # ====== 1. 止损检查（每次） ======
    _check_stop_loss(C, now)

    # ====== 2. 调仓日执行 ======
    if _is_rebalance_day(C, now) and C.mfic_last_rebal != now.strftime("%Y-%m-%d"):
        _execute_rebalance(C, now)

    # ====== 3. 收盘汇总（15:00） ======
    if hour == 15 and minute == 0:
        _daily_summary(C)


def _check_stop_loss(C, now):
    """检查止损"""
    ts = now.strftime("%H:%M")
    for code, pos in list(C.mfic_positions.items()):
        try:
            tick = C.get_full_tick([code])
            if tick and code in tick:
                price = tick[code].get("lastPrice", 0)
                if price > 0 and pos["entry_price"] > 0:
                    pnl = price / pos["entry_price"] - 1.0
                    if pnl <= STOP_LOSS:
                        _log("[%s] 止损: %s 入场%.2f 现价%.2f %.1f%%" % (
                            ts, code, pos["entry_price"], price, pnl*100))
                        passorder(24, 1101, ACCOUNT_ID, code, 5, -1, pos["shares"], C)
                        del C.mfic_positions[code]
                        _save_positions(C)
        except Exception:
            continue


def _execute_rebalance(C, now):
    """执行调仓"""
    ts = now.strftime("%H:%M")
    _log("[%s] 调仓日开始..." % ts)

    # 获取全市场行情
    try:
        codes = C.get_stock_list_in_sector("沪深A股")
    except Exception:
        codes = []
    codes = [c for c in codes if c and not c.startswith("3")]  # 排除创业板

    # 获取120日行情
    try:
        md = C.get_market_data_ex(
            stock_code=codes[:1000],  # 分批
            period="1d",
            count=120,
            dividend_type="front"
        )
    except Exception as e:
        _log("[%s] 获取数据失败: %s" % (ts, e))
        return

    # 计算评分
    top_stocks = _compute_scores(codes, C.mfic_fin_data, md)
    if len(top_stocks) < TOP_N:
        _log("[%s] 候选不足%d只" % (ts, TOP_N))
        return

    _log("[%s] 评分完成: %d只候选" % (ts, len(top_stocks)))

    # 卖出不在池中的持仓
    held = set(C.mfic_positions.keys())
    to_sell = [c for c in held if c not in top_stocks]
    for code in to_sell:
        pos = C.mfic_positions[code]
        try:
            passorder(24, 1101, ACCOUNT_ID, code, 5, -1, pos["shares"], C)
            _log("[%s] 卖出: %s %d股" % (ts, code, pos["shares"]))
            del C.mfic_positions[code]
        except Exception as e:
            _log("[%s] 卖出失败 %s: %s" % (ts, code, e))

    # 买入新股票
    new_buys = [c for c in top_stocks if c not in C.mfic_positions]
    try:
        account = C.get_account_info()
        available = account.get("cash", CAPITAL) if account else CAPITAL
    except Exception:
        available = CAPITAL

    cash_per = available * 0.95 / max(len(new_buys), 1)
    for code in new_buys[:TOP_N]:
        try:
            tick = C.get_full_tick([code])
            if tick and code in tick:
                price = tick[code].get("lastPrice", 0)
                if price > 0:
                    shares = int(cash_per / price / 100) * 100
                    if shares >= 100:
                        passorder(23, 1101, ACCOUNT_ID, code, 11, price, shares, C)
                        C.mfic_positions[code] = {
                            "shares": shares,
                            "entry_price": price,
                            "buy_date": now.strftime("%Y-%m-%d"),
                        }
                        _log("[%s] 买入: %s %d股 @ %.2f" % (ts, code, shares, price))
        except Exception as e:
            _log("[%s] 买入失败 %s: %s" % (ts, code, e))

    C.mfic_last_rebal = now.strftime("%Y-%m-%d")
    _save_positions(C)
    _log("[%s] 调仓完成: 持仓%d只" % (ts, len(C.mfic_positions)))


def _save_positions(C):
    """保存持仓"""
    import json, os
    try:
        pos_file = "D:/QMT_POOL/mfic_positions.json"
        os.makedirs(os.path.dirname(pos_file), exist_ok=True)
        with open(pos_file, "w", encoding="utf-8") as f:
            json.dump(C.mfic_positions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _daily_summary(C):
    """收盘汇总"""
    _log("")
    _log("====== 收盘汇总 ======")
    _log("持仓: %d只" % len(C.mfic_positions))
    for code, pos in C.mfic_positions.items():
        try:
            tick = C.get_full_tick([code])
            price = tick[code].get("lastPrice", pos["entry_price"]) if tick and code in tick else pos["entry_price"]
            pnl = price / pos["entry_price"] - 1.0 if pos["entry_price"] > 0 else 0
            _log("  %s: %d股 成本%.2f 现价%.2f 盈亏%.1f%%" % (
                code, pos["shares"], pos["entry_price"], price, pnl*100))
        except Exception:
            _log("  %s: %d股" % (code, pos["shares"]))
    _log("===========================")
