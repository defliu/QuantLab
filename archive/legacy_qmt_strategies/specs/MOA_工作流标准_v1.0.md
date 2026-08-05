# MOA 工作流标准 v1.0
## 核心原则（强制执行）

### 1. 单步执行时间 ≤ 120 秒
- 任何脚本执行超过120秒，必须拆分为独立子步骤
- 每个子步骤的timeout设置为实际预期时间的1.5倍
- 单次execute_code调用最大timeout=120秒

### 2. 缓存复用机制（强制执行）
- 所有大型不可变数据集必须保存为parquet格式
- 回测面板数据必须一次性缓存，后续所有脚本直接读取
- 缓存目录统一：`reports/v3_optimize/cache/`
- 缓存命中后，执行时间应降低>80%

### 3. 前置依赖校验（强制执行）
- 每个脚本执行前必须验证：
  - 前置阶段的交付物存在且内容有效
  - 所需的Python库已安装
  - 缓存文件存在且可读取
- 校验不通过时立即终止并输出可执行的修复步骤

### 4. 断点续跑机制（强制执行）
- 每个阶段执行前检查checkpoint文件：`checkpoint/phaseX_done.json`
- checkpoint存在且校验通过则跳过执行直接加载结果
- checkpoint包含：完成时间、关键指标、文件哈希
- 失败的阶段重新执行时自动从断点恢复

### 5. 硬门验证机制（强制执行）
- 每个阶段结束后必须运行硬门校验逻辑
- 指标偏差超过阈值立即终止并报错
- 关键校验项必须与任务书100%对齐

### 6. 错误自愈机制（强制执行）
- 瞬时错误（文件锁、内存峰值）自动重试最多2次
- 永久性错误立即终止并输出结构化错误报告
- 禁止对相同失败代码重复执行超过3次

---

## 标准工具模块
路径：`agent_hub/moa_workflow_utils.py`

提供以下标准函数：
- `precheck(required_files, required_packages)` — 前置依赖校验
- `save_checkpoint(phase, metrics, deliverables)` — 保存断点
- `load_checkpoint(phase)` — 加载断点
- `validate_hard_gate(actual, expected, tolerance, name)` — 硬门验证
- `auto_update_log(log_path, phase, status, metrics)` — 自动更新执行日志

---

## 标准执行流程

```
1. 调用 precheck() 验证所有依赖
2. 调用 load_checkpoint() 检查是否已完成
3. 执行核心逻辑（单步≤120秒）
4. 调用 validate_hard_gate() 验证结果
5. 调用 save_checkpoint() 保存断点
6. 调用 auto_update_log() 更新进度
```

---

## 任务A工作流适配

| 阶段 | 单步预计耗时 | 前置依赖 | 硬门校验 |
|------|-------------|----------|----------|
| Phase 0 | 82s | 无 | 回测指标与P0偏差<0.1% |
| Phase 1 | 5s | holdings_detail.csv | weight_pct总和=100%±0.1% |
| Phase 2a | 30s | holdings_detail.csv + panel.parquet | 中性化后R²<0.05 |
| Phase 2b | 30s | holdings_detail.csv + panel.parquet | 中性化后R²<0.03 |
| Phase 2c | 30s | holdings_detail.csv + panel.parquet | 中性化后R²<0.02 |
| Phase 2d | 15s | 2a/2b/2c产出 | 对比表完整 |
| Phase 3a | 30s | holdings_detail.csv + panel.parquet | IC均值统计显著(p<0.05) |
| Phase 3b | 20s | 3a产出 | 5个区间IC全部计算完成 |
| 汇总报告 | 15s | 所有交付物 | 8项全部存在 |

---

## 版本历史
- v1.0 2026-07-20: 初始版本，基于任务A实践总结
