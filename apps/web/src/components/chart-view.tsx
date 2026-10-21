/* 图表引擎通过动态导入按需加载；主题切换重挂渲染器时保留用户选择的图表类型。 */

import { lazy, Suspense, useEffect, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useTheme } from "@/lib/theme-store";
import type { ChartDatum, ChartSpec, ChartTune, ChartType } from "@/components/chart-shared";
import { normalizeType } from "@/components/chart-shared";

export type { ChartDatum, ChartTune } from "@/components/chart-shared";

const ChartViewImpl = lazy(() => import("./chart-view-impl"));

export function ChartView({
  candidates,
  data,
  tune,
  labels,
}: {
  candidates: ChartSpec[];
  data: ChartDatum;
  tune?: ChartTune;
  /** 呈现层列标签(与 data.columns 等长;缺失回退物理列名)——多度量系列名/调参占位用 */
  labels?: string[];
}) {
  const { theme } = useTheme();
  const [activeType, setActiveType] = useState<ChartType>(() =>
    candidates.length > 0 ? normalizeType(candidates[0].type) : "bar",
  );
  // 回落语义(spec):新结果进入/组件实例复用时重置回后端首候选。
  // 依赖取基元(首候选类型+绑定指纹)+react-query 缓存内稳定数组引用
  // (structural sharing),不能依赖外层内联组装的包装对象——否则父组件
  // 无关重渲染(如输入框逐键)会误重置
  const firstType = candidates.length > 0 ? normalizeType(candidates[0].type) : "bar";
  const fingerprint = candidates
    .map((c) => `${c.type}:${c.x ?? ""}:${c.y.join(",")}:${c.series ?? ""}`)
    .join("|");
  useEffect(() => {
    setActiveType(firstType);
  }, [firstType, fingerprint]);

  if (candidates.length === 0) return null; // 零候选:表格即答案
  return (
    <Suspense fallback={<Skeleton className="h-72 w-full" />}>
      <ChartViewImpl
        key={theme}
        activeType={activeType}
        onActiveTypeChange={setActiveType}
        candidates={candidates}
        data={data}
        tune={tune}
        labels={labels}
      />
    </Suspense>
  );
}
