/* 图表共享契约(零 echarts 依赖,体积治理边界):
   chart-view 壳(懒加载边界)静态导入本模块不含任何引擎代码;
   实现层 chart-view-impl 亦从此复用。新增共享契约放这里,禁止让壳
   直接 import chart-view-impl(会把 echarts 拖回首屏主包)。 */

export const CHART_TYPES = ["bar", "line", "pie", "scatter"] as const;
export type ChartType = (typeof CHART_TYPES)[number];

/* 图表绑定(049 候选化):候选自带完整映射——y 为值列(多度量多系列),
   series 为系列维(分组柱/时序多系列)。存量单图经 normalizeBinding 收为
   y 数组形态,两代数据同构进渲染层。 */
export interface ChartSpec {
  type: string;
  x: string | null;
  y: string[];
  series: string | null;
}

export interface ChartDatum {
  columns: string[];
  rows: unknown[][];
}

/* 026 调参(artifact chart 卡):标题/轴名本地即时重绘,不调 LLM;
   缺省空串=不渲染对应元素,与既有调用方零行为差 */
export interface ChartTune {
  title: string;
  xName: string;
  yName: string;
}

export function normalizeType(t: string): ChartType {
  return (CHART_TYPES as readonly string[]).includes(t) ? (t as ChartType) : "bar";
}

/* 存量单图(type/x/y 单值,049 前档)→ 绑定形态;type 为 none/空 → null(不配图) */
export function normalizeBinding(chart: {
  type: string;
  x: string | null;
  y: string | null;
}): ChartSpec | null {
  if (!chart.type || chart.type === "none") return null;
  return { type: chart.type, x: chart.x, y: chart.y ? [chart.y] : [], series: null };
}

/* 候选类型去重(每类型取首个候选,保持候选序)——切换器按类型渲染 */
export function uniqueTypes(candidates: ChartSpec[]): ChartType[] {
  const seen = new Set<ChartType>();
  const out: ChartType[] = [];
  for (const c of candidates) {
    const t = normalizeType(c.type);
    if (!seen.has(t)) {
      seen.add(t);
      out.push(t);
    }
  }
  return out;
}
