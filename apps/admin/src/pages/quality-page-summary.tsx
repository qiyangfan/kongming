/* 问答质量页的汇总卡、按天趋势与两类切片表。 */

import { useMemo } from "react";
import { Download } from "lucide-react";

import { Button } from "@kongming/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import { DataView } from "@kongming/ui/data-view";
import { ErrorAlert } from "@kongming/ui/error-alert";
import { StatCard } from "@kongming/ui/stat-card";
import type { QualitySummaryOut } from "@kongming/api-client/quality-api";
import { QualityTrendChart, pct } from "./quality-page-dialogs";

type QueryLike<T> = { data: T | undefined; isLoading: boolean; isError: boolean };

type Overview = QualitySummaryOut["overview"];

// 汇总卡数值:加载中显示 …、失败显示 —,避免误导为真实数字
function statOf(q: QueryLike<QualitySummaryOut>, v: string): string {
  if (q.isLoading) return "…";
  if (q.isError) return "—";
  return v;
}

function avgScoreLabel(ov: Overview | undefined): string {
  if (!ov) return "—";
  return ov.avg_score != null ? ov.avg_score.toFixed(1) : "未评分";
}

function StatCards({ q, ov }: { q: QueryLike<QualitySummaryOut>; ov: Overview | undefined }) {
  const stat = (v: string) => statOf(q, v);
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
      <StatCard label="总轮次" value={stat(String(ov?.total ?? 0))} />
      <StatCard label="成功率" value={stat(ov ? pct(ov.success / (ov.total || 1)) : "0.0%")} />
      <StatCard label="未覆盖率" value={stat(ov ? pct(ov.no_data_rate) : "0.0%")} />
      <StatCard label="自愈率" value={stat(ov ? pct(ov.selfheal_rate) : "0.0%")} />
      <StatCard label="AI 平均分" value={stat(avgScoreLabel(ov))} />
    </div>
  );
}

// 切片表列定义:数据源与 prompt 版本两张表结构一致
const SLICE_COLUMNS = [
  { key: "rounds", header: "轮次", cellClassName: "text-right" },
  { key: "success", header: "成功", cellClassName: "text-right" },
  { key: "no_data", header: "未覆盖", cellClassName: "text-right" },
  { key: "blocked", header: "拦截", cellClassName: "text-right" },
  { key: "failed", header: "失败", cellClassName: "text-right" },
];

function SliceCard<T extends { rounds: number }>({
  title,
  rows,
  rowKey,
  firstKey,
  firstHeader,
  q,
}: {
  title: string;
  rows: T[];
  rowKey: (r: T) => string;
  firstKey: string;
  firstHeader: string;
  q: QueryLike<QualitySummaryOut>;
}) {
  return (
    <div className="rounded-lg border bg-card">
      <div className="border-b px-4 py-3">
        <h3 className="text-sm font-medium">{title}</h3>
      </div>
      <DataView
        rows={rows}
        rowKey={rowKey}
        isLoading={q.isLoading}
        isEmpty={!q.isLoading && !q.isError && !rows.length}
        emptyText="暂无轮次记录"
        columns={[{ key: firstKey, header: firstHeader, cardPriority: 1 }, ...SLICE_COLUMNS]}
      />
    </div>
  );
}

export function QualitySummarySection({
  summary,
  theme,
  days,
  onDaysChange,
  exporting,
  onExport,
}: {
  summary: QueryLike<QualitySummaryOut>;
  theme: string;
  days: number;
  onDaysChange: (d: number) => void;
  exporting: boolean;
  onExport: () => void;
}) {
  return (
    <>
      {/* 汇总卡(卡级错误不逐卡 role=alert,数值降 …/— 示错) */}
      <StatCards q={summary} ov={summary.data?.overview} />

      <div className="rounded-lg border bg-card p-4">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-medium">按天趋势</h3>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={onExport} disabled={exporting}>
              <Download className="mr-1 size-3.5" aria-hidden />
              {exporting ? "导出中…" : "导出 CSV"}
            </Button>
            <Select value={String(days)} onValueChange={(v) => onDaysChange(Number(v))}>
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
        </div>
        {(() => {
          if (summary.isLoading) {
            return (
              <div className="flex h-56 items-center justify-center text-sm text-muted-foreground">
                加载中…
              </div>
            );
          }
          if (summary.isError) return <ErrorAlert text="汇总加载失败" />;
          return <QualityTrendChart key={theme} points={summary.data?.trend ?? []} />;
        })()}
      </div>

      {/* 数据源 × prompt 版本切片(纯展示表:不传 actions) */}
      <div className="grid gap-4 lg:grid-cols-2">
        <SliceCard
          title="数据源切片"
          rows={summary.data?.by_datasource ?? []}
          rowKey={(r) => r.datasource_key}
          firstKey="datasource_key"
          firstHeader="数据源"
          q={summary}
        />
        <SliceCard
          title="prompt 版本切片"
          rows={summary.data?.by_prompt_version ?? []}
          rowKey={(r) => r.prompt_version}
          firstKey="prompt_version"
          firstHeader="版本"
          q={summary}
        />
      </div>
    </>
  );
}

// 数据源筛选项从已加载的切片数据中生成。
export function useDatasourceOptions(summary: QueryLike<QualitySummaryOut>): string[] {
  return useMemo(
    () =>
      (summary.data?.by_datasource ?? [])
        .map((r) => r.datasource_key)
        .sort((a, b) => a.localeCompare(b)),
    [summary.data],
  );
}
