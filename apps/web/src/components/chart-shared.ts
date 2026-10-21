/* 图表共用契约放在此文件，零 echarts 依赖；懒加载壳 chart-view 禁止静态导入实现层 chart-view-impl，
   否则 echarts 会进入首屏包（壳只经 lazy() 动态加载）；实现层与壳均从此复用。 */

export const CHART_TYPES = ["bar", "line", "pie", "scatter"] as const;
export type ChartType = (typeof CHART_TYPES)[number];

/* 每个图表候选包含值列和系列维度；旧版单图由 normalizeBinding 转成相同结构。 */
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

/* 图表标题和轴名只在本地重绘；空字符串表示不显示对应元素。 */
export interface ChartTune {
  title: string;
  xName: string;
  yName: string;
}

export function normalizeType(t: string): ChartType {
  return (CHART_TYPES as readonly string[]).includes(t) ? (t as ChartType) : "bar";
}

/* 将旧版单图配置转换为绑定结构；类型为空或 none 时返回 null。 */
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
