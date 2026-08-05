---
kind: frontend_style
name: 前端样式系统（无独立前端，以Streamlit与内嵌HTML为主）
category: frontend_style
scope:
    - '**'
source_files:
    - A股量化框架_五大扩展模块.md
---

本仓库为A股量化研究框架，整体为Python后端工程，**不包含独立的前端样式系统**。未发现任何CSS、SCSS、Tailwind配置或设计令牌文件。前端展示主要通过以下两种方式实现：

1. **Streamlit监控看板（规划中）**：`dashboard/` 目录为空占位，但在 `A股量化框架_五大扩展模块.md` 文档中提供了完整的 Streamlit 看板代码示例，使用 `st.set_page_config` 配置页面主题、图标和布局，通过 Plotly 生成图表，使用 pandas DataFrame 的 `.style.background_gradient()` 进行表格热力图渲染。该看板尚未落地为实际代码文件。

2. **内嵌HTML报告**：部分策略脚本（如 `projects/Project_01_多因子IC小盘Alpha/research/multi_factor_ic/backtest.py`、`ic_test.py`、`timing.py` 以及 `Project_02_双均线趋势策略/run_backtest.py`）直接在 Python 字符串中拼接 `<style>` 标签生成 HTML 报告，采用内联样式而非外部样式文件。

3. **Markdown报告**：回测结果输出到 `reports/` 目录下的 `report.md` 文件，由引擎自动生成，未涉及自定义样式。

**结论**：该项目属于纯后端量化框架，前端样式并非其关注点。若需可视化界面，应参考文档中的 Streamlit 示例代码自行实现，当前仓库内无现成的前端样式体系可复用。