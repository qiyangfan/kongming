/* 任务详情:目标与状态、暂停/恢复/取消、步骤账本、协作视图与事件时间线。 */

import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router";

import { ErrorAlert } from "@kongming/ui/error-alert";
import { PageHeader } from "@kongming/ui/page-header";
import { StatusBadge } from "@kongming/ui/status-badge";
import { Skeleton } from "@kongming/ui/skeleton";
import type { TaskDetailOut } from "@kongming/api-client/task-api";
import { TASK_STATUS_META } from "@kongming/api-client/task-api";

import { TaskCollabSection, TaskEventsSection } from "./task-collab-section";
import { ConfirmOperationButton, OperationButton } from "./task-operation-buttons";
import { TaskStepsSection } from "./task-steps-section";
import { isActiveTask, useTaskDetail } from "./task-use-task-detail";

type TaskActionsApi = ReturnType<typeof useTaskDetail>["act"];

/** 页头操作组:暂停/恢复/取消(仅活动态出现,取消需二次确认)。 */
function TaskHeaderActions({ detail, act }: { detail: TaskDetailOut; act: TaskActionsApi }) {
  if (!isActiveTask(detail.task.status)) return null;
  return (
    <>
      <OperationButton
        label="暂停"
        onClick={() => act.mutate("pause")}
        disabled={act.isPending || detail.task.status !== "RUNNING"}
      />
      <OperationButton
        label="恢复"
        onClick={() => act.mutate("resume")}
        disabled={
          act.isPending ||
          !["PAUSED", "INTERRUPTED", "AWAITING_APPROVAL"].includes(detail.task.status)
        }
      />
      <ConfirmOperationButton
        label="取消"
        title="取消这个任务?"
        description={`「${detail.task.goal}」将进入 CANCELLED 终态,未完成步骤一并关闭;此操作不可撤销。`}
        confirmLabel="取消任务"
        onClick={() => act.mutate("cancel")}
        disabled={act.isPending}
      />
    </>
  );
}

/** 返回任务列表的链接。 */
function ButtonLink() {
  return (
    <Link to="/tasks" className="flex items-center gap-1 text-sm text-muted-foreground">
      <ArrowLeft className="size-4" aria-hidden /> 任务列表
    </Link>
  );
}

export default function TaskDetailPage() {
  const { taskId = "" } = useParams();
  const { detail, runs, act, approve, confirm, actionError } = useTaskDetail(taskId);

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
        actions={<TaskHeaderActions detail={d} act={act} />}
      />

      {actionError && <ErrorAlert text={actionError} />}
      {d.task.fail_reason && (
        <p role="status" className="rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
          {d.task.fail_reason}
        </p>
      )}

      {/* 步骤账本 */}
      <TaskStepsSection
        steps={d.steps}
        graphConfirms={d.graph_confirms}
        approvePending={approve.isPending}
        confirmPending={confirm.isPending}
        onApprove={(stepId, ok) => approve.mutate({ stepId, approve: ok })}
        onConfirm={(confirmId, ok) => confirm.mutate({ confirmId, approve: ok })}
      />

      {/* 协作视图:worker 账本 + 裁决;加载失败如实呈现,有账才渲染 */}
      {runs.isError && <ErrorAlert text="协作账本加载失败,可刷新重试。" />}
      {runs.data && runs.data.items.length > 0 && (
        <TaskCollabSection items={runs.data.items} events={d.events} />
      )}

      <TaskEventsSection events={d.events} />
    </div>
  );
}
