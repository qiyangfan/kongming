/* 图表渲染实现(web-nav spec「结果图表渲染」+ 011 图表增强):
   bar/line/pie/scatter 四类型;scatter 双形态(x 列全数值→数值散点,否则退化类目
   点图——value 轴遇 NaN 静默丢点为上游实证坑);手动切换=卡片本地状态,映射恒定,
   prop 变更回落建议类型;色板从设计 token 派生(铁律 6,禁硬编码)。
   本文件是懒加载动态目标(echarts 引擎不进首屏主包),对外经由 chart-view.tsx 壳。 */

import { useMemo, useRef } from "react";
import { ChartColumn, ChartLine, ChartPie, ChartScatter, ImageDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { EChart, themedTooltip } from "@/lib/echarts";
import type { ChartDatum, ChartSpec, ChartTune, ChartType } from "@/components/chart-shared";
import { CHART_TYPES } from "@/components/chart-shared";

export interface ChartViewProps {
  chart: ChartSpec;
  data: ChartDatum;
  tune?: ChartTune;
  /** 当前图表类型(状态提升至 chart-view 壳:主题重挂不丢用户手动选择,040) */
  activeType: ChartType;
  onActiveTypeChange: (t: ChartType) => void;
}

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

export default function ChartViewImpl({ chart, data, tune, activeType, onActiveTypeChange }: ChartViewProps) {
  const palette = useMemo(chartPalette, []);
  const axisColor = useMemo(() => cssVar("--muted-foreground"), []);
  const lineColor = useMemo(() => cssVar("--border"), []);
  const chartRef = useRef<EChart>(null);

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
    // 浮层 token 色:默认白底 tooltip 在暗色页面里刺眼(040 主题联动);
    // 构造单源 lib/echarts.themedTooltip(与两统计页同源,禁第二份)
    const tooltipCommon = themedTooltip();
    // 026 调参:标题非空才占位(顶部留白随有无标题切换,防空标题压图)
    const titleOpt = tune?.title
      ? {
          title: {
            text: tune.title,
            left: "center",
            top: 4,
            textStyle: { color: axisColor, fontSize: 13, fontWeight: 500 },
          },
        }
      : {};
    const xNameOpt = tune?.xName ? { name: tune.xName, nameTextStyle: { color: axisColor } } : {};
    const yNameOpt = tune?.yName ? { name: tune.yName, nameTextStyle: { color: axisColor } } : {};

    if (activeType === "pie") {
      return {
        ...titleOpt,
        tooltip: { trigger: "item", ...tooltipCommon },
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
    const grid = {
      left: 12,
      right: 16,
      top: tune?.title ? 36 : 24,
      bottom: 8,
      containLabel: true,
    };
    if (activeType === "scatter") {
      const xNumeric =
        data.rows.length > 0 && data.rows.every((r) => isNumericCell(r[xi]));
      if (xNumeric) {
        return {
          ...titleOpt,
          grid,
          tooltip: {
            trigger: "item",
            ...tooltipCommon,
            // 列名+数值双行呈现(011 挂账⑨/013),与 bar/line 信息量对齐;
            // y=null 空值点 echarts 不渲染,无 tooltip 触发,?? "—" 纯防御
            formatter: (p: unknown) => {
              const v = (p as { value: [number, number | null] }).value;
              return `${chart.x ?? "x"}: ${v[0]}<br/>${chart.y ?? "y"}: ${v[1] ?? "—"}`;
            },
          },
          // scale:true:轴不含零起点,窄区间远离 0 的数据不挤角(散点看分布)
          xAxis: { type: "value", scale: true, ...xNameOpt, ...axisCommon },
          yAxis: { type: "value", scale: true, ...yNameOpt, ...axisCommon },
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
        ...titleOpt,
        grid,
        tooltip: { trigger: "axis", ...tooltipCommon },
        xAxis: { type: "category", data: labels, ...xNameOpt, ...axisCommon },
        yAxis: { type: "value", ...yNameOpt, ...axisCommon },
        series: [
          { type: "scatter", data: values, itemStyle: { color: palette[0] } },
        ],
      };
    }
    return {
      ...titleOpt,
      grid,
      tooltip: { trigger: "axis", ...tooltipCommon },
      xAxis: { type: "category", data: labels, ...xNameOpt, ...axisCommon },
      yAxis: { type: "value", ...yNameOpt, ...axisCommon },
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
    // tune 基元展开入依赖(标题/轴名一改即重算重绘,026 调参语义)
  }, [activeType, chart, data, palette, axisColor, lineColor, tune?.title, tune?.xName, tune?.yName]);

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
            if (v) onActiveTypeChange(v as ChartType); // 单选恒保留一个激活(取消选中不回空)
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
          aria-label="下载图表图片"
          title="下载图片"
        >
          <ImageDown aria-hidden />
        </Button>
      </div>
      <EChart
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
