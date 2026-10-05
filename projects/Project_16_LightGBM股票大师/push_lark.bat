REM [已废弃 2026-10-02] 本脚本全库无调用方，且硬编码 lark 插件版本目录 1.0.4 已不存在（插件已升 1.0.5），执行必然失败。
REM 请改用项目统一包装器: python lark_push.py --file ^<txt^> --user-id ou_bd13444d8ea53c28249c669f43f3eeff --as bot
REM lark-cli 稳定出口 C:\Users\Administrator\bin\lark-cli.exe（禁止硬编码 ...\lark\1.0.x\... 版本目录）
REM open_id 唯一事实源 = qmt_config.py 的 FEISHU_OPEN_ID（ou_bd134...）；旧值 ou_76de... 已废弃。
REM 保留原因：历史推送身份验证记录，勿直接调用。
@echo off
setlocal
set LARKSUITE_CLI_STRICT_MODE=off
set LARKSUITE_CLI_APP_ID=
set LARKSUITE_CLI_USER_ACCESS_TOKEN=
"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\1.0.4\bin\lark-cli.exe" im messages-send --as bot --open-id ou_bd13444d8ea53c28249c669f43f3eeff --message "【Project_16 开盘复核 2026-09-28】大盘: 沪深300 -1.24%, T=1半仓。结论: 所有10只候选评分均<58红线(最高55.5), 本日不换仓(宁缺毋滥)。原因: F2主力资金Tushare T-1滞后>2天+F3催化无数据+F5板块无实时数据。F2主源=Tushare T-1本地。策略资金池89068.77元，买入0笔/卖出0笔。"
endlocal
