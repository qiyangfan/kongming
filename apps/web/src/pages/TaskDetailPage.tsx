import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { ErrorAlert } from "@/components/error-alert";
import { MarkdownText } from "@/components/markdown-text";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { getTask, TASK_STATUS_META } from "@/lib/task-api";
import { useTenant } from "@/lib/tenant-store";

export default function TaskDetailPage() {
  const { taskId = "" } = useParams();
  const qc = useQueryClient();
  const tenant = useTenant((s) => s.current);
  const [actionError, setActionError] = useState<string | null>(null);

  const detail = useQuery({
    queryKey: ["task", tenant, taskId],
    queryFn: () => getTask(taskId),
    refetchInterval: (q) =>
      ["PLANNING", "RUNNING"].includes(q.state.data?.task.status ?? "") ? 2000 : false,
  });

  function invalidate(): void {
    void qc.invalidateQueries({ queryKey: ["task", tenant, taskId] });
    void qc.invalidateQueries({ queryKey: ["tasks", tenant] });
  }

  const act = useMutation({
    mutationFn: async (kind: "pause" | "resume" | "cancel") => {
      const { pauseTask, resumeTask, cancelTask } = await import("@/lib/task-api");
      if (kind === "pause") return pauseTask(taskId);
      if (kind === "resume") return resumeTask(taskId);
      return cancelTask(taskId);
    },
    onSuccess: () => {
      setActionError(null);
      invalidate();
    },
    onError: (e) => setActionError(e instanceof Error ? e.message : "操作未成"),
  });

  const approve = useMutation({
    mutationFn: async (input: { stepId: string; approve: boolean }) => {
      const { approveStep } = await import("@/lib/task-api");
      return approveStep(taskId, input.stepId, input.approve);
    },
    onSuccess: () => {
      setActionError(null);
      invalidate();
    },
    onError: (e) => setActionError(e instanceof Error ? e.message : "审批未成"),
  });

  if (detail.isLoading) {
    return (
      <div className="mx-auto max-w-4xl space-y-3 px-4 py-6">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-6">
        <ErrorAlert text="任务不存在或不可见。" />
        <ButtonLink />
      </div>
    );
  }

  const d = detail.data;
  const active = ["PLANNING", "RUNNING", "AWAITING_APPROVAL", "PAUSED", "INTERRUPTED"].includes(
    d.task.status,
  );

  return (
    <div className="mx-auto max-w-4xl space-y-5 px-4 py-6">
      <ButtonLink />
      <PageHeader
        title={d.task.goal}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge meta={TASK_STATUS_META} value={d.task.status} />
            <span className="text-xs text-muted-foreground">
              创建于 {new Date(d.task.created_at).toLocaleString()}
            </span>
          </span>
        }
        actions={
          active && (
            <>
              <OperationButton
                label="暂停"
                onClick={() => act.mutate("pause")}
                disabled={act.isPending || d.task.status !== "RUNNING"}
              />
              <OperationButton
                label="恢复"
                onClick={() => act.mutate("resume")}
                disabled={
                  act.isPending ||
                  !["PAUSED", "INTERRUPTED", "AWAITING_APPROVAL"].includes(d.task.status)
                }
              />
              <ConfirmOperationButton
                label="取消"
                title="取消这个任务?"
                description={`「${d.task.goal}」将进入 CANCELLED 终态,未完成步骤一并关闭;此操作不可撤销。`}
                confirmLabel="取消任务"
                onClick={() => act.mutate("cancel")}
                disabled={act.isPending}
              />
            </>
          )
        }
      />

      {actionError && <ErrorAlert text={actionError} />}
      {d.task.fail_reason && (
        <p role="status" className="rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
          {d.task.fail_reason}
        </p>
      )}

      {/* 步骤账本 */}
      <section className="space-y-2" aria-label="步骤">
        <h3 className="text-sm font-semibold text-foreground">步骤</h3>
        {d.steps.map((s) => (
          <div key={s.id} className="space-y-1 rounded-lg border border-border p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium text-foreground">
                {s.ord + 1}. {s.goal}
              </span>
              <StatusBadge meta={TASK_STATUS_META} value={s.status} fallbackVariant="outline" />
            </div>
            {s.status === "AWAITING_APPROVAL" && (
              <div className="flex gap-2 pt-1">
                <OperationButton
                  label="批准"
                  onClick={() => approve.mutate({ stepId: s.id, approve: true })}
                  disabled={approve.isPending}
                />
                <ConfirmOperationButton
                  label="拒绝"
                  title="拒绝这个步骤?"
                  description="该步骤将置为 SKIPPED(不再执行),任务按其余步骤继续;任务将如实以 FAILED 收口并说明未完成原因。"
                  confirmLabel="拒绝执行"
                  onClick={() => approve.mutate({ stepId: s.id, approve: false })}
                  disabled={approve.isPending}
                />
              </div>
            )}
            {s.fail_reason && <p className="text-xs text-muted-foreground">{s.fail_reason}</p>}
            {s.checkpoint &&
              typeof s.checkpoint.summary === "string" &&
              s.checkpoint.summary !== "" && <MarkdownText text={s.checkpoint.summary} />}
          </div>
        ))}
      </section>

      {/* 事件时间线 */}
      <section className="space-y-1" aria-label="事件">
        <h3 className="text-sm font-semibold text-foreground">事件</h3>
        {d.events.length === 0 ? (
          <p className="text-xs text-muted-foreground">暂无事件。</p>
        ) : (
          <ol className="space-y-0.5">
            {[...d.events].reverse().map((e, i) => (
              <li key={i} className="text-xs text-muted-foreground">
                {typeof e.at === "string" ? new Date(e.at).toLocaleString() : ""} —{" "}
                {String(e.kind ?? "")}
                {e.reason ? `(${String(e.reason)})` : ""}
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}

function ButtonLink() {
  return (
    <Link to="/tasks" className="flex items-center gap-1 text-sm text-muted-foreground">
      <ArrowLeft className="size-4" aria-hidden /> 任务列表
    </Link>
  );
}

function OperationButton(props: { label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <Button variant="outline" size="sm" onClick={props.onClick} disabled={props.disabled}>
      {props.label}
    </Button>
  );
}

/** 破坏性操作按钮(宪法 Article 2:不可逆终态迁移必须 AlertDialog 确认)。 */
function ConfirmOperationButton(props: {
  label: string;
  title: string;
  description: string;
  confirmLabel: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="ghost" size="sm" className="text-destructive" disabled={props.disabled}>
          {props.label}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{props.title}</AlertDialogTitle>
          <AlertDialogDescription>{props.description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>再想想</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={props.onClick}>
            {props.confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
