# -*- coding: utf-8 -*-
import os
import subprocess
import sys

CLI = r"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\1.0.5\bin\lark-cli.exe"
OPEN_ID = "ou_bd13444d8ea53c28249c669f43f3eeff"

text = """📊 集合竞价预判 · 2026-09-10（周四）G2
> 生成 10:45 ｜ G2 大QMT文件桥（70180771）

【数据链路】G2候选=夜间预生成20260909（跳过build/deploy）✅；F2主源=Tushare 09-08（滞后2天权威）；悟道早盘受限/TDX早盘不可达已降级

【G2换仓】今日无动作（NOOP）：持仓300475/001266建仓09-04仅5日未满10日不卖，持2=TOP_N不补买

【持仓预警 🟢正常】
· 300475 香农芯创 177.84 +1.90% 浮盈+4.5%｜F2: T-1 +5.5亿 / 当日+5840万 双源同向｜续强持有，防冲高回落
· 001266 宏英智能 32.82 -1.20% 浮亏-3.7%｜当日+24万｜基本面扎实持有；风控线31.69距现价3.4%，跌破警惕

【G2候选强弱（10:45）】
· 603256 宏和科技 +3.70%｜F2双源同正(+1.50亿/+3919万)+玻纤+1.60%｜最健康
· 603093 南华期货 +2.40%｜双源同正+期货+1.05%｜强
· 603663 三祥新材 +4.05%｜但T-1/当日主力双源净流出(价涨量出背离)，防冲高回落
· ⚠️300434 金石亚药 -3.86% 董事减持 → 规避

【V1.3候选】
· 600830 香溢融通 +2.41%（多元金融+1.11%）、002396 星网锐捷（当日+2134万通信主线）较强
· ⚠️601890 亚星锚链 -2.18% 量比14.7 当日主力-1.95亿（9/9涨停后高开低走兑现）
· ⚠️603207 小方制药 -4.23%、000949 新乡化纤 -4.90% → 规避

【板块】化学原料+3.67%｜国防军工+2.76%｜玻纤+1.60%｜PCB+1.71%；存储芯片-1.67%走弱
【大盘】科创50+0.41% 创业板+0.30% 上证-0.14%（10:45）｜涨停10家情绪中性

> 模型+多源实时研究信号，不构成投资建议"""

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
