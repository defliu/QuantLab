# DE 报告：G2/ENS 桥 2026-10-14 丢单排查与加固修复

- 执行：DE（DevEco Code CLI），依据 `research_audit/DE_taskbook_g2_order_loss_20261015.md`
- 日期：2026-10-15
- 结论摘要：**三起静默失败同源**——QMT 内置 Python 3.6 运行时进入「打开已存在文件被拒」（`PermissionError: Foribdden FileIO`）状态，读路径全阻断、写路径（tmp+os.replace）不受影响；叠加各策略的静默吞异常点（桥 `_read_json`、V2 部署版静默 return）导致全天零可见性。修复 A/B/C 已按 §4 落地 4 个源码文件，修复 D 静态分析完成（只报告未改逻辑）。

---

## 1. 根因分层

### 1.1 已证实（有运行时直接证据）

| # | 事实 | 证据 |
|---|---|---|
| F1 | QMT python36 运行时存在「打开已存在文件抛 `PermissionError: Foribdden FileIO`」的故障态 | 第一实例 `userdata\log\XtClient_FormulaOutput_20261014.log` 23:46:13 / 23:46:44 / 23:59:03 三次错误明文：`[state] [!] 状态载入失败... PermissionError: Foribdden FileIO`（`_load_state` L829）、`[lock error] 锁写入失败: Foribdden FileIO`、`[log error] 日志写入失败: Foribdden FileIO` |
| F2 | 第二实例读阻断在 **10-13 20:53:47 前已生效** | 10-13 晚间重启 INIT `positions=0` 且无「外部成本表 N 条」行——`positions_cfg_20261013.json`（15:46 生成、account_id 匹配）已存在却读取失败；对照 10-13 08:52 INIT 成功回退读 `positions_cfg_20261012.json` 打印「外部成本表 3 条」positions=3（ENS 同构 4 条/4） |
| F3 | 第二实例读阻断贯穿 10-14 全天 | 10-14 08:52:26 两桥 INIT 均 `positions=0`、无成本表行；orders 09:34:55（ENS）/ 09:55:56（G2）写入后 heart `last_cmd_seq_processed=0`、orders 未归档、无 fills、无 `[CMD]` 打印 |
| F4 | 写路径不受影响 | 两桥 `state/asset_20261014.json`、`positions_20261014.json` 15:00:06 正常写出；heart 20:55/20:57 正常写——`_atomic_write_json` 走 tmp 新建 + `os.replace`，不触发「打开已存在文件」 |
| F5 | 第一实例读/追加阻断自 10-14 08:50:53 前生效 | `strategy_log_v2.txt` 零 10-14 行（`_log` 的 print 通道正常——FormulaOutput 有全部 hb 行，文件 append 通道全失败）；08:52 / 09:15 init `持仓=0 现金=100000`，对照 10-13 收盘 `持仓=31 现金=69806` = `_load_state` 被拒后静默空仓起步 |
| F6 | 10-13 白天两实例读路径均正常（对照日） | G2 09:31:30 RSK 止损成功、ENS 10:08:12 成功消费 orders seq=1、V2 10:34 补仓 + 10:35 起正常买入 |

### 1.2 高度疑似（强间接证据，无直接日志）

- **V2 评分 CSV 载入全空 → scores 空 → 部署版静默 return**（详见 §4 修复 D）。间接证据：与 F1/F5 同实例同时段同 IO 故障态；8 个 `financial_*.csv` + `bp_hist_pct.csv` 等 12 个候选 CSV 全部存在于 `D:/QMT_POOL`（LastWrite 10-13 05:55），`os.path.exists` 通过后 `open()` 被拒 → 5 个载入函数全部 `except: pass` 返回空。因部署 build 20261013-144730 在该路径零打印，无直接日志——这正是本次修复要消除的盲区。
- **Foribdden FileIO 的触发条件**：两实例（独立 QMT 客户端进程）在 10-13 晚间~10-14 晨间先后进入同一状态，疑似 QMT 客户端层面机制（字符串内置于 `bin.x64\python36.dll`），具体触发条件本次无法确证。

### 1.3 已排除（依据任务书 §1 + 本次复核一致）

权限 ACL / 配置漂移 / 路径常量 / 桥存活 / seq 幂等跳过 / 写侧 / 盘后重启门控——复核均与任务书排除依据一致，不再重复。补充排除：QMT 连接与行情推送（hb 心跳、行情时间戳全天正常）。

---

## 2. 10-14 三起静默失败独立分析 + 同源性

### 2.1 G2 桥（70180771，D:/QMT_POOL/g2_bridge/）

- 08:52:26 INIT：`_load_positions_cfg` 回退读 `positions_cfg_20261013.json` 被拒（F2/F3）→ positions=0（风控成本底丢失，潜在次生风险）。
- 09:55:56 外部写入 `cmd/orders_20261014.json`（seq=1：SELL 000607.SZ 200@4.13 + BUY 000960.SZ 1100@28.01）。
- 之后每 tick `_process_orders_cmd` → `_read_json(orders_path)` → `open(已存在文件,"r")` 被拒 → **`except Exception: return None` 静默** → `return 0`。零 `[CMD]`、零错误打印。
- 结果：heart seq=0、orders 未归档（无 `_done_1`）、无 `fills_20261014.json`、账户持仓未变（000607.SZ 仍 7700 股、无 000960.SZ）。
- 定性：**读路径被 Foribdden FileIO 阻断 + `_read_json` 静默吞异常**。已证实（F1-F4 链条闭合）。

### 2.2 ENS 桥（同账号，D:/QMT_POOL/p16_ensemble_bridge/）

- 同构：09:34:55 写入 orders seq=1（SELL 600691.SH 100 + BUY 002762.SZ 2000），全天未被消费，症状逐项同 G2。已证实。

### 2.3 STRATEGY_V2（67014907，第一实例）

- 09:15:03 部署 build **20261013-144730**（10-13 构建：无单实例锁、无 IO 预检、无「评分候选为空/宇宙筛选后候选为空」非静默日志——git 证实这些为 10-14 事故后加入工作区、未提交、不在该 build）。
- 08:52/09:15 init：`_load_state` 被拒静默空仓（F5）→ 持仓=0 触发 09:30:00 补仓（`[rebal] 持仓比例=0.0% < 60%` 打印正常）。
- 09:30:00 换仓块：`_last_rebal_month=10` 先消费 → 5 个 CSV 载入函数全部被拒返回空 → `_score_stocks` 宇宙筛选 `fin_data.get(code,{})` → circ_mv=0 → 全池 continue → `bp_vals={}` → `scores={}` → **部署版静默 `return`**。
- 之后：`current_month != _last_rebal_month` 不再成立（10 月已消费），补仓当月永不再触发 → 全天零 `[buy]`（对照 10-13 同流程 10:35 正常买入）。
- 定性：根因链条两端已证实（IO 故障态 F1/F5 + 补仓触发与零买入观察），中间环节（CSV 载入失败 → scores 空）为高度疑似的必然推论。**与两桥同源**（同一 python36.dll Foribdden FileIO），差异仅在暴露面：桥死在 `_read_json`，V2 死在 CSV 载入 + 部署版静默 return。

### 2.4 同源结论

三起失败 = 同一 QMT 运行时 IO 故障态 × 三个不同的静默吞异常点。修复策略因此一致：**把静默失败变成可见（print + 告警落盘）**，而非改变业务行为（宁可丢单不可重复下单的幂等语义全部保持）。

---

## 3. 修复 diff 清单

改动范围：仅任务书 §3 允许的 4 个源码文件；全部通过 QMT 内置 Python 3.6.8 编译检查（`bin.x64\pythonw.exe` 显式 UTF-8 读 + compile，ALL-OK）；未触碰 build/ 产物、未运行 build.py、未做任何 git 操作。

### 3.1 G2 桥 `strategy_p16_g2_bridge_src.py`（6 处）

**(1) Fix A — `_read_json` 显式错误日志**（原 L126-131 → 现 L144）：

```diff
 def _read_json(path):
     try:
         with open(path, encoding="utf-8") as f:
             return json.load(f)
-    except Exception:
+    except Exception as e:
+        # errno==2 (ENOENT) 是可选文件不存在的正常情况，保持静默；
+        # 其余异常（权限/解码/IO层）显式打印——10-14 丢单根因不可见即源于此处的静默吞异常
+        if getattr(e, "errno", None) == 2:
+            return None
+        print("[P16G2][READ-JSON-ERR] %s: %r" % (path, e))
         return None
```

**(2) Fix B — 新增 `_write_cmd_alert`**（置于 `_atomic_write_json` 之后、`_read_json` 之前，现 L126-140）：

```diff
+def _write_cmd_alert(date, kind, path):
+    """命令文件读取失败告警落盘。桥无推送能力（唯一输出通道是 print 到 QMT 日志），
+    写 state/alert_<date>.json 供外部监控脚本（qmt_monitor 等）发现。"""
+    try:
+        _atomic_write_json(
+            os.path.join(STATE_DIR, "alert_%s.json" % date),
+            {
+                "date": date,
+                "kind": kind,
+                "path": path,
+                "time": _now_str(),
+                "account_id": ACCOUNT_ID,
+            },
+        )
+    except Exception:
+        pass
```

**(3) Fix C — 新增 `_scan_stale_orders`**（置于 `_archive_cmd` 之后，现 L303-323）：

```diff
+def _scan_stale_orders():
+    """启动时扫描未归档的 orders/cancel 命令文件（只告警不补执行：
+    价格可能已 stale，宁可丢单不可重复。供人工/外部监控处置）。"""
+    try:
+        if not os.path.isdir(CMD_DIR):
+            return
+        for name in sorted(os.listdir(CMD_DIR)):
+            if not name.endswith(".json"):
+                continue
+            if "_done_" in name:
+                continue
+            if not (name.startswith("orders_") or name.startswith("cancel_")):
+                continue
+            full = os.path.join(CMD_DIR, name)
+            print("[P16G2][STALE-CMD] 未归档命令文件: %s" % full)
+            try:
+                _atomic_write_json(
+                    os.path.join(STATE_DIR, "stale_cmd_alert.json"),
+                    {"file": full, "time": _now_str(), "account_id": ACCOUNT_ID},
+                )
+            except Exception:
+                pass
+    except Exception:
+        pass
```

**(4) Fix B — `_process_orders_cmd` 前置 exists + 读取失败告警**（现 L1577-1585）：

```diff
     global _g_last_cmd_seq
     path = _orders_cmd_path(date)
+    if not os.path.exists(path):
+        return 0
     data = _read_json(path)
     if data is None:
+        # 文件存在但读取失败：10-14 丢单即此路径（静默吞异常无法定位）
+        print("[P16G2][CMD-READ-FAIL] %s exists but read failed" % path)
+        _write_cmd_alert(date, "orders", path)
         return 0
```

**(5) Fix B — `_process_cancel_cmd` 同构**（现 L1614-1622，`"orders"`→`"cancel"`、路径函数换 `_cancel_cmd_path(date)`，print 同款 `[P16G2][CMD-READ-FAIL]`）。

**(6) Fix C — `init(C)` 启动扫描**（现 L1658）：

```diff
     _g_today = _today_str()
+    _scan_stale_orders()
     # 读取上次已处理seq + 恢复pending孤儿单 + 恢复risk_sold
     heart = _read_json(_heart_path(_g_today))
```

### 3.2 ENS 桥 `strategy_p16_ens_bridge_src.py`（6 处）

与 3.1 逐字同构，tag 换 `[P16ENS]`：`_read_json`（原 L124-129 → 现 L142）、`_write_cmd_alert`（现 L124）、`_scan_stale_orders`（现 L301）、`_process_orders_cmd` 前置告警（现 L1582-1590）、`_process_cancel_cmd` 同构（现 L1621-1625）、`init` 扫描调用（现 L1661）。

### 3.3 V13 风控源 `strategy_p16_v13_risk_src.py`（1 处）

Fix A — `_read_json`（原 L117-122 → 现 L117-127），同 3.1(1)，tag 换 `[P16V13]`。

### 3.4 STRATEGY_V2 `strategy_v2.py`（1 处，仅 Fix A）

`_lock_read`（原 L961-969 → 现 L961-975）：

```diff
 def _lock_read():
     """读锁文件 -> dict 或 None(不存在/坏文件)"""
     try:
         if not os.path.exists(LOCK_FILE):
             return None
         with open(LOCK_FILE, "r", encoding="utf-8") as f:
             return json.load(f)
-    except Exception:
+    except Exception as e:
+        # errno==2 (ENOENT) 是锁文件不存在的正常情况，保持静默；
+        # 其余异常（权限/解码/IO层）显式打印——10-14 Foribdden FileIO 静默不可见即源于此
+        if getattr(e, "errno", None) != 2:
+            print("[V2][READ-JSON-ERR] %s: %r" % (LOCK_FILE, e))
         return None
```

### 3.5 行为等价性说明（全文件）

- ENOENT（文件不存在）保持静默：每分钟轮询无 orders 文件时不刷屏，与原行为逐 tick 等价。
- 幂等语义不变：`seq <= _g_last_cmd_seq` 静默跳过逻辑、heart 先写后执行、`_archive_cmd_if_same` 防重归档，全部未动。
- 只增不改：无任何下单/撤单/pending/风控/评分/原子写逻辑变更；无 f-string（全 `%` 格式化），Python 3.6.8 兼容。

---

## 4. 修复 D 静态分析报告（STRATEGY_V2，只报告未改逻辑）

### 4.1 `_execute_buy`（L1465-1513）5 个静默 return 点 × 10-14 场景证据支持度

| 静默点 | 条件 | 10-14 场景判定 | 证据支持度 |
|---|---|---|---|
| L1469 | `code in _pending_orders` | state 载入被拒 → `_pending_orders={}`，不可能命中 | **已排除** |
| L1471-1473 | `price is None or price<=0`（`_get_price` 走 `C.get_market_data_ex` API，非文件 IO） | 09:30:00 开盘首 tick 数据未就绪理论上可全部 None；但 10-13 同路径 10:35 正常取价，且该路径可达的前提（scores 非空）本身高度不可能成立 | 无法证实（从属上游） |
| L1477 | `n_to_buy <= 0` | 持仓=0 → n_to_buy=30 | **已排除** |
| L1483 | `amount <= 0` | per_stock≈3333 元，需全部候选股价 >33.3 元才触发，价值小盘 BP 池不可能 | **已排除** |
| L1486-1489 | 现金兜底后 `amount<=0` | 同上 | **已排除** |

### 4.2 调用链（handlebar → 补仓 → 候选 → `_execute_buy`）全部静默 no-op 路径

| 路径 | 位置 | 10-14 判定 | 证据支持度 |
|---|---|---|---|
| 被动实例直接 return | handlebar L1109 | 09:30 有 `[rebal]` 打印 = 非被动（部署版无锁机制，该守卫当时不存在） | **已排除** |
| 时间过滤 | L1128-1134 | 09:30:00 满足 `is_trading_time` | **已排除** |
| 补仓保护 | L1167-1168 | `reconciled_today=False`（当日未对账）、月未消费 | 正常触发（已证实：有 `[rebal]` 打印） |
| **scores 空 → 静默 return** | L1249-1252（部署版无 `[!]` 留痕日志） | 5 个 CSV 载入函数被拒全空 → `fin_data={}` → `_score_stocks` L895-913 宇宙筛选全 continue → `bp_vals={}` → L915 `return scores`（空） | **高度疑似（主根因）**：同实例同时段 state/log 被拒有直接证据（F1/F5），CSV 均存在（exists 通过、open 被拒）；无直接日志因部署版静默 |
| 退市排雷后空 → return | L1259-1261 | 从属于 scores 非空，被上游覆盖 | 无法独立证实（从属） |
| target 为空 | L1293-1298 | 从属于 scores 空 | 高度疑似（从属） |
| 买入循环全部 `[buy skip]`（涨停/停牌） | L1303-1308 | 零 `[buy skip]` 打印 → 循环未迭代到候选（30 只全部涨停/停牌不可能） | **已排除** |
| pool 为空 | `_load_pool` L149-165 | 主路径 QMT API `get_stock_list_in_sector` 非文件 IO，C 对象全天可用（hb 正常）；fallback CSV 被拒但主路径优先 | 低概率（无法完全排除 API 层故障） |
| 换仓块内未捕获异常 | L1238-1313 无整体 try/except | 若抛异常 QMT 会打堆栈到 FormulaOutput，10-14 全天零异常输出 | **已排除** |

### 4.3 结论

10-14 V2 零 `[buy]` 根因链：**Foribdden FileIO → `_load_state` 被拒静默空仓（持仓 31→0，触发补仓）→ 评分 CSV 载入全空 → `scores={}` → 部署版（20261013-144730）静默 return，且 `_last_rebal_month` 已在换仓块开头被消费 → 10 月补仓条件永不再触发 → 全天/全月零买入**。两端已证实、中间高度疑似。工作区现源码已含三层非静默留痕（`[state] [!] 状态载入失败`、`[score] [!] 宇宙筛选后候选为空`、`[rebal] [!] 评分候选为空`）+ IO 预检转被动，部署后同类故障将可见且防盲跑。本次未改任何买入逻辑（§4 规格）。

---

## 5. 部署验证建议（op 重建部署后模拟端 1 交易日核对清单）

1. **启动时（一次性，预期对 10-14 残留告警）**：
   - `[P16G2][STALE-CMD] 未归档命令文件: D:/QMT_POOL/g2_bridge/cmd/orders_20261014.json`
   - `[P16ENS][STALE-CMD] 未归档命令文件: D:/QMT_POOL/p16_ensemble_bridge/cmd/orders_20261014.json`
   - 两桥 `state/stale_cmd_alert.json` 生成。人工归档残留文件（重命名加 `_done_1` 后缀，由 op 处置，DE 未动证据文件）后，下次启动不再告警。
2. **正常消费路径**（外部管道推 orders 当日）：`[P16G2][CMD] 读取orders seq=1 (上次=0)` → 后续 `[P16G2][INIT]`/`[FILLED]` 正常 → orders 归档为 `orders_<date>_done_1.json` → `heart_<date>.json` 的 `last_cmd_seq_processed=1` → `state/fills_<date>.json` 出现。ENS 同构。
3. **IO 故障路径（若 Foribdden FileIO 复发）**：每分钟可见 `[P16G2][READ-JSON-ERR] <path>: PermissionError(...)` + `[P16G2][CMD-READ-FAIL] <path> exists but read failed` + `state/alert_<date>.json` 生成（ENS 同构）。
4. **V2**：正常时 `[init] 策略初始化完成, 持仓=...`；IO 复发时 `[preflight] [!]` 拦截转被动、`[state] [!] 状态载入失败`、`[V2][READ-JSON-ERR]`（本次新增）可见。
5. **监控接线建议**：`qmt_monitor` / `qmt_card` 增加对两桥 `state/alert_*.json`、`state/stale_cmd_alert.json` 的扫描项；盘后检查 `cmd/` 下除当日未消费时段外无未归档 `orders_*`/`cancel_*`。

---

## 6. 遗留风险（本次修复未覆盖的静默失败点）

1. **V2 的 5 个 CSV 载入函数仍 `except: pass`**（`_load_financial`/`_load_bp_history`/`_load_industry_map`/`_load_delist_info`/`_load_st_list`）：scores 空可由现源码的 `[score] [!]` 留痕，但「哪个 CSV 失败」不可分辨。建议后续加载入计数日志（超出本次 §4 范围，未改）。
2. **Foribdden FileIO 触发条件未确证**：修复只解决可见性，不阻止复发。两实例同日进入同一状态，疑似 QMT 客户端层机制（字符串内置于 python36.dll）；建议观察 QMT 客户端升级/重装后是否再现，并保持 §5.5 监控接线。
3. **V2 日志文件通道失败无外部告警**：`[log error]` 仅 print 到 QMT 窗口，无人盯则丢失（桥有 alert 落盘，V2 未加，超出本次范围）。
4. **`_scan_stale_orders` 仅启动时扫描一次**：日内产生的丢单（10-14 场景即启动时文件尚不存在）靠 `CMD-READ-FAIL`/`READ-JSON-ERR` 分钟级打印兜底，外部监控需接线才闭环。
5. **`_archive_cmd_if_same` 内 `_read_json` 失败会静默跳过归档**：若 IO 在「消费后、归档前」故障，orders 滞留由次日 STALE-CMD 兜底告警；当日重复处理风险受 heart 先写的 seq 幂等保护，可接受。
6. **两桥 10-14 的 positions=0 次生风险已实际发生**：`_load_positions_cfg`/`peak.json` 读取被拒 → 风控成本底/追盈峰值未恢复（G2 10-14 无 RSK 评估成本可用，万幸当日无触发）。修复 A 部署后此类失败可见，但风控评估质量依赖外部重推 cfg。
7. **strategy_v2.py 源文件带 `# coding=gbk` cookie 但实为 UTF-8**：直接用 Python 解析该源文件会 GBK 解码报错（build.py 显式 UTF-8 读不受影响，生产无影响）；历史遗留，本次未在授权范围内修正，建议 op 后续清理。

---

## 7. 完成标志核对（§7）

1. ✅ 4 个源码文件按 §4 完成修复（D 项为分析报告未改逻辑）：G2 6 处 / ENS 6 处 / V13 1 处 / V2 1 处
2. ✅ 本报告落盘 `research_audit/DE_report_g2_order_loss_20261015.md`
3. 修改清单与修复 D 结论：见 §3（函数名+行号）与 §4
4. ✅ 未触碰禁止项：未改 build/ 产物、未运行 build.py、无 git 操作、未动 10-14 运行时证据文件、未改下单/撤单/pending/风控/评分/原子写逻辑
