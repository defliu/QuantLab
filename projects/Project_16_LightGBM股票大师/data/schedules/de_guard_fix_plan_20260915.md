# DE 修复方案（第一步产出，2026-09-15）

> DE（GLM-5.3）第一步产出修复方案，经 stdout 返回（未落盘，TRAE 代为落盘存档）。
> 方案要点整理如下，第二步由 DE 执行落码，TRAE 交叉验证。

## R1a + R5（guard_backfill_v13.py，一次改完）

- **R1a F3 补充**：补跑链路增加 F3 补充——先读 `data/cache/review_<date>.json` 的 catalysts（若含候选则用），再用 Tushare 业绩公告（`tushare_source.earnings_anns_map`）兜底，仍缺失则 source 标注 `no-f3` 并日志醒目提示。
- **R5 新鲜度校验**：`latest_panel_date()` 校验面板最新日=最近已收盘交易日（对齐 nightly_check 口径），落后则告警。
- **R5 拒绝历史日改签**（防 look-ahead）：

```python
today = time.strftime("%Y%m%d")
if args.date != today and not args.allow_restamp:
    raise RuntimeError("--date %s 非今日：实时数据改签历史日属 look-ahead，已拒绝（复现研究用 --allow-restamp）" % args.date)
```

- **R5c 子进程超时**（防挂起无声僵死）：两处 `subprocess.run(...)` 加 `timeout=600`，捕获 `subprocess.TimeoutExpired` 转 `RuntimeError("... 超时 600s")`。

## R1b：上游 9:25 缓存扩容（任务指令层，非代码 diff）

9:25 任务（b0254f11）指令增加一步：对 `data/selections/{当日}_model_top10.csv`（V1.3 top10）采集 F3 催化，**合并**写入 `data/cache/review_<date>.json` 的 `catalysts` 键（现有 G2 候选条目保留）。按「脚本优先」原则，中期建议脚本化为 `premarket_catalyst_cache.py`（Tushare 公告/新闻），作为独立小任务另立。

## R2：T=0 清仓（需拍板 A/B → 已拍板 A）

- **方案 A（已选定）**：guard 在 T=0（沪深300≤-1.5%）且当日未换仓时，执行 `rebalance_daily.py --date <Date> --top 0 --live`（清仓），复用 P1-3 exit code 检查模式（失败不写锁+告警）。
- 方案 B（弃用）：告警人工清仓。

## R3：告警通道（P2-4）

核实 `qmt_card.py send_lark_card` / `qmt_bridge_client.py notify_feishu` 的 lark-cli 路径与配置读取；定时环境 lark-cli 不可用时增加第二告警通道（本地日志+写告警文件+退出码）。

## R4：CheckOnly 补跑分支（P2-5）

`rebalance_daily_guard.ps1` CheckOnly 缺清单时改为执行 `guard_backfill_v13.py` 生成清单但不下单（新增 `-BackfillOnly` 演练），验证补跑链路。

## 实施顺序（DE 建议）

1. R1a + R5（同一文件）→ py_compile + 非交易时段 --no-deploy 实跑验证 F3 非 0
2. R4 → -BackfillOnly 演练 + BOM/PSParser 校验
3. R3 → --no-send 断网演练
4. R2 → 方案 A 实施
5. R1b → 改 9:25 任务指令（TRAE 侧执行）
6. 收尾：更新看板 + repowiki

## 遗留核对项（非方案代码）

- 明日 10:05 首跑：核对新版 guard 文案 + backfill 分支首次生产执行
- 今晚 16:30：核对 `20260915_model_top10.csv` 为管道产出（09-14 管道已漏产，若再现需立新任务查 16:30 管道）
