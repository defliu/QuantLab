#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fix4 重放脚本：以 044755 (git 849e08d) 为基底，在内存中应用三块逻辑变更，
全程 GBK 编码处理，避免中文腐蚀。
"""
import re
from datetime import datetime

# 读取基底 (044755)，UTF-16-LE -> 去 BOM -> GBK 字符串
with open(r'D:\Temp\build_044755.py', 'rb') as f:
    raw = f.read()
base = raw.decode('utf-16-le')
if base.startswith('\ufeff'):
    base = base[1:]

# ============================================================
# 变更 1：_execute_buys_equalweight 插入 4a 仓位缩减块
# 定位：在 `spendable = min(virtual_cash, acct_cash)` 之后、`for code in target_codes:` 之前
# ============================================================
insert_4a = '''
    # \u4fee20260819: \u81ea\u52a8\u7f29\u51cf\u4ed3\u4f4d\u5f53\u8d44\u91d1\u4e0d\u8db3\u4ee5\u4e70\u5165\u6240\u6709\u76ee\u6807\u80a1\u5e38\uff0c\u6309\u6700\u5c0f1\u624b\u6210\u672c\u4ece\u4f4e\u5230\u9ad8\u9009\u62e9\u53ef\u4e70\u80a1\u6570\uff0c\u53ea\u4e70\u53ef\u4e70\u7684\u524dN\u80a1
    # 10\u4e07/100\u80a1=1000\u5143/\u80a1 \u2192 \u4e0d\u8db3\u4e701\u624b\u7684\u80a1\u5c31\u88ab\u5220\u9664\uff1d\u6309\u4ef7\u683c\u4ece\u4f4e\u5230\u9ad8\u62e9\u5e8f\uff0c\u53ea\u4fdd\u7559\u80fd\u4e70\u8d77\u7684\u524dN\u53ea
    min_lot_costs = []
    for code in target_codes:
        p = prices.get(code, 0)
        if p and p > 0:
            min_lot_costs.append((code, p * 100))
    min_lot_costs.sort(key=lambda x: x[1])
    affordable_n = 0
    remaining_cash = spendable
    for code, lot_cost in min_lot_costs:
        if lot_cost <= remaining_cash:
            remaining_cash -= lot_cost
            affordable_n += 1
        else:
            break
    if affordable_n < n_target:
        affordable_codes = [c for c, _ in min_lot_costs[:affordable_n]]
        print("[ATR_EW \u4ed3\u4f4d\u7f29\u51cf] \u8d44\u91d1\u671f%.0f\u53ea\u80fd\u4e70%d\u80a1(\u76ee\u6807%d\u80a1)\uff0c\u53ea\u53d6\u524d%d\u80a1"
              % (spendable, affordable_n, n_target, affordable_n))
        target_codes = affordable_codes
        n_target = max(len(target_codes), 1)
        target_value = nav / n_target
        spendable = min(virtual_cash, acct_cash)
'''

# 找到插入点
# 兼容 \r\n 和 \n
pattern_4a = r'(spendable = min\(virtual_cash, acct_cash\)\r?\n)'
match_4a = re.search(pattern_4a, base)
if not match_4a:
    raise ValueError('未找到 4a 插入点')
insert_pos = match_4a.end()
base = base[:insert_pos] + insert_4a + base[insert_pos:]

# ============================================================
# 变更 2：_get_account_cash 替换为三级 fallback 版本 (4c)
# 定位：整个函数体替换
# ============================================================
new_get_account_cash = '''def _get_account_cash(C):
    """\u83b7\u53d6\u8d26\u6237\u53ef\u7528\u8d44\u91d1\u4e09\u7ea7 fallback\uff1a
    1) C.get_account_info() (QMT\u5b98\u65b9\u63a5\u53e3\u4f18\u5148)
    2) get_trade_detail_data \u67e5\u8be2\u8d26\u6237\u5f85\u5355\u5f62\u5f0f (QMT\u65b0\u7248\u672c\u5176\u4ed6\u65b9\u5f0f\u5931\u6548)
    3) C.get_cash() (\u5176\u4ed6QMT\u7248\u672c)
    \u5168\u90e8\u5931\u8d25\u65f6\u8fd4\u56de 1e18 \u4f5c\u4e3a \u65e0\u7a7a\u5931\u8d44\u91d1\u5141\u5e95
    """
    # \u7b2c1\u5c42: C.get_account_info() (QMT\u5b98\u65b9\u63a5\u53e3\u4f18\u5148)
    try:
        info = C.get_account_info()
        if isinstance(info, dict):
            c = info.get('cash')
            if c is None:
                c = info.get('available_cash')
            if c is not None:
                return float(c)
        c = getattr(info, 'cash', None)
        if c is None:
            c = getattr(info, 'available_cash', None)
        if c is not None:
            return float(c)
    except Exception:
        pass
    # \u7b2c2\u5c42: get_trade_detail_data \u67e5\u8be2\u8d26\u6237\u5f85\u5355\u5f62\u5f0f (QMT\u65b0\u7248\u672c\u5176\u4ed6\u65b9\u5f0f\u5931\u6548)
    try:
        f_get = globals().get('get_trade_detail_data')
        if f_get is None:
            f_get = getattr(C, 'get_trade_detail_data', None)
        if f_get is not None:
            accts = f_get(_ACCOUNT_ID, 'stock', 'account')
            if accts:
                for a in accts:
                    avail = getattr(a, 'm_dAvailable', None)
                    if avail is not None:
                        return float(avail)
                    avail = getattr(a, 'm_dCash', None)
                    if avail is not None:
                        return float(avail)
    except Exception:
        pass
    # \u7b2c3\u5c42: C.get_cash() (\u5176\u4ed6QMT\u7248\u672c)
    try:
        c = C.get_cash()
        if isinstance(c, dict):
            avail = c.get('m_dAvailable', c.get('available', None))
            if avail is not None:
                return float(avail)
        elif c is not None:
            return float(c)
    except Exception:
        pass
    print("[ATR_EW] \u83b7\u53d6\u8d26\u6237\u53ef\u7528\u8d44\u91d1\u5168\u90e8\u5931\u8d25(\u5176\u4ed6\u5355\u4f4d): \u5df2\u7528\u5927\u503f\u503c")
    return 1e18'''

# 替换整个函数 (兼容 \r\n)
pattern_4c = r'(def _get_account_cash\(C\):.*?return 1e18\r?\n\r?\n)'
base = re.sub(pattern_4c, new_get_account_cash + '\r\n', base, flags=re.DOTALL)

# ============================================================
# 变更 3：_main_loop 中的 4b 死循环修复
# 基底已有 if _g_my_codes: -> 改为 if selected:
# ============================================================
# 直接搜索并替换关键行 (兼容 \r\n)
base = base.replace(
    'if _g_my_codes:\r\n                _g_last_rebalance_key = key',
    'if selected:\r\n                _g_last_rebalance_key = key'
)

# ============================================================
# 变更 4：BUILD_TAG 更新
# ============================================================
now_tag = datetime.now().strftime('%Y%m%d-%H%M%S')
base = re.sub(r'BUILD_TAG = "20260819-044755"', f'BUILD_TAG = "{now_tag}"', base)

# 写入新文件 (GBK 编码)
output_path = r'D:\QuantLab\projects\Project_ATR_lowvol\build\strategy_atr_lowvol_equalweight.py'
with open(output_path, 'wb') as f:
    f.write(base.encode('gbk'))

print(f'构建完成: {output_path}')
print(f'BUILD_TAG: {now_tag}')
print(f'文件大小: {len(base.encode("gbk"))} bytes')

# 关键字符串检查
required = ['沪深A股', '风险警示板', '买入', '卖出', 'ATR_EW', '止损', '止盈', '移动止盈', '卖出冷却']
for s in required:
    if s not in base:
        print(f'❌ 缺失关键字符串: {s}')
    else:
        print(f'✅ 包含: {s}')

q_count = base.count('?')
print(f'问号总数: {q_count} (要求 < 10)')
if q_count >= 10:
    print('❌ 疑似编码腐蚀')
else:
    print('✅ 编码完好')