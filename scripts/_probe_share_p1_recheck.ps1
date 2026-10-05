$si = @'
（夸克网盘分享数量超量受限，分一下多个链接分享，所有链接加起来才是全部数据）
我用夸克网盘给你分享了「1.parquet格式数据」链接：https://pan.quark.cn/s/c3a2365f2cac
「1.2parquet格式股票数据」链接：https://pan.quark.cn/s/f1732d2e8e2a
「每日A股日线行情数据parquet（日更）」：https://pan.quark.cn/s/ce4b5b27f15e"

这是供应商的分享链接，你分析一下
'@

$tags = @('c3a2365f2cac', 'f1732d2e8e2a', 'ce4b5b27f15e')

foreach ($t in $tags) {
  $url = 'https://pan.quark.cn/s/' + $t
  $out = 'D:\QuantLab\browse\RECHECK_' + $t + '.jsonl'
  node scripts/quark-drive.cjs share-detail --url $url --size 100 --session-input $si --session-id "1791073000-q7a2x9" > $out 2>&1
  Write-Output ("DONE " + $t)
}
