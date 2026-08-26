/* 我的记忆页(022,agent-memory spec「记忆自管理」):本人当前租户记忆的
   列表/编辑(唤醒)/删除/导出;隐式记忆(IMPLicIT)可确认(编辑即视为认可)。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Download, Loader2, Pencil, Trash2 } from "lucide-react";

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
import { QueryErrorAlert } from "@/components/query-error";
import { api } from "@/lib/api";
import { listMyTeams } from "@/lib/team-api";
import type { components } from "@/lib/api-types.gen";

// 036⑥:消费生成类型(ADR-005,禁手写;team_id 回传已在生成类型)
type MemoryItem = components["schemas"]["MemoryItemOut"];

const SOURCE_LABELS: Record<string, string> = {
  EXPLICIT: "显式",
  IMPLICIT: "隐式",
};

export default function MemoryPage() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<MemoryItem | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editCategory, setEditCategory] = useState("general");
  const [removing, setRemoving] = useState<MemoryItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 036⑥:归属徽标(team_id→团队名映射;032 已铺清单 query)
  const myTeams = useQuery({ queryKey: ["admin", "teams"], queryFn: listMyTeams });
  const teamName = (id: string | null | undefined) =>
    id ? (myTeams.data ?? []).find((t) => t.id === id)?.name ?? "团队" : null;

  const list = useQuery({
    queryKey: ["memory"],
    queryFn: () => api.get<{ items: MemoryItem[] }>("/memory"),
  });

  const update = useMutation({
    mutationFn: (input: { id: string; content: string; category: string }) =>
      api.patch(`/memory/${input.id}`, {
        content: input.content,
        category: input.category,
      }),
    onSuccess: () => {
      setEditing(null);
      setError(null);
      void qc.invalidateQueries({ queryKey: ["memory"] });
    },
    onError: (err) => setError(err instanceof Error ? err.message : "保存失败"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/memory/${id}`),
    onSuccess: () => {
      setRemoving(null);
      setError(null);
      void qc.invalidateQueries({ queryKey: ["memory"] });
    },
    onError: (err) => setError(err instanceof Error ? err.message : "删除失败"),
  });

  function openEdit(m: MemoryItem) {
    setEditing(m);
    setEditContent(m.content);
    setEditCategory(m.category);
    setError(null);
  }

  async function doExport() {
    // 审查循环 B5:失败零提示+unhandled rejection
    try {
      const data = await api.get<object[]>("/memory/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `我的记忆-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a); // Safari 纪律(010):锚点入 DOM+延迟 revoke
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导出失败");
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-4 px-6 py-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            <Brain className="size-5 text-primary" aria-hidden />
            我的记忆
          </h2>
          <p className="text-sm text-muted-foreground">
            孔明跨会话记住的偏好与事实;隐式记忆(系统自动沉淀)可编辑确认或删除。
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={doExport}>
          <Download className="size-3.5" aria-hidden />
          导出
        </Button>
      </div>

      {/* 032:团队记忆写入(个人记忆走对话「请记住」同路径) */}
      <TeamMemoryWrite />

      <div className="rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>内容</TableHead>
              <TableHead>分类</TableHead>
              <TableHead>来源</TableHead>
              <TableHead>归属</TableHead>
              <TableHead>置信度</TableHead>
              <TableHead>状态</TableHead>
              <TableHead className="w-24">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.isError ? (
              <TableRow>
                <TableCell colSpan={7}>
                  <QueryErrorAlert />
                </TableCell>
              </TableRow>
            ) : list.isLoading ? (
              <TableRow>
                <TableCell colSpan={7} className="py-8 text-center">
                  <Loader2 className="mx-auto size-4 animate-spin text-muted-foreground" aria-hidden />
                </TableCell>
              </TableRow>
            ) : (list.data?.items.length ?? 0) === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="py-8 text-center text-sm text-muted-foreground">
                  还没有记忆——在对话里说「请记住:…」即可沉淀
                </TableCell>
              </TableRow>
            ) : (
              list.data?.items.map((m) => (
                <TableRow key={m.id}>
                  <TableCell className="max-w-md text-sm">{m.content}</TableCell>
                  <TableCell className="text-xs">{m.category}</TableCell>
                  <TableCell>
                    <Badge variant={m.source === "EXPLICIT" ? "default" : "secondary"}>
                      {SOURCE_LABELS[m.source] ?? m.source}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {m.team_id ? (
                      <Badge variant="outline" className="font-normal">
                        {teamName(m.team_id)}
                      </Badge>
                    ) : (
                      <span className="text-xs text-muted-foreground">个人</span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs">{m.confidence.toFixed(2)}</TableCell>
                  <TableCell>
                    {m.dormant ? (
                      <Badge variant="outline">休眠</Badge>
                    ) : (
                      <Badge variant="secondary">活跃</Badge>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button size="icon" variant="ghost" onClick={() => openEdit(m)} aria-label="编辑">
                        <Pencil className="size-3.5" aria-hidden />
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        onClick={() => setRemoving(m)}
                        aria-label="删除"
                      >
                        <Trash2 className="size-3.5" aria-hidden />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* 编辑对话框 */}
      <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑记忆</DialogTitle>
            <DialogDescription>保存即视为确认(隐式记忆转为已认可),并重新计算召回向量。</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="mem-content">内容</Label>
              <Input
                id="mem-content"
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                maxLength={500}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mem-category">分类</Label>
              <Input
                id="mem-category"
                value={editCategory}
                onChange={(e) => setEditCategory(e.target.value)}
                maxLength={32}
              />
            </div>
            {error && (
              <p className="text-xs text-destructive" role="alert">{error}</p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditing(null)}>取消</Button>
            <Button
              disabled={!editContent.trim() || update.isPending}
              onClick={() =>
                editing &&
                update.mutate({
                  id: editing.id,
                  content: editContent.trim(),
                  category: editCategory.trim() || "general",
                })
              }
            >
              {update.isPending && <Loader2 className="animate-spin" aria-hidden />}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <AlertDialog open={removing !== null} onOpenChange={(open) => !open && setRemoving(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除这条记忆?</AlertDialogTitle>
            <AlertDialogDescription>
              「{removing?.content}」删除后不可恢复,后续问答不再召回。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>再想想</AlertDialogCancel>
            <AlertDialogAction
              disabled={remove.isPending}
              onClick={() => removing && remove.mutate(removing.id)}
            >
              {remove.isPending && <Loader2 className="animate-spin" aria-hidden />}
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/* 032:团队记忆写入卡(team-api 创建端点;OWNER/MEMBER 校验在服务端) */
function TeamMemoryWrite() {
  const [content, setContent] = useState("");
  const [teamId, setTeamId] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const myTeams = useQuery({ queryKey: ["admin", "teams"], queryFn: listMyTeams });
  const qc = useQueryClient();

  const write = useMutation({
    mutationFn: () =>
      api.post("/memory", {
        content: content.trim(),
        ...(teamId ? { team_id: teamId } : {}),
      }),
    onSuccess: () => {
      setContent("");
      setNote(teamId ? "已写入团队记忆。" : "已写入个人记忆。");
      setError("");
      void qc.invalidateQueries({ queryKey: ["memory"] });
    },
    onError: (e) => {
      setNote("");
      setError(e instanceof Error ? e.message : "写入失败");
    },
  });

  return (
    <form
      className="flex items-end gap-2 rounded-lg border border-border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (content.trim()) write.mutate();
      }}
    >
      <div className="flex-1 space-y-1">
        <Label htmlFor="tm-content">写入记忆</Label>
        <Input
          id="tm-content"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="偏好/口径/约定…"
          maxLength={500}
        />
      </div>
      <div className="w-44 space-y-1">
        <Label>归属</Label>
        <Select value={teamId || "_personal"} onValueChange={(v) => setTeamId(v === "_personal" ? "" : v)}>
          <SelectTrigger aria-label="归属">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="_personal">个人</SelectItem>
            {(myTeams.data ?? []).map((t) => (
              <SelectItem key={t.id} value={t.id}>
                团队:{t.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <Button type="submit" disabled={write.isPending || !content.trim()}>
        {write.isPending && <Loader2 className="animate-spin" aria-hidden />}
        写入
      </Button>
      {(error || note) && (
        <p role="alert" className={`text-sm ${error ? "text-destructive" : "text-muted-foreground"}`}>
          {error || note}
        </p>
      )}
    </form>
  );
}
