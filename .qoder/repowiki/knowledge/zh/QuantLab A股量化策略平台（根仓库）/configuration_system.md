## 概览
QuantLab 的配置采用「文件为主、Python 常量为辅」的混合模式：核心运行参数集中存放在 `config/*.yaml`，每个策略项目的独立参数放在各自目录；同时每个策略还保留一份纯 Python 配置文件（如 `qmt_config.py`、`data_config.py`、`research/multi_factor_ic/config.py`），用于路径与运行时常量。

该系统的权威约定与约束由根仓库文档 `AGENTS.md`（被 agent 载入）统一声明，代码通过读取 YAML / Python 模块来消费这些配置。

## 1. 层级结构

| 层级 | 位置 | 内容 | 说明 |
|---|---|---:|
| 全局配置 | `config/settings.yaml` | 项目名/版本、根路径、数据源开关、回测起止与初始资金、因子预处理参数、默认股票池等 |
| 实盘配置 | `config/trading_config.yaml` | QMT 账号(70180771)、交易费率/滑点、风控阈值(止损 8%、最大回撤 15%、持仓上限等)、委托超时重试、调度时间表、通知 webhook |
| 项目级配置 | `projects/Project_XX_*/config/strategy.yaml`（各项目中）、以及本仓内的策略专项 YAML：`config/atr_lowvol_equalweight_config.yaml`、`config/value_smallcap_v2_config.yaml` | 具体策略的 name、capital_base、account_id、screening/rebalance/pool 等参数 |
| 资金分配总表 | `config/capital_allocation.yaml` | 唯一事实源：账户总资产、每个策略的 `key/name/capital_base/max_hold/config_file/note` |
| 项目路径配置 | 各项目根下的 `data_config.py` / `qmt_config.py` | 绝对/相对路径：astock parquet 主仓、增量目录、模型输出目录、QMT 客户端路径、账户 ID、信号/成交日志路径等 |

## 2. 加载方式
- YAML：通过标准 `yaml.safe_load` 或同类方式加载至 Python dict，再由策略/回测引擎消费；`AGENTS.md` 明确定义了「全局→实盘→项目级」三级级联。
- Python 配置文件：`qmt_config.py`、`data_config.py`、`research/multi_factor_ic/config.py` 等以模块形式 import，直接读取顶层变量；这种模式在 QMT GBK 产物内尤为常见，因为 QMT 环境不一定装 pyyaml。
- 环境变量/环境变量注入：代码中未见集中式 `os.environ` 管理，仅作为可选补充（未形成规范）。

## 3. 关键约束与规则（经 AGENTS.md 与配置注释共同体现）
- **双账号并存隔离**：`config/trading_config.yaml` 指向新账号 `70180771`；`projects/Project_16_LightGBM股票大师/qmt_config.py` 单独用旧号 `67014907`。严禁混用——引用错号会废单。
- **虚拟子账户总额硬约束**：`capital_allocation.yaml` 所有策略 `capital_base` 之和必须 ≤ 账户实际总资产 `total_capital`；新增/调整额度后必须执行校验脚本 `scripts/check_capital_allocation.py`（退出码 0 才可部署）。该项目层 YAML 中也用注释标注此规则为“硬规则”。
- **QMT 产物的自包含要求**：AGENTS.md 强调 QMT 构建产物必须是「自包含」——如果读不到外部 config，必须有完整 `_DEFAULT_CONFIG` fallback；且不能依赖 `__file__` 解析相对路径。
- **GBK 编码与 Python 3.6.8 兼容**：QMT 部署产物禁止使用 f-string、类型注解等 3.6 不支持语法，这是强制红线；而当前研究/回测环境默认 Python ≥ 3.11。
- **PIT 安全与 look-ahead 禁忌**：财务数据必须基于 `ann_date`/`f_ann_date` 过滤；市值过滤等应在函数内应用，不应提前静态筛选导致全期幸存者偏差。这些属于配置/数据处理契约。
- **备份容灾**：涉及账号切换时，持票账本需内嵌 `account_id` 戳，加载不匹配时必须自动 `.bak_acct_<戳>` 备份并空仓起步，禁止拿错账号账本交易。
- **数据源统一入口**：`config/settings.yaml` 的 `data.source` 可切换 astock/DuckDB/gpsj 等后端；`data_config.py` 提供 `read_main_daily()` 封装主仓+周增量合并逻辑，增量优先覆盖。

## 4. 典型配置项对照
- 回测参数：`settings.yaml` 中的 `backtest.start_date/end_date/initial_capital/commission/slippage/benchmark` 统一定义。
- 因子预处理：`settings.yaml` 中 `factors.winsorize/std/neuralize_method/standardize_method` 控制 winsorize/z-score 及中性化方法。
- 策略参数：`value_smallcap_v2_config.yaml` 暴露 `screening.n_hold/market_cap_max/method/quality_gate/delist_screen`、`rebalance.freq/buffer_keep/stop_loss/max_holding_days/max_drawdown`、`pool.*` 等键供上层策略直读。
- 路径类配置：`data_config.py` 定义 `ASTOCK_DIR/FINANCE_DIR/MODEL_DIR/DATA_DIR/LIVE_DIR`，`qmt_config.py` 定义 `QMT_PATH/USERDATA/XTPACK/ACCOUNT_ID/START_CAPITAL` 等运行时常量。

## 5. 组织与扩展约定
- 新增策略：先在 `capital_allocation.yaml` 登记 `capital_base`，再创建对应 `config/<策略>_config.yaml`，并把 `config_file` 指回到该 YAML；上线前跑 `scripts/check_capital_allocation.py`。
- QMT 策略部署：保持 GBK 编码、首行 `# coding=gbk`，并通过 `broker/qmt_builder.py` 生成 GBK 单文件，构建产物自带 BUILD_TAG 时间戳用于版本核对。
- 多项目并行：`projects/` 下每个策略是独立工作空间，共享同一份 `data/*`、`factors/*`、`backtest/*` 和根 `config/`，但各自维护自己的 Python config（路径、账号、模型文件）和结果目录。

## 6. 结论
QuantLab 没有引入 Pydantic Settings、dotenv 等通用库，而是以「YAML + 模块导入」的组合实现了清晰的三层级联配置体系，并通过 `AGENTS.md` 把约束（账户隔离、资金总量、PIT 安全、GBK 兼容、自包含）固化为团队纪律；新增配置走 `config/*.yaml`，只在需要路径/运行时常量时才写 Python 模块文件。