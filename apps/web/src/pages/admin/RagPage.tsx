/* 知识库管理页(web-admin 009 delta):列表(数据源筛选/分页)+ 上传(文件选择
   +数据源/schema)+ 删除受控确认 + 抓取元数据 + 重建索引(受控确认,结果展示)。
   全部走 shadcn 组件;错误反馈 role=alert;进行中操作禁重复提交。 */

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { datasourceApi } from "@/lib/datasource-api";
import { ragApi, type KnowledgeItemOut } from "@/lib/rag-api";

const PAGE_SIZE = 10;

const SOURCE_LABEL: Record<string, string> = {
  INTROSPECTION: "内省",
  AI_GENERATED: "AI 润色",
  MANUAL_UPLOAD: "人工上传",
  MANUAL_EDIT: "人工精修",
  FEEDBACK_REFINED: "反馈精修",
};

export default function RagPage() {
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [dsFilter, setDsFilter] = useState("");
  const [actionDs, setActionDs] = useState("");
  const [uploadSchema, setUploadSchema] = useState("public");
  const [uploadDs, setUploadDs] = useState("default");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeItemOut | null>(null);
  const [reindexOpen, setReindexOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const { data: options } = useQuery({
    queryKey: ["rag", "ds-options"],
    queryFn: datasourceApi.options,
  });

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
      setError(`上传失败:${e.message}`);
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

  const fetchMeta = useMutation({
    mutationFn: () => ragApi.fetchMetadata(actionDs || "default"),
    onSuccess: (out) => {
      setNotice(`抓取完成:发现 ${out.found} 表,润色 ${out.enriched} 表,跳过 ${out.skipped} 表`);
      setError(null);
      invalidate();
    },
    onError: (e: Error) => {
      setError(`抓取元数据失败:${e.message}`);
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
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">知识库</h1>
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
          <Button
            variant="outline"
            onClick={() => fetchMeta.mutate()}
            disabled={fetchMeta.isPending}
          >
            {fetchMeta.isPending ? "抓取中…" : "抓取元数据"}
          </Button>
          <Button
            variant="destructive"
            onClick={() => setReindexOpen(true)}
            disabled={reindex.isPending}
          >
            重建索引
          </Button>
        </div>
      </div>

      {(error || notice) && (
        <p
          role="alert"
          className={
            error
              ? "text-sm text-destructive"
              : "text-sm text-muted-foreground"
          }
        >
          {error ?? notice}
        </p>
      )}

      <section className="rounded-lg border p-4 space-y-3">
        <h2 className="text-sm font-medium">上传表说明文档</h2>
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
            <Input
              id="rag-upload-schema"
              value={uploadSchema}
              onChange={(e) => setUploadSchema(e.target.value)}
              className="w-40"
              placeholder="tenant_default"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="rag-upload-file">文件(md/txt/pdf/docx,≤1MB/10MB)</Label>
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
              disabled={upload.isPending}
            >
              {upload.isPending ? "上传中…" : "选择文件并上传"}
            </Button>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          文档按 `## 表名` 二级标题分块;同表重复上传覆盖更新。PDF/Word 解析为纯文本后走同链路。
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
            <TableHead>说明</TableHead>
            <TableHead>来源</TableHead>
            <TableHead>更新时间</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(list.data?.items ?? []).map((item) => (
            <TableRow key={item.id}>
              <TableCell className="font-mono text-xs">{item.datasource_key}</TableCell>
              <TableCell className="font-mono text-xs">{item.schema_name}</TableCell>
              <TableCell className="font-mono text-xs">{item.table_name}</TableCell>
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
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-destructive hover:text-destructive"
                  onClick={() => setDeleteTarget(item)}
                >
                  删除
                </Button>
              </TableCell>
            </TableRow>
          ))}
          {list.isPending && (
            <TableRow>
              <TableCell colSpan={7} className="text-center text-muted-foreground">
                加载中…
              </TableCell>
            </TableRow>
          )}
          {!list.isPending && (list.data?.items ?? []).length === 0 && (
            <TableRow>
              <TableCell colSpan={7} className="text-center text-muted-foreground">
                暂无知识条目——可先对数据源「抓取元数据」
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
              将删除表 {deleteTarget?.schema_name}.{deleteTarget?.table_name} 的知识条目
              (硬删,后续召回不再命中)。此操作不可撤销。
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
              将清空数据源「{actionDs || "default"}」在该租户的全部表说明并重新抓取
              (含 LLM 润色)——人工上传/精修的条目也会被清掉,回到自动生成基线。
              大库耗时较长,请耐心等待。
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
    </div>
  );
}
