# Project_ATR_lowvol —— ATR 低波动策略（实盘主线）

> 本目录是 ATR 低波动策略在 QuantLab 的**完整自包含工程**，进来看这一份即知全貌。
> 全局视角见仓库根 `研究总览与路线图.md`（以前/踩坑/现在/未来）。

## 一、这是什么
- **状态**：🟢 实盘（国金模拟端 `67014907`，10 万虚拟子账户锁定，不杠杆、等权前 100、季频）
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
│   └── strategy_atr_lowvol_equalweight.py  # 等权不杠杆版（当前实盘用这个，已含下单三坑修复）
├── research/                    # 手写回测原稿 + 报告生成器（研究史料，不复用进实盘）
│   ├── backtest_atr_lowvol.py / _v2.py / _v3.py   # 手写回测 v1/v2/v3（v3 被 strategy/atr_lowvol.py 框架版重写）
│   ├── gen_*_report.py (×7)     # 各版本回测 HTML 报告生成器
│   ├── bench_atr_factor.py / diag_atr.py / probe_*.py   # 因子基准/诊断/探针
│   └── tests/test_turnover_fix.py  # 换手过滤修复单测
└── README.md                    # 本文件
```

**框架版策略**（不在本目录，在仓库 `strategy/atr_lowvol.py`）：把 v3 逻辑用通用回测框架重写成 `target_weights` 模式，组合层/风控全交给 `backtest/`。回测统一走 `backtest/`。

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
