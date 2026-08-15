param([string]$BackupRoot = "F:\WinReinstallBackup")
$Prof = $env:USERPROFILE
$Source = Join-Path $BackupRoot ("UserBackup_" + $env:USERNAME)
if (-not (Test-Path $Source)) { Write-Error ("Cannot find backup source: " + $Source); exit 1 }

# AI agent + dev config items only. Large user-data folders (Documents/Downloads/
# Desktop/Pictures/Videos/Music/Saved Games/Favorites/Links) are intentionally NOT
# included here so the restore is fast and low-risk; run the full restore_configs.ps1 later if needed.
$Items = @(
  ".ssh", ".gitconfig", ".mcp.json", ".npmrc", ".npm", ".bun",
  ".claude", ".claude.json", ".codebuddy", ".codex", ".qoder", ".qoder-cn",
  ".qwen", ".qwenworkcn", ".trae", ".trae-cn", ".aider-desk", ".cline",
  ".roo", ".continue", ".kilocode", ".vibe", ".vibe-trading", ".vibe-research",
  ".kimi", ".kimi-webbridge", ".doubao", ".hermes", ".lingma", ".augment",
  ".workbuddy",
  ".config", ".xtquant", ".vscode", ".vscode-shared", ".matplotlib"
)

# Exclude runtime/cache/volatile dirs & files inside .workbuddy so we do NOT
# clobber the live WorkBuddy process or copy multi-GB binaries.
$WBExcludeDirs = @(
  "binaries","blobs","sessions","logs","cache","file-history","artifact-index",
  "plugins","local_storage","pending-telemetry","plans","inspiration",
  "clipboard-images","desktop_conversation_migrated","session_fragment_repair_done520"
)
$WBExcludeFiles = @(
  "edge-sync-mapping.db","edge-sync-mapping.db-shm","edge-sync-mapping.db-wal",
  "ioa-im-override.json","last-launch.json"
)

function Invoke-Robo($src, $target, $exclDirs, $exclFiles) {
  $a = @($src, $target, "/E", "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS")
  if ($exclDirs) { $a += "/XD"; $a += $exclDirs }
  if ($exclFiles) { $a += "/XF"; $a += $exclFiles }
  & robocopy @a | Out-Null
}

function Restore-Safe($rel) {
  $src = Join-Path $Source $rel
  if (-not (Test-Path $src)) { Write-Host ("[SKIP] missing: " + $rel) -ForegroundColor DarkGray; return }
  $target = Join-Path $Prof $rel
  if ($rel -eq ".workbuddy") {
    Invoke-Robo $src $target $WBExcludeDirs $WBExcludeFiles
  } else {
    Invoke-Robo $src $target $null $null
  }
  Write-Host ("[OK]   " + $rel) -ForegroundColor Green
}

Write-Host ("Restore AI/dev configs from " + $Source + " -> " + $Prof) -ForegroundColor Cyan
foreach ($it in $Items) { Restore-Safe $it }
Write-Host "`nAI/dev config restore done." -ForegroundColor Green
Write-Host "Note: large user-data folders (Documents/Downloads/Desktop etc.) were skipped." -ForegroundColor DarkGray
Write-Host "      Run the original restore_configs.ps1 (after fixing its UTF-8/GBK encoding) for a full restore." -ForegroundColor DarkGray
