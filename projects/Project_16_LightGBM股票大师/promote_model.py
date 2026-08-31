# coding: utf-8
"""promote_model.py —— 上线门禁：把候选模型显式提升为正式模型（唯一写生产模型的入口）。

研发-生产隔离规范（研发-生产隔离规范.md）：
  - 训练脚本（train_optuna.py）一律输出候选文件（lgb_model_v3_<tag>.txt）
  - 生产模型 lgb_model_v3.txt 只能经本脚本提升，禁止研发流程直接写入

用法：
  python promote_model.py --model-file D:/QuantLab/models/lgb_model_v3_cand_20260825.txt --suffix v3 --yes
  python promote_model.py --model-file <候选路径> --suffix v3 --dry-run   # 只校验不落盘

流程：
  1. 校验：候选存在、树数>0、特征数 = features<后缀>.json 特征数
  2. 备份当前正式模型 -> versions/models/lgb_model<后缀>_pre_promote_<stamp>.txt
  3. 清只读 -> 复制候选到正式路径 -> 重新设只读
  4. 追加 VERSIONS.md 上线记录
"""
import argparse
import json
import os
import shutil
import sys
import datetime

import data_config as DC

HERE = DC.PROJECT_DIR
MODELS_BACKUP = os.path.join(HERE, "versions", "models")
BINDING = os.path.join(DC.DATA_DIR, "model_panel_binding.json")


def normalize_suffix(s):
    s = s or ""
    if s and not s.startswith("_"):
        s = "_" + s
    return s


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
    ap.add_argument("--model-file", required=True, help="候选模型绝对路径")
    ap.add_argument("--suffix", default="v3", help="正式模型后缀：''(v1) / v2 / v3")
    ap.add_argument("--yes", action="store_true", help="跳过确认")
    ap.add_argument("--dry-run", action="store_true", help="只校验不落盘")
    ap.add_argument("--note", default="", help="登记备注（可选）")
    ap.add_argument("--test-ic", default="", help="候选 test IC（写入绑定记录，供 auto_promote.py 下次门禁对比）")
    ap.add_argument("--icir", default="", help="候选 ICIR（写入绑定记录）")
    args = ap.parse_args()

    suffix = normalize_suffix(args.suffix)
    cand = os.path.abspath(args.model_file)
    prod = DC.model_file(suffix)  # lgb_model.txt / _v2 / _v3
    if not os.path.exists(cand):
        print(f"!! 候选模型不存在: {cand}")
        sys.exit(1)

    # ---- 1) 校验 ----
    n_tree = count_trees(cand)
    if n_tree <= 0:
        print("!! 候选模型树数为 0，拒绝提升")
        sys.exit(1)
    n_feat = max_feature_idx(cand) + 1
    meta_path = os.path.join(DC.DATA_DIR, f"features{suffix}.json")
    feat_count = None
    if os.path.exists(meta_path):
        meta = json.load(open(meta_path, encoding="utf-8"))
        feat_count = len(meta.get("feature_cols", []))
    if feat_count is not None and n_feat != feat_count:
        print(f"!! 特征数不匹配：候选={n_feat} vs 定义={feat_count}，拒绝提升")
        sys.exit(1)

    print("候选:", cand)
    print("树数:", n_tree, " 特征数:", n_feat, " 定义特征数:", feat_count)
    print("目标正式模型:", prod)
    if args.dry_run:
        print("[dry-run] 校验通过，未做任何改动")
        sys.exit(0)
    if not args.yes:
        r = input("确认提升为正式模型? [y/N] ").strip().lower()
        if r not in ("y", "yes"):
            print("已取消")
            sys.exit(0)

    # ---- 2) 备份当前正式模型 ----
    os.makedirs(MODELS_BACKUP, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(MODELS_BACKUP, f"lgb_model{suffix}_pre_promote_{stamp}.txt")
    if os.path.exists(prod):
        try:
            os.chmod(prod, 0o666)  # 清只读（Windows）
        except Exception:
            pass
        shutil.copy2(prod, bak)
        print(f"已备份旧正式模型 -> {bak}")

    # ---- 3) 复制候选 -> 正式路径（清只读 -> 替换 -> 重新只读）----
    try:
        os.chmod(prod, 0o666)
    except Exception:
        pass
    shutil.copy2(cand, prod)
    try:
        os.chmod(prod, 0o444)  # 重新设只读（生产文件保护）
    except Exception:
        pass
    print(f"已提升 -> {prod}（已重设只读）")

    # ---- 4) 追加 VERSIONS.md 上线记录 ----
    reg = (f"- [{stamp}] {os.path.basename(cand)} -> {os.path.basename(prod)} "
           f"(树数 {n_tree} / 特征 {n_feat}"
           + (f" / {args.note}" if args.note else "") + ")")
    vers = os.path.join(HERE, "VERSIONS.md")
    if os.path.exists(vers):
        with open(vers, "a", encoding="utf-8") as f:
            f.write(f"\n> 上线记录（promote_model.py）\n{reg}\n")
        print("已追加 VERSIONS.md 上线记录")

    # ---- 5) 写入模型-面板绑定记录（verify_model_panel_sync.py 校验用）----
    try:
        import pandas as pd
        panel_path = os.path.join(DC.DATA_DIR, f"feature_panel{suffix}.parquet")
        pdate = None
        if os.path.exists(panel_path):
            pdate = pd.to_datetime(pd.read_parquet(panel_path, columns=["trade_date"])["trade_date"]).max()
            pdate = pdate.strftime("%Y-%m-%d")
        binding = {
            "formal_model": os.path.basename(prod),
            "model_mtime": os.path.getmtime(prod),
            "model_trees": n_tree,
            "panel_file": os.path.basename(panel_path),
            "panel_max_date": pdate,
            "promote_time": stamp,
            "candidate": os.path.basename(cand),
            "note": args.note,
        }
        # 指标入档：auto_promote.py 下次门禁据此做「相对不退步」判断（G3）
        try:
            if args.test_ic != "":
                binding["test_ic"] = float(args.test_ic)
            if args.icir != "":
                binding["icir"] = float(args.icir)
        except ValueError:
            print("!! --test-ic/--icir 非数值，已忽略（不影响 promote）")
        with open(BINDING, "w", encoding="utf-8") as f:
            json.dump(binding, f, ensure_ascii=False, indent=2)
        print(f"已写入模型-面板绑定记录 -> {BINDING}（面板 {pdate}）")
    except Exception as e:
        print(f"!! 写入绑定记录失败（不影响 promote 本身）: {e}")
    print("完成。若涉及版本号变更，请同步更新 VERSIONS.md 版本登记表。")


if __name__ == "__main__":
    main()
