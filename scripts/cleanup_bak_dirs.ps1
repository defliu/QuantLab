# 清理 AI 目录迁移遗留的 .bak 目录（IDE 关闭后运行）
# 必须在 WorkBuddy/CodeBuddy IDE 完全退出后执行，否则文件被锁删不掉

$baks = @(
    "C:\Users\Administrator\.codebuddy.bak",
    "C:\Users\Administrator\.config.bak",
    "C:\Users\Administrator\.vscode.bak",
    "C:\Users\Administrator\.vscode-shared.bak",
    "C:\Users\Administrator\.codex.bak"
)

foreach ($bak in $baks) {
    if (Test-Path $bak) {
        try {
            [System.IO.Directory]::Delete($bak, $true)
            if (-not (Test-Path $bak)) {
                Write-Host "[OK] deleted: $bak"
            } else {
                Write-Host "[WARN] still exists: $bak"
            }
        } catch {
            Write-Host "[FAIL] $bak : $($_.Exception.Message)"
        }
    } else {
        Write-Host "[SKIP] not found: $bak"
    }
}
Write-Host "cleanup done."
