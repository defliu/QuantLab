# Project_16 miniQMT → 大QMT 迁移实施计划（文件桥方案）

> 编制：2026-08-27 | 依据：`F:\天翼云盘同步盘\Obsidian\量化知识库\miniQMT迁移大QMT方案调研报告.md`
> 决策：**路线一 + 文件桥（外算内执行）**。信号层（LightGBM 43 特征 + 真实 F2/F5 当日化）留在外部 Python 3.10，交易执行搬进大 QMT 内置 Python 3.6。
> 复用：`Project_ATR_lowvol/build/strategy_atr_lowvol_equalweight.py`（70180771 纯 QMT 内置执行脚手架）为内置侧模板；`broker/qmt_builder.py` 为构建工具。

---

## 一、为什么是文件桥（决策依据）

| 路线 | 结论 | 原因 |
|---|---|---|
| 路线一全量代码转换 | ❌ 不可行 | Project_16 信号层依赖 **LightGBM + pandas + 43 特征 + 新浪/东财当日化 HTTP 采集**，大 QMT 内置是 Python 3.6 + 无 pyarrow + 库受限，一个都塞不进去 |
| 路线二纯网关 | ❌ 死路 | 仍依赖 xtquant 外接，miniQMT 停运后网关一样失效（调研报告 9.2 自认） |
| **路线一 + 文件桥** | ✅ 采用 | 信号全留外部，只把"执行交易"搬进内置；ATR_EW 已证明该模式在本券商可行 |

---

## 二、现状盘点（miniQMT 依赖面）

### 外部下单链路（全部依赖 xtquant，需改造）

| 脚本 | 职责 | xtquant 依赖 |
|---|---|---|
| `qmt_trader.py` | 选股结果 → 下单（`--plan` 选股 CSV → dry-run/live） | `XtQuantTrader` / `order_stock` / `query_stock_asset` |
| `qmt_clear.py` | 清仓 | `query_stock_positions` / `order_stock` |
| `qmt_monitor.py` | 盯盘（止损/止盈/移动止盈） | 下单 / 查持仓 |
| `rebalance_daily.py` | 再平衡（先卖后买） | 复用 order_guard |
| `order_guard.py` | 委托守护：下单→轮询→撤单重试→涨跌停跳过 | `order_stock` / `query_stock_orders` / `cancel_order_stock` / `xtdata` |
| `data_sources.py` | 实时行情（F2 新浪已当日化，xtdata 仅 tick 参考） | `xtdata`（可弃） |

### 信号层（**零改动**，留在外部）

- `build_g2_daily.py`：43 特征 + 真实 F2/F5/龙虎榜/研报当日化（新浪/东财/增量库 HTTP，**已不依赖 xtdata**）
- `deploy_predict_g2.py`：g2 模型评分 → Top2 → `data/selections/g2/`
- LightGBM 模型 + pandas + pyarrow 全在外部 Python 3.10

### 复用资产（大 QMT 内置侧，已存在）

- `Project_ATR_lowvol/build/strategy_atr_lowvol_equalweight.py`：纯内置执行脚手架，含
  - `# coding=gbk` 头 + BUILD_TAG
  - `passorder(23/24, 1101, acct, code, 5, price, vol, 'remark', 2, userOrderId, C)` 下单
  - `_lookup_order`（轮询反查）+ `_cancel_order`（撤单）+ pending 状态机（超时重报/放弃）
  - 账本 `account_id` 戳校验（双账号 fail-safe）+ 对账（只清本策略持仓）
  - 资金三级 fallback（`get_account_info` / `get_trade_detail_data` / `get_cash`）
- `broker/qmt_builder.py`：QMT 单文件构建工具（GBK 转码 + py3.6 语法检查）

---

## 三、文件桥格式定义（核心交付）

### 3.1 目录约定

```
D:/QMT_POOL/g2_bridge/          # 文件桥根目录（与 ATR/V2 的 holdings 等并列）
├── cmd/                        # 外部 → 内置 指令（外部只写、内置只读）
│   ├── orders_<日期>.json      # 当日交易指令（买入/卖出）
│   └── cancel_<日期>.json      # 撤单指令（可空）
├── state/                      # 内置 → 外部 状态回写（内置只写、外部只读）
│   ├── fills_<日期>.json       # 成交回写（内置确认后追加）
│   ├── positions_<日期>.json   # 账户持仓快照（对账用）
│   ├── asset_<日期>.json       # 账户资产快照
│   └── heart_<日期>.json       # 心跳/状态（内置存活 + 上次动作）
└── meta.json                   # 桥配置（account_id / 策略名 / 文件版本）
```

### 3.2 指令格式 `cmd/orders_<日期>.json`

```json
{
  "account_id": "67014907",
  "strategy": "Project_16_g2",
  "date": "2026-08-28",
  "generated_at": "2026-08-28 09:34:12",
  "seq": 3,
  "orders": [
    {"action": "BUY",  "code": "600522.SH", "vol": 800, "price": 0.0, "side": "buy",
     "reason": "g2选股Top2", "strategy_order_id": "P16_20260828_0001"},
    {"action": "SELL", "code": "001266.SZ", "vol": 500, "price": 0.0, "side": "sell",
     "reason": "调出", "strategy_order_id": "P16_20260828_0002"}
  ]
}
```

字段说明：
- `vol`：股数（整手）；`price=0` 表示对手价（内置用 `passorder` 对手价 1101/5）
- `strategy_order_id`：外部生成的唯一委托号（`P16_<日期>_<序号>`），内置侧映射为 `userOrderId` 追踪
- `seq`：指令序号，内置侧用于去重（同 seq 不重复执行）

### 3.3 撤单格式 `cmd/cancel_<日期>.json`

```json
{
  "account_id": "67014907",
  "strategy": "Project_16_g2",
  "date": "2026-08-28",
  "seq": 1,
  "cancels": [
    {"strategy_order_id": "P16_20260828_0001", "code": "600522.SH", "reason": "成交超时撤单"}
  ]
}
```

### 3.4 成交回写 `state/fills_<日期>.json`

```json
{
  "account_id": "67014907",
  "strategy": "Project_16_g2",
  "date": "2026-08-28",
  "fills": [
    {"strategy_order_id": "P16_20260828_0001", "code": "600522.SH", "side": "buy",
     "vol": 800, "price": 12.35, "sys_order_id": "123456", "status": "FILLED",
     "time": "2026-08-28 09:35:01", "note": "全部成交"}
  ]
}
```

### 3.5 持仓/资产快照 `state/positions_<日期>.json` / `state/asset_<日期>.json`

```json
// positions：内置从 get_trade_detail_data('position') 导出（只含 .SH/.SZ 有效持仓）
{
  "account_id": "67014907", "date": "2026-08-28", "time": "14:50:00",
  "positions": [
    {"code": "600522.SH", "vol": 800, "available_vol": 800, "cost_price": 12.10, "market_price": 12.80}
  ]
}
// asset：内置从 get_account_info / get_trade_detail_data('account') 导出
{
  "account_id": "67014907", "date": "2026-08-28", "time": "14:50:00",
  "total_asset": 103500.0, "cash": 83500.0, "market_value": 20000.0
}
```

### 3.6 心跳 `state/heart_<日期>.json`

```json
{
  "strategy": "Project_16_g2_bridge", "account_id": "67014907",
  "build_tag": "20260827-000000",
  "last_heartbeat": "2026-08-28 14:55:01",
  "last_cmd_seq_processed": 3,
  "last_action": "processed_orders_seq3",
  "pending_count": 0,
  "errors": []
}
```

外部侧据此判断内置桥是否存活（`last_heartbeat` 超过 N 分钟未更新 → 告警）。

### 3.7 读写规则（防错）

| 规则 | 说明 |
|---|---|
| **指令只读一次** | 内置侧记录 `last_cmd_seq_processed`，同 `seq` 不重复执行（幂等） |
| **原子写** | 外部写 cmd 用 `写入临时文件 + rename`，内置读到完整 JSON 才处理 |
| **账号戳** | 指令/回写/心跳均带 `account_id`，内置侧校验匹配才处理（沿用 ATR_EW 账本戳 fail-safe） |
| **日期隔离** | 所有文件按 `_<日期>` 分文件，跨日不混 |
| **清空策略** | 内置每日收盘后把 `fills/positions/asset/heart` 归档到 `<日期>.bak`，次日重新生成 |

### 3.8 委托成功保障与挂单处理（pending 状态机规格，2026-08-27 补）

> 核心矛盾：`passorder` 是异步接口，**返回 0/None 不代表下单成功**。必须 userOrderId 追踪 + 反查确认 + 挂单重试。以下规格为内置桥必须实现的委托执行语义（复用 ATR_EW 成熟实现 + 浙能电力案例教训）。

#### 3.8.1 保证委托成功（防"假下单"）

| 规则 | 说明 |
|---|---|
| 唯一 userOrderId | 每笔委托生成 `P16_<日期>_<序号>`，下单后存入 pending 表 |
| 反查短轮询 | `passorder` 后即时反查撞 ~100ms order_id 分配延迟 → 短轮询 `get_trade_detail_data('order')` 约 1 秒后才查得到 |
| 反查匹配 | 只按 code/volume/status/direction/time 五条 AND 匹配；**remark 只能作候选优先级，不能硬过滤**（唯一候选即使 remark 空也返回） |
| 判方向 | 用 `m_strOptName`（含中文"买入"/"卖出"），不用未实测的 `m_nDirection` |

#### 3.8.2 挂单处理（pending 状态机）

**买入挂单**（5 分钟超时 + 3 次重试）：

```
1. passorder 下单 → 记 pending {userOrderId, code, vol, price, time, retry:0}
2. 每轮回调反查 order：
   - 成交 → 写 fills，清 pending，更新持仓
   - 未成交且 <5 分钟 → 继续等
3. 5 分钟超时 → 撤单(cancel sysid) → 重新 passorder(刷新价) → 更新 pending time/retry
4. 满 3 次（约 15 分钟）仍不成交 → 打印"pending放弃"彻底放弃
```

**卖出挂单**（30 秒超时 + 保留持仓）：

```
1. passorder 卖出 → 记 pending
2. 30 秒反查：确认则清 pending 记盈亏
3. 超时 → 撤单 → 保留 ledger 持仓待下次重试（绝不删持仓，防孤儿仓）
```

> **红线教训（AGENTS.md / 浙能电力案例）**：买入 pending 超时**必须重试，不可即刻放弃**。禁止"30 秒超时即撤单删 pending、永不补单"的写法——曾致该买没买。

#### 3.8.3 边界情况

| 情况 | 处理 |
|---|---|
| **涨跌停封板** | 反查涨跌停价，买入遇涨停 / 卖出遇跌停 → `LIMIT_SKIP`，不盲目重试 |
| **部分成交** | 撤单后只重试剩余未成交部分（`remaining -= traded`） |
| **废单(57)** | 重新委托，不记成功 |
| **已撤/部撤(54/53)** | 重试剩余 |
| **停牌** | `_is_suspended_bar`（连续两根无量才判停牌）跳过 |
| **委托丢失/反查不到** | 保留 pending + 心跳监测，外部可凭指令文件人工补救 |
| **状态码** | 48已报 / 49部成 / 50已报待撤 / 53部撤 / 54已撤 / 56已成 / 57废单（与 miniQMT 同数值集） |

#### 3.8.4 新账号调通小单验证（三件套，验收前置）

1. **不易成交价挂单测试**：买跌停价 / 卖涨停价下 1-2 笔小单 → 验证能挂上、能查到、能撤掉（报/撤链路）
2. **正常价成交测试**：下 1 笔能成交的小单 → 验证成交回写、fills、持仓更新
3. **对账**：`positions/asset` 回写 vs QMT 客户端实际一致

> 此三件套通过，pending 状态机的"保证委托成功 + 挂单处理"才算真正调通。

---

## 三·五、官方手册要点补充（2026-08-27 依据 dict.thinktrader.net）

> 官方手册（迅投知识库 `innerApi` vs `nativeApi`）已核对，以下要点需在实施时落实/修正。速查对照表见 `量化知识库\60_工程知识库\QMT\miniQMT与大QMT差异对照表.md`。

| # | 官方要点 | 对迁移的影响 |
|---|---|---|
| 1 | **`set_account` 是主推前置**：实盘所有主推函数（`order_callback`/`deal_callback`/`position_callback` 等）必须先在 `init` 中 `ContextInfo.set_account(account)` 才会推送 | **内置桥 `init` 必须调用 set_account**，否则成交回报收不到、只能靠轮询（次优） |
| 2 | **`prType` 语义**：5=最新价 `LATEST_PRICE`、11=指定价 `FIX_PRICE`、14=对手价。**ATR_EW build 原注释"对手价"为误标（实为最新价），已修正（BUILD_TAG 20260827-171855）** | 内置桥下单统一用最新价(5)或对手价(14)，按需选择，注释须与手册一致 |
| 3 | **`get_trade_detail_data` 大小写**：官方手册用大写 `'STOCK'/'ACCOUNT'/'ORDER'/'POSITION'`；ATR_EW 实测用小写 `'stock'` 可用。迁移时建议按官方大写 + 兼容小写 fallback | 内置桥对账/查询需验证大小写敏感性，双写兼容 |
| 4 | **`get_market_data_ex` 有 `subscribe` 开关**：内置取数时可顺带订阅实时（`subscribe=True`），比 xtquant 的"取缓存+独立 subscribe"更简洁 | 内置桥取实时价可直接 subscribe，减少一次调用 |
| 5 | **`quickTrade` 语义**：0=下根 bar 首 tick 触发、1=非历史 bar 调用即触发、2=不判断 bar 状态调用即触发（**历史 bar 也能触发**） | 回测/实盘对 quickTrade 行为敏感，内置桥回测模式需注意撮合时点 |
| 6 | **便捷下单函数仅内置且仅回测生效**：`order_target_percent/order_value/order_shares` 等只在回测生效，实盘要用 `passorder` + 自算仓位 | 内置桥实盘必须用 `passorder`（符合计划现状），勿依赖便捷函数 |
| 7 | **`opType` 信用账户**：`CREDIT_BUY/CREDIT_SELL` 实际值 23/24 与 `STOCK_BUY` 相同 → 信用账户必须显式用 33/34 | 本策略为普通账户（67014907 股票账户），用 23/24，但注释标注防误用 |
| 8 | **跨客户端对账**：`m_strRemark`（userOrderId）只在下单客户端可见，他端查为空 → 只能凭柜台委托号 `m_strOrderSysID` | 文件桥对账只凭 sysid（已在 3.4 定义），外部不直接查 order 表 |

**对内置桥 `init` 的追加要求**（并入 4.2）：
- 必须在 `init` 首部调用 `ContextInfo.set_account(account)`（官方前置，主推生效前提）
- 订单反查用官方大写 `get_trade_detail_data(account, 'STOCK', 'ORDER')` + 小写 fallback
- `passorder` 用 `prType=5`（最新价）或 `14`（对手价），quickTrade=2（立即下单）

---

## 四、改造清单

### 4.1 外部侧（Python 3.10，改 1 个新模块 + 3 个入口）

**新增 `qmt_bridge_client.py`**（替代 `order_guard.py` 的 xttrader 部分）：
- `write_orders(orders, date)` → 写 `cmd/orders_<日期>.json`
- `write_cancels(cancels, date)` → 写 `cmd/cancel_<日期>.json`
- `read_fills(date)` → 读 `state/fills_<日期>.json`
- `read_positions(date)` / `read_asset(date)` → 读状态快照
- `wait_fill(strategy_order_id, timeout)` → 轮询 fills 直到成交/超时（替代 `order_with_guard` 轮询）
- 内置桥心跳检查 `is_bridge_alive()`

**改 3 个入口**（替换 `order_stock`/`query_*` 调用为桥客户端）：
- `qmt_trader.py`：`--live` 时改为 `write_orders` + `wait_fill`
- `qmt_clear.py`：清仓改为 `write_orders(全卖)` + `wait_fill`
- `qmt_monitor.py`：盯盘卖出/止损/止盈改为 `write_orders` + `wait_fill`；持仓/资产查询改 `read_positions/read_asset`
- `order_guard.py`：`order_with_guard` 内部改为桥调用（保留涨跌停/重试逻辑在外部，或下沉内置——见 4.2）

**零改动**：`build_g2_daily.py`、`deploy_predict_g2.py`、选股/评分/风控决策全部不动。

### 4.2 内置侧（Python 3.6 + GBK，新增 1 个单文件）

**新增 `build/strategy_p16_g2_bridge.py`**（基于 ATR_EW 脚手架）：
- 生命周期：`init(C)` → `handlebar(C)`（定时器或日线触发）→ `exit(C)`
- `init`：**首部 `ContextInfo.set_account(account)`（官方主推前置）**、读 `meta.json`、校验账号、加载上次 `last_cmd_seq_processed`
- 每轮回调：
  1. 读 `cmd/orders_<日期>.json` + `cmd/cancel_<日期>.json`（若 `seq` > 已处理）
  2. 对每条指令：`passorder` 下单（BUY=23 / SELL=24，对手价 1101）+ 记录 pending `{userOrderId, code, vol, side}`
  3. `_check_pending_orders`：轮询 `get_trade_detail_data('order')` 确认成交 → 写 `state/fills_<日期>.json`；超时撤单重报（沿用 ATR_EW pending 状态机，5 分钟/3 次）
  4. 按需导出 `state/positions_<日期>.json` / `state/asset_<日期>.json`（对账时点）
  5. 更新 `state/heart_<日期>.json`
- **涨跌停跳过**：内置侧用 `C.get_market_data_ex` 判断（沿用 ATR_EW `_is_suspended_bar`/限价判断），外部指令带 `allow_limit_skip: true` 时执行，否则回报 `LIMIT_SKIP`
- **对账**：`_reconcile_own_holdings`（只清本策略 ledger，防误动 ATR/V2 持仓）
- **账本**：`_g_my_codes` + `account_id` 戳 + 累计盈亏（复用 ATR_EW `_load_holdings/_save_holdings`）

**构建**：`broker/qmt_builder.py` 生成（GBK 转码 + py3.6 语法检查 + BUILD_TAG）。

---

## 五、迁移实施阶段

### 阶段一：环境准备（0.5 天）

1. 确认大 QMT 客户端已装、内置 Python 3.6 可用（参考 ATR_EW 已运行环境）
2. 确认 67014907 账号在大 QMT 界面选定可注入
3. 建 `D:/QMT_POOL/g2_bridge/{cmd,state}/` 目录 + `meta.json`
4. 确认 `broker/qmt_builder.py` 可构建内置单文件

### 阶段二：外部桥客户端 + 内置桥单文件（2-3 天）

1. 写 `qmt_bridge_client.py`（外部侧）
2. 写 `build/strategy_p16_g2_bridge.py`（内置侧，复用 ATR_EW 脚手架）
3. `broker/qmt_builder.py` 构建 → 产出 GBK 单文件
4. 静态校验：`# coding=gbk` 头、py3.6 语法（无 f-string/walrus/match）、无 MOCK 残留

### 阶段三：模拟信号验证（1 个交易日）

1. 内置桥用**模拟账号**跑（或 real 账号 dry-run 不下单）
2. 外部 `qmt_trader.py --dry-run` 生成指令 → 内置桥读指令 → 验证信号时机/数量/价格与预期一致
3. 验证 fills 回写、心跳、pending 状态机走通

### 阶段四：小单实盘验证（0.5-1 天）

1. 用不易成交价（买跌停/卖涨停附近）下 1-2 笔小单并撤掉，验证报/撤链路
2. 对账验证：`positions/asset` 回写 vs 客户端实际一致
3. 确认 17 点后不撤单（盘后撤单窗口限制）

### 阶段五：灰度切换（1-2 天）

1. 先在 67014907 上跑**小资金**（如策略池 10%），外部旧链路并行观察 1-2 个交易日
2. 对账一致后，把 `capital_base` 调回全量
3. **旧 miniQMT 链路保留 ≥1 个月**作为回滚通道（调研报告阶段四）

### 阶段六：旧链路下线

1. 新方案稳定 ≥1 周后停 miniQMT 策略
2. 归档迁移文档 + 踩坑记录（写入 PROJECT_MEMORY.md）

---

## 六、验收清单

- [ ] 外部 `qmt_bridge_client.py` 写指令 → 内置桥读到并执行（seq 幂等）
- [ ] BUY/SELL 各验证 ≥1 笔成交 + 1 笔撤单
- [ ] fills 回写字段完整、`sys_order_id` 可追溯
- [ ] pending 超时 → 撤单 → 重报 → 3 次放弃 全链路
- [ ] 涨跌停跳过正确（LIMIT_SKIP 回报）
- [ ] positions/asset 对账一致（跨客户端只凭 sysid，remark 不可见）
- [ ] 心跳存活监测（外部可判内置桥死亡）
- [ ] 双账号隔离：桥只操作 67014907，不碰 ATR/V2 的 70180771 持仓
- [ ] 账本 account_id 戳校验 fail-safe 生效
- [ ] GBK 编码 + py3.6 语法检查通过 + BUILD_TAG 输出

---

## 七、风险与应对

| 风险 | 应对 |
|---|---|
| 内置 Python 3.6 缺库 | 信号全外置，内置只做纯执行（passorder/查询），零第三方库依赖 |
| 跨客户端对账（remark 不可见） | 只凭 `sys_order_id`（柜台委托号）对账；外部不直接查 order 表，靠 fills 回写 |
| 单线程阻塞 | 内置桥每轮回调 <100ms（只做读文件+下单+查委托），无 sleep 死循环 |
| 交易日切换重启 | `init` 幂等（读 last_cmd_seq_processed 继续），沿用 ATR_EW 模式 |
| 内置桥死亡静默 | 心跳监测 + 外部告警；外部保留最后一次指令文件可人工补救 |
| 双账号串账 | 桥只认 67014907（meta 校验），指令/回写/心跳全带 account_id |
| 券商未正式通知 | 迁移期间 miniQMT 保留运行，双通道并行，验证后再下线 |

---

## 八、参考资产

| 资产 | 路径 | 用途 |
|---|---|---|
| 调研报告 | `F:\...\Obsidian\量化知识库\miniQMT迁移大QMT方案调研报告.md` | 决策依据 |
| 内置执行模板 | `Project_ATR_lowvol/build/strategy_atr_lowvol_equalweight.py` | 内置侧脚手架（passorder/pending/对账/账本） |
| 构建工具 | `D:\QuantLab\broker\qmt_builder.py` | GBK 单文件生成 |
| 现有下单入口 | `qmt_trader.py` / `qmt_clear.py` / `qmt_monitor.py` / `order_guard.py` | 外部侧改造对象 |
| 大 QMT 内置文档 | `https://dict.thinktrader.net/` | API 参考 |

*仅供个人量化研究使用，不构成投资建议。市场有风险。*
