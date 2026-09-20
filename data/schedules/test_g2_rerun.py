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

    print("== 重放 deploy_predict_g2.py %s ..." % (args.date or "(快照最新日)"))
    r = subprocess.run(cmd, cwd=P16, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log = r.stdout.decode("utf-8", "replace")
    print(log[-3000:])
    if r.returncode != 0:
        # FAIL-LOUD 中止（防线D）或运行时异常
        _push("G2 快照回放回归 FAIL", "重放退出码 %d（%s）" % (r.returncode, (args.date or "快照最新日")), "error", args.no_send)
        return 1

    # 落盘 CSV 路径（日期缺省时取快照最新交易日）
    if args.date:
        date = args.date.replace("-", "")
    else:
        import pandas as pd
        snap = os.path.join(P16, "data_live", "g2_latest_features.parquet")
        day = pd.read_parquet(snap)
        date = str(pd.to_datetime(day["trade_date"]).max().date()).replace("-", "")
    csv = os.path.join(P16, "data", "selections", "g2", "%s_g2_top%d.csv" % (date, args.top))
    return 0 if _check_csv(csv, args.no_send) else 1


if __name__ == "__main__":
    sys.exit(main())
