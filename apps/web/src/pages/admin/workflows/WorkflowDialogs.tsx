/* 工作流的定义编辑、运行与删除三个弹层。 */

import type { RefObject } from "react";
import { Loader2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FormRow } from "@/components/form-row";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/status-badge";
import { Textarea } from "@/components/ui/textarea";
import {
  WorkflowDefinitionEditor,
  type WorkflowDefinitionEditorHandle,
} from "@/components/workflow/workflow-definition-editor";
import { formatDuration } from "@/lib/datetime";
import { nodeRunsOf, type WorkflowItem, type WorkflowRunOut } from "@/lib/workflow-api";

import type { EditingState } from "./use-workflows-page-state";
import { nodeStatusLabel, RUN_STATUS_META } from "./workflow-format";

export function WorkflowEditorDialog({
  editing,
  onEditingChange,
  formError,
  pending,
  editorRef,
  onSubmit,
  onClose,
}: {
  editing: EditingState | null;
  onEditingChange: (next: EditingState) => void;
  formError: string;
  pending: boolean;
  editorRef: RefObject<WorkflowDefinitionEditorHandle | null>;
  onSubmit: () => void;
  onClose: () => void;
}) {
  return (
    <Dialog
      open={editing !== null}
      onOpenChange={(o) => {
        if (pending) return;
        if (!o) onClose();
      }}
    >
      <DialogContent className="sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{editing?.wfKey ? "编辑工作流" : "新建工作流"}</DialogTitle>
          <DialogDescription>
            同名标识再保存自动产生新版本;旧版本冻结不可改。节点输出用
            {"{{节点id.outputs.字段}}"} 引用(不支持数组下标;loop 的 items_ref
            填不带大括号的裸路径)。
          </DialogDescription>
        </DialogHeader>
        {editing && (
          <EditorForm
            editing={editing}
            onEditingChange={onEditingChange}
            formError={formError}
            pending={pending}
            editorRef={editorRef}
            onSubmit={onSubmit}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

/** 编辑器表单主体:标识/名称 + 双态定义编辑器 + 校验错误回显。 */
function EditorForm({
  editing,
  onEditingChange,
  formError,
  pending,
  editorRef,
  onSubmit,
}: {
  editing: EditingState;
  onEditingChange: (next: EditingState) => void;
  formError: string;
  pending: boolean;
  editorRef: RefObject<WorkflowDefinitionEditorHandle | null>;
  onSubmit: () => void;
}) {
  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <div className="grid grid-cols-2 gap-3">
        <FormRow
          label="标识"
          htmlFor="wf-key"
          help="小写字母开头;同名标识保存即产生新版本,创建后不可改。"
        >
          <Input
            id="wf-key"
            value={editing.wfKey}
            onChange={(e) => onEditingChange({ ...editing, wfKey: e.target.value })}
            placeholder="小写字母开头"
            className="font-mono"
            disabled={editing.origKey !== null}
            aria-disabled={editing.origKey !== null}
          />
        </FormRow>
        <FormRow label="名称" htmlFor="wf-name">
          <Input
            id="wf-name"
            value={editing.name}
            onChange={(e) => onEditingChange({ ...editing, name: e.target.value })}
          />
        </FormRow>
      </div>
      <WorkflowDefinitionEditor
        ref={editorRef}
        initialDefinition={editing.initialDef}
        wfKey={editing.wfKey}
        version={editing.version}
        errorText={formError}
      />
      {formError && (
        <p role="alert" className="text-sm text-destructive">
          {formError}
        </p>
      )}
      <DialogFooter>
        <Button type="submit" disabled={pending}>
          {pending && <Loader2 className="animate-spin" aria-hidden />}
          校验并保存
        </Button>
      </DialogFooter>
    </form>
  );
}

export function RunWorkflowDialog({
  workflow,
  inputs,
  onInputsChange,
  result,
  error,
  pending,
  onSubmit,
  onClose,
}: {
  workflow: WorkflowItem | null;
  inputs: string;
  onInputsChange: (v: string) => void;
  result: WorkflowRunOut | null;
  error: string;
  pending: boolean;
  onSubmit: () => void;
  onClose: () => void;
}) {
  return (
    <Dialog
      open={workflow !== null}
      onOpenChange={(o) => {
        if (pending) return;
        if (!o) onClose();
      }}
    >
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>运行「{workflow?.name}」</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <FormRow label="输入(inputs JSON)" htmlFor="wf-inputs">
            <Textarea
              id="wf-inputs"
              value={inputs}
              onChange={(e) => onInputsChange(e.target.value)}
              rows={3}
              className="font-mono text-xs"
            />
          </FormRow>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          {result && (
            <div className="space-y-1 rounded-md border p-3 text-sm">
              <p>
                结果:
                <span className="ml-1">
                  <StatusBadge
                    meta={RUN_STATUS_META}
                    value={result.status}
                    fallbackVariant="destructive"
                  />
                </span>
                <span className="ml-2 text-xs text-muted-foreground">
                  v{result.version} · {formatDuration(result.duration_ms)}
                </span>
              </p>
              {result.error && <p className="text-xs text-destructive">{result.error}</p>}
              {nodeRunsOf(result).map((nr) => (
                <p key={nr.node_id} className="text-xs text-muted-foreground">
                  {nr.node_id}: {nodeStatusLabel(nr.status)}
                  {nr.error ? ` — ${nr.error}` : ""}
                </p>
              ))}
            </div>
          )}
          <DialogFooter>
            <Button onClick={onSubmit} disabled={pending}>
              {pending && <Loader2 className="animate-spin" aria-hidden />}
              执行
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function DeleteWorkflowDialog({
  workflow,
  error,
  pending,
  onConfirm,
  onClose,
}: {
  workflow: WorkflowItem | null;
  error: string;
  pending: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <AlertDialog
      open={workflow !== null}
      onOpenChange={(o) => {
        if (pending) return;
        if (!o) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除工作流「{workflow?.name}」?</AlertDialogTitle>
          <AlertDialogDescription>
            将删除该标识的全部版本(运行历史保留)。此操作不可恢复。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>再想想</AlertDialogCancel>
          <Button variant="destructive" disabled={pending} onClick={onConfirm}>
            {pending && <Loader2 className="animate-spin" aria-hidden />}
            确认删除
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
