/* 图表懒加载边界(体积治理):echarts 约占全站 JS 一半,只在本文件动态 import,
   引擎 chunk 随图表首次渲染按需下载,首屏主包不含图表引擎。
   主题联动(040)以 key 重挂实现,重挂边界=本组件以下:色板等 token 派生值随
   重挂重读;活动图表类型(activeType)提升至本组件持有,切主题重挂不丢失用户
   选择。049 候选化:候选为空不渲染(表格即答案),回落目标=首候选。 */

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
  /** 041 呈现层列标签(与 data.columns 等长;缺失回退物理列名)——多度量系列名/调参占位用 */
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
