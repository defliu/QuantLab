# QMT / miniQMT 避坑指南（Project16 实战踩坑记录）

> 更新：2026-08-22 ｜ 目的：跨会话持久记忆所有交易链路踩过的坑，任何会话接手必读。
> 关联知识库：《QMT孤儿持仓与撤单不拉黑》《QMT委托买卖防坑指南》《QMT_passorder异步与反查订单号》

---

## 一、QMT 持仓判断（最重要）

### 坑：持仓列表残留（孤儿持仓）
- **现象**：当天卖出的股票，QMT 持仓列表**仍会显示**该股，但 `volume=0`，要**次日才从列表清除**。
- **规则（必须遵守）**：判断持仓**必须以持仓数量 >0 为准**，`volume=0` 即已卖出。绝不能只看"列表里有"就当持仓。
- **代码落实**：`qmt_monitor.py` / `strategy_capital.py` / `qmt_clear.py` 均已按 `vol<=0` 过滤。
- **卖出后必须补 SELL 记录**到 `data/qmt_trade_log.csv`（否则"已有持仓数/已实现盈亏"会误判）。手动卖出脚本（如 qmt_trim）尤其容易漏。

### 坑：持仓查询非交易时段不稳定
- `query_stock_positions` 非交易时段**时好时坏**（可能返回空）。7:58 能查到 11 只、8:30 查 3 次全空，但挂单仍在（证明持仓还在）。
- **应对**：`strategy_capital.py` 内置重试（2 次 × 8s）；查不到按保守 0 计；`qmt_monitor` 回退本地成交记录。

### 坑：query_stock_trades 非交易时段阻塞
- `query_stock_trades`（查成交明细）**非交易时段会长时间阻塞/超时**，与 positions 的"返回空"不同，是直接卡住不返回。
- **应对**：只在交易时段查询成交明细；非交易时段如需核对费率/明细，改用 GUI 交易截图或人工提供数据。

### 坑：当天买入 T+1 不能卖
- 当天买入的票 `can_use_volume=0`，当天卖出会**废单**（状态码 57）。
- 精简持仓等操作只能卖"非当日买入"的票；当日买入的需次日开盘再卖。

### 坑：撤单非交易时段不生效
- `cancel_order_stock` 返回 0（受理），但状态仍"已报"（50），柜台非交易时段不处理，需开盘后才撤或成交。

### 坑：挂单冻结可用量
- 有未成交挂单时，持仓 `can_use_volume=0` 但 `volume>0`（被挂单冻结）。读取持仓应 `can_use or volume`。

---

## 二、成交记录（qmt_trade_log.csv）规范

- 字段：`time, code, side, vol, price, score, order_id`
- **清仓/市价卖出的 SELL 价格可能为 0**（qmt_clear 老版写法），会污染 FIFO 盈亏计算 → `strategy_capital.py` 已跳过 `price<=0` 的 SELL。
- **任何卖出都应写 SELL 记录**（含手动/临时脚本），否则"已有持仓数 H"（9:45 买入任务抵扣用）会误判。
- FIFO 配对：BUY 入队，SELL 按顺序抵消；已实现盈亏 = Σ(sell-buy)×vol。

---

## 三、miniQMT 委托状态码

| 码 | 含义 | 说明 |
|---|---|---|
| 48/49 | 未报/待报 | 排队中 |
| 50 | 已报 | 已报未成交（非交易时段挂单常见） |
| 55/56 | 部成/已成 | 成交（56=全部成交） |
| 57 | 废单 | 常见：T+1 卖出当日买入、非交易时段、涨跌停、资金不足 |

---

## 四、飞书推送（lark-cli bot 通道）

- **通道**：`lark-cli im +messages-send --user-id ou_76deaecde50e10576f8fdc8ba954a7b0 --text "..." --as bot`（lark-cli 路径 `C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\1.0.4\bin\lark-cli.exe`）。
- **必须处理环境变量**：调用前移除 `LARKSUITE_CLI_APP_ID` 和 `LARKSUITE_CLI_USER_ACCESS_TOKEN`，并设 `LARKSUITE_CLI_STRICT_MODE=off`。原因：外部注入的 app 只有 user token、无 bot 凭据，且 strict-mode=user 会挡 bot；移除后 lark-cli 回退 config.json 的 Trae app（cli_aa0fbe282c399cef，有 bot 凭据）。
- **不要用 user 身份**发 `ou_76de...` → 报 `open_id cross app`。
- **优先级与容错**：推送永远在所有主步骤之后执行；失败仅记录，绝不影响主流程（脚本层 try-except）。

---

## 五、账户纪律

- 账户约 1000 万，策略只用 `START_CAPITAL=100000`（10 万）建仓，其余资金不可动用；收益滚动进资金池（`strategy_capital.py`）。
- 模拟盘 `can_use_volume` 与 `volume` 语义：`can_use`=可卖（排除 T+1 冻结/挂单冻结），`volume`=总持股。

---

## 六、GUI 与自动化边界

### 坑：QMT 客户端为自绘界面（无障碍树稀疏）
- QMT 客户端界面控件为**自绘**（非系统原生控件），无障碍树稀疏，自动化无法读取"交易费用/成交明细"等栏位。
- **应对**：此类信息由人工提供 GUI 截图核对。本次模拟盘"交易费用"栏截图核对结果：佣金 5 元（万2 最低5元）/ 印花税 2.22 元（万5 卖出）/ 过户费 0.04 元（万0.1 沪市），与实盘费率一致 → 结算统一按实盘费率（见 `qmt_config.py`）。
