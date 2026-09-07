# API 参考 · 配置系统与券商接口

> 配置层回答「参数从哪来、怎么校验」；券商接口层回答「怎么下单才不出事故」。
> ⚠️ 下单相关改动务必先读 `broker/QMT委托买卖防坑指南.md`。

---

## 一、配置系统：三级分层

| 层级 | 文件 | 作用域 |
| --- | --- | --- |
| 全局 | `config/settings.yaml` | 数据路径、回测默认区间/成本、日志、因子预处理 |
| 策略 | `config/<strategy>_config.yaml` | 单个策略的参数（如 `atr_lowvol_equalweight_config.yaml`、`value_smallcap_v2_config.yaml`） |
| 资金分配 | `config/capital_allocation.yaml` | **单一事实源**：各策略锁定本金与账户总额约束 |

`config/data_source_keys.json` 登记外部数据源的 key 与状态（悟道 / caihui 等）。

### settings.yaml 关键片段

```yaml
data:
  source: "astock"
  astock:
    daily_path: "D:/astock/daily/stock_daily.parquet"
    finance_path: "D:/astock/finance"
    basic_path: "D:/astock/basic"
backtest:
  start_date: "2018-07-01"
  end_date: "2026-06-30"
  initial_capital: 500000
  commission: 0.00025
  slippage: 0.002
  benchmark: "000300.SH"
factors:
  neutralize: true
```

---

## 二、资金分配：`capital_allocation.yaml`

```yaml
account:
  id: "70180771"
  broker: "国金QMT模拟端"
  total_capital: 10000000        # 账户实际总资产（元）
strategies:
  - key: atr_lowvol_equalweight
    name: "ATR低波等权不杠杆(8只集中)"
    capital_base: 100000
    max_hold: 8
    config_file: "config/atr_lowvol_equalweight_config.yaml"
```

当前在册（2026-09）：`atr_lowvol_equalweight` 10万/8只、`value_smallcap_v2` 10万/80只、`huang529_breakout` 10万/12只、`g2_bridge` 10万/2只，合计 40 万 ≤ 账户 1000 万。

硬规则：

1. **Σ 各策略 `capital_base` ≤ 账户实际总资产**；
2. 改分配必须跑 `python scripts/check_capital_allocation.py`，**退出码 0 才允许部署**；校验器会同时核对每个策略独立 config 里的 `capital_base` 与此表是否一致；
3. 每个策略只能动自己 ledger 的票和自己的额度，**绝不抢占他人资金、绝不纳管/卖出他人持仓**；
4. 共享账户无法物理阻止别策略花掉你的额度，真隔离需开子账户/多账号。

---

## 三、QMT 策略生成器（`broker/qmt_builder.py`）

```python
from broker.qmt_builder import build_qmt_strategy, save_strategy
code = build_qmt_strategy(config)
save_strategy(code, output_path)
```

| 函数 | 说明 |
| --- | --- |
| `load_config()` | 读取构建配置 |
| `build_qmt_strategy(config)` | 把模块化研究代码拼成 QMT 单文件 |
| `save_strategy(source_code, output_path)` | 以 **GBK** 编码写出 |
| `_normalize(series, reverse=False)` | 因子归一化（可反向，用于「越小越好」的因子） |
| `_compute_vwap_corr(close_arr, vol_arr, amt_arr)` | VWAP 量价相关因子计算 |

产物生命周期固定为 `init(C)` → `handlebar(C)` → `exit(C)`。

### 产物红线（缺一即视为未完成）

1. 首行 `# coding=gbk`，文件编码 GBK；
2. 内嵌 `BUILD_TAG`（`YYYYmmdd-HHMMSS`），每次构建自动替换，并在 `init(C)` 与日终日志输出，用于核对「本地构建版本 = 模拟盘实际运行版本」；
3. 兼容 Python 3.6.8：**禁用** f-string、`dict[str,...]`、`list[str]`、`str | None`、`:=`、`match/case`；
4. `passorder()` 是**全局函数**，不是 `C.passorder()`；
5. 账号硬编码且严格对应：`67014907`（miniQMT / Project_16）、`70180771`（QMT 端 / ATR低波·价值小盘V2）——**引用错号会废单**；
6. `C.get_market_data_ex` 缺财务字段，PE/PB/circ_mv 必须来自预生成 CSV；QMT 内置 Python 无 pyarrow，**不能读 parquet**；
7. `circ_mv` 单位是**万元**（30 亿 = 300000）；
8. 生产版不得混入 mock 或测试代码。

---

## 四、防坑版下单（`broker/qmt_order.py`）

**禁止裸调 `passorder`，一律复用本模块。**

```python
from broker.qmt_order import QmtOrderExecutor, buy, sell

ex = QmtOrderExecutor(C, account_id, safemode=False, pending_timeout=30.0)
ex.send_limit_order(side, code, price, volume)
ex.send_market_order(side, code, volume, price_type=PRTYPE_LATEST)
ex.lookup_order(code, side)
ex.register_pending(code, volume, side)
ex.check_pending_orders(on_confirm, on_rollback)

OrderResult(status, order_id=None, note="").ok()   # 结果对象
```

便捷函数：`buy(C, account_id, code, price, volume, safemode=False)` / `sell(...)`。

### 实盘执行红线

- `passorder()` 是**异步接口**，正常返回 0/None，**不是订单号**；真实订单号必须反查 `get_trade_detail_data(acct, 'STOCK', 'order')`；
- 委托后即时反查会撞 QMT 约 100ms 的 order_id 分配延迟，**必须短轮询**（`*_LOOKUP_RETRIES` / `INTERVAL`）等待，反查失败会静默断链；
- 订单反查过滤：remark 只能作候选优先级信号，**不能硬过滤**；硬条件只留 code / volume / status / direction / time 五条 AND，唯一候选即使 remark 空也返回；
- 判订单方向用 `m_strOptName`（含中文「买入」/「卖出」），不要用未实测的 `m_nDirection`；
- **撤单正确姿势是 `cancel(orderId, acct, 'stock', C)`**，配合 `can_cancel_order(orderId, acct, 'stock')` 预检，`orderId = m_strOrderSysID`（柜台合同号）。`passorder` 的 opType 枚举**没有撤单值**（24 是股票卖出，用它假装撤单会被当卖出拒单）；`cancel()` 返回 True 仅表示指令送达，需轮询确认终态；
- 订单号提取：`m_nOrderID` 优先（真实 QMT 平台委托号），`m_strOrderSysID` / ref 兜底；真实成交量字段是 `m_nVolumeTraded`（不是 `m_nDealVolume`）；
- 状态码：**55（部成）是活跃态，不是终态**；终态排除集含 53/54/57；
- 代码格式：QMT 要「数字在前」如 `600522.SH`，`SH.600522` 会被判 `orderCode 不合法`；
- 策略时间必须用 `C.get_current_time()`，**不能读 `datetime.now()`**（CMOS 时钟可能错乱）；相对计时用 `time.time()`；
- 收盘 15:00 后 handlebar 不再触发，>15:00 的收盘任务永不执行；「启动时执行一次」的任务放 `init` 末尾并用标志防重复；
- 模拟端 `get_trade_detail_data` 只保留当日 deal/order，隔日查不到 → **每日导出 CSV 到 `D:/QMT_POOL/` 是刚需**；
- 卖出风控评估前必须同步账户全量持仓纳管（**孤儿持仓**＝账户有票但持仓文件没记录 → 止损/止盈永不触发）；
- **买入 pending 超时必须重试**：超时从 30 秒延长至 300 秒，最多重试 3 次（撤单→重新 passorder→更新 pending），约 15 分钟仍不成交才彻底放弃；禁止「30 秒超时即撤单删 pending、永不补单」；
- 持票账本必须内嵌 `account_id` 戳；加载时缺失或不匹配 → 自动备份旧档为 `.bak_acct_<旧戳>_<时间戳>` 并空仓起步（fail-safe）。

---

## 五、本地验证适配器（`broker/local_context.py`）

```python
from broker.local_context import LocalContext
```

把 `C.*` 调用映射到本地 `xtdata`，使 QMT 策略可以脱离 QMT 跑本地验证。各项目的 `local_validate.py` 通过 import 使用它。用途是**沙箱验证**，不得用于真实下单。

---

## 六、改配置/改下单后的检查清单

- [ ] 改了资金分配 → `python scripts/check_capital_allocation.py`（exit 0）
- [ ] 改了构建逻辑 → 重新 `build_qmt_strategy`，确认 `BUILD_TAG` 已刷新
- [ ] 产物编码 GBK、首行 `# coding=gbk`、py3.6 语法核验通过
- [ ] 账号 ID 与目标策略匹配（67014907 / 70180771 不混用）
- [ ] 本地 `local_validate.py` 通过
- [ ] 部署后核对 QMT 主日志出现 `CTradeClient::order`（委托真进通道）
