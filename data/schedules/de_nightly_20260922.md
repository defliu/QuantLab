# Project_16 夜间检修 FAIL 分析报告（2026-09-22）

- **任务**：DE 夜间检修 FAIL 保障分析（只读，不下单、不 --live、不改文件、不 commit）
- **触发**：`nightly_check_20260922.md`（FAIL 3 / WARN 1 / 32 项，01:00 由 `Quant_P16_NightlyCheck_0100` 生成，exit=1）
- **分析方式**：全部结论基于原始日志与数据实测复算（证据附 file:line），无臆测
- **总评**：**3 项 FAIL 均不阻断 09-22 开盘交易**，但 B2/B5 建议当日处理（各约 5~30 分钟），D1 排期修探活脚本即可

---

## 一、FAIL 清单

| # | 模块 | 检查项 | 报告说明 | 定性 |
|---|---|---|---|---|
| 1 | B | B2 模型-面板同步 | 校验结果: 不一致 (exit=1) | **绑定记录未随回滚更新**（监控口径失真，非模型/面板真故障） |
| 2 | B | B5 enh面板最新日 | enh 2026-09-11，滞后 10 天>阈值7 | **refresh_panel_enh 校验门禁口径过严致面板自锁 6 天**（真问题，影响 G2 链） |
| 3 | D | D1 悟道探活 | market_overview | **探活误报**（服务端不再回退昨日数据，凌晨请求"今日"必失败） |
| 附 | E | E6 TW调度快照 (WARN) | 快照 2026-09-13 过期 | 人工刷新事项，见第四节 |

---

## 二、逐项根因与证据

### B2 模型-面板同步 FAIL —— 回滚未回写绑定记录

**时间线**（证据：`data/schedules/retrain_20260921_170003.log`）：

| 时间 | 事件 | 证据行 |
|---|---|---|
| 09-21 18:07:39 | 周训产出候选 `lgb_model_v3_retrain_20260921.txt`（6617 树，testIC 0.04315 / ICIR 0.3428） | log L106-123 |
| 09-21 18:07:41 | auto_promote 门禁 G0~G6 全 PASS → promote_model.py 复制候选至正式模型 + 写绑定记录 | log L124-154 |
| 09-21 18:41:25 | **策略层门禁 FAIL**：候选 V1.4 配置 excess +0.0008 vs 旧模型 +0.0011（差 -0.0003 > 容差 +0.0002）→ 拒绝上线 | log L171-173 |
| 09-21 18:41:26 | `run_scheduled.ps1:274` `Copy-Item $oldFormal $formalModel -Force` **回滚正式模型至 09-14 版，但不回写 `model_panel_binding.json`** | log L174-175 |
| 09-21 18:41:27 | verify 立即 FAIL（当时已可见），日志误诊为「绑定写入异常」 | log L176-183 |

**现状实测**（本任务只读复算）：

- 正式模型 `D:/QuantLab/models/lgb_model_v3.txt`：4151 树 / mtime 2026-09-14 17:44:05 / **MD5 与 `lgb_model_v3_retrain_20260914.txt` 完全一致**（= 回滚后的 09-14 模型，B3 的 7305KB 与此自洽）
- 绑定记录 `data/model_panel_binding.json`：仍记录被拒候选（6617 树 / mtime 1789985258 = 09-21 18:07:38 / candidate retrain_20260921 / test_ic 0.04315）
- `verify_model_panel_sync.py` 复跑：`树数 4151 != 绑定 6617` + `mtime 不一致` → exit 1（与 01:00 检修一致）

**根因**：策略层门禁回滚（设计内动作）只还原了模型文件，没有把绑定记录同步回滚。绑定记录因此长期指向一个**从未上线过的候选模型**。`run_scheduled.ps1:287` 的「绑定写入异常」提示为误诊——绑定写入正常，是回滚路径漏了回写。

**副作用（不止告警噪声）**：
1. verify 每晚恒 FAIL（假警报），掩盖真实同步监控；
2. 下周一 auto_promote 的 G3「不退步」基线将错用被拒候选的 testIC 0.04315（门槛 0.0388），而正确基线是现役 09-14 模型的 0.04450（门槛 0.0401）——门禁偏松 0.0013；
3. 绑定 panel_max_date=09-21 与现役模型实际训练面板（09-14）不符，掩盖真实的训练/推理 skew 口径。

### B5 enh 面板滞后 10 天 —— turnover_rank 精确复现门禁自锁

**时间线**（证据：`data/schedules/daily_*.log`、`retrain_20260921_170003.log`）：

| 时间 | 事件 |
|---|---|
| 09-12 22:43（周六） | enh 面板最后一次成功写入（末日 09-11，T-20260912-004 人工修复落地） |
| 09-14 ~ 09-21 每日 16:3x | daily 链 `refresh_panel_enh.py` **连续 6 个交易日 exit 1**：`重叠期 turnover_rank 复现率 0.00%（<99%，口径漂移）→ 校验失败，保留旧面板不动` |
| 09-21 17:12 | retrain 链同样失败 → `check_enh_freshness.py` 判滞后 10 天 > 7 → exit 2 **阻断 G2 周训**（log L59-68、L218：本周围更未重训 G2） |

**实测诊断**（本任务只读复算，2026-09-22 凌晨）：

- 09-11 当日换手率**数值本身完全一致**：merged 原生 `turnover_rate` vs `vol/float_share` 反推，ratio 中位数 1.0000、corr 1.0000 —— 排除"反推口径漂移"；
- 但 `turnover_rank`（按日全市场百分位）：旧面板存储值 vs 现算值 **corr=0.9972，精确一致率（atol=1e-6）= 0.00%**，2845/2846 行全部位移；
- 结论：`merged_daily_full` 对 09-09~09-11 的**行集在 09-12 之后被回填/扩容**（xtdata 增量重下补齐当日缺失股票），排序宇宙变化 → 全市场 pct-rank 整体位移。方向确凿；当时精确行集不可回溯（无快照，见第五节）。

**根因**：`refresh_panel_enh.py:193-198` 对 `turnover_rank` 采用 atol=1e-6 的**精确一致**门禁——百分位排名特征对 merged 后期回填零容忍。旧面板末日（09-09~09-11）一旦滑入"会被增量重下回填"的日期区，该门禁即**确定性失败**；而门禁失败 → 面板冻结 → 滞后增大 → 对照日期离当下越来越远、回填累积越来越多 → **永远无法再通过（自锁）**。这是检测口径设计缺陷，**不是数据损坏**（换手数值、事件特征复现率均 100%）。

**影响链**：`build_g2_daily.py:175-177` 取 enh 面板**末日行**做 asof 基底 → G2 每日实盘候选的 6 个慢变量（turnover_rank / industry_mom20 等）停留在 09-11（陈旧 10 天且每日加重）；ENS 链消费 G2 候选，同样受累；G2 周训 09-21 已被硬门禁挡掉（本周未重训）。V1.3 链（v3 面板）不受影响。

### D1 悟道探活 FAIL —— 探活参数撞上服务端行为变更

**复现**（本任务实测，与 01:00 检修同错）：

```
[WUDAO-ERR] RuntimeError('悟道 RPC 错误: {"code": -32052, "message": "今日市场概要尚未就绪
（requested=2026-09-22）。不会回退为前一交易日数据。", "data": {"error": "MARKET_SUMMARY_NOT_READY",
"statusCode": 425, ... "hint": "开市后重试今日；若要昨收复盘请显式传 date=2026-09-21..."}}')
```

**根因**：`nightly_check.py:299` 探活以空参 `{}` 调 `market_overview` → 服务端按"今日"取数；凌晨 01:00 今日数据必然未就绪。**服务端近期变更了行为：不再自动回退为前一交易日数据**（错误文案明示）。证据：`datasource_health.json` 中 wudao `last_ok=2026-09-20 01:00:12`、随后 09-21/09-22 连续两晚 FAIL——行为变更发生在 09-20 ~ 09-21 之间。**悟道连通性本身正常**（RPC 有结构化应答、延迟 107ms 记录在案）。

**副作用**：`datasource_health.json` 的 wudao fail_count 被误报污染（已累计 2 次）。

---

## 三、影响评估（次日开盘前是否必须处理）

| 项 | 是否阻断 09:15 候选生成/交易 | 影响与时限 |
|---|---|---|
| B2 | **否**。正式模型（09-14 版）为策略层门禁**有意保留**，V1.3 链 09:15 照常出候选（模型-面板 skew 7 天，处设计容忍边界，与上周同水位） | 建议当日修复绑定（5 分钟）；不修则每晚假 FAIL + 下周 G3 门禁基线错误 |
| B5 | **否**（不阻断交易），但 G2/ENS 每日实盘候选慢变量持续陈旧且每日加重；**拖到 09-28 周一将再次阻断 G2 周训** | 强烈建议当日修复（改门禁口径 + 重刷面板 + 补跑 G2 重训） |
| D1 | 否。纯探活误报，悟道源可用 | 修复探活脚本排期即可；修复前该 FAIL 可忽略 |
| E6 (WARN) | 否 | 本周内人工刷新快照（双调度器红线 T-20260910-008） |

另记一处**告警时序倒挂**：refresh_panel_enh 自 09-14 起 daily 链每日 log 告警，但无升级推送；G2 周训 09-21 已被硬门禁实际阻断，而夜间检修 B5 直到 09-22（滞后跨过 7 天阈值）才 FAIL——**业务阻断先于告警 1 天+**。建议后续把「daily 链同一失败连续 N 日」纳入升级告警。

---

## 四、修复建议（精确动作）

### B2（当日，人工，约 5 分钟）

1. **回滚绑定记录**至现役模型口径（在项目目录执行；把被拒候选字段还原为 09-14 模型）：

```powershell
& "C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe" -c "import json,os; p=r'data\model_panel_binding.json'; b=json.load(open(p,encoding='utf-8')); b.update({'model_trees':4151,'model_mtime':os.path.getmtime(r'D:/QuantLab/models/lgb_model_v3.txt'),'panel_max_date':'2026-09-14','candidate':'lgb_model_v3_retrain_20260914.txt','promote_time':'20260921_184126_rollback','note':'策略层门禁FAIL回滚至09-14正式模型(gate V1.4: cand +0.0008 < live +0.0011)','test_ic':0.04450,'icir':0.3552}); json.dump(b,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=2)"
```

2. **校验**：`python verify_model_panel_sync.py` → 应输出「一致」（今晚面板 09-21 vs 绑定 09-14，gap=7 恰在阈值内）。
   - 预期行为说明：明晚起 gap=8>7，verify 会以「面板超前绑定」FAIL——这是 verify 的**设计内真信号**（本周候选被拒、模型确已一周未更新，下周一成功 promote 后自动归零），与当前的假警报性质不同，勿再误诊。
3. **根修（下次代码窗口）**：`run_scheduled.ps1:272-275` 回滚分支补「绑定回写」动作（还原 trees/mtime/panel_max_date/candidate/test_ic/icir 六字段），并修正 `:287` 的「绑定写入异常」误诊文案为「回滚后绑定需回写」。

### B5（当日，约 30 分钟 + G2 补跑）

1. **修门禁口径**：`refresh_panel_enh.py:193-198` 对 `turnover_rank` 的重叠期校验从精确一致（`np.isclose atol=1e-6`）改为**相关性/容差口径**（与 `:201-205` industry_mom20 同思路），建议 Spearman/Pearson corr ≥ 0.99 或 mean|Δrank| ≤ 0.05——按当前实测 corr=0.9972 应放行。事件特征（ex_days_since 等 4 项）保留精确一致门禁不变。
2. **手动重刷**：`python refresh_panel_enh.py` → 确认面板末日推进到 2026-09-21、六项校验全过。
3. **复核**：`python check_enh_freshness.py` → 应输出 `[OK] enh 面板新鲜（<=7 天）`。
4. **人工补跑 G2 周训**（retrain log L218 的指引）：确认 enh 修复后补跑 train_g2 链（走 `run_scheduled.ps1 -Mode retrain` 或单独 G2 训练入口），核对 G2 live 指针更新、配置漂移复查不再跳过。

### D1（排期）

- `nightly_check.py` check_d（`:299` 附近）探活参数改为显式上一交易日：`market_overview "{\"date\": \"<recent_trade_date()>\"}"`（服务端 hint 明示该传参格式）；或将 `MARKET_SUMMARY_NOT_READY` 降级为 WARN。
- 修复前：该 FAIL 视为已知误报，不计入健康判定；必要时人工清理 `datasource_health.json` 的 wudao fail_count 污染。

### E6（本周人工）

- TW 侧用 Schedule list 刷新 `data/tw_schedule_snapshot.json`（更新 generated_at），核对无 Active 冲突任务后 E6 自然回 PASS。

---

## 五、数据缺失与核查限制（如实标注）

1. `D:\QuantLab\data\schedules\de_nightly_20260922.log` 为本 DE 会话自身运行日志且被进程占用，未读取；nightly 原始输出已从 `data/schedules/nightly_check_task.log`（09-20/09-21/09-22 三晚完整记录）取得，不影响分析。
2. `merged_daily_full` 在 09-12 当时对 09-09~09-11 的行集**无法回溯**（无快照）。「宇宙回填/扩容」系由换手数值完全一致而 rank 全体位移（corr 0.9972 / 精确一致 0.00%）反推，方向性结论可靠，精确行数差异不可考。
3. 悟道服务端行为变更时间只能定位在 09-20 01:00 ~ 09-21 01:00 之间（health last_ok/last_fail 分界），无服务端 changelog 可查。
4. 本机 `python` 不在 PATH（exit 9009），全部复算使用与定时任务同源的 TRAE SOLO CN Python（nightly_check_task.ps1:19 探测口径一致）。

---

**执行声明**：本任务全程只读——未下单、未 --live、未修改任何项目文件、未 commit；除本报告文件外无任何写入。所有诊断命令（verify_model_panel_sync.py 复跑、wudao 探活复测、parquet 只读比对）均不改变业务状态。

*生成：DE 夜间检修保障（de_nightly_20260922），2026-09-22 凌晨*
