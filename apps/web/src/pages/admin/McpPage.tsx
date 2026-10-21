/* 管理 MCP 服务和工具、资源、模板；编辑时密钥留空表示保留，清空键值表示删除。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, Loader2, Plus, Trash2 } from "lucide-react";

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
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FormRow } from "@/components/form-row";
import { PageHeader } from "@/components/page-header";
import { StatusSwitch } from "@/components/status-switch";
import { DataView } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import {
  createMcpServer,
  deleteMcpServer,
  listMcpServers,
  previewMcpPrompt,
  setMcpServerEnabled,
  syncMcpServer,
  testMcpServer,
  updateMcpServer,
  type McpPromptEntryOut,
  type McpPromptPreviewOut,
  type McpServerOut,
} from "@/lib/mcp-api";

type HeaderRow = { key: string; value: string };

type FormState = {
  id: string | null; // null=新建
  serverKey: string;
  name: string;
  endpoint: string;
  headers: HeaderRow[];
  removedKeys: string[]; // 编辑态删除的头键(提交时空串=服务端删除该键)
  existingKeys: string[]; // 编辑态已有键；键名锁定，避免改名后产生重复配置。
};

const EMPTY_FORM: FormState = {
  id: null,
  serverKey: "",
  name: "",
  endpoint: "",
  headers: [],
  removedKeys: [],
  existingKeys: [],
};

export default function McpPage() {
  const qc = useQueryClient();
  const [form, setForm] = useState<FormState | null>(null);
  const [formError, setFormError] = useState("");
  const [deleting, setDeleting] = useState<McpServerOut | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const [rowNote, setRowNote] = useState<Record<string, string>>({});
  const [busyKey, setBusyKey] = useState<string | null>(null);
  // 原语清单(工具/资源/模板)与模板只读预览
  const [primitives, setPrimitives] = useState<McpServerOut | null>(null);
  const [previewing, setPreviewing] = useState<McpPromptEntryOut | null>(null);
  const [previewArgs, setPreviewArgs] = useState<Record<string, string>>({});
  const [previewData, setPreviewData] = useState<McpPromptPreviewOut | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);

  const servers = useQuery({ queryKey: ["admin", "mcp"], queryFn: listMcpServers });

  const invalidate = () => void qc.invalidateQueries({ queryKey: ["admin", "mcp"] });

  function note(id: string, text: string): void {
    setRowNote((m) => ({ ...m, [id]: text }));
  }

  const save = useMutation({
    mutationFn: async () => {
      if (!form) return;
      if (form.id === null) {
        // 新建:空值行忽略(无"保持现值"语义)
        await createMcpServer({
          server_key: form.serverKey.trim(),
          name: form.name.trim(),
          endpoint: form.endpoint.trim(),
          headers: Object.fromEntries(
            form.headers
              .filter((h) => h.key.trim() && h.value !== "")
              .map((h) => [h.key.trim(), h.value]),
          ),
        });
        return;
      }
      // 编辑:值留空=保持现值(null);删除的行=空串(服务端删除该键)
      const headers: Record<string, string | null> = {};
      for (const k of form.removedKeys) headers[k] = ""; // 先删
      for (const h of form.headers) {
        if (h.key.trim()) headers[h.key.trim()] = h.value === "" ? null : h.value;
      }
      await updateMcpServer(form.id, {
        name: form.name.trim(),
        endpoint: form.endpoint.trim(),
        headers,
      });
    },
    onSuccess: () => {
      setForm(null);
      setFormError("");
      invalidate();
    },
    onError: (e) => setFormError(e instanceof Error ? e.message : "保存失败"),
  });

  const [toggleError, setToggleError] = useState<Record<string, string>>({});
  const toggle = useMutation({
    mutationFn: async ({ row, enabled }: { row: McpServerOut; enabled: boolean }) => {
      await setMcpServerEnabled(row.id, enabled);
    },
    onSuccess: (_d, v) => {
      setToggleError((m) => ({ ...m, [v.row.id]: "" }));
      invalidate();
    },
    onError: (e, v) =>
      setToggleError((m) => ({
        ...m,
        [v.row.id]: e instanceof Error ? e.message : "操作失败",
      })),
  });

  const remove = useMutation({
    mutationFn: async () => {
      if (deleting) await deleteMcpServer(deleting.id);
    },
    onSuccess: () => {
      setDeleting(null);
      setDeleteError("");
      invalidate();
    },
    onError: (e) => setDeleteError(e instanceof Error ? e.message : "删除失败"),
  });

  async function runAction(row: McpServerOut, kind: "test" | "sync"): Promise<void> {
    if (busyKey) return;
    setBusyKey(`${row.id}:${kind}`);
    try {
      if (kind === "test") {
        const r = await testMcpServer(row.id);
        note(
          row.id,
          r.ok
            ? `连通正常:工具 ${r.tools ?? 0} / 资源 ${r.resources ?? 0} / 模板 ${r.prompts ?? 0}`
            : `连通失败(${r.kind}):${r.detail}`,
        );
      } else {
        const r = await syncMcpServer(row.id);
        note(
          row.id,
          r.ok
            ? `同步完成:注册工具 ${r.tools ?? 0} / 资源 ${r.resources ?? 0} / 模板 ${r.prompts ?? 0}`
            : `同步失败(${r.kind}):${r.detail}`,
        );
        invalidate();
      }
    } catch (e) {
      note(row.id, e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusyKey(null);
    }
  }

  function openEdit(row: McpServerOut): void {
    setForm({
      id: row.id,
      serverKey: row.server_key,
      name: row.name,
      endpoint: row.endpoint,
      headers: row.header_keys.map((k) => ({ key: k, value: "" })),
      removedKeys: [],
      existingKeys: [...row.header_keys],
    });
    setFormError("");
  }

  function openPreview(prompt: McpPromptEntryOut): void {
    // 参数缺省空值;required 声明仅供填写参考(缺参由 server 渲染语义兜底)
    setPreviewArgs(Object.fromEntries((prompt.arguments ?? []).map((a) => [a.name, ""])));
    setPreviewing(prompt);
    setPreviewData(null);
    setPreviewError("");
  }

  async function runPreview(serverId: string, name: string): Promise<void> {
    setPreviewLoading(true);
    setPreviewError("");
    setPreviewData(null);
    try {
      setPreviewData(await previewMcpPrompt(serverId, { name, arguments: previewArgs }));
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : "预览拉取失败");
    } finally {
      setPreviewLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="MCP 服务"
        description="外部 MCP server 的工具经桥接进入平台工具集(无 readOnlyHint 标注的工具按写工具走人工确认);自定义请求头加密落库,任何界面不回显明文。"
        actions={
          <Button
            onClick={() => {
              setForm(EMPTY_FORM);
              setFormError("");
            }}
          >
            <Plus aria-hidden />
            接入 MCP 服务
          </Button>
        }
      />

      {servers.isError && <ErrorAlert />}

      <DataView
        rows={servers.data ?? []}
        rowKey={(row) => row.id}
        isLoading={servers.isPending}
        isEmpty={!servers.isPending && !servers.isError && (servers.data ?? []).length === 0}
        emptyText="尚未接入 MCP 服务。"
        columns={[
          {
            key: "server_key",
            header: "标识",
            cardPriority: 4,
            cellClassName: "font-mono text-xs",
            render: (row) => row.server_key,
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
            key: "endpoint",
            header: "Endpoint",
            cardPriority: 2, // 连接标识(服务地址)窄屏卡片可见
            render: (row) => (
              <span className="block max-w-56 truncate font-mono text-xs" title={row.endpoint}>
                {row.endpoint}
              </span>
            ),
          },
          {
            key: "tools",
            header: "工具",
            cardPriority: 3,
            render: (row) => (
              <>
                <Badge variant="secondary">{row.tools.length} 个</Badge>
                {row.last_sync_error && (
                  <p
                    className="mt-1 max-w-52 truncate text-xs text-destructive"
                    title={row.last_sync_error}
                  >
                    {row.last_sync_error}
                  </p>
                )}
                {rowNote[row.id] && (
                  <p
                    className="mt-1 max-w-72 truncate text-xs text-muted-foreground"
                    title={rowNote[row.id]}
                    role="status"
                  >
                    {rowNote[row.id]}
                  </p>
                )}
              </>
            ),
          },
          {
            key: "enabled",
            header: "启用",
            cardPriority: 2,
            render: (row) => (
              <StatusSwitch
                active={row.enabled}
                pending={toggle.isPending && toggle.variables?.row.id === row.id}
                error={toggleError[row.id] || null}
                onToggle={() => toggle.mutate({ row, enabled: !row.enabled })}
                ariaLabel={`启停 ${row.name}`}
              />
            ),
          },
        ]}
        actions={[
          {
            // 行级禁用:测试/同步共用 busyKey,任一进行中即冻结全部行操作
            label: "测试",
            isDisabled: () => busyKey !== null,
            disabledReason: "操作进行中",
            onSelect: (row) => void runAction(row, "test"),
          },
          {
            label: "同步",
            isDisabled: () => busyKey !== null,
            disabledReason: "操作进行中",
            onSelect: (row) => void runAction(row, "sync"),
          },
          { label: "原语清单", onSelect: (row) => setPrimitives(row) },
          { label: "编辑", onSelect: openEdit },
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

      {/* 新建/编辑对话框 */}
      <Dialog
        open={form !== null}
        onOpenChange={(o) => {
          if (save.isPending) return; // 提交中不可关
          if (!o) setForm(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{form?.id ? "编辑 MCP 服务" : "接入 MCP 服务"}</DialogTitle>
          </DialogHeader>
          {form && (
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                save.mutate();
              }}
            >
              <FormRow label="标识(server_key)" htmlFor="mcp-key">
                <Input
                  id="mcp-key"
                  value={form.serverKey}
                  disabled={form.id !== null} // 创建后不可改
                  onChange={(e) => setForm({ ...form, serverKey: e.target.value })}
                  placeholder="小写字母开头,如 fs"
                  className="font-mono"
                />
              </FormRow>
              <FormRow label="名称" htmlFor="mcp-name">
                <Input
                  id="mcp-name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </FormRow>
              <FormRow label="Endpoint(Streamable HTTP)" htmlFor="mcp-endpoint">
                <Input
                  id="mcp-endpoint"
                  value={form.endpoint}
                  onChange={(e) => setForm({ ...form, endpoint: e.target.value })}
                  placeholder="https://example.com/mcp"
                  className="font-mono text-xs"
                />
              </FormRow>
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <Label>自定义请求头</Label>
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    onClick={() =>
                      setForm({ ...form, headers: [...form.headers, { key: "", value: "" }] })
                    }
                  >
                    <Plus aria-hidden />
                    加一行
                  </Button>
                </div>
                {form.headers.length === 0 && (
                  <p className="text-xs text-muted-foreground">无(按需添加鉴权头)</p>
                )}
                {form.headers.map((h, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <Input
                      value={h.key}
                      disabled={form.existingKeys.includes(h.key)}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          headers: form.headers.map((x, j) =>
                            j === i ? { ...x, key: e.target.value } : x,
                          ),
                        })
                      }
                      placeholder="Authorization"
                      className="font-mono text-xs"
                      aria-label={`请求头键 ${i + 1}`}
                    />
                    <Input
                      value={h.value}
                      type="password"
                      onChange={(e) =>
                        setForm({
                          ...form,
                          headers: form.headers.map((x, j) =>
                            j === i ? { ...x, value: e.target.value } : x,
                          ),
                        })
                      }
                      placeholder={form.id ? "留空保持现值" : "值"}
                      className="font-mono text-xs"
                      aria-label={`请求头值 ${i + 1}`}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      aria-label={`移除请求头 ${i + 1}`}
                      onClick={() => {
                        const removed = form.headers[i];
                        setForm({
                          ...form,
                          headers: form.headers.filter((_, j) => j !== i),
                          removedKeys:
                            form.id !== null && removed.key.trim()
                              ? [...form.removedKeys, removed.key.trim()]
                              : form.removedKeys,
                        });
                      }}
                    >
                      <Trash2 aria-hidden />
                    </Button>
                  </div>
                ))}
                {form.id !== null && (
                  <p className="text-xs text-muted-foreground">
                    值留空=保持现值;删除行=保存后移除该请求头。
                  </p>
                )}
              </div>
              {formError && (
                <p role="alert" className="text-sm text-destructive">
                  {formError}
                </p>
              )}
              <DialogFooter>
                <Button type="submit" disabled={save.isPending}>
                  {save.isPending && <Loader2 className="animate-spin" aria-hidden />}
                  保存并同步
                </Button>
              </DialogFooter>
            </form>
          )}
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
            <AlertDialogTitle>删除 MCP 服务「{deleting?.name}」?</AlertDialogTitle>
            <AlertDialogDescription>
              删除后其全部工具立即从注册面消失,已绑定这些工具的 Bot 运行时调用将被拒。
              此操作不可恢复。
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

      {/* 原语清单:工具/资源/模板分区;清单来自最近一次同步快照 */}
      <Dialog
        open={primitives !== null}
        onOpenChange={(o) => {
          if (!o) {
            setPrimitives(null);
            setPreviewing(null);
          }
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>原语清单「{primitives?.name}」</DialogTitle>
          </DialogHeader>
          {primitives && (
            <div className="space-y-5">
              <section>
                <h3 className="flex items-center gap-2 text-sm font-medium">
                  工具
                  <Badge variant="secondary">{primitives.tools.length}</Badge>
                </h3>
                {primitives.tools.length === 0 ? (
                  <p className="mt-1.5 text-xs text-muted-foreground">无(同步后可见)。</p>
                ) : (
                  <ul className="mt-1.5 space-y-1.5">
                    {primitives.tools.map((t) => (
                      <li key={t.qualified} className="text-xs">
                        <span className="font-mono">{t.qualified}</span>
                        {t.read_only && (
                          <Badge variant="outline" className="ml-1.5">
                            只读
                          </Badge>
                        )}
                        {t.description && <p className="text-muted-foreground">{t.description}</p>}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              <section>
                <h3 className="flex items-center gap-2 text-sm font-medium">
                  资源
                  <Badge variant="secondary">{primitives.resources.length}</Badge>
                </h3>
                {primitives.resources.length === 0 ? (
                  <p className="mt-1.5 text-xs text-muted-foreground">
                    无(server 未提供 resources 原语时如实为 0)。
                  </p>
                ) : (
                  <ul className="mt-1.5 space-y-1.5">
                    {primitives.resources.map((r) => (
                      <li key={r.uri} className="text-xs">
                        <span className="font-mono">{r.uri}</span>
                        {r.media_type && (
                          <Badge variant="outline" className="ml-1.5">
                            {r.media_type}
                          </Badge>
                        )}
                        {r.name && <span className="ml-1.5">{r.name}</span>}
                        {r.description && <p className="text-muted-foreground">{r.description}</p>}
                      </li>
                    ))}
                  </ul>
                )}
                <p className="mt-1.5 text-xs text-muted-foreground">
                  资源经工具 mcp__{primitives.server_key}__read_resource 读取(uri 白名单约束)。
                </p>
              </section>
              <section>
                <h3 className="flex items-center gap-2 text-sm font-medium">
                  提示模板
                  <Badge variant="secondary">{primitives.prompts.length}</Badge>
                </h3>
                {primitives.prompts.length === 0 ? (
                  <p className="mt-1.5 text-xs text-muted-foreground">
                    无(server 未提供 prompts 原语时如实为 0)。
                  </p>
                ) : (
                  <ul className="mt-1.5 space-y-1.5">
                    {primitives.prompts.map((p) => (
                      <li key={p.name} className="flex items-start justify-between gap-2 text-xs">
                        <div className="min-w-0">
                          <span className="font-mono">{p.name}</span>
                          {p.description && (
                            <p className="text-muted-foreground">{p.description}</p>
                          )}
                          {(p.arguments ?? []).length > 0 && (
                            <p className="text-muted-foreground">
                              参数:
                              {(p.arguments ?? [])
                                .map((a) => (a.required ? `${a.name}*` : a.name))
                                .join(", ")}
                              (* 为必填)
                            </p>
                          )}
                        </div>
                        <Button
                          type="button"
                          variant="ghost"
                          size="xs"
                          aria-label={`预览模板 ${p.name}`}
                          onClick={() => openPreview(p)}
                        >
                          <Eye aria-hidden />
                          预览
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              <DialogFooter>
                <Button variant="outline" onClick={() => setPrimitives(null)}>
                  关闭
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* 模板只读预览:按名实时拉取渲染结果,服务端已过注入检测+大小上限;
          只读展示,不注入任何提示词、非运行时提示来源 */}
      <AlertDialog
        open={previewing !== null}
        onOpenChange={(o) => {
          if (!o) setPreviewing(null);
        }}
      >
        <AlertDialogContent className="sm:max-w-xl">
          <AlertDialogHeader>
            <AlertDialogTitle>模板预览「{previewing?.name}」</AlertDialogTitle>
            <AlertDialogDescription>
              只读渲染结果(实时拉取,不落库);内容已过注入检测,超 64KB 截断。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {previewing && (previewing.arguments ?? []).length > 0 && (
            <div className="space-y-2">
              {(previewing.arguments ?? []).map((a) => (
                <FormRow
                  key={a.name}
                  label={`${a.name}${a.required ? " *" : ""}`}
                  htmlFor={`pv-${a.name}`}
                >
                  <Input
                    id={`pv-${a.name}`}
                    value={previewArgs[a.name] ?? ""}
                    onChange={(e) => setPreviewArgs({ ...previewArgs, [a.name]: e.target.value })}
                    aria-label={`参数 ${a.name}`}
                  />
                </FormRow>
              ))}
            </div>
          )}
          {previewLoading && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
              <Loader2 className="animate-spin" aria-hidden />
              拉取中…
            </p>
          )}
          {previewError && (
            <p role="alert" className="text-sm text-destructive">
              {previewError}
            </p>
          )}
          {previewData && !previewData.ok && (
            <p role="alert" className="text-sm text-destructive">
              拉取失败({previewData.kind}):{previewData.detail}
            </p>
          )}
          {previewData?.ok && (
            <div className="space-y-1.5">
              {previewData.injection_hits.length > 0 && (
                <p className="text-xs text-destructive">
                  内容命中注入检测已净化:{previewData.injection_hits.join(", ")}
                </p>
              )}
              {previewData.truncated && (
                <p className="text-xs text-muted-foreground">内容超 64KB 已截断。</p>
              )}
              <pre className="max-h-72 overflow-y-auto rounded-md border bg-muted/40 p-3 text-xs whitespace-pre-wrap">
                {previewData.text}
              </pre>
            </div>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel>关闭</AlertDialogCancel>
            <Button
              disabled={previewLoading || primitives === null || previewing === null}
              onClick={() => {
                if (primitives !== null && previewing !== null) {
                  void runPreview(primitives.id, previewing.name);
                }
              }}
            >
              {previewLoading ? (
                <Loader2 className="animate-spin" aria-hidden />
              ) : (
                <Eye aria-hidden />
              )}
              {previewData !== null ? "重新拉取" : "拉取预览"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
