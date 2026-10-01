# coding: utf-8
"""G2 快照回放回归（T-20260919-002 三道防线·C，2026-09-20 立）。

低频回归（建议每周六 09:10 非交易日跑，或改动 deploy_predict_g2/评分卡/模型后手动跑）：
  1) 用真实快照重放 deploy_predict_g2.py 主流程（--date 缺省取快照最新交易日）
  2) 独立校验落盘 CSV：存在、列齐全(SC_F1~F6)、SC_F2/SC_F5 非坍缩、候选非空
  3) 任一不满足 → exit 1 + 飞书告警（--no-send 调试）

与 B 防线（check_scorecard_health.py）区别：
  B 监控「日常链路每日 20:05 落盘的最新 CSV」是否健康；
  C 是「重放回归」——验证 deploy_predict_g2 / 评分卡 / 模型改动后，
    链路仍能正常出健康候选（防改坏后仍静默落盘坏候选，FAIL-LOUD 断言是运行期内嵌，
    本脚本是回归期独立校验）。

用法：
  python test_g2_rerun.py [--date 2026-09-18] [--top 10] [--no-send]
退出码：0=PASS；1=FAIL（重放失败或输出不健康）
"""
import argparse
import os
import shutil
import subprocess
import sys

P16 = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
G2_PY = os.path.join(P16, "deploy_predict_g2.py")
PUSH = os.path.join(P16, "push_alert_card.py")
PY = sys.executable


def _push(title, body, level, no_send):
    cmd = [PY, PUSH, "--title", title, "--body", body, "--level", level]
    if no_send:
        cmd.append("--no-send")
    subprocess.run(cmd, cwd=P16, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _backup_baseline(path):
    """重放前备份原件 → <path>.rerun_base；返回备份前原件是否存在。（F7 修复配套，2026-09-28）"""
    existed = os.path.exists(path)
    if existed:
        shutil.copy2(path, path + ".rerun_base")
        print("[F7] 备份原件 %s -> %s" % (os.path.basename(path), os.path.basename(path) + ".rerun_base"))
    return existed


def _restore_baseline(items):
    """F7 修复（2026-09-28，候选可审计性）：重放会用同名路径覆写历史候选（本周六 09:10 定时任务
    默认重放快照最新交易日），校验完成后必须恢复原件，否则历史候选被回归测试污染、不可审计。
    - 原件存在过 → 从 .rerun_base 恢复
    - 原件本不存在（红线空仓日）而重放新建 → 删除重放产物"""
    for path, existed in items:
        try:
            backup = path + ".rerun_base"
            if existed:
                if os.path.exists(backup):
                    shutil.copy2(backup, path)
                    print("[F7] 恢复原件 %s（重放产物不落盘）" % os.path.basename(path))
                else:
                    print("!! [F7] %s 原件存在过但备份 %s 缺失，无法恢复——盘上是重放产物，审计时注意"
                          % (os.path.basename(path), os.path.basename(backup)))
            else:
                if os.path.exists(path):
                    os.remove(path)
                    print("[F7] 移除重放产物 %s（当日原为红线空仓，保持无候选状态）" % os.path.basename(path))
        except Exception as e:
            print("!! [F7] 恢复 %s 失败（盘上可能留有重放产物，审计时注意）: %s" % (os.path.basename(path), e))


def _safe_print(text):
    """重放 stdout 按 utf-8/replace 解码后可能含 \ufffd，在 GBK 控制台 print 会
    UnicodeEncodeError（2026-09-29 E2E 实测）——降级为可编码字符再打印，绝不让
    打印崩溃中断「重放→恢复」链路。"""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(enc, "replace").decode(enc))


def _check_csv(path, no_send):
    import pandas as pd
    if not os.path.exists(path):
        _push("G2 快照回放回归 FAIL", "重放后 %s 未落盘" % path, "error", no_send)
        print("!! %s 未落盘" % path)
        return False
    df = pd.read_csv(path)
    need = ["ts_code", "SC_F1", "SC_F2", "SC_F3", "SC_F4", "SC_F5", "SC_F6"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        _push("G2 快照回放回归 FAIL", "重放 CSV 缺列 %s（%s）" % (miss, os.path.basename(path)), "error", no_send)
        print("!! 缺列 %s" % miss)
        return False
    if len(df) == 0:
        _push("G2 快照回放回归 FAIL", "重放 CSV 候选为空（%s）" % os.path.basename(path), "error", no_send)
        print("!! 候选为空")
        return False
    bad = []
    if df["SC_F2"].nunique() <= 1:
        bad.append("SC_F2 坍缩")
    if df["SC_F5"].nunique() <= 1:
        bad.append("SC_F5 坍缩")
    if bad:
        _push("G2 快照回放回归 FAIL", "评分卡坍缩: %s（%s）" % ("/".join(bad), os.path.basename(path)), "error", no_send)
        print("!! %s" % "/".join(bad))
        return False
    print("PASS: %s 行数=%d SC_F2 nunique=%d SC_F5 nunique=%d" % (
        os.path.basename(path), len(df), df["SC_F2"].nunique(), df["SC_F5"].nunique()))
    return True


def main():
    ap = argparse.ArgumentParser(description="G2 快照回放回归（防线C）")
    ap.add_argument("--date", default=None, help="目标交易日，缺省取快照最新日")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--no-send", action="store_true", help="不推送告警（调试）")
    args = ap.parse_args()

    cmd = [PY, G2_PY]
    if args.date:
        cmd += ["--date", args.date]
    cmd += ["--top", str(args.top)]

    # 日期先于重放解析（F7 修复需要重放前备份原件；原实现重放后才定位产物路径，来不及备份）
    if args.date:
        date = args.date.replace("-", "")
    else:
        import pandas as pd
        snap = os.path.join(P16, "data_live", "g2_latest_features.parquet")
        day = pd.read_parquet(snap)
        date = str(pd.to_datetime(day["trade_date"]).max().date()).replace("-", "")
    csv = os.path.join(P16, "data", "selections", "g2", "%s_g2_top%d.csv" % (date, args.top))
    md = os.path.join(P16, "data", "selections", "g2", "%s_g2_selection.md" % date)
    base_items = [(csv, _backup_baseline(csv)), (md, _backup_baseline(md))]

    print("== 重放 deploy_predict_g2.py %s ..." % (args.date or "(快照最新日)"))
    # F7 修复加固（2026-09-29 复核）：原实现逐 return 路径内联 _restore_baseline，
    # 一旦 _replay/_check_csv 抛异常（典型：GBK 控制台 print 重放 stdout 触发
    # UnicodeEncodeError），恢复永不执行 → 重放产物留在盘上污染历史候选。
    # 改为 try/finally 兜底，与 compare_g2_daily.py / compare_v13_daily.py 同款。
    try:
        r = subprocess.run(cmd, cwd=P16, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        log = r.stdout.decode("utf-8", "replace")
        _safe_print(log[-3000:])
        if r.returncode != 0:
            # FAIL-LOUD 中止（防线D）或运行时异常
            _push("G2 快照回放回归 FAIL", "重放退出码 %d（%s）" % (r.returncode, (args.date or "快照最新日")), "error", args.no_send)
            return 1

        ok = _check_csv(csv, args.no_send)   # 校验基于重放产物，校验完由 finally 恢复原件
    finally:
        _restore_baseline(base_items)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
