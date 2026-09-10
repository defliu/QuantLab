# QMT 策略粘贴部署半自动化脚本（T-20260910）
# 前提：QMT 客户端已打开且目标策略编辑器处于打开状态（模型交易页签）。
# 流程：产物写入剪贴板(GBK读入) → 激活QMT窗口 → 盲发 Ctrl+A/Ctrl+V/Ctrl+S → 校验部署文件哈希 → 轮询日志 BUILD_TAG。
# 校验兜底：①部署文件内容==产物内容 ②QMT日志出现新 BUILD_TAG。任一失败即报 FAIL，人工介入。
# 用法: .\deploy_paste_qmt.ps1 -Product <产物路径> -QmtDir <QMT安装目录> -StrategyFile <QMT端策略文件> -BuildTag <期望BUILD_TAG>
param(
  [Parameter(Mandatory=$true)][string]$Product,
  [Parameter(Mandatory=$true)][string]$QmtDir,
  [Parameter(Mandatory=$true)][string]$StrategyFile,
  [Parameter(Mandatory=$true)][string]$BuildTag
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinAct {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@

# 1) 产物读入剪贴板（GBK 解码，剪贴板走 Unicode，QMT 粘贴已验证可行）
$gbk = [System.Text.Encoding]::GetEncoding('GBK')
$text = [System.IO.File]::ReadAllText($Product, $gbk)
Set-Clipboard -Value $text
Write-Output "[1/5] 剪贴板已置入产物（$([math]::Round($text.Length/1024.0,1)) KB）"

# 2) 定位并激活 QMT 窗口（按进程路径匹配，双客户端并存时不会拿错）
$proc = Get-Process XtItClient -ErrorAction Stop | Where-Object { $_.Path -like "$QmtDir*" } | Select-Object -First 1
if (-not $proc) { throw "未找到 QMT 进程：$QmtDir" }
[WinAct]::ShowWindow($proc.MainWindowHandle, 9) | Out-Null   # SW_RESTORE
[WinAct]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 1200
Write-Output "[2/5] 已激活窗口 PID=$($proc.Id) title='$($proc.MainWindowTitle)'"

# 3) 盲发按键（焦点须在策略编辑器：Ctrl+A 全选 → Ctrl+V 粘贴 → Ctrl+S 保存）
[System.Windows.Forms.SendKeys]::SendWait('^a')
Start-Sleep -Milliseconds 300
[System.Windows.Forms.SendKeys]::SendWait('^v')
Start-Sleep -Milliseconds 800
[System.Windows.Forms.SendKeys]::SendWait('^s')
Write-Output "[3/5] 已发送 Ctrl+A / Ctrl+V / Ctrl+S"

# 4) 校验1：QMT 端策略文件 mtime 更新 + 归一化内容 == 产物（行尾差异容忍，BUILD_TAG 必须在）
$mtimeBefore = (Get-Item -LiteralPath $StrategyFile).LastWriteTime
$prodNorm = ([System.IO.File]::ReadAllText($Product, $gbk) -replace "`r","")
$ok = $false
for ($i = 0; $i -lt 5; $i++) {
  Start-Sleep -Seconds 2
  $fi = Get-Item -LiteralPath $StrategyFile
  if ($fi.LastWriteTime -le $mtimeBefore) { continue }
  $sfNorm = ([System.IO.File]::ReadAllText($StrategyFile, $gbk) -replace "`r","")
  if ($sfNorm -eq $prodNorm) { $ok = $true; break }
  Write-Output "  [WARN] mtime 已更新但内容不一致（粘贴不完整？）"
  break
}
if (-not $ok) {
  Write-Output "[4/5] FAIL: 部署文件未更新或内容与产物不一致（粘贴未落地或焦点不在编辑器），请人工粘贴"
  exit 2
}
Write-Output "[4/5] 校验1 PASS: 部署文件已保存且内容 == 产物"

# 5) 校验2：轮询 QMT 日志出现期望 BUILD_TAG（保存触发重载运行的证据）
$log = Join-Path $QmtDir "userdata\log\XtClient_FormulaOutput_$(Get-Date -Format yyyyMMdd).log"
$found = $false
for ($i = 0; $i -lt 15; $i++) {
  if (Test-Path $log) {
    $raw = [System.IO.File]::ReadAllText($log, $gbk)
    if ($raw.Contains("BUILD_TAG=$BuildTag")) { $found = $true; break }
  }
  Start-Sleep -Seconds 2
}
if (-not $found) {
  Write-Output "[5/5] WARN: 文件已更新但日志未见 BUILD_TAG=$BuildTag（可能需手动点击运行），请核对日志"
  exit 3
}
Write-Output "[5/5] 校验2 PASS: 日志已出现 BUILD_TAG=$BuildTag，部署生效"
exit 0
