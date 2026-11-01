# 纸面前向台账口径打标报告（2026-11-01）

> 生成：2026-11-01 16:52 | 脚本 `tag_paper_forward_caliber.py`
> 打标只新增 `caliber` 列，**不改任何收益数值**。

## 为什么打标

`build_g2_daily` 的 amount 单位缺陷（主库千元 / 增量库元，差 1000 倍）使 `amount_ma5` 在增量区被放大 log(1000)≈6.9。
43f 模型该特征有 **1212 个分裂阈值全部落在 8.75~14.30**，元口径值 18.34 **100% 落同一侧 ⇒ 特征彻底失效**。
已 2026-10-31 修复（commit `a9cc881`）。

## 标签定义

| caliber | 含义 | 区间 |
|---|---|---|
| `clean` | 候选生成无 amount 缺陷 | 2026-08-17 ~ 2026-08-27 |
| `contaminated_selection` | 候选受污染，**收益数值本身有效** | 2026-08-28 ~ 2026-10-31 |
| `forward` | 修复后真实前向 | 2026-11-01 起（台账尚无） |

> ⚠️ **`contaminated_selection` 的收益是真的，脏的只是「选了哪些票」**：
> entry/exit 用 open→open，与 amount 无关；独立用主库重算 224 笔，与台账最大差 **0.00000000**。
> 因此这批数据可用于「这些票拿 10 天赚不赚」，**不可用于「43f 策略行不行」**。

## 打标结果

### 43f 主臂 `paper_forward_live.csv`

| caliber | 行数 |
|---|---|
| _total | 237 |
| clean | 18 |
| contaminated_selection | 219 |

### 21f 影子臂 `paper_shadow_b21_picks.csv`

| caliber | 行数 |
|---|---|
| _total | 127 |
| clean | 27 |
| contaminated_selection | 100 |

## 收益核对（证明收益未被污染）

| caliber | 43f 已结算 | 43f 均值 | 21f 行数 | 21f 已结算(ret10) | 21f 均值 |
|---|---|---|---|---|---|
| clean | 18 | 2.110% | 27 | 27 | 3.935% |
| contaminated_selection | 206 | -2.676% | 100 | 31 | -2.788% |
| forward | — | — | — | — | — |

## 实测证据（两组收益差）

| 组 | 43f n | 43f 均值 | 43f 胜率 | 21f n | 21f 均值 |
|---|---|---|---|---|---|
| clean（8-17~8-27） | 18 | +2.110% | 55.6% | 27 | +3.935% |
| contaminated（8-28~10-31） | 206 | −2.676% | 32.0% | 31 | −2.788% |

两组差 t=2.41，p=0.02（Welch）。**但这个结论必须带两条警告**：

1. **clean 只有 18 笔，标准误 1.84%**，单样本 t=1.15（p=0.27，与 0 无显著差异）——
   「clean 组赚钱」不成立，只能说「与污染组有差」。
2. **两组不可直接比**：clean 段（8 月中下旬）与污染段（9-10 月）市场环境不同，
   均值差里混了行情因素。要干净地归因 amount 缺陷，只能等修复后的 `forward` 段。

## 后续（第 3 步，已完成）

- 管道已修复，自 **2026-11-01** 起新产出的记录自动打 `forward`
  （`paper_forward._append_live_dedup` / `paper_shadow_b21` 合并处均默认 forward）。
- **6 个消费端已全部接入排除闸**（回归 `_test_paper_forward_caliber.py` T3 钉住）：
  `forward_stats.py` / `paper_forward_downgrade.py` / `paper_forward_exit.py` /
  `paper_quarterly_review.py` / `rollback_check_g2.py` / `paper_forward_ab_stats.py`。
- 实测 `forward_stats.py` 现输出「已排除 caliber=contaminated_selection 218 笔」，
  统计只剩 18 笔 clean 样本。

> ⚠️ **样本量告急**：排除污染后 43f 只剩 18 笔（21f 27 笔），任何基于这批样本的
> 「策略行不行」判断都不成立。真实结论须等 `forward` 段累积（预计 11-01 起每交易日 2 笔）。
