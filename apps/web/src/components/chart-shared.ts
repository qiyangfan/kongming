/* 图表共享契约(零 echarts 依赖,体积治理边界):
   chart-view 壳(懒加载边界)静态导入本模块不含任何引擎代码;
   实现层 chart-view-impl 亦从此复用。新增共享契约放这里,禁止让壳
   直接 import chart-view-impl(会把 echarts 拖回首屏主包)。 */

export const CHART_TYPES = ["bar", "line", "pie", "scatter"] as const;
export type ChartType = (typeof CHART_TYPES)[number];

export interface ChartSpec {
  type: string;
  x: string | null;
  y: string | null;
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
