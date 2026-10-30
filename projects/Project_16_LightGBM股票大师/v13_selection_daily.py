# coding: utf-8
"""P16 V1.3 每日候选清单生成（纯脚本，不依赖任何 agent 会话）。

**为什么需要**：原本这一步由 Qoder/DE 的 agent 任务做（09:38），2026-10-30 实测四条
Qoder 自动化全部 `session-failed`（8–21 秒即挂）⇒ 当天没有 selection_full ⇒ V13 计划 0 腿。
关键路径不能挂在会挂的东西上，故改成 Windows 计划任务 + 纯脚本。

链路（全部确定性、无 LLM）：
  1) 交易日判定；
  2) 数据日 D = feature_panel_v3 的 trade_date 最大值；缺 D_model_top10.csv 则先跑 deploy_predict；
  3) review_full.py --candidates ... --date <今日>
     —— F1/F4 用面板代理分、F2 用本地 Tushare moneyflow T-1、F5 用申万行业自算、
        F6 用 Tushare daily_basic、F3 缺失时回退面板 SC_F3（与回测同口径）；
     —— 行情走 data_sources 的腾讯兜底（xtquant 在 2.1.33 上不可用），不依赖 MCP；
  4) 校验产物行数 > 0；失败则推告警并返回非 0（次日 09:45 的计划会是 0 腿，但绝不伪造）。

用法：
  python v13_selection_daily.py            # 正常
  python v13_selection_daily.py --no-push  # 不推飞书（调试用）
退出码：0 正常 / 2 非交易日 / 3 候选生成失败 / 4 清单生成失败
"""
from __future__ import print_function

import os
import subprocess
import sys
import time
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PY = sys.executable
SEL = os.path.join(HERE, "data", "selections")


def _log(msg):
    print("[V13SEL] " + str(msg))
    sys.stdout.flush()


def _today():
    return time.strftime("%Y%m%d")


def _is_trade_day():
    try:
        import is_trade_day as ITD
        cal = ITD.load_calendar()
        r = ITD.is_trade_day(datetime.now().date(), cal)
        return bool(r.get("is_trade_day")) if isinstance(r, dict) else bool(r)
    except Exception as e:
        _log("WARN 交易日判定失败(%s)，按交易日继续" % (str(e)[:80],))
        return True


def _panel_date():
    try:
        import pandas as pd
        df = pd.read_parquet(os.path.join(HERE, "data", "feature_panel_v3.parquet"),
                             columns=["trade_date"])
        return pd.to_datetime(df["trade_date"]).max().strftime("%Y%m%d")
    except Exception as e:
        _log("FAIL 读面板失败: %s" % (str(e)[:100],))
        return None


def _push(title, body, level="warn"):
    try:
        env = dict(os.environ)
        env["LARK_SRC_TAG"] = "[QD]"
        subprocess.call([PY, os.path.join(HERE, "push_alert_card.py"),
                         "--title", title, "--body", body, "--level", level],
                        cwd=HERE, env=env, timeout=60)
    except Exception as e:
        _log("PUSH-FAIL %s" % (str(e)[:100],))


def main():
    no_push = "--no-push" in sys.argv
    if not _is_trade_day():
        _log("非交易日，跳过")
        return 2

    D = _panel_date()
    if not D:
        return 3
    _log("面板数据日 D=%s" % D)

    cand = os.path.join(SEL, "%s_model_top10.csv" % D)
    if not os.path.exists(cand):
        _log("候选缺失，跑 deploy_predict ...")
        rc = subprocess.call([PY, os.path.join(HERE, "deploy_predict.py"),
                              "--model", "v3", "--top-k", "10"], cwd=HERE)
        if rc != 0 or not os.path.exists(cand):
            _log("FAIL deploy_predict rc=%d" % rc)
            if not no_push:
                _push("V1.3 候选生成失败", "deploy_predict 失败（rc=%d），次日 09:45 计划将为空。" % rc)
            return 3
    _log("候选就绪: %s" % os.path.basename(cand))

    # 关键一步：生成当日完整清单（--date 显式传今日，绕开旧复核 JSON 的 fail-closed）
    rc = subprocess.call([PY, os.path.join(HERE, "review_full.py"),
                          "--candidates", cand, "--date", _today()], cwd=HERE)
    out = os.path.join(SEL, "%s_selection_full.csv" % _today())
    if rc != 0 or not os.path.exists(out):
        _log("FAIL review_full rc=%d（产物缺失）" % rc)
        if not no_push:
            _push("V1.3 清单生成失败",
                  "review_full 失败（rc=%d），次日 09:45 计划将为空。排查：行情源/候选文件/红线。" % rc)
        return 4

    try:
        import pandas as pd
        n = len(pd.read_csv(out, dtype={"ts_code": str}))
    except Exception:
        n = -1
    _log("OK 清单 %d 行 -> %s" % (n, out))
    if n <= 0:
        if not no_push:
            _push("V1.3 清单为空", "selection_full 行数为 0，次日计划将为空。")
        return 4

    if not no_push:
        try:
            import pandas as pd
            d = pd.read_csv(out, dtype={"ts_code": str})
            top = d.sort_values("total", ascending=False).head(3)
            passed = int((d["total"] >= 58).sum())
            body = "候选 %d 只，过红线(58) %d 只。前三：%s" % (
                n, passed, " / ".join("%s %.1f" % (r.ts_code, r.total) for r in top.itertuples()))
            _push("V1.3 候选清单已生成", body, "info")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
