# P20 NAV 复投修复 · 二审复核记录（2026-09-13）

> 验收人：DE（GLM-5.3）二审 + TRAE 独立复核
> 验收对象：BUILD_TAG=**20260913-183327**（10513B，含 F1-F8 修复）

## 一、DE 二审执行情况（诚实记录）

- DE 受委派做快速二审（纯代码审查，复审点 R1-R6）。**DE 审查完成**（日志实证："源码已核完四条放行条件的实现。最后读产物核对 BUILD_TAG 与源码一致性" + "本轮纯代码审查：未运行命令、未写文件；证据均出自源码/产物/部署报告行号"）；
- ⚠️ **DE 结论文本未落盘**（进程退出时报告内容丢失，未写入指定输出文件）——这是**连续第二次** DE 完成分析但未落盘（R4 一次、R5b 一次），委派教训升级见下；
- 依据 DE 一审（落盘报告）的放行条件清单 + 二审审查痕迹，TRAE 按同一复审点（R1-R6）**逐项独立复核**（脚本 `data/schedules/_tmp_r2_recheck.py`，读源码断言）。

## 二、独立复核结果（R1-R6 全 PASS）

| 复审点 | 结果 | 证据 |
|---|---|---|
| R1 `_settle` 行情守卫（F2） | ✅ | `if len(prices) < len(LEGS): ... return`（abort 留 placing 不写账本） |
| R2 position 对账 + fallback（F1+F3） | ✅ | 读 `get_trade_detail_data(acct,'stock','position')`，m_nVolume/m_nCurrentPosition 兜底；反查失败 fallback 目标份额 + WARN |
| R3 QMT 行情时间（F5） | ✅ | `_now_hhmm` 优先 `C.get_current_time()`（datetime/epoch 秒/毫秒多形态），异常才 fallback |
| R3b handlebar 无 datetime.now 直用 | ✅ | handlebar 函数体无 datetime，`hhmm = _now_hhmm(C)` |
| R4 现金残留 7.5%（F4） | ✅ | docstring 已改：实测 7540 元/7.5%（修正 DE 一审 14.6%），实盘预期 4.40%−0.3~0.4pp |
| R5 回归（handlebar/_settle 调用链） | ✅ | 目标计算/漂移/下单/placing/_settle 全链完整 |
| R6 部署报告同步（F6/F8） | ✅ | BUILD_TAG=183327 + 分钟 K 必需 + 现金残留预期 |
| 产物校验 | ✅ | BUILD_TAG=20260913-183327、# coding=gbk、Py3.6 AST、无 MOCK/占位符 |

## 三、结论

**通过 —— 可放行模拟盘验证单。**

修复覆盖 DE 一审放行条件全部 4 项（F2 守卫 / F1+F3 成交核验与份额结转 / F5 QMT 时间 / F6+F8 部署同步），无残留 P0/P1；剩余 P3（死代码清理）不影响功能，随迭代清理。

## 四、模拟盘验证单（放行后动作，开盘执行）

1. 粘贴产物 `build/strategy_all_weather_v1.py` 到 QMT 客户端策略编辑器，**挂分钟 K（1m）**；
2. 核对日志 `[AW][INIT] BUILD_TAG=20260913-183327 account=70180771`；
3. 首仓验证：四腿 ETF 下单→反查→账本（现金残留约 7.5% 属预期）；
4. 次日对账：账本 vs 账户 position 一致（F1 修复后 position 为唯一真相）；
5. 顺带实测 513100 溢价（V2 数据点）。

## 五、DE 委派教训（更新）

连续两次（R4 分析后征询挂起、R5b 审查后未落盘）出现"完成但报告丢失"。**deveco run 一次会话的输出落盘不可靠**：
- 必须 serve + attach 交互取结果；
- 或任务书写死"最后一条消息必须以 `已写入 <绝对路径>` 开头"并人工盯盘验证文件落盘后再放行；
- 或（本次采用的替代）DE 审查 + TRAE 按同一复审点独立复核双保险。
