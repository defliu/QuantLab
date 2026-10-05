$si = @'
（夸克网盘分享数量超量受限，分一下多个链接分享，所有链接加起来才是全部数据）
我用夸克网盘给你分享了「1.parquet格式数据」链接：https://pan.quark.cn/s/c3a2365f2cac
「1.2parquet格式股票数据」链接：https://pan.quark.cn/s/f1732d2e8e2a
「每日A股日线行情数据parquet（日更）」：https://pan.quark.cn/s/ce4b5b27f15e"

这是供应商的分享链接，你分析一下
'@

$map = @(
  @{tag='c3a2365f2cac'; fid='a690e14484ab42b5b9a062d9381d8145'},
  @{tag='c3a2365f2cac'; fid='aae560fd2a294b50b5db2a4d07f35628'},
  @{tag='c3a2365f2cac'; fid='f039c46767854740aec8fa8ee8e3219d'},
  @{tag='c3a2365f2cac'; fid='c89fe0fd431f445a91ca5c8c3bf1bfaa'},
  @{tag='c3a2365f2cac'; fid='808c82dc3eca4ffca80bacb45ee406f8'},
  @{tag='c3a2365f2cac'; fid='65b4773a377a48cb9944dbaf34922272'},
  @{tag='c3a2365f2cac'; fid='21bebe2ec246442591aa552fe504ac81'},
  @{tag='c3a2365f2cac'; fid='585608e988ad4b3dade8cf263752263d'},
  @{tag='c3a2365f2cac'; fid='18f77c639d814f2f8e15777d945baa62'},
  @{tag='c3a2365f2cac'; fid='757ebe30b4834d7ba6a409b8c927b802'},
  @{tag='c3a2365f2cac'; fid='89f41d0936554a81b586e143a8fe4837'},
  @{tag='c3a2365f2cac'; fid='88bdf62c621943bebb00631376bfc9c0'},
  @{tag='c3a2365f2cac'; fid='646bfb0f5b7644858f552e2c1b97fbe9'},
  @{tag='f1732d2e8e2a'; fid='e53d619207e6450d905087e0d9f9b4ce'},
  @{tag='f1732d2e8e2a'; fid='b44a4bbd05284a79a21146e45fe56db8'},
  @{tag='f1732d2e8e2a'; fid='b0224b78f2f5401a9dabbc8b75775947'},
  @{tag='f1732d2e8e2a'; fid='35a72b36e3e04ea8ad2f9754fb428cfc'},
  @{tag='f1732d2e8e2a'; fid='4c941e3c436b40a3837bdd8f7b6c1535'},
  @{tag='f1732d2e8e2a'; fid='26fd3f54a46c456ebb5a531d1f850454'},
  @{tag='f1732d2e8e2a'; fid='b62efcb618dd447caa95b6c7c0e39964'},
  @{tag='f1732d2e8e2a'; fid='e20a4c8858894b2ba5f0c1e0ae2fc107'},
  @{tag='f1732d2e8e2a'; fid='83cea838d05447bdae11c16554161521'}
)

foreach ($m in $map) {
  $url = 'https://pan.quark.cn/s/' + $m.tag
  $out = 'D:\QuantLab\browse\L2_' + $m.tag + '_' + $m.fid.Substring(0,8) + '.jsonl'
  node scripts/quark-drive.cjs share-detail --url $url --pdir-fid $m.fid --size 100 --session-input $si --session-id "1791073000-q7a2x9" > $out 2>&1
  Write-Output ("DONE " + $m.tag + " " + $m.fid.Substring(0,8))
}