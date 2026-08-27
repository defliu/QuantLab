# coding: utf-8
"""清仓结果飞书通知观察器：等待开盘清仓完成后读取成交结果，并通过飞书通知刘诚。"""
import csv
import os
import subprocess
import time
from datetime import datetime

PROJECT_DIR = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
CLEAR_LOG = os.path.join(PROJECT_DIR, "data_live", "qmt_clear_atopen_20260821.log")
TRADE_LOG = os.path.join(PROJECT_DIR, "data", "qmt_trade_log.csv")
NOTIFY_LOG = os.path.join(PROJECT_DIR, "data_live", "notify_result_20260821.log")
OPEN_ID = "ou_34f40d438c894faaebd9904c906d19c3"
TODAY = "2026-08-21"
NOTIFY_TIME = "2026-08-21 09:42:00"
SELL_CODES = ["600180.SH", "600308.SH", "600528.SH", "600582.SH",
              "601311.SH", "601390.SH", "601800.SH", "000726.SZ", "002375.SZ"]


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} {msg}"
    print(line, flush=True)
    with open(NOTIFY_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    log("[NOTIFY] 启动观察器，等待清仓完成...")
    target = datetime.strptime(NOTIFY_TIME, "%Y-%m-%d %H:%M:%S")
    wait = (target - datetime.now()).total_seconds()
    if wait < 0:
        wait = 0
    log(f"[NOTIFY] 距通知检查还有约 {int(wait)} 秒")
    time.sleep(wait)

    # 解析成交日志（TRADE_LOG CSV，utf-8-sig）
    rows = []
    if os.path.exists(TRADE_LOG):
        with open(TRADE_LOG, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                t = (r.get("time") or "").strip().strip('"')
                code = (r.get("code") or "").strip().strip('"')
                side = (r.get("side") or "").strip().strip('"')
                vol = (r.get("vol") or "").strip().strip('"')
                oid = (r.get("order_id") or "").strip().strip('"')
                if t.startswith(TODAY) and side == "SELL" and code in SELL_CODES:
                    rows.append({"code": code, "vol": vol, "oid": oid})
    log(f"[NOTIFY] 今日卖出委托记录数: {len(rows)}")

    # 解析清仓日志中的关键状态
    log_text = ""
    if os.path.exists(CLEAR_LOG):
        with open(CLEAR_LOG, "r", encoding="utf-8") as f:
            log_text = f.read()

    # 判定结果
    success = []
    failed = []
    for c in SELL_CODES:
        r = next((x for x in rows if x["code"] == c), None)
        if r:
            try:
                ok = int(r["oid"]) > 0
            except Exception:
                ok = False
            if ok:
                success.append(r)
            else:
                failed.append(r)
        else:
            failed.append({"code": c, "vol": "-", "oid": "无委托"})

    if success and not failed:
        status = "✅ 清仓全部成功"
    elif success and failed:
        status = "⚠️ 清仓部分成功"
    elif not success:
        status = "❌ 清仓未执行或失败"
    else:
        status = "ℹ️ 清仓结果"

    # 组装 Markdown 消息
    md = "**📋 模拟盘清仓结果通知**\n\n"
    md += f"日期：{TODAY}\n"
    md += f"状态：{status}\n"
    md += "保留：001378.SZ、002912.SZ\n\n"
    md += "**卖出委托明细**\n"
    md += "| 代码 | 数量 | 委托号 | 结果 |\n"
    md += "| --- | --- | --- | --- |\n"
    for c in SELL_CODES:
        r = next((x for x in rows if x["code"] == c), None)
        if r:
            try:
                ok = int(r["oid"]) > 0
            except Exception:
                ok = False
            res = "✅ 成功" if ok else "❌ 失败"
            md += f"| {r['code']} | {r['vol']} | {r['oid']} | {res} |\n"
        else:
            md += f"| {c} | - | 无委托 | ❌ 未成交 |\n"
    md += "\n_本通知由 TraeWork 自动发送（模拟盘）_"

    if "连接失败" in log_text:
        err_lines = [ln.strip() for ln in log_text.splitlines() if "连接失败" in ln]
        md += "\n\n⚠️ 检测到连接异常：" + "；".join(err_lines)

    log(f"[NOTIFY] 准备发送飞书通知 -> openId {OPEN_ID}")
    log(f"[NOTIFY] 内容摘要: {status} (成功 {len(success)}/共 {len(SELL_CODES)})")

    try:
        out = subprocess.run(
            ["lark-cli", "im", "+messages-send", "--user-id", OPEN_ID,
             "--markdown", md, "--as", "user"],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        log(f"[NOTIFY] lark-cli 返回码: {out.returncode}")
        log(f"[NOTIFY] lark-cli stdout: {out.stdout.strip()}")
        if out.stderr:
            log(f"[NOTIFY] lark-cli stderr: {out.stderr.strip()}")
    except Exception as e:
        log(f"[NOTIFY] 发送异常: {e}")
    log("[NOTIFY] 完成。")


if __name__ == "__main__":
    main()
