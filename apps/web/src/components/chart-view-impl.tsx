import { useMemo, useRef } from "react";
import { ChartColumn, ChartLine, ChartPie, ChartScatter, ImageDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { EChart, themedTooltip } from "@/lib/echarts";
import { cssVar } from "@/lib/utils";
import type { ChartDatum, ChartSpec, ChartTune, ChartType } from "@/components/chart-shared";
import { uniqueTypes } from "@/components/chart-shared";

export interface ChartViewProps {
  /** 有序图表候选:每个候选自带完整绑定,活动绑定=同类型首个候选 */
  candidates: ChartSpec[];
  data: ChartDatum;
  tune?: ChartTune;
  /** 呈现层列标签(缺失/未命中回退物理列名)——多度量系列名用它,禁裸物理名直达用户 */
  labels?: string[];
  /** 当前图表类型(状态提升至 chart-view 壳:主题重挂不丢用户手动选择) */
  activeType: ChartType;
  onActiveTypeChange: (t: ChartType) => void;
}

const TYPE_META: Record<ChartType, { label: string; Icon: typeof ChartColumn }> = {
  bar: { label: "柱状图", Icon: ChartColumn },
  line: { label: "折线图", Icon: ChartLine },
  pie: { label: "饼图", Icon: ChartPie },
  scatter: { label: "散点图", Icon: ChartScatter },
};

/* 色板从面向数据的 token 派生;前景文字色不入系列(浅色底上近不可见) */
function chartPalette(): string[] {
  return [
    cssVar("--primary"),
    cssVar("--ring"),
    cssVar("--accent-foreground"),
    cssVar("--muted-foreground"),
  ];
}

/* 空值/空白串显式排除:Number(null)、Number("")、Number(" ") 均为 0,会把空单元格误判成数值 */
function isNumericCell(v: unknown): boolean {
  if (v === null || v === undefined || typeof v === "boolean") return false;
  if (typeof v === "string" && v.trim() === "") return false;
  return Number.isFinite(Number(v));
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/* 日期轴仅在整列均为 ISO 日期时格式化；混有其他值则整列原样显示，非零时间追加 HH:mm。 */
const ISO_DATE_RE = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/;

function formatTemporalLabels(labels: string[]): string[] {
  if (labels.length === 0 || !labels.every((l) => ISO_DATE_RE.test(l))) return labels;
  return labels.map((l) => {
    const m = ISO_DATE_RE.exec(l);
    if (!m) return l;
    return m[2] === "00" && m[3] === "00" ? m[1] : `${m[1]} ${m[2]}:${m[3]}`;
  });
}

export default function ChartViewImpl({
  candidates,
  data,
  tune,
  labels,
  activeType,
  onActiveTypeChange,
}: ChartViewProps) {
  const palette = useMemo(chartPalette, []);
  const axisColor = useMemo(() => cssVar("--muted-foreground"), []);
  const lineColor = useMemo(() => cssVar("--border"), []);
  const chartRef = useRef<EChart>(null);
  const toggleTypes = useMemo(() => uniqueTypes(candidates), [candidates]);

  const option = useMemo(() => {
    // 活动绑定:同类型取首个候选(切换类型即切换其完整 x/y/系列映射)
    const binding = candidates.find((c) => c.type === activeType) ?? candidates[0];
    const xi = binding.x ? data.columns.indexOf(binding.x) : 0;
    const yNames = binding.y.length > 0 ? binding.y : [data.columns[1] ?? ""];
    const yIdxs = yNames.map((n) => data.columns.indexOf(n)).filter((i) => i >= 0);
    const useYIdxs = yIdxs.length > 0 ? yIdxs : [1];
    const xLabels = formatTemporalLabels(data.rows.map((r) => String(r[xi] ?? "")));
    const axisCommon = {
      axisLabel: { color: axisColor },
      axisLine: { lineStyle: { color: lineColor } },
    };
    // 浮层配色走主题 token(暗色不现白底);themedTooltip 与统计页同源,勿另写
    const tooltipCommon = themedTooltip();
    // 标题非空才占位,防空标题压图
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
      // pie 恒单 y(候选生成保证;异常多 y 时取首个)
      const values = data.rows.map((r) => {
        const v = r[useYIdxs[0]];
        return typeof v === "number" ? v : Number(v);
      });
      return {
        ...titleOpt,
        tooltip: { trigger: "item", ...tooltipCommon },
        legend: { bottom: 0, left: "center", textStyle: { color: axisColor } },
        series: [
          {
            type: "pie",
            radius: ["38%", "66%"],
            center: ["50%", "44%"],
            data: xLabels.map((name, i) => ({ name, value: values[i] })),
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
      const yi = useYIdxs[0];
      const xNumeric = data.rows.length > 0 && data.rows.every((r) => isNumericCell(r[xi]));
      if (xNumeric) {
        return {
          ...titleOpt,
          grid,
          tooltip: {
            trigger: "item",
            ...tooltipCommon,
            // 列名和数值分两行,与 bar/line 图表信息量一致;
            // y=null 空值点 echarts 不渲染无 tooltip,?? "—" 纯防御
            formatter: (p: unknown) => {
              const v = (p as { value: [number, number | null] }).value;
              return `${binding.x ?? "x"}: ${v[0]}<br/>${binding.y[0] ?? "y"}: ${v[1] ?? "—"}`;
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
      // y 沿用单系列的 Number 既有语义,与 bar/line 同构)
      const values = data.rows.map((r) => {
        const v = r[yi];
        return typeof v === "number" ? v : Number(v);
      });
      return {
        ...titleOpt,
        grid,
        tooltip: { trigger: "axis", ...tooltipCommon },
        xAxis: { type: "category", data: xLabels, ...xNameOpt, ...axisCommon },
        yAxis: { type: "value", ...yNameOpt, ...axisCommon },
        series: [{ type: "scatter", data: values, itemStyle: { color: palette[0] } }],
      };
    }

    // bar/line:多系列=系列维绑定(分组柱/时序多系列)或多度量(y 列表);
    // 分组/多度量空值映 null(稀疏组合不落零柱),单系列沿用 Number 既有语义
    const si = binding.series ? data.columns.indexOf(binding.series) : -1;
    // 分组系列:类目轴=x 唯一值全集(首次出现序),各系列按「系列值×类目」
    // 对号取数、缺组合补 null——若逐行过滤,系列下标与类目下标错位,柱子会落错类别
    const hasSeries = si >= 0;
    const rawCats = [...new Set(data.rows.map((r) => String(r[xi] ?? "")))];
    const axisCats = formatTemporalLabels(rawCats);
    // 类目轴标签:少量横排,中量旋转,超过 24 项交给 ECharts 抽稀
    const catCount = hasSeries ? axisCats.length : xLabels.length;
    const catLabelOpt =
      catCount <= 12 ? { interval: 0 } : catCount <= 24 ? { interval: 0, rotate: 35 } : {};
    let seriesList: { name?: string; data: (number | null)[] }[];
    if (hasSeries) {
      const lookup = new Map<string, number | null>();
      for (const r of data.rows) {
        const v = r[useYIdxs[0]];
        lookup.set(
          `${String(r[si] ?? "")}||${String(r[xi] ?? "")}`,
          isNumericCell(v) ? Number(v) : null,
        );
      }
      const seriesVals = [...new Set(data.rows.map((r) => String(r[si] ?? "")))];
      seriesList = seriesVals.map((sv) => ({
        name: sv,
        data: rawCats.map((cat) => lookup.get(`${sv}||${cat}`) ?? null),
      }));
    } else if (useYIdxs.length > 1) {
      seriesList = useYIdxs.map((yi) => ({
        // 双轨:系列名经列标签(缺失回退物理列名),不裸投物理名
        name: labels?.[yi] || data.columns[yi],
        data: data.rows.map((r) => {
          const v = r[yi];
          return isNumericCell(v) ? Number(v) : null;
        }),
      }));
    } else {
      seriesList = [
        {
          data: data.rows.map((r) => {
            const v = r[useYIdxs[0]];
            return typeof v === "number" ? v : Number(v);
          }),
        },
      ];
    }
    const multi = seriesList.length > 1;
    const legendOpt = multi
      ? { legend: { bottom: 0, left: "center", textStyle: { color: axisColor } } }
      : {};
    // 底部图例占位:压 8px 底距为 28px,防图例与轴标签重叠
    const gridFinal = multi ? { ...grid, bottom: 28 } : grid;
    // 全整数量测(计数类)→ minInterval 1:小值域不产生 0.5/1.5 半数碎刻度
    const allInts = seriesList.every((s) => s.data.every((v) => v === null || Number.isInteger(v)));
    return {
      ...titleOpt,
      ...legendOpt,
      grid: gridFinal,
      tooltip: { trigger: "axis", ...tooltipCommon },
      xAxis: {
        type: "category",
        data: hasSeries ? axisCats : xLabels,
        axisLabel: { ...catLabelOpt, ...axisCommon.axisLabel },
        ...xNameOpt,
        axisLine: axisCommon.axisLine,
      },
      yAxis: {
        type: "value",
        ...(allInts ? { minInterval: 1 } : {}),
        ...yNameOpt,
        ...axisCommon,
      },
      series: seriesList.map((s, k) =>
        activeType === "line"
          ? {
              type: "line",
              name: s.name,
              data: s.data,
              smooth: true,
              itemStyle: { color: palette[k % palette.length] },
              lineStyle: { color: palette[k % palette.length], width: 2 },
            }
          : {
              type: "bar",
              name: s.name,
              data: s.data,
              itemStyle: { color: palette[k % palette.length], borderRadius: [3, 3, 0, 0] },
              barMaxWidth: 40,
            },
      ),
    };
    // tune 基元展开入依赖:标题/轴名一改即重算重绘
  }, [
    candidates,
    activeType,
    data,
    labels,
    palette,
    axisColor,
    lineColor,
    tune?.title,
    tune?.xName,
    tune?.yName,
  ]);

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
    // Safari 形态:锚点须入 DOM 再 click;dataURL 非 blob 无需 revoke
    const a = document.createElement("a");
    a.href = url;
    a.download = `图表-${stamp}.png`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  return (
    <div>
      <div
        className={`flex items-center px-2 pt-1 ${
          toggleTypes.length > 1 ? "justify-between" : "justify-end"
        }`}
      >
        {toggleTypes.length > 1 && (
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
            {toggleTypes.map((t) => {
              const { label, Icon } = TYPE_META[t];
              return (
                <ToggleGroupItem key={t} value={t} aria-label={label} title={label}>
                  <Icon aria-hidden />
                </ToggleGroupItem>
              );
            })}
          </ToggleGroup>
        )}
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
