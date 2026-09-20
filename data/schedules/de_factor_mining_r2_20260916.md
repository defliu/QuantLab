# DE 第二轮：排雷 ablation 设计 + 正式批次扩源候选 + 调度建议

> 生成: 2026-09-16 | 性质: T-20260916-005 后续（DE 第二轮，任务书 `data/schedules/de_factor_mining_r2_prompt_20260916.txt`）
> 输入: 白名单 12 份文档 + 上网实检索（Crossref API / Semantic Scholar DOI 精查；Bing 中文英文均垃圾结果、DuckDuckGo 不可达、Semantic Scholar 搜索接口限频 429，已如实记录检索方法学限制）
> 输出结构: §1 上网学习综述（6 主题）→ §2 排雷 ablation 实验设计 → §3 正式批次扩源候选（40 条 + 可粘贴 YAML）→ §4 调度窗口建议

---

## 一、上网学习综述（6 个检索主题）

**检索方法学声明**：本轮 Bing（bing.com/search，中英文）返回结果全为词典/门户站等无关内容，DuckDuckGo html 端点不可达，Semantic Scholar 搜索接口 429 限频；实际有效渠道为 **Crossref API**（api.crossref.org/works?query.bibliographic=...）做文献发现 + **Semantic Scholar DOI 精查**（graph/v1/paper/DOI:...）抓摘要。以下所有 DOI/URL 均为实检索确认，摘要为实抓原文；未检索到的直接说"未检索到"，不编造。

### 主题 1：龙虎榜/席位资金异动与后续收益 —— 机制支持度：强 ✅

| 文献 | 结论 | 来源 |
|---|---|---|
| Seasholes & Wu (2007) *Predictable Behavior, Profits, and Attention*, Journal of Empirical Finance | 用**上海股市涨跌停事件**证明：个人投资者对"上头条"的股票注意力驱动净买入 → 价格可预测地冲高 → **随后反转**；聪明资金靠注意力事件获利 | https://doi.org/10.1016/j.jempfin.2007.03.002 |
| Hong, Yao & Zhuang (2025) *Institutional Trading and Short-Term Stock Returns – Evidence from Dragon and Tiger List Data in China*, Emerging Markets Finance and Trade | 实抓摘要：龙虎榜机构席位交易与**短期**股票表现正相关（低研报覆盖股票中尤其强）、与**散户情绪正相关**、但**与长期收益及公司基本面无显著关系** | https://doi.org/10.1080/1540496X.2025.2520377 |
| Barber & Odean (2008) *All That Glitters*, Review of Financial Studies | 散户是注意力吸引股的净买入者，注意力买入产生**暂时性价格压力**（理论底座） | https://doi.org/10.1093/rfs/hhm079 |

**与试点发现的印证**：LHB_TIMES_20D 训练段 IC -0.095（ICIR -1.40，正值占比 11%）与三篇文献构成完整机制链——上榜=注意力事件 → 短期（日级）冲高（Hong et al. 的"短期正相关"）→ 月频持有期内回落（我们的月频 IC 负）。**两者时间尺度不同、不矛盾，恰好拼出"冲高→回落"全路径**。
**冲突**：无实质冲突。唯一注意点：Hong et al. 说机构席位交易短期正相关，若未来用 top_inst 区分席位，"机构席位净买入"与"游资炒作上榜"可能要分方向处理。
**因子构造启示**：①上榜频率作排除条件有充分文献机制（注意力溢价反转）；②席位类型（机构 vs 游资）是值得扩源的维度（需 top_inst 补数）；③"距上榜天数"越近风险越高（Seasholes-Wu 的反转集中在事件后短期），可作排除条件的细化。

### 主题 2：北向资金持股与 A 股收益 —— 机制支持度：弱（诚实声明）⚠️

| 文献 | 结论 | 来源 |
|---|---|---|
| He, Li & Tan, *The Time-Varying Interaction of Northbound Capital Flows and Stock Market Performance in China* (SSRN working paper) | 北向资金流与市场表现交互**随时间变化**，不存在稳定的单向关系 | https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4914256 |
| Twongirwe, Bakundana & Masimengo (2026), Finance Research Open | 外资的收益预测能力取决于**来源地与投资期限**，非普遍成立 | https://doi.org/10.1016/j.finr.2026.100154 |
| Korkeamäki, Virk & Wang, *Learning Chinese? The Changing Investment Behavior of Foreign Institutions in the Chinese Stock Market* (SSRN) | 外资机构在华投资行为随时间学习演化 | https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3288388 |

**诚实结论**：**未检索到直接支持"北向持股比例高 → 横截面负超额"的高质量公开论文**。命中的多为"流量-指数"时间序列研究或外资行为研究，横截面"外资重仓股跑输"的证据在公开学术渠道缺失（券商研报有"外资拥挤度"提法，但本轮检索工具无法有效触达券商研报库）。
**与试点发现的关系**：HK_RATIO_LEVEL/20D_AVG 训练段 IC -0.078/-0.083（ICIR -0.62/-0.67，2021-23 逐年全负）是**自有实证**，学术背书只能给到"外资定价能力时变/不稳定"这类间接支持。机制假说（自有推断，非文献结论）：①小盘股被外资买到高比例=已充分定价/拥挤；②"外资高比例持股的小盘股"本身是异质样本（陆股通可买小盘数量有限），存在选择效应。
**因子构造启示**：北向条件可以进 ablation，但定位是"**自有实证驱动的验证**"而非"文献驱动"，预期置信度低于龙虎榜/研报两个条件；写报告时必须如此标注。

### 主题 3：研报覆盖/评级与收益 —— 机制支持度：中强 ✅

| 文献 | 结论 | 来源 |
|---|---|---|
| Arbel & Strebel (1982) *The Neglected Firm and Small Firm Effects*, Financial Review | **被忽视公司效应**：低覆盖/被忽视的公司组合跑赢受关注公司——高覆盖=信息已定价、溢价被吃掉 | https://doi.org/10.1111/j.1540-6288.1982.tb00065.x |
| Womack (1996) *Do Brokerage Analysts' Recommendations Have Investment Value?*, Journal of Finance | 评级**变动**有价格冲击+漂移（信息通道）；但样本为美股大市值，不可直接迁移 A 股小盘 | https://doi.org/10.1111/j.1540-6261.1996.tb05205.x |
| Ouyang, Liu & Yin (2025), Emerging Markets Finance and Trade | 中国背景分析师覆盖网络研究（共享分析师），侧面说明中国分析师数据研究活跃 | https://doi.org/10.1080/1540496X.2025.2605157 |

**与试点发现的印证**：RES_COUNT_120D 训练段 IC -0.057（ICIR -0.52，逐年全负）与 Arbel-Strebel 的"忽视溢价"**方向完全一致**：小盘域里被研报覆盖越多越跑输 = 覆盖（关注度）是负向；无人问津反而有溢价。经典理论背景为 Merton (1987) 不完全信息模型（经典文献，本轮未单独核对 DOI，仅作背景引用）。
**关键辨析（对扩源很重要）**：Womack 的"评级变动有信息含量"与 Arbel-Strebel 的"覆盖水平是关注折价"是**两条不同通道**——试点批只测了覆盖计数和评级水平/变动，且评级变动族（RES_RATING_CHG）当时全期 IC 太小被 S1 淘汰。扩源时"评级相对自身历史分位"与"覆盖计数"必须分开设计、分开判定。
**因子构造启示**：①覆盖数作排除条件有"忽视溢价"文献背书；②评级**变动/修正**方向仍值得一试（信息通道），但要用"相对自身历史"的分位化处理（直接截面值已被证伪）；③目标价/EPS 修正幅度是未测过的增量字段。

### 主题 4：财务科目背离/盈余质量 —— 机制支持度：经典理论强、A 股实证弱 ⚠️

| 文献 | 结论 | 来源 |
|---|---|---|
| Sloan (1996) *Do Stock Prices Fully Reflect Information in Accruals and Cash Flows About Future Earnings?*, The Accounting Review | **应计异象原型**：应计高（利润与现金流背离）→ 未来收益低 | https://doi.org/10.2308/tar-9608042309 |

**诚实结论**：A 股应计异象的直接验证文献本轮**未检索成功**（Crossref 用 "accrual anomaly China" 等多组关键词命中质量差）。而本项目自有证据链明确偏负：试点财务 20 组全弱（|IC|<0.03、ICIR<0.3）+ 历史"基本面 6 因子 ICIR<0.5"互证 + FIN_OCF_TO_PROFIT 已测未过。
**因子构造启示**：①Sloan 机制（利润-现金流背离=低质量盈利）对应的 `netprofit_yoy - ocf_yoy` 背离族值得低成本一试，但**预期收益低，定位长尾候选，不作为本批重点**；②"营收↑净利↓"背离与 Sloan 应计是不同信号（增长质量 vs 盈利质量），分开登记；③若本批背离族再全灭，可对"财务因子在小盘域不可用"下更硬的结论。

### 主题 5：负向因子/排雷排除条件的量化实践 —— 机制支持度：强 ✅

| 证据 | 结论 | 来源 |
|---|---|---|
| Bali, Cakici & Whitelaw (2011) *Maxing Out*, Journal of Financial Economics | **MAX 彩票效应**原型：近月极端单日涨幅高的股票显著跑输——本身就是"排除条件"式使用 | https://doi.org/10.1016/j.jfineco.2010.08.014 |
| Hai (2023), Cogent Economics & Finance | MAX 效应在中国股市验证成立 | https://doi.org/10.1080/23322039.2023.2175471 |
| Hu (2026), Applied Economics Letters | 涨停/彩票偏好与中国横截面收益（最新佐证） | https://doi.org/10.1080/13504851.2026.2613078 |
| **本项目 MAX5 先例**（白名单文档） | ATR 合格域剔除近 20 日 MAX 极端股：CAGR 16.95%→18.40%（+1.45pp）、回撤 -24.80%→-20.05%；**阈值 0.10~0.30 五档逐字节相同（极端稳健）**；分时段"帮时不伤"（2019-22 +2.84pp/回撤改善 4.75pp，2023-26 逐字节相同）；651/658 笔仅 27 笔不同 | `ATR三方向优化回测_20260815.md`（含 20260816 审计勘误）+ `通宵工作报告_20260816.md` |

**实践共识（文献+内部先例合并）**：排雷条件不需要全截面单调 IC，只需要"高暴露组显著差"；但**必须验证阈值稳健性 + 分时段 + 交易明细 diff**，否则就是过拟合——MAX5 的三重验证（阈值网格/分时段/逐笔 diff）就是本项目"负向因子→排除条件"验证的标准范式，§2 完全照此设计。

### 主题 6：A 股小市值另类数据构造 —— 机制支持度：中 ✅

| 文献 | 结论 | 来源 |
|---|---|---|
| Liu, Stambaugh & Yuan (2019) *Size and Value in China*, Journal of Financial Economics | 中国最小市值段受**壳价值污染**，构造规模因子须剔除最小段（他们剔除 e/p 最低 30%）——直接警告：我们 0~30 亿池的下缘（壳价值段）本身就是雷区 | https://doi.org/10.1016/j.jfineco.2019.03.008 |
| Bali et al. (2011) + Hu (2026)（同主题 5） | 价格行为族（MAX/涨停/隔夜）是 A 股小盘**无需新数据**即可构造的另类维度 | 同上 |

**因子构造启示**：①daily 主库（2009 起，33 列含换手率/涨停价/成交额）可以直接构造试点批没覆盖的**价格行为族**：近 20 日涨停次数、隔夜收益均值、20 日均换手分位、波动率——与 MAX5 同机制不同度量，且零新数据成本；②"低价+小市值+高关注"三叠加是最危险组合（LSY 壳价值 + 注意力反转），可设计成交叉排除条件；③绝对股价低价溢价（真实价<50 的 ATR 池已隐含）可在小盘域做排序候选。

### 三个核心问题的机制回答（任务书点名）

| 问题 | 机制解释 | 文献支持 | 对"排雷排除条件"的支持度 |
|---|---|---|---|
| 龙虎榜上榜后回落 | **注意力溢价反转**：上榜=注意力事件 → 散户注意力买入 → 暂时价格压力 → 反转（Seasholes-Wu 2007 + Barber-Odean 2008 + Hong et al. 2025 三重印证，且 Hong et al. 证明无长期基本面信息） | 强 | **充分，优先做** |
| 研报覆盖负收益 | **忽视溢价**（Arbel-Strebel 1982）：覆盖=关注=定价充分，小盘域无人问津反而有溢价 | 中强 | 支持，第二优先 |
| 北向持股负超额 | 公开学术文献**未找到**直接机制；自有实证（训练段逐年全负 IC -0.078）+ "外资定价时变"间接支持；机制假说=拥挤/已定价（自有推断） | 弱 | 可验证，但报告须标注"自有实证驱动" |

---

## 二、排雷 ablation 实验设计

### 2.1 载体选择：先 ATR 合格域

**载体**：`config/atr_10w_price50.yaml` 口径（10 万本金/8 只/等权/真实价<50/2019-2026/季频），基线 **CAGR 16.95% / 回撤 -24.80% / 夏普 0.894 / 卡玛 0.86（CAGR 口径）**；参照系 MAX5：**18.40% / -20.05% / 0.951 / 0.92**（来源：ATR三方向优化回测_20260815.md 审计勘误后口径）。

理由：
1. **与 MAX5 先例同口径直接可比**，验证范式（阈值网格/分时段/逐笔 diff）整套复用；
2. ATR 合格域是"低波+低价"域，与"高关注排雷"是否正交恰好是检验点——若高关注股已被 ATR% 排序天然规避，ablation 会如实显示（全灭也是有效结论）；
3. 小盘域 TOP-N / P10 双月池**暂缓**：先在 ATR 载体上验证方法论（含证伪条件是否有效），再迁移。

**实现方式**：仿 `strategy/atr_lowvol.py` 的 `max_exclude_pct` 三参数模式，新增三个默认关的参数：`north_exclude_pct` / `lhb_exclude_times` / `res_exclude_count`。**fail-open 纪律：无该数据的小盘股不剔除**（小盘股大量无北向覆盖/无研报，缺数据≠低关注）。

### 2.2 排除条件与阈值网格

| 条件 | 定义 | 阈值网格 | 说明 |
|---|---|---|---|
| 北向持股水平 | 北向持股比例（20 日均值）> X% → 剔除 | X ∈ {3, 5, 8, 10, 15} | 用 20 日均值平滑单日噪声；X=3 激进档，X=15 极端档；无北向数据不剔 |
| 龙虎榜上榜频率 | 近 20 交易日上榜次数 ≥ N → 剔除 | N ∈ {1, 2, 3, 5} | N=1 激进（任何上榜都剔），N=5 极端 |
| 研报覆盖密度 | 近 120 日研报数 ≥ M → 剔除 | M ∈ {2, 5, 10, 20} | M=2 激进，M=20 极端；注意研报数据尾部滞后到 2026-05-15，2026-06 后的期用最近可得窗口 |

### 2.3 实验矩阵与指标

配置总数 ≈ 21 个：

| 组 | 配置 | 数量 |
|---|---|---|
| 基线 | 已有（16.95% 口径） | 1 |
| 单条件全网格 | 北向 5 档 + 龙虎榜 4 档 + 研报 4 档 | 13 |
| 双条件组合 | 三对组合（北向×龙虎榜、北向×研报、龙虎榜×研报），各取中档阈值（8/2/5） | 3 |
| 叠加检验 | MAX5(0.15) × 各条件中档 | 3（+MAX5 本身已有 1） |

- 单配置回测分钟级（20260815 一天 4 版 + 通宵 8+ 版实测），**周六下午窗口可全部跑完**。
- **指标（与 MAX5 同口径）**：复利 CAGR（严禁线性年化，T-20260816-001 教训）、最大回撤、卡玛（CAGR 口径）、夏普、**分段 2019-2022 / 2023-2026**、每期被剔除股票数/比例日志。
- **前置诊断（一次性，先于 ablation）**：用 run_screen 已产出的 ic.csv/面板数据做**五分位分组前向收益检查**（北向/龙虎榜/研报三因子各一组）：若 Q5（最高暴露组）显著差而 Q1-Q4 平坦 → 排除条件有增益空间；若中段驱动尾部平坦 → 直接放弃该条件，省 13 个回测。

### 2.4 判定标准（先定后跑）与证伪条件

**晋级标准（全部满足）**：
1. 全阈值网格内 CAGR ≥ 基线 + 0.3pp（MAX5 实测 +1.45pp 为上限参照，+0.3pp 为最低有效增益）；
2. 回撤不恶化（恶化 >1pp 即否）；
3. 分时段"帮时不伤"：任一子时段 CAGR 恶化 >0.5pp 即否；
4. 剔除比例 ≤30% 池（超过=与池定义重叠，剩余样本失去分散）。

**证伪/放弃条件（出现任一即放弃该方向）**：
1. 仅单一阈值档有增益、相邻档即失效（阈值敏感=过拟合，MAX5 反例是五档逐字节相同）；
2. 增益全部来自剔除某 1-2 只股票（查交易明细 diff：MAX5 是 27/658 笔不同但增益跨年分布；若增益集中单票=运气）；
3. 增益集中在单一子时段且另一子时段恶化（"帮时伤时"≠"帮时不伤"）；
4. 前置诊断显示负 IC 由中段驱动、尾部平坦（§2.5 详述）。

**预期管理**：三条件若有 1-2 个达到 +0.3~1pp 且稳健即为成功；**全部证伪也是有效结论**——说明 ATR 合格域内高关注股已被 ATR% 排序天然规避，排雷价值只在小盘域排序类策略（Project_10）存在，届时把实验迁移 P10 载体再验。

### 2.5 排除条件 vs 排序因子的本质区别 & "IC 为负 → 排除有增益"的推导边界

**信息使用方式的本质区别**：
- 排序因子消费**全截面单调性**——所有分位都携带信息，IC/ICIR 是正确度量；要求训练段 ICIR≥0.5 且逐年同号（S2 门槛）。
- 排除条件只消费**尾部**——最高暴露组的条件期望差，中段排序保持不变（仍由 ATR% 主导）。信息从"连续加权"降为"二元截断"，对因子质量的要求从"全截面稳定单调"降为"尾部条件期望显著为负"。
- 这就是为什么 HK_RATIO_LEVEL（ICIR -0.62）当排序因子不合格（S2 门槛 ICIR≥0.5 是对**正向**因子的要求，翻转后幅度也不够稳），但作为排除条件仍可能有效。

**"IC 为负 → 排除高暴露组有增益"的推导——成立前提与不成立情形（明说）**：
- 成立前提：①负 IC 主要由 Q5 尾部驱动（五分位分组检查可验证）；②高暴露组的负超额在排除后**可实现**（非流动性幻觉——高关注股恰恰流动性好，此风险低）；③月频 IC 为负在**季频 ATR 持有期**内仍为负（时间尺度迁移风险，ablation 直接验证）。
- **不成立情形**：若负 IC 由 Q2/Q3 中段驱动而 Q5 平坦，排除 Q5 无增益——**IC 为负并不自动推出排除有增益**，这就是 §2.3 前置诊断必须先做的原因。
- 反向注意：IC 为负但幅度小（如 LHB_NETRATE_20D -0.035）的因子，尾部驱动即使存在，排除增益也可能低于交易成本扰动，预期要放低。

---

## 三、正式批次扩源候选设计（40 条，batch_r2_20260917）

设计原则：①试点已证伪的**截面水平/截面变动**族不重复登记；②重点转向**自身历史分位化**（DE 第一轮建议）、**科目间背离**、**席位/评级细化**、**价格行为族**（主题 6 启示，零新数据）；③每条仍走 S0 预登记纪律。

### 3.1 数据源现状与本机可得性

| 依赖 | 现状 | 结论 |
|---|---|---|
| 北向 hk_hold_full（ratio/vol） | 2016-06-29 起 2406 交易日（普查确认） | ✅ 分位族全期可算（2018-01 起调仓期均有 ≥250 日历史） |
| 龙虎榜 top_list（l_buy/l_sell/l_amount/amount_rate/reason/net_amount/net_rate） | 2005 起，字段普查确认 | ✅ 席位结构族全期可算（需 load_lhb 扩展 pivot） |
| **top_inst 机构专用席位** | **D:\astock\lhb\top_inst.parquet 未落盘、未普查** | ⚠️ 需先经 Tushare top_inst 接口补数（注意限频），补数前 2 条候选标记 blocked |
| 研报 report_rc_daily（rating/tp/eps/np） | 2006 起，字段普查确认；**尾部滞后到 2026-05-15** | ✅ 可算，但 2026-06 后调仓期 IC 缺（预期覆盖 2018-01~2026-03）；需 load_res 扩展 res_tp/res_eps 面板 |
| 财务 fina_indicator（or_yoy/netprofit_yoy/ocf_yoy/assets_yoy/gross_margin/roe/q_*） | PIT ann_date 锚 95.2%（普查确认） | ✅ 背离族全期可算 |
| daily 主库（turnover_rate/up_limit/pre_close/open/amount） | 2009 起 33 列（普查确认） | ✅ 价格行为族全期可算，**需 ctx 扩展 per-code 序列缓存** |

**实现注意事项（跑批前必做，否则静默跳过）**：
1. `run_screen.py` 用 `getattr(FDS, cand_def["fn"], None)` 找不到 fn 会**静默跳过**——登记≠可跑。新 fn 建议放 `factor_defs_r2.py`，并在 run_screen 的 `import factor_defs_pilot as FDS` 后合并挂载（或直接追加进 factor_defs_pilot.py）。
2. ctx 扩展：北向分位族直接复用 `north_ratio` 面板（调仓期切片 + per-code rolling，计算量可控）；研报 tp/eps 需扩展 `load_res()`；价格行为族需新增 `pctchg_by_code`/`turnover_by_code`/`limit_flag_by_code` 缓存（daily panel 已加载这些列，只差 per-code 组织）。
3. 跑批后 smoke 检查：每个 id 的 IC 期数 ≥90（103 期口径），缺数严重的先查数据管道再谈 IC。

### 3.2 候选清单（40 条，按方向分组）

#### A-北向：自身历史分位族（8 条）｜覆盖：2018-01 起全期
> 核心思路：截面水平已被证伪（外资高比例=拥挤负向），改为"相对自身历史"的边际变化——外资在**这只股票**上是否处于异常状态。

| id | name | direction | source | fn | logic | hint |
|---|---|---|---|---|---|---|
| HK_RATIO_PCT250 | 北向持股比例自身250日分位 | A-北向 | northbound/hk_hold_full | hk_ratio_pct250 | 当前持股比例处于自身历史高位=外资对它已下重注（拥挤，负向） | -1 |
| HK_RATIO_Z250 | 北向持股比例自身250日z值 | A-北向 | northbound/hk_hold_full | hk_ratio_z250 | 同上的标准化版（对分布形态更稳健） | -1 |
| HK_RATIO_CHG20_PCT | 20日增持量自身历史分位 | A-北向 | northbound/hk_hold_full | hk_ratio_chg20_pct | 本次增持幅度在自身历史增持序列中的分位（排除"常年小步增持"噪声） | +1 |
| HK_RATIO_CHG60_PCT | 60日增持量自身历史分位 | A-北向 | northbound/hk_hold_full | hk_ratio_chg60_pct | 中期配置行为的自身分位 | +1 |
| HK_VOL_PCT250 | 持股数自身250日分位 | A-北向 | northbound/hk_hold_full | hk_vol_pct250 | 持股数量维度（排除股价波动对 ratio 的干扰） | -1 |
| HK_RATIO_FROM_PEAK | 距自身历史持股峰值回撤 | A-北向 | northbound/hk_hold_full | hk_ratio_from_peak | 外资从峰值减仓中=撤离信号（负向） | +1 |
| HK_HOLD_DAYS | 北向连续在册天数 | A-北向 | northbound/hk_hold_full | hk_hold_days | 连续持有时长=配置稳定度（连续持有久的外资票≠炒作票，正向） | +1 |
| HK_RATIO_STABILITY | 近60日持股比例波动率 | A-北向 | northbound/hk_hold_full | hk_ratio_stability | 比例低波动=稳定配置盘 vs 高波动=交易盘（交易盘负向） | -1 |

#### A-龙虎榜：席位结构扩展族（7 条，其中 2 条 blocked）｜覆盖：top_list 族全期；top_inst 族待补数
> 核心思路：上榜次数已证伪为正向、确认为负向；细化到**上榜时的席位结构**（谁在买）与**上榜强度**（占流通盘比）。

| id | name | direction | source | fn | logic | hint |
|---|---|---|---|---|---|---|
| LHB_AMOUNT_RATE_20D | 上榜日成交额占流通盘比均值 | A-龙虎榜 | lhb/top_list | lhb_amount_rate_20d | 上榜日成交占比高=筹码换手剧烈（博弈激烈，负向） | -1 |
| LHB_BUY_RATIO_20D | 买方席位成交占比均值 | A-龙虎榜 | lhb/top_list | lhb_buy_ratio_20d | l_buy/l_amount=买方席位集中度，高=游资对倒特征（负向） | -1 |
| LHB_NETRATE_60D | 60日净买入率均值 | A-龙虎榜 | lhb/top_list | lhb_netrate_60d | 中期净买入率水平（20 日版 IC -0.035 的加长版） | +1 |
| LHB_TIMES_60D | 60日上榜次数 | A-龙虎榜 | lhb/top_list | lhb_times_60d | 中期上榜频率（20 日版 IC -0.095 的加长版，验证衰减速度） | -1 |
| LHB_NET_MV_60D | 60日净买入额/流通市值 | A-龙虎榜 | lhb/top_list | lhb_net_mv_60d | 相对规模化的中期席位资金流 | +1 |
| LHB_INST_NET_20D ⚠️blocked | 机构专用席位20日净买入 | A-龙虎榜 | lhb/top_inst（**待补数**） | lhb_inst_net_20d | Hong et al. 2025：机构席位交易短期正相关且无长期信息——机构席位接力的上榜股短期或有动量 | +1 |
| LHB_INST_FLAG_20D ⚠️blocked | 近20日机构席位上榜哑变量 | A-龙虎榜 | lhb/top_inst（**待补数**） | lhb_inst_flag_20d | 机构席位参与=区别于纯游资炒作 | +1 |

⚠️ top_inst 数据源现状：Tushare `top_inst` 接口可得机构专用席位明细，本机 `D:\astock\lhb\` 下未见该表；**补数（含限频规划）并普查后再启用这 2 条**，登记保留占位。

#### A-研报：评级/预测修正分位族（8 条）｜覆盖：2018-01~2026-03（尾部滞后）
> 核心思路：覆盖计数已确认负向；转向**评级与预测的修正方向**（Womack 信息通道）+ 自身历史分位化（DE 第一轮建议）。

| id | name | direction | source | fn | logic | hint |
|---|---|---|---|---|---|---|
| RES_RATING_PCT | 当前评级自身两年分位 | A-研报 | research/report_rc_daily | res_rating_pct | 评级处于自身历史最看好分位=预期打满（利好出尽，负向） | -1 |
| RES_UPDOWN_120D | 120日上调次数-下调次数 | A-研报 | research/report_rc_daily | res_updown_120d | 评级上调净次数=盈利预期改善（信息通道，正向） | +1 |
| RES_TP_RET_60D | 60日目标价隐含收益均值 | A-研报 | research/report_rc_daily | res_tp_ret_60d | tp/close-1，目标价空间=卖方看到的上行（正向） | +1 |
| RES_TP_CHG_120D | 目标价均值120日修正幅度 | A-研报 | research/report_rc_daily | res_tp_chg_120d | 目标价上调=预期修正（信息通道，正向） | +1 |
| RES_EPS_CHG_120D | EPS预测均值120日修正 | A-研报 | research/report_rc_daily | res_eps_chg_120d | 盈利预测上修=基本面边际改善（正向） | +1 |
| RES_COUNT_SEASON | 60日覆盖数-去年同期 | A-研报 | research/report_rc_daily | res_count_season | 同比去季节性后的关注度变化（年报季覆盖潮干扰） | -1 |
| RES_RATING_DISP_60D | 60日评级分歧度 | A-研报 | research/report_rc_daily | res_rating_disp_60d | 评级标准差高=卖方分歧大=不确定性高（负向） | -1 |
| RES_INIT_COVER_120D | 120日首次覆盖哑变量 | A-研报 | research/report_rc_daily | res_init_cover_120d | 该股历史上首篇研报=关注度从零到一的起点（试点 RES_NEW_COVER 负向的真首次版） | -1 |

#### B-财务：科目背离族（8 条）｜覆盖：全期（PIT）
> 核心思路：单科目已全灭；转向**科目间不一致信号**（Sloan 1996 应计机制 + 增长质量背离）。预期收益低，定位长尾候选（§1 主题 4 诚实预期）。

| id | name | direction | source | fn | logic | hint |
|---|---|---|---|---|---|---|
| FIN_REV_NP_DIV | 营收增速-净利增速 | B-财务 | finance/fina_indicator | fin_rev_np_div | 收入涨得比利润快=费用/减值侵蚀（增长质量差，负向） | -1 |
| FIN_REV_NP_GAPSIGN | 营收增净利降背离哑变量 | B-财务 | finance/fina_indicator | fin_rev_np_gapsign | 经典"增收不增利"排雷信号 | -1 |
| FIN_NP_OCF_DIV | 净利增速-现金流增速 | B-财务 | finance/fina_indicator | fin_np_ocf_div | Sloan 应计机制：利润快于现金流=应计高=盈利质量低 | -1 |
| FIN_NP_OCF_DIV_Q | 单季净利-单季现金流背离 | B-财务 | finance/fina_indicator | fin_np_ocf_div_q | 单季版应计（更灵敏） | -1 |
| FIN_REV_ASSET_DIV | 总资产增速-营收增速 | B-财务 | finance/fina_indicator | fin_rev_asset_div | 资产扩张快于收入=低效扩产/商誉堆砌（负向） | -1 |
| FIN_MARGIN_TREND | 毛利率同比变动 | B-财务 | finance/fina_indicator | fin_margin_trend | 毛利率边际改善=定价力增强（正向） | +1 |
| FIN_ROE_STABILITY | 近8季ROE标准差 | B-财务 | finance/fina_indicator | fin_roe_stability | ROE 稳定=盈利可预测（低波动质量，正向） | +1 |
| FIN_Q_NP_ANNUAL | 单季净利/年净利比 | B-财务 | finance/fina_indicator | fin_q_np_annual | 季度利润分布异常=盈余管理嫌疑（负向） | -1 |

#### 其他：价格行为族（9 条，daily 主库衍生，零新数据）｜覆盖：全期（2009 起数据）
> 核心思路：主题 5/6 启示——MAX/涨停/隔夜/换手是 A 股小盘**注意力与彩票偏好**的价格度量，试点批完全没覆盖；与 MAX5 排雷机制同源。

| id | name | direction | source | fn | logic | hint |
|---|---|---|---|---|---|---|
| PX_MAX20 | 近20日最大单日涨幅 | 其他 | daily/stock_daily | px_max20 | MAX 彩票效应（Bali 2011；MAX5 排雷的因子化度量） | -1 |
| PX_LIMITUP_CNT20 | 近20日涨停次数 | 其他 | daily/stock_daily | px_limitup_cnt20 | 涨停密集=彩票股+注意力（Hu 2026；连板炒作特征） | -1 |
| PX_OVERNIGHT_20D | 近20日隔夜收益均值 | 其他 | daily/stock_daily | px_overnight_20d | 隔夜收益高=散户情绪推高开盘（彩票偏好度量，负向） | -1 |
| PX_INTRADAY_20D | 近20日日内收益均值 | 其他 | daily/stock_daily | px_intraday_20d | 日内收益=盘中真买盘（与隔夜互补，正向） | +1 |
| PX_TURN_PCT252 | 20日均换手自身一年分位 | 其他 | daily/stock_daily | px_turn_pct252 | 换手处于自身历史高位=过热（负向，分位化排除个股流动性差异） | -1 |
| PX_AMT_RATIO_20D | 20日均成交额/流通市值 | 其他 | daily/stock_daily | px_amt_ratio_20d | 换手的金额加权版（大单活跃度） | -1 |
| PX_VOL_STD20 | 近20日日收益标准差 | 其他 | daily/stock_daily | px_vol_std20 | 短期波动=彩票/博弈特征（与 ATR% 中长期波动互补） | -1 |
| PX_PRICE_LEVEL | 绝对股价 | 其他 | daily/stock_daily | px_price_level | 低价溢价（LSY 2019 壳价值段注意下缘污染） | +1 |
| PX_LISTED_NEW | 上市未满250日哑变量 | 其他 | daily/stock_daily | px_listed_new | 次新股=无历史+高波动+高关注（三雷叠加，负向） | -1 |

### 3.3 可粘贴 YAML 块（candidates/batch_r2_20260917.yaml）

```yaml
batch_id: r2_20260917
created: 2026-09-16
author: DE-R2
direction: "正式批R2：A-北向自身分位(8) + A-龙虎榜席位结构(7,含top_inst blocked 2) + A-研报修正分位(8) + B-财务背离(8) + 价格行为族(9)"
universe: "smallcap_30e"  # circ_mv 0~30亿 + pe/pb>0 + 非ST + 非停牌
note: "S0 预登记。direction_hint=+ 表示值大=预期看多；IC 按 hint 翻转后判定。fn 在 factor_defs_r2.py 实现（run_screen 需合并挂载），top_inst 2 条补数前勿跑。"
candidates:
  # ============ A-北向：自身历史分位族（hk_hold_full, 2016-06 起） ============
  - {id: HK_RATIO_PCT250,     name: 北向持股比例自身250日分位,  direction: A-北向, source: northbound/hk_hold_full, fn: hk_ratio_pct250,     logic: "当前比例处于自身历史高位=外资已下重注(拥挤)", direction_hint: -1}
  - {id: HK_RATIO_Z250,       name: 北向持股比例自身250日z值,   direction: A-北向, source: northbound/hk_hold_full, fn: hk_ratio_z250,       logic: "自身历史标准分(分布稳健版)", direction_hint: -1}
  - {id: HK_RATIO_CHG20_PCT,  name: 20日增持量自身历史分位,     direction: A-北向, source: northbound/hk_hold_full, fn: hk_ratio_chg20_pct,  logic: "增持幅度相对自身历史的异常度", direction_hint: 1}
  - {id: HK_RATIO_CHG60_PCT,  name: 60日增持量自身历史分位,     direction: A-北向, source: northbound/hk_hold_full, fn: hk_ratio_chg60_pct,  logic: "中期配置行为的自身分位", direction_hint: 1}
  - {id: HK_VOL_PCT250,       name: 北向持股数自身250日分位,    direction: A-北向, source: northbound/hk_hold_full, fn: hk_vol_pct250,       logic: "持股数量维度(排除股价干扰)", direction_hint: -1}
  - {id: HK_RATIO_FROM_PEAK,  name: 距自身持股峰值回撤,         direction: A-北向, source: northbound/hk_hold_full, fn: hk_ratio_from_peak,  logic: "外资从峰值减仓=撤离信号", direction_hint: 1}
  - {id: HK_HOLD_DAYS,        name: 北向连续在册天数,           direction: A-北向, source: northbound/hk_hold_full, fn: hk_hold_days,        logic: "连续持有久=配置盘而非交易盘", direction_hint: 1}
  - {id: HK_RATIO_STABILITY,  name: 近60日持股比例波动率,       direction: A-北向, source: northbound/hk_hold_full, fn: hk_ratio_stability,  logic: "比例高波动=交易盘(负向)", direction_hint: -1}
  # ============ A-龙虎榜：席位结构族（top_list, 2005 起；top_inst 待补数） ============
  - {id: LHB_AMOUNT_RATE_20D, name: 上榜日成交额占流通盘比,     direction: A-龙虎榜, source: lhb/top_list, fn: lhb_amount_rate_20d, logic: "上榜日筹码换手剧烈(博弈激烈)", direction_hint: -1}
  - {id: LHB_BUY_RATIO_20D,   name: 买方席位成交占比均值,       direction: A-龙虎榜, source: lhb/top_list, fn: lhb_buy_ratio_20d,   logic: "买方席位集中=游资对倒特征", direction_hint: -1}
  - {id: LHB_NETRATE_60D,     name: 60日净买入率均值,           direction: A-龙虎榜, source: lhb/top_list, fn: lhb_netrate_60d,     logic: "中期净买入率(20日版-0.035的加长版)", direction_hint: 1}
  - {id: LHB_TIMES_60D,       name: 60日上榜次数,               direction: A-龙虎榜, source: lhb/top_list, fn: lhb_times_60d,       logic: "中期上榜频率(验证-0.095的衰减)", direction_hint: -1}
  - {id: LHB_NET_MV_60D,      name: 60日净买入额/流通市值,      direction: A-龙虎榜, source: lhb/top_list, fn: lhb_net_mv_60d,      logic: "相对规模化的中期席位资金流", direction_hint: 1}
  - {id: LHB_INST_NET_20D,    name: 机构席位20日净买入,         direction: A-龙虎榜, source: "lhb/top_inst(待补数)", fn: lhb_inst_net_20d,  logic: "Hong2025:机构席位短期正相关无长期信息", direction_hint: 1}
  - {id: LHB_INST_FLAG_20D,   name: 近20日机构席位上榜哑变量,   direction: A-龙虎榜, source: "lhb/top_inst(待补数)", fn: lhb_inst_flag_20d, logic: "机构参与=区别于纯游资炒作", direction_hint: 1}
  # ============ A-研报：修正分位族（report_rc_daily, 2006 起, 尾部滞后2026-05） ============
  - {id: RES_RATING_PCT,      name: 当前评级自身两年分位,       direction: A-研报, source: research/report_rc_daily, fn: res_rating_pct,      logic: "评级自身历史最看好分位=预期打满", direction_hint: -1}
  - {id: RES_UPDOWN_120D,     name: 120日上调-下调次数,         direction: A-研报, source: research/report_rc_daily, fn: res_updown_120d,     logic: "评级上调净次数=预期改善(信息通道)", direction_hint: 1}
  - {id: RES_TP_RET_60D,      name: 60日目标价隐含收益,         direction: A-研报, source: research/report_rc_daily, fn: res_tp_ret_60d,      logic: "目标价空间=卖方上行预期", direction_hint: 1}
  - {id: RES_TP_CHG_120D,     name: 目标价120日修正幅度,        direction: A-研报, source: research/report_rc_daily, fn: res_tp_chg_120d,     logic: "目标价上调=预期修正", direction_hint: 1}
  - {id: RES_EPS_CHG_120D,    name: EPS预测120日修正,           direction: A-研报, source: research/report_rc_daily, fn: res_eps_chg_120d,    logic: "盈利预测上修=基本面边际改善", direction_hint: 1}
  - {id: RES_COUNT_SEASON,    name: 覆盖数同比变化,             direction: A-研报, source: research/report_rc_daily, fn: res_count_season,    logic: "同比去季节性的关注度变化", direction_hint: -1}
  - {id: RES_RATING_DISP_60D, name: 60日评级分歧度,             direction: A-研报, source: research/report_rc_daily, fn: res_rating_disp_60d, logic: "卖方分歧大=不确定性高", direction_hint: -1}
  - {id: RES_INIT_COVER_120D, name: 120日首次覆盖哑变量,        direction: A-研报, source: research/report_rc_daily, fn: res_init_cover_120d, logic: "历史首篇研报=关注度零到一", direction_hint: -1}
  # ============ B-财务：科目背离族（fina_indicator PIT, 全期） ============
  - {id: FIN_REV_NP_DIV,      name: 营收增速-净利增速,          direction: B-财务, source: finance/fina_indicator, fn: fin_rev_np_div,      logic: "增收不增利=增长质量差", direction_hint: -1}
  - {id: FIN_REV_NP_GAPSIGN,  name: 营收增净利降哑变量,         direction: B-财务, source: finance/fina_indicator, fn: fin_rev_np_gapsign,  logic: "经典增收不增利排雷", direction_hint: -1}
  - {id: FIN_NP_OCF_DIV,      name: 净利增速-现金流增速,        direction: B-财务, source: finance/fina_indicator, fn: fin_np_ocf_div,      logic: "Sloan应计:利润快于现金流=质量低", direction_hint: -1}
  - {id: FIN_NP_OCF_DIV_Q,    name: 单季净利-现金流背离,        direction: B-财务, source: finance/fina_indicator, fn: fin_np_ocf_div_q,    logic: "单季版应计(更灵敏)", direction_hint: -1}
  - {id: FIN_REV_ASSET_DIV,   name: 总资产增速-营收增速,        direction: B-财务, source: finance/fina_indicator, fn: fin_rev_asset_div,   logic: "低效扩产/商誉堆砌", direction_hint: -1}
  - {id: FIN_MARGIN_TREND,    name: 毛利率同比变动,             direction: B-财务, source: finance/fina_indicator, fn: fin_margin_trend,    logic: "毛利率边际改善=定价力增强", direction_hint: 1}
  - {id: FIN_ROE_STABILITY,   name: 近8季ROE标准差,             direction: B-财务, source: finance/fina_indicator, fn: fin_roe_stability,   logic: "ROE稳定=盈利可预测", direction_hint: 1}
  - {id: FIN_Q_NP_ANNUAL,     name: 单季净利/年净利比,          direction: B-财务, source: finance/fina_indicator, fn: fin_q_np_annual,     logic: "季度利润分布异常=盈余管理嫌疑", direction_hint: -1}
  # ============ 其他：价格行为族（daily 主库衍生, 2009 起, 零新数据） ============
  - {id: PX_MAX20,            name: 近20日最大单日涨幅,         direction: 其他, source: daily/stock_daily, fn: px_max20,            logic: "MAX彩票效应(MAX5排雷的因子化度量)", direction_hint: -1}
  - {id: PX_LIMITUP_CNT20,    name: 近20日涨停次数,             direction: 其他, source: daily/stock_daily, fn: px_limitup_cnt20,    logic: "涨停密集=彩票+注意力(Hu2026)", direction_hint: -1}
  - {id: PX_OVERNIGHT_20D,    name: 近20日隔夜收益均值,         direction: 其他, source: daily/stock_daily, fn: px_overnight_20d,    logic: "隔夜高=散户情绪推开盘(彩票偏好)", direction_hint: -1}
  - {id: PX_INTRADAY_20D,     name: 近20日日内收益均值,         direction: 其他, source: daily/stock_daily, fn: px_intraday_20d,     logic: "日内收益=盘中真买盘(与隔夜互补)", direction_hint: 1}
  - {id: PX_TURN_PCT252,      name: 20日均换手自身一年分位,     direction: 其他, source: daily/stock_daily, fn: px_turn_pct252,      logic: "换手自身历史高位=过热(分位化剔流动性差异)", direction_hint: -1}
  - {id: PX_AMT_RATIO_20D,    name: 20日均成交额/流通市值,      direction: 其他, source: daily/stock_daily, fn: px_amt_ratio_20d,    logic: "换手的金额加权版", direction_hint: -1}
  - {id: PX_VOL_STD20,        name: 近20日日收益标准差,         direction: 其他, source: daily/stock_daily, fn: px_vol_std20,        logic: "短期波动=彩票/博弈特征", direction_hint: -1}
  - {id: PX_PRICE_LEVEL,      name: 绝对股价,                   direction: 其他, source: daily/stock_daily, fn: px_price_level,      logic: "低价溢价(LSY2019注意壳价值下缘)", direction_hint: 1}
  - {id: PX_LISTED_NEW,       name: 上市未满250日哑变量,        direction: 其他, source: daily/stock_daily, fn: px_listed_new,       logic: "次新=无历史+高波动+高关注三雷叠加", direction_hint: -1}
```

**存活预期（敢下判断）**：40 条登记 → 预计 S1 晋级 10-18 条 → S2 通过 0-3 条。价格行为族（PX_*）大概率出现新的稳定负向因子（与试点三源负向同机制），它们的价值出口同样是 §2 的排雷 ablation 而非排序。若 B-财务背离族再全灭，建议对"财务因子在小盘域不可用"下终结结论，后续批次停登财务方向。

---

## 四、调度窗口建议

### 4.1 吞吐依据（试点实测回填）

- 40 因子 × 103 期 = **127s**（数据加载 60-70s 固定 + 逐期计算约 1.2s/因子），两次运行 123.4s/127.1s 稳定（试点报告）。
- 推算：500 表达式 ≈ 11 分钟；1000 ≈ 21 分钟；3000 ≈ 1 小时。试点报告建议正式批**每双周 500-1000 表达式**（留安全余量），本方案采纳。
- ATR ablation：单配置回测分钟级（20260815 单日 4 版实测），21 配置 + 分段统计在 2-3 小时窗口内绰绰有余。

### 4.2 冲突检查表（与现有任务）

依据任务书调度环境：TRAE 定时任务 14 个——交易日 09:15-15:10（各监控/换仓）、16:20-16:45（日终管道）、19:30-20:05（夜间数据）、01:00-01:30（检修）；schtasks Quant_* 系列 30+；**周末/非交易日绝大多数任务跳过**。

| 窗口 | 冲突 | 结论 |
|---|---|---|
| 交易日 09:15-15:10 | 监控/换仓任务活跃 | ✗ 禁用 |
| 交易日 16:20-16:45 | 日终对账管道 | ✗ 禁用 |
| 交易日 19:30-20:05 | 夜间数据刷新 | ✗ 禁用 |
| 每日 01:00-01:30 | 检修 | ✗ 禁用 |
| **周六 09:30-12:00** | 周末任务绝大多数跳过 | ✓ S1+S2 批量窗口 |
| **周六 14:00-18:00** | 同上 | ✓ ablation 窗口 |
| 周日 全天 | 同上 | ✓ 备用/补跑/长网格 |
| 交易日 20:05-23:00 | 夜刷已结束 | ✓ 增量小批（<200 表达式） |

**双调度器红线检查（T-20260910-008）**：新建任何 schtasks 前必须互查 TraeWORK 侧任务注册，确认无同产物双写者；本方案所有新任务的产物（ic_screen/ 目录、ablation 报告）均为新路径，QuantLab 侧唯一 owner。

### 4.3 最终调度方案表

| 任务 | 触发 | 执行内容 | 负责人/执行体 |
|---|---|---|---|
| Quant_FactorScreen_R2（schtasks 新建） | **每双周周六 09:30**（首次：本批 factor_defs_r2.py 实现完成后的第一个周六） | `run_screen.py --batch candidates/batch_<id>.yaml` → 报告+ic.csv 落 ic_screen/ → 飞书汇总卡（登记/晋级/通过漏斗） | **schtasks + 纯脚本**（脚本优先原则：核心动作零 LLM 依赖，agent 只核对+报告） |
| Quant_RiskExclude_Ablation（首轮手动，通过后再挂 schtasks） | 首轮：下个**周六 14:00**（前置五分位诊断完成后） | §2 的 21 配置 ablation：基线+13 单条件+3 双条件+3 叠加，产出 CAGR/回撤/卡玛/分段对照表 | DE/人工 + ATR 回测脚本（`scripts/run_backtest --config ...` 复现式） |
| DE 扩源候选设计（DE 会话委派） | **月度或按需，周五 20:30-22:30**（闲时、无交易风险） | 上网学习 + 产出下一批候选 YAML（只读白名单 + 只写指定输出文件） | DE 委派 + 人工审核落盘（DE 结论必须独立复算后采纳） |
| top_inst 补数（一次性前置） | 下一个交易日 20:30 后（避开 Tushare 白天限频窗口） | Tushare top_inst 增量拉取 → `D:\astock\lhb\top_inst.parquet` + 普查报告 → 解锁 LHB_INST_* 2 条候选 | 人工/脚本 |

**频率拍板建议**：S1+S2 **双周**足够（每窗 500-1000 表达式 × 每月 2 窗 = 月吞吐 1000-2000，远超候选设计速度——瓶颈在 LLM 候选设计与实现，不在算力；若积压可临时加周日窗口）。ablation 首轮跑完拍板后再定常态化（若全灭则不再占用窗口）。候选设计**月度**即可。

---

## 五、结论汇总（敢下判断）

1. **排雷方向文献支持分级明确**：龙虎榜条件（注意力反转，Seasholes-Wu/Hong/Barber-Odean 三重）> 研报条件（忽视溢价，Arbel-Strebel）> 北向条件（公开学术证据缺失，纯自有实证驱动，报告必须如此标注）。三条件都值得进 ablation，预期增益参照 MAX5 的 +0.3~1.5pp 区间。
2. **"IC 为负"不自动等于"排除有增益"**——必须先做五分位尾部诊断再跑 21 配置（§2.3/2.5），否则浪费窗口并引入过拟合风险。
3. **扩源 40 条已给全量 YAML**，五族分置：北向自身分位 8 / 龙虎榜席位结构 7（2 条 blocked 待 top_inst 补数）/ 研报修正分位 8 / 财务背离 8（长尾，再全灭即终结该方向）/ 价格行为 9（最可能产出新负向因子）。
4. **调度**：双周周六模式（S1+S2 上午 09:30 / ablation 下午 14:00），冲突检查表已给，全部动作脚本优先、agent 只做核对与报告，符合定时任务模型降级规范。
5. **证据不足明说**：北向负超额的学术机制、A 股应计异象的直接验证，本轮检索均未获得，相关结论以自有实证为准，不冒充文献支持。

（完）
