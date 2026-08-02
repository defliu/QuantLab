# coding=utf-8
"""检查 QMT 委托状态 — 盘后运行

用法：python scripts/check_order_status.py
功能：读取 QMT 日志，解析委托/成交状态
"""
import os
import re
from datetime import datetime

LOG_DIR = r"E:\国金QMT交易端模拟\userdata\log"

def check_orders():
    """检查今日委托状态"""
    today = datetime.now().strftime("%Y%m%d")
    
    # 找到今日的 FormulaOutput 日志
    formula_log = os.path.join(LOG_DIR, "XtClient_FormulaOutput_%s.log" % today)
    if not os.path.exists(formula_log):
        print("今日无策略日志: %s" % formula_log)
        return
    
    with open(formula_log, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    
    # 筛选包含 test/委托/买入/卖出 的行
    keywords = ["[test]", "委托", "买入", "卖出", "passorder", "成交", "废单"]
    relevant = [l for l in lines if any(k in l for k in keywords)]
    
    print("=" * 60)
    print("QMT 委托状态检查 - %s" % today)
    print("=" * 60)
    
    if not relevant:
        print("今日无委托相关日志")
        return
    
    for l in relevant:
        # 提取时间戳和内容
        match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*output = (.+)', l)
        if match:
            time_str = match.group(1)
            content = match.group(2)
            print("[%s] %s" % (time_str, content[:120]))
    
    print("=" * 60)

if __name__ == "__main__":
    check_orders()
