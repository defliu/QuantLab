# QMT 信号 hub 启停（2026-10-29 立）
#
# 作用：把外部算好的换仓计划，用只读 HTTP 暴露给 QMT 内置执行壳。
#   实例1（58610）：主号计划目录      -> 主号 STRATEGY_P16_G2_EXEC（67014907）
#   实例2（58611）：沙箱镜像计划目录  -> 沙箱 STRATEGY_P16_G2_EXEC_SBX（70180771）
#
# 为什么用计划任务而非常驻：hub 只在交易日盘中需要；白天起、收盘停，不给系统留长跑进程。
# 为什么 hub 挂了也没事：拿不到信号 = 不下单（fail-safe），不会出现半截指令。
#
# 用法：
#   .\qmt_signal_hub.ps1 -Mode Start     # 起（已在跑的端口自动 SKIP）
#   .\qmt_signal_hub.ps1 -Mode Stop      # 停（只杀命令行含 qmt_signal_hub.py 的进程）
#   .\qmt_signal_hub.ps1 -Mode Status    # 只读查看
#
# 退出码：0 正常 / 2 有实例启动失败 / 3 环境缺失（python 或脚本不在）
param(
    [ValidateSet("Start", "Stop", "Status")][string]$Mode = "Start"
)
$ErrorActionPreference = "Continue"

$dir    = "D:\QuantLab\projects\Project_16_LightGBM股票大师"
$sched  = Join-Path $dir "data\schedules"
$log    = Join-Path $sched "qmt_signal_hub.log"
# hub 与 mirror 只用标准库，**不依赖 miniQMT / xtquant** —— 直接用系统 3.11。
# （`envs\miniqmt\Scripts\python.exe` 这个名字是历史遗留：当初为跑 xtquant 装的 3.11 venv；
#   而且它是 wrapper，会再拉起 `D:\Python311\python.exe` 子进程，杀父时子进程消失会让 Stop 判失败。
#   直连后一个进程搞定，也免得再有人以为这条链要靠 miniQMT。）
$python = "D:\Python311\python.exe"
$pythonFallback = "C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe"
if (!(Test-Path $python)) { $python = $pythonFallback }
$hubPy  = Join-Path $dir "qmt_signal_hub.py"
$mirror = Join-Path $dir "qmt_signal_mirror.py"

# port | 计划目录 | 账号 | 资金 | 是否先镜像
$instances = @(
    @{ Port = 58610; PlanDir = "D:\QuantLab\data\rebalance_g2";      Account = "67014907"; Capital = 100000; Mirror = $false },
    @{ Port = 58611; PlanDir = "D:\QuantLab\data\rebalance_g2\sbx";  Account = "70180771"; Capital = 20000;  Mirror = $true  }
)

function Write-Log([string]$msg) {
    $msg = ($msg -replace "\r?\n", " | ")
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    try {
        if (!(Test-Path $sched)) { New-Item -ItemType Directory -Force -Path $sched | Out-Null }
        Add-Content -Path $log -Value $line -Encoding UTF8
    } catch { }
    Write-Host $line
}

function Test-Port([int]$port) {
    try {
        $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop
        return ($null -ne $c)
    } catch {
        try {
            $t = New-Object System.Net.Sockets.TcpClient
            $null = $t.Connect("127.0.0.1", $port)
            $t.Close(); return $true
        } catch { return $false }
    }
}

function Get-HubProcs([int]$port) {
    $out = @()
    try {
        $out = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop |
                 Where-Object { $_.CommandLine -and $_.CommandLine.Contains("qmt_signal_hub.py") -and
                                $_.CommandLine.Contains("--port $port") })
    } catch { }
    return $out
}

if ($Mode -eq "Status") {
    foreach ($i in $instances) {
        $listen = Test-Port $i.Port
        $procs = Get-HubProcs $i.Port
        Write-Log ("STATUS port={0} listen={1} pid={2} planDir={3}" -f $i.Port, $listen,
            (($procs | ForEach-Object { $_.ProcessId }) -join ","), $i.PlanDir)
    }
    exit 0
}

if ($Mode -eq "Stop") {
    $rc = 0
    foreach ($i in $instances) {
        $procs = Get-HubProcs $i.Port
        if (-not $procs) { Write-Log ("STOP port={0} 无在跑的 hub（跳过）" -f $i.Port); continue }
        foreach ($p in $procs) {
            # miniqmt 的 python.exe 是个 wrapper：它还会拉起 D:\Python311\python.exe 子进程，
            # 杀掉父进程时子进程往往同时消失。所以「进程已不存在」必须算成功，不能记失败
            # （否则 Stop 恒返 2，任务调度里看起来永远失败）。
            if (-not (Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue)) {
                Write-Log ("STOP port={0} pid={1} 已随父进程退出" -f $i.Port, $p.ProcessId); continue
            }
            try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; Write-Log ("STOP port={0} pid={1} 已停" -f $i.Port, $p.ProcessId) }
            catch { Write-Log ("STOP port={0} pid={1} 失败：{2}" -f $i.Port, $p.ProcessId, $_.Exception.Message); $rc = 2 }
        }
    }
    exit $rc
}

# ---- Start ----
if (!(Test-Path $python)) { Write-Log ("FAIL python 不存在：{0}" -f $python); exit 3 }
if (!(Test-Path $hubPy)) { Write-Log ("FAIL hub 脚本不存在：{0}" -f $hubPy); exit 3 }

$rc = 0
foreach ($i in $instances) {
    if ((Test-Port $i.Port) -or (Get-HubProcs $i.Port)) {
        Write-Log ("SKIP port={0} 已在监听（幂等）" -f $i.Port)
        continue
    }
    # 沙箱要先把主号计划镜像成自己账号/资金的计划；源不存在只记一条，不阻断主号实例
    if ($i.Mirror) {
        $src = Join-Path $instances[0].PlanDir ("rebalance_g2_{0}.json" -f (Get-Date -Format "yyyyMMdd"))
        if (Test-Path $src) {
            $mo = & $python $mirror --src $src --dst-dir $i.PlanDir --account $i.Account --capital $i.Capital 2>&1
            Write-Log ("MIRROR " + (($mo | Out-String).Trim()))
        } else {
            Write-Log ("MIRROR skip 源计划不存在：{0}" -f $src)
        }
    }
    try {
        # ★ 不能用 Start-Process：它起的子进程会被计划任务的 job 收编，任务于是「一直在运行」
        # （LastTaskResult=0x41301），30 分钟执行上限一到就把 hub 一起 TERMINATE 掉。
        # Win32_Process.Create 建的是独立进程（父进程是 services），任务可以立刻以 0 结束。
        $outLog = Join-Path $sched ("qmt_signal_hub_{0}.out.log" -f $i.Port)
        $cmdLine = 'cmd.exe /c ""{0}" "{1}" --plan-dir "{2}" --account {3} --port {4} >> "{5}" 2>&1"' -f `
            $python, $hubPy, $i.PlanDir, $i.Account, $i.Port, $outLog
        $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
            -Arguments @{ CommandLine = $cmdLine; CurrentDirectory = $dir }
        if ($r.ReturnValue -ne 0) { throw ("Win32_Process.Create ReturnValue=" + $r.ReturnValue) }
        Write-Log ("SPAWN port={0} launcherPid={1} out={2}" -f $i.Port, $r.ProcessId, $outLog)
    } catch {
        Write-Log ("FAIL port={0} 启动异常：{1}" -f $i.Port, $_.Exception.Message); $rc = 2; continue
    }
    $ok = $false
    for ($k = 0; $k -lt 20; $k++) {
        Start-Sleep -Milliseconds 500
        if (Test-Port $i.Port) { $ok = $true; break }
    }
    if ($ok) { Write-Log ("OK port={0} 已监听 planDir={1} account={2}" -f $i.Port, $i.PlanDir, $i.Account) }
    else { Write-Log ("FAIL port={0} 10s 内未监听（看 python 是否报错）" -f $i.Port); $rc = 2 }
}
exit $rc
