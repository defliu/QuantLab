# F5 死代码同类模式全库扫描报告（T-20260919-002 复盘 · 只读扫描）

- 扫描日期：2026-09-20
- 扫描方式：模式检索（`not in .columns` / `= np.nan` 建列 / `passorder` 返回值判定）+ 人工逐段核查
- 范围：P16 实盘链路（deploy_predict_g2 / deploy_predict / scorecard_real / build_g2_daily / rebalance_g2 / rebalance_ens / rebalance_daily / g2_realtime / position_filter / merge_live_features / paper_forward* / data_config / refresh_panel_v3）+ broker（qmt_builder / qmt_order）+ data/schedules/*.py + versions/ 存档副本
- 约束遵守：未改任何文件、未 commit、未联网跑行情

## 一、命中清单

| # | 文件 | 行号 | 变量/模式 | 说明 | 风险 |
|---|---|---|---|---|---|
| 1 | versions\G2-V1.0_20260908\deploy_predict_g2.py | 92-100 | main_net / industry_pct | L92-94 先无条件填 NaN 建列 → L97 `"main_net" not in pre.columns` 永假 → mf_main_net×1e4 写入唯一入口被阻断；L99 industry_pct 同理（F5 恒 5.0 原始 bug 完整存活） | **高**（存档不直接运行，但回滚/复制即复活 39 天未发现的实盘 bug） |
| 2 | versions\V20260909_门禁升级_纸面AB\deploy_predict_g2.py | 98-106 | 同上 | 同一模式的存档副本 | **高**（同上） |
| 3 | versions\V20260909_融合验证\deploy_predict_g2.py | 101-109 | 同上 | 同一模式的存档副本 | **高**（同上） |
| 4 | broker\qmt_builder.py（mfic 模板） | 331-335, 356-361 | order_id | `order_id = passorder(...)` 后 `if order_id:` 永假（passorder 异步返回 0/None，见 AGENTS 红线）→ 买入分支 L359 `positions[code]=...` **永不执行**，持仓账本漏记 → 下一 bar 重复买入；卖出分支恒打「卖出失败 passorder返回0」误导日志 | **高**（模式即 P0；所幸属 Project_01 mfic 模板，当前未部署实盘，实际暴露=中） |
| 5 | projects\...\deploy_predict_g2.py（现行已修版） | 141 | main_net | L118-120 已保证列存在 → `if "main_net" in pre.columns else np.nan` 的 else 永死。行为等价（值本就是 NaN），无实盘影响 | 低（残尾死分支，建议顺手清理） |
| 6 | projects\...\scorecard_real.py | 51/52/56/64/65 | main_net / volume_ratio / industry_pct / pe_ttm / turn_ma5 | 实盘链路调用方（deploy_predict_g2 L118-120）无条件预建列 → 函数内 `else np.full(...)` 回退分支在实盘路径永不可达，从未被真实执行/测试过；若未来换调用方不预建列，走的是未验证代码 | 低（行为等价：NaN→中性分；但回退路径无测试覆盖） |
| 7 | projects\...\position_filter.py | 119-121 | pre20/dist_hi20/vr/amt5/pct_chg | L90-92 无条件建列 → merge_asof 后必同时产生 _x/_y → L119 `elif c+"_y" in out.columns`（无 _x 情形）永不可达 | 低（行为无影响） |
| 8 | projects\...\rebalance_g2.py | 510/532/549 | plan["written"] | L510 赋值后 L532/549 在 if/else 两分支内重复赋相同值，冗余赋值（非严格死代码） | 低 |

### 核查过、确认无命中（回退分支可达）

- **deploy_predict.py**：`_prefer_snapshot` 快照缺失/读失败/缺列三条回退均可达；无建列-后判模式。
- **build_g2_daily.py**：L126 volume→vol rename 在前、L158/162 建列后无同类后判；F5 当日自算为空 → ths_daily 周更回退（L253-263）可达。
- **g2_realtime.py**：东财→MCP 文件→周更三级回退全部可达（fetch 失败返回 `{}`，调用方按真值判断）。
- **merge_live_features.py / paper_forward*.py / refresh_panel_v3.py / data_config.py**：填 NaN 建列后无同类后判；refresh_panel_v3 的 missing 检查在补算之前（顺序正确、诚实打印）。
- **broker/qmt_order.py**：`ret == 0` 正确判为 SUBMITTED（与 qmt_builder 模板形成对照，是正确写法）。
- **rebalance_ens.py / rebalance_daily.py**：无 DataFrame 列操作模式。
- **data/schedules/*.py**：均为探针/审计/对比脚本（只读诊断），probe_g2_rules_compare.py 与 probe_f2_main_source.py 复现的是修复后逻辑，无死代码。

## 二、预防机制意见（≤300 字）

**B 优先（成本最低、对症本次事故）**：39 天才发现的根因是「产出坍缩无监控」。每日选股后自检 SC_F1~F6 的唯一值数/标准差 + F2 主源生效比例 + main_net 非空率，异常即飞书告警。可挂靠已有 feature_health_weekly.py，不必新建任务。
**D 更简替代（推荐与 B 合并）**：deploy_predict_g2 落盘前加 3 行断言（main_net 非全 NaN、F5 std>0），违者 FAIL-LOUD 打印+告警——无独立脚本，零维护。
**A 静态扫描（建议做）**：AST 识别「先建列后判 not in columns」及「passorder 返回值当订单号」两类模式，纳入 build.py 与周训前置；本次命中 #1-#4 均可被覆盖。
**C 快照回放（缓行）**：最彻底但维护最重，留作发版/周训时跑，不必每日。
结论：D+B 立即落地，A 次之，C 缓行。
