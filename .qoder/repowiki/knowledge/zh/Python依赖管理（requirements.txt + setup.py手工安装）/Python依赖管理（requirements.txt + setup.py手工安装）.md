---
kind: dependency_management
name: Python依赖管理（requirements.txt + setup.py手工安装）
category: dependency_management
scope:
    - '**'
source_files:
    - requirements.txt
    - setup.py
---

本仓库采用最简化的 Python 依赖管理方式，未使用 pipenv、poetry、conda 等现代包管理器，也未维护 lockfile 或 vendoring。

1. 依赖声明与版本约束
- 核心依赖集中在根目录 `requirements.txt`，使用 `>=` 宽松版本约束（如 `pandas>=2.0`、`numpy>=1.24`、`pyyaml>=6.0`、`scipy>=1.10`、`lightgbm>=4.0`），便于在不同环境中自动解析兼容版本。
- 可选依赖通过注释形式列出（ML：xgboost、scikit-learn；可视化：streamlit、plotly；数据源：tushare、akshare；组合优化：cvxpy），由使用者按需取消注释安装。
- QMT 实盘依赖 `xtquant` 不通过 PyPI 安装，而是从 QMT 客户端安装目录手动复制。

2. 安装与部署流程
- `setup.py` 并非标准 Python 包构建脚本，而是一个“一键配置”工具：自动搜索本地 QMT 安装路径中的 `xtquant` 包，复制到系统 Python 的 site-packages，并创建日志/数据目录。该脚本硬编码了 Windows 路径（如 `E:/国金QMT交易端模拟/bin.x64/Lib/site-packages/xtquant`），仅适用于特定机器环境。
- 没有 `pyproject.toml`、`Pipfile.lock`、`poetry.lock` 等锁定文件，也没有 `vendor/` 目录，因此不存在可复现的二进制依赖缓存机制。

3. 架构与约定
- 依赖分层清晰：`requirements.txt` 中按“核心 / 回测 / ML / 组合优化 / 可视化 / 数据源 / QMT实盘”分组注释，便于按需裁剪。
- 第三方库均为纯 Python 或可通过 pip 安装的 wheel，无 C/C++ 扩展的特殊编译要求（除 xtquant 外）。
- 项目以 `main.py` 为统一入口，各子项目（projects/Project_XX_*）共享根级依赖，未为每个策略项目单独维护 requirements。

4. 约束与风险
- 缺少版本锁定意味着不同环境可能解析出不同次版本，存在潜在的不一致风险。
- xtquant 依赖通过硬编码路径复制安装，不具备跨机器可移植性。
- 未使用虚拟环境隔离，所有依赖直接安装到系统 Python 环境。