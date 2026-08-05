# QuantLab — A股量化研究与实盘框架

> 项目根目录：E:\QuantLab
> 创建时间：2026-07-22 ｜ 本文档更新：2026-08-04
> 开发规范见 `AGENTS.md`，任务看板见 `全局控制台.md`，踩坑记录见 `全局复利与踩坑日志.md`
> 知识中心见 `.qoder/repowiki/`（模块知识卡 + 体系化文档，**接手者从这里开始**）

## 项目定位

A 股量化全链路框架：因子研究 → 策略构建 → 回测验证 → 风控 → miniQMT 实盘。

- 实盘策略工程已收拢至本仓库：各项目 `build/` 子目录自包含 QMT 单文件（实盘策略构建 + 分层卖出风控）
- 共用文件交换目录 `D:/QMT_POOL/`（预生成 CSV / 持仓 / 净值等）
- 主数据源 `E:/astock/`：买断离线 parquet（日线 2009 起、财务全量 PIT 字段齐全）

## 实际目录结构（2026-08-04）

```
QuantLab/
├── config/
│   ├── settings.yaml           # 全局配置（数据源/回测/因子预处理/股票池）
│   └── trading_config.yaml     # 实盘配置（账号 67014907/风控阈值/调度）
│
├── data/                       # 数据读取层（鸭子类型 4 方法接口）
│   ├── feed.py                 # DataFeed 分发器
│   ├── astock_reader.py        # astock parquet 引擎
│   ├── astock_finance_reader.py# 财务 PIT 读取（ann_date 防前视）
│   ├── duckdb_reader.py        # DuckDB 引擎
│   ├── benchmark_reader.py / industry_map.py / universe.py
│   └── cache/                  # 缓存（过期 1 天）
│
├── factors/                    # 因子库（FactorBase: winsorize→zscore→rank）
│   ├── base.py / engine.py
│   ├── atr.py / roe.py / volatility.py / vwap_volume_corr.py
│
├── backtest/                   # 回测引擎
│   ├── engine.py / execution.py / portfolio.py / rebalance.py
│   ├── analyzer.py / report.py / hashing.py
│
├── broker/                     # 券商接口层
│   ├── qmt_builder.py          # QMT GBK 单文件策略生成器
│   └── local_context.py        # miniQMT 本地验证适配器（LocalContext）
│
├── strategy/                   # 策略库（ATR低波等 + registry/schedule）
│
├── projects/                   # 策略项目隔离目录（见下表）
│   ├── Project_01~10           # 各策略独立目录（strategy/config/results/build）
│   └── verification/           # 验证框架（B-1~B-8 引擎 + D-1~D-7 鲁棒性）
│
├── research_audit/             # 审计脚本与结果（audit12~17）
├── specs/                      # 需求/设计/评估文档
├── scripts/                    # 数据更新、CSV 生成、QMT 测试脚本
├── reports/                    # 回测报告产物
├── main.py                     # 回测主入口（默认 Project_01）
└── test_connection*.py         # QMT 连接测试
```

规划中（空占位，填充前先确认架构）：`risk/`、`optimization/`、`dashboard/`、`sentiment/`

## 策略项目状态（2026-08-04）

| 编号 | 名称 | 年化 | 回撤 | 状态 |
|---|---|---|---|---|
| 01 | 多因子IC小盘Alpha | 10.1%（审计后真实口径） | -24.4% | 已审计：超额仅 BP +6.1%/年，2024+ 转负，**不建议实盘** |
| 02 | 双均线趋势 | -8.9% | -48.4% | 失效 |
| 03 | PEAD盈余漂移 | -7.8% | — | 不推荐 |
| 04 | ML多因子 | -21.4% | -65.8% | 已淘汰 |
| 05 | 红利低波 | 3.7% | -11.3% | 推荐（防御） |
| 06 | 质量小市值 | 4.1% | -20.1% | 可选 |
| 07 | 低换手反转 | -6.9% | -39.0% | 不推荐 |
| 08 | 指数增强 | 0.4% | -8.2% | 防御 |
| 09 | 组合策略 | — | — | 70%红利+30%小盘 |
| **10** | **价值小盘V2（微调版）** | 含风控 15.1%/年，超额+174.2% | -29.1% | **主力候选：待网格验证结论 + 模拟盘验证** |

## 快速开始

```bash
# 回测主入口（默认 Project_01）
python main.py

# Project_10 回测
cd projects/Project_10_价值小盘V2
python runner.py                 # 状态机口径 + 风控
python run_grid_validation.py    # P0-1/P0-2/P1-1 配比网格验证

# 本地 miniQMT 快速验证（改策略后必跑，约 10 秒）
C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe <项目>/local_validate.py

# QMT 预生成 CSV（D:/QMT_POOL/，供 QMT 策略消费）
python scripts/gen_qmt_csv.py
```

## 环境

| 用途 | 版本 |
|---|---|
| 研究环境 | Python 3.11（pandas>=2.0 / numpy / pyyaml / pyarrow） |
| 回测工具链 | Python 3.10（含 duckdb） |
| QMT 生产 | Python 3.6.8（GBK 产物、无 f-string、无新式类型标注） |
| QMT 端 | E:\国金QMT交易端模拟（账号 67014907） |

## 核心约定（详见 AGENTS.md）

- 财务数据 PIT 安全：`ann_date`/`f_ann_date` 过滤，禁止未来数据
- QMT 红线：GBK + `# coding=gbk` 首行、`passorder()` 全局函数、circ_mv 单位万元
- 风控底线：个股止损 8%、组合回撤 15%、持有 60 天、单日换手 30%
- 新策略上线：B 模块全测 + D 模块 D-1/D-2/D-6 + 模拟盘 1 交易日
