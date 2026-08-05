# coding: utf-8
"""QuantLab 因子库。

本目录在原生 QuantLab 因子（base/engine/vwap_volume_corr）之外，新增从
QMT 回测框架迁移过来的低波动策略因子（atr/volatility/roe）。这些因子是
函数式、逐股 DataFrame -> 标量，被策略直接 import，不强制继承 FactorBase。
"""
