---
kind: build_system
name: 构建与部署系统 — Python脚本+批处理的一键运行模式
category: build_system
scope:
    - '**'
source_files:
    - setup.py
    - requirements.txt
    - start_trading.bat
    - start_trading_qmt.bat
    - scripts/run_backtest.py
    - main.py
    - config/settings.yaml
    - config/trading_config.yaml
---

## 1. 使用的系统与工具
- **Python 包管理**：使用 `requirements.txt` 声明核心依赖（pandas、numpy、pyyaml、scipy、lightgbm 等），未使用 pipenv/conda/poetry。
- **安装配置**：通过 `setup.py` 提供一键环境配置脚本，用于查找并复制 QMT 的 xtquant 到系统 Python site-packages，并创建 logs/data 目录。
- **Windows 批处理入口**：`start_trading.bat` 和 `start_trading_qmt.bat` 作为实盘启动入口，负责检查 Python/QMT 环境、创建日志目录、校验配置文件后调用 `live_trading.py`。
- **回测 CLI**：`scripts/run_backtest.py` 是标准回测入口，通过 argparse 接受 `--config` 参数，读取 YAML 配置并驱动 backtest engine。
- **项目级入口**：每个 `projects/Project_XX_*` 子项目都有独立的 `run_*.py` 或 `local_run_v2.py` 等入口脚本，以及可选的 `build.py`。
- **无容器化/CI**：仓库中未发现 Dockerfile、Makefile、GitHub Actions 等 CI/CD 配置。

## 2. 关键文件与位置
- `setup.py` — 一键配置脚本，自动定位并复制 xtquant，验证导入并创建必要目录。
- `requirements.txt` — 依赖清单，区分核心、回测、ML、可视化、数据源、QMT 实盘等分组。
- `start_trading.bat` / `start_trading_qmt.bat` — Windows 批处理启动器，分别使用系统 Python 和 QMT 内置 Python 3.6.8。
- `scripts/run_backtest.py` — 回测统一 CLI，解析 YAML、加载数据、执行引擎、输出报告。
- `main.py` — 主入口，支持按策略名称切换不同回测实现（兼容旧版 multi_factor_ic）。
- `config/*.yaml` — 回测与交易配置（settings.yaml、trading_config.yaml、各策略参数集）。
- `projects/Project_XX_*/run_*.py` — 各策略项目的独立运行脚本。

## 3. 架构与约定
- **配置驱动**：所有回测/交易行为由 YAML 配置控制（backtest、data、universe、execution、strategy_params 等段），CLI 仅负责参数解析与结果输出。
- **模块化入口**：`scripts/run_backtest.py` 封装通用回测流程；`main.py` 提供高层策略选择；`.bat` 文件封装环境检查与进程启动。
- **路径约定**：
  - 数据源固定为 `E:/astock`（只读），结果写入 `E:/QuantLab/reports/<run_id>_<config>/`。
  - 日志目录 `logs/`，缓存目录 `data/cache/`，配置文件 `config/`。
  - QMT 路径硬编码为 `E:/国金QMT交易端模拟/bin.x64/...`。
- **哈希可复现性**：`backtest.hashing.compute_config_hash` 与 `compute_universe_hash` 对配置文本与股票池生成哈希，用于结果版本追踪。
- **策略注册**：通过装饰器+自动发现机制注册策略名（如 `atr_lowvol`），由 `strategy.registry` 管理。

## 4. 约定与约束
- **环境要求**：必须安装 Python（≥3.6，QMT 内置 3.6.8），QMT 客户端需先启动 `XtMiniQmt.exe`。
- **配置文件强制存在**：`start_trading.bat` 在启动前检查 `config\trading_config.yaml`，不存在则直接退出。
- **数据调整选项限制**：`data.adjustment` 必须为 `raw/qfq/hfq` 之一，否则抛出 ValueError。
- **xtquant 手动安装**：不通过 pip 安装，需运行 `setup.py` 从 QMT 安装目录复制 xtquant 到系统 Python。
- **结果输出规范**：每次运行生成包含 equity_curve.csv、trades.csv、positions.csv、summary.json、report.md、logs.txt 的标准报告目录。
- **无跨平台/容器化**：所有路径均为 Windows 绝对路径，未提供 Linux/macOS 适配或 Docker 镜像。
- **测试框架**：`.gitignore` 中包含 `.pytest_cache/`，但未见 pytest 配置文件或集中测试入口，测试分散在各项目中。