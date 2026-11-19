/* 反馈页的顶层区块:趋势折线、统计卡与筛选栏。 */

import { useMemo } from "react";

import { EChart, themedTooltip } from "@/lib/echarts";
import { useTheme } from "@/lib/theme-store";
import { StatCard } from "@/components/stat-card";
import { ErrorAlert } from "@/components/error-alert";
import { cssVar } from "@/lib/utils";
import { feedbackOverview } from "@/lib/feedback-api";

type FeedbackTrendPoint = Awaited<ReturnType<typeof feedbackOverview>>["trend"][number];
type OverviewData = Awaited<ReturnType<typeof feedbackOverview>>;

type Stats = { isLoading: boolean; isError: boolean; data?: OverviewData };

/* 趋势折线(theme 经 key 作用于本组件:切主题整体重挂,option 重读 token 色板) */
function FeedbackTrendChart({ trend }: { trend: FeedbackTrendPoint[] }) {
  const option = useMemo(() => {
    const axisColor = cssVar("--muted-foreground");
    const lineColor = cssVar("--border");
    const axisCommon = {
      axisLabel: { color: axisColor },
      axisLine: { lineStyle: { color: lineColor } },
    };
    return {
      tooltip: { trigger: "axis" as const, ...themedTooltip() },
      legend: { bottom: 0, left: "center", textStyle: { color: axisColor } },
      grid: { left: 12, right: 16, top: 24, bottom: 28, containLabel: true },
      xAxis: {
        type: "category" as const,
        data: trend.map((t) => t.day.slice(5)), // MM-DD 省宽
        ...axisCommon,
      },
      yAxis: { type: "value" as const, minInterval: 1, ...axisCommon },
      series: [
        {
          name: "赞",
          type: "line" as const,
          data: trend.map((t) => t.up),
          smooth: true,
          itemStyle: { color: cssVar("--ring") },
          lineStyle: { color: cssVar("--ring"), width: 2 },
        },
        {
          name: "踩",
          type: "line" as const,
          data: trend.map((t) => t.down),
          smooth: true,
          itemStyle: { color: cssVar("--primary") },
          lineStyle: { color: cssVar("--primary"), width: 2 },
        },
      ],
    };
  }, [trend]);
  return (
    <EChart
      option={option}
      notMerge
      lazyUpdate
      style={{ height: 220, width: "100%" }}
      opts={{ renderer: "canvas" }}
    />
  );
}

/** 统计卡数值加载/错误态折叠(loading 显 …、错误显 —) */
function statValue(src: Stats) {
  return (v: number | string | undefined): number | string =>
    src.isLoading ? "…" : src.isError ? "—" : (v ?? 0);
}

export function FeedbackStats({ overview }: { overview: Stats }) {
  const { theme } = useTheme();
  const fbStat = statValue(overview);
  const ov = overview.data;
  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <StatCard label="反馈总数" value={fbStat(ov?.total)} />
        <StatCard label="有用(赞)" value={fbStat(ov?.up_count)} />
        <StatCard label="不准确(踩)" value={fbStat(ov?.down_count)} />
        <StatCard
          label="差评率"
          value={fbStat(ov ? `${(ov.down_rate * 100).toFixed(1)}%` : undefined)}
        />
        <StatCard label="带纠错" value={fbStat(ov?.corrected_count)} />
      </div>

      <div className="rounded-lg border border-border bg-card px-2 py-1">
        {overview.isError ? (
          <ErrorAlert />
        ) : (
          <FeedbackTrendChart key={theme} trend={overview.data?.trend ?? []} />
        )}
      </div>
    </>
  );
}
