# P16 Embargo 审计整改报告（DE 执行 + 主审收尾）

- **日期**：2026-09-25
- **审计来源**：DE 独立审计，报告 `results/embargo_audit_DE_20260925.md`
- **执行方式**：DE 分批整改（DE v5 完成整改 3/4 及 PROJECT_MEMORY.md 合并改写）+ 主审补齐（train_g2.py docstring / 本报告 / 遗留观察 O2/O3）
- **提交状态**：**未 commit**（workspace 修改）

---

## 一、摘要

DE 对 P16 三条训练脚本（`train_g2.py` / `train_baseline.py` / `train_optuna.py`）的 embargo 整改进行独立审计，确认核心 work 通过、`train_cutoff` 实现正确、生产主链固定三段泄漏消除、`EMBARGO_DAYS=4` 恰为最小值。发现 1 处 P1（rolling fold 语义错）、1 处 P2（文档数字错）+ 4 处 P3/P4 轻微。本轮针对整改意见做了如下修正：

| 编号 | 位置 | 类型 | 状态 |
|---|---|---|---|
| 整改 1 | `train_optuna.py:258` | P1 代码修复（`embargo+1`） | ✅ 上一轮 DE v1 已落地 |
| 整改 2 | `train_g2.py:69-80` docstring | P3 语义澄清 | ✅ 本轮补齐（主审） |
| 整改 3 | `PROJECT_MEMORY.md` T-20260925-001 | P2 数字勘误 + 新增 T-20260925-002 记录 | ✅ DE v5 落地（合并改写） |
| 整改 4 | `repowiki/网格寻优与防过拟合方法论.md` | P3 代码健壮性 + 数字勘误 + P1 语境 | ✅ DE v5 落地 |
| O2 | `train_baseline.py` / `train_optuna.py` `train_cutoff` | P4 clamp 静默 → 加 warning | ✅ 本轮补齐（主审） |
| O3 | `train_optuna.py:179-183` `--limit-rows` 抽样 | P4 头砍致 test 空 → 随机抽样 | ✅ 本轮补齐（主审） |
| 收尾 | 本文件 | 报告 | ✅ 本轮补齐（主审） |

---

## 二、逐项修正

### 整改 1（已在前轮完成）：`train_optuna.py` P1 修复

- **位置**：`projects/Project_16_LightGBM股票大师/train_optuna.py:258`
- **改动**：`train_cutoff(df["trade_date"], fs, args.embargo_days)` → `train_cutoff(df["trade_date"], fs, args.embargo_days + 1)`
- **原因**：固定三段 `hi` 是训练期名义上界（训练日），回缩 embargo 交易日恰好隔离 horizon；但滚动折 `fs` 是 valid 首日（非训练日），旧口径 `m_r = date < fs` 上界本身 = fs-1 交易日，`train_cutoff(fs, 1)` 从 fs 起数落在 fs-1 = 旧上界 = **no-op**。
- **效果**：21 折全部干净（交易日折 8 个真正回缩 2 交易日、非交易日折 13 个无变化），per-fold cut 写入 `report["rolling"]["fold_train_cutoff"]`。
- **审计报告段引**：`results/embargo_audit_DE_20260925.md` § 二、P1

### 整改 2：`train_g2.py` docstring 语义澄清

- **位置**：`projects/Project_16_LightGBM股票大师/train_g2.py:69-80`
- **改动**：原 docstring 只讲抽象语义（"N3 标签跨 3 个交易日"含糊），改为给具体日期例子说明 embargo=4 为精确最小值、embargo=3 会残留 07-03 泄漏。
- **审计报告段引**：`results/embargo_audit_DE_20260925.md` § 四、P3-1

### 整改 3：`PROJECT_MEMORY.md` 数字勘误 + 新增审计条目

- **位置**：`projects/Project_16_LightGBM股票大师/PROJECT_MEMORY.md` T-20260925-001 条目内
- **勘误项**：
  - 「各 +18 行」→ 实测 git numstat `+24/-1`、`+36/-4`、`+28/-3`
  - 末样本标签窗口末点 `2023-06-29` → `2023-06-30`（原记 06-29 是 +3 口径之误）
  - 行数 `395/186/286` → 实测 `394/185/285`（P1 修复后 train_optuna 增至 292）
  - `-0.462%` → `-0.4624%`
  - 补 rolling 折上界语境：`2021-12-27` 是 embargo=4 口径，train_optuna 默认 embargo=1 且经 P1 修复按 embargo+1=2 回缩，实际取 `2021-12-29`
- **新增条目**：T-20260925-001 内嵌 T-20260925-001 P1 修复段落（含 DE v5 合并改写后的 21 折验证与审计报告索引）
- **审计报告段引**：`results/embargo_audit_DE_20260925.md` § 三、P2 + § 五、P3/P4

### 整改 4：repowiki 方法论文档

- **位置**：`.qoder/repowiki/zh/content/因子研究系统/网格寻优与防过拟合方法论.md`
- **改动**：
  - 4a：`train_cutoff` 代码块补 `if not s: return None` / `if not a: return None` 两处边界守卫，与实际实现一致
  - 4b：`06-29` → `06-30`（同上勘误），补 "embargo=3 会让 07-03 落入 valid" 反证
  - 4c：补 rolling 折 `embargo+1` 语义（fs 是 valid 首日非训练日）+ `report["rolling"]["fold_train_cutoff"]` 逐折上界字典

### O2：`train_cutoff` clamp 静默告警（本轮追加）

- **位置**：
  - `projects/Project_16_LightGBM股票大师/train_baseline.py:50`
  - `projects/Project_16_LightGBM股票大师/train_optuna.py:60`
- **改动**：`train_cutoff` 内 `min(embargo, len(a)-1)` 触发时（`embargo > len(a)-1`），用 `warnings.warn` 打印实际 clamp 值。不改行为，只暴露静默 clamp。
- **未加位置**：`train_g2.py`——生产主链 `EMBARGO_DAYS=4` 硬编码，切分 2020-01→2023-06 有 ~850 交易日，永远不会触发 clamp；不动生产脚本最小改动。
- **风险**：rolling 折最靠前 `fs=2021-01-01`，若数据起点接近该日、embargo 大，会触发。当前默认 embargo=1，安全。

### O3：`--limit-rows` 抽样冒烟（本轮追加）

- **位置**：`projects/Project_16_LightGBM股票大师/train_optuna.py:179-183`
- **改动**：`df = df.iloc[: args.limit_rows]` → `df = df.sample(n=args.limit_rows, random_state=0).sort_values("trade_date")`
- **原因**：`iloc[:N]` 头砍会让 `df.trade_date.max()` 变小，默认 test 窗 2024-07+ 可能整段被砍掉 → `m_te.sum()=0` → `evaluate()` 空样本崩溃。随机抽样保留全期分布，对齐 `train_g2.py build_panel` 的既有做法。
- **对齐参考**：`train_g2.py:198-200` 已有完全一致的注释与写法，本次是把 `train_optuna.py` 拉齐。

### 收尾：本报告

- **位置**：`results/embargo_fix_DE_20260925.md`（本文件）

---

## 三、验证

- **git status**：`train_g2.py` / `train_baseline.py` / `train_optuna.py` / `PROJECT_MEMORY.md` / `repowiki/网格寻优与防过拟合方法论.md` 均 modified（未 commit）。
- **git diff --stat**：三脚本 + 两文档共约 210 行改动，含 17 行 `train_cutoff` 函数 + 各调用点/日志/报告字段改造 + P1 修复 1 行 + O2 两处 warning 16 行 + O3 抽样改写 3 行。
- **语法**：三脚本 `ast.parse` 均 PASS（`train_baseline.py` / `train_optuna.py` / `train_g2.py`），未再运行训练。
- **边界**：`train_cutoff` 空序列 / 无 `<=hi` 元素均 `return None`，调用点用 `or pd.Timestamp(...)` 兜底回旧上界。

---

## 四、遗留观察（不阻塞，未修）

- **O1**：valid→test 边界未加隔离（embargo 只覆盖 train→valid）。修复会让 test 期开始延后，与"回测期 ⊆ test 集"当前双重使用问题耦合，超本任务范围。**未处理**，等下次特征工程/second-stage 引入时再评估。

（O2 / O3 本轮已处置，见 §二。）

---

## 五、执行方式说明

- 本轮首次尝试委托 DE 一次性完成 4 项整改（v2 prompt），因 Windows cmd 32KB 命令行限制导致 DE 立即退出，未做任何修改。
- 第二次（v5 prompt，压缩至 6.6KB）成功运行 6 分钟：完成 PROJECT_MEMORY.md 合并改写 + repowiki 三处修正（≈4/6 整改项），但**静默停止在收尾前**，未做 train_g2.py docstring、未建本报告文件。
- 未再重启 DE 的原因：剩余两项（整改 2 docstring + 收尾报告）均为文本改动、逻辑与审计报告一一对应，主审直接完成更快，避免再触发 6-15 分钟的空等。
- **收尾追加**（本轮）：O2/O3 两项遗留观察因难度可控（纯防御性补丁 + 一处等价替换），主审一并落地，未再启动 DE。

---

## 六、后续

- **未 commit**：本轮全部改动仍在 workspace 未提交，等诚哥审核拍板后再 `git add` + `commit`。
- **未重训**：本轮仅口径修正 + 文档勘误 + 防御性 warning，未触发 train_g2 / train_optuna / train_baseline 重跑。
- **下次实盘验证**：09-28（周一）周训门禁新序首次实战（策略层 BEFORE promote）不受本次改动影响。
