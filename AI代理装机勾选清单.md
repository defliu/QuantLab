# AI 代理装机勾选清单（重装恢复 · AI 专项）

> 日期：2026-08-08
> 当前进度（你已确认）：驱动✓ / 软件只装了 WorkBuddy+OpenCode / 用户配置未做(3.2) / Python未装(3.6) / QMT不急(3.4)
> 目标：先把所有 AI 代理装起来 + 配好运行环境，再回头做量化/其他。

## ⚠️ 关键事实（决定清单顺序）

- **没有系统 Node.js**：当前 `node`/`npm` 仅是 WorkBuddy 沙箱版（`.workbuddy\binaries\node`，仅供我内部用），不是系统 Node。AI 终端 CLI 跑不起来。
- **npm 全局在 D 盘且存活**：`D:\Program Files\npm-global` 在重装中保留，内含 `claude` / `opencode` / `deveco` 等 CLI 及若干 MCP 工具，**只是没挂 PATH**。重装 Node 后接上即可用，不用逐个重装。
- **GUI 应用需重装**：Qoder/Kimi/豆包/元宝/ima/Qwen/Trae 等本体在 C 盘已被格，必须重装；它们的*配置*（含 API Key）由 `restore_configs.ps1`（3.2）从备份写回。
- **OpenCode 已装=桌面版**（winget 的 `SST.OpenCodeDesktop`），与 npm 的 `opencode` CLI 是两回事，互不冲突。

---

## 阶段 0 — 前置：系统 Node + npm 全局接线（所有终端 AI CLI 的前提）

- [ ] **安装 Node.js**（winget，装到 D 盘，避开 C 再满）
      ```powershell
      # 管理员 PowerShell
      winget install OpenJS.NodeJS.LTS --location "D:\Apps"   # 或报告里的 24.x
      ```
- [ ] **把 npm 全局接回 PATH + 设前缀**（复用 D 盘已有的 claude/opencode/deveco，免重装）
      ```powershell
      npm config set prefix "D:\Program Files\npm-global"
      # 然后把 D:\Program Files\npm-global 加入系统 PATH（设置→系统→关于→高级系统设置→环境变量→Path 新增一项）
      ```
- [ ] **验证终端 AI CLI 可调**（重启 PowerShell 后）
      ```powershell
      claude --version      # 期望有版本号
      opencode --version
      deveco --version
      ```

## 阶段 1 — 还原所有 AI 代理配置（一次性，最关键，对应 3.2）

- [ ] **运行还原脚本**（写回 .claude/.codex/.qoder/.qoder-cn/.qwen/.qwenworkcn/.trae/.trae-cn/.workbuddy/.mcp.json/.ssh/.gitconfig 等全部 API Key 与记忆）
      ```powershell
      # 管理员 PowerShell，cd 到 reinstall_inventory\
      .\restore_configs.ps1 -BackupRoot "F:\WinReinstallBackup"
      ```
- [ ] **重启终端** 使配置生效
- [ ] **确认 WorkBuddy 读到历史记忆**（我这边应恢复旧 MEMORY.md / 项目记忆，而非空白）

## 阶段 2 — 终端 AI CLI（npm 全局）

| 代理 | 状态 | 动作 |
|------|------|------|
| Claude Code | 已在 D:\Program Files\npm-global | 阶段0后直接用（或 `npm i -g @anthropic-ai/claude-code` 重装） |
| OpenCode CLI | 已在 | 阶段0后直接用（桌面版已装✓） |
| Codex (`@openai/codex`) | **未装** | `npm i -g @openai/codex` |
| Deveco | 已在 | 阶段0后直接用 |
| MCP 工具（eodhd-screener-mcp / lildax / mimo / arxiv） | 已在 | 阶段0后直接用 |

- [ ] Claude Code 可用
- [ ] OpenCode CLI 可用
- [ ] Codex 安装并可用（`codex --version`）
- [ ] Deveco 可用
- [ ] 上述 MCP 工具可用

## 阶段 3 — GUI AI 应用（winget 覆盖，勾选安装）

> 跑 `reinstall_interactive.ps1` 弹窗，在下面这些里勾选（其余软件按需）：
```powershell
# 管理员 PowerShell 5.1，cd 到 reinstall_inventory\
.\reinstall_interactive.ps1
```

- [ ] **Tencent.WorkBuddy**（已装✓）
- [ ] **SST.OpenCodeDesktop**（OpenCode 桌面，可选，CLI 已有）
- [ ] **Alibaba.Qoder**（Qoder IDE，腾讯）
- [ ] **Alibaba.Qianwen**（通义千问 / Qwen）
- [ ] **MoonshotAI.Kimi**（Kimi）
- [ ] **ByteDance.Doubao**（豆包）
- [ ] **Tencent.Yuanbao**（元宝）
- [ ] **Tencent.ima-copilot**（ima）
- [ ] **Microsoft.VisualStudioCode**（VS Code，阶段4扩展依赖）

## 阶段 3b — GUI AI 应用（winget 未覆盖，手动装）

- [ ] **Trae / Trae-cn**（字节 Trae，官方安装包，不在 winget 清单）
- [ ] 其他遗漏的 AI 客户端（如需）

## 阶段 4 — VS Code AI 扩展

- [ ] Cline / Roo Code / Continue / Kilocode / Augment / Lingma 等
      > VS Code 装好后，从市场重装；扩展列表可参考备份 `.vscode\extensions.json`（阶段1已还原）

## 阶段 5 — 逐代理登录 / API Key 校验

- [ ] **Claude**：`claude` 启动 → `/login` 或确认 `ANTHROPIC_API_KEY` 生效
- [ ] **Codex**：`codex` 启动 → 登录（OpenAI Key）
- [ ] **OpenCode**：确认 `~/.config\opencode` 配置（阶段1已还原）
- [ ] **Qoder / Qwen / Kimi / 豆包 / 元宝 / ima**：逐个打开应用登录账号
- [ ] **WorkBuddy**：确认我读到历史记忆
- [ ] **Trae**：登录

## 阶段 6 — MCP / 共享配置校验

- [ ] `.mcp.json` 还原后各 MCP server 可达（如 neodata / westock 等连接正常）
- [ ] `~/.ssh` / `~/.gitconfig` 就绪（`git pull` / `ssh` 正常）

---

## 建议执行顺序

1. **先做阶段 0 + 阶段 1**（Node 接线 + 配置还原）——这两步一通，绝大多数 AI 直接复活。
2. 再跑阶段 3 勾选 GUI 应用。
3. 阶段 2 的 Codex 单独补装。
4. 阶段 4/5/6 收尾校验。

> 量化环境（Python 重建、QuantLab venv、QMT 策略）按 Runbook 3.6 单独做，不急，可等 AI 全活后再说。
