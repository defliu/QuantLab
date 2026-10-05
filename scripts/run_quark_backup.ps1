# coding: utf-8
# ============================================================
# QuantLab -> 夸克网盘 定期增量备份 · 计划任务包装脚本
# 用法:
#   powershell -NoProfile -ExecutionPolicy Bypass -File run_quark_backup.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File run_quark_backup.ps1 -Mode full
#   powershell -NoProfile -ExecutionPolicy Bypass -File run_quark_backup.ps1 -Mode dry-run
# 业务逻辑全部在 scripts/quark_backup.py（纯标准库，不依赖 LLM）。
# 本脚本只负责: 固定解释器路径 / 固定 node PATH / 落盘调度层日志 / 透传退出码。
#
# 关键点（勿删）:
#   1) 必须用绝对 python 路径。S4U 计划任务环境下 PATH 里没有 python
#      （本机 PATH 只有 WindowsApps 的空壳 python.exe，直接调用会 9009 退出）。
#   2) 必须显式补 node 目录。quark_backup.py 内部 shutil.which("node") 找不到就 SKIP。
#   3) 日志用 UTF8 追加，避免中文落盘乱码。
# 退出码透传: 0 成功/无变更, 1 上传失败, 2 前置不满足, 3 备份自身异常
# ============================================================
param(
    [ValidateSet("auto", "full", "increment", "dry-run", "audit")]
    [string]$Mode = "auto"
)
$ErrorActionPreference = "Continue"

# ---- 路径 ----
$py = "C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe"
$script = "D:/QuantLab/scripts/quark_backup.py"
$log = "D:/QuantLab/data/quark_backup/scheduler.log"

# ---- node 必须可见（S4U session 0 环境 PATH 不含 nodejs）----
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    $nodeDir = "C:\Program Files\nodejs"
    if (Test-Path $nodeDir) {
        $env:Path = $nodeDir + ";" + $env:Path
    }
}

# ---- UTF-8 环境（Python stdout 与 PowerShell 解码双向对齐）----
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $log) | Out-Null

$pyArgs = @($script)
switch ($Mode) {
    "full"      { $pyArgs += "--full" }
    "increment" { $pyArgs += "--increment" }
    "dry-run"   { $pyArgs += "--dry-run" }
    "audit"     { $pyArgs += "--audit" }
    "auto"      { }
}

$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value "[$ts] ==== quark backup 启动 mode=$Mode ====" -Encoding UTF8

if (-not (Test-Path $py)) {
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] !! 解释器不存在: $py" -Encoding UTF8
    exit 2
}

& $py @pyArgs 2>&1 | ForEach-Object {
    Add-Content -Path $log -Value $_ -Encoding UTF8
}
$rc = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] quark backup exit=$rc" -Encoding UTF8
exit $rc