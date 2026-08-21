# coding: utf-8
"""统一数据/模型路径配置（部署到服务器时，只需修改本文件）。

集中管理所有脚本的数据源/输出/模型路径，避免散落硬编码。
部署时：修改 ASTOCK_DIR / UNIVERSE / MODEL_DIR 指向服务器路径即可。
"""
import os

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---- 主数据（周更权威快照，只读）----
ASTOCK_DIR = "E:/astock"
MAIN_DAILY = os.path.join(ASTOCK_DIR, "daily", "stock_daily.parquet")
FINANCE_DIR = os.path.join(ASTOCK_DIR, "finance")
BASIC_DIR = os.path.join(ASTOCK_DIR, "basic")

# 股票池
UNIVERSE = "D:/QuantLab/data/universe_all_a.csv"

# ---- 财务表（finance/ 目录下）----
FIN_FINA_INDICATOR = os.path.join(FINANCE_DIR, "fina_indicator.parquet")
FIN_FORECAST = os.path.join(FINANCE_DIR, "forecast.parquet")
FIN_EXPRESS = os.path.join(FINANCE_DIR, "express.parquet")
FIN_DIVIDEND = os.path.join(FINANCE_DIR, "dividend.parquet")
FIN_SHARE_CHANGE = os.path.join(FINANCE_DIR, "share_change_event.parquet")

# ---- 模型输出（LightGBM 需英文路径）----
MODEL_DIR = "D:/QuantLab/models"

# ---- 项目输出目录 ----
DATA_DIR = os.path.join(PROJECT_DIR, "data")
LIVE_DIR = os.path.join(PROJECT_DIR, "data_live")


def model_file(suffix=""):
    """返回模型文件路径，如 model_file() / model_file('_v2') / model_file('_v3')。"""
    return os.path.join(MODEL_DIR, f"lgb_model{suffix}.txt")
