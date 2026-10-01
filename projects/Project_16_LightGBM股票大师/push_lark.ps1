Remove-Item Env:\LARKSUITE_CLI_APP_ID -ErrorAction SilentlyContinue
Remove-Item Env:\LARKSUITE_CLI_USER_ACCESS_TOKEN -ErrorAction SilentlyContinue
$env:LARKSUITE_CLI_STRICT_MODE="off"
& "C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\1.0.4\bin\lark-cli.exe" im messages-send --as bot --open-id ou_bd13444d8ea53c28249c669f43f3eeff --message "Project_16 开盘复核 2026-09-28: 沪深300 -1.24%, T=1半仓。所有10只候选评分均<58红线(最高55.5), 本日不换仓(宁缺毋滥)。原因: F2主力资金Tushare T-1滞后>2天+F3催化无数据+F5板块无实时数据。F2主源=Tushare T-1本地。策略资金池89068.77元，买入0笔/卖出0笔。"
