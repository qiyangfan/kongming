/* 任务详情的数据与操作:详情/协作账本轮询、暂停恢复取消、步骤审批与图内确认处置。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { decideGraphConfirm, getTask, listCollabRuns } from "@kongming/api-client/task-api";
import { useTenant } from "@kongming/api-client/tenant-store";

// 活动期轮询节奏;终态即停
const TASK_ACTIVE_POLL_MS = 2000;
const COLLAB_RUNS_POLL_MS = 4000;
// 协作运行在途只可能发生在 PLANNING/RUNNING/PAUSED:审批挂起/中断期无执行者;
// 暂停不杀在途编排循环,PAUSED 下仍可能有收尾中的运行
const RUNS_ACTIVE_STATUSES = ["PLANNING", "RUNNING", "PAUSED"];

/** 任务详情的查询与全部可写操作。 */
export function useTaskDetail(taskId: string) {
  const qc = useQueryClient();
  const tenant = useTenant((s) => s.current);
  const [actionError, setActionError] = useState<string | null>(null);

  const detail = useQuery({
    queryKey: ["task", tenant, taskId],
    queryFn: () => getTask(taskId),
    refetchInterval: (q) =>
      ["PLANNING", "RUNNING"].includes(q.state.data?.task.status ?? "")
        ? TASK_ACTIVE_POLL_MS
        : false,
  });

  const runs = useQuery({
    queryKey: ["task-collab-runs", tenant, taskId],
    queryFn: () => listCollabRuns(taskId),
    // 仅协作可能在途的任务态轮询刷新;终态/审批挂起/中断一次性读取
    refetchInterval: RUNS_ACTIVE_STATUSES.includes(detail.data?.task.status ?? "")
      ? COLLAB_RUNS_POLL_MS
      : false,
    retry: false,
  });

  function invalidate(): void {
    void qc.invalidateQueries({ queryKey: ["task", tenant, taskId] });
    void qc.invalidateQueries({ queryKey: ["tasks", tenant] });
  }

  const act = useMutation({
    mutationFn: async (kind: "pause" | "resume" | "cancel") => {
      const { pauseTask, resumeTask, cancelTask } = await import("@kongming/api-client/task-api");
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
      const { approveStep } = await import("@kongming/api-client/task-api");
      return approveStep(taskId, input.stepId, input.approve);
    },
    onSuccess: () => {
      setActionError(null);
      invalidate();
    },
    onError: (e) => setActionError(e instanceof Error ? e.message : "审批未成"),
  });

  // 图内确认处置(add-graph-checkpoint):批准=动作恢复执行,拒绝=动作跳过;
  // 恢复是编排循环唯一的再启动口,处置后控制面自动重派
  const confirm = useMutation({
    mutationFn: async (input: { confirmId: string; approve: boolean }) =>
      decideGraphConfirm(taskId, input.confirmId, input.approve),
    onSuccess: () => {
      setActionError(null);
      invalidate();
    },
    onError: (e) => setActionError(e instanceof Error ? e.message : "确认处置未成"),
  });

  return { detail, runs, act, approve, confirm, actionError, setActionError };
}

/** 任务是否处于需要操作按钮的活动态。 */
export function isActiveTask(status: string | undefined): boolean {
  return ["PLANNING", "RUNNING", "AWAITING_APPROVAL", "PAUSED", "INTERRUPTED"].includes(
    status ?? "",
  );
}
