# 任务书：g2 大QMT 文件桥 · 内置执行器（Python 3.6.8 单文件源码）

> 本文件是对外部编码 agent 的唯一任务契约。执行者只允许产出「唯一输出文件」，禁止修改/创建任何其他文件，禁止任何 git 操作（commit/push/stash/checkout 一律禁止）。

## 一、目标

编写一个大QMT（QMT 客户端内置 Python 3.6.8）策略**源文件**，作为文件桥的「内置执行器」：
外部信号层把买卖指令写到文件桥，本策略在大QMT内轮询读取指令 → 委托 → 反查 → 超时撤单重试 → 状态回写。

数据流：
```
外部(选股/风控, Python3.10)                内置执行器(大QMT, Python3.6)
        │ 写 cmd/orders_<date>.json  ──▶  读指令(seq幂等)
        │ 写 cmd/cancel_<date>.json  ──▶  撤单处理
        │ ◀── 写 state/fills_<date>.json    委托/成交/失败回写
        │ ◀── 写 state/positions_<date>.json 持仓快照
        │ ◀── 写 state/asset_<date>.json    资产快照
        │ ◀── 写 state/heart_<date>.json    心跳+已处理seq
```

## 二、唯一输出文件

`D:\QuantLab\projects\Project_16_LightGBM股票大师\strategy\strategy_p16_g2_bridge_src.py`

- 编码 UTF-8，首行 `# coding=utf-8`（后续由人工转 GBK 产物，你不要做 GBK 转码）
- 纯 Python 3.6.8 语法，单文件自包含
- 只允许 import 标准库：os / json / time / traceback（不需要其他任何库）

## 三、允许读取的文件（仅限以下 5 个，禁止读取或修改其他任何文件）

1. `D:\QuantLab\projects\Project_16_LightGBM股票大师\qmt_bridge_client.py` —— 桥协议对端（外部客户端），**所有文件名/字段名必须与它严格一致**
2. `D:\QuantLab\projects\Project_16_LightGBM股票大师\specs\miniQMT迁移大QMT实施计划.md` —— 架构背景与首版范围
3. `D:\QuantLab\broker\qmt_order.py` —— passorder 正确签名与防坑参考（注意它是 miniQMT/xtquant 版，你只参考签名与 pending 思想，不用 xtquant）
4. `D:\QMT_POOL\g2_bridge\meta.json` —— 桥配置（account_id 等）
5. `D:\QuantLab\projects\Project_ATR_lowvol\build\strategy_atr_lowvol_equalweight.py` —— 大QMT 内置策略生命周期实战模板（init/handlebar/exit、调度方式、日志风格照它来）

## 四、硬性红线（违反任何一条 = 任务失败）

1. **Python 3.6.8 兼容**：禁止 f-string；禁止类型注解（`def f(x: int)`、`dict[str,...]`、`list[str]`）；禁止 `:=`；禁止 match/case；字符串格式化一律用 `%`。
2. **passorder() 是全局函数**，正确签名（第6参=价格、第7参=股数，绝不可颠倒）：
   `passorder(op, order_type, accountid, code, prType, price, volume, C)`
   常量：买 `OP_BUY=23`、卖 `OP_SELL=24`、`order_type=1101`（单股单账号按股数）、`prType=11`（指定价）。passorder 正常返回 0/None，**不是订单号**。
3. **账号硬编码** `ACCOUNT_ID = "70180771"`。
4. **交易时间判断只用 `C.get_current_time()`**（返回形如 "HH:MM:SS"），禁止用 datetime.now()/time.localtime() 判断交易时段；相对计时（轮询间隔/超时差）可用 `time.time()`。
5. **订单反查**：`C.get_trade_detail_data(ACCOUNT_ID, 'STOCK', 'order')`；passorder 后 sysid 分配有约 100ms 延迟，必须短轮询（如 6 次 × 0.25s）。判方向用字段 `m_strOptName`（含中文"买入"/"卖出"），不要用 `m_nDirection`。
6. **买入 pending 超时 300 秒、最多重试 3 次**：超时 → `C.cancel(订单ID, ACCOUNT_ID, 'STOCK')` → 重新 passorder → 更新 pending 时间与重试计数继续跟踪；3 次重试后写 fills `status="ABANDONED"` 并彻底放弃该笔（防「30秒即放弃永不补单」）。
7. 无任何 MOCK / 测试代码 / 打印调试残留（正常业务日志除外）；不 import pyyaml/pyarrow/xtquant 等任何第三方库。
8. 所有状态文件写入用「临时文件 + os.replace」原子写，防读到半个 JSON。

## 五、桥协议（文件名与字段名必须与 qmt_bridge_client.py 完全一致）

目录常量：
```
BRIDGE_DIR = "D:/QMT_POOL/g2_bridge"
CMD_DIR    = "D:/QMT_POOL/g2_bridge/cmd"
STATE_DIR  = "D:/QMT_POOL/g2_bridge/state"
```

读取（外部 → 内置）：
- `cmd/orders_<YYYYMMDD>.json`：`{"account_id","strategy","date","generated_at","seq",  "orders":[{"action":"BUY"/"SELL","code","vol","price","reason","strategy_order_id"}]}`
  - code 形如 `"600522.SH"`；**下单前必须转成 QMT 代码格式** `"<exch>.<num>"`（如 `600522.SH` → `SH.600522`，`000001.SZ` → `SZ.000001`，688/60 开头 SH，0/3 开头 SZ；其他前缀记日志跳过）
- `cmd/cancel_<YYYYMMDD>.json`：`{"account_id","strategy","date","seq","cancels":[{"strategy_order_id","code","reason"}]}`

写入（内置 → 外部，JSON，UTF-8）：
- `state/fills_<date>.json`：`{"account_id","date","updated_at","fills":[{"strategy_order_id","code","action","vol","price","status","sysid","reason","ts"}]}`
  - `status ∈ {"FILLED","PARTIAL_FILLED","CANCELED","REJECTED","LIMIT_SKIP","ABANDONED"}`
  - 同一 strategy_order_id 多次回写要**更新合并**（不重复 append）；追加整笔时保持已有键
- `state/positions_<date>.json`：`{"account_id","date","updated_at","positions":[{"code","volume","can_use_volume","avg_price","market_value"}]}`（code 输出成 `600522.SH` 风格，方便外部对账）
- `state/asset_<date>.json`：`{"account_id","date","updated_at","total_asset","cash_available","market_value"}`
- `state/heart_<date>.json`：`{"account_id","date","last_heartbeat","build_tag","pending_count","last_cmd_seq_processed"}`

## 六、执行逻辑要求

1. `BUILD_TAG` 硬编码为真实当前时间（格式 `"YYYYmmdd-HHMMSS"`）；`init(C)` 打印 `[P16G2][BUILD] BUILD_TAG=... account=70180771 bridge=D:/QMT_POOL/g2_bridge`。
2. **seq 幂等**：orders/cancel 文件的 `seq` 必须 > heart 的 `last_cmd_seq_processed` 才处理；处理完 → 更新 `last_cmd_seq_processed` → 把该 cmd 文件改名归档为 `orders_<date>_done_<seq>.json` / `cancel_<date>_done_<seq>.json`（归档目标已存在则先 os.remove）。外部客户端会以 heart 的 last_cmd_seq_processed 与归档文件共同防重。
3. **账号防呆**：cmd 文件 `account_id != ACCOUNT_ID` 时拒绝执行全部指令，打印 `[P16G2][ACCT-MISMATCH]`，不写 fills（直接忽略该文件，不改 seq——留给人工处理）。
4. **无效指令**：`vol<=0` 或 `price<=0` → 不调 passorder，直接写 fills `status="REJECTED"`、reason 注明。
5. **成交确认**：委托后短轮询反查；查到订单后按 `m_nOrderID` 登记 pending（含 strategy_order_id 映射），后续 handlebar 里持续反查订单状态字段（`m_nOrderStatus`：如 56=部成、57=已成、49=已撤 等，容错处理未知值仅记日志）与成交量（`m_nDealVolume`），最终态写 fills；订单对象里若有 `m_strSysid` 则回写 sysid 字段。
6. **持仓/资产快照**：`C.get_trade_detail_data(ACCOUNT_ID, 'STOCK', 'position')` 与 `'account'`；字段容错 getattr（position: m_strInstrumentID/m_nVolume/m_nCanUseVolume/m_dOpenPrice/m_dInstrumentValue；account: m_dBalance/m_dAvailable/m_dInstrumentValue 任取可用的），缺失记 0 并记一次日志。刷新间隔 ≥10 秒（用 time.time() 差控制），每次 handlebar 不必都刷。
7. **心跳**：每次 handlebar 刷新 heart（last_heartbeat = C.get_current_time()，date = 当日 YYYYMMDD，pending_count = 当前 pending 数）。
8. **调度方式**：完全跟随 ATR 模板（第 5 个参考文件）的 init/handlebar 触发与时间控制写法；策略需在交易时段每分钟被触发。
9. **日志**：统一前缀 `[P16G2]`，用 print；关键节点必须打日志：读桥新指令、每笔委托、反查结果、撤单、重试、放弃、日终。
10. `exit(C)` 打印日终摘要（当日委托笔数/成交笔数/放弃笔数/最终 pending）。
11. 中文注释适量（关键逻辑处），不写多余空话。

## 七、禁止事项（再强调）

- 只写第二节那 1 个文件；不创建 README/测试/脚本/备份文件
- 不执行任何 shell/git 命令；不安装依赖；不跑回测
- 不修改 qmt_bridge_client.py、meta.json、模板文件或任何已有文件
