# coding: utf-8
"""auto_promote.py —— 周更重训后的「条件式自动上线」门禁。

背景（2026-08-31 立）：
  周一 17:00 周更重训能正常产出候选，但 promote_model.py 是**唯一写生产模型的入口**、
  默认走交互式确认（`input()`），无人值守的 retrain 定时任务调不动它。
  结果是「面板已重建到 8/28、正式模型仍绑 8/27」，若无人接管，
  次日 09:15 候选预生成就会用旧模型吃新面板 = 训练/推理分布不一致。
  告警（verify_model_panel_sync exit=1）发了但没人接 = fail-loud 空转。

设计原则：
  **不是无脑自动，而是把「人工拍板」编码为一组可验证的门禁条件。**
  全部通过才自动 promote；任一不通过则拒绝上线并 exit 1，交由告警与人工介入。
  目标是既消除「周更后无人 promote」的开口，又不引入「坏模型自动上线」的风险。

门禁（全部满足才 promote）：
  G0 本次候选与当前正式模型相同（同候选文件名或同树数）→ 无需 promote，exit 0
  G1 候选存在、树数 > 0、特征数 == features_v3.json 定义（沿用 promote_model.py 校验）
  G2 test IC    >= IC_FLOOR                    绝对下限，防模型崩坏
  G3 test IC    >= 正式模型 IC × IC_TOLERANCE   相对不退步，吸收面板重建噪声
  G4 ICIR       >= ICIR_FLOOR                  稳定性下限
  G5 分位收益方向正确：Q5 > Q1                  模型排序方向未反转
  G6 训练报告 mtime > 面板 mtime                确认本次重训确实用了重建后的新面板

说明：
  - test 集为 train_optuna.py 固定切分（2024-07-01 ~ 2026-08-14），不随面板日期变化，
    故相邻两周的 test IC 属近似同口径，可直接比较。
  - 正式模型的 test IC 记录在 data/model_panel_binding.json 的 test_ic 字段，
    由 promote_model.py --test-ic 写入；无记录时 G3 自动放宽为只检查绝对下限。

用法：
  python auto_promote.py --candidate D:/QuantLab/models/lgb_model_v3_retrain_20260831.txt
  python auto_promote.py --candidate <路径> --dry-run   # 只判定不落盘
  python auto_promote.py --candidate <路径> --yes       # 门禁通过后自动 promote（定时任务用）

退出码：
  0 = 已 promote，或判定无需 promote
  1 = 门禁未通过，拒绝 promote（需人工介入）
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

import data_config as DC

HERE = DC.PROJECT_DIR
BINDING = os.path.join(DC.DATA_DIR, "model_panel_binding.json")
REPORT = os.path.join(DC.DATA_DIR, "optuna_report.json")
PANEL = os.path.join(DC.DATA_DIR, "feature_panel_v3.parquet")
META = os.path.join(DC.DATA_DIR, "features_v3.json")
PROD = DC.model_file("_v3")

# ---- 门禁阈值（可按需要调整；改动后请在 VERSIONS.md 登记）----
IC_FLOOR = 0.030       # test IC 绝对下限：低于此视为模型崩坏，绝不自动上线
IC_TOLERANCE = 0.90    # 相对正式模型的容忍比例：允许 10% 回退，吸收面板重建带来的噪声
ICIR_FLOOR = 0.30      # ICIR 下限：低于此认为稳定性不足

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def count_trees(path):
    n = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("Tree="):
                n += 1
    return n


def max_feature_idx(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("max_feature_idx="):
                return int(line.strip().split("=")[1])
    return -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", default="", help="候选模型路径；缺省取 optuna_report.json 的 model_file")
    ap.add_argument("--dry-run", action="store_true", help="只判定不落盘")
    ap.add_argument("--yes", action="store_true", help="门禁通过后执行 promote（定时任务用）")
    args = ap.parse_args()

    print("=" * 68)
    print("[auto_promote] 条件式自动上线门禁  %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 68)

    # ---------- 载入数据 ----------
    if not os.path.exists(REPORT):
        print("!! 训练报告不存在: %s —— 无法评估候选，拒绝 promote" % REPORT)
        sys.exit(1)
    rep = json.load(open(REPORT, encoding="utf-8"))
    m = rep.get("metrics", {}) or {}
    cand_ic = m.get("ic_all")
    cand_icir = m.get("icir")
    cand_acc = m.get("accuracy_0.5")
    q = m.get("quantile_fwd_ret", {}) or {}

    cand = args.candidate or rep.get("model_file", "")
    cand = os.path.abspath(cand) if cand else ""
    if not cand or not os.path.exists(cand):
        print("!! 候选模型不存在: %s —— 拒绝 promote" % cand)
        sys.exit(1)

    bind = {}
    if os.path.exists(BINDING):
        bind = json.load(open(BINDING, encoding="utf-8"))
    prod_ic = bind.get("test_ic")
    bind_trees = bind.get("model_trees")
    bind_cand = bind.get("candidate", "")

    print("候选模型 : %s" % os.path.basename(cand))
    print("test IC  : %s" % ("%.5f" % cand_ic if isinstance(cand_ic, (int, float)) else "缺失"))
    print("ICIR     : %s" % ("%.4f" % cand_icir if isinstance(cand_icir, (int, float)) else "缺失"))
    print("准确率   : %s" % ("%.4f" % cand_acc if isinstance(cand_acc, (int, float)) else "缺失"))
    print("正式模型 : 树数 %s | test IC %s | 绑定候选 %s"
          % (bind_trees, ("%.5f" % prod_ic if isinstance(prod_ic, (int, float)) else "无记录"), bind_cand or "无"))
    print("-" * 68)

    checks = []

    # ---------- G0 是否就是当前正式模型 ----------
    # 注意：只用「候选文件名」判重，**不能用树数**。
    # 树数由 early stopping 决定，相邻两周完全可能收敛到相同树数（如本次 6694 树），
    # 若用树数判重，会把真正需要评估的新候选误判为「已上线」，导致周更永远不升级。
    n_tree = count_trees(cand)
    same_trees = bind_trees is not None and n_tree == bind_trees  # 仅作提示，不用于判重
    if bind_cand and os.path.basename(cand) == os.path.basename(bind_cand):
        print("[G0] 无需 promote：本次候选即当前正式模型（候选文件名与绑定一致）")
        print("判定：跳过（exit=0）")
        sys.exit(0)
    checks.append(("G0 非重复上线", True,
                   "候选 %s 不同于绑定候选 %s%s"
                   % (os.path.basename(cand), bind_cand or "无",
                      "（注意：树数巧合相同 %d，仅作提示）" % n_tree if same_trees else "")))

    # ---------- G1 文件与特征数 ----------
    n_feat = max_feature_idx(cand) + 1
    feat_def = None
    if os.path.exists(META):
        feat_def = len(json.load(open(META, encoding="utf-8")).get("feature_cols", []))
    ok1 = n_tree > 0 and (feat_def is None or n_feat == feat_def)
    checks.append(("G1 候选完整性", ok1, "树数 %d / 特征 %d / 定义 %s" % (n_tree, n_feat, feat_def)))

    # ---------- G2 test IC 绝对下限 ----------
    ok2 = isinstance(cand_ic, (int, float)) and cand_ic >= IC_FLOOR
    checks.append(("G2 IC 绝对下限 >= %.3f" % IC_FLOOR, ok2,
                   "实测 %s" % ("%.5f" % cand_ic if isinstance(cand_ic, (int, float)) else "缺失")))

    # ---------- G3 相对正式模型不退步 ----------
    if isinstance(prod_ic, (int, float)) and isinstance(cand_ic, (int, float)):
        ok3 = cand_ic >= prod_ic * IC_TOLERANCE
        checks.append(("G3 IC 不退步 >= 正式×%.2f" % IC_TOLERANCE, ok3,
                       "候选 %.5f vs 门槛 %.5f（正式 %.5f）" % (cand_ic, prod_ic * IC_TOLERANCE, prod_ic)))
    else:
        ok3 = True
        checks.append(("G3 IC 不退步", True, "正式模型无 IC 记录，放宽为只检查绝对下限（首次自动上线）"))

    # ---------- G4 ICIR 下限 ----------
    ok4 = isinstance(cand_icir, (int, float)) and cand_icir >= ICIR_FLOOR
    checks.append(("G4 ICIR >= %.2f" % ICIR_FLOOR, ok4,
                   "实测 %s" % ("%.4f" % cand_icir if isinstance(cand_icir, (int, float)) else "缺失")))

    # ---------- G5 分位收益方向 ----------
    try:
        q1 = float(q.get("1", q.get(1)))
        q5 = float(q.get("5", q.get(5)))
        ok5 = q5 > q1
        detail5 = "Q1 %.5f -> Q5 %.5f" % (q1, q5)
    except (TypeError, ValueError):
        ok5 = False
        detail5 = "分位收益缺失或非数值"
    checks.append(("G5 分位收益方向正确 (Q5>Q1)", ok5, detail5))

    # ---------- G6 重训确实用了新面板 ----------
    if os.path.exists(PANEL):
        ok6 = os.path.getmtime(REPORT) > os.path.getmtime(PANEL)
        checks.append(("G6 报告晚于面板（确用新面板）", ok6,
                       "report %s vs panel %s"
                       % (datetime.datetime.fromtimestamp(os.path.getmtime(REPORT)).strftime("%m-%d %H:%M:%S"),
                          datetime.datetime.fromtimestamp(os.path.getmtime(PANEL)).strftime("%m-%d %H:%M:%S"))))
    else:
        ok6 = False
        checks.append(("G6 报告晚于面板", False, "生产面板不存在: %s" % PANEL))

    # ---------- 汇总判定 ----------
    print("门禁结果:")
    all_pass = True
    for name, ok, detail in checks:
        flag = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print("  [%s] %-32s %s" % (flag, name, detail))
    print("-" * 68)

    if not all_pass:
        print("判定：门禁未通过，拒绝自动上线（保持现有正式模型不变）")
        print("!! 需人工介入：请核对 data/optuna_report.json 与候选模型，必要时手动 promote 或重训")
        sys.exit(1)

    print("判定：全部门禁通过，%s" % ("[dry-run] 未落盘" if args.dry_run else "执行自动上线"))

    if args.dry_run:
        sys.exit(0)
    if not args.yes:
        r = input("确认自动提升为正式模型? [y/N] ").strip().lower()
        if r not in ("y", "yes"):
            print("已取消")
            sys.exit(0)

    note = ("auto: 周更重训自动上线 | testIC %.5f / ICIR %.4f / acc %.4f"
            % (cand_ic, cand_icir, cand_acc)) if all(isinstance(x, (int, float)) for x in (cand_ic, cand_icir, cand_acc)) \
        else "auto: 周更重训自动上线"
    cmd = [sys.executable, os.path.join(HERE, "promote_model.py"),
           "--model-file", cand, "--suffix", "v3", "--yes", "--note", note]
    if isinstance(cand_ic, (int, float)):
        cmd += ["--test-ic", "%.6f" % cand_ic]
    if isinstance(cand_icir, (int, float)):
        cmd += ["--icir", "%.6f" % cand_icir]
    print(">> %s" % " ".join(cmd))
    ret = subprocess.call(cmd, cwd=HERE)
    if ret != 0:
        print("!! promote_model.py 返回非零 (%s)，自动上线失败，需人工介入" % ret)
        sys.exit(1)
    print("[auto_promote] 完成：候选已提升为正式模型")
    sys.exit(0)


if __name__ == "__main__":
    main()
