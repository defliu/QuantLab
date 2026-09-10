# coding: utf-8
"""G4 观察期回滚检查（2026-09-09 门禁升级配套）。

用法：
  python rollback_check_g2.py            # 检查并输出建议（只读）
  python rollback_check_g2.py --apply    # 观察期结束且前向劣于基线时，回退到 prev_model（需人工确认）

逻辑：
  读 data/g2_live_model.json 的 trial 字段（promote 时写入）：
    观察期（ends_after_days=10 个交易日）结束后，统计观察期内该模型在 paper_forward_live.csv
    中选出的候选前向收益（fwd_ret 均值），与 trial.prev_top2_ret 对比：
      前向均值 < prev_top2_ret  -> 建议回退
      前向均值 >= prev_top2_ret -> 通过观察期，移除 trial 字段
"""
import argparse
import copy
import json
import os
import sys

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
import data_config as DC  # noqa: E402

POINTER = DC.G2_LIVE_POINTER
LIVE_CSV = os.path.join(DC.DATA_DIR, "real", "paper_forward_live.csv")
G2_NAME = "g2_strong_real"


def trading_days_since(day_str, live_csv=LIVE_CSV):
    """以 paper_forward_live 中的交易日序列近似交易日历，统计 day_str 之后的交易日数。"""
    try:
        df = pd.read_csv(live_csv)
        dates = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d").unique()
        dates = sorted(dates)
        t0 = pd.Timestamp(day_str)
        after = [d for d in dates if pd.Timestamp(d) > t0]
        return len(after)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="满足条件时执行回退（默认只读）")
    args = ap.parse_args()

    if not os.path.exists(POINTER):
        print("无 live 指针文件")
        return 0
    ptr = json.load(open(POINTER, encoding="utf-8"))
    trial = ptr.get("trial")
    if not trial:
        print(f"当前 live（{os.path.basename(ptr['model_path'])}）无观察期记录（trial 字段为空）。")
        print("  说明：可能是手动回退/旧格式指针；无需检查。")
        return 0

    promoted_at = ptr.get("promoted_at", "")
    ndays = trading_days_since(promoted_at[:10])
    if ndays is None:
        print("无法计算交易日（paper_forward_live.csv 不可用）")
        return 1

    print(f"live 模型: {os.path.basename(ptr['model_path'])}（promoted {promoted_at}）")
    print(f"观察期: 需 {trial['ends_after_days']} 交易日，promoted 后已过 {ndays} 交易日")
    if ndays < trial["ends_after_days"]:
        print(f">> 观察期未结束（还需 {trial['ends_after_days'] - ndays} 交易日），继续观察。")
        return 0

    # 统计观察期内候选的前向收益
    df = pd.read_csv(LIVE_CSV)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    t0 = pd.Timestamp(promoted_at[:10])
    after = df[df["trade_date"] > t0]
    if "fwd_ret" not in after.columns:
        print("paper_forward_live.csv 无 fwd_ret 列，无法对比")
        return 1
    fwd = pd.to_numeric(after["fwd_ret"], errors="coerce").dropna()
    fwd_mean = float(fwd.mean()) if len(fwd) else None

    prev_ret = float(trial["prev_top2_ret"])
    print(f"观察期候选 {len(fwd)} 笔，前向 fwd_ret 均值 = {fwd_mean if fwd_mean is not None else 'N/A'}")
    print(f"基线 prev_top2_ret = {prev_ret:+.4f}（上一模型同窗口 TOP2 模拟）")

    if fwd_mean is None or len(fwd) < 3:
        print(">> 样本不足（<3 笔），暂不判定，继续观察。")
        return 0

    worse = fwd_mean < prev_ret
    verdict = "建议回退" if worse else "通过观察期"
    print(f">> 判定: 前向 {fwd_mean:+.4f} {'< 劣于' if worse else '>= 不劣于'} 基线 {prev_ret:+.4f} -> {verdict}")

    if worse and args.apply:
        prev_model = trial.get("prev_model")
        if prev_model and os.path.exists(prev_model):
            newptr = copy.deepcopy(ptr)
            newptr["model_path"] = prev_model
            # 从模型文件名推断配套 meta（features_v3_g2_strong_real_<date>.json）
            base = os.path.basename(prev_model)
            date_part = base.split("_")[-2] if "_" in base else None
            prev_meta = None
            if date_part:
                cand_meta = os.path.join(DC.DATA_DIR, f"features_v3_{G2_NAME}_{date_part}.json")
                if os.path.exists(cand_meta):
                    prev_meta = cand_meta
            if prev_meta:
                newptr["meta_path"] = prev_meta
            newptr.pop("trial", None)
            newptr["note"] = (newptr.get("note", "") + f" | G4 观察期回退 {promoted_at[:10]}，"
                              f"前向 {fwd_mean:+.4f} < 基线 {prev_ret:+.4f}")
            with open(POINTER, "w", encoding="utf-8") as f:
                json.dump(newptr, f, ensure_ascii=False, indent=2)
            print(f"[已回退] live -> {os.path.basename(prev_model)}（trial 已清除）")
        else:
            print("prev_model 不存在，无法回退，请手动处理")
        return 0

    if not worse:
        newptr = copy.deepcopy(ptr)
        newptr.pop("trial", None)
        newptr["note"] = newptr.get("note", "") + f" | G4 观察期通过 {promoted_at[:10]}（前向 {fwd_mean:+.4f} >= {prev_ret:+.4f}）"
        with open(POINTER, "w", encoding="utf-8") as f:
            json.dump(newptr, f, ensure_ascii=False, indent=2)
        print("[已更新] 观察期通过，trial 字段已清除")
    return 0


if __name__ == "__main__":
    sys.exit(main())
