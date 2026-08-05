# coding=utf-8
"""将 dev 版转为 GBK 生产版 strategy_mfic.py"""
import os
src = open('D:/QMT_STRATEGIES/research/multi_factor_ic/mfic_strategy/strategy_mfic_dev.py', 'r', encoding='utf-8').read()
# 移除 coding行，换成gbk
src = '# coding=gbk\n' + src.split('\n', 1)[1]
# 简化debug打印
src = src.replace('print("[mfic] ', 'print("[MF] ')
with open('D:/QMT_STRATEGIES/strategy_mfic.py', 'w', encoding='gbk') as f:
    f.write(src)
print('生产版生成:', os.path.getsize('D:/QMT_STRATEGIES/strategy_mfic.py'), 'bytes')
print('文件头:', open('D:/QMT_STRATEGIES/strategy_mfic.py', 'r', encoding='gbk').readline().strip())
