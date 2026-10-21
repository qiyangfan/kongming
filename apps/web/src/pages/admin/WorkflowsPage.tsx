/* 管理工作流定义和运行记录；定义可用画布或 JSON 编辑，服务端校验错误会定位到对应节点。 */

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Loader2, Plus } from "lucide-react";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/empty-state";
import { FormRow } from "@/components/form-row";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/page-header";
import { StatusBadge, type StatusMeta } from "@/components/status-badge";
import { StatusSwitch } from "@/components/status-switch";
import { DataView } from "@/components/data-view";
import { Textarea } from "@/components/ui/textarea";
import { ErrorAlert } from "@/components/error-alert";
import {
  WorkflowDefinitionEditor,
  type WorkflowDefinitionEditorHandle,
} from "@/components/workflow/workflow-definition-editor";
import { formatDateTime, formatDuration } from "@/lib/datetime";
import {
  deleteWorkflow,
  listWorkflows,
  nodeRunsOf,
  pageWorkflowRuns,
  runWorkflow,
  saveWorkflow,
  setWorkflowEnabled,
  workflowRunDetail,
  type WorkflowItem,
  type WorkflowRunOut,
} from "@/lib/workflow-api";

const PAGE_SIZE = 10;

/** 新建工作流的空定义(画布初始态;JSON 态展示为 {"nodes": []}) */
const EMPTY_DEFINITION: unknown = { nodes: [] };

// run/节点状态 → 徽章统一走 StatusBadge(未知值回落 destructive,即非 SUCCESS 一律红);
// SKIPPED 为节点级状态(条件门控/依赖跳过),run 终态不出现
const RUN_STATUS_META: StatusMeta = {
  SUCCESS: { label: "成功", variant: "secondary" },
  FAILED: { label: "失败", variant: "destructive" },
  TIMEOUT: { label: "超时", variant: "destructive" },
  STEP_LIMIT: { label: "超步数", variant: "destructive" },
  SKIPPED: { label: "已跳过", variant: "outline" },
};

/** 节点状态中文(与 RUN_STATUS_META 同源);未收录值回退原值 */
function nodeStatusLabel(s: string): string {
  return RUN_STATUS_META[s]?.label ?? s;
}

// 触发源中文(后端枚举:MANUAL 人工端点 / API 外部通道);未收录值回退原值
const TRIGGER_SOURCE_LABELS: Record<string, string> = {
  MANUAL: "手动触发",
  API: "API 触发",
};

export default function WorkflowsPage() {
  const qc = useQueryClient();
  const editorRef = useRef<WorkflowDefinitionEditorHandle | null>(null);
  // 定义编辑交由双态编辑器(画布 ⇄ JSON);页面只持初始定义与布局键
  const [editing, setEditing] = useState<{
    origKey: string | null; // 编辑态锁定 wf_key，避免改名后保存为另一条工作流。
    wfKey: string;
    name: string;
    initialDef: unknown;
    version: number; // 布局持久化键(新建=0)
  } | null>(null);
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
      let inputs: Record<string, unknown>;
      try {
        inputs = JSON.parse(runInputs || "{}") as Record<string, unknown>;
      } catch (e) {
        throw new Error(`inputs JSON 解析失败: ${e instanceof Error ? e.message : e}`, {
          cause: e,
        });
      }
      return runWorkflow(running.wf_key, inputs);
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

  return (
    <div className="space-y-4">
      <PageHeader
        title="工作流"
        description="用画布拖拽编排多步骤自动任务:SQL 查询、HTTP 调用、条件分支、循环、模板转换与 AI 步骤;同名保存自动产生新版本,旧版本冻结。"
        actions={
          <Button
            onClick={() => {
              setEditing({
                origKey: null,
                wfKey: "",
                name: "",
                initialDef: EMPTY_DEFINITION,
                version: 0,
              });
              setFormError("");
            }}
          >
            <Plus aria-hidden />
            新建工作流
          </Button>
        }
      />

      {workflows.isError && <ErrorAlert />}

      <DataView
        rows={workflows.data ?? []}
        rowKey={(row) => row.id}
        isLoading={workflows.isPending}
        isEmpty={!workflows.isPending && !workflows.isError && (workflows.data ?? []).length === 0}
        emptyText="尚未定义工作流。"
        columns={[
          {
            key: "wf_key",
            header: "标识",
            cellClassName: "font-mono text-xs",
            render: (row) => row.wf_key,
          },
          {
            key: "name",
            header: "名称",
            cardPriority: 1,
            render: (row) => (
              <span className="block max-w-40 truncate" title={row.name}>
                {row.name}
              </span>
            ),
          },
          {
            key: "version",
            header: "版本",
            cardPriority: 3,
            render: (row) => <Badge variant="secondary">v{row.version}</Badge>,
          },
          {
            key: "nodes",
            header: "节点数",
            cardPriority: 4,
            render: (row) =>
              Array.isArray((row.definition as { nodes?: unknown[] }).nodes)
                ? ((row.definition as { nodes?: unknown[] }).nodes as unknown[]).length
                : 0,
          },
          {
            key: "enabled",
            header: "启用",
            cardPriority: 2,
            render: (row) => (
              <StatusSwitch
                active={row.enabled}
                pending={toggle.isPending && toggle.variables?.row.wf_key === row.wf_key}
                error={toggleError[row.wf_key] || null}
                onToggle={() => toggle.mutate({ row, enabled: !row.enabled })}
                ariaLabel={`启停 ${row.name}`}
              />
            ),
          },
        ]}
        actions={[
          {
            label: "运行",
            // 停用的工作流不可运行;行级禁用保留可视反馈
            isDisabled: (row) => !row.enabled,
            disabledReason: "已停用的工作流不可运行",
            onSelect: (row) => {
              setRunning(row);
              setRunInputs("{}");
              setRunResult(null);
              setRunError("");
            },
          },
          {
            label: "编辑",
            onSelect: (row) => {
              setEditing({
                origKey: row.wf_key,
                wfKey: row.wf_key,
                name: row.name,
                initialDef: row.definition,
                version: row.version,
              });
              setFormError("");
            },
          },
          {
            label: "历史",
            onSelect: (row) => {
              setHistoryOf(row);
              setHistoryPage(1);
            },
          },
          {
            label: "删除",
            destructive: true,
            onSelect: (row) => {
              setDeleting(row);
              setDeleteError("");
            },
          },
        ]}
      />

      {/* 新建/编辑(双态编辑器:画布 ⇄ JSON) */}
      <Dialog
        open={editing !== null}
        onOpenChange={(o) => {
          if (save.isPending) return;
          if (!o) setEditing(null);
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
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                save.mutate();
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
                    onChange={(e) => setEditing({ ...editing, wfKey: e.target.value })}
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
                    onChange={(e) => setEditing({ ...editing, name: e.target.value })}
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
                <Button type="submit" disabled={save.isPending}>
                  {save.isPending && <Loader2 className="animate-spin" aria-hidden />}
                  校验并保存
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      {/* 运行对话框 */}
      <Dialog
        open={running !== null}
        onOpenChange={(o) => {
          if (run.isPending) return;
          if (!o) setRunning(null);
        }}
      >
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>运行「{running?.name}」</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <FormRow label="输入(inputs JSON)" htmlFor="wf-inputs">
              <Textarea
                id="wf-inputs"
                value={runInputs}
                onChange={(e) => setRunInputs(e.target.value)}
                rows={3}
                className="font-mono text-xs"
              />
            </FormRow>
            {runError && (
              <p role="alert" className="text-sm text-destructive">
                {runError}
              </p>
            )}
            {runResult && (
              <div className="space-y-1 rounded-md border p-3 text-sm">
                <p>
                  结果:
                  <span className="ml-1">
                    <StatusBadge
                      meta={RUN_STATUS_META}
                      value={runResult.status}
                      fallbackVariant="destructive"
                    />
                  </span>
                  <span className="ml-2 text-xs text-muted-foreground">
                    v{runResult.version} · {formatDuration(runResult.duration_ms)}
                  </span>
                </p>
                {runResult.error && <p className="text-xs text-destructive">{runResult.error}</p>}
                {nodeRunsOf(runResult).map((nr) => (
                  <p key={nr.node_id} className="text-xs text-muted-foreground">
                    {nr.node_id}: {nodeStatusLabel(nr.status)}
                    {nr.error ? ` — ${nr.error}` : ""}
                  </p>
                ))}
              </div>
            )}
            <DialogFooter>
              <Button onClick={() => run.mutate()} disabled={run.isPending}>
                {run.isPending && <Loader2 className="animate-spin" aria-hidden />}
                执行
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>

      {/* 运行历史 */}
      <Dialog
        open={historyOf !== null}
        onOpenChange={(o) => {
          if (!o) {
            setHistoryOf(null);
            setDetailRunId(null); // 详情叠层随历史面板一起收起
          }
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>运行历史「{historyOf?.name}」</DialogTitle>
            <DialogDescription>按时间倒序的执行记录;点「详情」查看完整节点输出。</DialogDescription>
          </DialogHeader>
          <div className="max-h-96 space-y-2 overflow-y-auto">
            {runs.isError ? (
              <ErrorAlert />
            ) : (runs.data?.items ?? []).length === 0 ? (
              <EmptyState>暂无运行记录。</EmptyState>
            ) : (
              (runs.data?.items ?? []).map((r) => (
                <Collapsible key={r.id}>
                  <div className="rounded-md border px-3 py-2">
                    <div className="flex items-center gap-2">
                      <CollapsibleTrigger className="group flex flex-1 items-center gap-2 text-sm">
                        <ChevronRight
                          className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
                          aria-hidden
                        />
                        <StatusBadge
                          meta={RUN_STATUS_META}
                          value={r.status}
                          fallbackVariant="destructive"
                        />
                        <span className="text-xs text-muted-foreground">
                          v{r.version} ·{" "}
                          {TRIGGER_SOURCE_LABELS[r.trigger_source] ?? r.trigger_source} ·{" "}
                          {formatDuration(r.duration_ms)} · {formatDateTime(r.created_at)}
                        </span>
                      </CollapsibleTrigger>
                      <Button
                        variant="outline"
                        size="xs"
                        className="shrink-0"
                        onClick={() => setDetailRunId(r.id)}
                      >
                        详情
                      </Button>
                    </div>
                    <CollapsibleContent>
                      <div className="mt-2 space-y-1 border-t pt-2">
                        {typeof r.error === "string" && r.error && (
                          <p className="text-xs text-destructive">{r.error}</p>
                        )}
                        {nodeRunsOf(r).map((nr) => (
                          <div key={nr.node_id} className="text-xs">
                            <span className="font-mono">{nr.node_id}</span>{" "}
                            <span
                              className={
                                nr.status === "FAILED"
                                  ? "text-destructive"
                                  : "text-muted-foreground"
                              }
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
              ))
            )}
          </div>
          {(runs.data?.total ?? 0) > PAGE_SIZE && (
            <div className="flex items-center justify-between pt-2">
              <Button
                variant="outline"
                size="xs"
                disabled={historyPage <= 1}
                onClick={() => setHistoryPage((p) => p - 1)}
              >
                上一页
              </Button>
              <span className="text-xs text-muted-foreground">
                第 {historyPage} / {Math.ceil((runs.data?.total ?? 0) / PAGE_SIZE)} 页
              </span>
              <Button
                variant="outline"
                size="xs"
                disabled={historyPage * PAGE_SIZE >= (runs.data?.total ?? 0)}
                onClick={() => setHistoryPage((p) => p + 1)}
              >
                下一页
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* run 详情:独立端点,node_runs 含 outputs */}
      <Dialog
        open={detailRunId !== null}
        onOpenChange={(o) => {
          if (!o) setDetailRunId(null);
        }}
      >
        <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>运行详情</DialogTitle>
            <DialogDescription
              className="max-w-full truncate font-mono text-xs"
              title={detailRunId ?? undefined}
            >
              运行 ID:{detailRunId}
            </DialogDescription>
          </DialogHeader>
          {runDetail.isLoading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" aria-hidden />
              加载中…
            </div>
          ) : runDetail.isError ? (
            <ErrorAlert />
          ) : runDetail.data ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <StatusBadge
                  meta={RUN_STATUS_META}
                  value={runDetail.data.status}
                  fallbackVariant="destructive"
                />
                <span className="text-xs text-muted-foreground">
                  v{runDetail.data.version} · 触发源{" "}
                  {TRIGGER_SOURCE_LABELS[runDetail.data.trigger_source] ??
                    runDetail.data.trigger_source}{" "}
                  · {formatDuration(runDetail.data.duration_ms)}
                  {runDetail.data.created_at
                    ? ` · ${formatDateTime(runDetail.data.created_at)}`
                    : ""}
                </span>
              </div>
              {runDetail.data.error && (
                <p className="text-sm text-destructive">{runDetail.data.error}</p>
              )}
              <div className="space-y-2">
                {(runDetail.data.node_runs ?? []).map((raw, i) => {
                  const nr = nodeRunsOf({ node_runs: [raw] })[0];
                  // 渲染前截断 64KB(引擎节点级截断兜底之外的第二道防线,
                  // 防多 MB 文本整段进 DOM)
                  // outputs 为 null(未产出)不渲染折叠区——
                  // stringify(null) 会得到无信息量的字面 "null"
                  const outputsRaw =
                    raw.outputs == null ? null : JSON.stringify(raw.outputs, null, 2);
                  const outputs: string | null =
                    outputsRaw == null
                      ? null
                      : outputsRaw.length > 64 * 1024
                        ? outputsRaw.slice(0, 64 * 1024) +
                          "\n…(截断,共 " +
                          outputsRaw.length +
                          " 字符)"
                        : outputsRaw;
                  return (
                    <div key={nr.node_id || i} className="rounded-md border px-3 py-2">
                      <div className="flex flex-wrap items-center gap-2 text-sm">
                        <span className="font-mono text-xs">{nr.node_id}</span>
                        <StatusBadge
                          meta={RUN_STATUS_META}
                          value={nr.status}
                          fallbackVariant="secondary"
                        />
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
          ) : null}
        </DialogContent>
      </Dialog>

      {/* 删除确认(受控:成功才关) */}
      <AlertDialog
        open={deleting !== null}
        onOpenChange={(o) => {
          if (remove.isPending) return;
          if (!o) setDeleting(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除工作流「{deleting?.name}」?</AlertDialogTitle>
            <AlertDialogDescription>
              将删除该标识的全部版本(运行历史保留)。此操作不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deleteError && (
            <p role="alert" className="text-sm text-destructive">
              {deleteError}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>再想想</AlertDialogCancel>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
            >
              {remove.isPending && <Loader2 className="animate-spin" aria-hidden />}
              确认删除
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
