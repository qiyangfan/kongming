/* 问答质量页的轮次明细表:状态/数据源/兜底分类筛选、行操作与分页。 */

import { Copy } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DataView, type DataViewAction } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import { Pagination } from "@/components/pagination";
import { StatusBadge } from "@/components/status-badge";
import type { QualityPageOut, QualityRoundItemOut } from "@/lib/quality-api";
import { formatDateTime } from "@/lib/datetime";
import { FALLBACK_META, ROUND_STATUS_META } from "./quality-page-meta";

type QueryLike<T> = { data: T | undefined; isLoading: boolean; isError: boolean };

export type QualityFiltersState = { status: string; datasourceKey: string; fallback: string };

// 三组筛选共用的「全部=x"形态:改选即回第一页
function FilterSelect({
  value,
  onValueChange,
  width,
  children,
}: {
  value: string;
  onValueChange: (v: string) => void;
  width: string;
  children: React.ReactNode;
}) {
  return (
    <Select value={value} onValueChange={onValueChange}>
      <SelectTrigger size="sm" className={width}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>{children}</SelectContent>
    </Select>
  );
}

function DetailFilters({
  filters,
  dsOptions,
  onFilterChange,
}: {
  filters: QualityFiltersState;
  dsOptions: string[];
  onFilterChange: (key: keyof QualityFiltersState, v: string) => void;
}) {
  return (
    <>
      <FilterSelect
        value={filters.status || "all"}
        onValueChange={(v) => onFilterChange("status", v === "all" ? "" : v)}
        width="w-28"
      >
        <SelectItem value="all">全部状态</SelectItem>
        <SelectItem value="SUCCESS">成功</SelectItem>
        <SelectItem value="NO_DATA">未覆盖</SelectItem>
        <SelectItem value="REJECTED">已拦截</SelectItem>
        <SelectItem value="FAILED">失败</SelectItem>
      </FilterSelect>
      <FilterSelect
        value={filters.datasourceKey || "all"}
        onValueChange={(v) => onFilterChange("datasourceKey", v === "all" ? "" : v)}
        width="w-36"
      >
        <SelectItem value="all">全部数据源</SelectItem>
        {dsOptions.map((k) => (
          <SelectItem key={k} value={k}>
            {k}
          </SelectItem>
        ))}
      </FilterSelect>
      <FilterSelect
        value={filters.fallback || "all"}
        onValueChange={(v) => onFilterChange("fallback", v === "all" ? "" : v)}
        width="w-32"
      >
        <SelectItem value="all">全部分类</SelectItem>
        <SelectItem value="CHITCHAT">闲聊</SelectItem>
        <SelectItem value="CLARIFY">澄清</SelectItem>
        <SelectItem value="none">无分类</SelectItem>
      </FilterSelect>
    </>
  );
}

const RATING_LABELS: Record<string, string> = { UP: "赞", DOWN: "踩" };

function detailColumns(ctx: {
  lowScore: number;
  onScore: (id: string) => void;
  onTimeline: (traceId: string | null) => void;
}) {
  return [
    {
      key: "question",
      header: "问题",
      cardPriority: 1,
      render: (r: QualityRoundItemOut) => (
        <div className="max-w-64">
          <span className="block truncate" title={r.question ?? ""}>
            {r.question ?? "—"}
          </span>
          {/* 多轮改写结果(仅实际改写时存在):诊断信号,弱化呈现 */}
          {r.resolved_question && (
            <span
              className="block truncate text-xs text-muted-foreground"
              title={r.resolved_question}
            >
              改写:{r.resolved_question}
            </span>
          )}
        </div>
      ),
    },
    {
      key: "status",
      header: "状态",
      cardPriority: 2,
      render: (r: QualityRoundItemOut) => (
        <span className="inline-flex items-center gap-1.5">
          <StatusBadge value={r.status} meta={ROUND_STATUS_META} />
          {r.fallback_type && <StatusBadge value={r.fallback_type} meta={FALLBACK_META} />}
          {r.retry_count > 0 && (
            <span className="text-xs text-muted-foreground">重试 {r.retry_count}</span>
          )}
        </span>
      ),
    },
    {
      key: "rating",
      header: "反馈",
      render: (r: QualityRoundItemOut) => RATING_LABELS[r.rating ?? ""] ?? "—",
    },
    {
      key: "score",
      header: "AI 评分",
      render: (r: QualityRoundItemOut) =>
        r.score != null ? (
          <Button
            variant="link"
            size="xs"
            className={`h-auto p-0 font-mono ${
              r.score < ctx.lowScore ? "text-destructive" : "text-foreground"
            }`}
            onClick={() => ctx.onScore(r.id)}
          >
            {r.score}
          </Button>
        ) : (
          <span className="text-muted-foreground">未评分</span>
        ),
    },
    {
      key: "datasource_key",
      header: "数据源",
      cellClassName: "text-muted-foreground",
    },
    {
      key: "prompt_version",
      header: "prompt",
      cellClassName: "text-muted-foreground",
    },
    {
      key: "created_at",
      header: "时间",
      render: (r: QualityRoundItemOut) => formatDateTime(r.created_at),
    },
    {
      key: "trace_id",
      header: "链路",
      render: (r: QualityRoundItemOut) =>
        r.trace_id ? (
          <Button
            variant="link"
            size="xs"
            className="h-auto font-mono p-0 text-xs"
            onClick={() => ctx.onTimeline(r.trace_id ?? null)}
          >
            {r.trace_id.slice(0, 8)}…
          </Button>
        ) : (
          "—"
        ),
    },
  ];
}

// 行操作是状态无关的固定集合,复制后短暂提示由页面持有
export function buildRowActions(h: {
  onCopy: (row: QualityRoundItemOut) => void;
  onContext: (id: string) => void;
  onStageTrace: (id: string) => void;
  onRag: () => void;
}): DataViewAction<QualityRoundItemOut>[] {
  return [
    {
      label: "复制问题",
      onSelect: h.onCopy,
      isVisible: (row) => Boolean(row.question),
    },
    { label: "同会话轮次", onSelect: (row) => h.onContext(row.id) },
    { label: "执行轨迹", onSelect: (row) => h.onStageTrace(row.id) },
    {
      label: "知识库补全",
      onSelect: () => h.onRag(),
      isVisible: (row) => row.status === "NO_DATA",
    },
  ];
}

export function QualityDetailSection({
  list,
  filters,
  dsOptions,
  onFilterChange,
  page,
  totalPages,
  onPageChange,
  rowActions,
  lowScore,
  copied,
  exportError,
  onScore,
  onTimeline,
}: {
  list: QueryLike<QualityPageOut>;
  filters: QualityFiltersState;
  dsOptions: string[];
  onFilterChange: (key: keyof QualityFiltersState, v: string) => void;
  page: number;
  totalPages: number;
  onPageChange: (p: number) => void;
  rowActions: DataViewAction<QualityRoundItemOut>[];
  lowScore: number;
  copied: string | null;
  exportError: boolean;
  onScore: (id: string) => void;
  onTimeline: (traceId: string | null) => void;
}) {
  return (
    <div className="rounded-lg border bg-card">
      <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
        <h3 className="mr-auto text-sm font-medium">轮次明细</h3>
        <DetailFilters filters={filters} dsOptions={dsOptions} onFilterChange={onFilterChange} />
      </div>
      {!list.isLoading && list.isError && (
        <div className="px-4 py-3">
          <ErrorAlert text="明细加载失败" />
        </div>
      )}
      {exportError && (
        <div className="px-4 py-3">
          <ErrorAlert text="CSV 导出失败,请稍后重试" />
        </div>
      )}
      <DataView
        rows={list.data?.items ?? []}
        rowKey={(r) => r.id}
        actions={rowActions}
        isLoading={list.isLoading}
        isEmpty={!list.isLoading && !list.isError && !list.data?.items.length}
        emptyText="暂无轮次记录"
        columns={detailColumns({ lowScore, onScore, onTimeline })}
      />
      {copied && (
        <p className="px-4 pb-2 text-xs text-muted-foreground" role="status">
          <Copy className="mr-1 inline size-3" aria-hidden />
          已复制问题到剪贴板
        </p>
      )}
      {totalPages > 1 && (
        <div className="border-t px-4 py-3">
          <Pagination
            page={page}
            pageCount={totalPages}
            total={list.data?.total}
            onPageChange={onPageChange}
          />
        </div>
      )}
    </div>
  );
}
