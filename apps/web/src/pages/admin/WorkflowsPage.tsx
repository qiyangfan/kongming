/* 工作流管理页(029 workflow-orchestration spec):
   定义列表(最新版本)+ JSON 编辑器(保存即结构校验,服务端错误行内反馈;
   v1 不做拖拽画布——Non-goal)+ 启停 StatusSwitch + 删除受控确认 +
   运行对话框(inputs JSON)+ run 历史分页(节点状态逐行展开)。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Loader2, Play, Plus, Trash2 } from "lucide-react";

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
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { StatusSwitch } from "@/components/status-switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { QueryErrorAlert } from "@/components/query-error";
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

const TEMPLATE = `{
  "nodes": [
    { "id": "q", "type": "sql", "config": { "sql": "SELECT ..." } },
    { "id": "t", "type": "transform",
      "config": { "template": "共 {{q.outputs.row_count}} 行" },
      "depends_on": ["q"] }
  ]
}`;

const STATUS_TEXT: Record<string, string> = {
  SUCCESS: "成功",
  FAILED: "失败",
  TIMEOUT: "超时",
  STEP_LIMIT: "超步数",
};

export default function WorkflowsPage() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<{
    origKey: string | null; // 审查循环 B3:编辑态锁 wf_key(改 key 保存=静默新建另一工作流)
    wfKey: string;
    name: string;
    defText: string;
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
  // D2:run 详情对话框(存 runId;详情数据只来自新端点)
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
      if (!editing) return;
      let definition: unknown;
      try {
        definition = JSON.parse(editing.defText);
      } catch (e) {
        throw new Error(`JSON 解析失败: ${e instanceof Error ? e.message : e}`);
      }
      await saveWorkflow({
        wf_key: editing.wfKey.trim(),
        name: editing.name.trim(),
        definition,
      });
    },
    onSuccess: () => {
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
        throw new Error(`inputs JSON 解析失败: ${e instanceof Error ? e.message : e}`);
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
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">工作流</h1>
        <Button
          onClick={() => {
            setEditing({ origKey: null, wfKey: "", name: "", defText: TEMPLATE });
            setFormError("");
          }}
        >
          <Plus aria-hidden />
          新建工作流
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">
        JSON DAG 编排:节点类型 sql/http/condition/loop/transform/agent,依赖经
        depends_on 声明;同名再保存即新版本(旧版本冻结)。SQL 走只读防御、
        HTTP 有 SSRF 防护、agent 以运行者权限执行。引用写法
        {"{{节点id.outputs.字段}}"}(不支持数组下标;loop 的 items_ref 填裸路径,
        不带大括号)。
      </p>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>标识</TableHead>
              <TableHead>名称</TableHead>
              <TableHead>版本</TableHead>
              <TableHead>节点数</TableHead>
              <TableHead>启用</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {workflows.isError ? (
              <TableRow>
                <TableCell colSpan={6}>
                  <QueryErrorAlert />
                </TableCell>
              </TableRow>
            ) : (workflows.data ?? []).length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">
                  尚未定义工作流。
                </TableCell>
              </TableRow>
            ) : (
              (workflows.data ?? []).map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="font-mono text-xs">{row.wf_key}</TableCell>
                  <TableCell>{row.name}</TableCell>
                  <TableCell>
                    <Badge variant="secondary" className="font-normal">
                      v{row.version}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {Array.isArray((row.definition as { nodes?: unknown[] }).nodes)
                      ? ((row.definition as { nodes?: unknown[] }).nodes as unknown[]).length
                      : 0}
                  </TableCell>
                  <TableCell>
                    <StatusSwitch
                      active={row.enabled}
                      pending={toggle.isPending && toggle.variables?.row.wf_key === row.wf_key}
                      error={toggleError[row.wf_key] || null}
                      onToggle={() => toggle.mutate({ row, enabled: !row.enabled })}
                      ariaLabel={`启停 ${row.name}`}
                    />
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="xs"
                        disabled={!row.enabled}
                        onClick={() => {
                          setRunning(row);
                          setRunInputs("{}");
                          setRunResult(null);
                          setRunError("");
                        }}
                      >
                        <Play aria-hidden />
                        运行
                      </Button>
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={() => {
                          setEditing({
                            origKey: row.wf_key,
                            wfKey: row.wf_key,
                            name: row.name,
                            defText: JSON.stringify(row.definition, null, 2),
                          });
                          setFormError("");
                        }}
                      >
                        编辑
                      </Button>
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={() => {
                          setHistoryOf(row);
                          setHistoryPage(1);
                        }}
                      >
                        历史
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={`删除 ${row.name}`}
                        onClick={() => {
                          setDeleting(row);
                          setDeleteError("");
                        }}
                      >
                        <Trash2 aria-hidden />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* 新建/编辑(JSON 编辑器) */}
      <Dialog
        open={editing !== null}
        onOpenChange={(o) => {
          if (save.isPending) return;
          if (!o) setEditing(null);
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {editing?.wfKey ? `编辑工作流(保存即新版本)` : "新建工作流"}
            </DialogTitle>
            <DialogDescription>
              同名标识再保存自动产生新版本;旧版本冻结不可改。
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
                <div className="space-y-1.5">
                  <Label htmlFor="wf-key">标识(wf_key)</Label>
                  <Input
                    id="wf-key"
                    value={editing.wfKey}
                    onChange={(e) => setEditing({ ...editing, wfKey: e.target.value })}
                    placeholder="小写字母开头"
                    className="font-mono"
                    disabled={editing.origKey !== null}
                    aria-disabled={editing.origKey !== null}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="wf-name">名称</Label>
                  <Input
                    id="wf-name"
                    value={editing.name}
                    onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                  />
                </div>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="wf-def">定义(JSON DAG)</Label>
                <Textarea
                  id="wf-def"
                  value={editing.defText}
                  onChange={(e) => setEditing({ ...editing, defText: e.target.value })}
                  rows={14}
                  className="font-mono text-xs leading-relaxed"
                />
              </div>
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
            <div className="space-y-1.5">
              <Label htmlFor="wf-inputs">输入(inputs JSON)</Label>
              <Textarea
                id="wf-inputs"
                value={runInputs}
                onChange={(e) => setRunInputs(e.target.value)}
                rows={3}
                className="font-mono text-xs"
              />
            </div>
            {runError && (
              <p role="alert" className="text-sm text-destructive">
                {runError}
              </p>
            )}
            {runResult && (
              <div className="space-y-1 rounded-md border p-3 text-sm">
                <p>
                  结果:
                  <Badge
                    variant={runResult.status === "SUCCESS" ? "secondary" : "destructive"}
                    className="ml-1 font-normal"
                  >
                    {STATUS_TEXT[runResult.status] ?? runResult.status}
                  </Badge>
                  <span className="ml-2 text-xs text-muted-foreground">
                    v{runResult.version} · {runResult.duration_ms}ms
                  </span>
                </p>
                {runResult.error && (
                  <p className="text-xs text-destructive">{runResult.error}</p>
                )}
                {nodeRunsOf(runResult).map((nr) => (
                  <p key={nr.node_id} className="text-xs text-muted-foreground">
                    {nr.node_id}: {nr.status}
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
              <QueryErrorAlert />
            ) : (runs.data?.items ?? []).length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                暂无运行记录。
              </p>
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
                        <Badge
                          variant={r.status === "SUCCESS" ? "secondary" : "destructive"}
                          className="font-normal"
                        >
                          {STATUS_TEXT[r.status] ?? r.status}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          v{r.version} · {r.trigger_source} · {r.duration_ms}ms ·{" "}
                          {r.created_at ? new Date(r.created_at).toLocaleString() : ""}
                        </span>
                      </CollapsibleTrigger>
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-7 shrink-0"
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
                              {nr.status}
                            </span>
                            {nr.error && (
                              <span className="text-destructive"> — {nr.error}</span>
                            )}
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

      {/* run 详情(D2:独立端点,node_runs 含 outputs;列表行展示不动) */}
      <Dialog
        open={detailRunId !== null}
        onOpenChange={(o) => {
          if (!o) setDetailRunId(null);
        }}
      >
        <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>运行详情</DialogTitle>
            <DialogDescription className="font-mono text-xs break-all">
              {detailRunId}
            </DialogDescription>
          </DialogHeader>
          {runDetail.isLoading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" aria-hidden />
              加载中…
            </div>
          ) : runDetail.isError ? (
            <QueryErrorAlert />
          ) : runDetail.data ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge
                  variant={runDetail.data.status === "SUCCESS" ? "secondary" : "destructive"}
                  className="font-normal"
                >
                  {STATUS_TEXT[runDetail.data.status] ?? runDetail.data.status}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  v{runDetail.data.version} · 触发源 {runDetail.data.trigger_source} ·{" "}
                  {runDetail.data.duration_ms}ms
                  {runDetail.data.created_at
                    ? ` · ${new Date(runDetail.data.created_at).toLocaleString()}`
                    : ""}
                </span>
              </div>
              {runDetail.data.error && (
                <p className="text-sm text-destructive">{runDetail.data.error}</p>
              )}
              <div className="space-y-2">
                {(runDetail.data.node_runs ?? []).map((raw, i) => {
                  const nr = nodeRunsOf({ node_runs: [raw] })[0];
                  // R1-B7:渲染前截断 64KB(引擎节点级截断兜底之外的第二道防线,
                  // 防多 MB 文本整段进 DOM)
                  // R2-B1:outputs 为 null(未产出)不渲染折叠区——
                  // stringify(null) 会得到无信息量的字面 "null"
                  const outputsRaw =
                    raw.outputs == null ? null : JSON.stringify(raw.outputs, null, 2);
                  const outputs: string | null =
                    outputsRaw == null
                      ? null
                      : outputsRaw.length > 64 * 1024
                        ? outputsRaw.slice(0, 64 * 1024) + "\n…(截断,共 " + outputsRaw.length + " 字符)"
                        : outputsRaw;
                  return (
                    <div key={nr.node_id || i} className="rounded-md border px-3 py-2">
                      <div className="flex flex-wrap items-center gap-2 text-sm">
                        <span className="font-mono text-xs">{nr.node_id}</span>
                        <Badge
                          variant={nr.status === "FAILED" ? "destructive" : "secondary"}
                          className="font-normal"
                        >
                          {nr.status}
                        </Badge>
                        {nr.duration_ms !== undefined && (
                          <span className="text-xs text-muted-foreground">
                            {nr.duration_ms}ms
                          </span>
                        )}
                        {nr.error && (
                          <span className="text-xs text-destructive">{nr.error}</span>
                        )}
                      </div>
                      {outputs !== null && (
                        <Collapsible>
                          <CollapsibleTrigger className="group mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                            <ChevronRight
                              className="size-3 transition-transform group-data-[state=open]:rotate-90"
                              aria-hidden
                            />
                            outputs
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
