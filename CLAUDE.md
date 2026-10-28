# QuantLab · Claude 协作须知

**权威源是 `AGENTS.md`（优先于本文件），开工前必须先读。**
任务看板：`全局控制台.md` 的「三、当前任务看板」；知识库入口：`.qoder/repowiki/zh/content/知识库导航.md`。

## 硬红线（违反即报废）

- QMT 产物：GBK 编码、首行 `# coding=gbk`、内嵌 `BUILD_TAG`（`YYYYmmdd-HHMMSS`）。**部署不再人工粘贴**（2026-10-28 实证：明文写入 `<QMT>\python\<模型名>.py` 会被客户端直接加载，走 `projects/Project_16_LightGBM股票大师/qmt_autodeploy.py` 自动落盘 + `reboot` 重启 + 核对 BUILD_TAG）；唯一保留的人工动作 = **从未注册过的新模型名**需在客户端 UI 建一次同名空壳。旧规则「复制到 `python\` 无效」已被推翻。
- 账号严格分离：`67014907` = P16 V1.3 / 价值小盘V2；`70180771` = ATR / 黄氏529 / G2 / ENS / 全天候。禁止混用，禁止用 xtquant 外连 `70180771`（未开通 miniQMT）。
- 真实下单、清仓、改风控阈值/评分权重、改资金分配：必须人工拍板，禁止自行执行。
- 数据 PIT 安全不可妥协；行情主源 `D:/astock/`，备用源 `data/gpsj_reader.py`（`收盘价` 列是前复权，禁用）。
- QMT 内置 Python 3.6.8：禁 f-string / 海象 / match-case / 新式类型注解。

## 修改边界（默认只读）

- 不修改 `全局控制台.md`、`.qoder/repowiki/`、`config/capital_allocation.yaml` 和任何 `build/` 产物——这些由主控 agent 单点收口，避免双写冲突。
- 需要改代码时在独立 git worktree 里做，交付 diff + 文件清单，由主控 agent 复核后落地。

## 结论格式

- 每条结论必须带 `文件:行号` 或命令输出证据；拿不准写 `unknown`，禁止凭印象猜测。
- 「找出所有 X」类问题：先报所属章节的**标题行号**，再列该节内条目；禁止用 grep 命中代替区段阅读。
- 结尾必须声明：哪些题没有把握、哪些文件没打开过。

## 本机环境事实（2026-10-27 实测）

- `claude` 走第三方网关（`~/.claude/settings.json` 配了 `ANTHROPIC_BASE_URL`），后端不是 Anthropic 官方模型；会话 JSON 里 `requestedModel` 才是真实模型名。
- **必须显式传 `--model`**：settings.json 的默认别名在本网关会挂死（同一句 >10 分钟零输出）；实测可用值 `commandcode-plan/deepseek/deepseek-v4.1-flash`（同一句 6 秒、带 Read 工具 11 秒）。stderr 里的 `[claude-code:unrecognized_model]` 是常态噪音，不代表失败。
- `--permission-mode plan` 在本网关下会长时间挂起（同一句 7 秒 vs 150 秒未回）；只读请用 `--allowedTools "Read,Grep,Glob"`。
- 非交互调用必须关闭 stdin（`< /dev/null`），否则阻塞等 EOF；`--output-format json` 会挂起，固定 `text`。
- 统一入口：`scripts/claude_audit.ps1`（单实例、硬超时、结果落 `logs/claude_audit/`，已内置默认模型）。
