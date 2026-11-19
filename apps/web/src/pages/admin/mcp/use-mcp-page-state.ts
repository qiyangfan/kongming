/* MCP 页的查询、变更与行操作逻辑;页面只负责组装区块。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

import {
  actionNote,
  createPayload,
  formFromRow,
  updatePayload,
  type FormState,
} from "./mcp-form-model";

export function useMcpPageState() {
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
        await createMcpServer(createPayload(form));
        return;
      }
      await updateMcpServer(form.id, updatePayload(form));
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
        note(row.id, actionNote("test", await testMcpServer(row.id)));
      } else {
        note(row.id, actionNote("sync", await syncMcpServer(row.id)));
        invalidate();
      }
    } catch (e) {
      note(row.id, e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusyKey(null);
    }
  }

  function openEdit(row: McpServerOut): void {
    setForm(formFromRow(row));
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

  return {
    form,
    setForm,
    formError,
    setFormError,
    deleting,
    setDeleting,
    deleteError,
    setDeleteError,
    rowNote,
    busyKey,
    primitives,
    setPrimitives,
    previewing,
    setPreviewing,
    previewArgs,
    setPreviewArgs,
    previewData,
    previewError,
    previewLoading,
    servers,
    save,
    toggle,
    toggleError,
    remove,
    runAction,
    openEdit,
    openPreview,
    runPreview,
  };
}
