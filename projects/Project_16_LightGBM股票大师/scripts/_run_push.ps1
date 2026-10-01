# coding: utf-8
$pythonPath = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
if (-not (Test-Path $pythonPath)) {
    $pythonPath = "python"
}
& $pythonPath "D:\QuantLab\projects\Project_16_LightGBM股票大师\push_alert_card.py" --title "集合竞价预判 2026-09-28" --body "步骤1-5全部完成。G2候选7只，F2资金流T-4过期需注意，eastmoney_curl熔断。推送失败仅记录。" --level warn 2>&1
