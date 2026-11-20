/* 图表 option 构造：按活动类型（柱/线/饼/散点）由候选绑定与数据行生成 ECharts 配置。
   放在 lib 下以便纯函数单测；颜色经 token 派生，无 echarts 运行时依赖。 */

import { themedTooltip } from "@/lib/echarts";
import { cellText } from "@/lib/utils";
import type { ChartDatum, ChartSpec, ChartTune, ChartType } from "@/lib/chart-shared";

/* 空值/空白串显式排除:Number(null)、Number("")、Number(" ") 均为 0,会把空单元格误判成数值 */
function isNumericCell(v: unknown): boolean {
  if (v === null || v === undefined || typeof v === "boolean") return false;
  if (typeof v === "string" && v.trim() === "") return false;
  return Number.isFinite(Number(v));
}

/* 日期轴仅在整列均为 ISO 日期时格式化；混有其他值则整列原样显示，非零时间追加 HH:mm。 */
const ISO_DATE_RE = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/;

function formatTemporalLabels(labels: string[]): string[] {
  if (labels.length === 0 || !labels.every((l) => ISO_DATE_RE.test(l))) return labels;
  return labels.map((l) => {
    const m = ISO_DATE_RE.exec(l);
    if (!m) return l;
    return m[2] === "00" && m[3] === "00" ? (m[1] ?? l) : `${m[1] ?? l} ${m[2]}:${m[3]}`;
  });
}

/* 类目轴标签密度：少量横排，中量旋转，超过 24 项交给 ECharts 抽稀。 */
function categoryLabelOption(catCount: number): { interval?: number; rotate?: number } {
  if (catCount <= 12) return { interval: 0 };
  if (catCount <= 24) return { interval: 0, rotate: 35 };
  return {};
}

/* 单系列数值列：沿用 Number 既有语义（空单元格落 0）。 */
function plainValues(rows: unknown[][], idx: number): number[] {
  return rows.map((r) => {
    const v = r[idx];
    return typeof v === "number" ? v : Number(v);
  });
}

/* 稀疏系列数值列：空值/非数值映 null（不落零柱）。 */
function sparseValues(rows: unknown[][], idx: number): (number | null)[] {
  return rows.map((r) => {
    const v = r[idx];
    return isNumericCell(v) ? Number(v) : null;
  });
}

type BuildCtx = {
  binding: ChartSpec;
  data: ChartDatum;
  xi: number;
  useYIdxs: number[];
  xLabels: string[];
  rawCats: string[];
  palette: string[];
  axisColor: string;
  axisCommon: { axisLabel: { color: string }; axisLine: { lineStyle: { color: string } } };
  titleOpt: Record<string, unknown>;
  xNameOpt: Record<string, unknown>;
  yNameOpt: Record<string, unknown>;
  grid: Record<string, unknown>;
};

/* 饼图：恒单 y（候选生成保证；异常多 y 时取首个）。 */
function pieOption(ctx: BuildCtx): Record<string, unknown> {
  const { data, useYIdxs, xLabels, palette, axisColor, titleOpt } = ctx;
  const values = plainValues(data.rows, useYIdxs[0] ?? 0);
  return {
    ...titleOpt,
    tooltip: { trigger: "item", ...themedTooltip() },
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

/* 散点的数值 x 形态：轴不含零起点，y 空值映 null（echarts 跳过该点）。 */
function numericScatterOption(ctx: BuildCtx): Record<string, unknown> {
  const { binding, data, xi, useYIdxs, palette, axisCommon, titleOpt, xNameOpt, yNameOpt, grid } =
    ctx;
  const yi = useYIdxs[0] ?? 0;
  return {
    ...titleOpt,
    grid,
    tooltip: {
      trigger: "item",
      ...themedTooltip(),
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
        data: data.rows.map((r) => [Number(r[xi]), isNumericCell(r[yi]) ? Number(r[yi]) : null]),
        itemStyle: { color: palette[0] },
      },
    ],
  };
}

/* 散点的类目轴退化形态：一行非数值即整体退化,宁退化不丢点。 */
function categoryScatterOption(ctx: BuildCtx): Record<string, unknown> {
  const { data, useYIdxs, xLabels, palette, axisCommon, titleOpt, xNameOpt, yNameOpt, grid } = ctx;
  return {
    ...titleOpt,
    grid,
    tooltip: { trigger: "axis", ...themedTooltip() },
    xAxis: { type: "category", data: xLabels, ...xNameOpt, ...axisCommon },
    yAxis: { type: "value", ...yNameOpt, ...axisCommon },
    series: [
      {
        type: "scatter",
        data: plainValues(data.rows, useYIdxs[0] ?? 0),
        itemStyle: { color: palette[0] },
      },
    ],
  };
}

function scatterOption(ctx: BuildCtx): Record<string, unknown> {
  const { data, xi } = ctx;
  const xNumeric = data.rows.length > 0 && data.rows.every((r) => isNumericCell(r[xi]));
  return xNumeric ? numericScatterOption(ctx) : categoryScatterOption(ctx);
}

type SeriesEntry = { name?: string | undefined; data: (number | null)[] };

/* 分组系列：类目轴=x 唯一值全集(首次出现序),各系列按「系列值×类目」
   对号取数、缺组合补 null——若逐行过滤,系列下标与类目下标错位,柱子会落错类别 */
function groupedSeries(ctx: BuildCtx & { si: number; rawCats: string[] }): SeriesEntry[] {
  const { data, si, xi, useYIdxs, rawCats } = ctx;
  const lookup = new Map<string, number | null>();
  for (const r of data.rows) {
    lookup.set(
      `${cellText(r[si])}||${cellText(r[xi])}`,
      sparseValues([r], useYIdxs[0] ?? 0)[0] ?? null,
    );
  }
  const seriesVals = [...new Set(data.rows.map((r) => cellText(r[si])))];
  return seriesVals.map((sv) => ({
    name: sv,
    data: rawCats.map((cat) => lookup.get(`${sv}||${cat}`) ?? null),
  }));
}

/* 多度量系列：系列名经列标签(缺失回退物理列名),不裸投物理名。 */
function measureSeries(
  ctx: BuildCtx & { labels?: string[] | undefined; multiY: number[] },
): SeriesEntry[] {
  const { data, labels, multiY } = ctx;
  return multiY.map((yi) => ({
    name: labels?.[yi] || data.columns[yi] || "",
    data: sparseValues(data.rows, yi),
  }));
}

function cartesianSeries(ctx: BuildCtx & { labels?: string[] | undefined }): {
  seriesList: SeriesEntry[];
  hasSeries: boolean;
} {
  const { binding, data, xi, useYIdxs } = ctx;
  const si = binding.series ? data.columns.indexOf(binding.series) : -1;
  const rawCats = [...new Set(data.rows.map((r) => cellText(r[xi])))];
  if (si >= 0) return { seriesList: groupedSeries({ ...ctx, si, rawCats }), hasSeries: true };
  if (useYIdxs.length > 1) {
    return { seriesList: measureSeries({ ...ctx, multiY: useYIdxs }), hasSeries: false };
  }
  return { seriesList: [{ data: plainValues(data.rows, useYIdxs[0] ?? 0) }], hasSeries: false };
}

/* 系列样式：折线平滑带线色，柱状圆角限宽；颜色按系列序循环取色板。 */
function seriesStyle(
  activeType: ChartType,
  s: SeriesEntry,
  style: { index: number; palette: string[] },
) {
  const color = style.palette[style.index % style.palette.length];
  return activeType === "line"
    ? {
        type: "line",
        name: s.name,
        data: s.data,
        smooth: true,
        itemStyle: { color },
        lineStyle: { color, width: 2 },
      }
    : {
        type: "bar",
        name: s.name,
        data: s.data,
        itemStyle: { color, borderRadius: [3, 3, 0, 0] },
        barMaxWidth: 40,
      };
}

/* 柱图/折线：多系列=系列维绑定(分组柱/时序多系列)或多度量(y 列表)。 */
function cartesianOption(ctx: BuildCtx, activeType: ChartType): Record<string, unknown> {
  const { axisColor, xLabels, palette, axisCommon, titleOpt, xNameOpt, yNameOpt, grid } = ctx;
  const { seriesList, hasSeries } = cartesianSeries(ctx);
  const axisCats = formatTemporalLabels(ctx.rawCats);
  const multi = seriesList.length > 1;
  const legendOpt = multi
    ? { legend: { bottom: 0, left: "center", textStyle: { color: axisColor } } }
    : {};
  // 底部图例占位:压 8px 底距为 28px,防图例与轴标签重叠
  const gridFinal = multi ? { ...grid, bottom: 28 } : grid;
  // 全整数量测(计数类)→ minInterval 1:小值域不产生 0.5/1.5 半数碎刻度
  const allInts = seriesList.every((s) => s.data.every((v) => v === null || Number.isInteger(v)));
  // 类目轴标签:少量横排,中量旋转,超过 24 项交给 ECharts 抽稀
  const catCount = hasSeries ? axisCats.length : xLabels.length;
  return {
    ...titleOpt,
    ...legendOpt,
    grid: gridFinal,
    tooltip: { trigger: "axis", ...themedTooltip() },
    xAxis: {
      type: "category",
      data: hasSeries ? axisCats : xLabels,
      axisLabel: { ...categoryLabelOption(catCount), ...axisCommon.axisLabel },
      ...xNameOpt,
      axisLine: axisCommon.axisLine,
    },
    yAxis: {
      type: "value",
      ...(allInts ? { minInterval: 1 } : {}),
      ...yNameOpt,
      ...axisCommon,
    },
    series: seriesList.map((s, k) => seriesStyle(activeType, s, { index: k, palette })),
  };
}

type BuildChartOptionArgs = {
  candidates: ChartSpec[];
  data: ChartDatum;
  labels?: string[] | undefined;
  tune?: ChartTune | undefined;
  activeType: ChartType;
  palette: string[];
  axisColor: string;
  lineColor: string;
};

/* 标题非空才占位,防空标题压图 */
function titleOption(tune: ChartTune | undefined, axisColor: string): Record<string, unknown> {
  if (!tune?.title) return {};
  return {
    title: {
      text: tune.title,
      left: "center",
      top: 4,
      textStyle: { color: axisColor, fontSize: 13, fontWeight: 500 },
    },
  };
}

/* 轴名非空才占位;标题存在时图区上移让出标题高度 */
function axisNameOptions(
  tune: ChartTune | undefined,
  axisColor: string,
): {
  xNameOpt: Record<string, unknown>;
  yNameOpt: Record<string, unknown>;
  grid: Record<string, unknown>;
} {
  const nameOpt = (name: string | undefined) =>
    name ? { name, nameTextStyle: { color: axisColor } } : {};
  return {
    xNameOpt: nameOpt(tune?.xName),
    yNameOpt: nameOpt(tune?.yName),
    grid: {
      left: 12,
      right: 16,
      top: tune?.title ? 36 : 24,
      bottom: 8,
      containLabel: true,
    },
  };
}

/* 由绑定解析出横轴列下标、生效的 y 列下标、横轴值与类目全集。 */
function resolveAxes(
  binding: ChartSpec,
  data: ChartDatum,
): { xi: number; useYIdxs: number[]; xLabels: string[]; rawCats: string[] } {
  const xi = binding.x ? data.columns.indexOf(binding.x) : 0;
  const yNames = binding.y.length > 0 ? binding.y : [data.columns[1] ?? ""];
  const yIdxs = yNames.map((n) => data.columns.indexOf(n)).filter((i) => i >= 0);
  return {
    xi,
    useYIdxs: yIdxs.length > 0 ? yIdxs : [1],
    xLabels: formatTemporalLabels(data.rows.map((r) => cellText(r[xi]))),
    // 分组系列:类目轴=x 唯一值全集(首次出现序)
    rawCats: [...new Set(data.rows.map((r) => cellText(r[xi])))],
  };
}

/* 生成当前活动类型的 ECharts option；tune 基元（标题/轴名）已由调用方展开进依赖。 */
export function buildChartOption({
  candidates,
  data,
  labels,
  tune,
  activeType,
  palette,
  axisColor,
  lineColor,
}: BuildChartOptionArgs): Record<string, unknown> {
  // 活动绑定:同类型取首个候选(切换类型即切换其完整 x/y/系列映射)
  const binding = candidates.find((c) => c.type === activeType) ?? candidates[0];
  if (!binding) return {}; // 无候选时无 option 可建(调用方按空图处理)
  const axisCommon = {
    axisLabel: { color: axisColor },
    axisLine: { lineStyle: { color: lineColor } },
  };
  const ctx: BuildCtx & { labels?: string[] | undefined } = {
    binding,
    data,
    labels,
    ...resolveAxes(binding, data),
    palette,
    axisColor,
    axisCommon,
    titleOpt: titleOption(tune, axisColor),
    ...axisNameOptions(tune, axisColor),
  };
  const builders: Partial<Record<ChartType, (c: BuildCtx) => Record<string, unknown>>> = {
    pie: pieOption,
    scatter: scatterOption,
  };
  const builder = builders[activeType];
  return builder ? builder(ctx) : cartesianOption(ctx, activeType);
}
