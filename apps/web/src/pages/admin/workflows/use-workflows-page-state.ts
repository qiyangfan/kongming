/* 工作流页的状态、查询与变更逻辑;页面只负责组装。 */

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { WorkflowDefinitionEditorHandle } from "@/components/workflow/workflow-definition-editor";
import {
  deleteWorkflow,
  listWorkflows,
  pageWorkflowRuns,
  runWorkflow,
  saveWorkflow,
  setWorkflowEnabled,
  workflowRunDetail,
  type WorkflowItem,
  type WorkflowRunOut,
} from "@/lib/workflow-api";

export const PAGE_SIZE = 10;

export type EditingState = {
  origKey: string | null; // 编辑态锁定 wf_key，避免改名后保存为另一条工作流。
  wfKey: string;
  name: string;
  initialDef: unknown;
  version: number; // 布局持久化键(新建=0)
};

/** 解析运行输入 JSON;语法错误转成可读文案抛出。 */
function parseRunInputs(raw: string): Record<string, unknown> {
  try {
    return JSON.parse(raw || "{}") as Record<string, unknown>;
  } catch (e) {
    throw new Error(`inputs JSON 解析失败: ${e instanceof Error ? e.message : String(e)}`, {
      cause: e,
    });
  }
}

export function useWorkflowsPageState() {
  const qc = useQueryClient();
  const editorRef = useRef<WorkflowDefinitionEditorHandle | null>(null);
  // 定义编辑交由双态编辑器(画布 ⇄ JSON);页面只持初始定义与布局键
  const [editing, setEditing] = useState<EditingState | null>(null);
  const [formError, setFormError] = useState("");
  const [deleting, setDeleting] = useState<WorkflowItem | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const [running, setRunning] = useState<WorkflowItem | null>(null);
  const [runInputs, setRunInputs] = useState("{}");
  const [runResult, setRunResult] = useState<WorkflowRunOut | null>(null);
  const [runError, setRunError] = useState("");
  const [historyOf, setHistoryOf] = useState<WorkflowItem | null>(null);
  const [historyPage, setHistoryPage] = useState(1);
  // run 详情对话框(存 runId;详情数据来自独立端点)
  const [detailRunId, setDetailRunId] = useState<string | null>(null);
  const [toggleError, setToggleError] = useState<Record<string, string>>({});

  const workflows = useQuery({ queryKey: ["admin", "workflow"], queryFn: listWorkflows });
  const runs = useQuery({
    queryKey: ["admin", "workflow-runs", historyOf?.wf_key, historyPage],
    queryFn: () => pageWorkflowRuns(historyOf!.wf_key, historyPage, PAGE_SIZE),
    enabled: historyOf !== null,
  });
  const runDetail = useQuery({
    queryKey: ["admin", "workflow-run-detail", historyOf?.wf_key, detailRunId],
    queryFn: () => workflowRunDetail(historyOf!.wf_key, detailRunId!),
    enabled: detailRunId !== null && historyOf !== null,
  });

  const invalidate = () => void qc.invalidateQueries({ queryKey: ["admin", "workflow"] });

  const save = useMutation({
    mutationFn: async () => {
      if (!editing || !editorRef.current) throw new Error("编辑态已关闭");
      // 单一数据源:从编辑器取当前定义(画布投影已同步;JSON 态先过语法解析)
      const got = editorRef.current.getDefinition();
      if (!got.ok) throw new Error(got.error);
      return saveWorkflow({
        wf_key: editing.wfKey.trim(),
        name: editing.name.trim(),
        definition: got.definition,
      });
    },
    onSuccess: (res) => {
      // 布局坐标迁移到新版本键(须在卸载编辑器前完成)
      editorRef.current?.notifySaved(res.version);
      setEditing(null);
      setFormError("");
      invalidate();
    },
    onError: (e) => setFormError(e instanceof Error ? e.message : "保存失败"),
  });

  const toggle = useMutation({
    mutationFn: async ({ row, enabled }: { row: WorkflowItem; enabled: boolean }) => {
      await setWorkflowEnabled(row.wf_key, enabled);
    },
    onSuccess: (_d, v) => {
      setToggleError((m) => ({ ...m, [v.row.wf_key]: "" }));
      invalidate();
    },
    onError: (e, v) =>
      setToggleError((m) => ({
        ...m,
        [v.row.wf_key]: e instanceof Error ? e.message : "操作失败",
      })),
  });

  const remove = useMutation({
    mutationFn: async () => {
      if (deleting) await deleteWorkflow(deleting.wf_key);
    },
    onSuccess: () => {
      setDeleting(null);
      setDeleteError("");
      invalidate();
    },
    onError: (e) => setDeleteError(e instanceof Error ? e.message : "删除失败"),
  });

  const run = useMutation({
    mutationFn: async () => {
      if (!running) return null;
      return runWorkflow(running.wf_key, parseRunInputs(runInputs));
    },
    onSuccess: (out) => {
      setRunResult(out);
      setRunError("");
      void qc.invalidateQueries({ queryKey: ["admin", "workflow-runs"] });
    },
    onError: (e) => {
      setRunResult(null);
      setRunError(e instanceof Error ? e.message : "运行失败");
    },
  });

  return {
    editorRef,
    editing,
    setEditing,
    formError,
    setFormError,
    deleting,
    setDeleting,
    deleteError,
    setDeleteError,
    running,
    setRunning,
    runInputs,
    setRunInputs,
    runResult,
    setRunResult,
    runError,
    setRunError,
    historyOf,
    setHistoryOf,
    historyPage,
    setHistoryPage,
    detailRunId,
    setDetailRunId,
    toggleError,
    workflows,
    runs,
    runDetail,
    save,
    toggle,
    remove,
    run,
  };
}

/** 列表行操作的动作处理器:新建、编辑、运行、历史、删除的入口状态切换。 */
export function useWorkflowRowActions(
  s: ReturnType<typeof useWorkflowsPageState>,
  emptyDefinition: unknown,
) {
  return {
    openCreate: () => {
      s.setEditing({ origKey: null, wfKey: "", name: "", initialDef: emptyDefinition, version: 0 });
      s.setFormError("");
    },
    openEdit: (row: WorkflowItem) => {
      s.setEditing({
        origKey: row.wf_key,
        wfKey: row.wf_key,
        name: row.name,
        initialDef: row.definition,
        version: row.version,
      });
      s.setFormError("");
    },
    openRun: (row: WorkflowItem) => {
      s.setRunning(row);
      s.setRunInputs("{}");
      s.setRunResult(null);
      s.setRunError("");
    },
    openHistory: (row: WorkflowItem) => {
      s.setHistoryOf(row);
      s.setHistoryPage(1);
    },
    openDelete: (row: WorkflowItem) => {
      s.setDeleting(row);
      s.setDeleteError("");
    },
  };
}
