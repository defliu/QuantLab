# coding: utf-8
"""21f 影子臂「主库更新后」跑批：先重建 merged_daily_full，再跑 paper_shadow_b21 --daily。

背景（2026-10-31，T-20261031-003）：
  paper_forward_daily 16:55 跑影子臂时主库只有 T-1（Tushare 17:40 才补 T），
  脚本按「数据最后交易日 == 运行日」判 forward → 永远记 backfill。
  本脚本排在 17:50（主库已更新），data_last == 运行日 → 记真正的 forward。

只读/写边界：只重建 data_live/merged_daily_full.parquet（派生文件）并写 data/real/
  paper_shadow_b21_*（影子臂产物）；不碰交易、账本、plan、state。

退出码：0 成功；2 merged 重建失败；其余透传影子臂退出码。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import refresh_panel_v3 as RP  # noqa: E402


def main():
    print("[SHADOW] 重建 merged_daily_full（主库应已由 Tushare 17:40 更新到当日）...")
    try:
        merged = RP.build_merged()
    except Exception as e:
        print("[SHADOW] merged 重建异常：%s" % e)
        return 2
    if not merged or not os.path.exists(merged):
        print("[SHADOW] merged 重建失败，跳过影子臂")
        return 2
    print("[SHADOW] merged -> %s" % merged)
    rc = subprocess.call([sys.executable, os.path.join(HERE, "paper_shadow_b21.py"), "--daily"], cwd=HERE)
    print("[SHADOW] paper_shadow_b21 rc=%d" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
