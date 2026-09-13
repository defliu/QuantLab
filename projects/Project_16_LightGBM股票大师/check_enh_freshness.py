# -*- coding: utf-8 -*-
"""enh 面板新鲜度硬门禁（2026-09-13 立，T-20260913-001 F2）。

周更 retrain 前置检查：enh 面板末日距今（最近交易日）> MAX_LAG_DAYS 天 → exit 2 阻断训练（fail-loud）。
背景：refresh_panel_enh 有相对门禁（vs merged 5.1），但若 daily 刷新链断流 N 天且无人发现，
train_g2 会用陈旧 enh 面板训练（慢变量 asof 落后），越训越差。此门禁用绝对日历锚兜底。

用法：python check_enh_freshness.py
退出码：0 新鲜（<=7 天）；2 陈旧（>7 天，阻断）；1 运行错误/无法判定（fail-safe 视为可训练但告警）。
"""
import datetime
import json
import os
import sys

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
import data_config as DC

ENH_PANEL = os.path.join(DC.DATA_DIR, "feature_panel_v3_enh.parquet")
V3_PANEL = os.path.join(DC.DATA_DIR, "feature_panel_v3.parquet")
MAX_LAG_DAYS = 7


def _panel_max_date(path):
    import pandas as pd
    df = pd.read_parquet(path)
    if "trade_date" in df.columns:
        return pd.to_datetime(df["trade_date"]).max()
    return pd.to_datetime(df.index.get_level_values("trade_date")).max()


def _latest_trade_day():
    """最近交易日：从 merged_daily_full（主库+增量合并，16:40 每日刷新）取。trade_date 在 index。"""
    import pandas as pd
    p = os.path.join(DC.LIVE_DIR, "merged_daily_full.parquet")
    if not os.path.exists(p):
        return None
    df = pd.read_parquet(p)
    if "trade_date" in df.columns:
        return pd.to_datetime(df["trade_date"]).max()
    return pd.to_datetime(df.index.get_level_values("trade_date")).max()


def main():
    if not os.path.exists(ENH_PANEL):
        print("!! enh 面板不存在: %s" % ENH_PANEL)
        return 1
    enh_max = _panel_max_date(ENH_PANEL)
    latest = _latest_trade_day()
    print("enh 面板末日: %s | 最近交易日: %s" % (enh_max.date(), latest.date() if latest is not None else "未知"))
    if latest is None:
        print("!! 无法获取最近交易日（merged_daily_full 缺失），fail-safe 放行（不阻断训练）但告警")
        return 1
    lag = (latest - enh_max).days
    print("enh 面板落后 %d 自然日（阈值 %d）" % (lag, MAX_LAG_DAYS))
    if lag > MAX_LAG_DAYS:
        print("!! [阻断] enh 面板落后 %d 天 > %d 天——train_g2 将用陈旧慢变量训练，阻断周更（先核查 refresh_panel_enh/daily 链）" % (lag, MAX_LAG_DAYS))
        return 2
    print("[OK] enh 面板新鲜（<=%d 天）" % MAX_LAG_DAYS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
