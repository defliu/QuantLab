# QMT / miniQMT 避坑指南（Project16 实战踩坑记录）

> 更新：2026-10-02（最近）｜ 目的：跨会话持久记忆所有交易链路踩过的坑，任何会话接手必读。
> 历史更新：2026-08-22｜ 最近一次修订：T-20261002-003 —— 订正飞书 open_id 漂移（`ou_76dea...` → `ou_bd134...`）、bot appId 漂移（`cli_aa0f...` → `cli_aade2524ddbd9bd6`，已实读 `~/.lark-cli/config.json` 核实）、lark-cli 版本目录硬编码改为稳定出口 `C:\Users\Administrator\bin\lark-cli.exe` + 统一包装器 `lark_push.py`。
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

- **通道**：`lark-cli im +messages-send --user-id ou_bd13444d8ea53c28249c669f43f3eeff --text "..." --as bot`（bot `cli_aade...` 下刘诚的 open_id，2026-08-29 实测可私聊）。**open_id 以代码为准**：现行为 `qmt_config.py` / `g2_config.py` / `g2_ens_config.py` 的 `FEISHU_OPEN_ID = ou_bd13444d8ea53c28249c669f43f3eeff`；`ou_76dea...` 是**已废弃旧值**（见 `部署核对清单_新机.md`），文档残留该值属漂移，勿照抄。
- **lark-cli 路径（禁止硬编码插件版本目录）**：统一稳定出口 `C:\Users\Administrator\bin\lark-cli.exe`。**首选项目统一包装器 `lark_push.py`**（自带多候选探测 + glob 兜底 + 分片 + 严格成功判定），而非直接调 CLI。自检：`python lark_push.py --check`。
  - 历史教训（2026-09-23 实测）：文档写 `...\lark\1.0.4\...` 而插件实际已升 `1.0.5`，路径不存在 → 模型只能「记录失败」，用户收到「任务跑了但没消息」。插件每次升级都会重演，故此处只给稳定出口与包装器，不写 `1.0.x`。
- **必须处理环境变量**：调用前移除 `LARKSUITE_CLI_APP_ID` 和 `LARKSUITE_CLI_USER_ACCESS_TOKEN`，并设 `LARKSUITE_CLI_STRICT_MODE=off`。原因：外部注入的 app 只有 user token、无 bot 凭据，且 strict-mode=user 会挡 bot；移除后 lark-cli 回退 `~/.lark-cli/config.json` 里注册的 bot 应用 **`cli_aade2524ddbd9bd6`**（2026-10-02 实读：这是本机唯一注册的应用，secret 在系统 keychain）。**本文档此前写的 `cli_aa0fbe282c399cef` 属漂移——该 appId 不在本机 config.json 里。**
- **不要用 user 身份**发 `ou_bd134...` → 报 `open_id cross app`（open_id 是应用维度的，只在生成它的那个应用下有效）。
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

---

## 七、大QMT（交易端模拟）passorder 代码格式（2026-08-31 迁移实锤，T-20260831-002）

### 坑：下单代码必须 `600522.SH`（数字在前），禁止 `SH.600522`

- **现象**：大QMT 文件桥策略 passorder 返回 `ret=0`，但委托**从未进通道**：委托界面无记录、`get_trade_detail_data` 查无此单、`userdata\users\70180771\XtTradeData` 委托库 0 字节；策略端 PENDING-NO-ORDER 空转 300s 重试，全是废单。
- **根因**：`_to_qmt_code` 把桥协议 `600522.SH` 翻转成 `SH.600522` 传 passorder。QMT 主日志 `userdata\log\XtClient_20260831.log` 实锤：
  ```
  parserParam func:passorder, opType:23, orderType:1101, accountID:70180771, orderCode:600522SH, prType:5, modelPrice:35.14
  [msg service] 下单代码 [600522SH] 不合法!
  ```
  QMT 把 `SH.600522` 解析为 `600522SH`（数字尾挂交易所）→ 校验不合法 → **静默废单**（ret=0 仅异步接口受理，不报错）。
- **对照**：ATR（`atr_ew_holdings.json` 实盘持仓 `601985.SH`）与 Project_10（build 里 `600000.SH`）都是**数字在前**，实盘成交正常。
- **修复（已落盘 BUILD_TAG=20260831-151459）**：
  1. `_to_qmt_code` 原样返回 `600522.SH`，不再翻转；
  2. `_norm_code` 用 `split('.')[0]` 取 6 位数字（原 `[-1]` 误取交易所后缀）；
  3. 风控纳管 / 持仓快照裸码补 `.SH`/`.SZ` **后缀**（原补 `SH.` 前缀）。
- **判断委托真进通道**：QMT 主日志出现 `CTradeClient::order [order] ... acc: 2_..._70180771`；只有 `parserParam` + 「不合法」就是废单。
- **排查手法**：废单时先看 QMT 主日志（`userdata\log\XtClient_<date>.log`），不是 FormulaOutput 策略日志；`passorder` 消息在 msg service / parserParam 段。
