/* 触发器执行历史弹窗:按时间倒序分页展示执行状态、尝试次数与失败原因。 */

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import type { TriggerItem, TriggerRunItem } from "@/lib/trigger-api";
import { formatDateTime, formatDuration } from "@/lib/datetime";
import { PAGE_SIZE, RUN_STATUS_META } from "./triggers-page-model";

type TriggersHistoryDialogProps = {
  trigger: TriggerItem | null;
  page: number;
  items: TriggerRunItem[] | undefined;
  total: number | undefined;
  isError: boolean;
  onPageChange: (page: number) => void;
  onClose: () => void;
};

function RunItem({ run }: { run: TriggerRunItem }) {
  return (
    <div className="rounded-md border px-3 py-2 text-sm">
      <StatusBadge meta={RUN_STATUS_META} value={run.status} fallbackVariant="destructive" />
      {/* 重试链:attempt>1 标注尝试序号(首跑不标,免噪音) */}
      {run.attempt > 1 && (
        <Badge variant="outline" className="ml-2">
          第 {run.attempt} 次
        </Badge>
      )}
      <span className="ml-2 text-xs text-muted-foreground">
        {formatDuration(run.duration_ms)} · {formatDateTime(run.created_at)}
      </span>
      {run.status === "PENDING" && run.next_retry_at && (
        <p className="mt-1 text-xs text-muted-foreground">
          将于 {formatDateTime(run.next_retry_at)} 重试
        </p>
      )}
      {run.error && <p className="mt-1 text-xs text-destructive">{run.error}</p>}
    </div>
  );
}

export function TriggersHistoryDialog(props: TriggersHistoryDialogProps) {
  const { trigger, page, items, total, isError, onPageChange, onClose } = props;
  const rows = items ?? [];
  return (
    <Dialog open={trigger !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>执行历史「{trigger?.name}」</DialogTitle>
          <DialogDescription>按时间倒序的触发执行记录。</DialogDescription>
        </DialogHeader>
        <div className="max-h-96 space-y-2 overflow-y-auto">
          {(() => {
            if (isError) return <ErrorAlert />;
            if (rows.length === 0) return <EmptyState>暂无记录。</EmptyState>;
            return (
              <>
                {rows.map((r) => (
                  <RunItem key={r.id} run={r} />
                ))}
              </>
            );
          })()}
        </div>
        {(total ?? 0) > PAGE_SIZE && (
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
              第 {page} / {Math.ceil((total ?? 0) / PAGE_SIZE)} 页
            </span>
            <Button
              variant="outline"
              size="xs"
              disabled={page * PAGE_SIZE >= (total ?? 0)}
              onClick={() => onPageChange(page + 1)}
            >
              下一页
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
