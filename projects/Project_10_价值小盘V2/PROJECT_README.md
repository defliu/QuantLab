# Project 10: 价值小盘V2 微调版 — 立项文档

> 立项日期：2026-08-02
> 审计基线：audit14-17 (2026-08-01~02)
> 状态：**v2.3 已集成退市排雷+buffer，待模拟盘验证**
> **2026-08-06 v2.3（讨论室拆解吸收，诚哥批准）**：并入小市值策略 v2.3 两个已验证组件——
> ① 退市排雷（市值红线缓冲+退市临近剔除，config `universe.delist_screen`，QMT 端 `_delist_hit_qmt`）
> ② buffer 降换手（config `rebalance.buffer_keep=160`，QMT 端 `BUFFER_KEEP_MAX=160`）。
> 研究回测 v2.3 口径：年化 18.0% / 超额全期 +244.9% / 换手 0.80 / 2024+ +50.1%；buffer=0 精确复现存档 +16.2%/+200.9%。
> QMT 单文件已重建（build/strategy_v2.py, GBK, 3.6 兼容），本地验证通过（research/local_validate_v23.py：退市排雷5例+buffer边界6例全过）。
> **2026-08-04 更新（P0-1/P0-2/P1-1 网格验证，run_grid_validation.py）**：
> - hp 因子已去除：纯BP z 1.0（V2a）年化 16.2%/超额+200.9%/2026超额+0.4%，全窗口优于 0.8/0.2 基线
> - EP 替代验证无效（超额-13.8%），Wharton 结论在 A 股小盘不成立
> - ATR%/换手率叠加（V3系列）：回撤略降、2026 全正，但牺牲约 1-2pp 年化，未设为默认
> - P1-1 风控重构实测不达标：分层降仓回撤反而恶化至 -32.7%（基线 15% 一刀清仓断路器更有效），ATR止损增加成本；均保留开关但默认关闭

## 一、策略概述

**核心逻辑**：在30亿以下小盘股中，用行业中性化BP z-score（权重1.0，V2a 纯 BP）选股，等权持有80只，双月换仓。

**一句话**：低估值小盘 + 质量排雷。

## 二、因子体系

| 因子 | 计算 | 权重 | 逻辑 |
|---|---|---|---|
| 行业中性BP z-score | 1/PB，行业内标准化 | 1.0 | 行业内相对便宜（V2a 网格验证最优） |

**质量排雷**：EPS > 0, ROE > 0, 扣非净利润 > 0。

## 三、风控参数

| 参数 | 值 | 说明 |
|---|---|---|
| 个股止损 | 8% | 持仓亏损超8%次日卖出 |
| 组合最大回撤 | 15% | 触发清仓 |
| 最长持有期 | 60天 | 超期强制卖出 |
| 单日最大换手 | 30% | 限制冲击成本 |

## 四、回测表现

### 审计基线（无风控，状态机口径）

| 指标 | 值 |
|---|---|
| 全期累计超额 | +241.6% |
| 全期年化 | 17.9% |
| 2024+超额 | +43.7% |
| 2026超额 | +0.1% |
| 牛市超额 | +35.0% |
| 熊市超额 | +50.2% |
| 震荡超额 | +45.2% |

### 实际回测（含风控）

| 指标 | 值 |
|---|---|
| 全期累计超额 | +174.2% |
| 全期年化 | 15.1% |
| 2024+超额 | +30.4% |
| 2026超额 | -0.2% |
| 平均换手率 | 0.91 |

**风控成本**：约2.8pp年化（止损+持有期截断盈利）。

## 五、文件结构

```
Project_10_价值小盘V2/
├── strategy_v2.py            # QMT单文件源码（UTF-8，开发维护位）
├── build/
│   └── strategy_v2.py        # QMT部署文件（GBK，构建生成，就在本项目内）
├── config/strategy.yaml      # 策略参数配置
├── strategy/
│   ├── scoring.py            # 评分模块（V2Scorer）
│   └── risk.py               # 风控模块（RiskController）
├── runner.py                 # 回测入口
├── build.py                  # 构建：UTF-8 → GBK + 语法检查
├── results/
│   ├── backtest_result.txt   # 回测结果
│   └── risk_state.json       # 风控状态持久化
└── PROJECT_README.md         # 本文件
```

## 六、开发工作流

```bash
# 1. 开发：编辑 strategy_v2.py（UTF-8）
# 2. 构建：语法检查 + 转 GBK → build/strategy_v2.py
python build.py
# 3. QMT加载：直接从 build/strategy_v2.py 启动
```

1. 确保 `D:/QMT_POOL/` 下有预生成CSV：
   - `selected.txt` — 股票池
   - `financial_pe_ttm.csv` / `financial_pb.csv` / `financial_circ_mv.csv` / `financial_industry.csv`
   - `financial_total_mv.csv` — 总市值(退市排雷市值红线, v2.3 新增)
   - `delist_info.csv` — 退市信息 list_status/delist_date(退市排雷, v2.3 新增)
   - `bp_hist_pct.csv` — BP历史分位
3. 账号：67014907

## 七、待办

- [x] 预生成CSV数据管道（`scripts/update_p10_csv.bat`，已注册计划任务 QuantLab_P10_CSV_Pipeline，工作日 18:30；v2.3 起 gen_qmt_csv.py 额外产出 financial_total_mv.csv + delist_info.csv）
- [x] v2.3 退市排雷 + buffer 集成（研究回测+QMT单文件+本地验证，2026-08-06）
- [ ] 模拟盘跑1个交易日验证日志（需 QMT 模拟端在线，人工执行；v2.3 需重点核对 [buffer]/[排雷] 日志行）
- [ ] 与SellStrategyEngine对接（分层卖出风控模块，D:/QuantLab 内集成）
- [ ] 组合层配置（与红利低波等防御策略配比）

## 八、v2.3 变更与验证（2026-08-06，讨论室拆解吸收）

来源：小市值策略 v2.3 SPEC（已归档冻结）拆解吸收，诚哥批准并入 V2a。

**组件 A 退市排雷**（config `universe.delist_screen: true`）：
- 规则：已退市(list_status=D)剔除；距退市日≤30天剔除；主板总市值<7.5亿/创业板·科创板<4.5亿剔除（5亿/3亿红线×1.5缓冲）；北交所不适用市值红线。
- 命中实证：回测全期命中4只真实退市股（烯碳退/长生退/退西水/退博天，3只BP排名第1）本会进入TOP80。
- 实现：runner.py `_delist_hit`+get_candidates；QMT `strategy_v2.py` `_delist_hit_qmt`+换仓过滤。

**组件 B buffer 降换手**（config `rebalance.buffer_keep: 160`）：
- 规则：换仓时持仓按"非禁入候选评分降序"排名，rank≤160 保留、>160 或落出候选卖出，买入区仍为 top-80。
- 消融：年化 16.3%→18.0%、超额全期 +201.6%→+244.9%、换手 0.91→0.80、回撤不变、2024+ +35.2%→+50.1%（否决项全过）。
- buffer_keep=0/80 等价全量重建（复现存档），便于回退对照。

**验证记录**：
- 研究回测三分区校验（research/validate_v23_integration.py）：buffer=0≡80 复现存档 +16.2%/+200.9%，v2.3 口径 +18.0%/+244.9%。
- QMT 本地验证（research/local_validate_v23.py）：退市排雷5用例 + buffer边界6用例全过。
- 构建：build.py 通过（GBK + Python3.6 兼容检查）。
- 迁移修复：runner.py / run_grid_validation.py 的 E:\QuantLab→D:\QuantLab 路径 + 新版 pandas 日期兼容（date→Timestamp）。

**待办**：模拟盘验证（v2.3 行为与旧版不同——换仓会卖出超界持仓，需核对日志与成交）。
