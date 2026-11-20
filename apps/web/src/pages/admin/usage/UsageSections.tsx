/* 用量页的趋势图、汇总卡、按天趋势区块与「模型 × 任务」汇总表。 */

import { useMemo } from "react";

import { EChart, themedTooltip } from "@/lib/echarts";
import { useTheme } from "@/lib/theme-store";
import { Badge } from "@/components/ui/badge";
import { DataView } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { StatCard } from "@/components/stat-card";
import { usageSummary, usageTrend } from "@/lib/usage-api";
import { cssVar } from "@/lib/utils";
import { formatDuration } from "@/lib/datetime";

import { fmtTokens } from "./usage-format";

type UsageTrendPoint = Awaited<ReturnType<typeof usageTrend>>["points"][number];
type Totals = { calls: number; tokens: number; errorRate: number; latAvg: number | null };
type SummarySource = {
  isLoading: boolean;
  isError: boolean;
  data?: Awaited<ReturnType<typeof usageSummary>> | undefined;
};

/* 按天趋势(theme 经 key 作用于本组件:切主题整体重挂,option 重读 token 色板) */
function UsageTrendChart({ points }: { points: UsageTrendPoint[] }) {
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
        data: points.map((p) => p.date.slice(5)), // MM-DD 省宽
        ...axisCommon,
      },
      yAxis: [
        { type: "value" as const, minInterval: 1, ...axisCommon },
        { type: "value" as const, ...axisCommon },
      ],
      series: [
        {
          name: "调用",
          type: "line" as const,
          data: points.map((p) => p.calls),
          smooth: true,
          itemStyle: { color: cssVar("--ring") },
          lineStyle: { color: cssVar("--ring"), width: 2 },
        },
        {
          name: "失败",
          type: "line" as const,
          data: points.map((p) => p.calls_error),
          smooth: true,
          itemStyle: { color: cssVar("--primary") },
          lineStyle: { color: cssVar("--primary"), width: 2 },
        },
        {
          name: "Token",
          type: "bar" as const,
          yAxisIndex: 1,
          data: points.map((p) => p.total_tokens),
          itemStyle: { color: cssVar("--accent-foreground"), opacity: 0.35 },
        },
      ],
    };
  }, [points]);
  return <EChart option={option} notMerge style={{ height: 240 }} />;
}

export function UsageStats({ totals, summary }: { totals: Totals; summary: SummarySource }) {
  /** 汇总卡数值加载/错误态折叠(loading 显 …、错误显 —,不逐卡 role=alert) */
  const usageStat = (v: string) => {
    if (summary.isLoading) return "…";
    if (summary.isError) return "—";
    return v;
  };
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <StatCard label="总调用" value={usageStat(String(totals.calls))} />
      <StatCard label="Tokens 总量" value={usageStat(fmtTokens(totals.tokens))} />
      <StatCard label="失败率" value={usageStat(`${(totals.errorRate * 100).toFixed(1)}%`)} />
      <StatCard label="平均延迟" value={usageStat(formatDuration(totals.latAvg))} />
    </div>
  );
}

/** 按天趋势区块:标题旁的时间范围选择 + 加载/错误/图表三态。 */
export function UsageTrendSection({
  trend,
  days,
  onDaysChange,
}: {
  trend: { isLoading: boolean; isError: boolean; data?: { points: UsageTrendPoint[] } | undefined };
  days: number;
  onDaysChange: (days: number) => void;
}) {
  const { theme } = useTheme();
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-medium">按天趋势</h3>
        <Select value={String(days)} onValueChange={(v) => onDaysChange(Number(v))}>
          {/* w-28 下限:装下最宽项「近 30 天」(w-24 时文本预算 48px<53px 被裁) */}
          <SelectTrigger size="sm" className="w-28">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="7">近 7 天</SelectItem>
            <SelectItem value="30">近 30 天</SelectItem>
            <SelectItem value="90">近 90 天</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {(() => {
        if (trend.isLoading) {
          return (
            <div className="flex h-56 items-center justify-center text-sm text-muted-foreground">
              加载中…
            </div>
          );
        }
        if (trend.isError) return <ErrorAlert text="趋势加载失败" />;
        return <UsageTrendChart key={theme} points={trend.data?.points ?? []} />;
      })()}
    </div>
  );
}

/** 「模型 × 任务」汇总表(纯展示,无行操作)。 */
export function SummaryTable({ summary }: { summary: SummarySource }) {
  return (
    <div className="rounded-lg border bg-card">
      <div className="border-b px-4 py-3">
        <h3 className="text-sm font-medium">模型 × 任务</h3>
      </div>
      {/* 汇总加载失败:错误条置于表区上方,DataView 只管加载/空态 */}
      {!summary.isLoading && summary.isError && (
        <div className="px-4 py-3">
          <ErrorAlert text="汇总加载失败" />
        </div>
      )}
      {/* 纯展示表(无行操作):不传 actions;rowKey 取分组维度三元组 */}
      <DataView
        rows={summary.data?.items ?? []}
        rowKey={(i) => `${i.tenant_key}-${i.model}-${i.task}`}
        isLoading={summary.isLoading}
        isEmpty={!summary.isLoading && !summary.isError && summary.data?.items.length === 0}
        emptyText="暂无用量记录"
        columns={[
          { key: "tenant_key", header: "租户", cellClassName: "text-muted-foreground" },
          {
            key: "model",
            header: "模型",
            cardPriority: 1,
            render: (i) => (
              <span className="block max-w-40 truncate font-medium" title={i.model}>
                {i.model}
              </span>
            ),
          },
          { key: "task", header: "任务", cardPriority: 2 },
          {
            key: "calls",
            header: "调用",
            cardPriority: 3,
            cellClassName: "text-right",
            render: (i) => i.calls,
          },
          {
            key: "calls_error",
            header: "失败",
            cardPriority: 4,
            cellClassName: "text-right",
            render: (i) =>
              i.calls_error > 0 ? (
                <Badge variant="destructive">{i.calls_error}</Badge>
              ) : (
                <span className="text-muted-foreground">0</span>
              ),
          },
          {
            key: "total_tokens",
            header: "Tokens",
            cardPriority: 5,
            cellClassName: "text-right",
            render: (i) => fmtTokens(i.total_tokens),
          },
          {
            key: "latency_ms_avg",
            header: "平均延迟",
            cellClassName: "text-right",
            render: (i) => formatDuration(i.latency_ms_avg),
          },
          {
            key: "latency_ms_max",
            header: "最大延迟",
            cellClassName: "text-right",
            render: (i) => formatDuration(i.latency_ms_max),
          },
        ]}
      />
    </div>
  );
}
