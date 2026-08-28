# coding: utf-8
"""模型-面板同步校验（T-20260828-005 固化）。

背景：2026-08-28 发现正式模型 lgb_model_v3.txt 训练于旧面板(8/14 冻结版)，
生产面板已重建到 8/27 —— "新面板喂旧模型"造成训练/推理分布不一致（选股分不可信）。
本脚本校验：正式模型绑定的面板日期 vs 当前生产面板日期是否一致。

绑定记录 data/model_panel_binding.json 由 promote_model.py 每次提升正式模型时写入：
  {formal_model, model_mtime, model_trees, panel_file, panel_max_date, promote_time}

规则（任一不满足即 exit 1，fail-loud）：
  1. 绑定记录不存在          -> 告警（从未 promote 或记录丢失）
  2. 当前面板最新日 > 绑定日 -> 告警（面板已更新，正式模型需重训+promote）
  3. 当前面板最新日 < 绑定日 -> 告警（面板回退/异常）
  4. 正式模型文件 mtime/树数 != 绑定 -> 告警（模型被改但未登记）

用法：python verify_model_panel_sync.py
接线：run_scheduled.ps1 daily（盘后）/ retrain（周更后）自动调用，exit!=0 日志告警。
"""
import datetime
import json
import os
import sys

import pandas as pd

import data_config as DC  # noqa: E402

DATA = DC.DATA_DIR
BINDING = os.path.join(DATA, "model_panel_binding.json")
PROD_MODEL = DC.model_file("_v3")
PANEL = os.path.join(DATA, "feature_panel_v3.parquet")


def count_trees(path):
    n = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("Tree="):
                n += 1
    return n


def panel_max_date():
    if not os.path.exists(PANEL):
        return None
    df = pd.read_parquet(PANEL, columns=["trade_date"])
    return pd.to_datetime(df["trade_date"]).max().date()


def main():
    issues = []

    if not os.path.exists(BINDING):
        print("!! 无模型-面板绑定记录 data/model_panel_binding.json —— 从未 promote 或记录丢失，需先 promote 一次")
        sys.exit(1)
    b = json.load(open(BINDING, encoding="utf-8"))

    if not os.path.exists(PROD_MODEL):
        print(f"!! 正式模型不存在: {PROD_MODEL}")
        sys.exit(1)
    if not os.path.exists(PANEL):
        print(f"!! 生产面板不存在: {PANEL}")
        sys.exit(1)

    cur_panel = panel_max_date()
    cur_trees = count_trees(PROD_MODEL)
    cur_mtime = os.path.getmtime(PROD_MODEL)

    bind_panel = b.get("panel_max_date")
    try:
        bind_panel = datetime.date.fromisoformat(str(bind_panel)) if bind_panel else None
    except ValueError:
        bind_panel = None
    bind_trees = b.get("model_trees")
    bind_mtime = b.get("model_mtime")

    print(f"当前: 面板最新日 {cur_panel} | 正式模型树数 {cur_trees} | mtime {datetime.datetime.fromtimestamp(cur_mtime).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"绑定: 面板最新日 {bind_panel} | 正式模型树数 {bind_trees} | promote_time {b.get('promote_time','?')}")

    if bind_panel is None:
        issues.append("绑定记录缺失 panel_max_date")
    else:
        if cur_panel > bind_panel:
            issues.append(f"面板已更新至 {cur_panel}，但正式模型仍绑定 {bind_panel} —— 需重训候选并 promote（模型与数据必须同版联动）")
        elif cur_panel < bind_panel:
            issues.append(f"面板最新日 {cur_panel} 小于绑定 {bind_panel}（面板回退/异常）")

    if bind_trees is not None and cur_trees != bind_trees:
        issues.append(f"正式模型树数 {cur_trees} != 绑定 {bind_trees} —— 模型被改动但未登记")
    if bind_mtime is not None and abs(cur_mtime - float(bind_mtime)) > 60:
        issues.append("正式模型文件 mtime 与绑定不一致 —— 模型被改动但未登记")

    if issues:
        for it in issues:
            print("!! " + it)
        print("校验结果: 不一致 (exit=1)")
        sys.exit(1)

    print("校验结果: 一致（面板与正式模型同版）")
    sys.exit(0)


if __name__ == "__main__":
    main()
