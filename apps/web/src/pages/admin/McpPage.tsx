/* MCP 服务管理页(028 mcp-interop spec「配置管理」):
   列表(启用态/工具数/同步态)+ 新建/编辑对话框(server_key 创建后不可改;
   headers 键值对编辑——值掩码语义:编辑态留空=保持现值,清空=删除该键)+
   启停 StatusSwitch + 删除受控确认 + 连通性测试/同步(行内结果)。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cable, Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";

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
import { StatusSwitch } from "@/components/status-switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { QueryErrorAlert } from "@/components/query-error";
import {
  createMcpServer,
  deleteMcpServer,
  listMcpServers,
  setMcpServerEnabled,
  syncMcpServer,
  testMcpServer,
  updateMcpServer,
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
  existingKeys: string[]; // 编辑态已有键(键名锁定,改名=并存陷阱 R1-T1)
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
      for (const k of form.removedKeys) headers[k] = ""; // 先删(R1-T1)
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
      // R1-A9:成功清行内错误
      setToggleError((m) => ({ ...m, [v.row.id]: "" }));
      invalidate();
    },
    onError: (e, v) =>
      // 012/008 范式:失败行内 role=alert,不只回弹
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
          r.ok ? `连通正常,发现 ${r.tools ?? 0} 个工具` : `连通失败(${r.kind}):${r.detail}`,
        );
      } else {
        const r = await syncMcpServer(row.id);
        note(
          row.id,
          r.ok
            ? `同步完成,注册 ${r.tools ?? 0} 个工具`
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
      // 掩码语义:键名回显,值留空=保持(028 spec)
      headers: row.header_keys.map((k) => ({ key: k, value: "" })),
      removedKeys: [],
      existingKeys: [...row.header_keys],
    });
    setFormError("");
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">MCP 服务</h1>
        <Button onClick={() => { setForm(EMPTY_FORM); setFormError(""); }}>
          <Plus aria-hidden />
          接入 MCP 服务
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">
        外部 MCP server 的工具经桥接进入平台工具集(无 readOnlyHint 标注的工具按
        写工具走人工确认);自定义请求头加密落库,任何界面不回显明文。
      </p>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>标识</TableHead>
              <TableHead>名称</TableHead>
              <TableHead>Endpoint</TableHead>
              <TableHead>工具</TableHead>
              <TableHead>启用</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {servers.isError ? (
              <TableRow>
                <TableCell colSpan={6}>
                  <QueryErrorAlert />
                </TableCell>
              </TableRow>
            ) : (servers.data ?? []).length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">
                  尚未接入 MCP 服务。
                </TableCell>
              </TableRow>
            ) : (
              (servers.data ?? []).map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="font-mono text-xs">{row.server_key}</TableCell>
                  <TableCell>{row.name}</TableCell>
                  <TableCell className="max-w-56 truncate font-mono text-xs">
                    {row.endpoint}
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary" className="font-normal">
                      {row.tools.length} 个
                    </Badge>
                    {row.last_sync_error && (
                      <p className="mt-1 max-w-52 truncate text-xs text-destructive" title={row.last_sync_error}>
                        {row.last_sync_error}
                      </p>
                    )}
                    {rowNote[row.id] && (
                      <p className="mt-1 text-xs text-muted-foreground" role="status">
                        {rowNote[row.id]}
                      </p>
                    )}
                  </TableCell>
                  <TableCell>
                    <StatusSwitch
                      active={row.enabled}
                      pending={
                        toggle.isPending && toggle.variables?.row.id === row.id
                      }
                      error={toggleError[row.id] || null}
                      onToggle={() =>
                        toggle.mutate({ row, enabled: !row.enabled })
                      }
                      ariaLabel={`启停 ${row.name}`}
                    />
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="xs"
                        disabled={busyKey !== null}
                        onClick={() => void runAction(row, "test")}
                      >
                        {busyKey === `${row.id}:test` ? (
                          <Loader2 className="animate-spin" aria-hidden />
                        ) : (
                          <Cable aria-hidden />
                        )}
                        测试
                      </Button>
                      <Button
                        variant="ghost"
                        size="xs"
                        disabled={busyKey !== null}
                        onClick={() => void runAction(row, "sync")}
                      >
                        {busyKey === `${row.id}:sync` ? (
                          <Loader2 className="animate-spin" aria-hidden />
                        ) : (
                          <RefreshCw aria-hidden />
                        )}
                        同步
                      </Button>
                      <Button variant="ghost" size="xs" onClick={() => openEdit(row)}>
                        编辑
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={`删除 ${row.name}`}
                        onClick={() => { setDeleting(row); setDeleteError(""); }}
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

      {/* 新建/编辑对话框 */}
      <Dialog
        open={form !== null}
        onOpenChange={(o) => {
          if (save.isPending) return; // 提交中不可关(013 同范式)
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
              <div className="space-y-1.5">
                <Label htmlFor="mcp-key">标识(server_key)</Label>
                <Input
                  id="mcp-key"
                  value={form.serverKey}
                  disabled={form.id !== null} // 创建后不可改(008 同范式)
                  onChange={(e) => setForm({ ...form, serverKey: e.target.value })}
                  placeholder="小写字母开头,如 fs"
                  className="font-mono"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="mcp-name">名称</Label>
                <Input
                  id="mcp-name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="mcp-endpoint">Endpoint(Streamable HTTP)</Label>
                <Input
                  id="mcp-endpoint"
                  value={form.endpoint}
                  onChange={(e) => setForm({ ...form, endpoint: e.target.value })}
                  placeholder="https://example.com/mcp"
                  className="font-mono text-xs"
                />
              </div>
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

      {/* 删除确认(受控:成功才关,009 教训) */}
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
    </div>
  );
}
