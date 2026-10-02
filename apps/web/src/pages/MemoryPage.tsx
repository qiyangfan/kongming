/* 我的记忆页(022,agent-memory spec「记忆自管理」):本人当前租户记忆的
   列表/编辑(唤醒)/删除/导出;隐式记忆(IMPLicIT)可确认(编辑即视为认可)。
   087:增偏好区(查看/编辑/单项清除/全部清空),与笔记列表同页分区。 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Download, Loader2, Plus, SlidersHorizontal, X } from "lucide-react";

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
import { FormRow } from "@/components/form-row";
import { StatusBadge, type StatusMeta } from "@/components/status-badge";
import { formatDate } from "@/lib/datetime";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DataView } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import { api } from "@/lib/api";
import { listMyTeams } from "@/lib/team-api";
import { useTenant } from "@/lib/tenant-store";
import {
  PREF_SLOTS,
  buildPrefsPatch,
  draftFromPrefs,
  isEmptyPatch,
  singleSlotDelete,
  type PrefsDraft,
  type PrefsMap,
} from "@/lib/prefs-form";
import type { components } from "@/lib/api-types.gen";

// 036⑥:消费生成类型(ADR-005,禁手写;team_id 回传已在生成类型)
type MemoryItem = components["schemas"]["MemoryItemOut"];

// 值 → 徽章统一走 StatusBadge(宪法 Article 6)
const SOURCE_META: StatusMeta = {
  EXPLICIT: { label: "显式", variant: "default" },
  IMPLICIT: { label: "隐式", variant: "secondary" },
};
const DORMANT_META: StatusMeta = {
  ACTIVE: { label: "活跃", variant: "secondary" },
  DORMANT: { label: "休眠", variant: "outline" },
};

// 分类中文(后端现为 free-form,内置仅 general;未收录值回退原值)
const CATEGORY_LABELS: Record<string, string> = {
  general: "通用",
};

function categoryLabel(c: string): string {
  return CATEGORY_LABELS[c] ?? c;
}

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
    id ? ((myTeams.data ?? []).find((t) => t.id === id)?.name ?? "团队") : null;

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
      a.download = `我的记忆-${formatDate(new Date())}.json`;
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
            孔明在对话中记住的偏好与事实;「隐式记忆」为系统自动沉淀,可编辑确认或删除。
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={doExport}>
          <Download className="size-3.5" aria-hidden />
          导出
        </Button>
      </div>

      {/* 032:团队记忆写入(个人记忆走对话「请记住」同路径) */}
      <TeamMemoryWrite />

      {/* 087:偏好自管理(本人当前租户;查看/编辑/单项清除/全部清空) */}
      <PrefsSection />

      {list.isError && <ErrorAlert />}
      {/* 外框仅在宽屏保留(md- 卡片形态裸露,与 DataView 样板口径一致) */}
      <div className="md:rounded-lg md:border md:border-border">
        <DataView
          rows={list.data?.items ?? []}
          rowKey={(m) => m.id}
          isLoading={list.isPending}
          isEmpty={!list.isPending && list.data?.items.length === 0}
          emptyText="还没有记忆——在对话里说「请记住:…」即可沉淀"
          columns={[
            {
              key: "content",
              header: "内容",
              cardPriority: 1,
              render: (m) => (
                <span className="block max-w-md truncate text-sm" title={m.content}>
                  {m.content}
                </span>
              ),
            },
            {
              key: "category",
              header: "分类",
              cellClassName: "text-xs",
              render: (m) => categoryLabel(m.category),
            },
            {
              key: "source",
              header: "来源",
              cardPriority: 2,
              // source 契约为宽 string:未知值回落 secondary,与原 else 分支等价
              render: (m) => (
                <StatusBadge meta={SOURCE_META} value={m.source} fallbackVariant="secondary" />
              ),
            },
            {
              key: "team_id",
              header: "归属",
              cardPriority: 4,
              render: (m) =>
                m.team_id ? (
                  <Badge
                    variant="outline"
                    className="max-w-32"
                    title={teamName(m.team_id) ?? undefined}
                  >
                    <span className="truncate">{teamName(m.team_id)}</span>
                  </Badge>
                ) : (
                  <span className="text-xs text-muted-foreground">个人</span>
                ),
            },
            {
              key: "confidence",
              header: "置信度",
              cellClassName: "text-xs",
              render: (m) => `${Math.round(m.confidence * 100)}%`,
            },
            {
              key: "dormant",
              header: "状态",
              cardPriority: 3,
              render: (m) => (
                <StatusBadge meta={DORMANT_META} value={m.dormant ? "DORMANT" : "ACTIVE"} />
              ),
            },
          ]}
          actions={[
            { label: "编辑", onSelect: openEdit },
            { label: "删除", destructive: true, onSelect: setRemoving },
          ]}
        />
      </div>

      {/* 编辑对话框 */}
      <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑记忆</DialogTitle>
            <DialogDescription>保存即视为确认,这条隐式记忆将转为「已认可」。</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <FormRow label="内容" htmlFor="mem-content">
              <Input
                id="mem-content"
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                maxLength={500}
              />
            </FormRow>
            <FormRow label="分类" htmlFor="mem-category">
              <Input
                id="mem-category"
                value={editCategory}
                onChange={(e) => setEditCategory(e.target.value)}
                maxLength={32}
              />
            </FormRow>
            {error && (
              <p className="text-xs text-destructive" role="alert">
                {error}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditing(null)}>
              取消
            </Button>
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
              「{removing?.content}」删除后不可恢复,孔明后续回答不再参考这条记忆。
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
      className="flex flex-col gap-2 rounded-lg border border-border p-3 md:flex-row md:items-end"
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
          placeholder="偏好/规则/约定…"
          maxLength={500}
        />
      </div>
      {/* 窄屏全宽折行,md+ 恢复固定 44 宽 */}
      <div className="w-full space-y-1 md:w-44">
        <Label>归属</Label>
        <Select
          value={teamId || "_personal"}
          onValueChange={(v) => setTeamId(v === "_personal" ? "" : v)}
        >
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
        <p
          role="alert"
          className={`text-sm ${error ? "text-destructive" : "text-muted-foreground"}`}
        >
          {error || note}
        </p>
      )}
    </form>
  );
}

/* 087:偏好自管理区(本人当前租户)。查询与表单按租户分区(query key 含
   tenant,切租户即新语境);成功操作以服务端响应替换本页偏好(setQueryData
   在 mutationFn 内执行——闭包在触发时固化租户,在途请求晚归时切了租户
   也不会把旧语境响应写进新语境缓存),并重播表单草稿。 */
function PrefsSection() {
  const qc = useQueryClient();
  const { current: tenantKey } = useTenant();
  const queryKey = ["memory", "prefs", tenantKey];
  const [draft, setDraft] = useState<PrefsDraft>({});
  const [error, setError] = useState<string | null>(null);
  const [clearingAll, setClearingAll] = useState(false);

  const prefs = useQuery({
    queryKey,
    queryFn: () => api.get<{ prefs: PrefsMap }>("/memory/prefs"),
  });
  const server: PrefsMap = prefs.data?.prefs ?? {};

  // 草稿与数据同源重播:切租户/读取成功/写操作响应替换后都回到服务端确认值
  useEffect(() => {
    setDraft(draftFromPrefs(prefs.data?.prefs ?? {}));
  }, [prefs.data]);

  const save = useMutation({
    // setQueryData 放 mutationFn 内:租户在触发时被闭包固化(见组件头注释)
    mutationFn: (patch: PrefsMap) =>
      api.put<{ prefs: PrefsMap }>("/memory/prefs", patch).then((resp) => {
        qc.setQueryData(["memory", "prefs", tenantKey], resp);
        return resp;
      }),
    onSuccess: () => setError(null),
    onError: (e) => setError(e instanceof Error ? e.message : "保存失败"),
  });

  const clearSlot = useMutation({
    mutationFn: (key: string) =>
      api.put<{ prefs: PrefsMap }>("/memory/prefs", singleSlotDelete(key)).then((resp) => {
        qc.setQueryData(["memory", "prefs", tenantKey], resp);
        return resp;
      }),
    onSuccess: () => setError(null),
    onError: (e) => setError(e instanceof Error ? e.message : "清除失败"),
  });

  const clearAll = useMutation({
    mutationFn: () =>
      api.delete<{ prefs: PrefsMap }>("/memory/prefs").then((resp) => {
        qc.setQueryData(["memory", "prefs", tenantKey], resp);
        return resp;
      }),
    onSuccess: () => {
      setClearingAll(false);
      setError(null);
    },
    // 失败保持对话框打开:用户可重试或取消;错误文案同时在对话框内可见
    onError: (e) => setError(e instanceof Error ? e.message : "清空失败"),
  });

  function setSlot(key: string, value: string) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  function setAlias(i: number, value: string) {
    setDraft((d) => {
      const list = [...(d.metric_aliases as string[])];
      list[i] = value;
      return { ...d, metric_aliases: list };
    });
  }

  function removeAlias(i: number) {
    setDraft((d) => ({
      ...d,
      metric_aliases: (d.metric_aliases as string[]).filter((_, j) => j !== i),
    }));
  }

  function addAlias() {
    setDraft((d) => ({ ...d, metric_aliases: [...(d.metric_aliases as string[]), ""] }));
  }

  const pending = save.isPending || clearSlot.isPending || clearAll.isPending;
  const patch = buildPrefsPatch(server, draft);

  return (
    <section className="md:rounded-lg md:border md:border-border md:p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <SlidersHorizontal className="size-4 text-primary" aria-hidden />
            偏好设置
          </h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            影响孔明回答的默认值与表达习惯(可忽略,不约束数据范围);留空保存即删除该偏好。
          </p>
        </div>
        {/* 已存偏好才可清空;正在读取时不给危险操作 */}
        <Button
          type="button"
          variant="ghost-destructive"
          size="sm"
          disabled={pending || Object.keys(server).length === 0}
          onClick={() => setClearingAll(true)}
        >
          全部清空
        </Button>
      </div>

      {prefs.isError ? (
        // 读取失败显式可见,不用空配置掩盖(087 D3)
        <div className="space-y-1">
          <ErrorAlert
            text={`偏好加载失败:${(prefs.error as Error).message}。笔记与历史偏好不受影响;重试或稍后再来。`}
          />
          <Button
            type="button"
            variant="link"
            size="xs"
            className="px-0"
            onClick={() => void prefs.refetch()}
          >
            重试
          </Button>
        </div>
      ) : prefs.isPending ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
          <Loader2 className="animate-spin" aria-hidden />
          正在读取偏好…
        </div>
      ) : (
        <div className="space-y-3">
          {PREF_SLOTS.filter((s) => !s.list).map((slot) => (
            <div key={slot.key} className="flex items-end gap-2">
              <FormRow label={slot.label} htmlFor={`pref-${slot.key}`} className="flex-1">
                <Input
                  id={`pref-${slot.key}`}
                  value={(draft[slot.key] as string) ?? ""}
                  onChange={(e) => setSlot(slot.key, e.target.value)}
                  disabled={pending}
                  placeholder="未设置"
                />
              </FormRow>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`清除${slot.label}`}
                title={`清除${slot.label}`}
                disabled={pending || !(slot.key in server)}
                onClick={() => clearSlot.mutate(slot.key)}
              >
                <X className="size-4" aria-hidden />
              </Button>
            </div>
          ))}

          {/* metric_aliases 可编辑列表 */}
          <FormRow label="指标别名" htmlFor="pref-alias-0">
            <div className="space-y-2">
              {(draft.metric_aliases as string[]).map((alias, i) => (
                <div key={i} className="flex items-center gap-2">
                  <Input
                    id={i === 0 ? "pref-alias-0" : `pref-alias-${i}`}
                    value={alias}
                    onChange={(e) => setAlias(i, e.target.value)}
                    disabled={pending}
                    placeholder="如 GMV=成交金额"
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label={`删除别名 ${i + 1}`}
                    title="删除该别名"
                    disabled={pending}
                    onClick={() => removeAlias(i)}
                  >
                    <X className="size-4" aria-hidden />
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={pending}
                onClick={addAlias}
              >
                <Plus className="size-3.5" aria-hidden />
                添加别名
              </Button>
            </div>
          </FormRow>

          {error && (
            <p className="text-xs text-destructive" role="alert">
              {error}
            </p>
          )}

          <div className="flex justify-end">
            <Button
              size="sm"
              disabled={pending || isEmptyPatch(patch)}
              onClick={() => save.mutate(patch)}
            >
              {save.isPending && <Loader2 className="animate-spin" aria-hidden />}
              保存偏好
            </Button>
          </div>
        </div>
      )}

      {/* 全部清空确认:仅影响偏好,笔记/团队记忆不动 */}
      <AlertDialog open={clearingAll} onOpenChange={setClearingAll}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>清空全部偏好?</AlertDialogTitle>
            <AlertDialogDescription>
              仅清空上方偏好槽位,个人笔记与团队记忆保持不变;清空后孔明的下一问不再注入旧偏好。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {error && (
            <p className="text-xs text-destructive" role="alert">
              {error}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={clearAll.isPending}>再想想</AlertDialogCancel>
            <AlertDialogAction
              disabled={clearAll.isPending}
              onClick={(e) => {
                e.preventDefault(); // 等 mutation 结果再收口(失败保持打开)
                clearAll.mutate();
              }}
            >
              {clearAll.isPending && <Loader2 className="animate-spin" aria-hidden />}
              清空偏好
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
