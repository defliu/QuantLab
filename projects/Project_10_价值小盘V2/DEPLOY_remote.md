# Project 10 · 价值小盘V2 — 远程服务器部署手册

> 版本：v2.3（退市排雷 + buffer）
> 创建：2026-08-06
> 适用：将 QMT 实盘策略 + 数据管道部署到远程服务器（非 E:/ 本机）

---

## 〇、部署原则

远程服务器只需 **「策略单文件 + 运行依赖CSV + 状态持久化」** 三类内容即可支撑 QMT 实盘；
**不要**拷贝研究回测脚本（runner / results / __pycache__ / 数据产物 E:/astock 全量）。

---

## 一、必须部署的文件清单

### 1. QMT 策略单文件

| 文件 | 说明 |
|------|------|
| `build/strategy_v2.py` | **GBK 单文件**，QMT 直接加载此文件（不是源码 `strategy_v2.py`，源码是 UTF-8 开发位） |

> 构建命令：在项目目录 `python build.py`（语法检查 + UTF-8 → GBK 转码）。改源码后必须重新构建，不要手工改产物。

### 2. 运行依赖数据 CSV（部署到 `D:\QMT_POOL\`）

| 文件 | 用途 | 产出脚本 |
|------|------|----------|
| `financial_pb.csv` | PB → BP 评分 | gen_qmt_csv.py |
| `financial_pe_ttm.csv` | PE（备用） | gen_qmt_csv.py |
| `financial_circ_mv.csv` | 流通市值 | gen_qmt_csv.py |
| `financial_total_mv.csv` | 总市值（退市市值红线） | gen_qmt_csv.py |
| `industry_map.csv` | 行业（BP 中性化） | gen_qmt_csv.py |
| `bp_hist_pct.csv` | BP 历史分位 | gen_qmt_csv.py |
| `delist_info.csv` | 退市排雷（list_status / delist_date） | gen_qmt_csv.py |
| `selected.txt` | 股票池 fallback | gen_qmt_csv.py |

> **新增资金池子目录约定**：这些 CSV 必须放在 `D:\QMT_POOL\`（代码硬编码 `DATA_DIR`），路径不可变，否则静默 fallback 失败。

### 3. 状态持久化文件（重要）

| 文件 | 说明 |
|------|------|
| `v2_holdings_state.json` | 策略运行态（持仓 / 成本 / 净值 / 今日订单）。**换机或重启必须带上**，否则持仓归零、净值重置 |

> 该文件位于 `D:\QMT_POOL\v2_holdings_state.json`（`STATE_FILE` 硬编码）。首次部署无此文件 → 从 `CAPITAL_INIT=10万` 冷启动。

### 4. 资金分配表（资产管理/合规，非 QMT 运行时依赖）

| 文件 | 说明 |
|------|------|
| `config/capital_allocation.yaml` | 资金分配**单一事实源**：`Σ capital_base ≤ total_capital`（账户总额硬约束） |
| `config/value_smallcap_v2_config.yaml` | 本策略部署配置（`capital_base=10万` 须与 QMT 内硬编码 `CAPITAL_INIT=100000` 一致，校验器交叉核对） |
| `scripts/check_capital_allocation.py` | 资金分配校验器（改表后必须跑，退出码 0 才许部署） |

> **QMT 运行时不需要** —— `strategy_v2.py` 硬编码 `CAPITAL_INIT` / `ACCOUNT_ID`，不读分配表。
> 但**资产管理/对账层面需要**：若远程与账户内其他策略共账，须保证此表约束一致；建议随部署带上这 3 个小文件，保持单一事实源。当前 `value_smallcap_v2` 已在表中锁定 10 万，校验 PASS。。

---

## 二、CSV 数据的每日刷新管道

远程服务器若要独立产新数据，需要以下脚本（含数据源依赖）：

```
scripts/update_data.py        # 更新 E:/astock 基础数据
scripts/gen_qmt_csv.py        # 从 E:/astock 生成上述 8 个 CSV
scripts/update_p10_csv.bat    # 串接脚本（计划任务，工作日 18:30）
```

### 数据源路径依赖（关键）

`gen_qmt_csv.py` 内硬编码：
- 行情源：`E:\astock\daily\stock_daily.parquet`
- 基础信息：`E:\astock\basic\stock_basic.parquet`
- 输出目录：`D:\QMT_POOL\`

若远程数据布局不同，需先在本地改 `gen_qmt_csv.py` 源路径再迁移；否则直接拷贝已生成的 CSV 即可（不用带整份 E:/astock）。

---

## 三、账密与红线（必须遵守）

| 项 | 值 / 规则 |
|----|-----------|
| 账号 ID | `70180771`（硬编码） |
| 初始本金 | `CAPITAL_INIT = 100000`（专属虚拟子账户，与账户其他策略隔离） |
| 资金分配 | 先改 `config/capital_allocation.yaml` + 跑 `scripts/check_capital_allocation.py`（退出码 0 才算） |
| 编码 | QMT 运行产物必须 GBK 且首行 `# coding=gbk` |
| Python | QMT 环境按 3.6.8 兼容（无 f-string / `dict[str]` / walrus） |
| 下单 | 一律复用 `broker/qmt_order.py` 惯例，11 参数 `passorder(..., C)` |

---

## 四、部署步骤（新服务器）

1. **建目录**：`D:\QMT_POOL\`（必须存在）
2. **拷贝策略**：`build/strategy_v2.py` → QMT 加载位（各项目 build 目录自包含）
3. **拷贝数据**：8 个 CSV → `D:\QMT_POOL\`
4. **拷贝状态**：若迁移已有持仓，`v2_holdings_state.json` → `D:\QMT_POOL\`；全新部署则跳过
5. **校验编码**：确认 `build/strategy_v2.py` 首行为 `# coding=gbk`、GBK 编码、Python 3.6 语法兼容、无 MOCK 残留
6. **QMT 加载**：从 `build/strategy_v2.py` 启动，打开 QMT 模拟端核对初始化日志
7. **本地验证**：用 `broker/local_context.py` 连本地 miniQMT 快速验证选股管线（选股候选数、排序、fail-open）

---

## 五、部署后验证清单

- [ ] `build/strategy_v2.py` 首行 `# coding=gbk`
- [ ] 文件名、编码（GBK）、Python 3.6 语法检查通过
- [ ] `D:\QMT_POOL\` 下 8 个 CSV 存在且非空
- [ ] `v2_holdings_state.json` 存在（如需延续持仓）
- [ ] 模拟盘跑 1 个交易日，核对 `[buffer]` / `[排雷]` / `[rebal]` / `[hb]` 日志行
- [ ] `check_capital_allocation.py` 退出码 0

---

## 六、回滚 / 恢复

- 策略逻辑回退：重新构建旧版本源码 → `python build.py` → 覆盖 `build/strategy_v2.py`
- 数据回退：`D:\QMT_POOL\` 下 CSV 为最新快照，可备份覆盖
- 持仓恢复：任意机器复制 `v2_holdings_state.json` 到 `D:\QMT_POOL\` 即可

---

*2026-08-06 | Project_10 v2.3 | 部署手册*