# -*- coding: utf-8 -*-
import os
import subprocess
import sys

CLI = r"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\1.0.5\bin\lark-cli.exe"
OPEN_ID = "ou_bd13444d8ea53c28249c669f43f3eeff"

text = """Project_16 每日换仓结果 · 2026-09-10（LIVE，账号67014907）

[大盘] 沪深300 -0.38%（腾讯 09:58）→ T=2 正常档
[F2 主源=Tushare T-1 本地] moneyflow 2026-09-08（滞后2天≤2权威，与训练口径零skew）

✅ 买入 1 笔：
· 601058 赛轮轮胎 3100股 @14.48 = 44,888元 FILLED（65.0分 Top1；9/9埃及建厂潮催化）
  （首次限价14.45低于卖盘5次超时撤单 → 10:07刷新价14.48重试成交，无遗留挂单）

🚫 未买 1 只：
· 600551 时代出版（64.0分 Top2）：卖出后仍持1只(601579未满5交易日到期保留)≥目标2 → 新增买入名额仅1只，只买最高分（T-20260907-003防超买）

❌ 卖出 0 笔：
· 601579.SH 掉出top2但持有3交易日<HOLD_DAYS=5 → 到期制保留不卖

[持仓] 601579 会稽山1900股 + 601058 赛轮轮胎3100股（今日买入T+1锁定）
[资金池] 95,898元，策略占用≈90,751元，未动用账户全量资金（账户可用10,045,123）
[委托守护] 买601058.SH:FILLED；异常：无，成交/撤单状态已全部回写

> 本结果为策略信号执行记录，不构成投资建议"""

env = dict(os.environ)
env.pop("LARKSUITE_CLI_APP_ID", None)
env.pop("LARKSUITE_CLI_USER_ACCESS_TOKEN", None)
env["LARKSUITE_CLI_STRICT_MODE"] = "off"
env.pop("HERMES_HOME", None)
env.pop("OPENCLAW_HOME", None)
env.pop("LARK_CHANNEL", None)
env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"

cmd = [CLI, "im", "+messages-send", "--user-id", OPEN_ID, "--text", text, "--as", "bot"]
try:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    print("RETURNCODE:", r.returncode)
    print("STDOUT:", r.stdout.strip()[:2000])
    if r.stderr:
        print("STDERR:", r.stderr.strip()[:2000])
    sys.exit(0 if r.returncode == 0 else 1)
except Exception as e:
    print("LARK_PUSH_FAIL: %r" % e)
    sys.exit(1)
