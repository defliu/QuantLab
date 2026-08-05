# SPEC：黄氏主升浪「箱体突破初选 + 双中军精筛」组合公式 1:1 复刻为 QMT 选股代码

版本：v1.2  
日期：2026-06-23  
发起人：诚哥  
整理：Hermes  
执行：CC  
原始公式文件：`F:\天翼云盘同步盘\Obsidian\量化知识库\20_策略知识库\黄氏主升浪策略.txt`  
会议结论文件：`D:\QMT_STRATEGIES\agent_hub\2026-06-23_huang_main_uptrend\90_hermes_summary.md`

---

## Objective

### 目标

把黄氏主升浪通达信公式中的两个模块，按诚哥与 CC/MIMO 反馈修正为**一个时间窗口组合选股逻辑**：

```text
最终唯一选股逻辑 = 先出现“箱体突破版初选”信号，随后 N 个交易日内出现“双中军版精筛”信号
```

注意：本 SPEC **不是让 CC 生成两个互相独立的最终选股代码**，也**不是同日 AND**。

正确理解是：

```text
箱体突破版 = 阶段1，负责识别“前期箱体/均线黏连后的突破启动点”
双中军版 = 阶段2，负责在突破后的观察窗口内确认“趋势发散 + 动能确认 + 主升浪成立”
最终输出 = 某股票在最近 N 个交易日内曾触发 box_breakout_XG，且今天触发 double_zhongjun_XG
```

### 关键修正说明

CC/MIMO 诊断确认：同日 `box_breakout_XG AND double_zhongjun_XG` 在逻辑上近似互斥。

原因：

```text
箱体突破版要求：前 60 日窄幅震荡、均线黏连
双中军版要求：均线多头排列、MA5 角度 >30、MA5/MA20 >1.05
```

前者代表“刚从压缩区启动”，后者代表“启动后趋势已发散并确认”。二者不是同一天同时成立的关系，而是**先后阶段关系**。

### 最终输出

CC 最终只需要交付一个主输出：

```text
huang_main_uptrend_combo_XG
```

其语义必须等价于：

```text
huang_main_uptrend_combo_XG = 最近 N 个交易日内曾触发 box_breakout_XG AND 今日触发 double_zhongjun_XG
```

其中：

```text
box_breakout_XG       = 箱体突破版初选条件
double_zhongjun_XG    = 双中军版主升浪精筛条件
box_window_hit        = 最近 N 个交易日内是否出现过 box_breakout_XG
combo_XG              = box_window_hit AND 今日 double_zhongjun_XG
```

### 中间条件

可以保留两个中间布尔字段用于调试和报告：

```text
box_breakout_XG
双中军版 double_zhongjun_XG
```

但这两个字段不是最终策略输出，只用于解释最终组合选股为什么通过/不通过。

---

## Commands

### CC 执行前必读

```text
D:\QMT_STRATEGIES\specs\SPEC_HUANG_MAIN_UPTREND_TDX_TO_QMT.md
D:\QMT_STRATEGIES\agent_hub\2026-06-23_huang_main_uptrend\90_hermes_summary.md
F:\天翼云盘同步盘\Obsidian\量化知识库\20_策略知识库\黄氏主升浪策略.txt
```

### CC 执行要求

```text
请遵循 ODM 工作流执行本 SPEC。
只做“箱体突破初选 + 双中军精筛”的组合选股逻辑。
不要生成两个独立最终策略。
不要改原始 Obsidian 文件。
不要启动实盘/模拟盘交易。
不要调用 passorder / xttrader / xtbp / 任何下单接口。
```

### 交付前验证

如生成 QMT 可加载文件，必须执行：

```bash
python scripts/validate_qmt_file.py <交付文件>
```

要求：

```text
6 项 ALL PASS
GBK 编码通过
Python 3.6.8 兼容
无 mock/passorder 污染
```

如只生成离线回测/选股模块，也必须跑对应单元测试或最小样本验证，并输出验证报告。

---

## Structure

### 建议目录

CC 可根据现有项目结构落位，但建议作为一个独立组合策略，不要拆成两个最终策略：

```text
D:\QMT_STRATEGIES\huang_main_uptrend_combo\
  README.md
  huang_main_uptrend_combo_selector.py
  config.yaml
  tests\
    test_huang_main_uptrend_combo_selector.py
  reports\
    tdx_mapping_report.md
```

### 逻辑入口

最终只需要一个主入口：

```text
select_huang_main_uptrend_combo(data, index_data, params)
```

主入口内部必须执行三步：

```text
1. 计算箱体突破初选条件 box_breakout_XG
2. 计算双中军精筛条件 double_zhongjun_XG
3. 在每只股票的时间序列上计算 box_window_hit = 最近 N 个交易日内曾触发 box_breakout_XG
4. 输出 combo_XG = box_window_hit AND 今日 double_zhongjun_XG
```

允许为了调试拆出内部辅助函数：

```text
_calc_box_breakout_conditions(...)
_calc_double_zhongjun_conditions(...)
```

但对外最终输出必须以组合结果为准。

---

## Code Style

### 总体要求

1. **1:1 复刻通达信语义**，不要擅自优化参数。
2. 最终选股输出只能代表时间窗口组合条件：

```text
最近 N 个交易日内曾触发 箱体突破版_XG
AND 今日触发 双中军版_主升浪启动
```

3. 箱体突破版和双中军版可以作为中间字段输出，但不得作为两个独立最终策略交付。
4. 每个通达信条件都要有对应的中间变量，便于逐项核对。
5. 参数必须可配置，但默认值必须与原公式一致。
6. 输出结果中应能看到每只股票每个条件是否通过。

### QMT / Python 兼容红线

如果产物要放进 QMT 策略环境，必须遵守：

```text
Python 3.6.8 兼容
GBK 编码
禁止 dict[str, ...]
禁止 list[str]
禁止 str | None
禁止 match-case
禁止 dataclass
禁止 walrus operator :=
```

### 禁止事项

```text
禁止调用 passorder
禁止调用 xttrader 下单接口
禁止引入 context_mock.py
禁止把 mock passorder 打进生产构建
禁止修改现有实盘/模拟盘策略
禁止改原始通达信公式文件
禁止把 P0 双中军版单独作为最终输出
禁止把箱体突破版单独作为最终输出
```

---

## Testing

### 验收目标

本任务验收重点是：

```text
组合逻辑是否严格等价于：最近 N 个交易日内曾触发箱体突破版_XG，且今日触发双中军版_主升浪启动
```

不是比较两个独立策略的收益。

### 必须验证项

1. 每个通达信函数都有明确 Python/QMT 映射：

| 通达信函数 | QMT/Python 映射要求 |
|---|---|
| `MA(X,N)` | N 日简单移动平均 |
| `EMA(X,N)` | 通达信 EMA 口径，需确认 alpha 规则 |
| `REF(X,N)` | 向前 N 日引用，严禁未来数据 |
| `HHV(X,N)` | 最近 N 日最高值 |
| `LLV(X,N)` | 最近 N 日最低值 |
| `CROSS(A,B)` | 当日 A>B 且昨日 A<=B |
| `COUNT(COND,N)` | 最近 N 日条件成立次数 |
| `ATAN` | 反正切，角度换算为 `*180/3.1416` |
| `AVEDEV(X,N)` | 平均绝对偏差，用于 CCI |
| `INDEXC` | 大盘指数收盘价，需明确使用哪个指数 |

2. 箱体突破初选字段必须单独输出：

```text
箱体振幅<20
均线黏连
放量
突破
涨幅>0.05
box_breakout_XG
```

3. 双中军精筛字段必须单独输出：

```text
多头排列条件
均线发散条件
MACD条件
CCI条件
突破压力条件
MA20向上
MA60向上
大盘条件
double_zhongjun_XG
```

4. 时间窗口与最终组合字段必须单独输出：

```text
box_last_signal_date
box_days_since_last_signal
box_window_hit = 最近 N 个交易日内曾触发 box_breakout_XG
combo_XG = box_window_hit AND 今日 double_zhongjun_XG
```

5. 验证报告必须说明：

```text
有多少股票通过箱体突破初选
有多少股票通过双中军精筛
有多少股票在 N 日窗口内完成 box→zhongjun 确认
每个最终候选最近一次 box 日期、间隔天数、今日 zhongjun 条件明细
最终候选为什么通过/不通过各条件
```

### 最小样本验证

至少对若干个股票/日期输出条件明细表：

```text
code
date
box_breakout_XG
box_last_signal_date
box_days_since_last_signal
box_window_hit
box_箱体振幅_ok
box_均线黏连_ok
box_放量_ok
box_突破_ok
box_涨幅_ok
double_多头排列_ok
double_均线发散_ok
double_MACD_ok
double_CCI_ok
double_突破压力_ok
double_MA20向上_ok
double_MA60向上_ok
double_大盘_ok
combo_XG
```

---

## Boundaries

### Always

- 最终只交付一个时间窗口组合选股逻辑。
- 组合语义必须是：先 box 后 zhongjun，不允许再写同日 AND。
- 保留箱体突破版和双中军版原始公式作为注释或映射依据。
- 默认参数一律与原公式一致。
- 最终输出必须是：

```text
box_window_hit = 最近 N 个交易日内曾触发 box_breakout_XG
combo_XG = box_window_hit AND 今日 double_zhongjun_XG
```

- 所有输出必须是“候选股票”，不是交易指令。
- 任何结论必须区分：

```text
公式语义已复刻
逻辑合理性待验证
收益效果待回测
```

### Never

- 不做实盘交易。
- 不调用任何下单函数。
- 不擅自修改参数。
- 不加入 529版、424版、低吸版、430版、505版到本次交付范围。
- 不把箱体突破版或双中军版单独作为最终策略输出。
- 不把本任务扩展成完整交易系统。

---

# 原始通达信公式

以下为本 SPEC 要求 1:1 复刻并组合的两段原始通达信公式。

---

## A. 初选层：箱体突破版

```text
#箱体突破版----------------------

{箱体突破初选 - 通达信电脑版}
{功能：选出放量突破箱体上沿的强势股，供HERMES二次精筛}

N:=60;          {箱体观察周期}
MA_SHORT:=5;    {短均线}

{===== 箱体识别 =====}
箱顶:=HHV(H,N);     {近N日最高价}
箱底:=LLV(L,N);     {近N日最低价}
箱体振幅:=(箱顶-箱底)/箱底*100;

{===== 均线黏连 =====}
MA5:=MA(C,5);
MA10:=MA(C,10);
MA20:=MA(C,20);
均线差1:=ABS(MA5-MA10)/MA5*100;
均线差2:=ABS(MA10-MA20)/MA10*100;
均线黏连:=均线差1<5 AND 均线差2<5;

{===== 放量突破 =====}
前5日量:=MA(V,5);
放量:=V>前5日量*1.5;
突破:=C>=箱顶*0.995;
涨幅:=C/REF(C,1)-1;

{===== 综合条件（唯一输出） =====}
XG: 箱体振幅<20 AND 均线黏连 AND 放量 AND 突破 AND 涨幅>0.05;

{===== 辅助字段（赋值不输出，选股结果里可查看） =====}
箱体高度:=箱体振幅;
量比:=V/前5日量;
```

### 初选层复刻说明

CC 必须注意：

1. 箱体突破版的 `箱顶:=HHV(H,N)` 包含当日 high。原公式如此，1:1 复刻时默认保留。
2. `突破:=C>=箱顶*0.995` 因箱顶包含当日 high，所以这是“接近当日/近60日箱顶”的逻辑，不等同于突破昨日箱顶。
3. `MA_SHORT:=5` 在原公式中定义但没有参与最终条件。不要擅自加入最终条件。
4. `箱体高度` 和 `量比` 是辅助字段，不参与初选 XG。

---

## B. 精筛层：双中军版

```text
#双中军版----------------------

{=== 主升浪启动选股公式（通达信，已删除温和放量+筹码条件）===}
{----- 1. 均线多头排列 -----}
MA5 := MA(CLOSE,5);
MA10 := MA(CLOSE,10);
MA20 := MA(CLOSE,20);
MA60 := MA(CLOSE,60);
MA120 := MA(CLOSE,120);
多头排列条件 := MA5 > MA10 AND MA10 > MA20 AND MA20 > MA60 AND MA60 > MA120;

{----- 2. 均线刚发散 -----}
发散确认 := CLOSE > MA20;
MA5角度 := ATAN((MA5/REF(MA5,1)-1)*100)*180/3.1416;
均线发散条件 := MA5角度 > 30 AND MA5/MA20 > 1.05;

{----- 3. MACD零轴上金叉或双线向上 -----}
DIF := EMA(CLOSE,12) - EMA(CLOSE,26);
DEA := EMA(DIF,9);
MACD红柱 := (DIF - DEA) * 2;
MACD条件 := (CROSS(DIF,DEA) AND DEA > 0) OR (DIF > DEA AND DIF > REF(DIF,1) AND DEA > REF(DEA,1));

{----- 4. CCI突破100 -----}
TYP := (HIGH + LOW + CLOSE)/3;
CCI14 := (TYP - MA(TYP,14))/(0.015 * AVEDEV(TYP,14));
CCI条件 := CROSS(CCI14,100) OR (CCI14 > 100 AND CCI14 > REF(CCI14,1));

{----- 5. 突破压力位 -----}
N := 20;
近期高点 := REF(HHV(HIGH, N), 1);
突破压力条件 := CLOSE > 近期高点 AND CLOSE/近期高点 < 1.08;

{----- 8. 中期趋势确认 -----}
MA20向上 := MA20 > REF(MA20,5);
MA60向上 := MA60 > REF(MA60,5);

{----- 9. 大盘环境过滤 -----}
大盘指数 := INDEXC;
大盘MA20 := MA(大盘指数,20);
大盘MA60 := MA(大盘指数,60);
大盘条件 := 大盘指数 > 大盘MA20 AND 大盘MA20 > 大盘MA60;

{----- 最终选股条件 -----}
主升浪启动: 多头排列条件 AND 均线发散条件 AND MACD条件 AND CCI条件
            AND 突破压力条件
            AND MA20向上 AND MA60向上 AND 大盘条件;
```

### 精筛层复刻说明

CC 必须注意：

1. `发散确认 := CLOSE > MA20;` 在原公式中被定义但没有进入最终 `主升浪启动` 条件。1:1 复刻时不要擅自加入最终条件，除非单独作为 debug 字段输出。
2. `MACD红柱` 在原公式中被定义但没有进入最终条件。不要擅自加入最终条件。
3. `INDEXC` 需要明确映射。建议作为配置项指定默认大盘指数，必须在报告里说明。不得静默乱用。
4. `突破压力条件` 使用 `REF(HHV(HIGH, N), 1)`，即不包含当日 high，避免当日自引用。

---

## C. 最终组合规则

### C1. 错误规则（废弃）

```text
combo_XG = box_breakout_XG AND double_zhongjun_XG
```

该规则已被 CC/MIMO 样本诊断证伪：两个信号同日近似互斥，交集为 0，不能作为最终策略。

### C2. 正确规则（时间窗口确认）

```text
box_window_hit = 最近 N 个交易日内曾触发 box_breakout_XG
combo_XG = box_window_hit AND 今日 double_zhongjun_XG
```

解释：

```text
箱体突破版先识别压缩后的突破启动日；
随后给股票一个观察窗口；
窗口内如果双中军版确认趋势发散和动能成立，则输出最终主升浪候选。
```

### C3. 窗口参数

默认建议：

```text
N = 120 个交易日
```

理由：MIMO 诊断显示同一股票两个信号错位约 73~370 天，若窗口太短会继续无票；120 日约半年，先作为默认验证参数。

后续回测可做敏感性对比，但不属于 1:1 复刻主任务：

```text
N = 60 / 120 / 180 / 250
```

注意：这是唯一最终输出，不是两个独立策略，也不是同日 AND。

---

## D. 参数默认值

| 参数 | 默认值 | 来源 |
|---|---:|---|
| 箱体周期 N | 60 | 箱体突破版原公式 |
| 箱体振幅阈值 | <20% | 箱体突破版原公式 |
| 均线黏连阈值 | <5% | 箱体突破版原公式 |
| 放量阈值 | V > MA(V,5) * 1.5 | 箱体突破版原公式 |
| 突破容差 | C >= 箱顶 * 0.995 | 箱体突破版原公式 |
| 当日涨幅阈值 | >0.05 | 箱体突破版原公式 |
| 双中军 MA5 | 5 | 双中军原公式 |
| 双中军 MA10 | 10 | 双中军原公式 |
| 双中军 MA20 | 20 | 双中军原公式 |
| 双中军 MA60 | 60 | 双中军原公式 |
| 双中军 MA120 | 120 | 双中军原公式 |
| MA5角度阈值 | >30 | 双中军原公式 |
| MA5/MA20 发散阈值 | >1.05 | 双中军原公式 |
| MACD DIF | EMA12 - EMA26 | 双中军原公式 |
| MACD DEA | EMA(DIF,9) | 双中军原公式 |
| CCI周期 | 14 | 双中军原公式 |
| CCI突破阈值 | 100 | 双中军原公式 |
| 压力位周期 N | 20 | 双中军原公式 |
| 突破上限 | CLOSE/近期高点 < 1.08 | 双中军原公式 |
| MA20向上周期 | 5 | 双中军原公式 |
| MA60向上周期 | 5 | 双中军原公式 |

---

## E. 后续回测建议，不属于本次编码强制范围

后续回测时只比较时间窗口组合策略表现，不再把箱体突破版和双中军版作为两个独立策略比赛，也不要再使用同日 AND 规则。

观察指标：

```text
1. 每日通过初选数量
2. 每日通过精筛数量
3. 每日最终组合出票数量
4. 最终组合候选的 box→zhongjun 间隔天数分布
4. 5/10/20 日收益
5. 最大回撤
6. 胜率
7. 平均持有收益
8. 是否追高
9. 是否长期无票
```

本 SPEC 当前任务重点是：

```text
先完成“箱体突破初选后，在 N 日窗口内由双中军精筛确认”的组合公式语义复刻。
```
