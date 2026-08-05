---
kind: configuration_system
name: YAML配置驱动的多层配置系统
category: configuration_system
scope:
    - '**'
source_files:
    - config/settings.yaml
    - config/trading_config.yaml
    - config/atr_lowvol_fw.yaml
    - scripts/run_backtest.py
    - main.py
    - projects/Project_01_多因子IC小盘Alpha/config/strategy.yaml
---

QuantLab 采用以 YAML 为核心的多层配置体系，通过 config/settings.yaml（全局设置）、config/trading_config.yaml（实盘交易）以及各策略项目独立 config/strategy.yaml 实现配置分层管理，由 Python 原生 yaml.safe_load 直接加载，无第三方配置框架。

### 架构与约定
- **配置分层**：settings.yaml 定义全局项目信息、数据源路径、回测默认参数、日志与因子预处理开关；trading_config.yaml 专管实盘账户、风控阈值、委托超时、调度时间表与通知 webhook；每个 Project_* 目录内自包含 strategy.yaml 描述因子权重、回测与实盘参数。
- **加载方式**：所有配置均通过 yaml.safe_load 从固定路径读取，main.py 硬编码 config/settings.yaml，scripts/run_backtest.py 通过 --config 参数传入具体策略 YAML 路径，broker/qmt_builder.py 与 test_connection*.py 直接读取 config/trading_config.yaml。
- **运行入口**：scripts/run_backtest.py 是统一 CLI 入口，解析 YAML 后组装 reader/universe/execution_cfg/strategy_params 并调用 backtest.engine.run_backtest，结果写入 reports/<run_id>/<config_name>/ 目录。
- **配置校验**：对关键字段做运行时校验，如 data.adjustment 必须为 raw/qfq/hfq 之一，universe.csv 必填，未命中则抛出 ValueError。

### 约定与约束
- 所有配置文件均为 UTF-8 编码的 YAML，使用 yaml.safe_load 安全加载，禁止任意对象反序列化。
- 全局配置固定位于 config/settings.yaml，实盘配置固定位于 config/trading_config.yaml，策略级配置位于各自 projects/Project_XX/config/strategy.yaml。
- 回测配置 YAML 必须包含 backtest、data、universe、execution、strategy_params 五个顶层键，其中 universe.csv 为必填字段。
- data.adjustment 仅允许 raw/qfq/hfq 三值，其他值在 scripts/run_backtest.py 中触发 ValueError。
- 实盘配置中的 account.path 指向 QMT 客户端 userdata_mini 目录，notification.webhook_url 为空时通知功能默认关闭。
- 配置变更会通过 compute_config_hash 与 compute_universe_hash 生成哈希写入 summary，用于结果可复现性追踪。