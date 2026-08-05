---
kind: external_dependency
name: Tushare 金融数据接口
slug: tushare
category: external_dependency
category_hints:
    - vendor_identity
scope:
    - '**'
---

### Tushare 金融数据接口
- A 股数据源，提供日线、财务等金融数据
- 数据格式为 parquet 文件，路径 E:/astock/daily/stock_daily.parquet
- 包含 ts_code 格式的股票代码（如 000001.SZ）
- 数据从 2009 年开始，包含完整的财务指标和 PIT 字段
- 作为 astock 数据源的原始提供者，被 astock_reader.py 封装使用