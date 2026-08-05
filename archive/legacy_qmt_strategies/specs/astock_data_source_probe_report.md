# 探查报告: astock 增量更新数据源对齐

> 作者: CC
> 日期: 2026-06-28
> 对应 SPEC: `specs/astock_daily_update_SPEC.md`
> 方法: py -3.10 实跑 xtquant / mootdx / akshare, 与 astock parquet 同日同股逐值对比
> 基准样本: 000001.SZ @ 2026-06-18 (astock 已有最后交易日)

---

## 一、结论速览

原 SPEC 第5节"QMT xtdata 一行拿 adjFactor + pe/pb/市值/股本"**方案前提错误**, 实测不成立。已重定数据源分工, 见第三节。两处单位雷 (amount 千元 / vol freq 锁定) 必须在编码前固化。

---

## 二、三方同日对比 (000001.SZ @ 2026-06-18)

| 字段 | astock | QMT `get_market_data_ex` | mootdx freq=9 | mootdx freq=4 | akshare |
|:--|:--|:--|:--|:--|:--|
| close | 10.52 | 10.52 | 10.52 | 10.52 | 10.52 |
| open | 10.74 | 10.74 | 10.74 | 10.74 | 10.74 |
| vol | 1426893.16 (float, 股) | 1426893 (股) | 1426893 (股) | **142689312 (手×100)** | 142689316 (手, ÷100=股) |
| amount | **1.511e+06 (千元)** | 1.511e+09 (元) | 1.511e+09 (元) | 1.511e+09 (元) | 1.511e+09 (元, ÷1000=千元) |
| pre_close | 10.78 | 10.78 (preClose) | — | — | — |
| adj_factor | 139.008 | ❌ 不返回 | ❌ | ❌ | ❌ |
| pe | 4.7886 | ❌ | ❌ | ❌ | stock_value_em PE(静)=4.78 |
| pe_ttm | (有列) | ❌ | ❌ | ❌ | stock_value_em PE(TTM)=4.74 |
| pb | 0.4399 | ❌ | ❌ | ❌ | stock_value_em 市净率=0.44 |
| total_mv | 2.0415e+07 (万元) | ❌ | ❌ | ❌ | stock_value_em 总市值=2.04e11 (元, ÷1e4=万元) |
| total_share | (有列, 股) | ❌ | ❌ | ❌ | stock_value_em 总股本=1.94e10 (股) |
| turnover_rate | 0.7353 (%) | ❌ | ❌ | ❌ | stock_zh_a_daily turnover=0.007353 (小数, ×100=%) |
| up_limit | 11.86 | ❌ (detail 给当日截面 11.46) | ❌ | ❌ | — |

---

## 三、修订后数据源分工 (诚哥 2026-06-28 拍板)

```
OHLCV + amount + pre_close  → mootdx freq=9 (主) / akshare stock_zh_a_daily (降级)
adj_factor                  → 当天沿用旧值, 次日补 (不自算, dr累乘对齐已验证失败)
pe/pe_ttm/pb/ps             → akshare stock_value_em(symbol)  [历史日频, 覆盖到最新]
total_mv/circ_mv            → akshare stock_value_em  (÷1e4 转万元)
total_share/float_share     → akshare stock_value_em  (股, 直接)
turnover_rate               → akshare stock_zh_a_daily turnover (×100 转%)
up_limit/down_limit         → QMT get_instrument_detail UpStopPrice/DownStopPrice (当日截面)
is_st                       → QMT instrument_detail / akshare (待定, 次要)
change/pct_chg              → 自算: close - pre_close / (close-pre_close)/pre_close*100
```

### 不补/留空的次要列 (本轮不苛求)
`volume_ratio`(量比), `ps_ttm`, `dv_ratio`/`dv_ttm`(股息率), `free_share`(自由流通股本), `suspend_timing`
→ 当天沿用旧值或留空, 不阻断。后续可单开通道补。

---

## 四、单位转换表 (编码必须逐条遵守)

| astock 列 | 数据源 | 源单位 | astock 单位 | 转换 |
|:--|:--|:--|:--|:--|
| `vol` | mootdx freq=9 | 股 | 股 | 直接 (float) |
| `amount` | mootdx | 元 | **千元** | **÷1000** |
| `pre_close` | QMT get_market_data_ex.preClose | 元 | 元 | 直接 |
| `total_mv` | akshare stock_value_em.总市值 | 元 | 万元 | **÷10000** |
| `circ_mv` | akshare stock_value_em.流通市值 | 元 | 万元 | **÷10000** |
| `total_share` | akshare stock_value_em.总股本 | 股 | 股 | 直接 |
| `float_share` | akshare stock_value_em.流通股本 | 股 | 股 | 直接 |
| `pe` | akshare stock_value_em.PE(静) | — | — | 直接 |
| `pe_ttm` | akshare stock_value_em.PE(TTM) | — | — | 直接 |
| `pb` | akshare stock_value_em.市净率 | — | — | 直接 |
| `ps` | akshare stock_value_em.市销率 | — | — | 直接 |
| `turnover_rate` | akshare stock_zh_a_daily.turnover | 小数 | % | **×100** |
| `up_limit` | QMT instrument_detail.UpStopPrice | 元 | 元 | 直接 |
| `down_limit` | QMT instrument_detail.DownStopPrice | 元 | 元 | 直接 |
| `adj_factor` | 沿用该股上一交易日值 | — | — | 次日补 |
| `change` | 自算 close-pre_close | 元 | 元 | 直接 |
| `pct_chg` | 自算 (close-pre_close)/pre_close*100 | % | % | 直接 |

---

## 五、阻断性发现详记

### 5.1 get_market_data_ex 不返回衍生字段
默认全字段 (field_list=[]) 只返回 11 列: `time, open, high, low, close, volume, amount, settelementPrice, openInterest, preClose, suspendFlag`。
请求 `adjFactor/pe/pe_ttm/pb/totalShares/circulatingShares/freeShares/turnover/upLimit/downLimit/isST/changeRatio/totalMV/circMV` **全部不返回** (静默丢弃, 不报错)。
plan 中"✅ 此方式已在项目中验证"系夸大 — `build_core_universe.py:140` 实际只取过 `["time","close","amount"]`。

### 5.2 adj_factor 自算对齐失败
QMT `get_divid_factors(code)` 返回每次除权的 `dr` 列 (单次复权因子)。
累乘 `dr.cumprod()` 末值 = **107.65**, astock 同日 adj_factor = **139.008**, 对不上 (差 1.29 倍)。
tushare 口径 adj_factor 基准/累积方式与 QMT dr 非简单累乘关系, 精确对齐需大量验证 — 正是 plan 当初废弃自算的原因。**结论: 不自算, 当天沿用旧值次日补。**

### 5.3 amount 单位雷
astock amount = 千元, QMT/mootdx/akshare = 元。原 SPEC 4.4 写"amount → float64 (元)"**错误**, 会差 1000 倍。必须 ÷1000。

### 5.4 mootdx frequency 必须锁 freq=9
- freq=9: 日线, vol 单位=股 (与 astock/QMT 一致) ✅
- freq=4: 日线, vol 单位=手 (×100) ❌
- 两者 close 一致但 vol 差 100 倍, 不同 freq 走不同协议分支返回不同单位。**锁死 freq=9。**

### 5.5 mootdx start/count 语义诡异
`count` 不控制返回行数 (始终返回 ~800 行), `start` 控制偏移。
`start=0, count=5` 实测返回 **2023 年**老数据; `start=2, count=8` 返回 2026 近期。
原 SPEC 4.2 步骤4 `client.bars(symbol, frequency=9, start=0, count=1)` **拿不到当日**, 改为"取最近 N 根后按 datetime 筛最后一行"。

### 5.6 QMT instrument_detail 是当日截面
`get_instrument_detail(code)` 返回 PreClose/UpStopPrice/DownStopPrice/FloatVolume/TotalVolume, 但都是**最新值** (非历史序列)。只能补当天涨跌停/股本/昨收, 补不了历史。涨跌停历史值无源 → 历史回补时 up_limit/down_limit 沿用旧值。

---

## 六、akshare 接口实测

### 6.1 stock_value_em(symbol) — 历史日频估值 ✅
- 签名: `(symbol='300766') -> DataFrame`
- 列: `数据日期, 当日收盘价, 当日涨跌幅, 总市值, 流通市值, 总股本, 流通股本, PE(TTM), PE(静), 市净率, PEG值, 市现率, 市销率`
- 覆盖: 历史到最新 (000001 到 2026-06-26)
- 用途: pe/pe_ttm/pb/ps/total_mv/circ_mv/total_share/float_share 全部从此取

### 6.2 stock_zh_a_daily(symbol, adjust='') — 日线 OHLCV+换手 ✅
- 列: `date, open, high, low, close, volume, amount, outstanding_share, turnover`
- 单位: volume=手(÷100=股), amount=元(÷1000=千元), turnover=小数(×100=%), outstanding_share=股
- 用途: mootdx 降级源 + turnover_rate 来源

### 6.3 stock_zh_a_spot_em() — 全市场实时截面 (仅当天)
- 含 市盈率-动态/市净率/总市值/流通市值/换手率/量比
- 慢 (~60s 全市场), 仅截面, 不补历史。本轮不用, 备查。

### 6.4 已失效接口
`stock_a_lg_indicator` / `stock_a_indicator_lg` 均已从 akshare 移除, 不要引用。

---

## 七、astock 现状 (实测)
- max trade_date = **2026-06-18** (plan 写 06-26 不符)
- distinct ts_code = **5793** (plan/SPEC 写 5518 不符, README 写 5793 对)
- daily parquet = 1.3GB, MultiIndex (trade_date, ts_code)

---

## 八、环境
- `py -3.10` (Python 3.10.11) 可用, xtquant/mootdx 0.11.7/akshare 1.18.21/pandas 2.3.3/pyarrow 24.0.0/duckdb 齐全
- QMT 模拟端在线 (xtdata.connect 成功, 127.0.0.1:58610)
- 数据路径: `E:\国金QMT交易端模拟\bin.x64\..\userdata_mini\datadir`

---

## 九、待诚哥确认后进入编码
本报告对齐结论 + 诚哥拍板 (adj_factor 沿用旧值次日补 / 衍生字段 akshare 补) 已落定, SPEC 将据此修订。修订后派 MIMO 编码工单。
