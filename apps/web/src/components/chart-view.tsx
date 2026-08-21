/* 图表渲染(web-nav spec「结果图表渲染」+ 011 图表增强):
   bar/line/pie/scatter 四类型;scatter 双形态(x 列全数值→数值散点,否则退化类目
   点图——value 轴遇 NaN 静默丢点为上游实证坑);手动切换=卡片本地状态,映射恒定,
   prop 变更回落建议类型;色板从设计 token 派生(铁律 6,禁硬编码)。 */

import { useEffect, useMemo, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import { ChartColumn, ChartLine, ChartPie, ChartScatter, ImageDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

interface ChartSpec {
  type: string;
  x: string | null;
  y: string | null;
}

export interface ChartDatum {
  columns: string[];
  rows: unknown[][];
}

const CHART_TYPES = ["bar", "line", "pie", "scatter"] as const;
type ChartType = (typeof CHART_TYPES)[number];

const TYPE_META: Record<ChartType, { label: string; Icon: typeof ChartColumn }> = {
  bar: { label: "柱状图", Icon: ChartColumn },
  line: { label: "折线图", Icon: ChartLine },
  pie: { label: "饼图", Icon: ChartPie },
  scatter: { label: "散点图", Icon: ChartScatter },
};

/* 从 CSS token 派生图表色板:朱砂打底、鎏金点睛、青灰过渡,惜色如金;
   只用面向数据的 token,前景文字色不入系列(浅色底上近不可见) */
function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function chartPalette(): string[] {
  return [cssVar("--primary"), cssVar("--ring"), cssVar("--accent-foreground"), cssVar("--muted-foreground")];
}

function normalizeType(t: string): ChartType {
  return (CHART_TYPES as readonly string[]).includes(t) ? (t as ChartType) : "bar";
}

/* 空值显式排除:JS Number(null)===0 / Number("")===0 会把空单元格误判为数值 0;
   空白串同理(Number("  ")===0),一并拦下 */
function isNumericCell(v: unknown): boolean {
  if (v === null || v === undefined || typeof v === "boolean") return false;
  if (typeof v === "string" && v.trim() === "") return false;
  return Number.isFinite(Number(v));
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/* temporal 轴标签本地化(011 挂账⑤/013):PG 日期序列化为 ISO 原文
   (2005-07-05T00:00:00)不可读。全列匹配 ISO 日期形态才启用(任一不匹配
   即整列原样,宁不错格式化——编号串不可能匹配完整形态);启用时呈现日期
   部分,时间非全零附加 HH:mm。不引日期库(截断即可),轴与 tooltip 同源。 */
const ISO_DATE_RE = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/;

function formatTemporalLabels(labels: string[]): string[] {
  if (labels.length === 0 || !labels.every((l) => ISO_DATE_RE.test(l))) return labels;
  return labels.map((l) => {
    const m = ISO_DATE_RE.exec(l);
    if (!m) return l;
    return m[2] === "00" && m[3] === "00" ? m[1] : `${m[1]} ${m[2]}:${m[3]}`;
  });
}

export function ChartView({ chart, data }: { chart: ChartSpec; data: ChartDatum }) {
  const palette = useMemo(chartPalette, []);
  const axisColor = useMemo(() => cssVar("--muted-foreground"), []);
  const lineColor = useMemo(() => cssVar("--border"), []);
  const chartRef = useRef<ReactECharts>(null);

  const [activeType, setActiveType] = useState<ChartType>(() => normalizeType(chart.type));
  // 回落语义(spec):新结果进入/组件实例复用时重置回后端建议类型。
  // 依赖取基元+react-query 缓存内稳定数组引用(structural sharing),
  // 不能依赖外层内联组装的包装对象——否则父组件无关重渲染(如输入框逐键)会误重置
  useEffect(() => {
    setActiveType(normalizeType(chart.type));
  }, [chart.type, chart.x, chart.y, data.columns, data.rows]);

  const option = useMemo(() => {
    const xi = chart.x ? data.columns.indexOf(chart.x) : 0;
    const yi = chart.y ? data.columns.indexOf(chart.y) : 1;
    const labels = formatTemporalLabels(data.rows.map((r) => String(r[xi] ?? "")));
    const values = data.rows.map((r) => {
      const v = r[yi];
      return typeof v === "number" ? v : Number(v);
    });
    const axisCommon = {
      axisLabel: { color: axisColor },
      axisLine: { lineStyle: { color: lineColor } },
    };

    if (activeType === "pie") {
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
    const grid = { left: 12, right: 16, top: 24, bottom: 8, containLabel: true };
    if (activeType === "scatter") {
      const xNumeric =
        data.rows.length > 0 && data.rows.every((r) => isNumericCell(r[xi]));
      if (xNumeric) {
        return {
          grid,
          tooltip: {
            trigger: "item",
            // 列名+数值双行呈现(011 挂账⑨/013),与 bar/line 信息量对齐;
            // y=null 空值点 echarts 不渲染,无 tooltip 触发,?? "—" 纯防御
            formatter: (p: unknown) => {
              const v = (p as { value: [number, number | null] }).value;
              return `${chart.x ?? "x"}: ${v[0]}<br/>${chart.y ?? "y"}: ${v[1] ?? "—"}`;
            },
          },
          // scale:true:轴不含零起点,窄区间远离 0 的数据不挤角(散点看分布)
          xAxis: { type: "value", scale: true, ...axisCommon },
          yAxis: { type: "value", scale: true, ...axisCommon },
          series: [
            {
              type: "scatter",
              // y 空值/非数值映 null(echarts 跳过该点),防 Number(null)===0 落假点
              data: data.rows.map((r) => [
                Number(r[xi]),
                isNumericCell(r[yi]) ? Number(r[yi]) : null,
              ]),
              itemStyle: { color: palette[0] },
            },
          ],
        };
      }
      // 退化:类目轴点图(一行非数值即整体退化,宁退化不丢点;
      // y 沿用 values 的 null→0 既有语义,与 bar/line 同构)
      return {
        grid,
        tooltip: { trigger: "axis" },
        xAxis: { type: "category", data: labels, ...axisCommon },
        yAxis: { type: "value", ...axisCommon },
        series: [
          { type: "scatter", data: values, itemStyle: { color: palette[0] } },
        ],
      };
    }
    return {
      grid,
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: labels, ...axisCommon },
      yAxis: { type: "value", ...axisCommon },
      series: [
        activeType === "line"
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
  }, [activeType, chart, data, palette, axisColor, lineColor]);

  function downloadPng(): void {
    const inst = chartRef.current?.getEchartsInstance();
    if (!inst) return;
    // 背景取卡片底色 token 防透明底;pixelRatio 2 覆盖汇报贴图清晰度
    const url = inst.getDataURL({
      type: "png",
      pixelRatio: 2,
      backgroundColor: cssVar("--card"),
    });
    const d = new Date();
    const stamp = `${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}-${pad2(d.getHours())}${pad2(d.getMinutes())}${pad2(d.getSeconds())}`;
    // Safari 形态:锚点须入 DOM 再 click(010 教训);dataURL 非 blob 无需 revoke
    const a = document.createElement("a");
    a.href = url;
    a.download = `图表-${stamp}.png`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  return (
    <div>
      <div className="flex items-center justify-between px-2 pt-1">
        <ToggleGroup
          type="single"
          value={activeType}
          onValueChange={(v) => {
            if (v) setActiveType(v as ChartType); // 单选恒保留一个激活(取消选中不回空)
          }}
          variant="outline"
          size="sm"
          aria-label="图表类型"
        >
          {CHART_TYPES.map((t) => {
            const { label, Icon } = TYPE_META[t];
            return (
              <ToggleGroupItem key={t} value={t} aria-label={label} title={label}>
                <Icon aria-hidden />
              </ToggleGroupItem>
            );
          })}
        </ToggleGroup>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={downloadPng}
          aria-label="下载图表 PNG"
          title="下载 PNG"
        >
          <ImageDown aria-hidden />
        </Button>
      </div>
      <ReactECharts
        ref={chartRef}
        option={option}
        notMerge
        lazyUpdate
        style={{ height: 260, width: "100%" }}
        opts={{ renderer: "canvas" }}
      />
    </div>
  );
}
