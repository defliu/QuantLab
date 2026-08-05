# coding: utf-8
"""QuantLab 策略库。

框架原生策略（target_weights 即插即跑）：
  - atr_lowvol      ATR 低波动选股（equal/vol_parity + 行业cap + vol_target + 两融杠杆）
  - template_lowvol 最小范例模板（按波动率最低选股，演示即插即跑）

策略用 @register_strategy("<name>") 注册 evaluate_day；registry 模块导入时
自动扫描本包触发注册。runner 通过 strategy_name 选取。
"""
