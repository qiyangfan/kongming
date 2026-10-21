/* 只注册应用使用的图表系列和组件，避免载入 ECharts 完整入口。 */
import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
// Vite 8(Rolldown)CJS 互操作统一:lib/ 构建无 __esModule 标记,默认导入
// 不再解包 exports.default(类拿到手是命名空间对象,extends 即炸)——改用
// 官方 ESM 构建 esm/(导出语义同 lib/,形态等价)
import EChartsReactCore from "echarts-for-react/esm/core";
import type { EChartsReactProps } from "echarts-for-react/esm/types";

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  ScatterChart,
  GridComponent,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
]);

class EChart extends EChartsReactCore {
  constructor(props: EChartsReactProps) {
    super({ ...props, echarts });
  }
}

/* Tooltip 颜色来自 CSS 变量，主题切换后重新生成图表选项即可更新。 */
export function themedTooltip() {
  const css = (name: string) =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return {
    backgroundColor: css("--card"),
    borderColor: css("--border"),
    textStyle: { color: css("--foreground") },
  };
}

export { EChart };
