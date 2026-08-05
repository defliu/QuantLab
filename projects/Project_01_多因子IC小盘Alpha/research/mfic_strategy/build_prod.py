# coding=utf-8
"""将 dev 版转为 GBK 生产版 strategy_mfic.py（读本地 src，部署到 E:/QuantLab 子项目 build/）"""
import os
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(_THIS_DIR, 'strategy_mfic_dev.py'), 'r', encoding='utf-8').read()
# 移除 coding行，换成gbk
src = '# coding=gbk\n' + src.split('\n', 1)[1]
# 简化debug打印
src = src.replace('print("[mfic] ', 'print("[MF] ')
out_path = 'E:/QuantLab/projects/Project_01_多因子IC小盘Alpha/build/strategy_mfic.py'
os.makedirs(os.path.dirname(out_path), exist_ok=True)
with open(out_path, 'w', encoding='gbk') as f:
    f.write(src)
print('生产版生成:', os.path.getsize(out_path), 'bytes')
print('文件头:', open(out_path, 'r', encoding='gbk').readline().strip())
