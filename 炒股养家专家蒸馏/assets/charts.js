/* 炒股养家专家蒸馏 · 图表逻辑（情绪周期温度曲线） */
(function () {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();

  var el = document.getElementById('chart-cycle');
  if (!el || !window.echarts) { return; }

  var chart = echarts.init(el, null, { renderer: 'svg' });

  var stages = ['冰点', '启动', '发酵', '高潮', '退潮', '冰点\n回归'];
  var temps = [8, 42, 78, 96, 30, 8];
  var actions = ['小仓试错\n逆向布局', '跟踪主线\n试错确认', '重仓主线\n加仓龙头', '分批卖出\n逐步撤退', '轻仓空仓\n不做杂毛', '等待新循环'];

  chart.setOption({
    animation: false,
    tooltip: {
      trigger: 'axis',
      appendToBody: true,
      backgroundColor: bg2,
      borderColor: rule,
      textStyle: { color: ink, fontFamily: 'inherit' }
    },
    grid: { left: 46, right: 30, top: 46, bottom: 44 },
    xAxis: {
      type: 'category',
      data: stages,
      boundaryGap: false,
      axisLine: { lineStyle: { color: rule } },
      axisTick: { show: false },
      axisLabel: { color: muted, fontSize: 12, lineHeight: 16 }
    },
    yAxis: {
      type: 'value',
      name: '情绪温度',
      min: 0,
      max: 100,
      interval: 20,
      splitLine: { lineStyle: { color: rule } },
      axisLabel: { color: muted },
      nameTextStyle: { color: muted }
    },
    series: [{
      type: 'line',
      data: temps,
      smooth: true,
      symbol: 'circle',
      symbolSize: 8,
      lineStyle: { color: accent, width: 3 },
      itemStyle: { color: accent },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: accent + '59' },
            { offset: 1, color: accent + '00' }
          ]
        }
      },
      markPoint: {
        symbol: 'none',
        label: {
          show: true,
          position: 'top',
          color: accent2,
          fontSize: 12,
          fontWeight: 600,
          lineHeight: 16,
          formatter: function (p) { return actions[p.dataIndex]; }
        },
        data: temps.map(function (v, i) { return { value: v, xAxis: i, yAxis: v }; })
      },
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: accent2, type: 'dashed', width: 1 },
        label: { color: accent2, fontSize: 11, position: 'insideEndTop' },
        data: [
          { yAxis: 80, label: { formatter: '顶部区 · 分批撤退' } },
          { yAxis: 20, label: { formatter: '底部区 · 小仓试错' } }
        ]
      }
    }]
  });

  window.addEventListener('resize', function () { chart.resize(); });
})();
