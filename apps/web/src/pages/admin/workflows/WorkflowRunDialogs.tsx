/* 工作流的运行历史对话框(可折叠节点摘要 + 分页)与单次运行详情对话框。 */

import { ChevronRight, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/empty-state";
import { ErrorAlert } from "@/components/error-alert";
import { StatusBadge } from "@/components/status-badge";
import { formatDateTime, formatDuration } from "@/lib/datetime";
import { nodeRunsOf, type WorkflowItem, type WorkflowRunItem } from "@/lib/workflow-api";

import {
  formatOutputs,
  nodeStatusLabel,
  RUN_STATUS_META,
  triggerSourceLabel,
} from "./workflow-format";

/** 运行历史里的单条记录:摘要行 + 展开后的错误与节点状态。 */
function RunHistoryEntry({ run, onDetail }: { run: WorkflowRunItem; onDetail: () => void }) {
  return (
    <Collapsible>
      <div className="rounded-md border px-3 py-2">
        <div className="flex items-center gap-2">
          <CollapsibleTrigger className="group flex flex-1 items-center gap-2 text-sm">
            <ChevronRight
              className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
              aria-hidden
            />
            <StatusBadge meta={RUN_STATUS_META} value={run.status} fallbackVariant="destructive" />
            <span className="text-xs text-muted-foreground">
              v{run.version} · {triggerSourceLabel(run.trigger_source)} ·{" "}
              {formatDuration(run.duration_ms)} · {formatDateTime(run.created_at)}
            </span>
          </CollapsibleTrigger>
          <Button variant="outline" size="xs" className="shrink-0" onClick={onDetail}>
            详情
          </Button>
        </div>
        <CollapsibleContent>
          <div className="mt-2 space-y-1 border-t pt-2">
            {typeof run.error === "string" && run.error && (
              <p className="text-xs text-destructive">{run.error}</p>
            )}
            {nodeRunsOf(run).map((nr) => (
              <div key={nr.node_id} className="text-xs">
                <span className="font-mono">{nr.node_id}</span>{" "}
                <span
                  className={nr.status === "FAILED" ? "text-destructive" : "text-muted-foreground"}
                >
                  {nodeStatusLabel(nr.status)}
                </span>
                {nr.error && <span className="text-destructive"> — {nr.error}</span>}
              </div>
            ))}
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}

export function RunHistoryDialog({
  workflow,
  runs,
  page,
  pageSize,
  onPageChange,
  onDetail,
  onClose,
}: {
  workflow: WorkflowItem | null;
  runs: { isError: boolean; data?: { items: WorkflowRunItem[]; total: number } };
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onDetail: (runId: string) => void;
  onClose: () => void;
}) {
  const items = runs.data?.items ?? [];
  const total = runs.data?.total ?? 0;
  return (
    <Dialog
      open={workflow !== null}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>运行历史「{workflow?.name}」</DialogTitle>
          <DialogDescription>按时间倒序的执行记录;点「详情」查看完整节点输出。</DialogDescription>
        </DialogHeader>
        <div className="max-h-96 space-y-2 overflow-y-auto">
          {runs.isError ? (
            <ErrorAlert />
          ) : items.length === 0 ? (
            <EmptyState>暂无运行记录。</EmptyState>
          ) : (
            items.map((r) => <RunHistoryEntry key={r.id} run={r} onDetail={() => onDetail(r.id)} />)
          )}
        </div>
        {total > pageSize && (
          <HistoryPager page={page} total={total} pageSize={pageSize} onPageChange={onPageChange} />
        )}
      </DialogContent>
    </Dialog>
  );
}

/** 历史分页条(上一页/页码/下一页)。 */
function HistoryPager({
  page,
  total,
  pageSize,
  onPageChange,
}: {
  page: number;
  total: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}) {
  return (
    <div className="flex items-center justify-between pt-2">
      <Button
        variant="outline"
        size="xs"
        disabled={page <= 1}
        onClick={() => onPageChange(page - 1)}
      >
        上一页
      </Button>
      <span className="text-xs text-muted-foreground">
        第 {page} / {Math.ceil(total / pageSize)} 页
      </span>
      <Button
        variant="outline"
        size="xs"
        disabled={page * pageSize >= total}
        onClick={() => onPageChange(page + 1)}
      >
        下一页
      </Button>
    </div>
  );
}

/** run 详情:独立端点,node_runs 含 outputs。 */
export function RunDetailDialog({
  runId,
  detail,
  onClose,
}: {
  runId: string | null;
  detail: { isLoading: boolean; isError: boolean; data?: WorkflowRunItem };
  onClose: () => void;
}) {
  return (
    <Dialog
      open={runId !== null}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
    >
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>运行详情</DialogTitle>
          <DialogDescription
            className="max-w-full truncate font-mono text-xs"
            title={runId ?? undefined}
          >
            运行 ID:{runId}
          </DialogDescription>
        </DialogHeader>
        {detail.isLoading ? (
          <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            加载中…
          </div>
        ) : detail.isError ? (
          <ErrorAlert />
        ) : detail.data ? (
          <RunDetailBody run={detail.data} />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

/** 详情主体:运行摘要 + 每个节点的状态、耗时、错误与输出折叠区。 */
function RunDetailBody({ run }: { run: WorkflowRunItem }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <StatusBadge meta={RUN_STATUS_META} value={run.status} fallbackVariant="destructive" />
        <span className="text-xs text-muted-foreground">
          v{run.version} · 触发源 {triggerSourceLabel(run.trigger_source)} ·{" "}
          {formatDuration(run.duration_ms)}
          {run.created_at ? ` · ${formatDateTime(run.created_at)}` : ""}
        </span>
      </div>
      {run.error && <p className="text-sm text-destructive">{run.error}</p>}
      <div className="space-y-2">
        {(run.node_runs ?? []).map((raw, i) => {
          const nr = nodeRunsOf({ node_runs: [raw] })[0];
          const outputs = formatOutputs(raw.outputs);
          return (
            <div key={nr.node_id || i} className="rounded-md border px-3 py-2">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-mono text-xs">{nr.node_id}</span>
                <StatusBadge meta={RUN_STATUS_META} value={nr.status} fallbackVariant="secondary" />
                {nr.duration_ms !== undefined && (
                  <span className="text-xs text-muted-foreground">
                    {formatDuration(nr.duration_ms)}
                  </span>
                )}
                {nr.error && <span className="text-xs text-destructive">{nr.error}</span>}
              </div>
              {outputs !== null && (
                <Collapsible>
                  <CollapsibleTrigger className="group mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                    <ChevronRight
                      className="size-3 transition-transform group-data-[state=open]:rotate-90"
                      aria-hidden
                    />
                    输出
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <pre className="mt-1 max-h-64 overflow-auto rounded-md bg-muted/60 p-2 text-xs leading-relaxed">
                      {outputs}
                    </pre>
                  </CollapsibleContent>
                </Collapsible>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
