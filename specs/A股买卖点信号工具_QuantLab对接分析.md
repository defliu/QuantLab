# A股买卖点信号提示 / 选股工具 — QuantLab 对接分析

- **日期**：2026-08-27
- **需求来源**：飞书知识库文档《A股买卖点信号提示 / 选股工具需求文档》（作者 Chris，最后修改 2026-03-16）
- **文档定位**：需求 → 现有框架能力的对照与缺口分析，作为后续开发的依据
- **关联**：`AGENTS.md`（项目开发指南）、`D:\QuantLab\specs\`（需求与设计文档目录）

---

## 一、需求要点摘要

该需求是做一个 **A股买卖点信号提示 / 选股工具**，核心定位：**程序筛选 + 信号提示，最终买卖由人工决定**。关键要求如下：

1. **按不同买点逻辑分别选股**：不采用一套统一条件适配所有买点，拆成 4 个独立策略模块：
   - 箱体突破买点
   - 杯柄图形买点
   - 上升趋势回调低吸买点
   - 二次启动买点
2. **多策略池分别监控**：多模块可同时启用，各模块独立筛股、独立提示，不混用条件。
3. **卖点 / 失效条件**：按买点结构分别设置（非统一规则），另加一个共通"高位放量大阳线"辅助风险提示。跌破判断统一以 **收盘价** 为准，盘中跌破不算。
4. **提示方式**：弹窗提示 + 提示音报警，目标是第一时间发现信号、不影响正常看盘。
5. **参数可调 + 勾选开关**：主要参数（箱体周期、突破涨幅阈值、回调幅度、均线周期、量能比较周期、放量倍数、二次启动整理周期/涨幅上限等）手动可调；部分条件（MACD 金叉、次日确认、回踩确认、趋势支撑/颈线、模块启用）支持勾选开关。

### 需求自述的两点前提

- 杯柄图形、二次启动属于**规则化近似实现**，不保证与人工看图完全一致。
- 超出当前范围的内容（新买点、自动买卖、热点板块识别、大幅调整逻辑）需另外评估。

---

## 二、QuantLab 现有框架能力盘点

| 能力层 | 现成资产 | 文件位置 |
|---|---|---|
| 历史数据 | `AstockParquetReader`：OHLCV/成交量/复权/市值/换手率/财务，hfq 复权，PIT 安全 | `data/astock_reader.py`、`data/feed.py` |
| 备用数据源 | `GpsjDuckDBReader`（2015 起，交叉验证） | `data/gpsj_reader.py` |
| 实时数据 | `LocalContext` 映射 `C.*` → xtdata；tick 级 `get_full_tick` | `broker/local_context.py` |
| 技术指标库 | MA5/10/20/60/120、均线角度、MACD、CCI、hhv/llv、放量比、振幅、大阴/大阳线标记 | `projects/Project_12_RPS主升浪/research/huangshi_formula_scan.py::compute_indicators` |
| 形态信号（突破/回踩） | `signal_529`、`signal_shuangzhongjun`、`_check_breakout`、`_check_pullback_entry`、`_check_volume_confirm`、`_check_market_gate` | `huangshi_formula_scan.py`、`strategy/rps_momentum.py` |
| 持仓管理 | 止损/止盈/移动止盈/时间止损 | `strategy/rps_momentum.py::_hold_decision`、`projects/Project_16.../qmt_monitor.py` |
| 选股→信号管线 | 日更信号 CSV → `D:/QMT_POOL` 文件交换（完整闭环范例） | `projects/Project_13_529主升浪/research/gen_529_signal_daily.py` |
| 策略注册 | `@register_strategy` 装饰器 + autodiscover，多策略可并行 | `strategy/registry.py` |
| 配置系统 | YAML 三级级联 + `strategy_params` 字典 + 0/1 flag 开关 | `config/settings.yaml`、`config/trading_config.yaml`、项目级 `config/strategy.yaml` |
| 飞书/IM 告警 | `notify_feishu()`（lark-cli bot 身份，已验证连通） | `projects/Project_16.../qmt_monitor.py` |

---

## 三、逐模块对接分析

| 需求模块 | QuantLab 现状 | 对接评估 |
|---|---|---|
| 数据底座（OHLCV/量/复权/市值） | `AstockParquetReader` 全字段 + hfq + PIT 财务 | ✅ 完全满足 |
| 实时数据/盯盘 | `LocalContext` + xtdata `get_full_tick`；分钟级 K 线需新封装 | ✅ 够用 |
| 技术指标 | `compute_indicators` 全套指标现成 | ✅ 直接复用 |
| 箱体突破买点 | `signal_529`/`signal_shuangzhongjun`/`_check_breakout` + `_check_volume_confirm` | ✅ 高度复用 |
| 上升趋势回调低吸 | `_check_pullback_entry`/`signal_match_master` 买点2/摆动低点/MACD | ✅ 高度复用 |
| 杯柄图形买点 | 无现成形态代码 | ⚠️ 需新写（底层指标齐） |
| 二次启动买点 | 无显式函数；Project_13 黄氏529"主升浪"骨架方向接近 | ⚠️ 需新写（要素可拼装） |
| 卖点/失效条件 | 止损/止盈现成；"放量跌破箱体上沿/坑沿/支撑"需新写 | ⚠️ 部分复用 |
| 分模块独立选股管线 | `gen_529_signal_daily.py` 完整闭环范例 + 多策略注册 | ✅ 完整闭环可复制扩展 |
| 参数可调 + 开关 | YAML + `strategy_params` + 0/1 flag | ✅ 完全兼容 |
| 飞书/IM 告警 | `notify_feishu()`（已验证连通） | ✅ 最强现成通道 |
| 弹窗提醒 | 无（无 winsound/MessageBox） | ❌ 需新增 |
| 提示音报警 | 无 | ❌ 需新增 |
| 多策略池分别监控 | `qmt_monitor.py` 实时盯盘模板 + 多策略注册 | ✅ 架构支持 |

---

## 四、详细对接说明

### 4.1 箱体突破买点 —— ✅ 高度复用

文档要求：低位横盘 → 突破箱体上沿；细分强势突破 / 弱势突破（次日确认）/ 回踩确认；失效 = 放量跌破箱体上沿（收盘价判断，放量倍数参数化）。

- 现成可复用：`signal_529`（突破 60 日高 + MA 多头 + 筹码密集）、`signal_shuangzhongjun`（突破 20 日高）、`_check_breakout`（收盘突破 N 日新高）、`_check_volume_confirm`（放量确认：当日量/5日均量 ≥ 阈值）。
- 箱体上沿可用 `hhv` 前 N 日最高价表达；"弱势突破"= 当日突破但涨幅低于阈值，标记待次日确认，即文档的"次日确认"开关；"回踩确认"= 突破后回踩箱体上沿不破再走强，可用 `llv`/MA 支撑判断。
- 需做：把硬编码周期/倍数收敛为 `strategy.yaml` 参数。

### 4.2 上升趋势回调低吸 —— ✅ 高度复用

文档要求：均线多头排列、量能均衡、回调不深、连续 2-3 根不创新低小K线、缩量企稳、可开 MACD 金叉；趋势支撑按摆动低点连线。

- 现成可复用：`_check_pullback_entry`（回调到均线企稳）、`signal_match_master` 买点2（多头趋势回踩）、`compute_indicators` 的均线/角度/量比/MACD、`llv` 摆动低点。
- "连续小K线不创新低"用 MA/振幅/`llv` 组合可实现；"趋势支撑（摆动低点连线）"是规则化连接每次折返关键低点，需按该规则新写一段，但底层构件齐备。

### 4.3 杯柄图形买点 —— ⚠️ 需新写

文档要求：低位横盘 → 挖坑回调 → 回到坑上沿附近 → 整理几天 → 再启动提示；卖点 = 跌破坑上沿（收盘价）+ 高位放量大阳线（量 2 倍）。

- 仓库无任何杯柄（cup & handle）识别代码，需按规则化流程新写：低位箱体（`hhv`/`llv` 区间）→ 坑底回调（中途低点 + 回补幅度）→ 坑沿整理（横盘 N 日）→ 启动确认（放量/突破）。
- 底层指标（高低点、均线、量能）全部可用，实现为纯规则函数即可。

### 4.4 二次启动买点 —— ⚠️ 需新写（要素可拼装）

文档要求：底部横盘/箱体 → 突破后拉升 ≤30% → 创半年新高 → 再横盘整理（重心不降、周期 ≤14 交易日）→ 再次启动提示；卖点 = 放量跌破横盘区域（收盘价）+ 高位放量大阳线。

- 无显式"二次启动"函数，但每个要素都有现成构件：箱体（`hhv`/`llv`）、拉升幅度（区间涨幅计算）、半年新高（`hhv` 120 日）、再横盘（振幅/均线收敛 + 重心 `MA20` 斜率）、再启动（放量突破）。
- Project_13 黄氏529"主升浪"是事件驱动骨架，时序结构可参考，但信号条件需按本文档重写。

### 4.5 卖点 / 失效条件 —— ⚠️ 部分复用

- 通用止损/止盈/移动止盈已有（`_hold_decision`、`qmt_monitor`），可直接承接持仓后的日常风控。
- 文档特有的结构失效卖点（放量跌破箱体上沿 / 坑上沿 / 趋势支撑 / 横盘区域，统一收盘价判定 + 放量倍数参数）需按各模块新写判定函数。
- 共通"高位放量大阳线"辅助提示 = `hhv` 高位判断 + 当日大阳线（涨幅阈值）+ 放量倍数（量 2 倍）组合，规则简单。

### 4.6 提示方式 —— ❌ 需新增（工作量大头在告警通道）

- **飞书/IM**：`notify_feishu()` 已验证连通，是首选通道（可推送信号明细 + 人工确认链接）。
- **弹窗**：仓库无现成。可选 Windows 通知（如 `winotify`）或 QMT 端弹窗；也可用飞书消息替代，视用户使用场景定。
- **提示音**：仓库无现成。可用 `winsound.Beep`（Windows 原生，无第三方依赖）实现，工作量很小。
- 注意：告警通道应独立于信号计算，推送失败只记录不阻塞主流程（沿用 Project_16 的既有约定）。

### 4.7 选股→提示闭环 —— ✅ 完整闭环可直接复制扩展

- `gen_529_signal_daily.py`（日更信号 CSV → `D:/QMT_POOL`）+ `notify_feishu()` 已是完整的"选股 → 落盘 → 推送"范例。
- 4 个买点模块各注册为一个策略，各自输出信号表与提示；多模块并行时分别输出，天然满足文档"不混用条件、分别提示"的要求。

### 4.8 参数配置 —— ✅ 完全兼容

文档列出的参数（箱体周期、突破涨幅阈值、回调幅度、均线周期、成交量比较周期、放量倍数、MACD金叉开关、次日确认、回踩确认、趋势支撑/颈线开关、二次启动整理周期、二次启动涨幅上限、模块启用）全部可落到 `strategy.yaml` 的 `strategy_params`，开关用 0/1 flag，与现有策略完全同构。

---

## 五、关键判断

1. **定位匹配**：文档定位为"信号提示 + 人工决策"的信号工具，与 QuantLab 现有"选股 → 信号 → 文件/IM 推送"管线本质一致，是友好扩展而非重造。
2. **复用度总评**：数据、指标、选股管线、飞书告警、参数化配置五大底座全部现成；箱体突破、趋势低吸基本可复用现成函数并参数化；杯柄、二次启动需新写形态识别（底层构件齐备，工作量可控）；弹窗 + 提示音需小量新增。
3. **纪律性提醒（重要）**：4 类买点均为技术形态/趋势动量类。Project_12 教训在案：**A 股全市场动量/趋势类因子 IC 验证为负**。当前定位为人工决策的信号工具，合规；但若未来转自动实盘，必须先做 IC 验证 + 回测（含 gpsj 交叉验证），不得凭形态直觉直接上线。
4. **时序与防前视**：信号判定统一用收盘数据（文档也要求收盘价判断），避免盘中取当日 close 的 look-ahead；跨年回测需 PIT 安全。

---

## 六、落地路径建议

按文档"分模块"思路，在 `projects/` 下新建信号工具项目（如 `Project_18_买卖点信号工具`），结构：

1. `strategy/`：4 个买点模块（箱体突破 / 杯柄 / 趋势低吸 / 二次启动），各含 `选股()` 与 `信号()` 函数，共用 `compute_indicators` 指标库；卖点/失效判定并入对应模块。
2. `config/strategy.yaml`：全部参数 + 勾选开关（0/1 flag）收敛于此。
3. `runner` / 日更脚本：复用 `gen_529_signal_daily.py` 管线模式，输出各模块信号表到 `D:/QMT_POOL/`。
4. `notify`：复用 `notify_feishu()` 推送；按需新增弹窗（winotify）与提示音（winsound）。
5. 可选：分钟级 K 线封装（xtdata 支持，仓库暂无），用于盘中信号；首期可用收盘后信号。

**开发前置检查**：新项目需遵循 AGENTS.md 的验证框架（B/D 模块）、PIT 安全、回测交叉验证、QMT 产物 GBK/Python3.6 兼容等红线。

---

## 七、风险与注意事项

| 风险项 | 说明 | 应对 |
|---|---|---|
| 形态类信号有效性 | 趋势/动量类因子全市场 IC 为负的历史教训 | 保持"信号提示 + 人工决策"定位；转实盘前必做 IC/回测 |
| 形态识别主观性 | 杯柄/二次启动为规则化近似 | 文档已自述接受；实现时参数化以容错 |
| 弹窗/提示音 | 无现成通道 | 小工作量新增；优先飞书通道 |
| 分钟级数据 | 仓库无封装 | 首期用收盘后信号；需要时再接 xtdata 分钟 |
| 多模块并行的资源/告警噪音 | 多策略池同时提示 | 模块启用开关 + 推送合并/去重 |

---

## 附录：可复用资源清单（速查）

| 资源 | 路径 | 用途 |
|---|---|---|
| 指标库 | `projects/Project_12_RPS主升浪/research/huangshi_formula_scan.py::compute_indicators` | MA/角度/MACD/CCI/hhv/llv/放量等 |
| 形态信号函数 | 同上 `signal_529`/`signal_shuangzhongjun`/`signal_match_master`；`strategy/rps_momentum.py::_check_breakout/_check_pullback_entry/_check_volume_confirm/_check_market_gate` | 突破/回踩/放量/门控 |
| 持仓风控 | `strategy/rps_momentum.py::_hold_decision`、`projects/Project_16.../qmt_monitor.py` | 止损/止盈/移动止盈 |
| 日更信号管线 | `projects/Project_13_529主升浪/research/gen_529_signal_daily.py` | 选股→CSV→`D:/QMT_POOL` 闭环 |
| 飞书推送 | `projects/Project_16.../qmt_monitor.py::notify_feishu` | IM 告警（已验证） |
| 历史数据 | `data/astock_reader.py`（hfq）、`data/gpsj_reader.py`（交叉验证） | 回测与信号计算 |
| 实时数据 | `broker/local_context.py`、xtdata `get_full_tick` | 实时/盯盘 |
| 配置 | `config/settings.yaml`、`config/trading_config.yaml`、项目 `config/strategy.yaml` | 参数与开关 |
