---
kind: external_dependency
name: QMT 券商接口与实盘执行
slug: qmt-mini
category: external_dependency
category_hints:
    - vendor_identity
    - client_constraint
scope:
    - '**'
---

### QMT 券商接口与实盘执行
- 国金证券 QMT 交易端（miniQMT）作为实盘执行通道，通过 xtquant Python SDK 集成
- 账号 ID 固定为 67014907，路径配置在 `config/trading_config.yaml` 中
- QMT 策略生成器 `broker/qmt_builder.py` 将研究代码转换为 GBK 编码单文件策略
- QMT 模拟端只保留当日委托数据，隔日查询失败，需每日导出 CSV 到 D:/QMT_POOL/
- 安装脚本 `setup.py` 自动查找并复制 xtquant 到系统 Python 环境
- 路径约定：E:\国金QMT交易端模拟\userdata_mini 为配置文件目录