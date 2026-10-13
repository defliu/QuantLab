# 飞书 ↔ WorkBuddy AI 互通（feishu_bridge）

> 建立日期：2026-10-09 · 状态：**双向通道已打通（发送实测成功 / 接收长连接已连通）**
> 归属：QuantLab 基础设施（非策略模块）

## 1. 这套东西解决什么

让诚哥在**飞书里直接和 WorkBuddy AI 对话**，并让 AI 能主动向飞书推送消息。
与此前 P16 的单向推送（QuantLab → 飞书）不同，这里是**双向**的。

## 2. 关键机制（必读，踩坑记录）

### 2.1 lark-cli 在 Agent 环境里拒绝读配置

`lark-cli` 检测到 Agent 环境变量（`OPENCLAW_CLI` / `HERMES_HOME` / `LARK_CHANNEL`）时，
会拒绝读取 `~/.lark-cli/config.json`，直接报：

```
openclaw context detected but lark-cli is not bound to it
```

**为什么 `config bind` 在本机走不通**（2026-10-09 实测）：

| 源 | 结果 |
|---|---|
| `--source openclaw` | 缺 `C:\Users\Administrator\.openclaw\openclaw.json`（该文件不存在） |
| `--source hermes` | `does not match detected Agent environment (openclaw)` |
| `--source lark-channel` | 同上 |

**解法**：调用 lark-cli 时清掉这些环境变量。`bridge.py::clean_env()` 统一处理，
这与 P16 `qmt_monitor._env_for_cli()` 是同一套做法，是本机唯一可用路径。

### 2.2 能力矩阵（实测）

| 能力 | 结论 | 说明 |
|---|---|---|
| 发送消息（私聊/群） | ✅ | bot 身份，`im +messages-send` |
| 接收消息事件 | ✅ | WebSocket 长连接 `im.message.receive_v1`，**无需公网 IP** |
| 按 `message_id` 回复 | ❌ | 应用缺 `im:message` 系列 scope → `99992354`（权限掩码错误） |
| 读取消息列表 | ❌ | 应用缺 `im:message:readonly`，错误码 `230027` → 一律走事件流 |
| bot 列私聊会话 | ❌ | 飞书隐私保护，bot 身份不可列 p2p chat |

> **回复的落地方式**：`bridge.py reply` 先试 `+messages-reply`（需 scope），失败则自动回退为
> 「按 `chat_id` 发新消息」。在 1:1 私聊里两种方式体验一致。
> 之所以能回退，是因为事件载荷里带了 `chat_id`——所以**不要**只存 `message_id`。

> **重要**：事件长连接的打通，绕开了 P16 卡了两个月的「webhook 需公网映射」难题。
> 若将来 P16 卡片回调要上线，可优先考虑改造成长连接方案。

### 2.3 调度粒度限制（设计前提）

WorkBuddy 定时任务的最小调度粒度是 **1 小时**（rrule 仅支持 HOURLY/DAILY/WEEKLY/MONTHLY/YEARLY）。
因此架构上拆成两层：

```
飞书用户消息
   │
   ▼
listen.py（常驻，WebSocket 长连接，实时）──► data/feishu_bridge/inbox.jsonl
   │                                            （捕获零丢失）
   ▼
WorkBuddy 定时任务「飞书消息桥接：处理并回复」（每小时）
   └─ pending → AI 处理 → reply ──► 飞书回复
```

**已知代价**：回复延迟最长约 1 小时（受平台调度粒度限制）。

## 3. 文件清单

| 文件 | 作用 |
|---|---|
| `bridge.py` | 桥接工具：`whoami` / `pull` / `pending` / `reply` / `send` / `mark` |
| `listen.py` | 常驻监听：循环消费 `im.message.receive_v1`，新消息追加 inbox |
| `start_feishu_listen.ps1` | 守护启动脚本（幂等 pid 模式，**必须保持 UTF-8 BOM**） |
| `data/feishu_bridge/inbox.jsonl` | 收到消息（append-only） |
| `data/feishu_bridge/processed.json` | 已处理 message_id |
| `data/feishu_bridge/listen.log` | 监听日志（含 ready / websocket connected 标记） |
| `data/feishu_bridge/listen.pid` | 守护进程 pid |

## 4. 常用命令

```bash
PY="C:/Users/Administrator/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"

# 健康检查（bot 身份 + token）
"$PY" scripts/feishu_bridge/bridge.py whoami

# 查看未处理消息
"$PY" scripts/feishu_bridge/bridge.py pending

# 主动私聊诚哥
"$PY" scripts/feishu_bridge/bridge.py send --text "盘后复盘已完成"

# 回复某条消息（自动标记已处理）
"$PY" scripts/feishu_bridge/bridge.py reply --message-id om_xxx --text "收到，正在跑回测"

# 手动兜底抓取（常驻监听掉线时）
"$PY" scripts/feishu_bridge/bridge.py pull --minutes 5
```

## 5. 计划任务

| 任务名 | 触发 | 作用 |
|---|---|---|
| `QuantLab_FeishuListen_Boot` | 用户登录时 | 拉起监听 |
| `QuantLab_FeishuListen_Guard` | 每天 00:00 起，每 5 分钟重复 | 掉线自愈（幂等，已在跑则退出） |

两个任务均为 `S4U` / `RunLevel=Limited`，Action = `powershell -File start_feishu_listen.ps1`。
注册方式严格使用 `Register-ScheduledTask` 对象方式（禁止 XML/schtasks 拼接，见 AGENTS.md 规则）。

## 6. 关键标识

| 项 | 值 |
|---|---|
| 飞书应用 | `cli_aade2524ddbd9bd6`（与 P16 盯盘推送共用） |
| 诚哥 open_id | `ou_bd13444d8ea53c28249c669f43f3eeff` |
| 机器人↔诚哥 私聊 | `oc_3bb09b777437e5a79a3fa371c4bf7689` |
| 股票投研群 | `oc_f436148efffabf9383cf7075bca9b17f` |

> ⚠️ open_id 是**应用维度**的，跨应用不可混用。
> ⚠️ `~/.lark-cli/config.json` 是 P16 推送与本案**共用**的凭据文件；
> 已备份为 `config.json.bak_20261009`。任何「新建应用 / config init」操作前必须先备份。

## 7. 故障排查

| 现象 | 根因 | 解决 |
|---|---|---|
| `openclaw context detected but lark-cli is not bound` | Agent 环境变量存在 | 用 `bridge.py`（内部已清变量），不要裸调 lark-cli |
| `230027 access denied` | 应用缺对应 scope | 到开放平台申请（如 `im:message:readonly`） |
| `99992354 not a valid open_message_id` | 应用缺 `im:message` 系列 scope（权限掩码），按 message_id 定位失败 | `bridge.py reply` 已自动回退为按 `chat_id` 发新消息 |
| 报「该消息不在 inbox 中」 | **手抄 message_id 出错**（实测漏抄一个字符，肉眼完全看不出差别） | **ID 一律从 `inbox.jsonl` 或 JSON 输出程序化读取，不要手打**；`_norm_id()` 仅作兜底 |
| `open_id cross app` | 用了别的应用维度的 open_id | 换成本应用下的 open_id |
| 监听进程不在 | 进程被结束 | 等 Guard（≤5 分钟）自愈，或手动 `Start-ScheduledTask -TaskName QuantLab_FeishuListen_Guard` |
| ps1 解析报「意外的标记」 | BOM 丢失 | 重新写入 UTF-8 BOM（项目规则 T-20260903-015） |

## 8. 待办

- [x] 端到端闭环实测（2026-10-09）：发送 → 接收（诚哥消息被长连接实时捕获）→ 回复，全部通过
- [ ] 评估是否需要把回复延迟从「小时级」降到「分钟级」（需另找触发机制）
- [ ] 评估是否给应用申请 `im:message:readonly` 等 scope
