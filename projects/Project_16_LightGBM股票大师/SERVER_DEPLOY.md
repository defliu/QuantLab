# 股票大师 · 服务器部署指导（全套迁移）

> 生成/更新：2026-08-22（**已按路径集中配置化改造更新**）
> 适用范围：将「TraeWork 选股 + miniQMT 执行/盯盘 + 本地数据源」整套迁移到服务器无人值守运行
> ✅ **路径已集中配置**：数据/模型脚本统一走 `data_config.py`，交易/盯盘脚本统一走 `qmt_config.py`。部署到服务器**只需改这两个配置文件的顶部路径/账号**，其余脚本零修改。
> ⚠️ **部署前必读（2026-08-22 审计警示）**：第三方审计+验证已确认原回测口径（close→close）与实盘系统性错位（官方日均超额 +0.676% → 可执行口径实测 -0.109%~-0.278%）。**实盘部署前必须先完成可执行口径回测验证**（任务见 `TODO_PENDING.md`「修正回测口径重跑」）；未验证通过前，仅可按模拟盘流程部署，不得以旧口径回测数字作为实盘收益依据。详见 `第三方审计报告_20260822.md` / `第三方审计验证报告_20260822.md`。

---

## 一、部署前提与拓扑（务必先读）

**关键约束：QMT/miniQMT 是 Windows 客户端**（xtquant 通过本地进程通信连接客户端），所以：

- **交易（qmt_trader/qmt_monitor/qmt_clear/qmt_mini_test）和实时行情（xtdata_update）必须在 Windows 服务器上运行**（Windows Server 或长期开机的 Windows 桌面机）
- 纯数据/模型部分（build_features/deploy_predict/factor_ic_monitor 等）由 `data_config.py` 统一指路，可跨平台（Linux 也能跑，但数据要挂载到对应绝对路径）
- **推荐拓扑**：一台 Windows 服务器 = 全部（QMT 客户端 + Python + 数据 + 定时任务），最省事

```
Windows 服务器
├── 国金 QMT/miniQMT 客户端（登录常驻） ← xtquant 通信
├── Python 3.10 + 依赖
├── 代码：D:/quant_server/app/           ← data_config.py / qmt_config.py 指向此布局
├── 数据：D:/quant_server/astock（主库） + app\data_live（增量）
├── 模型：D:/quant_server/models/
└── 定时任务：Windows 计划任务 + TraeWork Schedule（TDX 复核）
```

> 以下统一采用建议路径 `D:\quant_server\`。你可在 `data_config.py` / `qmt_config.py` 里改成一处自定义即可，不必逐文件改。

## 二、需迁移的资产清单

| 类别 | 来源路径（本地） | 迁移目标（服务器建议） | 大小/说明 |
|---|---|---|---|
| 代码 | `d:\trae_workspace\projects\Project_16_LightGBM股票大师\*.py` | `D:\quant_server\app\` | 27 个 py（**含 data_config.py、qmt_config.py 两个配置文件**）+ 9 个 md（含审计三件套） |
| 审计文档 | `第三方审计交接书.md`、`第三方审计报告_20260822.md`、`第三方审计验证报告_20260822.md`、`TODO_PENDING.md` | `D:\quant_server\app\` | 部署前必读（见顶部审计警示） |
| 主库行情 | `E:\astock\daily\stock_daily.parquet` | `D:\quant_server\astock\daily\` | 1.3 GB，`data_config.MAIN_DAILY` 指此 |
| 主库财务 | `E:\astock\finance\*.parquet` | `D:\quant_server\astock\finance\` | 553 MB，`data_config.FIN_*` 指此 |
| 主库基础 | `E:\astock\basic\` | `D:\quant_server\astock\basic\` | 可选，`data_config.BASIC_DIR` 指此 |
| 分钟线 | `E:\astock\minute\` | 可选（盯盘若用 tick 实时可不用） | 67 GB，按需迁移 |
| 股票池 | `D:\QuantLab\data\universe_all_a.csv` | `D:\quant_server\app\data\universe_all_a.csv` | `data_config.UNIVERSE` 指此 |
| 特征面板 | `Project_16\data\feature_panel*.parquet` | `D:\quant_server\app\data\` | 670+694 MB |
| 特征定义 | `data\features*.json` | 同上 | — |
| 模型 | `D:\QuantLab\models\lgb_model*.txt` | `D:\quant_server\models\` | v1/v2/v3，`data_config.MODEL_DIR` 指此 |
| 选股/报告 | `data\selections\` + `*report.*` | `app\data\` | — |
| 临时增量库 | `data_live\` | `app\data_live\` | — |
| 交易/信号记录 | `data\qmt_trade_log.csv`、`qmt_signal.json` | `app\data\` | — |

> ⚠️ 迁移前在本地先跑一次 `python deploy_predict.py --model v3` 和 `python factor_ic_monitor.py`，把最新面板/报告一并带上。

### 部署进度记录（2026-08-22 实测）

| 资产 | 目标目录 | 状态 | 核对 |
|---|---|---|---|
| 代码 + 文档（27 py + 9 md） | `D:\quant_server\app\` | ✅ 已部署 | — |
| 模型 v1/v2/v3（`lgb_model.txt`/`_v2`/`_v3`） | `D:\quant_server\models\` | ✅ 已传输 | 17.3 MB，大小一致 |
| 主库行情 `stock_daily.parquet` | `D:\quant_server\astock\daily\` | ✅ 已传输 | 1.24 GB，大小一致，可读 |
| 财务数据 8 个 parquet | `D:\quant_server\astock\finance\` | ✅ 已传输 | 574 MB，大小一致 |
| 特征面板 v1/v2/v3 + `features*.json` | `D:\quant_server\app\data\` | ✅ 已传输 | 2.0 GB，大小一致 |
| 服务器环境 Python 3.10.11 + 全部依赖 | — | ✅ 就绪 | lightgbm 4.7 / optuna 4.9 |
| QMT 客户端在线 + xtquant 通信 | — | ✅ 验证通过 | — |
| 交易/行情通道（步骤 4-3 / 4-4） | — | ✅ 实测通过 | — |
| 选股链路（步骤 4-5） | — | ⏳ 待服务器跑通 | `python deploy_predict.py --model v3 --top-k 5` |
| 定时任务（步骤 5） | — | ⏳ 待配置 | — |

> 传输方式：原开发机 robocopy `/MT:8` 至 `\\192.168.31.131\d\quant_server\`，目标文件大小与源逐一核对一致。
> 注意：`feature_panel_v3.parquet` 是 v2 精简 27 特征的定制版（IC 监控回灌），`build_features_v2.py` 只产出 v2 全量；服务器周更重训后如需重建 v3 面板，按 `WORKFLOW_DEPLOY.md` 的 v3 精简步骤执行，且重建仅用训练期特征选择（见 `TODO_PENDING.md` 审计项）。

## 三、部署步骤

### 步骤 0：服务器系统与环境

```powershell
# Windows Server，安装 Python 3.10（64 位，勾选 Add to PATH）
# 安装依赖
pip install lightgbm pyarrow scikit-learn scipy optuna optuna-integration

# 验证
python -c "import lightgbm, pyarrow, pandas; print('env OK', lightgbm.__version__)"
```

### 步骤 1：安装国金 QMT/miniQMT 客户端

1. 在服务器安装「国金 QMT 交易端」（模拟或实盘），安装到**纯英文路径**（如 `D:\QMT`），避免中文路径（LightGBM 写模型在中文路径会失败）
2. 启动 `bin.x64\XtMiniQmt.exe` 并登录（测试账号 70180771 或实盘账号）
3. 确认服务器上能看到 `bin.x64\Lib\site-packages\xtquant`（含 `IPythonApiClient.cp310-win_amd64.pyd`，匹配 Python 3.10）
4. **保持客户端常驻登录**（设开机自启：任务计划程序 → 登录时启动 XtMiniQmt）

### 步骤 2：迁移代码与数据

```powershell
# 服务器创建目录
New-Item -ItemType Directory -Force -Path 'D:\quant_server\app','D:\quant_server\astock','D:\quant_server\models'

# 拷贝代码（含所有 .py + 手册与审计文档）
Copy-Item 'd:\trae_workspace\projects\Project_16_LightGBM股票大师\*.py' 'D:\quant_server\app\'
Copy-Item 'd:\trae_workspace\projects\Project_16_LightGBM股票大师\*.md' 'D:\quant_server\app\'

# 拷贝数据（用 robocopy 支持大文件断点）
robocopy 'E:\astock\daily' 'D:\quant_server\astock\daily' /E
robocopy 'E:\astock\finance' 'D:\quant_server\astock\finance' /E
robocopy 'E:\astock\basic' 'D:\quant_server\astock\basic' /E
robocopy 'D:\QuantLab\data\universe_all_a.csv' 'D:\quant_server\app\data\' /E
robocopy '...\Project_16...\data' 'D:\quant_server\app\data' /E
robocopy '...\Project_16...\data_live' 'D:\quant_server\app\data_live' /E
Copy-Item 'D:\QuantLab\models\lgb_model*.txt' 'D:\quant_server\models\'
```

### 步骤 3：只改两个配置文件的顶部路径（⭐ 核心，已集中配置化）

**部署到服务器只需改这两处，其余脚本全部零修改：**

| 配置文件 | 修改项 | 本地（原值） | 服务器改为 |
|---|---|---|---|
| `data_config.py` | `ASTOCK_DIR` | `E:/astock` | `D:/quant_server/astock` |
| `data_config.py` | `UNIVERSE` | `D:/QuantLab/data/universe_all_a.csv` | `D:/quant_server/app/data/universe_all_a.csv` |
| `data_config.py` | `MODEL_DIR` | `D:/QuantLab/models` | `D:/quant_server/models` |
| `qmt_config.py` | `QMT_PATH` | `E:\国金QMT交易端模拟` | `D:\QMT`（服务器 QMT 安装路径） |
| `qmt_config.py` | `ACCOUNT_ID` | `70180771` | 服务器资金账号 |

> 派生说明（它们由上面的根配置自动算出，**无需单独改**）：
> - `data_config.py`：`MAIN_DAILY`、`FINANCE_DIR`、`BASIC_DIR`、`FIN_*`、`DATA_DIR`、`LIVE_DIR`、`model_file()` 全部由 `ASTOCK_DIR/UNIVERSE/MODEL_DIR` 派生。
> - `qmt_config.py`：`USERDATA`（`QMT_PATH/userdata_mini`）、`XTPACK`（`QMT_PATH/bin.x64/Lib/site-packages`）由 `QMT_PATH` 派生。

**改完到服务器确认：**
```powershell
cd D:\quant_server\app
python -c "import data_config as DC, qmt_config as C; print(DC.MAIN_DAILY); print(DC.model_file('_v3')); print(C.XTPACK); print(C.ACCOUNT_ID)"
```
输出应为服务器路径/账号即正确。

### 步骤 4：验证清单（逐项 smoke test）

```powershell
cd D:\quant_server\app

# 1. 数据可读
python -c "import pandas as pd; df=pd.read_parquet('D:/quant_server/astock/daily/stock_daily.parquet', columns=['close']); print('主库 OK', df.index.get_level_values('trade_date').max())"

# 2. 模型可加载（注意英文路径）
python -c "import lightgbm as lgb; m=lgb.Booster(model_file='D:/quant_server/models/lgb_model_v3.txt'); print('模型 OK', m.num_trees())"

# 3. 行情通道（需 QMT 在线）
python -c "import sys; sys.path.append(r'D:\QMT\bin.x64\Lib\site-packages'); from xtquant import xtdata; print('行情', xtdata.get_full_tick(['001378.SZ']))"

# 4. 交易通道（只查资产，不下单）
python qmt_clear.py --keep 000000.SZ     # dry-run 会查持仓并打印（保留空即全清预览）

# 5. 选股链路
python deploy_predict.py --model v3 --top-k 5

# 6. 增量更新链路
python xtdata_update.py --start <主库最后日+1>
python merge_live_features.py --date <最新交易日>
```

### 步骤 5：无人值守（定时任务）

**A. 服务器本地（Windows 计划任务）——不依赖 TraeWork 的纯脚本任务**
```powershell
# 盘后 16:30 增量+选股（需 QMT 在线）
schtasks /Create /TN "quant_daily_update" /TR "cmd /c cd /d D:\quant_server\app && python xtdata_update.py && python merge_live_features.py --date 2026-08-20 && python deploy_predict.py --model v3 --top-k 10 >> D:\quant_server\app\data\daily.log 2>&1" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 16:30
# 周一 17:00 周更重训
schtasks /Create /TN "quant_weekly_retrain" /TR "cmd /c cd /d D:\quant_server\app && python build_features_v2.py && python train_optuna.py --panel v3 --n-trials 20" /SC WEEKLY /D MON /ST 17:00
```

**B. TraeWork Schedule（需要 TDX MCP 授权的任务——实时复核）**
- 每日 9:45 开盘后：用 TDX MCP 采集候选资金/新闻/板块 → 写 `tdx_review.json` → `python review_full.py` → 完整版清单（这个走 TraeWork 会话，因为需要 TDX MCP）

> 说明：纯脚本任务（增量/选股/重训/盯盘）放服务器计划任务最稳；TDX 实时复核因为走 MCP 授权，放 TraeWork 定时任务。

## 四、数据更新策略（服务器上）

- **主库（周更）**：每周从本地/数据源更新 `D:\quant_server\astock\`，更新后跑「周一重训」任务重建面板+模型
- **每日增量**：`xtdata_update.py`（需 QMT 在线）拉当日行情到 `data_live`，`merge_live_features.py` 更新最新特征，`deploy_predict.py` 出次日候选
- **交易日判断**：计划任务在节假日也会触发，脚本需容错（增量无数据则跳过）——建议在批处理里判断 `trade_calendar` 或让脚本对空数据 graceful 处理

## 五、常见问题与排障

| 现象 | 原因 | 处理 |
|---|---|---|
| xtdata 连不上行情 | QMT 客户端未登录/掉线 | 重新打开 XtMiniQmt 登录；设开机自启 |
| 模型保存/加载中文路径失败 | LightGBM Windows 中文路径 | 模型/数据目录一律用英文路径（`D:\quant_server\models\`） |
| `data_config` 指向仍为 E:/astock | 服务器未改配置文件 | 改 `data_config.py` 顶部三个路径后重跑 |
| 交易通道连不上 | `qmt_config.QMT_PATH` 非服务器路径 | 改 `QMT_PATH` + `ACCOUNT_ID` 后重跑 |
| xtdata 拉不到增量 | 本地缓存落后 | 脚本已内置 download_history_data，先下载再读 |
| TDX 复核为空 | 盘前无实时数据/无新闻 | 9:30 后复核；如实标注"无数据"不编造 |
| 定时任务没执行 | 计划任务未配置对/客户端离线 | 检查 schtasks 状态 + 客户端在线 |
| 交易失败 | 账号/资金/非交易时段 | 检查 qmt_config ACCOUNT_ID、可用资金、交易时段 |
| 飞书推送失败/发错人 | 环境变量/身份处理不当 | 按 WORKFLOW_DEPLOY.md 第十一节：移除 `LARKSUITE_CLI_APP_ID`/`LARKSUITE_CLI_USER_ACCESS_TOKEN`、设 `LARKSUITE_CLI_STRICT_MODE=off`，bot 身份发 `ou_76deaecde50e10576f8fdc8ba954a7b0` |

> **飞书推送迁移提示**：服务器上飞书推送依赖 `lark-cli`（`C:\Users\Administrator\.trae-cn\plugins\...\lark\...\bin\lark-cli.exe`）及其 `~/.lark-cli` 配置。迁移到服务器时需一并安装 lark-cli、重建登录态，并将 `qmt_config.py` 的 `LARK_CLI` / `FEISHU_OPEN_ID` 指向服务器环境。推送规则与排障详见 WORKFLOW_DEPLOY.md 第十一节。

## 六、安全与备份

1. **账号与密钥**：`qmt_config.py` 的账号/密码不要提交到代码仓库；服务器上单独保护
2. **数据备份**：主库（astock）建议每周备份快照；面板/模型重训前备份旧版本（`lgb_model_v3_bak.txt`）
3. **交易安全**：`--live` / `--auto-sell` 是真实委托，实盘前务必模拟盘完整跑通；建议先以"仅预警"模式运行 1-2 周
4. **日志**：所有任务输出重定向到 `data\*.log`，方便排查

## 七、部署前自查清单（新增）

在动手迁移前，先在本地确认改造已完成，避免漏改：

- [ ] `data_config.py` 已创建，`ASTOCK_DIR / UNIVERSE / MODEL_DIR` 三个根路径集中于此
- [ ] `build_features*`、`train_*`、`deploy_predict`、`backtest_dual`、`factor_ic_monitor`、`merge_live_features`、`xtdata_update` 均已从 `data_config` 导路径（grep 确认无残留硬编码 `E:/astock`、`D:/QuantLab/models`）
- [ ] 本地已跑通过：`deploy_predict.py --model v3`、`merge_live_features.py`、`factor_ic_monitor.py`（本次部署演练已全部通过）
- [ ] `qmt_config.py` 中 `QMT_PATH / ACCOUNT_ID` 确认过

> 本地自查命令：`findstr /S "E:/astock D:/QuantLab/models E:\\astock" *.py` 在迁移前跑一遍，应只命中 `data_config.py` 与 `.md` 文档，无其他脚本残留硬编码。

---
*仅供个人量化研究使用，不构成投资建议。市场有风险。*