/* 知识库管理页(web-admin 009 delta):列表(数据源筛选/分页)+ 上传(文件选择
   +数据源/schema)+ 删除受控确认 + 扫描数据源 + 重建索引(受控确认,结果展示)。
   全部走 shadcn 组件;错误反馈 role=alert;进行中操作禁重复提交。 */

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router";

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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { PageHeader } from "@/components/page-header";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { datasourceApi } from "@/lib/datasource-api";
import { api } from "@/lib/api";
import { ragApi, type KnowledgeItemOut } from "@/lib/rag-api";
import type { components } from "@/lib/api-types.gen";
import {
  CircleHelp,
  FileText,
  Loader2 as LoaderIcon,
  Trash2 as TrashIcon,
  Upload,
} from "lucide-react";

const PAGE_SIZE = 10;

const SOURCE_LABEL: Record<string, string> = {
  INTROSPECTION: "自动扫描",
  AI_GENERATED: "AI 润色",
  MANUAL_UPLOAD: "人工上传",
  MANUAL_EDIT: "人工精修",
  FEEDBACK_REFINED: "反馈精修",
};

export default function RagPage() {
  const qc = useQueryClient();
  // ?table=<名> 定位(014 feedback-refine「编辑知识」跳转):提示条+行高亮
  const [searchParams, setSearchParams] = useSearchParams();
  const locateTable = searchParams.get("table");
  const [page, setPage] = useState(1);
  const [dsFilter, setDsFilter] = useState("");
  const [actionDs, setActionDs] = useState("");
  const [uploadSchema, setUploadSchema] = useState("");
  const [uploadDs, setUploadDs] = useState("default");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeItemOut | null>(null);
  const [reindexOpen, setReindexOpen] = useState(false);
  // 037 表说明编辑(表说明与业务称谓别名同面)
  const [editTarget, setEditTarget] = useState<KnowledgeItemOut | null>(null);
  const [editComment, setEditComment] = useState("");
  const [editAlias, setEditAlias] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const { data: options } = useQuery({
    queryKey: ["rag", "ds-options"],
    queryFn: datasourceApi.options,
  });

  // 040:可扫描 schema 集合(上传表单候选源,随上传数据源联动)
  const schemas = useQuery({
    queryKey: ["rag", "schemas", uploadDs],
    queryFn: () => ragApi.allowedSchemas(uploadDs),
  });

  // 已选 schema 不在新数据源集合(换源/集合变化)时回落到首项
  useEffect(() => {
    const list = schemas.data?.schemas;
    if (list && list.length > 0 && !list.includes(uploadSchema)) {
      setUploadSchema(list[0]);
    }
  }, [schemas.data, uploadSchema]);

  const list = useQuery({
    queryKey: ["rag", "tables", page, dsFilter],
    queryFn: () => ragApi.page(page, PAGE_SIZE, dsFilter),
  });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["rag", "tables"] });
  };

  const upload = useMutation({
    mutationFn: (file: File) => ragApi.upload(file, uploadDs, uploadSchema),
    onSuccess: (out) => {
      setNotice(`上传成功:${out.tables.join("、")}`);
      setError(null);
      invalidate();
    },
    onError: (e: Error) => {
      // 040 写边界闸 detail 映射(黑话禁入用户文案)
      const reason =
        e.message === "SCHEMA_NOT_ALLOWED" ? "schema 不在该数据源的可扫描集合" : e.message;
      setError(`上传失败:${reason}`);
      setNotice(null);
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => ragApi.remove(id),
    onSuccess: () => {
      setDeleteTarget(null);
      setNotice("已删除");
      setError(null);
      invalidate();
    },
    onError: (e: Error) => {
      setError(`删除失败:${e.message}`);
      setNotice(null);
    },
  });

  const edit = useMutation({
    mutationFn: (t: { id: string; comment: string; alias: string }) =>
      ragApi.edit(t.id, { table_comment: t.comment, alias: t.alias }),
    onSuccess: (out) => {
      setEditTarget(null);
      setNotice(`已保存:${out.table_name}${out.alias ? `(${out.alias})` : ""}`);
      setError(null);
      invalidate();
    },
    onError: (e: Error) => {
      setError(`保存失败:${e.message}`);
      setNotice(null);
    },
  });

  const openEdit = (item: KnowledgeItemOut) => {
    setEditComment(item.table_comment);
    setEditAlias(item.alias);
    setEditTarget(item);
  };

  const fetchMeta = useMutation({
    mutationFn: () => ragApi.fetchMetadata(actionDs || "default"),
    onSuccess: (out) => {
      const parts = [`发现 ${out.found} 张表,已生成 ${out.enriched} 张的说明`];
      if (out.skipped > 0) parts.push(`${out.skipped} 张人工精修未覆盖`);
      if (out.removed > 0) parts.push(`清理 ${out.removed} 条失效条目`);
      setNotice(`扫描完成:${parts.join(",")}`);
      setError(null);
      invalidate();
    },
    onError: (e: Error) => {
      setError(`扫描数据源失败:${e.message}`);
      setNotice(null);
    },
  });

  const reindex = useMutation({
    mutationFn: () => ragApi.reindex(actionDs || "default"),
    onSuccess: (out) => {
      setReindexOpen(false);
      setNotice(`重建完成:清空 ${out.deleted} 条,重建 ${out.rebuilt} 表(润色 ${out.enriched})`);
      setError(null);
      invalidate();
    },
    onError: (e: Error) => {
      setReindexOpen(false);
      setError(`重建索引失败:${e.message}`);
    },
  });

  // 数据源动作下拉缺省对齐筛选
  useEffect(() => {
    if (!actionDs && dsFilter) setActionDs(dsFilter);
  }, [actionDs, dsFilter]);

  const total = list.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // 删除末页唯一条目后分页越界自愈(007 R2 同款:空页误导)
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  return (
    <div className="space-y-4">
      {locateTable && (
        <div className="flex items-center justify-between rounded-lg border border-ring/40 bg-accent/40 px-3 py-2 text-sm">
          <span>
            已定位自反馈详情:表 <code className="font-mono">{locateTable}</code>
          </span>
          <Button
            variant="ghost"
            size="xs"
            aria-label="清除定位"
            onClick={() => setSearchParams({}, { replace: true })}
          >
            清除
          </Button>
        </div>
      )}
      <PageHeader
        title="知识库"
        actions={
          <div className="flex items-center gap-2">
            <Select value={actionDs || "default"} onValueChange={setActionDs}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(options ?? []).map((o) => (
                  <SelectItem key={o.datasource_key} value={o.datasource_key}>
                    {o.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="outline"
                  onClick={() => fetchMeta.mutate()}
                  disabled={fetchMeta.isPending}
                >
                  {fetchMeta.isPending ? "扫描中…" : "扫描数据源"}
                </Button>
              </TooltipTrigger>
              <TooltipContent className="max-w-72">
                读取该数据源的表结构,由 AI
                生成每张表的业务说明并保存到知识库(耗时较长);人工精修的条目不会被覆盖。
              </TooltipContent>
            </Tooltip>
            <Button
              variant="destructive"
              onClick={() => setReindexOpen(true)}
              disabled={reindex.isPending}
            >
              重建索引
            </Button>
          </div>
        }
      />

      {(error || notice) && (
        <p
          role="alert"
          className={error ? "text-sm text-destructive" : "text-sm text-muted-foreground"}
        >
          {error ?? notice}
        </p>
      )}

      <section className="rounded-lg border p-4 space-y-3">
        <div className="flex items-center gap-1">
          <h2 className="text-sm font-medium">表说明</h2>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="size-5 text-muted-foreground hover:text-foreground"
                aria-label="什么是表说明"
              >
                <CircleHelp className="size-3.5" aria-hidden />
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-72">
              {
                "教 AI 认识数据表——字段含义、业务别名、示例问题。可上传文档补充,或用「重建索引」自动扫描生成。"
              }
            </TooltipContent>
          </Tooltip>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label htmlFor="rag-upload-ds">数据源</Label>
            <Select value={uploadDs} onValueChange={setUploadDs}>
              <SelectTrigger id="rag-upload-ds" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(options ?? []).map((o) => (
                  <SelectItem key={o.datasource_key} value={o.datasource_key}>
                    {o.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="rag-upload-schema">schema</Label>
            {/* 040:自由输入改封闭集合选择框(候选=可扫描 schema,与扫描/上传闸同源) */}
            <Select
              value={uploadSchema}
              onValueChange={setUploadSchema}
              disabled={schemas.isPending || schemas.isError}
            >
              <SelectTrigger id="rag-upload-schema" className="w-40">
                <SelectValue
                  placeholder={
                    schemas.isError ? "候选加载失败" : schemas.isPending ? "加载中…" : "选择 schema"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {(schemas.data?.schemas ?? []).map((s) => (
                  <SelectItem key={s} value={s}>
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {schemas.isError && (
              <p className="text-xs text-destructive" role="alert">
                schema 候选加载失败,请刷新重试
              </p>
            )}
          </div>
          <div className="space-y-1">
            <Label htmlFor="rag-upload-file" className="flex items-center gap-1">
              文件(Markdown/TXT/PDF/Word)
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-5 text-muted-foreground hover:text-foreground"
                    aria-label="支持的文件格式与大小限制"
                  >
                    <CircleHelp className="size-3.5" aria-hidden />
                  </Button>
                </TooltipTrigger>
                <TooltipContent className="max-w-72">
                  支持 Markdown / TXT / PDF / Word 文档,文本类不超过 1MB,PDF 和 Word 不超过 10MB。
                </TooltipContent>
              </Tooltip>
            </Label>
            {/* 隐藏 file input=不可见管道元素(样式铁律豁免);Button 触发 */}
            <input
              ref={fileRef}
              id="rag-upload-file"
              type="file"
              accept=".md,.markdown,.txt,.pdf,.docx"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                // 读值后立即清 input:失败重选同一文件才触发 change
                // (浏览器对同路径 value 不派发 onChange——R2-S2)
                e.target.value = "";
                if (f) upload.mutate(f);
              }}
            />
            <Button
              variant="outline"
              onClick={() => fileRef.current?.click()}
              disabled={upload.isPending || schemas.isPending || !uploadSchema}
            >
              {upload.isPending ? "上传中…" : "选择文件并上传"}
            </Button>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          上传后文档会按表格自动整理拆分;同一张表重复上传,新内容会覆盖旧内容。
        </p>
      </section>

      <div className="flex items-center gap-2">
        <Select
          value={dsFilter || "all"}
          onValueChange={(v) => {
            setDsFilter(v === "all" ? "" : v);
            setPage(1);
          }}
        >
          <SelectTrigger className="w-44">
            <SelectValue placeholder="全部数据源" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部数据源</SelectItem>
            {(options ?? []).map((o) => (
              <SelectItem key={o.datasource_key} value={o.datasource_key}>
                {o.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-sm text-muted-foreground">共 {total} 条</span>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>数据源</TableHead>
            <TableHead>schema</TableHead>
            <TableHead>表名</TableHead>
            <TableHead>业务称谓</TableHead>
            <TableHead>说明</TableHead>
            <TableHead>来源</TableHead>
            <TableHead>更新时间</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(list.data?.items ?? []).map((item) => (
            <TableRow
              key={item.id}
              className={item.table_name === locateTable ? "bg-accent/50" : undefined}
            >
              <TableCell className="font-mono text-xs">{item.datasource_key}</TableCell>
              <TableCell className="font-mono text-xs">{item.schema_name}</TableCell>
              <TableCell className="font-mono text-xs">{item.table_name}</TableCell>
              <TableCell className="text-xs">
                {item.alias ? (
                  <Badge variant="outline" className="max-w-40" title={item.alias}>
                    <span className="truncate">{item.alias}</span>
                  </Badge>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </TableCell>
              <TableCell className="max-w-64 truncate" title={item.table_comment}>
                {item.table_comment}
              </TableCell>
              <TableCell>
                <Badge variant="secondary">
                  {SOURCE_LABEL[item.source_type] ?? item.source_type}
                </Badge>
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {new Date(item.updated_at).toLocaleString("zh-CN")}
              </TableCell>
              <TableCell className="text-right">
                <Button variant="ghost" size="sm" onClick={() => openEdit(item)}>
                  编辑
                </Button>
                <Button variant="ghost-destructive" size="sm" onClick={() => setDeleteTarget(item)}>
                  删除
                </Button>
              </TableCell>
            </TableRow>
          ))}
          {list.isPending && (
            <TableRow>
              <TableCell colSpan={8} className="text-center text-muted-foreground">
                加载中…
              </TableCell>
            </TableRow>
          )}
          {!list.isPending && (list.data?.items ?? []).length === 0 && (
            <TableRow>
              <TableCell colSpan={8} className="text-center text-muted-foreground">
                暂无知识条目——可在右上角对数据源执行「扫描数据源」
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>

      <div className="flex items-center justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={page <= 1}
          onClick={() => setPage((p) => p - 1)}
        >
          上一页
        </Button>
        <span className="text-sm text-muted-foreground">
          {page}/{totalPages}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={page >= totalPages}
          onClick={() => setPage((p) => p + 1)}
        >
          下一页
        </Button>
      </div>

      {/* 037 表说明编辑:表说明与业务称谓别名同面(MANUAL_EDIT,自动润色不覆盖) */}
      <Dialog
        open={editTarget !== null}
        onOpenChange={(open) => {
          if (!open) setEditTarget(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑表说明</DialogTitle>
            <DialogDescription>
              表 {editTarget?.schema_name}.{editTarget?.table_name}
              ——保存后标记为「人工精修」,自动扫描/润色不再覆盖。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="rag-edit-comment">表说明</Label>
              <Textarea
                id="rag-edit-comment"
                value={editComment}
                maxLength={500}
                rows={3}
                onChange={(e) => setEditComment(e.target.value)}
                placeholder="一句话中文表说明"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="rag-edit-alias">业务称谓别名</Label>
              <Input
                id="rag-edit-alias"
                value={editAlias}
                maxLength={64}
                onChange={(e) => setEditAlias(e.target.value)}
                placeholder="如「商品表」;留空表示无别名"
              />
              <p className="text-xs text-muted-foreground">
                摘要与洞察将以此称谓提及该表(用户用语优先);留空时回退表说明中的中文名称。
              </p>
            </div>
          </div>
          {edit.isError && (
            <p className="text-sm text-destructive" role="alert">
              {(edit.error as Error).message}
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditTarget(null)}>
              取消
            </Button>
            <Button
              disabled={edit.isPending}
              onClick={() =>
                editTarget &&
                edit.mutate({
                  id: editTarget.id,
                  comment: editComment,
                  alias: editAlias,
                })
              }
            >
              {edit.isPending ? "保存中…" : "保存"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* open 只看 target:成功关闭由 onSuccess setDeleteTarget(null) 收口;
          若并入 !remove.isSuccess,一次成功后 mutation 状态保持 true,
          后续删除对话框永远打不开(须先 mutate 才重置——而不开框就到不了 mutate) */}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除知识条目?</AlertDialogTitle>
            <AlertDialogDescription>
              将删除表 {deleteTarget?.schema_name}.{deleteTarget?.table_name} 的知识条目, 删除后 AI
              问答不再引用;此操作不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {remove.isError && (
            <p className="text-sm text-destructive" role="alert">
              {(remove.error as Error).message}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => deleteTarget && remove.mutate(deleteTarget.id)}
            >
              {remove.isPending ? "删除中…" : "删除"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={reindexOpen} onOpenChange={setReindexOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>重建索引?</AlertDialogTitle>
            <AlertDialogDescription>
              将清空数据源「{actionDs || "default"}」在该租户的全部表说明并重新扫描 (含 AI
              润色)——人工上传/精修的条目也会被清掉,回到自动生成基线。 大库耗时较长,请耐心等待。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <Button
              variant="destructive"
              disabled={reindex.isPending}
              onClick={() => reindex.mutate()}
            >
              {reindex.isPending ? "重建中…" : "确认重建"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 024 业务文档区:政策/流程类文档上传与维护(与表知识双通道隔离) */}
      <DocumentsSection datasource={actionDs || "default"} />
    </div>
  );
}

/* ---- 024 业务文档区 ---- */

// ADR-005:消费生成契约(审查循环 F1)
type DocItem = components["schemas"]["DocumentItemOut"];

function DocumentsSection({ datasource }: { datasource: string }) {
  const qc2 = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [removing, setRemoving] = useState<DocItem | null>(null); // R1-L7
  const [delErr, setDelErr] = useState<string | null>(null);
  const docs = useQuery({
    queryKey: ["rag-docs", datasource],
    queryFn: () => api.get<DocItem[]>(`/system/document/documents?datasource_key=${datasource}`),
  });
  // 审查循环 B4:裸 promise 改受控 mutation——删除在途可关框/双击重复发请求
  const delDoc = useMutation({
    mutationFn: (id: string) => api.delete(`/system/document/documents/${id}`),
    onSuccess: () => {
      setRemoving(null);
      setDelErr(null);
      void qc2.invalidateQueries({ queryKey: ["rag-docs", datasource] });
    },
    onError: (e) => setDelErr(e instanceof Error ? e.message : "删除失败"),
  });

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
            e.target.value = ""; // 同文件重选触发(009 R2-S2 教训)
          }}
        />
      </div>
      {err && (
        <p className="text-xs text-destructive" role="alert">
          {err}
        </p>
      )}
      <div className="divide-y">
        {docs.isLoading ? (
          <div className="py-4 text-center">
            <LoaderIcon className="mx-auto size-4 animate-spin text-muted-foreground" aria-hidden />
          </div>
        ) : (docs.data?.length ?? 0) === 0 ? (
          <p className="py-4 text-center text-xs text-muted-foreground">
            暂无文档——上传后,AI 遇到政策、流程类问题会直接按文档内容回答(标注出处)
          </p>
        ) : (
          docs.data?.map((d: DocItem) => (
            <div key={d.id} className="flex items-center gap-2 py-2 text-sm">
              <FileText className="size-4 text-muted-foreground" aria-hidden />
              <span className="flex-1 truncate">{d.doc_name}</span>
              <span className="text-xs text-muted-foreground">{d.block_count} 块</span>
              <Button
                size="icon"
                variant="ghost"
                aria-label="删除文档"
                onClick={() => {
                  setRemoving(d);
                  setDelErr(null);
                }}
              >
                <TrashIcon className="size-3.5" aria-hidden />
              </Button>
            </div>
          ))
        )}
      </div>

      {/* R1-L7:删除受控确认(与同页表知识同先例);失败对话框内可见 */}
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
