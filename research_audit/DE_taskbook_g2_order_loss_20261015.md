# DE 任务书：G2/ENS 桥 2026-10-14 丢单排查与加固修复

## §0 身份与目标

你是 DE（DevEco Code CLI）。本任务书由 QuantLab op 编写，op 会用原始数据独立复算验证你的结论后再落盘。

目标：
1. 排查 2026-10-14 G2 桥 + ENS 桥 orders 指令文件未被消费（丢单）的根因
2. 按 §4 规格实施修复（加固错误可见性，只增不改业务逻辑）
3. 顺带分析 STRATEGY_V2（Project_10 价值小盘V2）同类静默失败（只报告不改买入逻辑）
4. 输出报告到 §5 指定路径

## §1 背景与已确认事实（不要重复调查，直接使用）

### 事故描述

2026-10-14（周二）：
- **G2 桥**（账号 70180771，运行目录 `D:/QMT_POOL/g2_bridge/`）：`cmd/orders_20261014.json`（seq=1，09:55:56 写入，内容=SELL 000607.SZ 200股 price=4.13「动态等权超配减仓」+ BUY 000960.SZ 1100股 price=28.01「g2选股Top1」）**未被消费**。证据：`state/heart_20261014.json` 的 `last_cmd_seq_processed=0`；orders 文件未归档（无 `_done_1` 后缀）；无 `state/fills_20261014.json`；账户持仓未变（000607.SZ 仍 7700 股、无 000960.SZ）。
- **ENS 桥**（同账号，`D:/QMT_POOL/p16_ensemble_bridge/`）：同症状（orders 未归档、无 fills、heart seq=0）。丢失内容：SELL 600691.SH 100股 + BUY 002762.SZ 2000股「融合选股Top1」。
- **STRATEGY_V2**（账号 67014907，第一实例 `D:\国金QMT交易端模拟`）：10-14 09:30 触发补仓（日志 `[rebal] 持仓比例=0.0% < 60%, 触发补仓`）但全天零 `[buy]` 输出（对照 10-13 正常 24 笔）；当日策略日志 `D:/QMT_POOL/strategy_log_v2.txt` 完全缺失（止于 10-13 15:00:01，日志写入失败）。

### 已排除假说（附依据，勿重复排查）

1. 文件权限：ACL Everyone:(I)(F) 全权；丢失 orders 文件字节级干净（纯 UTF-8 无 BOM，与归档成功文件逐字节同构），无 .tmp 残留
2. 配置漂移：两桥 build 产物 BUILD_TAG/BRIDGE_DIR/ACCOUNT_ID 与 QMT 运行日志 INIT 行逐字一致
3. 路径构造：源码 BRIDGE_DIR/CMD_DIR/STATE_DIR 常量正确
4. QMT 连接/桥存活：桥进程存活，15:00:06 正常写 state
5. seq 幂等跳过：heart seq=0，orders seq=1，不满足 `seq <= _g_last_cmd_seq` 的静默跳过条件
6. 写侧：`_atomic_write_json` 正常（外部信号层写入成功，LastWriteTime 在交易时段内：ENS 09:34:55 / G2 09:55:56）
7. 盘后重启补处理：handlebar 交易时段门控是设计行为，盘后重启不处理当日未消费 orders（20:53 重启后重新 INIT last_seq 仍为 0 符合预期）

### 关键证据链

- 两桥 `state/` 下 `asset_20261014.json` 与 `positions_20261014.json` **存在** = handlebar 交易时段在运转（阶段 4/5 快照有执行），仅 `_process_orders_cmd` 静默失败
- 第二实例 FormulaOutput_20261014 全文 19 行：两桥 08:52:26 NEW-DAY + INIT（last_seq=0），**08:52 之后到 20:53:49 交易时段零输出**（无 `[CMD]`、无 HANDLEBAR-ERR 堆栈）
- 10-14 23:46 第一实例三次 "Foribdden FileIO" 错误命中：锁写入失败 / 状态载入失败（`_load_state`）/ 日志写入失败，均转被动实例。"Foribdden FileIO"（拼写如此）字符串内置于两个 QMT 实例的 `bin.x64\python36.dll` —— QMT 内置 Python 3.6 运行时文件 IO 层错误
- 10-13 对照完好：ENS 10-13 10:08:12 成功消费 orders seq=1；G2 10-13 09:31:30 成功执行 RISK 止损

### 当前根因假设（待你验证或推翻）

10-14 存在 QMT python36.dll 文件 IO 层异常，`_read_json` 对 orders 文件全天返回 None（零 `[CMD]` 打印）。具体是 `open()` 还是 `json.load()` 抛异常，因静默吞异常而无法分辨——这正是修复要解决的可见性问题。

## §2 允许读取的文件清单

源码（QuantLab 仓库）：
- `D:/QuantLab/projects/Project_16_LightGBM股票大师/strategy/strategy_p16_g2_bridge_src.py`
- `D:/QuantLab/projects/Project_16_LightGBM股票大师/strategy/strategy_p16_ens_bridge_src.py`
- `D:/QuantLab/projects/Project_16_LightGBM股票大师/strategy/strategy_p16_v13_risk_src.py`
- `D:/QuantLab/projects/Project_10_价值小盘V2/strategy_v2.py`
- 外部管道脚本（同 Project_16 strategy 目录）：`rebalance_g2.py`、`rebalance_ens.py`、`qmt_bridge_client.py`、`qmt_bridge_client_ens.py`、`reconcile_g2.py`、`reconcile_ens.py`、`push_alert_card.py`、`qmt_monitor.py`、`qmt_card.py`
- 构建脚本：`D:/QuantLab/projects/Project_16_LightGBM股票大师/build.py`（只读，禁止运行）

运行时证据（D:/QMT_POOL/）：
- `D:/QMT_POOL/g2_bridge/cmd/`（含未归档 `orders_20261014.json` + `positions_cfg_20261014.json` + 历史 `orders_*_done_*.json`）
- `D:/QMT_POOL/g2_bridge/state/`（`asset_20261014.json`、`positions_20261014.json`、`heart_20261014.json`，**缺 `fills_20261014.json`**）
- `D:/QMT_POOL/p16_ensemble_bridge/{cmd,state}/`（同症状）
- `D:/QMT_POOL/p16_v13_risk/`（只有 `cmd/positions_cfg_v13_*.json`，无 orders 消费；`state/heart_v13_20261014.json` 正常）
- `D:/QMT_POOL/strategy_log_v2.txt`（止于 10-13 15:00:01）+ `strategy_log_v2.txt.bak_mockpollute_20261014_151516`（1935 行，10-14 行数=0）+ `v2_holdings_state.json` + `v2_strategy.lock`

QMT 实例日志（在两个 QMT 实例根目录下自行定位，文件名形如 `FormulaOutput_20261014.log` / `XtClient_FormulaOutput_*.log`）：
- 第二实例 `D:/QMT交易端模拟`（账号 70180771，跑 G2/ENS 两桥）
- 第一实例 `D:/国金QMT交易端模拟`（账号 67014907，跑 STRATEGY_V2 + V13 风控）

构建产物（只读对照）：
- `D:/QuantLab/projects/Project_16_LightGBM股票大师/build/strategy_p16_g2_bridge.py`（BUILD_TAG=20261002-193332）
- `D:/QuantLab/projects/Project_16_LightGBM股票大师/build/strategy_p16_ens_bridge.py`（BUILD_TAG=20261002-200314）
- `D:/QuantLab/projects/Project_10_价值小盘V2/build/strategy_v2.py`（build=20261013-144730）

## §3 允许修改的文件（仅以下 4 个源码文件）

1. `D:/QuantLab/projects/Project_16_LightGBM股票大师/strategy/strategy_p16_g2_bridge_src.py`（修复 A+B+C）
2. `D:/QuantLab/projects/Project_16_LightGBM股票大师/strategy/strategy_p16_ens_bridge_src.py`（修复 A+B+C）
3. `D:/QuantLab/projects/Project_16_LightGBM股票大师/strategy/strategy_p16_v13_risk_src.py`（修复 A）
4. `D:/QuantLab/projects/Project_10_价值小盘V2/strategy_v2.py`（修复 A on `_lock_read`；修复 D 仅分析不改逻辑）

**禁止项**：
- 禁止修改除上述 4 个文件 + §5 输出报告外的任何文件
- 禁止 commit、禁止任何 git 操作
- 禁止直接修改 `build/` 产物（由 op 审核后运行 build.py 重建）
- 禁止运行 build.py
- 禁止改 `_execute_order`/passorder 调用/风控阈值/撤单逻辑/pending 状态机/评分逻辑/`_atomic_write_json` 实现
- 禁止删除、改名或改动 10-14 的运行时证据文件（`orders_20261014.json` 等是事故证据，必须保留）

## §4 修复规格（按函数定位，行号为参考；逐字遵守）

### 修复 A：`_read_json` 显式错误日志（4 个文件）

**原因**：`_read_json` 静默吞所有异常返回 None，是 `_process_orders_cmd` 中除「seq 已处理」外唯一零打印路径——10-14 丢单无法定位根因即源于此。

G2 桥 `_read_json`（约 L126-131），改为（print tag `[P16G2]`）：

```python
def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        # errno==2 (ENOENT) 是可选文件不存在的正常情况，保持静默；
        # 其余异常（权限/解码/IO层）显式打印——10-14 丢单根因不可见即源于此处的静默吞异常
        if getattr(e, "errno", None) == 2:
            return None
        print("[P16G2][READ-JSON-ERR] %s: %r" % (path, e))
        return None
```

ENS 桥 `_read_json`（约 L124-129）同款，tag 换 `[P16ENS]`。V13 源码 `_read_json`（约 L117-122）同款，tag 换 `[P16V13]`。

STRATEGY_V2 `_lock_read`（约 L961-969），改为（tag `[V2]`）：

```python
def _lock_read():
    """读锁文件 -> dict 或 None(不存在/坏文件)"""
    try:
        if not os.path.exists(LOCK_FILE):
            return None
        with open(LOCK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        if getattr(e, "errno", None) != 2:
            print("[V2][READ-JSON-ERR] %s: %r" % (LOCK_FILE, e))
        return None
```

**行为等价性**：ENOENT 仍静默（每分钟轮询无 orders 文件时不刷屏）；其余异常从静默变为打印一行。用 print 而非 _log：10-14 证明日志文件 IO 本身可能失败，print 到 QMT 控制台是更可靠的通道。

### 修复 B：`_process_orders_cmd`/`_process_cancel_cmd` 前置 exists + 读取失败告警（G2 + ENS 桥）

**原因**：`_read_json` 返回 None 时无法区分「文件不存在」与「文件存在但读失败」。前置 exists 后，文件存在但读失败即可告警。

G2 桥 `_process_orders_cmd`（约 L1524-1541 区域），把开头：

```python
    path = _orders_cmd_path(date)
    data = _read_json(path)
    if data is None:
        return 0
```

改为：

```python
    path = _orders_cmd_path(date)
    if not os.path.exists(path):
        return 0
    data = _read_json(path)
    if data is None:
        # 文件存在但读取失败：10-14 丢单即此路径（静默吞异常无法定位）
        print("[P16G2][CMD-READ-FAIL] %s exists but read failed" % path)
        _write_cmd_alert(date, "orders", path)
        return 0
```

`_process_cancel_cmd`（约 L1556-1585 区域）同构，`"orders"` 换 `"cancel"`，路径函数换 `_cancel_cmd_path(date)`。ENS 桥两函数同构（tag 换 `[P16ENS]`）。

新增辅助函数（放在 `_atomic_write_json` 之后、`_read_json` 之前附近，G2/ENS 各一份，tag 对应）：

```python
def _write_cmd_alert(date, kind, path):
    """命令文件读取失败告警落盘。桥无推送能力（唯一输出通道是 print 到 QMT 日志），
    写 state/alert_<date>.json 供外部监控脚本（qmt_monitor 等）发现。"""
    try:
        _atomic_write_json(
            os.path.join(STATE_DIR, "alert_%s.json" % date),
            {
                "date": date,
                "kind": kind,
                "path": path,
                "time": _now_str(),
                "account_id": ACCOUNT_ID,
            },
        )
    except Exception:
        pass
```

**行为等价性验证**：无文件时——现状=open 抛 ENOENT→None→return 0；新=exists False→return 0。等价。幂等保持（seq<=last 静默跳过逻辑不变）。

### 修复 C：init 时扫描未归档命令文件 `_scan_stale_orders()`（G2 + ENS 桥）

**原因**：10-14 丢单后无任何痕迹（无打印、无告警文件）。启动时扫描让丢单「次日可见」。**只告警不自动补执行**——价格可能已 stale，且项目哲学是「宁可丢单不可重复下单」，用户红线是「勿手动补单，次日由日度管道重推」。

新增函数（G2 桥，建议放在 `_archive_cmd` 约 L263-277 之后）：

```python
def _scan_stale_orders():
    """启动时扫描未归档的 orders/cancel 命令文件（只告警不补执行：
    价格可能已 stale，宁可丢单不可重复。供人工/外部监控处置）。"""
    try:
        if not os.path.isdir(CMD_DIR):
            return
        for name in sorted(os.listdir(CMD_DIR)):
            if not name.endswith(".json"):
                continue
            if "_done_" in name:
                continue
            if not (name.startswith("orders_") or name.startswith("cancel_")):
                continue
            full = os.path.join(CMD_DIR, name)
            print("[P16G2][STALE-CMD] 未归档命令文件: %s" % full)
            try:
                _atomic_write_json(
                    os.path.join(STATE_DIR, "stale_cmd_alert.json"),
                    {"file": full, "time": _now_str(), "account_id": ACCOUNT_ID},
                )
            except Exception:
                pass
    except Exception:
        pass
```

在 `init(C)` 中、`_g_today = _today_str()` 之后调用 `_scan_stale_orders()`。ENS 桥同构，tag 换 `[P16ENS]`。

**预期行为**：修复部署后每次启动会对 10-14 残留的两个 stale 文件告警一次（预期行为）；op 事后手工归档（重命名加 `_done_1` 后缀）后消除。DE 不要动证据文件。

### 修复 D：STRATEGY_V2 买入路径静态分析（只报告，不改逻辑）

**背景**：10-14 09:30 触发补仓但全天零 `[buy]` 输出。当日策略日志缺失（日志写入失败），无法从日志定位。

`_execute_buy`（约 L1465-1513）有 5 个静默 return 点：
- L1469 `code in _pending_orders`
- L1472 `price is None or price <= 0`（`_get_price` 取价失败）
- L1477 `n_to_buy <= 0`（10-14 持仓=0 时不成立，可排除）
- L1483 `amount <= 0`
- L1488 现金兜底后 `amount <= 0`

**任务**：静态分析 `_execute_buy` 的完整调用链（`handlebar` → 补仓触发 → 候选列表来源 → `_execute_buy`），枚举 10-14 场景下所有可能导致零 `[buy]` 的静默 no-op 条件（含候选列表为空的路径——`_execute_buy` 之外、调用侧的静默路径也要查）。对每条路径给出「证据支持度」（有运行时证据支持 / 无法证实 / 已排除）。无法确定根因的部分如实报告，**不改买入逻辑**（买入逻辑改动属高风险，需 op 拍板）。

## §5 输出交付物

报告写到 `D:/QuantLab/research_audit/DE_report_g2_order_loss_20261015.md`，结构：

1. **根因分层**：已证实（有运行时证据）/ 高度疑似（有间接证据）/ 已排除（附排除依据）
2. **10-14 三起静默失败**（G2 桥、ENS 桥、STRATEGY_V2）各自独立分析 + 是否同源
3. **修复 diff 清单**：每个文件每处修改的前后对照（unified diff 格式）
4. **修复 D 静态分析报告**：所有静默 no-op 路径枚举 + 证据支持度
5. **部署验证建议**：op 重建部署后，模拟端 1 交易日复跑应核对的关键日志行清单
6. **遗留风险**：本次修复未覆盖的静默失败点

## §6 硬约束

1. **保持各文件现有编码**：源文件是 UTF-8，build.py 负责 GBK 转换——DE 只改 UTF-8 源文件，不要自己转码
2. **Python 3.6.8 兼容**：禁 f-string / `dict[str,...]` / `list[str]` / `str | None` / `:=` / `match-case`；print 用 `%` 格式化
3. **print 风格与现有 tag 一致**：`[P16G2]` / `[P16ENS]` / `[P16V13]` / `[V2]`
4. **最小改动**：只按 §4 规格改，不顺手重构、不调整无关行
5. **不改业务逻辑**：下单/撤单/pending/风控/评分/原子写/幂等语义全部保持
6. 中文注释可用（源文件现有风格即中英混合）

## §7 完成标志

1. 4 个源码文件按 §4 完成修复（D 项为分析报告不改逻辑）
2. 报告落到 §5 路径
3. 回报：每个文件修改了哪几处（函数名 + 行号）、修复 D 的结论摘要
4. 未触碰 §3 禁止项
