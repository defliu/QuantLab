# coding: utf-8
"""G4 观察期回滚检查（2026-09-09 门禁升级配套；2026-09-13 深度劣化自动回退 T-20260913-001 M3；09-13 晚 P0-1/P1-2/P2-1 修复）。

用法：
  python rollback_check_g2.py            # 检查并输出建议（只读）
  python rollback_check_g2.py --apply    # 观察期结束且前向劣于基线时，回退到 prev_model（需人工确认）
  python rollback_check_g2.py --auto     # 深度劣化（劣于基线 > AUTO_THRESHOLD）连续 AUTO_STREAK_DAYS 日自动回退 + 飞书；轻度仍人工

逻辑：
  读 data/g2_live_model.json 的 trial 字段（promote 时写入）：
    观察期（ends_after_days=10 个交易日）结束后，统计观察期内该模型在 paper_forward_live.csv
    中选出的候选前向收益（ret 列均值，真实 CSV schema：date/code/.../ret/hold/rank），与 trial.prev_top2_ret 对比：
      前向均值 < prev_top2_ret  -> 建议回退（轻度）或自动回退（深度劣化且 --auto 且连续3日）
      前向均值 >= prev_top2_ret -> 通过观察期，移除 trial 字段

口径注记（P2-9）：paper_forward_live.csv 的 ret 由 paper_forward_daily.ps1 以 --hold 10 回填（N10 口径），
而 G2 live 实盘持有期 N15——评估窗口与实盘持有期不一致，属已知近似（prev_top2_ret 同为 N10 口径，
对比同源公平；待统一前向数据底座时按臂分 hold 解决）。
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
AUTO_THRESHOLD = 0.0005   # 深度劣化阈值：前向均值低于基线 >0.05pp 才自动回退（防微差抖动）
AUTO_STREAK_DAYS = 3      # 连续 3 个交易日深度劣化才自动回退（M3 规格：防单日极端触发，2026-09-13 DE P2-1 对齐）
STREAK_FILE = os.path.join(DC.DATA_DIR, "rollback_streak.json")


def load_streak():
    try:
        with open(STREAK_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_streak(streak):
    tmp = STREAK_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(streak, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STREAK_FILE)


def update_streak(promoted_at, deep_worse):
    """深度劣化连续计数：deep_worse 则 +1，否则归 0；按 promoted_at 隔离（promote 后重新计数）。"""
    st = load_streak()
    if st.get("promoted_at") != promoted_at:
        st = {"promoted_at": promoted_at, "streak": 0}
    st["streak"] = st.get("streak", 0) + 1 if deep_worse else 0
    save_streak(st)
    return st["streak"]


def trading_days_since(day_str):
    """以 paper_forward_live 中的交易日序列近似交易日历，统计 day_str 之后的交易日数。"""
    try:
        df = pd.read_csv(LIVE_CSV)
        dates = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d").unique()
        dates = sorted(dates)
        t0 = pd.Timestamp(day_str)
        after = [d for d in dates if pd.Timestamp(d) > t0]
        return len(after)
    except Exception:
        return None


def _notify(text):
    try:
        from qmt_bridge_client_ens import notify_feishu
        notify_feishu(text)
    except Exception:
        try:
            from qmt_bridge_client import notify_feishu as nf2
            nf2(text)
        except Exception as e:
            print("[通知] 飞书异常: %s" % e)
            return False
    return True


def _do_rollback(ptr, promoted_at, fwd_mean, prev_ret):
    """执行回退（写指针 + trial 清除 + 返回新指针）。失败返回 None（调用方须 fail-loud）。"""
    prev_model = ptr.get("trial", {}).get("prev_model")
    if not (prev_model and os.path.exists(prev_model)):
        print("prev_model 不存在，无法回退，请手动处理")
        return None
    newptr = copy.deepcopy(ptr)
    newptr["model_path"] = prev_model
    base = os.path.basename(prev_model)
    date_part = base.split("_")[-2] if "_" in base else None
    prev_meta = None
    if date_part:
        cand_meta = os.path.join(DC.DATA_DIR, "features_v3_%s_%s.json" % (G2_NAME, date_part))
        if os.path.exists(cand_meta):
            prev_meta = cand_meta
    if prev_meta:
        newptr["meta_path"] = prev_meta
    newptr.pop("trial", None)
    newptr["note"] = (newptr.get("note", "") + " | G4 观察期回退 %s，前向 %+.4f < 基线 %+.4f" % (
        promoted_at[:10], fwd_mean, prev_ret))
    # 原子写（tmp + os.replace，与账本/标记文件惯例一致，防回滚瞬间崩溃留半写指针）
    try:
        tmp = POINTER + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(newptr, f, ensure_ascii=False, indent=2)
        os.replace(tmp, POINTER)
    except Exception as e:
        print("[回滚] 指针写入失败: %s" % e)
        return None
    print("[已回退] live -> %s（trial 已清除）" % os.path.basename(prev_model))
    return newptr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="满足条件时执行回退（默认只读）")
    ap.add_argument("--auto", action="store_true", help="深度劣化自动回退（劣于基线 >%.4f）+ 飞书" % AUTO_THRESHOLD)
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

    # 统计观察期内候选的前向收益（真实 CSV schema：date,code,total_new,prob,entry,exit,ret,hold,rank）
    df = pd.read_csv(LIVE_CSV)
    df["date"] = pd.to_datetime(df["date"])
    t0 = pd.Timestamp(promoted_at[:10])
    after = df[df["date"] > t0]
    if "ret" not in after.columns:
        print("paper_forward_live.csv 无 ret 列，无法对比")
        return 1
    fwd = pd.to_numeric(after["ret"], errors="coerce").dropna()
    fwd_mean = float(fwd.mean()) if len(fwd) else None

    prev_ret = float(trial["prev_top2_ret"])
    print(f"观察期候选 {len(fwd)} 笔，前向 ret 均值 = {fwd_mean if fwd_mean is not None else 'N/A'}")
    print(f"基线 prev_top2_ret = {prev_ret:+.4f}（上一模型同窗口 TOP2 模拟）")

    if fwd_mean is None or len(fwd) < 3:
        print(">> 样本不足（<3 笔），暂不判定，继续观察。")
        return 0

    worse = fwd_mean < prev_ret
    deep_worse = worse and (prev_ret - fwd_mean) > AUTO_THRESHOLD
    verdict = "建议回退" if worse else "通过观察期"
    print(">> 判定: 前向 %+.4f %s 基线 %+.4f -> %s%s" % (
        fwd_mean, "< 劣于" if worse else ">= 不劣于", prev_ret, verdict,
        "（深度劣化 %.4f > %.4f）" % (prev_ret - fwd_mean, AUTO_THRESHOLD) if deep_worse else ""))

    # ---- 深度劣化连续计数（M3 规格：连续 3 日才自动回退，防单日极端触发）----
    # 注意：只有在"观察期已结束"且"样本充足"到达此处才更新 streak；观察期内不累计。
    streak = update_streak(promoted_at[:10], deep_worse)
    print(">> 深度劣化连续 %d/%d 日" % (streak, AUTO_STREAK_DAYS))

    # 深度劣化且 --auto 且连续达阈值：自动回退（回已知好版本是低风险动作，回退方向永远安全）+ 飞书
    # P1-2 修复：回退失败必须 fail-loud（exit 非 0 + 飞书告警），防"深度劣化已自动回退，OK"假阳性
    if deep_worse and streak >= AUTO_STREAK_DAYS and args.auto:
        newptr = _do_rollback(ptr, promoted_at, fwd_mean, prev_ret)
        if newptr:
            _notify("【G4 观察期自动回退】G2 live %s 前向 %+.4f 连续%d日深度劣于基线 %+.4f（>%.4f），已自动回退到 prev_model %s" % (
                os.path.basename(ptr["model_path"]), fwd_mean, streak, prev_ret, AUTO_THRESHOLD,
                os.path.basename(newptr["model_path"])))
            return 0
        _notify("【G4 观察期自动回退失败】G2 live %s 前向 %+.4f 连续%d日深度劣于基线 %+.4f，但回退执行失败（prev_model 缺失/写入异常），需人工处理" % (
            os.path.basename(ptr["model_path"]), fwd_mean, streak, prev_ret))
        print("!! [回滚] 自动回退失败，人工介入（exit 2 供调度感知）")
        return 2

    if worse and args.apply:
        _do_rollback(ptr, promoted_at, fwd_mean, prev_ret)
        return 0

    if not worse:
        newptr = copy.deepcopy(ptr)
        newptr.pop("trial", None)
        newptr["note"] = newptr.get("note", "") + " | G4 观察期通过 %s（前向 %+.4f >= 基线 %+.4f）" % (
            promoted_at[:10], fwd_mean, prev_ret)
        with open(POINTER, "w", encoding="utf-8") as f:
            json.dump(newptr, f, ensure_ascii=False, indent=2)
        print("[已更新] 观察期通过，trial 字段已清除")
        return 0

    # worse 且未 --apply/--auto：建议回退（退出码 2，供 run_scheduled daily 调度告警）
    print(">> 提示: 人工确认后执行 python rollback_check_g2.py --apply 完成回退（或 --auto 深度劣化自动回退）")
    return 2


if __name__ == "__main__":
    sys.exit(main())
