# Project_22 RPS 每日监控 — DE 第三轮执行报告（2026-10-04）

任务书：`specs/DE_TASKS_03_20261004.md`。执行环境：`D:\Python311\python.exe`（3.11.15，pandas 3.0.5 / pyarrow 25.0.1 / numpy 2.4.6）。

## 0. 结论先行

> **本轮未修改 D:/astock 主库，5 个缺陷日（2026-08-03/04/05、09-14、09-18）为主库上游数据缺陷
>（`listed_days` 列整日大面积 NaN），60 交易日窗口（2026-07-03 ~ 2026-09-24）实际有效产出 55 天。**
> 「坏数据日显式跳过 + 缺陷台账留痕」已按业主拍板口径扶正进主程序 `rps_daily.py`；
> `verify_rps.py` 扩展至 8 项断言全绿（`[VERIFY-PASS] 8/8`）。
> **不声称「60 日回补完成」——真实口径为 55/60 天，5 天主库缺陷日按政策跳过并留痕。**

## 1. 改动文件清单

| 文件 | 改动 | 为什么 |
|---|---|---|
| `research/rps_daily.py` | ① 新增常量 `DEFECT_NAN_RATIO_THRESHOLD = 0.5` 与 `_DEFAULT_CONFIG["defect"]` 配置段；② 新增 `detect_defect_days(df, days, threshold)`（返回缺陷日列表 + 每交易日 NaN 比例 + 每交易日总行数）；③ 新增 `write_defects_manifest(...)`（覆盖写 `results/defects_manifest.csv`，列 `trade_date, listed_days_nan_ratio, total_rows, verdict, skipped`，verdict 固定 `MAIN_DB_DEFECT`、skipped 固定 `True`）；④ `main()` 在 `quality_gate` **之前**做缺陷日分流：缺陷日打印 `[SKIP-DATADEFECT]` 并跳过（不调用 compute_day/quality_gate/update_state/write_outputs），正常日走原有完整流程，`quality_gate` 一字未改；⑤ enrich 预取只对产出日发起；⑥ 各步骤补耗时输出；⑦ 启动时校验 `defect.action == skip_with_manifest`（改配置为 continue/silent 直接拒绝启动） | 任务 A：把缺陷日识别与跳过从任务书外 driver 扶正进正式主程序。**RPS 计算逻辑、收益窗口、PIT 池阈值、排名算法均未动。** |
| `config/rps_monitor.yaml` | 追加 `defect:` 段：`nan_ratio_threshold: 0.5` / `manifest: defects_manifest.csv` / `action: skip_with_manifest` | 任务 E：阈值与台账文件名入配置，不硬编码。 |
| `research/verify_rps.py` | 新增 4 项断言（原 4 项判据强度未动）：⑤ 窗口完整性（产出 + 缺陷 == 窗口交易日，以日历为真值）；⑥ 缺陷台账一致性（每行在窗口日历内 + 条数与日志 `[SKIP-DATADEFECT]` 行数一致 + verdict/skipped/nan_ratio>0.5 固定值校验）；⑦ 链条连续性（54 对相邻产出日，`daily[Ti].prev_rank_20` vs `full[T(i-1)].rank_20` 共同股票逐对无断裂，NaN↔-1 语义对齐）；⑧ 无未来状态（55 个产出日 `first_top20_date`/`last_update_date` 无晚于产出日的值） | 任务 D：防「报 60 实 55」复发。 |
| `research/backfill_60_driver.py` | **已删除** | 任务 B：逻辑已并入 `rps_daily.py`；删除前已确认 `rps_daily.py --backfill 60` 独立完成同样工作（本轮重跑即验证，A6）。 |
| `results/defects_manifest.csv` | 新建，5 行 | 任务 A：缺陷台账留痕。 |
| `results/backfill_round3.log` | 新建（tee 落盘，126 行） | 任务 C：命令行 / 起止时间 / 每步耗时 / 总耗时 76.8s / 退出码 0 / 缺陷日清单 / 55 行 `[RUN]` / 峰值内存 0.29 GB 全留痕。 |

数据安全声明：**未修改 `D:/astock/` 下任何文件**（全程只读 `pyarrow.read_table`）。

## 2. 重跑命令与退出码

```
命令行: D:\Python311\python.exe research\rps_daily.py --backfill 60 --workers 2
开始: 2026-10-04 12:30:40  结束: 2026-10-04 12:31:57  退出码: 0
日志: results/backfill_round3.log（tee 重定向落盘，非仅 stdout）
```

重跑前置（任务 C）：
- state 备份并清空：`rps_state.parquet` → `rps_state.parquet.bak_preround3_20261004_122954`（改名即备份+清空，重跑从空 state 起步，日志可见「状态快照载入：0 只股票」）；
- round2 旧 CSV 归档（防混入）：110 个文件（55 full + 55 daily）移入 `results/_archive_round2_20261004_122954/`，未删除。

## 3. 缺陷日台账（results/defects_manifest.csv，5 行）

| trade_date | listed_days_nan_ratio | total_rows | verdict | skipped |
|---|---|---|---|---|
| 20260803 | 0.999277 | 5529 | MAIN_DB_DEFECT | True |
| 20260804 | 0.999277 | 5529 | MAIN_DB_DEFECT | True |
| 20260805 | 0.999277 | 5532 | MAIN_DB_DEFECT | True |
| 20260914 | 1.0 | 5562 | MAIN_DB_DEFECT | True |
| 20260918 | 1.0 | 5553 | MAIN_DB_DEFECT | True |

与 OP 独立复核结论一致（恰好 5 日，验收 A1 ✓）。

## 4. 55 天产出统计

- 窗口：2026-07-03 ~ 2026-09-24，**60 个交易日 = 产出 55 + 缺陷日 5**（与日历精确对账，验收 A4 ✓）；
- 逐日池大小 3338 ~ 3967 只，预期池中位数 3948；每日 top1 rps20=100.0；
- 55 个正常日 `quality_gate` **全部通过**（验收 A3 ✓）——日志中仅有 `[GATE-WARN]`（涨停池接口 07-03~09-08 返回 0 条，网络降级不阻断，与 round2 行为一致），无任何 `[GATE-FAIL]`；
- 总耗时 76.8s（其中涨停池/题材归因预取 69.4s、逐日产出循环 6.6s），峰值内存 0.29 GB。

## 5. verify_rps.py 完整输出（8/8 全绿，退出码 0）

```
[VERIFY] Project_22 RPS 产物独立交叉校验（独立实现，不复用 rps_daily 内部函数）
[VERIFY] 最新产物: rps_rank_full_20260924.csv（3575 行），最早产物: rps_rank_full_20260703.csv
    300283.SZ: 手算 0.07675112 vs 产物 0.07675112 基准日 2026-08-27 误差 0.00e+00 OK
    688105.SH: 手算 0.20171868 vs 产物 0.20171868 基准日 2026-08-27 误差 0.00e+00 OK
    688303.SH: 手算 -0.09387521 vs 产物 -0.09387521 基准日 2026-08-27 误差 6.94e-17 OK
[VERIFY-PASS] 1_ret_20_recompute 3 只随机股票 @ 2026-09-24
[VERIFY-PASS] 2_rps20_distribution max=100.00 min=0.03 median=50.01 n=3574
[VERIFY-PASS] 3_state_rank_chg 抽 5 只在榜股票
    后上市例子: 920201.BJ 首现 2026-09-24 -> 不在早期池，T0 前无行情记录
    早期池缺席的 663 只均为近期仍有行情的股票（停牌口径，当日不可交易不出池属正常），退市方向以停牌口径验证通过
[VERIFY-PASS] 4_pit_pool 早期 2026-07-03 池 3943 只 / 最新 2026-09-24 池 3575 只
[VERIFY-PASS] 5_window_completeness 窗口 2026-07-03 ~ 2026-09-24 交易日 60 天 = 产出 55 + 缺陷日 5，日志声明窗口 60 天
[VERIFY-PASS] 6_manifest_consistency 台账 5 行 vs 日志 [SKIP-DATADEFECT] 5 行；verdict=OK skipped=OK nan_ratio>0.5=OK 日历命中 5/5
[VERIFY-PASS] 7_chain_continuity 54 对相邻产出日，共同股票观测 2654 次，断裂 0 处
[VERIFY-PASS] 8_no_future_state 55 个产出日的状态字段全部不晚于产出日
[VERIFY-PASS] 汇总: 8/8 项通过
```

## 6. 可复现性与 round2 对照（诚实披露）

- **55 天日集与 round2 完全一致**（文件名集合 diff=0，验收 A6「同样 55 天」✓）；
- **55 个 `rps_rank_daily_*.csv`（Top50 监控产物）与 round2 归档逐字节一致**（抽查 20260924/20260703/20260831 哈希全同）；
- `rps_rank_full_*.csv` 与 round2 存在 32/3575 行差异（以 09-24 为例）：**全部位于 `rps_20` 精确并列组内**（如 002303.SZ/300426.SZ 的 ret_20 逐位相同、rps_20 完全相等，仅并列组内 rank_20 名次互换）。SPEC 口径 rps_20 并列取平均，并列组内名次次序无业务语义；
- **round3 确定性已独立验证**：另起独立进程按 `rps_daily` 代码路径重算 2026-09-24，3575 行 rank_20 与 round3 产物 **0 差异**（复跑 2 次均 0 差异）；round2（11:44 driver 运行，无日志留存）的并列组内次序差异无法从现存产物反推其进程环境，疑与其运行时排序路径/解释器环境有关，不影响任何口径结论；
- 链条断言⑦在本轮产物内部自洽（prev_rank_20 ↔ 前一产出日 rank_20 零断裂）。

## 7. 验收对照

| 编号 | 验收项 | 结果 |
|---|---|---|
| A1 | 恰好识别 5 个缺陷日 | ✅ 08-03/04/05（0.999）、09-14/09-18（1.000） |
| A2 | 台账 5 行字段完整 | ✅ 见 §3 |
| A3 | 正常日门控未削弱 | ✅ 55/55 通过 quality_gate，判据一字未改 |
| A4 | 55 + 5 = 60 与日历一致 | ✅ 断言⑤ PASS |
| A5 | driver 已删 | ✅ `research/backfill_60_driver.py` 不存在 |
| A6 | 清 state 单跑可复现 | ✅ 本轮即从空 state 单跑 `rps_daily.py --backfill 60` 得 55 天 |
| A7 | verify 全绿 | ✅ `[VERIFY-PASS] 汇总: 8/8 项通过`（含新增 4 项） |
| A8 | 无未来污染 | ✅ 断言⑧ PASS |
| A9 | 日志留痕 | ✅ `backfill_round3.log` 含命令行/起止时间/退出码 0/缺陷日清单/55 行 [RUN]/峰值内存 |

## 8. 遗留事项

- `D:/astock` 主库 `listed_days` 五日整列缺陷为**上游数据问题，本轮只报告不修**（业主拍板，主库为多项目共用资产）。若后续离线数据包升级修复这 5 日，回补将自动恢复产出（识别逻辑按 NaN 比例动态判定）；
- 单日模式（`--date`）遇缺陷日同样跳过并覆盖台账（1 行）；正常日单跑不覆盖台账，防止日常监控运行清掉回补台账记录。
