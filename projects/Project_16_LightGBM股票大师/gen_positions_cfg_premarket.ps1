# 每日 09:00 盘前生成 P16 风控成本锚（T-20260910：根治"成本表收盘后才生成/断档 → 桥内风控全天静默"时序缺陷）
# 1) gen_positions_cfg_g2.py ：G2 桥（70180771）positions_cfg_<date>.json（当日快照缺失时回退最近快照）
# 2) gen_positions_cfg_v13.py：V1.3 内置风控（67014907）positions_cfg_v13_<date>.json（qmt_trade_log FIFO 含费成本）
# 注：策略端另有"当日表缺失回退最近表"兜底（BUILD 20260910-181126/181135），本任务是调度层根治，双保险。
$py = "C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe"
$dir = "D:/QuantLab/projects/Project_16_LightGBM股票大师"
$log = "$dir/data/schedules/gen_positions_cfg_premarket.log"
New-Item -ItemType Directory -Force -Path "$dir/data/schedules" | Out-Null
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value "[$ts] ==== P16 风控成本锚盘前生成开始 ====" -Encoding UTF8
& $py "$dir/gen_positions_cfg_g2.py" *>> $log
$rc1 = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] gen_positions_cfg_g2 exit=$rc1" -Encoding UTF8
& $py "$dir/gen_positions_cfg_v13.py" *>> $log
$rc2 = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] gen_positions_cfg_v13 exit=$rc2" -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] ==== P16 风控成本锚盘前生成结束 ====" -Encoding UTF8
if (($rc1 -ne 0) -or ($rc2 -ne 0)) { exit 1 } else { exit 0 }
