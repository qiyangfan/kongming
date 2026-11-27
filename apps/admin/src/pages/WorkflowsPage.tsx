/* 管理工作流定义和运行记录；定义可用画布或 JSON 编辑，服务端校验错误会定位到对应节点。 */

import { Plus } from "lucide-react";

import { Button } from "@kongming/ui/button";
import { DataView } from "@kongming/ui/data-view";
import { ErrorAlert } from "@kongming/ui/error-alert";
import { PageHeader } from "@kongming/ui/page-header";

import { workflowColumns } from "./workflows/WorkflowColumns";
import {
  DeleteWorkflowDialog,
  RunWorkflowDialog,
  WorkflowEditorDialog,
} from "./workflows/WorkflowDialogs";
import { EMPTY_DEFINITION } from "./workflows/workflow-format";
import { RunDetailDialog, RunHistoryDialog } from "./workflows/WorkflowRunDialogs";
import {
  PAGE_SIZE,
  useWorkflowRowActions,
  useWorkflowsPageState,
} from "./workflows/use-workflows-page-state";

export default function WorkflowsPage() {
  const s = useWorkflowsPageState();
  const row = useWorkflowRowActions(s, EMPTY_DEFINITION);

  return (
    <div className="space-y-4">
      <PageHeader
        title="工作流"
        description="用画布拖拽编排多步骤自动任务:SQL 查询、HTTP 调用、条件分支、循环、模板转换与 AI 步骤;同名保存自动产生新版本,旧版本冻结。"
        actions={
          <Button onClick={row.openCreate}>
            <Plus aria-hidden />
            新建工作流
          </Button>
        }
      />

      {s.workflows.isError && <ErrorAlert />}

      <DataView
        rows={s.workflows.data ?? []}
        rowKey={(w) => w.id}
        isLoading={s.workflows.isPending}
        isEmpty={
          !s.workflows.isPending && !s.workflows.isError && (s.workflows.data ?? []).length === 0
        }
        emptyText="尚未定义工作流。"
        columns={workflowColumns({
          togglePending: s.toggle.isPending,
          toggleKey: s.toggle.variables?.row.wf_key,
          toggleError: s.toggleError,
          onToggle: (w, enabled) => s.toggle.mutate({ row: w, enabled }),
        })}
        actions={[
          {
            label: "运行",
            // 停用的工作流不可运行;行级禁用保留可视反馈
            isDisabled: (w) => !w.enabled,
            disabledReason: "已停用的工作流不可运行",
            onSelect: row.openRun,
          },
          { label: "编辑", onSelect: row.openEdit },
          { label: "历史", onSelect: row.openHistory },
          { label: "删除", destructive: true, onSelect: row.openDelete },
        ]}
      />

      {/* 新建/编辑(双态编辑器:画布 ⇄ JSON) */}
      <WorkflowEditorDialog
        editing={s.editing}
        onEditingChange={s.setEditing}
        formError={s.formError}
        pending={s.save.isPending}
        editorRef={s.editorRef}
        onSubmit={() => s.save.mutate()}
        onClose={() => s.setEditing(null)}
      />

      <RunWorkflowDialog
        workflow={s.running}
        inputs={s.runInputs}
        onInputsChange={s.setRunInputs}
        result={s.runResult}
        error={s.runError}
        pending={s.run.isPending}
        onSubmit={() => s.run.mutate()}
        onClose={() => s.setRunning(null)}
      />

      <RunHistoryDialog
        workflow={s.historyOf}
        runs={s.runs}
        page={s.historyPage}
        pageSize={PAGE_SIZE}
        onPageChange={s.setHistoryPage}
        onDetail={s.setDetailRunId}
        onClose={() => {
          s.setHistoryOf(null);
          s.setDetailRunId(null); // 详情叠层随历史面板一起收起
        }}
      />

      <RunDetailDialog
        runId={s.detailRunId}
        detail={s.runDetail}
        onClose={() => s.setDetailRunId(null)}
      />

      <DeleteWorkflowDialog
        workflow={s.deleting}
        error={s.deleteError}
        pending={s.remove.isPending}
        onConfirm={() => s.remove.mutate()}
        onClose={() => s.setDeleting(null)}
      />
    </div>
  );
}
