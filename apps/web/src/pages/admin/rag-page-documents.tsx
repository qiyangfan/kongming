/* 知识文档区:政策/流程类文档的上传、列表与删除(与表知识双通道隔离)。 */

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CircleHelp,
  FileText,
  Loader2 as LoaderIcon,
  Trash2 as TrashIcon,
  Upload,
} from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types.gen";

// ADR-005:消费生成契约
type DocItem = components["schemas"]["DocumentItemOut"];

// 文档列表:加载中占位 / 空态 / 逐条展示名称与块数,行尾删除入确认框
function DocList({
  docs,
  isLoading,
  onRemove,
}: {
  docs: DocItem[] | undefined;
  isLoading: boolean;
  onRemove: (d: DocItem) => void;
}) {
  if (isLoading) {
    return (
      <div className="divide-y">
        <div className="py-4 text-center">
          <LoaderIcon className="mx-auto size-4 animate-spin text-muted-foreground" aria-hidden />
        </div>
      </div>
    );
  }
  if ((docs?.length ?? 0) === 0) {
    return (
      <EmptyState>
        暂无文档——上传后,AI 遇到政策、流程类问题会直接按文档内容回答(标注出处)
      </EmptyState>
    );
  }
  return (
    <div className="divide-y">
      {docs?.map((d: DocItem) => (
        <div key={d.id} className="flex items-center gap-2 py-2 text-sm">
          <FileText className="size-4 text-muted-foreground" aria-hidden />
          <span className="flex-1 truncate">{d.doc_name}</span>
          <span className="text-xs text-muted-foreground">{d.block_count} 块</span>
          <Button size="icon" variant="ghost" aria-label="删除文档" onClick={() => onRemove(d)}>
            <TrashIcon className="size-3.5" aria-hidden />
          </Button>
        </div>
      ))}
    </div>
  );
}

export function DocumentsSection({ datasource }: { datasource: string }) {
  const qc2 = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [removing, setRemoving] = useState<DocItem | null>(null);
  const [delErr, setDelErr] = useState<string | null>(null);
  const docs = useQuery({
    queryKey: ["rag-docs", datasource],
    queryFn: () => api.get<DocItem[]>(`/system/document/documents?datasource_key=${datasource}`),
  });
  // 受控 mutation 驱动删除:在途时禁用取消/确认,防关框后重复发请求或双击重复提交
  const delDoc = useMutation({
    mutationFn: (id: string) => api.delete(`/system/document/documents/${id}`),
    onSuccess: () => {
      setRemoving(null);
      setDelErr(null);
      void qc2.invalidateQueries({ queryKey: ["rag-docs", datasource] });
    },
    onError: (e) => setDelErr(e instanceof Error ? e.message : "删除失败"),
  });

  const openRemove = (d: DocItem) => {
    setRemoving(d);
    setDelErr(null);
  };

  async function doUpload(file: File) {
    setBusy(true);
    setErr(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("datasource_key", datasource);
      await api.postForm("/system/document/upload", form);
      void qc2.invalidateQueries({ queryKey: ["rag-docs", datasource] });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "上传失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-lg border p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1">
          <h2 className="text-sm font-medium">知识文档</h2>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="size-5 text-muted-foreground hover:text-foreground"
                aria-label="什么是知识文档"
              >
                <CircleHelp className="size-3.5" aria-hidden />
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-72">
              存放政策、流程、规范等资料,支持 Markdown / TXT / PDF / Word。上传后用户问到相关内容,AI
              直接按文档回答并标注出处,不查数据库。
            </TooltipContent>
          </Tooltip>
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={() => fileRef.current?.click()}
        >
          {busy ? (
            <LoaderIcon className="animate-spin" aria-hidden />
          ) : (
            <Upload className="size-3.5" aria-hidden />
          )}
          上传文档
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".md,.txt,.pdf,.docx"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void doUpload(f);
            e.target.value = ""; // 同文件重选触发
          }}
        />
      </div>
      {err && (
        <p className="text-xs text-destructive" role="alert">
          {err}
        </p>
      )}
      <DocList docs={docs.data} isLoading={docs.isLoading} onRemove={openRemove} />

      {/* 删除受控确认(与同页表知识同先例);失败对话框内可见 */}
      <AlertDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open && !delDoc.isPending) setRemoving(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除文档「{removing?.doc_name}」?</AlertDialogTitle>
            <AlertDialogDescription>
              删除后其全部知识块移除,知识问答不再引用该文档。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {delErr && (
            <p className="text-xs text-destructive" role="alert">
              {delErr}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={delDoc.isPending}>再想想</AlertDialogCancel>
            <AlertDialogAction
              disabled={delDoc.isPending}
              onClick={() => removing && delDoc.mutate(removing.id)}
            >
              {delDoc.isPending ? "删除中…" : "删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
