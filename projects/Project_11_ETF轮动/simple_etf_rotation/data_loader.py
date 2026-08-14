# -*- coding: utf-8 -*-
"""
数据加载模块
支持本地CSV + QMT内置接口
"""
import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta


class DataLoader:
    """数据加载器"""

    def __init__(self, data_dir="data"):
        self.data_dir = data_dir

    def load_csv(self, code, start_date=None, end_date=None):
        """
        从本地CSV加载ETF数据
        CSV格式: date,open,high,low,close,volume,amount
        """
        csv_path = os.path.join(self.data_dir, f"{code}.csv")
        if not os.path.exists(csv_path):
            return None
        try:
            df = pd.read_csv(csv_path)
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
            if start_date:
                df = df[df.index >= pd.to_datetime(start_date)]
            if end_date:
                df = df[df.index <= pd.to_datetime(end_date)]
            return df
        except Exception as e:
            print(f"加载 {code} 失败: {e}")
            return None

    def load_all_etfs(self, codes, start_date=None, end_date=None):
        """批量加载所有ETF数据"""
        result = {}
        for code in codes:
            df = self.load_csv(code, start_date, end_date)
            if df is not None and len(df) > 0:
                result[code] = df
        return result

    def get_current_prices(self, etf_data_dict):
        """获取每只ETF的最新价格"""
        prices = {}
        for code, df in etf_data_dict.items():
            if df is not None and len(df) > 0:
                prices[code] = float(df["close"].iloc[-1])
        return prices

    def generate_sample_data(self, codes, days=1500):
        """
        生成模拟数据（用于测试）
        实际使用时请用真实数据替换
        """
        np.random.seed(42)
        dates = pd.date_range(end=datetime.now(), periods=days, freq="D")
        dates = dates[dates.dayofweek < 5]  # 只保留工作日

        for code in codes:
            # 不同ETF给不同的初始价格和波动率
            config = {
                "510300": (3.8, 0.012),  # 沪深300，波动率中等
                "510500": (5.5, 0.015),
                "512100": (2.5, 0.020),  # 中证1000，小盘高波动
                "159915": (2.2, 0.022),  # 创业板
                "588200": (1.2, 0.028),  # 科创50，高波动
                "512480": (1.0, 0.035),  # 半导体，高波动高Beta
                "515030": (1.5, 0.030),  # 新能源车
                "512010": (0.8, 0.025),  # 医药
                "512660": (1.1, 0.030),  # 军工
                "512880": (0.9, 0.028),  # 证券
            }
            init_price, vol = config.get(code, (2.0, 0.02))

            # 生成几何布朗运动
            returns = np.random.normal(0.0003, vol, len(dates))
            prices = init_price * np.exp(np.cumsum(returns))

            df = pd.DataFrame(
                {
                    "date": dates,
                    "open": prices * (1 + np.random.normal(0, 0.002, len(dates))),
                    "high": prices * (1 + np.abs(np.random.normal(0.005, 0.003, len(dates)))),
                    "low": prices * (1 - np.abs(np.random.normal(0.005, 0.003, len(dates)))),
                    "close": prices,
                    "volume": np.random.randint(1000000, 50000000, len(dates)),
                    "amount": prices * np.random.randint(1000000, 50000000, len(dates)),
                }
            )
            df.to_csv(os.path.join(self.data_dir, f"{code}.csv"), index=False)
        print(f"已生成 {len(codes)} 只ETF的模拟数据")


# QMT内置数据接口示例
class QMTDataLoader:
    """
    QMT内置数据加载器
    在QMT环境中使用
    """

    def __init__(self, ContextInfo):
        self.ContextInfo = ContextInfo

    def load_etf_data(self, code, days=150):
        """从QMT获取ETF历史数据"""
        try:
            data = self.ContextInfo.get_history(
                count=days, field="close", stock_code=code, period="1d"
            )
            return data
        except Exception as e:
            print(f"QMT数据加载失败 {code}: {e}")
            return None

    def get_current_price(self, code):
        """获取当前价格"""
        try:
            return self.ContextInfo.get_last_price(code)
        except Exception as e:
            print(f"获取 {code} 当前价格失败: {e}")
            return 0.0