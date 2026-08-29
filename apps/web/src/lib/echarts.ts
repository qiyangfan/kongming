/* echarts 按需注册单源(体积治理):全站只用 line/bar/pie/scatter 四系列与
   title/tooltip/legend/grid 组件,经 echarts/core 注册后体积约为全量入口一半。
   echarts-for-react v3 的 core 基类不自带引擎,靠 props.echarts 注入实现;
   其默认入口(lib/index.js)注入的是全量包,会把整包引擎拉回 chunk,禁用——
   此处在子类构造时注入按需注册后的核心包。新增图表能力时在此补注册;
   禁止任何模块直接 import "echarts" 全量入口。 */
import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import EChartsReactCore from "echarts-for-react/lib/core";
import type { EChartsReactProps } from "echarts-for-react/lib/types";

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

/* 统计图 tooltip 主题适配单源(040 theme-switch):浮层底/边框/文字取 token,
   防 ECharts 默认白底浮层在暗色页面刺眼;调用方在 option 的 tooltip 内展开。
   实时读 CSS 变量——key={theme} 重挂后重调即得新主题值。 */
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
