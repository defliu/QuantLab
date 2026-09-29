# Project_21 F1-F6 六族日内信号扫描框架设计

**版本**: v1.0  
**日期**: 2026-09-27  
**状态**: Phase 1 设计文档

---

## 核心约束

| 约束项 | 规范 |
|--------|------|
| 入场价格 | 信号触发根 bar 结束，**下一根 1min bar 的 open**（t+1 bar open） |
| 出场价格 | **14:50 强制平仓**（最后一根 bar close），不持仓过夜 |
| 最小成交量 | **≥ 100 股**（零股直接跳过，不计入统计） |
| 候选密度指标 | **双指标并列**：每股日均信号数 + 每百股日均信号数 |
| 参数模式 | **预注册参数**，严禁参数扫描/过拟合 |
| 统计检验 | **按交易日聚类的稳健 t-stat**（Cluster-robust t-stat at trade-day level） |

---

## 六族信号定义

### F1：集合竞价信号
**逻辑**：利用开盘集合竞价价量偏离，捕捉隔夜信息冲击后的首笔成交方向。

| 参数 | 说明 | 预注册值 |
|------|------|----------|
| `auction_threshold_pct` | 集合竞价涨跌幅阈值 | ±2.0% |
| `volume_ratio_threshold` | 竞价量比阈值（vs 过去20日均量） | ≥ 1.5 |
| `gap_confirm` | 是否要求隔夜跳空确认 | True |

**信号类型**：
- `F1_LONG`：竞价涨幅 > 阈值 + 量能放大 → 正T 买入（预期高开低走回补）
- `F1_SHORT`：竞价跌幅 > 阈值 + 量能放大 → 反T 卖出（预期低开高走回补）

**数据源**：`stock_auction_o_daily.parquet`（开盘集合竞价）、`stock_auction_c_daily.parquet`（收盘集合竞价）、1min 数据

---

### F2：早盘动量/反转信号
**逻辑**：开盘前 30 分钟（9:30-10:00）的价量行为，判断趋势延续 vs 反转。

| 参数 | 说明 | 预注册值 |
|------|------|----------|
| `momentum_window_min` | 动量计算窗口 | 15 min |
| `reversal_threshold_pct` | 反转判定阈值 | ±1.5% |
| `volume_surge_ratio` | 放量倍数 | ≥ 2.0 |

**信号类型**：
- `F2_MOM_LONG`：前15min 涨幅 > 阈值 + 放量 → 趋势延续买入
- `F2_MOM_SHORT`：前15min 跌幅 > 阈值 + 放量 → 趋势延续卖出（反T）
- `F2_REV_LONG`：前30min 大跌后企稳回升（V型） → 反转买入
- `F2_REV_SHORT`：前30min 大涨后回落（倒V型） → 反转卖出

**数据源**：1min 数据（9:30-10:00 窗口）

---

### F3：VWAP 偏离信号
**逻辑**：价格偏离 VWAP 的均值回归特性，中波股日内 VWAP 回归概率较高。

| 参数 | 说明 | 预注册值 |
|------|------|----------|
| `deviation_threshold_pct` | 偏离阈值 | ±1.0% |
| `vwap_window_min` | VWAP 计算窗口 | 全日累计（从开盘累计到当前） |
| `reentry_cooldown_min` | 重入冷却期 | 30 min |

**信号类型**：
- `F3_LONG`：价格跌破 VWAP - 阈值 → 买入期待回归
- `F3_SHORT`：价格突破 VWAP + 阈值 → 卖出期待回归（反T）

**数据源**：1min 数据，实时累计 VWAP = Σ(amount) / Σ(vol)

---

### F4：开盘 30 分钟量能结构信号
**逻辑**：开盘前 30 分钟的量能分布特征（前重/后重/均匀），判断主力意图。

| 参数 | 说明 | 预注册值 |
|------|------|----------|
| `front_heavy_ratio` | 前15min/后15min 量比阈值 | ≥ 1.5（前重）或 ≤ 0.67（后重） |
| `price_volume_corr_threshold` | 价量相关性阈值 | ≥ 0.3 |
| `min_bars` | 最小有效 bar 数 | 25 |

**信号类型**：
- `F4_FRONT_LONG`：前重放量 + 价量正相关 → 买入（主力进场）
- `F4_BACK_LONG`：后重放量 + 价格企稳 → 买入（主力护盘）
- `F4_FRONT_SHORT`：前重放量下跌 → 卖出（主力出货，反T）

**数据源**：1min 数据（9:30-10:00 窗口，分前15min/后15min）

---

### F5：隔夜缺口回补信号
**逻辑**：隔夜跳空缺口的回补概率，中波股缺口回补胜率较高。

| 参数 | 说明 | 预注册值 |
|------|------|----------|
| `gap_threshold_pct` | 缺口最小幅度 | ±1.5% |
| `fill_window_min` | 回补观察窗口 | 60 min（10:30 前） |
| `partial_fill_ratio` | 部分回补比例 | ≥ 50% |

**信号类型**：
- `F5_GAP_UP_FILL`：向上跳空 > 阈值，开盘回落回补 ≥ 50% → 反T 卖出（预期继续回补）
- `F5_GAP_DOWN_FILL`：向下跳空 > 阈值，开盘回升回补 ≥ 50% → 正T 买入（预期继续回补）

**数据源**：1min 数据 + 前日收盘价 + 当日开盘价

---

### F6：日内脉冲回归信号（5min z-score 触发）
**逻辑**：5 分钟频度下的价格标准化异动（z-score），捕捉超买超卖瞬时机会。

| 参数 | 说明 | 预注册值 |
|------|------|----------|
| `zscore_threshold` | z-score 触发阈值 | ±2.0 |
| `lookback_bars` | 回望窗口（5min bar 数） | 48（约 4 小时） |
| `min_holding_bars` | 最小持仓根数 | 3（15 分钟） |

**信号类型**：
- `F6_LONG`：5min 收盘价 z-score < -2.0 → 买入（超卖反弹）
- `F6_SHORT`：5min 收盘价 z-score > +2.0 → 卖出（超买回落，反T）

**数据源**：5min 数据（d:/astock/min_5min/），或由 1min 重采样生成

---

## 扫描框架架构

```
┌─────────────────────────────────────────────────────────────┐
│                    SignalScanner (主扫描器)                   │
├─────────────────────────────────────────────────────────────┤
│  1. 加载中波股票池 (midvol_criteria.yaml)                     │
│  2. 并行加载 1min/5min/auction 数据                           │
│  3. 按交易日分组，逐日扫描                                    │
│  4. 对每只股票每日运行 F1-F6 信号计算器                       │
│  5. 输出信号事件表：ts_code, trade_date, signal_family,      │
│     signal_type, trigger_time, trigger_price,                │
│     entry_price (t+1 open), exit_price (14:50 close),        │
│     pnl_bps, shares                                          │
│  6. 汇总统计：候选密度、聚类 t-stat、年度表现、极端日稳健性    │
└─────────────────────────────────────────────────────────────┘
```

### 信号计算器基类
```python
class SignalBase(ABC):
    @abstractmethod
    def compute_signals(self, df_1min: pd.DataFrame, df_5min: pd.DataFrame, 
                        df_auction: pd.DataFrame, context: Dict) -> List[SignalEvent]:
        """返回该族信号事件列表"""
        pass
    
    def validate_entry(self, signal: SignalEvent, df_1min: pd.DataFrame) -> bool:
        """防前视验证：入场价必须是 t+1 bar open"""
        pass
```

### 输出数据结构
```python
@dataclass
class SignalEvent:
    ts_code: str
    trade_date: str          # YYYYMMDD
    family: str              # F1-F6
    signal_type: str         # LONG/SHORT + 细分类型
    trigger_bar_idx: int     # 触发时的 1min bar 索引
    trigger_time: str        # HH:MM
    trigger_price: float     # 触发时价格（仅作参考，不入场）
    entry_price: float       # 入场价 = t+1 bar open
    exit_price: float        # 出场价 = 14:50 bar close
    direction: int           # +1 买入(正T) / -1 卖出(反T)
    shares: int              # 预估成交股数（按流动性估算，<100 跳过）
    pnl_bps: float           # 单笔收益 bps（含成本）
    cost_bps: float          # 单笔成本 bps
```

---

## 评估指标体系

### 1. 候选密度（双指标并列）
| 指标 | 计算 | 合格线 |
|------|------|--------|
| 每股日均信号数 | Σ(该股全期信号数) / 交易日数 | ≥ 0.5 |
| 每百股日均信号数 | Σ(全池单日信号数) / 交易日数 / 100 * 100 | ≥ 5 |

### 2. 统计显著性
- **Cluster-robust t-stat**：按 trade_date 聚类，计算日均超额收益的 t-stat
- 要求：t-stat ≥ 2.0

### 3. 年度表现分解
| 年份 | 交易日数 | 信号数 | 胜率 | 平均收益 | 夏普 | 最大回撤 |
|------|----------|--------|------|----------|------|----------|
| 2019 | - | - | - | - | - | - |
| 2020 | - | - | - | - | - | - |
| 2021 | - | - | - | - | - | - |
| 2022 | - | - | - | - | - | - |
| 2023 | - | - | - | - | - | - |
| 2024 | - | - | - | - | - | - |

### 4. 极端日稳健性
- 单日最大亏损 ≤ -200bps 的天数占比
- 连续亏损天数分布
- 压力测试：2020/2022 熊市、2024 低波行情

---

## 伪造标准（预注册，不可事后修改）

> **⚠️ 2026-09-28 起本节已被取代**：证伪线唯一事实源 = `specs/证伪线预注册锁定_20260928.md`（+ `config/falsification_criteria.yaml`，诚哥 2026-09-28 拍板：方案 r3 建议线全采纳）。
> 差异：单族「t < 1.5 / 年化夏普 < 0.5」作废 → 改为「净期望>0 且毛期望≥0.3% + 聚类 t≥2.0 或 bootstrap CI 下界>0 + 分年>-0.5% + 极端日同向 + 密度每股≥1.0」；密度「0.3 / 3」作废；合并六族 t≥2.0 与夏普≥1.0 保留（进 M1 门）；极端日 300bps、容量两行不作过线依据。
> 本节保留原文仅作历史对照，**裁决不得引用本节**。

| 伪造条件 | 判定标准 |
|----------|----------|
| F1-F6 任一族 | Cluster t-stat < 1.5 或 年化夏普 < 0.5 |
| 合并六族 | Cluster t-stat < 2.0 或 年化夏普 < 1.0 |
| 候选密度 | 每股日均 < 0.3 或 每百股日均 < 3 |
| 极端日 | 单日亏损 > 300bps 超过 3 次/年 |
| 容量 | 5000万资金下年化收益 < 5% |

---

## 实现路线图

| 阶段 | 任务 | 产出物 |
|------|------|--------|
| Phase 1a | 实现 F1 集合竞价信号计算器 | `signals/f1_auction.py` |
| Phase 1b | 实现 F2 早盘动量/反转计算器 | `signals/f2_momentum_reversal.py` |
| Phase 1c | 实现 F3 VWAP 偏离计算器 | `signals/f3_vwap_dev.py` |
| Phase 1d | 实现 F4 量能结构计算器 | `signals/f4_volume_structure.py` |
| Phase 1e | 实现 F5 缺口回补计算器 | `signals/f5_gap_fill.py` |
| Phase 1f | 实现 F6 脉冲回归计算器 | `signals/f6_pulse_regression.py` |
| Phase 1g | 主扫描器 + 批量跑 2019-2024 | `scanner.py` + `results/signal_scan_2019_2024.parquet` |
| Phase 1h | 统计报告生成 | `results/M1_decision_report.md` |

---

## 里程碑 M1 决策标准

> **2026-09-28 更新**：以下五条中的单族阈值与密度阈值已由 `specs/证伪线预注册锁定_20260928.md` 取代；「至少 3 族通过」「合并六族 t≥2.0 / 夏普≥1.0」「2024 不转负」三条仍为 Phase 2 进入门（见该文 §二）。密度过线改为「每股日均 ≥ 1.0」。

**通过进入 Phase 2（分钟级回测引擎）的条件**：
- [ ] 至少 3 个族通过单族伪造线
- [ ] 合并六族 Cluster t-stat ≥ 2.0
- [ ] 年化夏普 ≥ 1.0（扣全成本后）
- [ ] 候选密度双指标均达标
- [ ] 2024 年样本外不转负（或亏损可解释）

**不通过则**：
- 记录失败原因，归档 Phase 1 成果
- 不进入 Phase 2/3，避免沉没成本