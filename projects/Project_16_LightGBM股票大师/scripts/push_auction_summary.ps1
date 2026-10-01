$ErrorActionPreference = 'Continue'
$cli = 'C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\1.0.5\bin\lark-cli.exe'
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
