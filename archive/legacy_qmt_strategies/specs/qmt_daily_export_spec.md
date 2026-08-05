# QMT 每日交易数据导出脚本 — Task SPEC

> **版本**: v1.0
> **日期**: 2026-06-30
> **交付对象**: CC（Claude Code）
> **验收人**: Hermes

---

## 一、Objective（策略目标）

在 QMT 模拟端（账号 67014907）内运行一个 Python 脚本，每天收盘后自动导出三张数据表：

1. **委托成交明细** — 当日所有成交记录
2. **持仓明细** — 当前持仓盈亏
3. **资金账号情况** — 账户总览

输出为 **GBK编码的CSV文件**，写入网络共享目录 `D:\qmt_pool\`，供 Hermes 后续汇总统计。

---

## 二、Commands（开发指令）

### 2.1 文件位置

- 脚本文件: `D:\QMT_STRATEGIES\scripts\qmt_daily_export.py`
- 输出目录: `D:\qmt_pool\`
- 输出文件命名规则:
  - `成交明细_YYYYMMDD.csv`
  - `持仓明细_YYYYMMDD.csv`
  - `资金概况_YYYYMMDD.csv`

### 2.2 开发约束

- **Python 3.6.8 兼容**（QMT内置Python版本）
- **GBK编码**（QMT终端中文显示要求）
- 禁止使用 `dict[str, ...]`、`str | None`、`match-case` 等 3.9+/3.10+ 语法
- 用 `typing.Dict` / `typing.Optional` 或直接删注解
- 交付前必须 `validate_qmt_file.py` ALL PASS

### 2.3 运行方式

脚本在 QMT 策略中通过以下方式触发：

```python
# 在 strategy_main.py 的收盘处理段调用
import sys
sys.path.insert(0, r'D:\QMT_STRATEGIES\scripts')
from qmt_daily_export import export_daily_data
export_daily_data(ContextInfo)
```

或者在 QMT 的"公式研究"或"Python策略研究"中直接运行。

---

## 三、Structure（策略结构）

### 3.1 核心API

使用 QMT 内置的 `get_trade_detail_data()` API：

```python
# 成交明细
get_trade_detail_data(account_id, 'STOCK', 'deal')  # 当日成交

# 持仓明细
get_trade_detail_data(account_id, 'STOCK', 'position')  # 当前持仓

# 资金账号
get_trade_detail_data(account_id, 'STOCK', 'account')  # 账户资金
```

### 3.2 脚本结构

```
qmt_daily_export.py
├── ACCOUNT_ID = '67014907'
├── OUTPUT_DIR = r'\\192.168.31.131\qmt_pool'
├── export_daily_data(ContextInfo)
│   ├── 1. 导出成交明细 → 成交明细_YYYYMMDD.csv
│   │   ├── 列: 资金账号,成交时间,证券代码,证券名称,买卖标记,成交数量,成交价格,成交金额,手续费,成交编号,合同编号,任务编号,投资备注,账号备注,分支机构,投资备注1,订单编号,股东号
│   │   └── 数据源: get_trade_detail_data(ACCOUNT_ID, 'STOCK', 'deal')
│   │
│   ├── 2. 导出持仓明细 → 持仓明细_YYYYMMDD.csv
│   │   ├── 列: 资金账号,证券代码,证券名称,当前拥股,可用数量,冻结数量,成本价,最新价,持仓盈亏,盈亏比例,当日涨幅,市值,持仓成本,股东账号,市场名称,资产占比,市值占比,状态,分支机构,非流通股,当日盈亏
│   │   └── 数据源: get_trade_detail_data(ACCOUNT_ID, 'STOCK', 'position')
│   │
│   └── 3. 导出资金概况 → 资金概况_YYYYMMDD.csv
│       ├── 列: 资金账号,账号名称,账号备注,登录状态,操作,总资产,净资产,总负债,总市值,可用金额,冻结金额,持仓盈亏,手续费,可取金额,股票总市值,基金总市值,债券总市值,回购总市值,报撤单比,分支机构,资金余额,今日账号盈亏
│       └── 数据源: get_trade_detail_data(ACCOUNT_ID, 'STOCK', 'account')
│
└── if __name__ == '__main__':
    └── init(ContextInfo) 中调用 export_daily_data(ContextInfo)
```

### 3.3 字段映射说明

`get_trade_detail_data()` 返回的对象属性名与QMT终端列名的对应关系（参考QMT API文档）：

**成交明细（deal）对象属性：**
| CSV列名 | 对象属性 | 说明 |
|---------|---------|------|
| 资金账号 | `m_strAccountID` | |
| 成交时间 | `m_strTradeID` 或 `m_strTime` | 格式 HH:mm:ss |
| 证券代码 | `m_strStockCode` | 注意不含.SH/.SZ后缀 |
| 证券名称 | `m_strStockName` | |
| 买卖标记 | `m_strDirection` | "限价买入"/"限价卖出" |
| 成交数量 | `m_nVolume` | |
| 成交价格 | `m_dPrice` | |
| 成交金额 | `m_dBalance` | |
| 手续费 | `m_dFee` | |
| 成交编号 | `m_strTradeID` | |
| 合同编号 | `m_strOrderID` | |
| 任务编号 | `m_strTaskID` | |
| 投资备注 | `m_strInvestRemark` | |
| 账号备注 | `m_strAccountRemark` | |
| 分支机构 | `m_strBranch` | |
| 投资备注1 | `m_strInvestRemark1` | |
| 订单编号 | `m_strOrderID` | |
| 股东号 | `m_strStockHolderID` | |

**持仓明细（position）对象属性：**
| CSV列名 | 对象属性 | 说明 |
|---------|---------|------|
| 资金账号 | `m_strAccountID` | |
| 证券代码 | `m_strStockCode` | |
| 证券名称 | `m_strStockName` | |
| 当前拥股 | `m_nVolume` | |
| 可用数量 | `m_nCanUseVolume` | |
| 冻结数量 | `m_nFrozenVolume` | |
| 成本价 | `m_dOpenPrice` | |
| 最新价 | `m_dPrice` | |
| 持仓盈亏 | `m_dProfit` | |
| 盈亏比例 | `m_dProfitRate` | 百分比值 |
| 当日涨幅 | `m_dTodayProfitRate` | |
| 市值 | `m_dMarketValue` | |
| 持仓成本 | `m_dCost` | |
| 股东账号 | `m_strStockHolderID` | |
| 市场名称 | `m_strMarket` | |
| 资产占比 | `m_dAssetRatio` | |
| 市值占比 | `m_dMarketValueRatio` | |
| 状态 | `m_strStatus` | |
| 分支机构 | `m_strBranch` | |
| 非流通股 | `m_nNonTradeVolume` | |
| 当日盈亏 | `m_dTodayProfit` | |

**资金账号（account）对象属性：**
| CSV列名 | 对象属性 | 说明 |
|---------|---------|------|
| 资金账号 | `m_strAccountID` | |
| 账号名称 | `m_strAccountName` | |
| 账号备注 | `m_strAccountRemark` | |
| 登录状态 | `m_strLoginStatus` | |
| 操作 | `m_strOperation` | |
| 总资产 | `m_dTotalAssets` | |
| 净资产 | `m_dNetAssets` | |
| 总负债 | `m_dTotalDebt` | |
| 总市值 | `m_dTotalMarketValue` | |
| 可用金额 | `m_dAvailable` | |
| 冻结金额 | `m_dFrozen` | |
| 持仓盈亏 | `m_dProfit` | |
| 手续费 | `m_dFee` | |
| 可取金额 | `m_dWithdrawable` | |
| 股票总市值 | `m_dStockMarketValue` | |
| 基金总市值 | `m_dFundMarketValue` | |
| 债券总市值 | `m_dBondMarketValue` | |
| 回购总市值 | `m_dRepoMarketValue` | |
| 报撤单比 | `m_strOrderCancelRatio` | |
| 分支机构 | `m_strBranch` | |
| 资金余额 | `m_dCash` | |
| 今日账号盈亏 | `m_dTodayProfit` | |

> **注意**: 以上属性名基于QMT xtquant文档和社区经验。CC需要先写一个探针脚本打印所有可用属性来确认实际属性名，再写正式导出脚本。

---

## 四、Code Style（代码风格）

### 4.1 代码模板

```python
# coding=gbk
"""
QMT 每日交易数据导出脚本
每天收盘后导出：成交明细、持仓明细、资金概况
输出到 D:\qmt_pool\ 目录
"""

import os
import time
from datetime import datetime

ACCOUNT_ID = '67014907'
OUTPUT_DIR = r'\\192.168.31.131\qmt_pool'

def get_date_str():
    return datetime.now().strftime('%Y%m%d')

def safe_attr(obj, attr, default=''):
    """安全获取对象属性"""
    return str(getattr(obj, attr, default))

def export_deals(ContextInfo):
    """导出当日成交明细"""
    deals = get_trade_detail_data(ACCOUNT_ID, 'STOCK', 'deal')
    date_str = get_date_str()
    filepath = os.path.join(OUTPUT_DIR, '成交明细_%s.csv' % date_str)
    
    # 写CSV头
    header = '资金账号,成交时间,证券代码,证券名称,买卖标记,成交数量,成交价格,成交金额,手续费,成交编号,合同编号,任务编号,投资备注,账号备注,分支机构,投资备注1,订单编号,股东号'
    
    with open(filepath, 'w', encoding='gbk') as f:
        f.write(header + '\n')
        for deal in deals:
            row = ','.join([
                safe_attr(deal, 'm_strAccountID'),
                safe_attr(deal, 'm_strTime'),  # 或 m_strTradeID
                safe_attr(deal, 'm_strStockCode'),
                safe_attr(deal, 'm_strStockName'),
                safe_attr(deal, 'm_strDirection'),
                safe_attr(deal, 'm_nVolume'),
                safe_attr(deal, 'm_dPrice'),
                safe_attr(deal, 'm_dBalance'),
                safe_attr(deal, 'm_dFee'),
                safe_attr(deal, 'm_strTradeID'),
                safe_attr(deal, 'm_strOrderID'),
                safe_attr(deal, 'm_strTaskID'),
                safe_attr(deal, 'm_strInvestRemark'),
                safe_attr(deal, 'm_strAccountRemark'),
                safe_attr(deal, 'm_strBranch'),
                safe_attr(deal, 'm_strInvestRemark1'),
                safe_attr(deal, 'm_strOrderID'),
                safe_attr(deal, 'm_strStockHolderID'),
            ])
            f.write(row + '\n')
    
    return filepath

def export_positions(ContextInfo):
    """导出持仓明细"""
    # 类似结构，用 get_trade_detail_data(ACCOUNT_ID, 'STOCK', 'position')
    pass

def export_account(ContextInfo):
    """导出资金概况"""
    # 类似结构，用 get_trade_detail_data(ACCOUNT_ID, 'STOCK', 'account')
    pass

def export_daily_data(ContextInfo):
    """主入口：导出所有数据"""
    files = []
    files.append(export_deals(ContextInfo))
    files.append(export_positions(ContextInfo))
    files.append(export_account(ContextInfo))
    return files

# 独立运行入口
def init(ContextInfo):
    export_daily_data(ContextInfo)
```

### 4.2 关键要求

1. **必须先写探针脚本** — 打印 `dir(deal_obj)` 确认实际属性名，因为不同QMT版本属性名可能有差异
2. **数值格式化** — 金额保留2位小数，数量整数，百分比保留2位小数
3. **空值处理** — 空字段写空字符串，不要写 `None`
4. **CSV格式** — 逗号分隔，GBK编码，第一行为列名
5. **网络路径** — `D:\qmt_pool\` 需确保QMT运行环境能访问

---

## 五、Testing（回测验证）

### 5.1 探针脚本（必须先执行）

在写正式导出脚本前，先运行以下探针脚本确认API可用性和属性名：

```python
# coding=gbk
"""探针：打印get_trade_detail_data返回对象的属性和示例值"""
import os

ACCOUNT_ID = '67014907'
OUTPUT_DIR = r'D:\QMT_POOL'

def init(ContextInfo):
    lines = []
    
    for data_type in ['deal', 'position', 'account']:
        lines.append('=' * 60)
        lines.append('数据类型: %s' % data_type)
        lines.append('=' * 60)
        
        data = get_trade_detail_data(ACCOUNT_ID, 'STOCK', data_type)
        lines.append('返回数量: %d' % len(data))
        
        for i, obj in enumerate(data[:3]):  # 只打前3条
            lines.append('--- 记录 %d ---' % i)
            for attr in dir(obj):
                if not attr.startswith('_'):
                    try:
                        val = getattr(obj, attr)
                        lines.append('  %s = %s' % (attr, val))
                    except:
                        pass
    
    probe_path = os.path.join(OUTPUT_DIR, 'probe_output.txt')
    with open(probe_path, 'w', encoding='gbk') as f:
        f.write('\n'.join(lines))
    
    print('探针结果已写入: %s' % probe_path)
```

### 5.2 验收标准

1. ✅ 探针脚本能正常返回数据，属性名确认
2. ✅ 三张CSV文件生成到 `D:\qmt_pool\`
3. ✅ CSV文件GBK编码，Excel打开不乱码
4. ✅ 列名与用户提供的QMT终端截图完全一致
5. ✅ 数值格式正确（金额有小数、数量整数）
6. ✅ 当日无成交时成交明细表头正确、内容为空

---

## 六、Boundaries（边界约束）

### 6.1 不做的事

- ❌ 不修改策略逻辑
- ❌ 不涉及实盘交易
- ❌ 不做数据统计/分析（只导出原始数据）
- ❌ 不清理历史文件

### 6.2 注意事项

- QMT模拟端数据是测试账号67014907的数据
- 脚本应在 **收盘后（15:00之后）** 运行，确保数据完整
- 如果网络共享不可达，降级写入 `D:\QMT_POOL\`
- 成交明细只取**当日**数据（get_trade_detail_data的deal默认当日）
- 持仓明细取**当前**持仓（含当日已清仓但有盈亏记录的）
- 资金概况取**当前**账户状态
