<#
.SYNOPSIS
  Claude Code 只读审计包装器：单实例、双重超时、任务书与结果全部落盘。

.DESCRIPTION
  设计依据（2026-10-27 实测，Claude Code 2.1.311 + 本机网关后端）：
  1) --permission-mode plan 会长时间挂起（同一句 7s vs >150s），改用 --allowedTools 做只读约束；
  2) 非交互模式必须关闭 stdin，否则永久等待 EOF；
  3) --output-format json 会挂起，固定 text；
  4) 任务书经 bash 的 "$(cat 文件)" 传入，避免中文经 Windows 命令行编码损坏（与 DE 委派同一教训）；
  5) Git Bash 拉起的 claude 是孙进程，taskkill /T 杀不到，故超时由 bash 内部看门狗兜底，PS 侧再做一次进程清理；
  6) 本机 settings.json 的默认模型别名在本网关会挂死（同一句 >10min 无输出），必须显式 --model。
     默认值见 param 块的 -Model（2026-10-27 实测可用的网关模型：同一句 6s，带 Read 工具 11s）；
     换网关注销/改模型时用 -Model 覆盖，可用值见 ~/.claude/projects/*/*.jsonl 的 requestedModel 字段。

  默认只放行 Read/Grep/Glob（纯只读文件工具）；加 -AllowShell 才放行 PowerShell（用于 git log 等只读命令）。
  本脚本不修改仓库；需要改代码请走 git worktree + 人工复核。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\claude_audit.ps1 -Brief logs\claude_audit\brief.md

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\claude_audit.ps1 -Brief brief.md -AllowShell -TimeoutMin 20
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Brief,
    [string]$OutDir = '',
    [int]$MaxTurns = 200,
    [int]$TimeoutMin = 30,
    # 2026-10-31 实测：commandcode-plan/zai-org/GLM-5.3 与 Qwen3.8-Max-0902 都能跑，
    # 二者均为 1M 上下文（旧默认 codegenie/GLM-5.3 只有 170K，大审计必然反复压缩丢证据）。
    # 审计结论的完整性取决于能同时装下多少日志/测试输出，故默认取 1M 档。
    [string]$Model = 'commandcode-plan/zai-org/GLM-5.3',
    # Reasoning effort：CLI 不设时是默认档，桌面版内嵌实例显式用 high。
    # 审计是「逐条核证据」，深度不够会漏死代码/空断言这类隐蔽缺陷，故显式拉满。
    [ValidateSet('low', 'medium', 'high', 'xhigh', 'max')][string]$Effort = 'high',
    [switch]$AllowShell,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($OutDir)) { $OutDir = Join-Path $repo 'logs\claude_audit' }

function Stop-With([int]$code, [string]$msg) {
    Write-Host "[claude_audit] $msg" -ForegroundColor Red
    exit $code
}

function Read-TextSafe([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return '' }
    for ($i = 0; $i -lt 15; $i++) {
        try { return [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8) } catch { Start-Sleep -Milliseconds 400 }
    }
    return ''
}

# 1) 任务书
if (-not [IO.Path]::IsPathRooted($Brief)) { $Brief = Join-Path $repo $Brief }
if (-not (Test-Path -LiteralPath $Brief)) { Stop-With 2 "任务书不存在: $Brief" }

# 2) Git Bash
$bash = $env:CLAUDE_BASH
if ($bash -and -not (Test-Path -LiteralPath $bash)) { $bash = $null }
if (-not $bash) {
    foreach ($c in @('D:\Program Files\Git\usr\bin\bash.exe', 'C:\Program Files\Git\usr\bin\bash.exe', 'D:\Program Files\Git\bin\bash.exe')) {
        if (Test-Path -LiteralPath $c) { $bash = $c; break }
    }
}
if (-not $bash) {
    $g = Get-Command bash.exe -ErrorAction SilentlyContinue
    if ($g) { $bash = $g.Source }
}
if (-not $bash) { Stop-With 1 '找不到 Git Bash（bash.exe），请设置环境变量 CLAUDE_BASH' }

# 3) 输出目录与单实例锁
$null = New-Item -ItemType Directory -Force -Path $OutDir
$lock = Join-Path $OutDir '.lock'
if (Test-Path -LiteralPath $lock) {
    $old = ([IO.File]::ReadAllText($lock)).Trim()
    $alive = $false
    if ($old -match '^\d+$') {
        if (Get-Process -Id ([int]$old) -ErrorAction SilentlyContinue) { $alive = $true }
    }
    if ($alive) { Stop-With 3 "已有审计在跑 (PID $old)；确认无用后删除 $lock" }
    Remove-Item -LiteralPath $lock -Force
}

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$runDir = Join-Path $OutDir ('run_' + $stamp)
$null = New-Item -ItemType Directory -Force -Path $runDir

# 4) 归一化任务书为 UTF-8 无 BOM 副本
$briefCopy = Join-Path $runDir 'brief.md'
$briefText = [IO.File]::ReadAllText($Brief, [Text.Encoding]::UTF8)
[IO.File]::WriteAllText($briefCopy, $briefText, (New-Object Text.UTF8Encoding($false)))

function ConvertTo-MsysPath([string]$p) {
    $q = $p -replace '\\', '/'
    if ($q -match '^([A-Za-z]):(.*)$') { return ('/' + $Matches[1].ToLower() + $Matches[2]) }
    return $q
}

# 5) 生成 bash 脚本（中文不进命令行参数；超时由内部看门狗兜底）
$tools = 'Read,Grep,Glob'
if ($AllowShell) { $tools = $tools + ',PowerShell' }
$outFile = Join-Path $runDir 'out.txt'
$errFile = Join-Path $runDir 'err.txt'
$rcFile  = Join-Path $runDir 'rc.txt'
$shFile  = Join-Path $runDir 'run.sh'
$timeoutSec = [Math]::Max(60, $TimeoutMin * 60)

$body = @(
    'claude -p "$(cat ''{0}'')" --model "{7}" --effort "{8}" --allowedTools "{1}" --max-turns {2} --output-format text < /dev/null > ''{3}'' 2> ''{4}'' &'
    'cid=$!'
    '( sleep {5}; pkill -9 -P $cid 2>/dev/null; kill -9 $cid 2>/dev/null ) &'
    'wd=$!'
    'wait $cid'
    'rc=$?'
    'kill $wd 2>/dev/null'
    'pkill -9 -P $wd 2>/dev/null'
    'echo $rc > ''{6}'''
) -join "`n"
$shText = "#!/bin/bash`n" + ($body -f (ConvertTo-MsysPath $briefCopy), $tools, $MaxTurns, (ConvertTo-MsysPath $outFile), (ConvertTo-MsysPath $errFile), $timeoutSec, (ConvertTo-MsysPath $rcFile), $Model, $Effort) + "`n"
[IO.File]::WriteAllText($shFile, $shText, (New-Object Text.UTF8Encoding($false)))

if ($DryRun) {
    Write-Host "=== run.sh ($shFile) ==="
    Write-Host $shText
    exit 0
}

# 6) 运行（记录启动前已存在的 claude 进程，超时时只清理新产生的）
$beforeIds = @()
Get-Process -Name claude -ErrorAction SilentlyContinue | ForEach-Object { $beforeIds += [int]$_.Id }
Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*claude-code*' } |
    ForEach-Object { $beforeIds += [int]$_.ProcessId }

[IO.File]::WriteAllText($lock, [string]$PID)
$start = Get-Date
$status = 'done'
$finished = $false
try {
    $proc = Start-Process -FilePath $bash -ArgumentList @((ConvertTo-MsysPath $shFile)) -NoNewWindow -PassThru
    $ms = ($TimeoutMin + 2) * 60000
    $finished = $proc.WaitForExit($ms)
    if (-not $finished) {
        $status = 'timeout'
        & taskkill.exe /PID $proc.Id /T /F | Out-Null
        Get-Process -Name claude -ErrorAction SilentlyContinue |
            Where-Object { $beforeIds -notcontains [int]$_.Id } |
            ForEach-Object { & taskkill.exe /PID $_.Id /T /F | Out-Null }
        Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like '*claude-code*' -and ($beforeIds -notcontains [int]$_.ProcessId) } |
            ForEach-Object { & taskkill.exe /PID $_.ProcessId /T /F | Out-Null }
    }
}
finally {
    if (Test-Path -LiteralPath $lock) { Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue }
}

$end = Get-Date
$rc = (Read-TextSafe $rcFile).Trim()
$outText = Read-TextSafe $outFile
$errText = Read-TextSafe $errFile

if ($rc -eq '137' -and -not $outText.Trim()) { $status = 'watchdog' }

# 7) 元数据
$meta = @()
$meta += 'start=' + $start.ToString('yyyy-MM-dd HH:mm:ss')
$meta += 'end=' + $end.ToString('yyyy-MM-dd HH:mm:ss')
$meta += 'duration_sec=' + [int]($end - $start).TotalSeconds
$meta += 'status=' + $status
$meta += 'rc=' + $rc
$meta += 'tools=' + $tools
$meta += 'model=' + $Model
$meta += 'effort=' + $Effort
$meta += 'max_turns=' + $MaxTurns
$meta += 'timeout_sec=' + $timeoutSec
$meta += 'brief=' + $briefCopy
$meta += 'out_chars=' + $outText.Length
$meta += 'err_chars=' + $errText.Length
[IO.File]::WriteAllText((Join-Path $runDir 'meta.txt'), ($meta -join "`n") + "`n", (New-Object Text.UTF8Encoding($false)))

Write-Host ('[claude_audit] ' + $status + '  rc=' + $rc + '  用时=' + [int]($end - $start).TotalSeconds + 's')
Write-Host ('[claude_audit] 结果: ' + (Join-Path $runDir 'out.txt'))
if ($errText.Trim()) {
    $lastErr = ($errText.Trim() -split "`r?`n")[-1]
    Write-Host ('[claude_audit] stderr: ' + $lastErr)
}

if ($status -eq 'timeout' -or $status -eq 'watchdog') { exit 4 }
if (-not $outText.Trim()) { exit 5 }
if ($rc -and $rc -ne '0') { exit 6 }
exit 0
