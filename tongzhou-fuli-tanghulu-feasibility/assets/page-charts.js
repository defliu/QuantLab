(function () {
  const palette = {
    blue: "#2563eb",
    teal: "#0f766e",
    rose: "#b83f58",
    gold: "#b7791f",
    line: "#e6ebf0",
    axis: "#475569"
  };

  function readJson(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    try {
      return JSON.parse(el.textContent || "null");
    } catch (error) {
      console.warn("Invalid JSON block", id, error);
      return null;
    }
  }

  function axisName(text) {
    return { textStyle: { color: palette.axis, fontSize: 12 } };
  }

  function scenarioOption(rows, type) {
    const isLine = type === "line";
    return {
      animation: false,
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      legend: { top: 0, itemGap: 18 },
      grid: { left: 56, right: 26, top: 46, bottom: 34, containLabel: true },
      xAxis: {
        type: "category",
        data: rows.map((row) => row.scenario),
        axisLabel: axisName(),
        axisTick: { alignWithLabel: true }
      },
      yAxis: {
        type: "value",
        name: "万元",
        nameTextStyle: axisName(),
        splitLine: { lineStyle: { color: palette.line } },
        axisLabel: axisName()
      },
      series: [
        {
          name: "年营收",
          type: isLine ? "line" : "bar",
          smooth: isLine,
          symbolSize: 7,
          barMaxWidth: 52,
          data: rows.map((row) => row.revenue),
          itemStyle: { color: palette.blue, borderRadius: isLine ? 0 : [4, 4, 0, 0] },
          lineStyle: { width: 2.5, color: palette.blue }
        },
        {
          name: "年净利",
          type: isLine ? "line" : "bar",
          smooth: isLine,
          symbolSize: 7,
          barMaxWidth: 52,
          data: rows.map((row) => ({
            value: row.net,
            itemStyle: {
              color: row.net >= 0 ? palette.teal : palette.rose,
              borderRadius: isLine ? 0 : row.net >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4]
            }
          })),
          lineStyle: { width: 2.5, color: palette.teal }
        }
      ]
    };
  }

  function seasonOption(rows, type) {
    if (type === "line") {
      return {
        animation: false,
        tooltip: { trigger: "axis" },
        legend: { top: 0, itemGap: 18 },
        grid: { left: 60, right: 60, top: 46, bottom: 34, containLabel: true },
        xAxis: {
          type: "category",
          data: rows.map((row) => row.m + "月"),
          axisLabel: axisName(),
          axisTick: { alignWithLabel: true }
        },
        yAxis: [
          {
            type: "value",
            name: "累计现金 万元",
            nameTextStyle: axisName(),
            splitLine: { lineStyle: { color: palette.line } },
            axisLabel: axisName()
          },
          {
            type: "value",
            name: "月营收 万元",
            nameTextStyle: axisName(),
            splitLine: { show: false },
            axisLabel: axisName()
          }
        ],
        series: [
          {
            name: "累计现金",
            type: "line",
            smooth: true,
            symbolSize: 7,
            data: rows.map((row) => row.cum),
            itemStyle: { color: palette.gold },
            lineStyle: { width: 2.5, color: palette.gold },
            areaStyle: { color: "rgba(183,121,31,0.10)" },
            markLine: {
              silent: true,
              symbol: "none",
              data: [
                {
                  yAxis: 0,
                  lineStyle: { color: palette.rose, type: "dashed", width: 1.5 },
                  label: { formatter: "回本线", color: palette.rose, fontSize: 11 }
                }
              ]
            }
          },
          {
            name: "月营收",
            type: "line",
            yAxisIndex: 1,
            smooth: true,
            symbolSize: 6,
            data: rows.map((row) => row.rev),
            itemStyle: { color: palette.blue },
            lineStyle: { width: 2, type: "dashed", color: palette.blue }
          }
        ]
      };
    }

    return {
      animation: false,
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      legend: { top: 0, itemGap: 18 },
      grid: { left: 56, right: 60, top: 46, bottom: 34, containLabel: true },
      xAxis: {
        type: "category",
        data: rows.map((row) => row.m + "月"),
        axisLabel: axisName(),
        axisTick: { alignWithLabel: true }
      },
      yAxis: [
        {
          type: "value",
          name: "营业利润 万元",
          nameTextStyle: axisName(),
          splitLine: { lineStyle: { color: palette.line } },
          axisLabel: axisName()
        },
        {
          type: "value",
          name: "月营收 万元",
          nameTextStyle: axisName(),
          splitLine: { show: false },
          axisLabel: axisName()
        }
      ],
      series: [
        {
          name: "月营业利润",
          type: "bar",
          barMaxWidth: 26,
          data: rows.map((row) => ({
            value: row.profit,
            itemStyle: { color: row.profit >= 0 ? palette.teal : palette.rose }
          }))
        },
        {
          name: "月营收",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbolSize: 6,
          data: rows.map((row) => row.rev),
          itemStyle: { color: palette.gold },
          lineStyle: { width: 2.5, color: palette.gold }
        }
      ]
    };
  }

  function sensitivityOption(rows, type) {
    const isBar = type === "bar";
    return {
      animation: false,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: isBar ? "shadow" : "line" }
      },
      legend: { top: 0, itemGap: 18 },
      grid: { left: 60, right: 60, top: 46, bottom: 40, containLabel: true },
      xAxis: {
        type: "value",
        min: 30,
        max: 140,
        interval: 20,
        name: "日均成交单数",
        nameLocation: "middle",
        nameGap: 26,
        nameTextStyle: axisName(),
        axisLabel: axisName(),
        splitLine: { lineStyle: { color: palette.line } }
      },
      yAxis: [
        {
          type: "value",
          name: "年净利 万元",
          nameTextStyle: axisName(),
          axisLabel: axisName(),
          splitLine: { lineStyle: { color: palette.line } }
        },
        {
          type: "value",
          name: "年营收 万元",
          nameTextStyle: axisName(),
          axisLabel: axisName(),
          splitLine: { show: false }
        }
      ],
      series: [
        {
          name: "年净利",
          type: isBar ? "bar" : "line",
          smooth: !isBar,
          symbolSize: 7,
          barMaxWidth: 22,
          data: rows.map((row) => ({
            value: [row.orders, row.net],
            itemStyle: { color: row.net >= 0 ? palette.teal : palette.rose }
          })),
          itemStyle: { color: palette.teal },
          lineStyle: { width: 2.5, color: palette.teal },
          areaStyle: isBar ? undefined : { color: "rgba(15,118,110,0.10)" },
          markLine: {
            silent: true,
            symbol: "none",
            data: [
              {
                yAxis: 0,
                lineStyle: { color: palette.rose, type: "dashed", width: 1.5 },
                label: { formatter: "盈亏平衡线", color: palette.rose, fontSize: 11 }
              },
              {
                xAxis: 43,
                lineStyle: { color: palette.gold, type: "dotted", width: 1.5 },
                label: { formatter: "43单/日", color: palette.gold, fontSize: 11 }
              }
            ]
          }
        },
        {
          name: "年营收",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbolSize: 5,
          data: rows.map((row) => [row.orders, row.rev]),
          itemStyle: { color: palette.blue },
          lineStyle: { width: 2, type: "dashed", color: palette.blue }
        }
      ]
    };
  }

  function optionFor(kind, rows, type) {
    if (kind === "scenario-bars") return scenarioOption(rows, type);
    if (kind === "season-bars") return seasonOption(rows, type);
    if (kind === "sensitivity-line") return sensitivityOption(rows, type);
    return {};
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!window.echarts) return;
    document.querySelectorAll("[data-chart]").forEach((block) => {
      const kind = block.getAttribute("data-chart");
      if (kind === "market-trend" || kind === "share-ranking") return;
      const canvas = block.querySelector(".chart-canvas");
      const rows = readJson(block.getAttribute("data-data"));
      if (!canvas || !rows) return;
      const chart = echarts.getInstanceByDom(canvas) || echarts.init(canvas, null, { renderer: "canvas" });
      const initial = block.getAttribute("data-type") || "bar";
      chart.setOption(optionFor(kind, rows, initial), true);

      block.querySelectorAll("[data-chart-type]").forEach((button) => {
        button.addEventListener("click", () => {
          chart.setOption(optionFor(kind, rows, button.dataset.chartType), true);
        });
      });
    });
  });
})();
