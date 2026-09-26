# coding: utf-8
"""模型-面板同步校验（T-20260828-005 固化；T-20260923-005 升级：门禁拒绝感知）。

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

T-20260923-005 升级（B2 反复事故彻底修复）：
  背景：策略层门禁每次拒绝候选 → 模型回滚到上周版本 → 面板继续日刷 → gap>7 →
        B2 每天 FAIL，直到下次周更成功（结构性循环，已复发多次）。
  修复：binding 增加 gate_rejected 状态（run_scheduled.ps1 门禁 FAIL 时写入）。
        若 gap>7 但 gate_rejected 在「期望重训窗口」内（next_retrain >= 今天），
        降级为 WARN（exit 0）——这是设计内等待，不是系统故障。
        超过 next_retrain 仍无新 promote → 恢复 FAIL（真·周更失败）。
        成功 promote 时 promote_model.py 重写 binding 自动清除 gate_rejected。

用法：python verify_model_panel_sync.py
接线：run_scheduled.ps1 daily（盘后）/ retrain（周更后）自动调用，exit!=0 日志告警；
      scripts/nightly_check.py B2 解析输出中的 WARN 标记。
"""
import argparse
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


def check_gate_rejected(b, gap, bind_panel):
    """门禁拒绝后的设计内等待判断。返回 (is_expected_wait, message)。"""
    gr = b.get("gate_rejected") or {}
    if not gr:
        return False, ""
    next_retrain = gr.get("next_retrain")
    rejected_at = gr.get("at")
    layer = gr.get("layer", "unknown")
    reason = gr.get("reason", "")
    candidate = gr.get("candidate", "")
    if not next_retrain:
        return False, ""
    try:
        nr = datetime.date.fromisoformat(str(next_retrain))
    except ValueError:
        return False, ""
    today = datetime.date.today()
    if today <= nr:
        msg = (
            "WARN: 门禁拒绝后的设计内等待 —— %s层于 %s 拒绝候选 %s（%s）；"
            "模型有意保持 %s 版，面板超前 %s 天属预期，等待下次周更 %s"
            % (layer, rejected_at, candidate, reason, bind_panel, gap, next_retrain)
        )
        return True, msg
    return False, (
        "已过期望重训日 %s 仍无新 promote —— 上次门禁拒绝 %s，本轮周更可能失败，需人工"
        % (next_retrain, rejected_at)
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--warn-only", action="store_true",
                    help="仅报告不一致项、不改变退出码（供周更门禁拒绝后的信息提示，避免误报为系统故障）")
    args = ap.parse_args()
    issues = []
    expected_wait_msgs = []

    if not os.path.exists(BINDING):
        print("!! 无模型-面板绑定记录 data/model_panel_binding.json —— 从未 promote 或记录丢失，需先 promote 一次")
        sys.exit(1)
    # T-20260923-005 尾巴：run_scheduled.ps1 的 Set-Content -Encoding UTF8 写 binding 带 BOM，
    # 必须用 utf-8-sig 读（否则 JSONDecodeError: Unexpected UTF-8 BOM）。
    b = json.load(open(BINDING, encoding="utf-8-sig"))

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

    gap = None
    if bind_panel is None:
        issues.append("绑定记录缺失 panel_max_date")
    else:
        gap = (cur_panel - bind_panel).days
        if gap > 7:
            is_wait, msg = check_gate_rejected(b, gap, bind_panel)
            if is_wait:
                expected_wait_msgs.append(msg)
            elif msg:
                issues.append(f"面板最新日 {cur_panel} 比绑定 {bind_panel} 超前 {gap} 天(>7) —— {msg}")
            else:
                issues.append(f"面板最新日 {cur_panel} 比绑定 {bind_panel} 超前 {gap} 天(>7) —— 已多日未周更重训 promote（模型与数据必须同版联动）")
        elif cur_panel < bind_panel:
            issues.append(f"面板最新日 {cur_panel} 小于绑定 {bind_panel}（面板回退/异常）")

    if bind_trees is not None and cur_trees != bind_trees:
        issues.append(f"正式模型树数 {cur_trees} != 绑定 {bind_trees} —— 模型被改动但未登记")
    if bind_mtime is not None and abs(cur_mtime - float(bind_mtime)) > 60:
        issues.append("正式模型文件 mtime 与绑定不一致 —— 模型被改动但未登记")

    # 设计内等待：仅当无其他硬性 issue 时降级为 WARN/exit 0
    # （树数/mtime 不一致仍必须 FAIL —— 那是回滚链路 bug，不是设计内等待）
    hard_issues = [i for i in issues if "超前" not in i or "已过期望重训日" in i]
    if expected_wait_msgs and not hard_issues:
        for m in expected_wait_msgs:
            print(m)
        print("校验结果: WARN（门禁拒绝后的设计内等待，非故障；下次周更将自动恢复）")
        sys.exit(0)

    if issues:
        for it in issues:
            print("!! " + it)
        if expected_wait_msgs:
            for m in expected_wait_msgs:
                print(m)
        if args.warn_only:
            print("校验结果: 不一致（--warn-only，仅提示，不阻断）")
            sys.exit(0)
        print("校验结果: 不一致 (exit=1)")
        sys.exit(1)

    print("校验结果: 一致（面板与正式模型同版）")
    sys.exit(0)


if __name__ == "__main__":
    main()
