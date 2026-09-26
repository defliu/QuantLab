# T-20260925-001 训练切分 embargo 改动 独立审计报告

- 审计人：独立审计员（DE）
- 审计日期：2026-09-25
- 审计对象：`train_g2.py` / `train_baseline.py` / `train_optuna.py` 新增 `train_cutoff()` 训练上界回缩（embargo）
- 审计方式：全部结论基于本人逐行读代码 + 独立编写复算脚本实测（Python 3.11.15，miniQMT venv），未引用任何文档声称
- gpsj 备用数据源状态：`E:/huicexitong/runtime/sj/gpsj.duckdb` 不存在（`is_available` 前置路径检查 False）→ D7 降级为「第二区间 + 全日历不变量断言」，本报告相关结论标注【单源：astock 面板日历】

---

## 摘要

**一句话结论：固定三段切分（生产主链）的 embargo 实现正确、泄漏确实消除、`EMBARGO_DAYS=4` 恰为最小正确值，核心工作通过；但 `train_optuna.py` 季度滚动折的 embargo 语义搞错了回缩基数（fs 是 valid 首日而非训练上界），21 个折中 8 个交易日折起点完全 no-op、残留 1 日标签泄漏，且 PROJECT_MEMORY / repowiki 有两处数字写错。**

问题统计：**严重 0 / 中等 2 / 轻微 4 / 观察 3**

| 编号 | 等级 | 问题 |
|---|---|---|
| P1 | 中等 | 滚动折 `train_cutoff(fs, embargo=1)` 对交易日折起点是 no-op，8/21 折残留泄漏，「季度滚动折同步生效」声称只对了 62%（C3/B4） |
| P2 | 中等 | 文档数字错误：「末样本标签窗口末点 = 2023-06-29」实际为 **2023-06-30**；「rolling fs=2022-01-01 → 2021-12-27」是 embargo=4 的结果，train_optuna 默认 embargo=1 实际为 **2021-12-30**（E1/E2） |
| P3 | 轻微 | 声称行数 395/186/286，实测 394/185/285（各差 1） |
| P4 | 轻微 | 「各 +18 行」不实：git 实测 +24/-1、+36/-4、+28/-3 |
| P5 | 轻微 | repowiki 代码片段省略两处 `return None` 守卫，非逐字 |
| P6 | 轻微 | 改动未 commit，且 train_g2 工作区混入非本任务的 T-fix 改动，版本不可核对 |
| O1 | 观察 | valid→test 边界未加隔离（声明范围外，残留记录） |
| O2 | 观察 | embargo 超大时钳位到 `a[0]`，配合 `>= tr0` 会得到空训练集（静默退化，不崩溃） |
| O3 | 观察 | `--limit` 随机抽样下 `train_cutoff` 的日历来自抽样后日期，结果偏早（仅影响冒烟） |

**总判定：有条件通过**（条件见 §G）。

---

## A. train_cutoff 函数实现正确性

### A1. 三份是否逐字一致 —— PASS（代码逐字一致；docstring 不同）

实测方法：`ast.get_source_segment` 提取三份函数源码段比对。

- 三份完整源码段逐字一致：**False**（docstring 各不相同，见下方）
- **去除 docstring 后 AST 完全一致：True**（即执行逻辑逐字一致）

三份实际代码体（一致，以 train_g2.py:69-85 为准）：

```python
def train_cutoff(dates, hi, embargo):
    """...（三份 docstring 内容不同：g2 写 N3=T+1开→T+4开；baseline 写 --fwd 1；optuna 写 horizon 1 日）..."""
    hi = pd.Timestamp(hi)
    s = sorted(pd.Series(pd.to_datetime(dates)).dropna().unique())
    if not s:
        return None
    a = [t for t in s if t <= hi]
    if not a:
        return None
    k = max(0, min(int(embargo), len(a) - 1))
    return a[-1 - k]
```

- train_g2.py:69-85（docstring 6 行，写 N3 标签口径）
- train_baseline.py:35-51（docstring 写 `--fwd 1 = T 收盘→T+1 收盘`）
- train_optuna.py:44-60（docstring 写 `fwd_ret 默认 horizon 1 日`）

docstring 差异属合理（各自描述本脚本标签），不构成问题。

### A2. 按交易日回缩而非自然日 —— PASS

实现从 `dates` 取 `sorted(unique())` 真实交易日序列再索引回缩（train_g2.py:78-81），不含任何 `pd.DateOffset(days=k)`。实测对照演示自然日口径的坑：

```
2023-06-30 - DateOffset(days=4) = 2023-06-26  （本例恰好同值，因 06-26~06-30 无周末）
2023-07-03 - DateOffset(days=4) = 2023-06-29  （跨周末实际只回缩 2 个交易日）
```

### A3. 入口把 hi 转 pd.Timestamp —— PASS

三份函数第一行均为 `hi = pd.Timestamp(hi)`（train_g2.py:77 / train_baseline.py:43 / train_optuna.py:52）。三个调用点确实传字符串（train_g2.py:210 传 `"2023-06-30"`；train_baseline.py:90 传 `train_rng[1]`；train_optuna.py:180 传 `tr1`、:258 传 `fs`——`fs` 是 Timestamp，但入口归一后两者统一）。实测复现旧写法报错：

```
直接比较 Timestamp <= str 抛 TypeError: '<=' not supported between instances of 'Timestamp' and 'str'
```

### A4. embargo=0 严格返回旧口径 —— PASS

实测（工作日序列 2023-06-19~2023-07-07）：

```
embargo=0 -> 2023-06-30   （= <=hi 的最大交易日）
tc(hi='2023-07-01'(周六), 0) -> 2023-06-30  （hi 非交易日时 = <=hi 最大交易日，与旧 mask date<=hi 完全等价）
负数 embargo(-3) -> 2023-06-30  （max(0,k) 钳住）
```

### A5. 钳位与空序列 —— PASS（附 O2 观察）

- `k = max(0, min(int(embargo), len(a) - 1))`（train_g2.py:84）：embargo=9999 实测返回序列**首元素** `2023-06-19`，不抛错。
- 空输入实测全部返回 None：空列表 / 空 Series / 全 NaT / 全 NaN float / hi 早于全部日期 → `None`。
- 调用点兜底：`train_cutoff(...) or pd.Timestamp("2023-06-30")`（train_g2.py:210、train_baseline.py:90、train_optuna.py:180/258），None 时回旧上界。

**O2 观察**：钳位语义是「回缩到最早可用日」而非报错。若有人把 EMBARGO_DAYS 配成超大值，`cut < tr0` 会得到空训练 mask，LightGBM 在 fit 处才报错——静默退化。当前取值 1/4 远离该区，风险极低，仅记录。

### A6. off-by-one —— PASS

实测：`a = [<=2023-06-30 的工作日]`，`a[-1-4] = a[-5] = 2023-06-26`，与函数返回一致。embargo=4 语义 = 从 `<=hi` 最大交易日往前数第 4 个交易日，正确。单测：

```
embargo=0 -> 2023-06-30   PASS
embargo=4 -> 2023-06-26   PASS
```

---

## B. 泄漏机理与 embargo 取值

### B1. G2 N3 标签公式 —— PASS（机理描述正确）

train_g2.py:177-182：

```python
ao = daily.set_index(["ts_code", "trade_date"])["adj_open"].sort_index()
op1 = ao.groupby(level=0).shift(-1)
op4 = ao.groupby(level=0).shift(-4)
...
fwd3 = op4 / op1 - 1.0
lbl3 = (fwd3 > 0.0).astype("int8")
```

标签 = `adj_open_{T+4} / adj_open_{T+1} - 1`：**open→open**，跨 T+1~T+4 共 **4 个价格点**（实际只用 T+1、T+4 两个，但窗口触及 T+4）、**3 个交易日间隔**；最远触及 **T+4（T 后第 4 个交易日）**。

### B2. EMBARGO_DAYS=4 是否匹配 —— PASS（恰为最小正确值，实测验证）

实测日历（feature_panel_v3 唯一交易日，与 A 股日历一致：06-22~06-24 端午休市）：

```
2023-06-26 后续 4 个交易日: 06-27, 06-28, 06-29, 06-30  → embargo=4 的末样本窗口末点 = 06-30 ≤ 06-30 ✓
（embargo=3 时上界 = 06-27，其 T+4 = 07-03 ∈ valid → 仍泄漏，3 不够）
```

embargo=4 是让「任何训练样本标签窗口完整落在训练期内」的**精确最小值**，不过大不过小。

### B3. train_baseline 标签 —— PASS（embargo=1 匹配）

标签来自 `data/feature_panel.parquet`，由 build_features.py 生成：build_features.py:72 `--fwd` 默认 1，build_features.py:180：

```python
fwd_ret = gshift(close, -args.fwd) / close - 1.0
```

即 **close→close 1 日**（T 收盘→T+1 收盘）。embargo 默认 1（train_baseline.py:70-71）→ 上界 2023-06-29，其标签用 06-30 收盘 ≤ 06-30 ✓。匹配。

### B4. train_optuna 标签 —— 固定三段 PASS；**滚动折见 P1（中等）**

固定三段用同一 v1 面板（`--panel v3` 时用 feature_panel_v3，其 label/fwd_ret 由 build_features_v2.py:301 同公式切自 v2 面板，refresh_panel_v3.py:120-121 直接带出），horizon 均为 1 日，默认 embargo=1 对固定三段正确（上界 2023-06-29，标签日 06-30 ✓）。

**但滚动折的 embargo 语义有误，详见 C3/P1。**

### B5. open→open vs close→close —— PASS

G2 是 open→open N3（最远 T+4），baseline/optuna 是 close→close 1 日（最远 T+1）→ 两类脚本取不同 embargo（4 vs 1）是**必要且正确的**，已在各脚本分别落地（train_g2.py:56 硬编码 / 另两个 `--embargo-days` 默认 1）。B5 关注的「应取不同值」已被满足。

---

## C. 调用点正确性

### C1. train_g2.py —— PASS

train_g2.py:210-213：

```python
cut_tr = train_cutoff(d["trade_date"], "2023-06-30", EMBARGO_DAYS) or pd.Timestamp("2023-06-30")
m_tr = (d["trade_date"] >= "2020-01-01") & (d["trade_date"] <= cut_tr)
m_va = (d["trade_date"] >= "2023-07-01") & (d["trade_date"] <= "2024-06-30")
m_te = (d["trade_date"] >= "2024-07-01")
```

train 上界真的走了 `train_cutoff`；valid 起点 `"2023-07-01"` 原样未动（实测 valid 首个交易日 2023-07-03）。

### C2. train_baseline.py —— PASS

train_baseline.py:90-91：`cut_tr = train_cutoff(df["trade_date"], train_rng[1], args.embargo_days) or ...`，`mask_tr` 用 `cut_tr`。全文件仅此一个训练 mask（mask_va/mask_te 是评估窗），无遗漏。

### C3. train_optuna.py —— 固定三段 PASS；**滚动折部分（P1，中等）**

固定三段（train_optuna.py:180-181）与 baseline 同构，PASS。

滚动折（train_optuna.py:258-259）：

```python
cut_r = train_cutoff(df["trade_date"], fs, args.embargo_days) or fs
m_r = df["trade_date"] <= cut_r
```

git diff 确认旧代码为 `m_r = df["trade_date"] < fs`，**m_r 确实改了**——但语义基数错了。关键差异：

- 固定三段里 `hi=2023-06-30` 是**训练期名义上界**（训练日），回缩 1 个交易日即隔离 1 日 horizon；
- 滚动折里 `fs` 是 **valid 首日**（不是训练日）。旧口径训练上界 = `<fs` 的最大交易日；`train_cutoff(fs, 1)` 从 fs 起数，落在「fs 前 1 个交易日」= 旧上界本身。**当 fs 是交易日时，改动是彻底的 no-op**，训练末样本（fs-1 交易日）的 1 日标签恰好落在 fs（valid 首日），泄漏原样残留。

本人对全部 21 个折起点逐一实测（用真实面板日历）：

| fs | 面板交易日 | 旧上界(<fs) | 新上界(embargo=1) | no-op | 新上界样本标签日 | 漏入 valid |
|---|---|---|---|---|---|---|
| 2021-04-01 | 是 | 2021-03-31 | 2021-03-31 | **是** | 2021-04-01 | **是** |
| 2021-07-01 | 是 | 2021-06-30 | 2021-06-30 | **是** | 2021-07-01 | **是** |
| 2022-04-01 | 是 | 2022-03-31 | 2022-03-31 | **是** | 2022-04-01 | **是** |
| 2022-07-01 | 是 | 2022-06-30 | 2022-06-30 | **是** | 2022-07-01 | **是** |
| 2024-04-01 | 是 | 2024-03-29 | 2024-03-29 | **是** | 2024-04-01 | **是** |
| 2024-07-01 | 是 | 2024-06-28 | 2024-06-28 | **是** | 2024-07-01 | **是** |
| 2025-04-01 | 是 | 2025-03-31 | 2025-03-31 | **是** | 2025-04-01 | **是** |
| 2025-07-01 | 是 | 2025-06-30 | 2025-06-30 | **是** | 2025-07-01 | **是** |
| 其余 13 个折（非交易日 fs） | 否 | 例 2021-12-31 | 例 2021-12-30 | 否 | 2021-12-31 | 否 ✓ |

结论：**21 折中 13 折真正生效、8 折（fs 为交易日）no-op 且残留 1 日泄漏**。PROJECT_MEMORY:1185「季度滚动折同步生效（m_r 由 date < fs 改为 date <= train_cutoff(fs)，即 fs 前 1 个交易日）」的声称只部分成立。

建议修法（一行）：

```python
# train_optuna.py:258 —— fs 是 valid 首日，需在 horizon 之外再多跨过 fs 本身
cut_r = train_cutoff(df["trade_date"], fs, args.embargo_days + 1) or fs
```

或等价地以「fs 的前一交易日」为 hi。修后交易日折上界 = fs-2 交易日（标签日 fs-1 < fs ✓），非交易日折上界 = 最大可用日-2 交易日（标签日 ≤ 最大可用日 < fs ✓），两种情况都干净。

### C4. optuna_report.json 可审计性 —— PASS

train_optuna.py:232-233：

```python
"splits": {"train": [tr0, str(pd.Timestamp(cut_tr).date())], "valid": [va0, va1], "test": [te0, te1]},
"embargo_days": args.embargo_days,
```

训练上界记录的是**回缩后实际值**，且新增 `embargo_days` 字段。滚动折的逐折 cut 未写入报告（轻微缺口，折 IC 仍在 `rolling.fold_ic`）。

### C5. 漏改/旧硬编码残留 —— PASS

- train_g2.py 中残留的 `"2023-07-01"`（:212）与 `"2024-07-01"`（:213、:273 eval_metrics）均为 valid/test 窗定义，属设计内不动项，不是训练上界。
- 三脚本 grep `train_cutoff\(` 结果：g2:210 / baseline:90 / optuna:180、258，共 4 个调用点，全部已切换；无其他训练 mask 使用旧硬编码上界。
- 残留缺口（声明范围外，O1）：valid→test 边界（valid 末样本标签落入 test 期）在三个脚本均未隔离，本任务只声明修 train→valid，记录不扣分。

### C6. 日志打印 —— PASS

三脚本均在切分后打印 embargo 与实际上界：train_g2.py:214 / train_baseline.py:94 / train_optuna.py:184：

```python
log(f"    embargo={EMBARGO_DAYS} 交易日内（train 上界 {pd.Timestamp(cut_tr).date()}）")
```

滚动折无逐折 cut 日志（轻微，可与 C4 一并补）。

---

## D. 独立复算（本人写脚本实测，miniQMT venv 3.11.15，PYTHONUTF8=1）

### D1. ast.parse + 行数 —— PASS（但见 P3）

```
train_g2.py:      ast.parse OK  splitlines=394  readlines=394
train_baseline.py: ast.parse OK  splitlines=185  readlines=185
train_optuna.py:  ast.parse OK  splitlines=285  readlines=285
```

三脚本语法全部通过。**注意：PROJECT_MEMORY:1190 声称 395/186/286，实测各差 1**（疑似把文件末尾换行多计一行）。

### D2. 工作日序列单测 —— PASS

构造 `pd.bdate_range("2023-06-19", "2023-07-07")`（15 个工作日，含 06-19~23、06-26~30、07-03~07）：

```
embargo=0 -> 2023-06-30   PASS（期望 2023-06-30）
embargo=4 -> 2023-06-26   PASS（期望 2023-06-26）
a[-1-4]=a[-5] = 2023-06-26，与函数返回一致
```

Series / list 输入形态结果一致。

### D3. 真实面板 —— PASS（数字全部对上，除 06-29 一处，见 P2）

面板：`data/feature_panel_v3.parquet`（4,812,180 行，唯一交易日 **1863** 个，**2019-01-10 ~ 2026-09-24**，与 PROJECT_MEMORY:1186 声称一致）。

```
embargo=4 -> train 上界 2023-06-26；embargo=1 -> 2023-06-29
剔除的交易日: 2023-06-27 / 06-28 / 06-29 / 06-30（与声称一致）
train 行数: 旧 2,075,966 -> 新 2,066,366  差 9,600  (-0.4624%)   （声称 -0.462% ✓）
剔除日逐日行数: 06-27: 2377 | 06-28: 2387 | 06-29: 2406 | 06-30: 2430
末样本 2023-06-26 的 N3 标签窗口末点 = 2023-06-30（< 2023-07-01 ✓ 泄漏消除）
```

**但 PROJECT_MEMORY:1186 与 repowiki:62 声称「末点 = 2023-06-29」是错的**：06-26 后第 4 个交易日是 **06-30**（06-27/28/29/30），06-29 是第 3 个（+3 口径）。该错误不影响「泄漏消除」的结论（06-30 仍 < 07-01），但它会让读者误以为 embargo=4 比最小值宽松 1 天——实际上 06-26 的窗口末点恰好压在训练期最后一天 06-30 上，**embargo=4 是精确最小值、一点不宽松**（若真按 06-29 的理解，embargo=3 就该够了，那是错的）。

### D4. 极端值 —— PASS

```
embargo=9999（超过可用日数）-> 2023-06-19（钳位到序列首元素，不崩溃）
```

### D5. 空序列 / 全 NaN —— PASS

```
空列表 -> None；空 Series -> None；全 NaT -> None；全 NaN float -> None；hi 早于全部日期 -> None
```

### D6. rolling fs=2022-01-01 —— 实测与文档声称不一致（P2 的一半）

```
embargo=1（train_optuna 实际默认）-> 2021-12-30
embargo=4（G2 口径）             -> 2021-12-27
```

PROJECT_MEMORY:1186 写「rolling 折 fs=2022-01-01 → 训练上界 2021-12-27」——这是用 **embargo=4** 测的数，放在描述 train_optuna 滚动折（默认 embargo=1，结果应为 **2021-12-30**）的语境里，**误导**。

### D7. 交叉验证 —— 降级执行【单源：astock 面板日历】

gpsj 备用源路径不存在（本机 E 盘存在但 `huicexitong/runtime/sj/gpsj.duckdb` 缺失），按 AGENTS 降级规则明确标注。降级替代验证：

- **全日历不变量断言**：对全部 1843 个候选 hi × k∈{1,2,3,4} 共 **7,372 组**断言 `tc(hi,k) 后第 k 个交易日 == max(交易日<=hi)`，**失败 0**。这从语义上证明了索引正确性贯穿整个日历，而非只验一个边界。
- **多边界抽验**（第二区间）：
  ```
  hi=2021-06-30: e4->2021-06-24  e1->2021-06-29
  hi=2022-06-30: e4->2022-06-24  e1->2022-06-29
  hi=2024-06-30: e4->2024-06-24  e1->2024-06-27（06-30 周日，末交易日 06-28）
  hi=2025-06-30: e4->2025-06-24  e1->2025-06-27
  hi=2026-06-30: e4->2026-06-24  e1->2026-06-29
  ```

---

## E. 文档准确性

### E1. PROJECT_MEMORY.md T-20260925-001 条目数字核对 —— 部分（P2/P3）

| 声称（PROJECT_MEMORY:1179-1191） | 实测 | 判定 |
|---|---|---|
| train 上界 2023-06-30 → 2023-06-26 | 2023-06-26 | ✓ |
| 剔除 2023-06-27/28/29/30 四个交易日 | 一致 | ✓ |
| 2,075,966 → 2,066,366 行 | 一致 | ✓ |
| -0.462% | -0.4624% | ✓ |
| 末样本标签窗口末点 = 2023-06-29 | **2023-06-30** | **✗（P2）** |
| rolling fs=2022-01-01 → 2021-12-27 | embargo=1 实际 **2021-12-30**（12-27 是 embargo=4 的数） | **✗ 语境误导（P2）** |
| 1863 交易日 2019-01-10~2026-09-24 | 一致 | ✓ |
| a[-1-4] 索引语义 | 一致 | ✓ |
| ast.parse 行数 395/186/286 | **394/185/285** | **✗（P3）** |
| 「各 +18 行」 | git 实测 +24/-1、+36/-4、+28/-3 | **✗（P4）** |
| 「embargo=0 时严格回到旧口径」 | 实测成立 | ✓ |
| 「空序列返回 None / 超大钳位不抛错」 | 实测成立 | ✓ |
| 「hi 转 Timestamp 防 TypeError」 | 实测复现旧写法确实抛 TypeError | ✓ |

### E2. repowiki「网格寻优与防过拟合方法论.md」§五 —— 部分（P2/P5）

- **代码片段非逐字（P5）**：doc 第 46-53 行的片段省略了真实代码中的两处守卫：

  ```python
  # 真实代码（train_g2.py:79-83）有、doc 片段没有：
      if not s:
          return None
      a = [t for t in s if t <= hi]
      if not a:
          return None
  ```

  doc 片段直接从 `s = sorted(...)` 跳到 `a = [...]` 再跳到 `k = ...`。空序列行为只在 §五「边界健壮性」文字里提了一句，片段本身与真实代码**不逐字一致**。
- **06-29 数字错误（P2）**：doc 第 62 行「末样本 2023-06-26 的标签窗口第 4 个价格点 = 2023-06-29」——第 4 个价格点是 **06-30**（06-27/28/29/30），同 E1。
- **其余数字**：2,075,966→2,066,366（-0.462%）、剔除四天、「不用 DateOffset」要点、「hi 转 Timestamp」坑、「基线 fwd_ret 是 close.shift(-1)/close - 1，horizon 1 日」（与 build_features.py:72/180 实测一致）均准确。
- **结论是否夸大：不夸大，反而克制**。doc 第 66 行明确「embargo 只是顺手补的小缝……真正的大头仍是『回测期 ⊆ test 集』的双重使用（§四）——embargo 不解决它，也不该被误读为解决了它」。

### E3. 章节编号 —— PASS

一(6)→二(16)→三(25)→四(29)→五(38)→六(74)→七(84)→八(94)，连续、无重复、无跳号。

### E4. 有无夸大表述 —— PASS

同 E2 最后一条：§五与 §四 的关系表述准确，明确声明 embargo 不解决测试集双重使用，无夸大。

### E5. 「各 +18 行」是否属实 —— 不属实（P4）

git numstat（工作区 vs HEAD 922c422）：

```
train_baseline.py: +24 / -1
train_g2.py:       +36 / -4
train_optuna.py:   +28 / -3
```

没有任何一种合理口径能得到 18（train_cutoff 函数块本身是 17 行代码+docstring、含空行 19 行新增）。另注：train_g2 的 diff 里还混入了**非本任务**的改动（eval_metrics test 窗口 T-fix + pointer NaN 防护，docstring 自标「T-fix」），说明工作区堆积了多个任务的未提交修改——见 P6。

---

## F. 完整性与遗漏

### F1. train*.py 全量清点 —— PASS

glob `**/*train*.py` + `overnight*.py` 结果（Project_16 下）：

| 文件 | embargo | 判定 |
|---|---|---|
| train_g2.py | 有（EMBARGO_DAYS=4，:56） | 生产 G2 重训路径 ✓ |
| train_optuna.py | 有（--embargo-days 默认 1） | 生产 V1.3 重训路径 ✓（滚动折缺陷见 P1） |
| train_baseline.py | 有（--embargo-days 默认 1） | 阶段1 基线，非周训路径，加了无害 |
| versions/V20260909_门禁升级_纸面AB/train_g2.py | 无 | 历史归档，不该改 ✓ |
| overnight_20260825.py | 无 | 一次性研究脚本（08-25 通宵），非生产路径 ✓ |
| overnight_opt_20260825.py | 无 | 同上 ✓ |

grep `def train_cutoff` 全项目仅命中三个主脚本，归档脚本确认未动。生产重训路径（train_optuna + train_g2）**全部已覆盖**，无应加未加。

### F2. 「有意保留旧口径」清单 —— PASS

PROJECT_MEMORY:1191 / repowiki:68 列出的三个保留脚本（overnight_20260825.py、overnight_opt_20260825.py、versions/V20260909_*/train_g2.py）与实际 grep 结果一致，且给出了理由（历史归档/一次性研究，复现 G2 生产训练用带 embargo 的 train_g2.py 即可）。理由充分。

### F3. quant_weekly_retrain 定时任务 —— PASS（已查实）

schtasks 只读查询结果：

```
Execute:   powershell.exe -NoProfile -ExecutionPolicy Bypass
           -File "D:\QuantLab\projects\Project_16_LightGBM股票大师\run_scheduled.ps1" -Mode retrain
Trigger:   每周一 17:00（DayOfWeek=2）
LastRun:   2026-09-21 17:00:00, LastResult=0
NextRun:   2026-09-28 17:00:00
```

retrain 模式按 PROJECT_MEMORY:1153-1155 的 09-21 执行记录，覆盖两条训练链：train_optuna.py（V1.3 候选 → auto_promote 门禁）与 train_g2.py（G2 链，前置 enh 新鲜度硬门禁）。两个脚本均已带 embargo，且计划任务直接执行工作区文件，**新口径于下一次周训 2026-09-28（周一）17:00 起自动生效**，无需等 commit。参数：train_g2 用硬编码 EMBARGO_DAYS=4；train_optuna 用默认 --embargo-days 1（滚动评估仅 --rolling 时附加）。

注：run_scheduled.ps1 是否传 --rolling/--embargo-days 覆盖，本次未读该 ps1（不在允许清单内）；如需 100% 确认参数面，建议后续补查。

### F4. Python 3.6.8 兼容扫描 —— PASS

三脚本扫描 `:=` / `match x:` / PEP585 泛型（`list[`/`dict[` 注解）均为 **0**。f-string 大量使用——但 f-string 是 3.6 引入的语法，3.6.8 本身支持；且这三个是研究侧 3.11 运行脚本、非 QMT 产物，不触碰 AGENTS 红线。（AGENTS 把 f-string 列为 QMT 禁用项与 3.6 事实不符是另一回事，与本任务无关。）

### F5. mock/测试残留 —— PASS

grep `mock|MOCK|pytest|unittest|test_` 三脚本：命中项全部是 `test_ic` / `test_rng` / `test_panel` 等评估变量名，无 mock、无测试框架、无 `if __name__ == "__test__"` 类残留。train_g2 的 `--limit` 冒烟参数是文档化功能，非残留。

### F6. check_embargo.py 已删除 —— PASS

glob 全项目 `check_embargo*` 无命中；git status 未跟踪清单中亦无此文件。一次性脚本确实未留仓库。

---

## G. 总判定

### **有条件通过**

**通过的部分（主体）**：
- `train_cutoff` 实现（A1-A6）完全正确，三份代码逻辑逐字一致，边界行为健壮（D2/D4/D5 实测全过）；
- 生产主链固定三段切分的泄漏确实消除：G2 embargo=4 恰为最小正确值（末样本窗口末点 06-30 恰好压线训练期最后一天），baseline/optuna embargo=1 与 1 日 close→close 标签匹配（B2/B3/D3 实测）；
- 全日历 7,372 组不变量断言 0 失败（D7）；
- 调用点无漏改、报告可审计、日志可核对（C1/C2/C4/C5/C6）；
- 生产重训路径全覆盖、归档脚本有意保留口径正当（F1/F2），新口径 2026-09-28 周训自动生效（F3）。

**条件（修复后才可视为完全通过）**：

1. **【P1，中等】修 train_optuna.py:258 滚动折 off-by-one**：
   ```python
   # 现状
   cut_r = train_cutoff(df["trade_date"], fs, args.embargo_days) or fs
   # 改为（fs 是 valid 首日，需额外跨过 fs 本身）
   cut_r = train_cutoff(df["trade_date"], fs, args.embargo_days + 1) or fs
   ```
   修后 21 个折（交易日/非交易日起点）全部干净；建议同时把逐折 cut_r 写入 `report["rolling"]` 并补一行日志。
2. **【P2，中等】更正两处文档数字**：
   - PROJECT_MEMORY:1186 与 repowiki:62：「末样本 2023-06-26 的 N3 标签窗口末点 = 2023-06-29」→ **2023-06-30**（并建议补一句「embargo=4 为精确最小值，末点恰压训练期最后交易日」）；
   - PROJECT_MEMORY:1186：「rolling fs=2022-01-01 → 2021-12-27」→ 注明该数是 embargo=4 口径；train_optuna 默认 embargo=1 下为 **2021-12-30**（若采纳条件 1 的 +1 修法则变 2021-12-29，届时以修后实测为准）。
3. **【P6，轻微】尽快 commit 本次改动**：当前三个脚本 + 文档全部处于未提交状态，且 train_g2.py 工作区混入非本任务的 T-fix（eval_metrics test 窗口过滤、pointer NaN 防护）。按「版本可核对」的项目纪律，至少把 T-20260925-001 与 T-fix 分两个 commit 落库，否则 09-28 周训跑的代码无法用 commit 哈希核对。
4. **【P3/P4/P5，轻微】文档顺手勘误**：行数 395/186/286 → 394/185/285；「各 +18 行」→ 按实测 +24/+36/+28（或改为「新增同名 17 行函数 + 调用点改造」的准确表述）；repowiki 代码片段补上两处 `return None` 守卫使其与真实代码逐字一致。

**遗留观察（不阻塞，记录在案）**：
- O1：valid→test 边界（三个脚本的 valid 末样本标签窗口落入 test 期）未隔离——本任务范围只声明 train→valid，若后续追求 Purged K-Fold 全标准，需在 valid 侧做同样回缩；
- O2：embargo 超大时静默钳位到首日（不崩溃但训练集可能为空），可在 train_cutoff 内加一条 `embargo >= len(a)` 告警日志；
- O3：`--limit` 随机抽样冒烟时 train_cutoff 的日历来自抽样后行，回缩结果偏早——仅影响冒烟路径，无需修。

**审计声明**：本报告全部数字由本人独立复算（脚本已按约定用后删除），未引用被审计方任何声称；D7 因 gpsj 备用源在本机不可用而降级为全日历不变量断言 + 多边界抽验，相关结论为【单源：astock 面板日历】口径，与本仓库现行降级规则一致。
