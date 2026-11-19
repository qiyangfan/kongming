/* 展示 LLM 调用、失败率、延迟和 tokens 的汇总、趋势、模型对比、明细及 trace 时间线。 */

import { DataView } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import { PageHeader } from "@/components/page-header";
import { Pagination } from "@/components/pagination";

import { detailColumns, UsageFilters } from "./usage/UsageFilters";
import { SummaryTable, UsageStats, UsageTrendSection } from "./usage/UsageSections";
import { UsageTimelineDialog } from "./usage/UsageTimelineDialog";
import { PAGE_SIZE, useUsagePageState } from "./usage/use-usage-page-state";

export default function UsagePage() {
  const s = useUsagePageState();
  const totalPages = Math.max(1, Math.ceil((s.list.data?.total ?? 0) / PAGE_SIZE));
  // 筛选回调统一归回第 1 页
  const reset = (fn: (v: string) => void) => (v: string) => {
    fn(v);
    s.setPage(1);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="模型用量"
        description="模型调用的用量、成败与延迟——看穿每一次问卦背后的开销。"
      />

      {/* 汇总卡(卡级错误不逐卡 role=alert,数值降 …/— 示错;区块级错误条由消费区承载) */}
      <UsageStats totals={s.totals} summary={s.summary} />

      <UsageTrendSection trend={s.trend} days={s.days} onDaysChange={s.setDays} />

      <SummaryTable summary={s.summary} />

      <div className="rounded-lg border bg-card">
        <UsageFilters
          model={s.model}
          onModelChange={reset(s.setModel)}
          modelOptions={s.modelOptions}
          task={s.task}
          onTaskChange={reset(s.setTask)}
          taskOptions={s.taskOptions}
          status={s.status}
          onStatusChange={reset(s.setStatus)}
          tenantId={s.tenantId}
          onTenantChange={reset(s.setTenantId)}
          tenantsSource={s.tenants}
          protocol={s.protocol}
          onProtocolChange={reset(s.setProtocol)}
          traceId={s.traceId}
          onTraceIdChange={(v) => {
            s.setTraceId(v);
            s.setPage(1);
          }}
        />
        {/* 明细加载失败:错误条置于表区上方,DataView 只管加载/空态 */}
        {!s.list.isLoading && s.list.isError && (
          <div className="px-4 py-3">
            <ErrorAlert text="明细加载失败" />
          </div>
        )}
        <DataView
          rows={s.list.data?.rows ?? []}
          rowKey={(r) => r.id}
          isLoading={s.list.isLoading}
          isEmpty={!s.list.isLoading && !s.list.isError && s.list.data?.rows.length === 0}
          emptyText="无匹配记录"
          columns={detailColumns()}
          actions={[
            {
              label: "时间线",
              isVisible: (r) => Boolean(r.trace_id),
              onSelect: (r) => {
                if (r.trace_id) s.setTimelineTrace(r.trace_id);
              },
            },
          ]}
        />
        <Pagination
          page={s.page}
          pageCount={totalPages}
          total={s.list.data?.total ?? 0}
          onPageChange={s.setPage}
          className="border-t px-4 py-2"
        />
      </div>

      <UsageTimelineDialog
        traceId={s.timelineTrace}
        timeline={s.timeline}
        onClose={() => s.setTimelineTrace(null)}
      />
    </div>
  );
}
