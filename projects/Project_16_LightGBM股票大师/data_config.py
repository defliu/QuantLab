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

# ---- 行情增量目录（2026-08-28 新增，研发/回测使用）----
# 用户约定：每周行情增量数据以"周区间"子目录形式放入 E:/astock/Updatedata，
# 每个子目录内为独立 parquet（stock_daily.parquet / stock_1min_data.parquet 等）。
# 读取时按"主仓 + 增量合并"处理：增量目录按日期去重后覆盖/追加到主仓，增量优先。
UPDATE_DATA_DIR = os.path.join(ASTOCK_DIR, "Updatedata")
# 增量目录内周子目录的命名前缀约定（如 "8.17-8.21"），用于扫描识别
UPDATE_SUBDIR_PREFIX_HINT = "."

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


def list_update_weeks():
    """扫描 Updatedata 下的周增量子目录，按名称排序返回目录路径列表。

    返回每个包含 stock_daily.parquet 的周子目录路径；目录不存在或为空时返回 []。
    调用方无需感知目录命名规则，只需对返回列表逐个读取并合并。
    """
    if not os.path.isdir(UPDATE_DATA_DIR):
        return []
    weeks = []
    for name in sorted(os.listdir(UPDATE_DATA_DIR)):
        sub = os.path.join(UPDATE_DATA_DIR, name)
        if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, "stock_daily.parquet")):
            weeks.append(sub)
    return weeks


def read_main_daily(columns=None):
    """读取研发/回测用日线行情：主仓 + Updatedata 增量合并，增量优先。

    参数 columns：需要读取的列名列表（不含 trade_date/ts_code 也自动保留二者）；
    不传则返回全列。返回 MultiIndex (trade_date, ts_code) 的 DataFrame，
    与主仓原始格式一致，供 build_features / build_panel 等直接消费。

    合并规则：主仓 + 各周增量按 (trade_date, ts_code) 去重，增量优先
    （同一天同一只股票以增量目录中的值为准，增量用于补充主仓缺失的新日期）。
    """
    import pandas as pd

    need = ["trade_date", "ts_code"]
    if columns:
        need += [c for c in columns if c not in need]
    df = pd.read_parquet(MAIN_DAILY, columns=need)
    # 主仓统一为 MultiIndex
    if not isinstance(df.index, pd.MultiIndex):
        df = df.set_index(["trade_date", "ts_code"])
    df.index.names = ["trade_date", "ts_code"]
    # 增量合并
    weeks = list_update_weeks()
    if not weeks:
        return df
    parts = [df]
    for week_dir in weeks:
        fp = os.path.join(week_dir, "stock_daily.parquet")
        if not os.path.isfile(fp):
            continue
        try:
            inc = pd.read_parquet(fp, columns=need)
        except Exception as e:  # pragma: no cover
            print("[data_config] 增量 parquet 读取失败，跳过 %s: %s" % (fp, e))
            continue
        if "trade_date" not in inc.columns or "ts_code" not in inc.columns:
            continue
        inc = inc.set_index(["trade_date", "ts_code"])
        inc.index.names = ["trade_date", "ts_code"]
        parts.append(inc)
    combined = pd.concat(parts)
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined.sort_index()
    return combined


def model_file(suffix=""):
    """返回模型文件路径，如 model_file() / model_file('_v2') / model_file('_v3')。"""
    return os.path.join(MODEL_DIR, f"lgb_model{suffix}.txt")
