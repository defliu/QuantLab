# -*- coding: utf-8 -*-
"""_audit_version_flag 的针对性回归测试。

覆盖两个已实测的真实缺陷：

1. _flag_order_key 的缺失值计数曾把字面 '<NA>' 当成可解析值 ->
   返回 string 模式 -> D-2 无条件 float64 转换时抛
   "could not convert string to float: '<NA>'"，而侧级异常又只打印
   traceback 就继续，末尾照样 DONE、退出码 0（伪装成完整成功）。

2. fina_indicator 的 update_flag 是 str dtype 且混着 '1' / '1.0'。
   按原始字符串 factorize 会把 '1' vs '1.0' 当成「flag 有别」，
   凭空造出假版本对；转数值后两者又同为 1.0，于是 flagmax 把两行
   都判成最大版本 -> 假「最大 flag 不唯一」，裁决规则自相矛盾。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _audit_version_flag import _flag_array, _flag_order_key  # noqa: E402

FAILED = []


def check(name, cond, detail=''):
    if cond:
        print('  ok   %s' % name)
    else:
        print('  FAIL %s  %s' % (name, detail))
        FAILED.append(name)


class FakeSeries(object):
    """最小 stub：只提供 _flag_array 需要的 .map(...).to_numpy()。"""

    def __init__(self, values):
        self.values = values

    def map(self, fn):
        return FakeSeries([fn(v) for v in self.values])

    def to_numpy(self, dtype=None):
        return np.array(self.values, dtype=dtype or object)


def norm(values):
    return _flag_array(FakeSeries(values))


print('== 缺陷1：缺失 flag 不得让排序键退回字符串模式 ==')
flag = norm(['0', '1', np.nan, None, float('nan')])
key, canon, n_unparsed = _flag_order_key(flag)
check('全 NaN flag 不产生无法解析计数',
      n_unparsed == 0, 'n_unparsed=%d' % n_unparsed)
check('排序键是 float64（可直接比较大小）',
      key.dtype == np.float64, 'dtype=%s' % key.dtype)
check('缺失 flag 的排序键是 NaN',
      bool(np.isnan(key[2])) and bool(np.isnan(key[3])))
check('缺失 flag 的规范字符串是 NA 哨兵',
      canon[2] == '<NA>' and canon[3] == '<NA>',
      'canon=%r' % (canon,))
check('非缺失 flag 规范化为数值文本',
      canon[0] == '0' and canon[1] == '1', 'canon=%r' % (canon,))

print('== 缺陷1b：真正的非数字 flag 必须被显式计数 ==')
key2, canon2, n_unparsed2 = _flag_order_key(norm(['0', 'X']))
check('非数字 flag 被计入 n_unparsed',
      n_unparsed2 == 1, 'n_unparsed=%d' % n_unparsed2)
check('非数字 flag 的排序键为 NaN（不参与大小比较）',
      bool(np.isnan(key2[1])))

print('== 缺陷2：\'1\' 与 \'1.0\' 必须是同一个 flag ==')
key3, canon3, _ = _flag_order_key(norm(['1', '1.0', '0', '0.0']))
check('canon 归一为同一文本',
      canon3[0] == canon3[1] == '1' and canon3[2] == canon3[3] == '0',
      'canon=%r' % (canon3,))
check('排序键数值相等（flagmax 不会双命中）',
      key3[0] == key3[1] and key3[2] == key3[3],
      'key=%r' % (key3,))

print('== 回归：全 flag 缺失的组无法用 flag 定序 ==')
key4, canon4, _ = _flag_order_key(norm([np.nan, np.nan]))
mx = np.nanmax(key4) if not np.all(np.isnan(key4)) else np.nan
check('全缺失时 max 无定义（调用方必须靠 ~isnan(mx) 兜住）',
      bool(np.isnan(mx)), 'mx=%r' % mx)

print('== 回归：整数值不带 .0 ==')
_, canon5, _ = _flag_order_key(norm([1, 1.0, 0, 0.0, np.nan]))
check('int/float 混合归一为整数文本',
      list(canon5[:4]) == ['1', '1', '0', '0'], 'canon=%r' % (canon5,))

print()
if FAILED:
    print('FAILED=%d: %s' % (len(FAILED), ','.join(FAILED)))
    sys.exit(1)
print('ALL OK')