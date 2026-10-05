# [已废弃 2026-10-02] 本脚本全库无调用方，且硬编码 lark 插件版本目录（1.0.5）必然随插件升级腐化（1.0.4 -> 1.0.5 已发生过一次）。
# 请改用项目统一包装器（多候选探测 + glob 兜底 + 分片 + 严格成功判定）：
#   python lark_push.py --file <txt> --user-id ou_bd13444d8ea53c28249c669f43f3eeff --as bot
#   自检：python lark_push.py --check
# lark-cli 稳定出口：C:\Users\Administrator\bin\lark-cli.exe（禁止硬编码 ...\lark\1.0.x\... 版本目录）
# open_id 唯一事实源 = qmt_config.py 的 FEISHU_OPEN_ID（ou_bd134...）；旧值 ou_76de... 已废弃。
# 下方 body 文件 data\cache\_push_body_20260929.md 为 2026-09-29 一次性快照，保留仅作历史追溯。

$ErrorActionPreference = 'Continue'
$cli = 'C:\Users\Administrator\bin\lark-cli.exe'
$projRoot = Split-Path -Parent $PSScriptRoot
$bodyFile = Join-Path $projRoot 'data\cache\_push_body_20260929.md'
$openId = 'ou_bd13444d8ea53c28249c669f43f3eeff'

if (-not (Test-Path $cli)) { Write-Output "[LARK-ERR] cli not found: $cli"; exit 2 }
if (-not (Test-Path $bodyFile)) { Write-Output "[LARK-ERR] body not found: $bodyFile"; exit 3 }

Remove-Item Env:LARKSUITE_CLI_APP_ID -ErrorAction SilentlyContinue
Remove-Item Env:LARKSUITE_CLI_USER_ACCESS_TOKEN -ErrorAction SilentlyContinue
$env:LARKSUITE_CLI_STRICT_MODE = 'off'

$md = Get-Content -Path $bodyFile -Raw -Encoding UTF8
if ([string]::IsNullOrWhiteSpace($md)) { Write-Output '[LARK-ERR] empty body'; exit 4 }

Write-Output "[LARK-INFO] bodyChars=$($md.Length)"
& $cli im +messages-send --user-id $openId --as bot --markdown $md 2>&1 | ForEach-Object { Write-Output $_ }
Write-Output "[LARK-EXIT] lastExitCode=$LASTEXITCODE"