---
kind: external_dependency
name: DuckDB 本地数据库引擎
slug: duckdb
category: external_dependency
category_hints:
    - framework_behavior
scope:
    - '**'
---

### DuckDB 本地数据库引擎
- 高性能列式内存数据库，用于存储 A 股历史数据
- 支持两种 schema 模式：jince_zhisuan（v0.2）和 qmt_self_owned（v0.3 主路径）
- 默认按 source='xtquant' 过滤，adjustment='hfq' 后复权数据
- WAL 检测机制防止数据同步期间的不一致读取
- 只读模式访问，不写操作，确保数据安全
- 统一输出格式：date, open, high, low, close, vol, amount
- 与 astock parquet 形成双数据源架构，通过 DataFeed 统一接口访问