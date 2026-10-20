# DE 任务书：分析 G2/ENS 两策略 2026-10-15 运行日志

## 我理解的范围（请先回报这一段再开工）

你要分析的文件只有一个：

- `D:\QuantLab\projects\Project_16_LightGBM股票大师\results\log_20261015_g2_ens.txt`

这是 2026-10-15 大 QMT 里 STRATEGY_P16_G2_BRIDGE 和 STRATEGY_P16_ENS_BRIDGE 两个桥接策略的运行日志，约 2.9MB。

## 任务

1. 通读该日志文件，找出所有异常/错误/警告类型的日志行（包括但不限于：STALE-CMD、READ-JSON-ERR、CMD-READ-FAIL、PERMISSION、PermissionError、Foribdden、ERROR、ERR、WARN、FAIL、SKIP 等模式）。
2. 统计每类问题出现的次数、首次/末次出现时间、涉及的文件路径。
3. 特别关注（已知线索，按优先级）：
   - **PermissionError('Foribdden FileIO')**：大量 JSON 文件读取失败（heart_*.json、positions_cfg_*.json、peak.json、orders_*.json），这个拼写 "Foribdden" 是 QMT 沙箱特有的报错字符串。查清楚哪些文件读失败、读失败后策略走了什么 fallback 分支、是否影响下单/持仓管理。
   - **STALE-CMD 未归档命令文件**：orders_20261015.json 未归档说明什么？是当日任务没跑完还是归档逻辑没触发？
   - **CMD-READ-FAIL**：orders 文件存在但读失败，反复出现，查频率和持续时间。
   - 两个策略（G2 / ENS）分别有多少有效交易动作（BUY/SELL 委托）？有没有实际成交回报日志？
   - INIT 时 positions=0，是否意味着账本加载失败（本应有历史持仓）？
   - 日志尾部（最后 200 行）是什么状态，策略是否正常收尾还是中断？
4. 输出一份结构化分析报告，按以下结构：
   - 一、日志概况（时间范围、两个策略的 BUILD_TAG、INIT 参数）
   - 二、问题清单（每类问题：模式、次数、时间范围、影响评估）
   - 三、根因推断（PermissionError 的可能来源：QMT 沙箱文件权限？文件被其他进程锁住？路径含中文？）
   - 四、影响评估（当日是否正常执行了换仓？是否有漏单/重复单风险？）
   - 五、修复建议（按优先级 P0/P1/P2 排列）
5. 报告写入：`D:\QuantLab\projects\Project_16_LightGBM股票大师\results\日志分析报告_G2ENS_20261015.md`

## 硬约束

- **只读分析**：禁止修改 `log_20261015_g2_ens.txt` 和任何其他已有文件（除输出报告文件外）。
- **禁止 commit / 禁止改类命令**。
- 允许用 grep/PowerShell Select-String 等方式检索日志内容；允许用 Python 做统计。
- 报告中的数字必须来自日志原文，不得编造或估算。

## 开工前回报

在写报告之前，先在你的回复中输出：

> 我理解的范围是：分析 `D:\QuantLab\projects\Project_16_LightGBM股票大师\results\log_20261015_g2_ens.txt` 这 1 个文件，产出报告到 `日志分析报告_G2ENS_20261015.md`。

确认无误后开始。
