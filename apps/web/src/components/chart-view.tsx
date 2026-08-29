/* 图表懒加载边界(体积治理):echarts 约占全站 JS 一半,只在本文件动态 import,
   引擎 chunk 随图表首次渲染按需下载,首屏主包不含图表引擎。
   主题联动(040)以 key 重挂实现,重挂边界=本组件以下:色板等 token 派生值随
   重挂重读;用户手动选定的图表类型(activeType)提升至本组件持有,切主题重挂
   不丢失用户选择。对外 API 与类型不变,实现与图表语义见 chart-view-impl.tsx。 */

import { lazy, Suspense, useEffect, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useTheme } from "@/lib/theme-store";
import type { ChartDatum, ChartSpec, ChartTune, ChartType } from "@/components/chart-shared";
import { normalizeType } from "@/components/chart-shared";

export type { ChartDatum, ChartTune } from "@/components/chart-shared";

const ChartViewImpl = lazy(() => import("./chart-view-impl"));

export function ChartView({
  chart,
  data,
  tune,
}: {
  chart: ChartSpec;
  data: ChartDatum;
  tune?: ChartTune;
}) {
  const { theme } = useTheme();
  const [activeType, setActiveType] = useState<ChartType>(() => normalizeType(chart.type));
  // 回落语义(spec):新结果进入/组件实例复用时重置回后端建议类型。
  // 依赖取基元+react-query 缓存内稳定数组引用(structural sharing),
  // 不能依赖外层内联组装的包装对象——否则父组件无关重渲染(如输入框逐键)会误重置
  useEffect(() => {
    setActiveType(normalizeType(chart.type));
  }, [chart.type, chart.x, chart.y, data.columns, data.rows]);

  return (
    <Suspense fallback={<Skeleton className="h-72 w-full" />}>
      <ChartViewImpl
        key={theme}
        activeType={activeType}
        onActiveTypeChange={setActiveType}
        chart={chart}
        data={data}
        tune={tune}
      />
    </Suspense>
  );
}
