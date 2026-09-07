# P16 F2 主数据源升级说明（Tushare moneyflow）

> 决策日期：2026-09-03 ｜ 状态：工程改动已落地，待用户填入 Tushare token 激活

## 一、决策背景

- P16（LightGBM 股票大师）的 **F2 主力资金流**是评分卡 0.20 权重的核心因子，且 g2 模型的 43 特征中含 `mf_main_net`/`mf_elg_net`/`mf_main_ratio`（均源自 moneyflow 五档）。
- 调研确认（2026-09-01~09-03）：F2 的**权威源必须是 Tushare `moneyflow` 四档口径**（`buy_lg`/`buy_elg`/`sell_lg`/`sell_elg`/`net_mf_amount`），与训练期绑定，**零 train-serving skew**。新浪/悟道/QVeris 的 F2 均为近似口径，只能是校验/兜底位。
- Tushare **200元/2000积分档**（包年、调用**不消耗积分**）覆盖 `moneyflow` 及全部行情/财务/基础信息接口，已逐一比对确认满足 P16 需求。
- 原链路痛点：
  - 底层 Tushare 真值 `D:/astock/moneyflow/moneyflow.parquet` **滞后 11 天**（最新 2026-08-21），未每日刷新；
  - 实盘层 `deploy_predict_g2.py` 的 F2 被**新浪当日近似强制覆盖**，且 9:25 盘前连挂不稳。

## 二、升级内容（已落地）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `config/data_source_keys.json` | 新增 `tushare` 条目（`token: 待填`，`status: PENDING`） |
| 2 | `scripts/refresh_tushare_moneyflow.py`（新建） | Tushare moneyflow 每日增量刷新脚本（见下） |
| 3 | `deploy_predict_g2.py` | F2 **自适应主源**：Tushare 快照新鲜→主源，新浪仅缺失兜底；滞后→自动回退新浪；报告标注主源状态 |
| 4 | `build_g2_daily.py` | F2 主读已是 Tushare parquet，加新鲜度标记 + 说明段更新 |
| 5 | `g2_realtime.py` | docstring 明确 F2 主源=Tushare，新浪降为兜底 |
| 6 | 自动化任务 | 每日 **19:30** 运行刷新脚本（id `268d2dd9-5b08-4537-b2d0-8f3f39e36a8a`），token 填后自动生效 |

### 刷新脚本关键对齐规则（防 skew / 防量纲 bug）
- **单位**：Tushare `moneyflow` 原生 `amount` 字段**即「万元」**（实测核对：600519.SH 2026-09-02 RAW `buy_lg_amount=81094.1`、`net_mf_amount=-1879.33` 均为万元），仓库 parquet 同样以**万元**存储（与 g2 训练口径一致）→ **amount 列直接映射，无需 ÷1e4**。⚠️ 初版误写「元→÷1e4」致新刷数据偏小 1e4 倍，已修正（详见第七节）。
- **字段**：与 Tushare 原生完全一致（`buy_lg_amount`/`buy_elg_amount`/`net_mf_amount` 等 18 列），直接映射。
- **index**：固定 `['ts_code','trade_date']` 多级索引（与现有 parquet 一致）。
- **增量**：读现有 parquet 最新日期→从次日刷到 T-1；按 `trade_date` 拉全市场（分页 `limit=5000`）；合并去重（`keep=last`）；写盘前备份 `.bak`。
- **健壮性**：缺 `tushare` 包自动 `pip install`；`trade_cal` 失败时 fallback 到工作日近似。

## 三、激活步骤（用户购买 Tushare 后）

1. 将 token 填入 `config/data_source_keys.json` → `sources.tushare.token`（替换"待填"）。
2. **手动补刷一次**消除 11 天滞后：
   ```bash
   python D:/QuantLab/scripts/refresh_tushare_moneyflow.py --days 15
   ```
3. **验证**：
   - parquet 最新日期应达 **T-1**（滞后 ≤1 天）；
   - `python deploy_predict_g2.py --date <今日>` 日志应打印 `F2 主源=Tushare每日快照(主源)`；
   - amount 列数值 = 万元单位下的值，应精确等于 Tushare RAW（如 600519.SH 9-02 `buy_lg_amount=81094.10` 万、000008.SZ 9-02 `buy_lg_amount=1655.26` 万）；若出现个位数（如 8.1）即误 ÷1e4，须回滚 `.bak` 重刷。
4. 此后自动化 **19:30** 接管每日滚动，无需人工干预。

## 四、验证要点（防回归）

- [ ] 单位：刷新后 amount 列 = 万元量级（非元）
- [ ] 字段：18 列完整，`buy_lg_amount` 等存在
- [ ] 去重：重复运行不产生重复行（`keep=last`）
- [ ] 零 skew：Tushare 四档口径 == 训练期使用口径（已确认字段名一致）
- [ ] 自适应：token 未填时自动化跳过；token 填后自动刷新

## 五、回退方案

- Tushare 临时失效 → 代码**自适应回退新浪**（滞后判定）；或把 `tushare.token` 改回"待填"，自动化自动跳过刷新。
- parquet 有 `.bak` 备份，异常可还原。

## 六、成本模型

- 固定 **200元/年**（包年档位制，调用**不消耗积分**）；每日 19:30 仅数次 `moneyflow` 调用，远在 200次/分、10万次/天限制内 → **零边际成本**。
- 对比：QVeris 按次消耗（约 $19/月 ≈ 1300元/年），仅适合偶发校验/兜底，不适合做每日主源。

## 七、单位坑修复记录（2026-09-03 实测）

- **现象**：首次刷新后 000008.SZ 2026-09-02 `buy_lg_amount` 仅 0.165526（应为 1655.26），与历史 8-21 值（4473.72）差约 1e4 倍。
- **根因**：误判 Tushare `moneyflow` amount 单位为「元」需 ÷1e4；实测原生单位即「万元」，历史 parquet 同是万元 → 多除一次。
- **修复**：① 归档错误版 `moneyflow.parquet.bad_div_20260903`；② 回滚 `.bak`（8-21 正确版）；③ 改脚本去掉 ÷1e4、docstring 更正；④ 重刷并验证新数据精确等于 RAW 且与历史量级一致。
- **防复发**：脚本已删 ÷1e4；后续任何改动须先 `pro.moneyflow(ts_code='600519.SH', trade_date='最新交易日')` 核对原始 amount 单位再动换算逻辑。
