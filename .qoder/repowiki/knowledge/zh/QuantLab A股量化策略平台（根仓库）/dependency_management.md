## 1. 使用的系统与方法

本仓库采用最轻量级的 Python 第三方包依赖声明方式：**根目录单文件 `requirements.txt`**，配合 `setup.py`（仅用于 QMT 环境的 `xtquant` 二进制拷贝），不依赖任何现代包管理器（无 pyproject.toml、Pipfile、poetry.lock、conda environment.yml）。对于极小量的 Node.js 工具（`.mimocode/`）使用独立的 `package.json` + `package-lock.json`。

核心依赖来源如下：
- **PyPI / Conda 环境安装**：pandas、numpy、pyyaml、scipy、lightgbm（可选 tushare/akshare/streamlit/plotly/cvxpy 等通过注释预留）。
- **手工拷贝**：`xtquant` 来自 QMT 客户端安装目录，通过 `setup.py` 中的路径探测脚本从 `E:/国金QMT交易端模拟/bin.x64/Lib/site-packages/xtquant`（或 miniQMT 对应路径）复制到运行环境的 site-packages，不在 PyPI 上获取。
- **Node 依赖**：仅 `.mimocode/package.json` 引用 `@mimo-ai/plugin: 0.1.10`（由 Qoder/mimecode 工作区内部工具链使用，与量化主工程无关）。

## 2. 关键文件

| 文件 | 作用 |
|---|---|
| `requirements.txt` | 全仓唯一 Python 依赖清单，集中声明最小运行时及研究/回测所需包，标注核心/回测/ML/可视/数据源/QMT 分类 |
| `setup.py` | QMT 一键部署辅助：查找本地 QMT 安装路径 → 复制 `xtquant` 到当前 Python 环境 site-packages → 验证导入并创建 `E:/QuantLab/logs/data/cache` |
| `.freebuff/project-id` | 非代码文件，仅保存项目标识，不影响依赖 |
| `.mimocode/package.json` | 独立于量化主工程的 Node 插件声明，不参与策略构建 |
| `.mimocode/package-lock.json` | Node 依赖锁定（与量化代码解耦） |

## 3. 架构与约定

### 3.1 Python 依赖分层与可选性
`requirements.txt` 按用途分段并用纯文本标题分隔，所有“非生产必需”的包以 `#` 注释保留版本下限，作为“启用即配”的备选：
- **核心**：pandas>=2.0, numpy>=1.24, pyyaml>=6.0 — 贯穿 data/factors/backtest/strategy/broker
- **回测**：scipy>=1.10
- **机器学习**：lightgbm>=4.0；xgboost 和 scikit-learn 目前仅注释保留
- **可选**：cvxpy、streamlit、plotly、tushare、akshare 全部以注释存在，需时取消注释安装
- **QMT**：xtquant 不走 requirements.txt（因需本地 QMT 安装包），注释仅为说明

这导致同一套源码在“无 lightgbm/pandas/scipy”环境中会导入失败——没有 `setup.cfg`/`extras_require` 的可选安装机制。新增第三方库应继续在 `requirements.txt` 中以相同风格追加，并按模块标注所属段。

### 3.2 多 Python 环境并存的现实约束
仓库运行涉及多种解释器：
- 研究/回测：Python 3.11（AGENTS.md 明确）
- QMT 生产：Python 3.6.8（QMT 内置解释器，无 pyarrow/parquet，GBK 编码限制）
- mcp/Node：Python 3.12 等（MCP server、浏览器扩展宿主）
- miniQMT：`C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe`

因此不存在统一的 lockfile，每个环境各自维护自己的 site-packages。依赖解析靠人工保证而非 CI 强制。

### 3.3 自包含构建产物（QMT）
AGENTS.md 规定 QMT 产物为 GBK 单文件 `build/strategy_xxx.py`，通过 `broker/qmt_builder.py` 生成。**产物自身不包含 import 的第三方包**，完全依赖 QMT 服务端已安装的 Python 环境——这意味着依赖管理职责被推到“部署机器预装”，构建过程只负责把策略逻辑输出成可执行文件。构建产物必须带 `BUILD_TAG` 时间戳以区分不同构建。

### 3.4 配置即“软依赖”的替代
仓库用 YAML 文件（`config/settings.yaml`、`config/trading_config.yaml`、各项目 `config/strategy.yaml`）声明运行时可调参数和数据源路径。对某些功能（如外部搜索、备用数据源 gpsj）通过 try/except 包裹 import 实现动态启用，例如 `data/gpsj_reader.py` 尝试 duckdb/gpsj 读取但允许回退到其他 reader。

## 4. 约定与约束（从代码与文档观察到的规则）

- **rules 层面**（有明文依据）：
  - `requirements.txt` 中所有包一律使用 `>=最低大版本号` 的下界形式（如 `pandas>=2.0`），**不允许 pin 死精确版本**，也不存在 `.txt` 以外的锁定文件。
  - QMT 产物必须是 GBK 编码单文件，首行 `# coding=gbk`，且不得引入 Python 3.6.8 不支持的语法——这意味着即使研究环境用了 Python 3.11+ 的新语法，最终产出也必须兼容 3.6.8。（依据：AGENTS.md QMT 红线）
  - `xtquant` 不走 pip/conda，必须从本地 QMT 安装目录手动复制到目标 site-packages；未找到源路径时脚本直接 `sys.exit(1)`。（依据：`setup.py` 行为）
  - QMT 环境不可假设任何第三方包（如 pyyaml）可用，import 必须 try/except fallback；策略读不到 config 要有 `_DEFAULT_CONFIG` 兜底。（依据：AGENTS.md 实盘执行红线）
  - 账户总额约束（虽属资金分配，但跨策略共享额度，本质是“外部依赖配额”）必须由 `check_capital_allocation.py` 校验通过后才能部署。（依据：资金分配红线）
- **conventions 层面**（观察到的惯用模式）：
  - 非核心依赖统一放在注释块内，按需取消注释安装，避免污染默认运行环境。
  - 每个 strategy/project 不自行维护子 `requirements.txt`——全仓共用根级清单。
  - 新策略上线前须确认依赖可在 QMT 环境中导入（尤其 ML、可视化、网络相关包），否则需在本地先验证再构建。
  - 数据源选择（astock vs gpsj vs tushare/akshare）通过运行时配置 + try/except 切换，不依赖多个锁文件。

## 5. 已知缺口与建议

- 无 `pip freeze`/lockfile → 无法保证重现精确的依赖快照。
- 无 CI 步骤自动拉取 requirements.txt → 依赖变更无人工审计易漂移。
- 无 `pyproject.toml` → 不支持现代可选依赖分组、editable install、构建后端集成。
- `xtquant` 依赖完全出镜于版本控制之外（本地绝对路径）。