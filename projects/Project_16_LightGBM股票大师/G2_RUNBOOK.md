# G2（大QMT 文件桥）运行手册

> 版本：2026-09-01 搭建 | 适用：Project\_16 G2 版本（账号 **70180771**，大QMT 文件桥）
> 核心原则：**与 V1.3（miniQMT 67014907）完全隔离，绝不混文件/配置/账号/资金池/候选。**

## 一、架构

```
信号层（外部 Python 3.10，零改动）                    执行层（大QMT 内置 Python 3.6）
─────────────────────────────                       ─────────────────────────────
build_g2_daily.py → 43特征快照
deploy_predict_g2.py → data/selections/g2/           strategy_p16_g2_bridge.py
    <date>_g2_top10.csv（红线60/top10候选池）            （BUILD_TAG，读 cmd → passorder → 回写 state）
        │                                                  ▲
        ▼                                                  │
rebalance_g2.py ──写──> D:/QMT_POOL/g2_bridge/cmd/orders_<date>.json
reconcile_g2.py ──读──> D:/QMT_POOL/g2_bridge/state/*（对账）
```

## 二、与 V1.3 隔离边界（硬约束）

| 维度   | V1.3（miniQMT 67014907）                                            | G2（大QMT 70180771）                                            | 禁止混用                     |
| ---- | ----------------------------------------------------------------- | ------------------------------------------------------------ | ------------------------ |
| 账号   | `qmt_config.ACCOUNT_ID=67014907`                                  | `g2_config.ACCOUNT_ID=70180771`                              | 引用错号=废单                  |
| 执行   | `rebalance_daily.py` → `qmt_trader.py`                            | `rebalance_g2.py` → 桥 `cmd/orders_*.json`                    | G2 绝不调用 qmt\_trader      |
| 资金池  | `data/strategy_capital.json`（67014907 戳）                          | `D:/QMT_POOL/g2_bridge/g2_strategy_capital.json`（70180771 戳） | G2 绝不读 V1.3 资金池          |
| 候选   | `data/selections/<date>_selection_full.csv` / `D_model_top10.csv` | `data/selections/g2/<date>_g2_top2.csv`                      | 不混候选文件                   |
| 持仓归属 | QMT 实时全量（单策略）                                                     | 只认 G2 账本（positions\_cfg + fills FIFO）                        | G2 绝不纳管/卖出他人持仓           |
| 配置   | `qmt_config.py`                                                   | `g2_config.py`                                               | G2 绝不 import qmt\_config |
| 对账   | `reconcile_trades.py`                                             | `reconcile_g2.py`                                            | 独立报告目录                   |

## 三、基础设施清单（已搭建 2026-09-01）

| 文件                                                | 用途                                   |
| ------------------------------------------------- | ------------------------------------ |
| `g2_config.py`                                    | G2 独立配置（账号/桥路径/资金池/参数），不 import V1.3 |
| `D:/QMT_POOL/g2_bridge/g2_strategy_capital.json`  | G2 独立资金池（初始 10 万，account\_id 戳）      |
| `rebalance_g2.py`                                 | G2 每日换仓（先卖后买，dry-run 默认，写桥）          |
| `reconcile_g2.py`                                 | G2 日终对账（fills/pending/持仓差额/孤儿预警）     |
| `qmt_bridge_client.py`（已有）                        | 桥外部客户端（build/wait/alive/write-cfg）   |
| `D:/QMT_POOL/g2_bridge/{cmd,state,meta.json}`（已有） | 桥目录（meta 账号=70180771）                |

## 四、每日流程（G2 版，灰度后启用）

| 时间    | 步骤              | 命令                                                                                                                              |
| ----- | --------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| 09:15 | 候选预生成（g2）       | （TraeWork 任务，deploy\_predict\_g2 候选）                                                                                            |
| 09:25 | 集合竞价预判          | `build_g2_daily.py` + `deploy_predict_g2.py --threshold 60 --top 10 --pool 100` + 竞价/新闻/板块 → 《集合竞价预判》                           |
| 09:50 | **G2 换仓（先卖后买）** | TRAE 自动化任务 `30344e79`（Active，agent 执行：候选检查→dry-run 核对→--live→成交回报卡）；手动：`python rebalance_g2.py --date <YYYYMMDD>`（dry-run 核对）→ `python rebalance_g2.py --live`（写桥） |
| 盘中    | 内置风控（止损/止盈/追盈）  | 桥内置 `_check_risk_signals` 自动执行（70180771）                                                                                        |
| 15:45 | **G2 日终对账**     | TRAE 自动化任务 `4cf3db92`（Active，agent 执行：对账→异常告警→盘后总览卡）；手动：`python reconcile_g2.py --date <YYYYMMDD>` |
| 任意    | 桥健康             | `python qmt_bridge_client.py alive`                                                                                             |

## 五、常用命令

```bash
# 换仓计划（dry-run，不写桥）
python rebalance_g2.py --date 20260902

# 真写桥（先卖后买，慎用；桥心跳异常会拒绝）
python rebalance_g2.py --date 20260902 --live

# 日终对账
python reconcile_g2.py --date 20260902

# 桥健康/等待成交
python qmt_bridge_client.py alive
python qmt_bridge_client.py wait --order-id P16_20260902_0001

# 清仓脚本（2026-09-01 遗留孤儿仓 500 股，T+1 次日可卖）
python clear_orphans_20260902.py
```

## 六、灰度计划（2026-09-01 已搭基础，未启用）

1. **SELL 链路验证**：09-02 清 500 股孤儿仓 → 验证 SELL 成交回写 + 对账闭环
2. **G2 换仓 dry-run 观察**：连续 1-2 交易日 `rebalance_g2.py` dry-run 对照真实持仓
3. **G2 自动化任务** ✅ TRAE 面板已有：`30344e79`（G2换仓 09:50）+ `4cf3db92`（G2日终对账 15:45），均 Active；**勿再建 Windows 计划任务**（会与 TRAE 双跑重复下单，2026-09-03 曾误建后已删）
4. **资金分配登记** ✅ 已登记（2026-09-01）：`capital_allocation.yaml`（QMT\_POOL + QuantLab 双镜像）`g2_bridge` 10 万，`check_capital_allocation.py` 退出码 0 PASS
5. **灰度切换**：G2 小资金并行 → 稳定后切换 → 旧 V1.3（miniQMT 67014907）保留 ≥1 月回滚

## 七、红线提醒

- 双账号并存：67014907（V1.3 miniQMT）/ 70180771（G2 大QMT），**禁止混用**

- G2 只动自己账本的票；账户共享账号（70180771 还有 ATR/V2），**绝不抢占他人资金/持仓**

- 资金池文件改动 → 先 `check_capital_allocation.py` 校验

- 桥产物必须 GBK + `# coding=gbk` + BUILD\_TAG；外部脚本 Python 3.10，内置兼容 3.6

## 八、2026-09-02 P0 修复：status=55 误当废单 → 双倍建仓

> 首日实盘（09-02）暴露：初始 2 笔 BUY（600262 3000 / 300964 800）成交后，positions 显示 600262=6400、300964=1000（多 3400/200 股）。
> 排查链路：桥 seq=1 只发 2 笔 → fills 只回 2 笔 → QMT 委托记录 6 笔（同价 15.77/57.03）→ 拉 QMT 策略日志定位 4 笔为 `[REJECTED-RETRY]` 废单重试。

**根因**：`_check_pending_orders` 1b 分支 `if status == 55` 把「部成活跃态」当「废单」直接 `_do_passorder` 重报剩余量，且**不撤原单**。
55 在模拟端 = 原单仍在挂单继续成交（DIAG 实锤：3000 单 m\_nVolumeTraded=400 / m\_nVolumeTotal=2600 / status=55）。
→ 原单继续成交到 3000 + 重报单（2200/1000/200）也全部成交 = 6400。300964 同理 800+200=1000。

**修复（BUILD\_TAG 161814 起）**：

1. 1b 分支 `status == 55` → `status == 57`（55 部成走 1a/1d 等原单自然成交满；57 真废单才重试）。
2. `_handle_rejected_retry` 加重报前死透确认（对齐 timeout\_retry 纪律）：短轮询反查原单，原单已全成交 → FILLED 收尾；原单仍活跃 → 延后 60s 复查绝不重报；死透（53/54/57 或查不到）才重报 remaining。

**遗留（09-02 当日）**：6400/1000 已 T+1 锁定无法撤销/卖出，明日换仓时按目标持仓差额自然处理（600262/300964 为目标票，多余 3400/200 为超额仓位，卖出时按账本+实际差额核对）。

**部署要求**：QMT 端 `python/STRATEGY_P16_G2_BRIDGE.py` 为 QMT 加密密文，**无法脚本覆盖**，需在 QMT 界面重新加载 `build/strategy_p16_g2_bridge.py`（BUILD\_TAG=20260902-161814），并核对心跳 `build_tag` 与部署生效判据。

## 九、明日（9/3）执行清单（分工：用户=QMT重载，Agent=其余）

> 分工已确认（2026-09-02）：**用户**开盘前 QMT 界面重载新版策略；**Agent** 负责换仓/对账/核对全流程。

1. **开盘前（用户）**：QMT 界面重新加载 `build/strategy_p16_g2_bridge.py`（BUILD\_TAG=20260902-161814）
2. **盘前（Agent）**：`miniqmt venv python premarket_g2_check.py --date 20260902`（核对：①心跳 build\_tag=161814 生效 ②候选 20260902\_g2\_top10.csv 就绪 ③超额持仓提醒 600262 +3400 / 300964 +200 ④数据当日性：增量库/快照=20260902 + F6 估值反推 PASS——2026-09-03 加，防估值再滞后）

   - 注意：rebalance `--date` 传**数据日期=前一交易日**（9/3 换仓 → `--date 20260902`）
3. **09:50 换仓（全自动）**：TRAE 自动化任务 `30344e79` 自动执行（候选检查→dry-run 核对→--live→成交回报卡；当日 600262/300964 持有未满 10 日 → NOOP 不卖不买保持 2 只）
4. **盘中（Agent）**：盯心跳/委托，确认不再出现 `[REJECTED-RETRY]` 双倍建仓（本次 P0 验证点）
5. **15:45 对账（Agent）**：`reconcile_g2.py --date 20260902`，账本 vs positions 差额核对

## 十、实盘对齐回测口径（2026-09-02 拍板：N=10 持有期 + Top10 候选池）

> 背景：回测/前向（`scan_rotate_cost_real.py` N=10、`paper_forward.py --hold 10`）为「买入后持有 10 个交易日」口径，
> 但实盘 `rebalance_g2.py` 原逻辑为「每日对齐 top2、掉出即卖」——回测失去指导意义，用户拍板对齐。

**对齐后的实盘逻辑（rebalance\_g2.py + g2\_config.py）**：

- **候选池**：`deploy_predict_g2.py --top 10` 产出 `_g2_top10.csv`（Top10 池，对齐回测 TOP10），不再只产 top2。

- **卖出（对齐回测 simulate）**：持仓**满 HOLD\_DAYS=10 个交易日到期 → SELL**（不看是否在候选池内；止损/止盈由桥内风控处理）。
  持仓未满 10 日 → 不卖（`[SKIP] 持有未满10日`，继续持有）。

- **买入（对齐回测 while len(hold) < TOP）**：仅当持仓数 < TOP\_N=2 时，从 Top10 池选 total\_new 最高、不在持仓、过红线的补足（避免持仓膨胀）。

**持仓建仓日持久化**：`data/rebalance_g2/g2_hold_dates.json`（`{code: "YYYYMMDD"}`，由 rebalance\_g2 每日更新：新 BUY 记当日、SELL 清仓移除、保护仓保留）。
交易日计数：`is_trade_day.is_trade_day()`（主库日历滞后时走默认交易日+节假日表 fallback，9/25-27 中秋、10/1-7 国庆自动排除）。
**首次初始化（2026-09-02 已做）**：600262/300964 建仓日=20260902，9/3 起持有未满 10 日 → 不卖。

**验证记录**：未满10日不卖 ✅ / 满10日到期SELL+从Top10池补足 ✅ / 持仓不膨胀（保留满2只不买新）✅ / 建仓日持久化 ✅ / Top10 候选生成+消费方改读 ✅ / py_compile 通过 ✅

## 十一、positions_cfg 成本锚自动化（2026-09-02，T-20260902-005）

> 背景：`cmd/positions_cfg_<date>.json` 是桥内止损/止盈/对账的成本锚，此前只能手动生成（9/1 手动一次，9/2 建仓后缺失 → 桥 init 读不到成本）。

**机制**（`gen_positions_cfg_g2.py`）：
- 数据源：G2 持仓 code（`data/rebalance_g2/g2_hold_dates.json`）∩ 账户持仓（`state/positions_<date>.json` 的 `avg_price`=含费成本）
- 输出：`cmd/positions_cfg_<date>.json`（account_id 戳 + cost + vol），只写 G2 自己持仓，绝不纳入他人/孤儿
- 触发：①`rebalance_g2 --live` 换仓成交后自动生成；②`reconcile_g2` 对账前刷新校准（覆盖风控卖出后变化，失败不阻断）
- 安全：空仓/无账户持仓 → SKIP 不写；幂等
- 手动：`python gen_positions_cfg_g2.py --date <YYYYMMDD> [--dry-run]`

**已补 9/2 缺口**：`positions_cfg_20260902.json` = 600262 6400@15.7841 / 300964 1000@57.0989（含费成本 + 实际持仓，与账户实况一致）。

## 十二、F6 估值补字段（2026-09-03，T-20260902-006）

> 背景：增量库仅 10 列 OHLCV，`build_g2_daily.py` 的 `fill_cols` 把主库周更最后值（8/21）map 到增量最新日 → 9/2 估值（pe_ttm/pb/circ_mv/turnover_rate）全用 8/21 旧值，滞后 11 天。

**修复**：增量日逐日用主库每股慢变量 × 当日真实 close 反推当日估值：
- `pe_ttm = close / EPS_ttm`，EPS_ttm = close_主库 / pe_ttm_主库
- `pb = close / BVPS`，BVPS = close_主库 / pb_主库
- `dv_ttm = DPS / close`，DPS = dv_ttm_主库 × close_主库
- `circ_mv(万元) = float_share(万股) × close(元)`
- `turnover_rate(%) = vol(手) / float_share(万股)`
- `pct_chg = close/preClose - 1`（增量自带 preClose，真实值）

**口径验证**（主库 daily 交叉验证 d0→d1）：pe/pb/dv/mv 中位误差 0.00%；turnover 用 float_share（流通股本）口径修正后 3 只全吻合。重建快照后 5 只抽样反推 pe_ttm 逐位一致、turn_ma5 精确 0.0001。

**注意**：主库读取需带 `float_share`（RAW_COLS 不含，已在 build_g2_daily.py 单独加列读取）。`volume_ratio` 口径难复现（主库≈多日均量加权），保持主库前向填充（滞后影响小）。
