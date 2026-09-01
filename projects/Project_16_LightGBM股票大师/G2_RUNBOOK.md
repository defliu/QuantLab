# G2（大QMT 文件桥）运行手册

> 版本：2026-09-01 搭建 | 适用：Project\_16 G2 版本（账号 **70180771**，大QMT 文件桥）
> 核心原则：**与 V1.3（miniQMT 67014907）完全隔离，绝不混文件/配置/账号/资金池/候选。**

## 一、架构

```
信号层（外部 Python 3.10，零改动）                    执行层（大QMT 内置 Python 3.6）
─────────────────────────────                       ─────────────────────────────
build_g2_daily.py → 43特征快照
deploy_predict_g2.py → data/selections/g2/           strategy_p16_g2_bridge.py
    <date>_g2_top2.csv（红线60/top2）                   （BUILD_TAG，读 cmd → passorder → 回写 state）
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

| 时间    | 步骤              | 命令                                                                                                                     |
| ----- | --------------- | ---------------------------------------------------------------------------------------------------------------------- |
| 09:15 | 候选预生成（g2）       | （TraeWork 任务，deploy\_predict\_g2 候选）                                                                                   |
| 09:25 | 集合竞价预判          | `build_g2_daily.py` + `deploy_predict_g2.py --threshold 60 --top 2 --pool 100` + 竞价/新闻/板块 → 《集合竞价预判》                   |
| 09:50 | **G2 换仓（先卖后买）** | 计划任务 `30344e79`（Paused）；手动：`python rebalance_g2.py --date <YYYYMMDD>`（dry-run 核对）→ `python rebalance_g2.py --live`（写桥） |
| 盘中    | 内置风控（止损/止盈/追盈）  | 桥内置 `_check_risk_signals` 自动执行（70180771）                                                                               |
| 15:45 | **G2 日终对账**     | 计划任务 `4cf3db92`（Paused）；手动：`python reconcile_g2.py --date <YYYYMMDD>`                                                  |
| 任意    | 桥健康             | `python qmt_bridge_client.py alive`                                                                                    |

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
3. **G2 计划任务** ✅ 已建（Paused）：`30344e79` 换仓 09:50 + `4cf3db92` 对账 15:45（工作日），与 V1.3（09:45/15:40）错开 5 分钟；验证后 resume 启用
4. **资金分配登记** ✅ 已登记（2026-09-01）：`capital_allocation.yaml`（QMT\_POOL + QuantLab 双镜像）`g2_bridge` 10 万，`check_capital_allocation.py` 退出码 0 PASS
5. **灰度切换**：G2 小资金并行 → 稳定后切换 → 旧 V1.3（miniQMT 67014907）保留 ≥1 月回滚

## 七、红线提醒

- 双账号并存：67014907（V1.3 miniQMT）/ 70180771（G2 大QMT），**禁止混用**

- G2 只动自己账本的票；账户共享账号（70180771 还有 ATR/V2），**绝不抢占他人资金/持仓**

- 资金池文件改动 → 先 `check_capital_allocation.py` 校验

- 桥产物必须 GBK + `# coding=gbk` + BUILD\_TAG；外部脚本 Python 3.10，内置兼容 3.6

