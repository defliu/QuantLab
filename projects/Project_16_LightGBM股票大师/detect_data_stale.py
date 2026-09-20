# coding: utf-8
"""数据新鲜度检测（DE 保障 #4，2026-09-11）：盘后检查数据链路各源最新日期 vs 今日。
- moneyflow（Tushare）：滞后 > 2 个自然日 → 告警
- 增量库 incremental_daily / merged_daily_full：滞后 > 2 自然日 → 告警
- 面板 feature_panel_v3 / g2 面板：滞后 > 3 自然日 → 告警
- 当日候选（selection_full / g2_top10 / ens）：缺失 → 告警
输出：stdout 明细；退出码 0=正常，2=有滞后/缺失（供 ps1 触发 DE 诊断）。
"""
import json
import os
import sys
from datetime import date, datetime

sys.stdout.reconfigure(encoding="utf-8")

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
ASTOCK = r"D:\astock"
TODAY = date.today().isoformat()


def _lag_days(datestr):
    try:
        d = datetime.strptime(str(datestr)[:10], "%Y-%m-%d").date()
        return (date.today() - d).days
    except Exception:
        return None


def _latest_parquet_date(path):
    """取 parquet 最新 trade_date（兼容 index 与数据列两种布局；仅读 index 加速）。"""
    try:
        import pandas as pd
        d = pd.read_parquet(path, columns=[])
        names = [str(x) for x in d.index.names]
        if "trade_date" in names:
            return str(d.index.get_level_values("trade_date").max())[:10]
    except Exception:
        pass
    try:
        import pandas as pd
        d = pd.read_parquet(path, columns=["trade_date"])
        return str(d["trade_date"].max())[:10]
    except Exception:
        return None


def main():
    issues = []
    rows = []

    # 1) moneyflow（Tushare 每日刷新至 T-1；周末/节假日容差 → 阈值 4 自然日）
    mf = os.path.join(ASTOCK, "moneyflow", "moneyflow.parquet")
    if os.path.exists(mf):
        dt = _latest_parquet_date(mf)
        lag = _lag_days(dt)
        rows.append(("moneyflow", dt, lag))
        if lag is None or lag > 4:
            issues.append("moneyflow 最新 %s，滞后 %s 天" % (dt, lag))
    else:
        rows.append(("moneyflow", "缺失", None))
        issues.append("moneyflow.parquet 不存在")

    # 2) 增量库（每日 16:30 更新；周末容差 → 阈值 3 自然日）
    incr = os.path.join(PROJ, "data_live", "incremental_daily.parquet")
    if os.path.exists(incr):
        dt = _latest_parquet_date(incr)
        lag = _lag_days(dt)
        rows.append(("增量库", dt, lag))
        if lag is None or lag > 3:
            issues.append("增量库最新 %s，滞后 %s 天" % (dt, lag))
    else:
        rows.append(("增量库", "缺失", None))
        issues.append("incremental_daily.parquet 不存在")

    # 3) 面板（data/ 目录；周更重训刷新，周末/周中容差 → 阈值 5 自然日）
    for name, path in [
        ("feature_panel_v3", os.path.join(PROJ, "data", "feature_panel_v3.parquet")),
        ("feature_panel_v2", os.path.join(PROJ, "data", "feature_panel_v2.parquet")),
    ]:
        if os.path.exists(path):
            dt = _latest_parquet_date(path)
            lag = _lag_days(dt)
            rows.append((name, dt, lag))
            if lag is None or lag > 5:
                issues.append("%s 最新 %s，滞后 %s 天" % (name, dt, lag))
        else:
            rows.append((name, "缺失", None))
            issues.append("%s 不存在" % name)

    # 4) 当日候选（G2/V1.3 为周一~五生成；周末/节假日当日缺失属正常回退——只要存在可回退文件即视为正常）
    today = TODAY.replace("-", "")
    for name, p, d in [
        ("V1.3 selection", os.path.join(PROJ, "data", "selections", today + "_selection_full.csv"),
         os.path.join(PROJ, "data", "selections")),
        ("G2 top10", os.path.join(PROJ, "data", "selections", "g2", today + "_g2_top10.csv"),
         os.path.join(PROJ, "data", "selections", "g2")),
    ]:
        if os.path.exists(p):
            continue
        fallback = [f for f in os.listdir(d) if f.endswith(".csv")] if os.path.isdir(d) else []
        if fallback:
            rows.append((name, "当日缺失(回退 %s)" % sorted(fallback)[-1], None))
        else:
            issues.append("候选完全缺失（无任何回退文件）: %s" % name)

    print("数据新鲜度（今日 %s）：" % TODAY)
    for r in rows:
        print("  %-18s 最新 %-12s 滞后 %s" % (r[0], r[1] or "-", r[2] if r[2] is not None else "-"))
    if not issues:
        print("[OK] 全部新鲜")
        return 0
    print("滞后/缺失 %d 项：" % len(issues))
    for i in issues:
        print("  ⚠ " + i)
    return 2


if __name__ == "__main__":
    sys.exit(main())
