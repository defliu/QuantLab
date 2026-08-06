# coding=utf-8
"""组件 A: 退市排雷增强 —— V2a 池口径下历史命中统计
讨论室 Round 1 裁决执行 (2026-08-06, 诚哥批准 C->A->B)

目的:
  量化 v2.2 SPEC 退市排雷口径对 V2a 股票池的实际影响 (每期排除多少只、
  是否改变 TOP80 构成), 决定走"直接增强"还是"升级全量回测复核"。
  升级判据 (讨论室设计): 单期改变 TOP80 构成 >= 3 只 -> 需全量回测复核。

新增排雷规则 (限 E:/astock 可得字段, 无数据源的立案/非标/解禁不做):
  R1 市值红线缓冲区: 主板 total_mv < 5亿x1.5=7.5亿; 创业板/科创板 < 3亿x1.5=4.5亿
     (主板5亿为2024-10-30起现行标准; 北交所无对应标准, 不适用本规则, 单列)
  R2 退市临近: basic.delist_date 已知且调仓日距退市日 <= 30 天

口径 (与 runner.py / V2a 完全一致):
  池 = circ_mv in (0, 30亿) & pe_ttm>0 & pb>0 & 非ST & 非停牌
  质量排雷 (PIT, ann_date<=调仓日的最新一期): bps>0 & roe>0 & profit_dedt>0
  排序代理 = 原始BP排名 (V2a 实际为行业中性z, 边界±2只内可能偏差, 命中<=2只时风险小)
  调仓日 = 每月首个交易日 (V2a 实际双月, 月频采样为超集, 更保守)

输出: results/a_delisting_screen_hits.csv + 控制台摘要
"""
import duckdb
import pandas as pd
import os

HERE = os.path.dirname(os.path.abspath(os.path.dirname(__file__)))
RES = os.path.join(HERE, "results")
os.makedirs(RES, exist_ok=True)

DAILY = "E:/astock/daily/stock_daily.parquet"
FIN = "E:/astock/finance/fina_indicator.parquet"
BASIC = "E:/astock/basic/stock_basic.parquet"

MV_MAIN = 75000.0     # 万元: 主板 5亿 x 1.5
MV_GEM_STAR = 45000.0  # 万元: 创业板/科创板 3亿 x 1.5

con = duckdb.connect()

sql = f"""
WITH cal AS (
    SELECT min(trade_date) AS d
    FROM read_parquet('{DAILY}')
    WHERE trade_date >= '2018-01-01' AND trade_date <= '2026-07-31'
    GROUP BY strftime(trade_date, '%Y-%m')
),
cand AS (
    SELECT dd.trade_date AS d, dd.ts_code,
           dd.pb, dd.circ_mv, dd.total_mv
    FROM read_parquet('{DAILY}') dd
    SEMI JOIN cal ON dd.trade_date = cal.d
    WHERE dd.circ_mv > 0 AND dd.circ_mv < 300000
      AND dd.pe_ttm > 0 AND dd.pb > 0
      AND (dd.is_st IS NULL OR dd.is_st = 0)
      AND (dd.suspend_type IS NULL OR dd.suspend_type NOT IN ('S','R','R&S'))
),
fin AS (
    SELECT ts_code, strptime(ann_date, '%Y%m%d')::DATE AS ann_dt,
           bps, roe, profit_dedt
    FROM read_parquet('{FIN}')
    WHERE ann_date IS NOT NULL AND length(ann_date) = 8
),
snap AS (
    SELECT c.*, f.bps, f.roe, f.profit_dedt
    FROM cand c
    ASOF JOIN fin f ON c.ts_code = f.ts_code AND c.d >= f.ann_dt
),
quality AS (
    SELECT * FROM snap
    WHERE bps > 0 AND roe > 0 AND profit_dedt > 0
),
basic AS (
    SELECT ts_code,
           TRY_CAST(delist_date AS VARCHAR) AS delist_date
    FROM read_parquet('{BASIC}')
),
tagged AS (
    SELECT q.d, q.ts_code, q.pb, q.total_mv,
           CASE WHEN q.ts_code LIKE '%.BJ' THEN 'BJ'
                WHEN q.ts_code LIKE '30%' OR q.ts_code LIKE '688%' THEN 'GEM_STAR'
                ELSE 'MAIN' END AS board,
           CASE WHEN q.ts_code LIKE '%.BJ' THEN FALSE
                WHEN (q.ts_code LIKE '30%' OR q.ts_code LIKE '688%')
                     AND q.total_mv < {MV_GEM_STAR} THEN TRUE
                WHEN NOT (q.ts_code LIKE '30%' OR q.ts_code LIKE '688%')
                     AND q.total_mv < {MV_MAIN} THEN TRUE
                ELSE FALSE END AS hit_r1_mv,
           CASE WHEN b.delist_date IS NOT NULL AND length(b.delist_date) = 8
                     AND q.d >= strptime(b.delist_date, '%Y%m%d')::DATE - INTERVAL 30 DAY
                THEN TRUE ELSE FALSE END AS hit_r2_delist
    FROM quality q
    LEFT JOIN basic b ON q.ts_code = b.ts_code
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY d ORDER BY pb ASC) AS bp_rank
    FROM tagged
)
SELECT d, ts_code, board, total_mv, bp_rank, hit_r1_mv, hit_r2_delist
FROM ranked
WHERE hit_r1_mv OR hit_r2_delist
ORDER BY d, bp_rank
"""

print("执行命中统计...", flush=True)
hits = con.execute(sql).fetchdf()

# 每期池规模 (用于占比)
pool_n = con.execute(f"""
WITH cal AS (
    SELECT min(trade_date) AS d
    FROM read_parquet('{DAILY}')
    WHERE trade_date >= '2018-01-01' AND trade_date <= '2026-07-31'
    GROUP BY strftime(trade_date, '%Y-%m')
)
SELECT dd.trade_date AS d, count(*) AS n_pool
FROM read_parquet('{DAILY}') dd
SEMI JOIN cal ON dd.trade_date = cal.d
WHERE dd.circ_mv > 0 AND dd.circ_mv < 300000
  AND dd.pe_ttm > 0 AND dd.pb > 0
  AND (dd.is_st IS NULL OR dd.is_st = 0)
  AND (dd.suspend_type IS NULL OR dd.suspend_type NOT IN ('S','R','R&S'))
GROUP BY dd.trade_date
""").fetchdf()

hits["hit_top80"] = hits["bp_rank"] <= 80
per = hits.groupby("d").agg(
    n_hits=("ts_code", "count"),
    n_hit_top80=("hit_top80", "sum"),
).reset_index()
per = per.merge(pool_n, on="d", how="left")

per.to_csv(os.path.join(RES, "a_delisting_screen_hits_bydate.csv"), index=False, encoding="utf-8")
hits.to_csv(os.path.join(RES, "a_delisting_screen_hits.csv"), index=False, encoding="utf-8")

print("\n============ 组件A: 退市排雷命中统计 ============")
print("总期数:", len(pool_n), " 有命中期数:", len(per))
print("累计命中:", len(hits), "只次  其中TOP80内:", int(hits['hit_top80'].sum()), "只次")
print("命中规则分布: R1市值红线 =", int(hits['hit_r1_mv'].sum()),
      " R2退市临近 =", int(hits['hit_r2_delist'].sum()))
print("\n单期命中最多 TOP5:")
print(per.nlargest(5, "n_hits").to_string(index=False))
print("\nTOP80命中最多 TOP5:")
print(per.nlargest(5, "n_hit_top80").to_string(index=False))
esc = per[per["n_hit_top80"] >= 3]
print("\n触发升级判据(单期TOP80命中>=3)的期数:", len(esc))
if len(esc) > 0:
    print(esc.to_string(index=False))
print("\n按年度命中汇总:")
hits["year"] = hits["d"].dt.year
print(hits.groupby("year").agg(n=("ts_code", "count"),
                               top80=("hit_top80", "sum")).to_string())
print("\n结果已写入:", RES)
