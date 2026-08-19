/* 图表渲染(web-nav spec「结果图表渲染」):
   chart.type=line/pie/bar 渲染,none 不渲染;色板从设计 token 派生(铁律 6,禁硬编码)。 */

import { useMemo } from "react";
import ReactECharts from "echarts-for-react";

interface ChartSpec {
  type: string;
  x: string | null;
  y: string | null;
}

export interface ChartDatum {
  columns: string[];
  rows: unknown[][];
}

/* 从 CSS token 派生图表色板:朱砂打底、鎏金点睛、青灰过渡,惜色如金;
   只用面向数据的 token,前景文字色不入系列(浅色底上近不可见) */
function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function chartPalette(): string[] {
  return [cssVar("--primary"), cssVar("--ring"), cssVar("--accent-foreground"), cssVar("--muted-foreground")];
}

export function ChartView({ chart, data }: { chart: ChartSpec; data: ChartDatum }) {
  const palette = useMemo(chartPalette, []);
  const axisColor = useMemo(() => cssVar("--muted-foreground"), []);
  const lineColor = useMemo(() => cssVar("--border"), []);

  const option = useMemo(() => {
    const xi = chart.x ? data.columns.indexOf(chart.x) : 0;
    const yi = chart.y ? data.columns.indexOf(chart.y) : 1;
    const labels = data.rows.map((r) => String(r[xi] ?? ""));
    const values = data.rows.map((r) => {
      const v = r[yi];
      return typeof v === "number" ? v : Number(v);
    });

    if (chart.type === "pie") {
      return {
        tooltip: { trigger: "item" },
        legend: { bottom: 0, left: "center", textStyle: { color: axisColor } },
        series: [
          {
            type: "pie",
            radius: ["38%", "66%"],
            center: ["50%", "44%"],
            data: labels.map((name, i) => ({ name, value: values[i] })),
            color: palette,
            label: { show: false },
            labelLine: { show: false },
          },
        ],
      };
    }
    const axisCommon = {
      axisLabel: { color: axisColor },
      axisLine: { lineStyle: { color: lineColor } },
    };
    return {
      grid: { left: 12, right: 16, top: 24, bottom: 8, containLabel: true },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: labels, ...axisCommon },
      yAxis: { type: "value", ...axisCommon },
      series: [
        chart.type === "line"
          ? {
              type: "line",
              data: values,
              smooth: true,
              itemStyle: { color: palette[0] },
              lineStyle: { color: palette[0], width: 2 },
            }
          : {
              type: "bar",
              data: values,
              itemStyle: { color: palette[0], borderRadius: [3, 3, 0, 0] },
              barMaxWidth: 40,
            },
      ],
    };
  }, [chart, data, palette, axisColor, lineColor]);

  return (
    <ReactECharts
      option={option}
      notMerge
      lazyUpdate
      style={{ height: 260, width: "100%" }}
      opts={{ renderer: "canvas" }}
    />
  );
}
