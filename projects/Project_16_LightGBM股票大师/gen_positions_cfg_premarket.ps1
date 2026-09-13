# 每日 09:00 盘前生成 P16 风控成本锚（T-20260910：根治"成本表收盘后才生成/断档 → 桥内风控全天静默"时序缺陷）
# 1) gen_positions_cfg_g2.py ：G2 桥（70180771）positions_cfg_<date>.json（当日快照缺失时回退最近快照）
# 2) gen_positions_cfg_v13.py：V1.3 内置风控（67014907）positions_cfg_v13_<date>.json（qmt_trade_log FIFO 含费成本）
# 3) gen_positions_cfg_ens.py：融合桥（70180771 虚拟子账户）positions_cfg_<date>.json（独立目录 p16_ensemble_bridge）
# 注：策略端另有"当日表缺失回退最近表"兜底（BUILD 20260910-181126/181135），本任务是调度层根治，双保险。
# 编码修复（DE 体检 P2-11，2026-09-11）：禁止 `*>> $log`（PowerShell 5.1 重定向=UTF-16LE）与
# Add-Content -Encoding UTF8 混写 → 日志 UTF-8/UTF-16LE 混合损坏；统一用 Out-String 捕获后 UTF-8 追加。
$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$py = "C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe"
$dir = "D:/QuantLab/projects/Project_16_LightGBM股票大师"
$log = "$dir/data/schedules/gen_positions_cfg_premarket.log"
New-Item -ItemType Directory -Force -Path "$dir/data/schedules" | Out-Null
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value "[$ts] ==== P16 风控成本锚盘前生成开始 ====" -Encoding UTF8
$o1 = (& $py "$dir/gen_positions_cfg_g2.py" 2>&1 | Out-String)
$rc1 = $LASTEXITCODE
Add-Content -Path $log -Value $o1 -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] gen_positions_cfg_g2 exit=$rc1" -Encoding UTF8
$o2 = (& $py "$dir/gen_positions_cfg_v13.py" 2>&1 | Out-String)
$rc2 = $LASTEXITCODE
Add-Content -Path $log -Value $o2 -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] gen_positions_cfg_v13 exit=$rc2" -Encoding UTF8
$o3 = (& $py "$dir/gen_positions_cfg_ens.py" 2>&1 | Out-String)
$rc3 = $LASTEXITCODE
Add-Content -Path $log -Value $o3 -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] gen_positions_cfg_ens exit=$rc3" -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] ==== P16 风控成本锚盘前生成结束 ====" -Encoding UTF8
if (($rc1 -ne 0) -or ($rc2 -ne 0) -or ($rc3 -ne 0)) { exit 1 } else { exit 0 }
