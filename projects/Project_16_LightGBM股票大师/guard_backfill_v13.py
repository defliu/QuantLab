# coding: utf-8
"""V1.3 每日换仓 guard 自动补跑脚本（2026-09-15 立，T-20260915-001）。

背景：TRAE 09:45「开盘实时复核」(15868a74) 偶发"触发但会话中断零产出"（09-15 实证），
      guard 因缺当日 selection_full 只能告警、无法自动补跑（原设计留白）。
      本脚本将 review_full 所需取数脚本化（data_sources.write_review_json 自动采集
      miniQMT+腾讯实时行情 + 9:25 缓存 review_<date>.json/crosscheck_<date>.json），
      使 guard 在缺当日清单时能自动补跑完整链路：
        候选检查 → 实时取数(write_review_json) → review_full 生成当日清单
      （大盘 T 档与 rebalance --live 仍由 rebalance_daily_guard.ps1 负责，本脚本只保证清单就绪）

用法:
  python guard_backfill_v13.py                        # 今日补跑
  python guard_backfill_v13.py --date 20260915        # 指定日（非今日将被拒绝，见 R5）
  python guard_backfill_v13.py --no-deploy            # 候选缺失时不现场 deploy（仅告警）
  python guard_backfill_v13.py --date 20260914 --allow-restamp   # 复现研究专用：允许改签历史日

T-20260915-001 加固（2026-09-15，方案 de_guard_fix_plan_20260915.md）:
  R5   新鲜度校验：面板最新日须 ≥ 目标日上一交易日（guard 10:05 时点=最近已收盘交易日，
       对齐 nightly_check.recent_trade_date 口径，T-20260903-028 修复语义），落后则告警（不阻断）
  R5   拒绝历史日改签：--date 非今日且未带 --allow-restamp 时直接拒绝（防 look-ahead）
  R5c  子进程超时：deploy_predict / review_full 各 600s 超时，防挂起无声僵死
  R1a  F3 缺失统计：review_full 已内置 Tushare 业绩公告兜底（earnings_anns_map），
       本脚本在补跑后统计 f3_missing 并醒目告警（9:25 缓存未覆盖候选时 F3 恒 0 → 总分可能不过红线）

输出:
  data/selections/{date}_selection_full.csv           # 生成的当日清单
  data/cache/review_guard_{date}.json                 # 自动采集的实时复核数据
  stdout JSON: {"ok":true,"date":"...","sel":"...","cand":N,"f3_missing":N,"panel_stale":bool,...}
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
SELECT_DIR = os.path.join(DATA_DIR, "selections")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
PANEL_PATH = os.path.join(DATA_DIR, "feature_panel_v3.parquet")


def log(m):
    print("[guard_backfill] %s" % m, flush=True)


def latest_panel_date():
    import pandas as pd
    if not os.path.exists(PANEL_PATH):
        raise RuntimeError("面板缺失: %s" % PANEL_PATH)
    df = pd.read_parquet(PANEL_PATH, columns=["trade_date"])
    return pd.to_datetime(df["trade_date"]).max().strftime("%Y%m%d")


def expected_panel_date(target_date):
    """R5 新鲜度校验的期望值：目标日 D 的上一交易日 = guard 10:05 运行时点的「最近已收盘交易日」
    （对齐 nightly_check.recent_trade_date 口径，T-20260903-028 修复语义：未收盘不算当日）。
    交易日历不可用时返回 None（跳过校验，不阻断补跑）。"""
    import datetime
    import is_trade_day as ITD
    try:
        d = datetime.datetime.strptime(str(target_date), "%Y%m%d").date()
        cal = ITD.load_calendar()
        for _ in range(20):
            d -= datetime.timedelta(days=1)
            if ITD.is_trade_day(d, cal).get("is_trade_day"):
                return d.strftime("%Y%m%d")
    except Exception as e:
        log("  [新鲜度] 交易日历不可用，跳过校验: %s" % e)
    return None


def ensure_candidate(panel_date, allow_deploy=True):
    """候选 = data/selections/{panel_date}_model_top10.csv；缺失时现场 deploy_predict 生成。"""
    path = os.path.join(SELECT_DIR, "%s_model_top10.csv" % panel_date)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        log("候选已就绪: %s" % os.path.basename(path))
        return path
    if not allow_deploy:
        raise RuntimeError("候选缺失且 --no-deploy，无法补跑: %s" % path)
    log("候选缺失 %s，现场运行 deploy_predict.py --model v3 --top-k 10 ..." % os.path.basename(path))
    # R5c：600s 超时防挂起无声僵死（挂起时 guard ps1 会一直等，任务卡死无告警）
    try:
        r = subprocess.run([sys.executable, "deploy_predict.py", "--model", "v3", "--top-k", "10"],
                           cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=600)
    except subprocess.TimeoutExpired:
        raise RuntimeError("deploy_predict 超时 600s，已中止补跑（R5c）")
    for line in (r.stdout or "").splitlines():
        log("  [deploy] %s" % line)
    if r.returncode != 0:
        for line in (r.stderr or "").splitlines():
            log("  [deploy-err] %s" % line)
        raise RuntimeError("deploy_predict 失败 exit=%s" % r.returncode)
    if not (os.path.exists(path) and os.path.getsize(path) > 0):
        raise RuntimeError("deploy_predict 后候选仍缺失: %s" % path)
    log("候选已生成: %s" % os.path.basename(path))
    return path


def write_live_review(codes, date, allow_restamp=False):
    """脚本化实时取数：data_sources.write_review_json（miniQMT+腾讯自动采集）。
    R5：--date 非今日且未带 --allow-restamp 时拒绝改签（防 look-ahead）。"""
    import data_sources as ds
    today = time.strftime("%Y%m%d")
    if date != today and not allow_restamp:
        raise RuntimeError("--date %s 非今日：实时数据改签历史日属 look-ahead，已拒绝（复现研究用 --allow-restamp）" % date)
    out = os.path.join(CACHE_DIR, "review_guard_%s.json" % date)
    ds.write_review_json(codes, out)
    if date != today:  # 仅 --allow-restamp 复现场景才允许改签
        with open(out, encoding="utf-8") as f:
            payload = json.load(f)
        payload["date"] = date
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    return out


def run_review_full(cand_path, review_path, date):
    """跑 review_full 生成当日完整版清单（R5c：600s 超时）。"""
    try:
        r = subprocess.run([sys.executable, "review_full.py",
                            "--candidates", cand_path,
                            "--review", review_path,
                            "--date", date],  # T-20261009-004：显式日期锚（restamp 复现场景 review JSON 日期为历史日，无此项会被 fail-closed 拒绝）
                           cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=600)
    except subprocess.TimeoutExpired:
        raise RuntimeError("review_full 超时 600s，已中止补跑（R5c）")
    for line in (r.stdout or "").splitlines():
        log("  [review] %s" % line)
    if r.returncode != 0:
        for line in (r.stderr or "").splitlines():
            log("  [review-err] %s" % line)
        raise RuntimeError("review_full 失败 exit=%s" % r.returncode)
    sel = os.path.join(SELECT_DIR, "%s_selection_full.csv" % date)
    if not (os.path.exists(sel) and os.path.getsize(sel) > 0):
        raise RuntimeError("review_full 后当日清单仍缺失: %s" % sel)
    return sel


def count_f3_missing(review_path, codes):
    """R1a：统计 review_guard json 中候选催化分缺失/为 0 的数量（review_full 已内置
    Tushare 业绩公告兜底，此处只做统计告警，不编造数据）。
    判定"缺失"= catalyst_score 为 None/NaN/0 且 catalyst_note 为空（0 分无注记=未采到催化）。"""
    if not os.path.exists(review_path):
        return len(codes)
    try:
        with open(review_path, encoding="utf-8") as f:
            payload = json.load(f)
        have = set()
        for s in payload.get("stocks", []):
            code = s.get("ts_code")
            sc = s.get("catalyst_score")
            note = s.get("catalyst_note") or ""
            if sc is None or (isinstance(sc, float) and sc != sc):  # None/NaN → 缺失
                continue
            if float(sc) == 0.0 and not note:  # 0 分且无注记 → 视为缺失
                continue
            have.add(code)
        return len([c for c in codes if c not in have])
    except Exception as e:
        log("  [F3统计] 读取 review_guard 失败: %s" % e)
        return len(codes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=time.strftime("%Y%m%d"))
    ap.add_argument("--no-deploy", action="store_true", help="候选缺失时不现场 deploy（仅告警）")
    ap.add_argument("--allow-restamp", action="store_true", help="复现研究专用：允许 --date 非今日改签（look-ahead 风险自担）")
    args = ap.parse_args()

    try:
        panel_date = latest_panel_date()
        log("面板最新日 D=%s，补跑目标日 date=%s" % (panel_date, args.date))

        # R5 新鲜度校验（告警不阻断）：面板最新日应 ≥ 目标日上一交易日
        exp = expected_panel_date(args.date)
        panel_stale = bool(exp) and panel_date < exp
        if panel_stale:
            log("  !! [新鲜度] 面板最新日 %s < 期望最近已收盘交易日 %s，候选可能陈旧（不阻断）" % (panel_date, exp))

        cand = ensure_candidate(panel_date, allow_deploy=not args.no_deploy)

        import pandas as pd
        df = pd.read_csv(cand, dtype={"ts_code": str})
        codes = [c.strip() for c in df["ts_code"].tolist() if c and str(c).strip()]
        if not codes:
            raise RuntimeError("候选为空: %s" % cand)
        log("候选 %d 只: %s" % (len(codes), ",".join(codes[:10])))

        review = write_live_review(codes, args.date, allow_restamp=args.allow_restamp)
        log("实时复核已写入: %s" % os.path.basename(review))

        sel = run_review_full(cand, review, args.date)
        log("当日清单已生成: %s" % os.path.basename(sel))

        # R1a F3 缺失统计告警
        f3_missing = count_f3_missing(review, codes)
        if f3_missing >= len(codes) and len(codes) > 0:
            log("  !! [F3缺失] %d/%d 候选无催化分（9:25 缓存未覆盖或 Tushare 业绩公告缺失）→ 总分可能低于红线 58，guard 可能安全阀不换仓（宁缺毋滥，可接受）" % (f3_missing, len(codes)))
        elif f3_missing > 0:
            log("  [F3缺失] %d/%d 候选无催化分" % (f3_missing, len(codes)))

        # 读清单首行确认非空/有票
        sdf = pd.read_csv(sel)
        n = len(sdf)
        ok = n > 0
        top = []
        if ok and "ts_code" in sdf.columns:
            for _, row in sdf.head(2).iterrows():
                top.append({"code": row.get("ts_code"), "total": row.get("total"),
                            "prob": row.get("model_prob")})
        print(json.dumps({"ok": ok, "date": args.date, "sel": sel,
                          "cand": len(codes), "rows": n, "top": top,
                          "f3_missing": f3_missing, "panel_stale": panel_stale,
                          "panel_date": panel_date}, ensure_ascii=False))
        return 0 if ok else 2
    except Exception as e:
        log("!! 补跑失败: %s" % e)
        print(json.dumps({"ok": False, "date": args.date, "error": str(e)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
