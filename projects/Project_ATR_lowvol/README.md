# Project_ATR_lowvol —— ATR 低波动策略（实盘主线）

> 本目录是 ATR 低波动策略在 QuantLab 的**完整自包含工程**，进来看这一份即知全貌。
> 全局视角见仓库根 `研究总览与路线图.md`（以前/踩坑/现在/未来）。

## 一、这是什么
- **状态**：🟢 实盘（国金模拟端 `67014907`，10 万虚拟子账户锁定，不杠杆、**8 只等权 + 真实价<50 + MAX5 彩票过滤 + 季频**）
- **核心逻辑**：合格域(ATR%<6 & 换手 1-8% & 非 ST & 上市≥60 日) 按 ATR% 最低取前 N 只；动量**门控**(剔除近期输家)免费增益，动量/价值**排序加权**为负贡献。
- **实盘表现**：状态机口径年化 18.2%、超额 +13.9%、回撤 -17.7%；框架验证口径年化 ~18.7%。
- **冲 15%+ 路径**：季频 + 质量 + 动量门控 + VT + IC + 1.5x 两融（框架已验证 +23.4%/夏普 0.955/回撤 -37%），须解决整数手瓶颈（减到 30~50 只 + vol-parity）。

## 二、目录结构与各自用途（重要：改哪改哪）
```
Project_ATR_lowvol/
├── src/                         # ★ 实盘源码（你每天本地验证的纯函数在这里）
│   ├── strategy_atr.py          #   实盘纯函数源（42KB）：is_last_bar 守卫 / 真实换手过滤 / 选股 + 下单入口
│   └── _strategy_atr_local.py   #   本地 miniQMT 验证运行器（~10 秒出结果，免部署）
├── build/                       # QMT 部署构件（GBK 单文件，由 src 构建而来，QMT 直接加载）
│   ├── strategy_atr_lowvol.py           # 标准尾盘版
│   ├── strategy_atr_lowvol_allday.py    # 全天调试版
│   └── strategy_atr_lowvol_equalweight.py  # 等权不杠杆版（当前实盘用这个，已含下单三坑修复+R9 ROE空结果fail-open）
├── config/                      # 10万/8只+价<50 回测配置（2026-08-15 从 Project_12 迁入统一）
│   ├── atr_10w_price50.yaml             # 部署基线（10万/8只/等权/无杠杆/真实价<50/2019-2026，总+208.8%/CAGR 16.95%/-24.80%/卡玛(CAGR) 0.68）
│   ├── atr_10w_8.yaml / atr_100w_100.yaml        # 基线对照
│   ├── atr_10w_price50_a_max.yaml       # ✅ MAX5 彩票过滤 0.20（首个全维度正优化：总+237.6%/CAGR 18.40%/-20.05%/卡玛(CAGR) 0.92；2026-08-14 已入 build 部署口径）
│   └── atr_10w_price50_{aplus_mom,aplus_momval,e_buffer,ma200_exit,ma200_hold}.yaml  # 已证伪方向留档
├── research/                    # 手写回测原稿 + 报告生成器 + 10万研究脚本（研究史料，不复用进实盘）
│   ├── backtest_atr_lowvol.py / _v2.py / _v3.py   # 手写回测 v1/v2/v3（v3 被 strategy/atr_lowvol.py 框架版重写）
│   ├── gen_*_report.py (×7)     # 各版本回测 HTML 报告生成器
│   ├── bench_atr_factor.py / diag_atr.py / probe_*.py   # 因子基准/诊断/探针
│   ├── scan/report_10w_price50_voltarget.py / gen_voltarget_dashboard.py  # VT 扫描+看板（已证伪，留档）
│   ├── analyze_atr_10w.py / compare_10w_*.py / ml_atr_hybrid.py           # 10万对比/ML-ATR
│   └── tests/                   # 单测（test_turnover_fix.py / test_roe_r9_failopen.py）
├── results/                     # 回测报告（10万价格过滤/真钱版/VT扫描/大盘门控/三方向优化/文献调研 + 看板HTML）
└── README.md                    # 本文件
```

**框架版策略**（不在本目录，在仓库 `strategy/atr_lowvol.py`）：把 v3 逻辑用通用回测框架重写成 `target_weights` 模式，组合层/风控全交给 `backtest/`。回测统一走 `backtest/`（`python -m scripts.run_backtest --config projects/Project_ATR_lowvol/config/atr_10w_price50.yaml`）。

> **2026-08-15 结构统一**：10万/8只+价<50 及近两日全部优化研究（VT扫描/MA200门控/三方向优化）已从 `Project_12_RPS主升浪` 迁入本目录 `config/`+`research/`+`results/`，ATR 研究全资产现集中于此（详见看板 T-20260815-006）。

## 三、日常开发闭环（照这个走）
1. 改策略逻辑 → 编辑 `src/strategy_atr.py`（纯函数，零 QMT 依赖）。
2. **本地验证（改完必跑，~10 秒）**：用 miniQMT venv 跑 `src/_strategy_atr_local.py`，连本地 miniQMT 实时数据，核对选股管线/候选数量/排序/fail-open。
   ```
   C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe src/_strategy_atr_local.py
   ```
3. 只有验证 `is_last_bar` 守卫 / 真实换手过滤 / 实盘下单时才重建 `build/` 并部署远程国金端（一天一次）。
4. 实盘下单**一律走 `broker/qmt_order.py`**（防坑版），禁止裸调 `passorder`。

## 四、致命踩坑（已固化进 broker 层，勿重蹈）
1. **`passorder` 价格↔股数颠倒** → 废单。第 6 位是价、第 7 位是量；`passorder` 是全局函数，第 8 位传 `C`。
2. **miniQMT 无 `get_trade_detail_data`** → 反查失败误删 ledger。用 `lookup_order()` 乐观确认兜底。
3. **买入 pending 超时误删 ledger** → 乐观模式超时只清 pending、保留持仓。
详情见 `broker/QMT委托买卖防坑指南.md`。

## 五、资金与约束
- 本策略锁定 10 万虚拟子账户，**只动自己 ledger，绝不抢占他人资金/持仓**。
- 多策略资金分配唯一事实源：`D:/QuantLab/config/capital_allocation.yaml`；改后跑 `D:/QuantLab/scripts/check_capital_allocation.py`（EXIT 0 才许部署）。
- 账户 `total_capital` 待在国金 QMT 客户端查「总资产」填入。
