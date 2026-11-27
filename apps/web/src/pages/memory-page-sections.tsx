/* 记忆页的分区部件:团队记忆写入卡与偏好设置区(查看/编辑/单项清除/全部清空)。 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, SlidersHorizontal, X } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@kongming/ui/alert-dialog";
import { Button } from "@kongming/ui/button";
import { Input } from "@kongming/ui/input";
import { Label } from "@kongming/ui/label";
import { FormRow } from "@kongming/ui/form-row";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import { ErrorAlert } from "@kongming/ui/error-alert";
import { api } from "@kongming/api-client/api";
import { listMyTeams } from "@kongming/api-client/team-api";
import { useTenant } from "@kongming/api-client/tenant-store";
import {
  PREF_SLOTS,
  buildPrefsPatch,
  draftFromPrefs,
  isEmptyPatch,
  singleSlotDelete,
  type PrefsDraft,
  type PrefsMap,
} from "@kongming/api-client/prefs-form";

/* 团队记忆写入卡(team-api 创建端点;OWNER/MEMBER 校验在服务端) */
export function TeamMemoryWrite() {
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

/** 偏好数据按租户隔离；提交时固定租户，避免切换租户后迟到的响应污染新租户缓存。 */
function useTenantPrefs(tenantKey: string | null) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<PrefsDraft>({});
  const [error, setError] = useState<string | null>(null);
  // 清空成功后关闭确认框(失败保持打开,错误在框内可见)
  const [clearingAll, setClearingAll] = useState(false);

  const prefs = useQuery({
    queryKey: ["memory", "prefs", tenantKey],
    queryFn: () => api.get<{ prefs: PrefsMap }>("/memory/prefs"),
  });
  const server: PrefsMap = prefs.data?.prefs ?? {};

  // 草稿随服务端数据重置:切租户/读取成功/写回后回到服务端确认值
  useEffect(() => {
    setDraft(draftFromPrefs(prefs.data?.prefs ?? {}));
  }, [prefs.data]);

  // setQueryData 放在请求链内:租户在触发时被闭包固化,不会写到新租户缓存
  const withCache = (p: Promise<{ prefs: PrefsMap }>) =>
    p.then((resp) => {
      qc.setQueryData(["memory", "prefs", tenantKey], resp);
      return resp;
    });

  const save = useMutation({
    mutationFn: (patch: PrefsMap) =>
      withCache(api.put<{ prefs: PrefsMap }>("/memory/prefs", patch)),
    onSuccess: () => setError(null),
    onError: (e) => setError(e instanceof Error ? e.message : "保存失败"),
  });

  const clearSlot = useMutation({
    mutationFn: (key: string) =>
      withCache(api.put<{ prefs: PrefsMap }>("/memory/prefs", singleSlotDelete(key))),
    onSuccess: () => setError(null),
    onError: (e) => setError(e instanceof Error ? e.message : "清除失败"),
  });

  const clearAll = useMutation({
    mutationFn: () => withCache(api.delete<{ prefs: PrefsMap }>("/memory/prefs")),
    onSuccess: () => {
      setClearingAll(false);
      setError(null);
    },
    onError: (e) => setError(e instanceof Error ? e.message : "清空失败"),
  });

  return {
    prefs,
    server,
    draft,
    setDraft,
    error,
    save,
    clearSlot,
    clearAll,
    clearingAll,
    setClearingAll,
  };
}

type PrefsApi = ReturnType<typeof useTenantPrefs>;

/** 偏好槽位行(单值输入 + 清除按钮)。 */
function PrefSlotRow({
  slot,
  apiState,
}: {
  slot: (typeof PREF_SLOTS)[number];
  apiState: PrefsApi;
}) {
  const { draft, setDraft, server, clearSlot } = apiState;
  const pending = apiState.save.isPending || clearSlot.isPending || apiState.clearAll.isPending;
  return (
    <div className="flex items-end gap-2">
      <FormRow label={slot.label} htmlFor={`pref-${slot.key}`} className="flex-1">
        <Input
          id={`pref-${slot.key}`}
          value={draft[slot.key] ?? ""}
          onChange={(e) => setDraft((d) => ({ ...d, [slot.key]: e.target.value }))}
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
  );
}

/** 偏好内的错误提示(浅色红字,批量清空对话框与外层共用)。 */
function PrefsError({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <p className="text-xs text-destructive" role="alert">
      {error}
    </p>
  );
}

/* 偏好设置区体:读取失败可重试;读取中给出状态;成功后渲染槽位与保存。 */
function PrefsBody({ apiState }: { apiState: PrefsApi }) {
  const { prefs, server, draft, save, error } = apiState;
  if (prefs.isError) {
    // 读取失败显式报错,避免误读为空配置
    return (
      <div className="space-y-1">
        <ErrorAlert
          text={`偏好加载失败:${prefs.error.message}。笔记与历史偏好不受影响;重试或稍后再来。`}
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
    );
  }
  if (prefs.isPending) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
        <Loader2 className="animate-spin" aria-hidden />
        正在读取偏好…
      </div>
    );
  }
  const pending =
    apiState.save.isPending || apiState.clearSlot.isPending || apiState.clearAll.isPending;
  const patch = buildPrefsPatch(server, draft);
  return (
    <div className="space-y-3">
      {PREF_SLOTS.filter((s) => !s.list).map((slot) => (
        <PrefSlotRow key={slot.key} slot={slot} apiState={apiState} />
      ))}
      <MetricAliases apiState={apiState} />
      <PrefsError error={error} />
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
  );
}

/** 指标别名列表:逐条增删改(草稿态,随保存一同提交)。 */
function MetricAliases({ apiState }: { apiState: PrefsApi }) {
  const { draft, setDraft } = apiState;
  const pending =
    apiState.save.isPending || apiState.clearSlot.isPending || apiState.clearAll.isPending;
  const aliases = draft.metric_aliases as string[];

  const setAlias = (i: number, value: string) =>
    setDraft((d) => {
      const list = [...(d.metric_aliases as string[])];
      list[i] = value;
      return { ...d, metric_aliases: list };
    });
  const removeAlias = (i: number) =>
    setDraft((d) => ({
      ...d,
      metric_aliases: (d.metric_aliases as string[]).filter((_, j) => j !== i),
    }));
  const addAlias = () =>
    setDraft((d) => ({ ...d, metric_aliases: [...(d.metric_aliases as string[]), ""] }));

  return (
    <FormRow label="指标别名" htmlFor="pref-alias-0">
      <div className="space-y-2">
        {aliases.map((alias, i) => (
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
        <Button type="button" variant="outline" size="sm" disabled={pending} onClick={addAlias}>
          <Plus className="size-3.5" aria-hidden />
          添加别名
        </Button>
      </div>
    </FormRow>
  );
}

/** 全部清空确认:仅影响偏好,笔记/团队记忆不动;失败保持对话框打开。 */
function ClearAllPrefsDialog({
  open,
  onOpenChange,
  apiState,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  apiState: PrefsApi;
}) {
  const { clearAll, error } = apiState;
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>清空全部偏好?</AlertDialogTitle>
          <AlertDialogDescription>
            仅清空上方偏好槽位,个人笔记与团队记忆保持不变;清空后孔明的下一问不再注入旧偏好。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <PrefsError error={error} />
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
  );
}

/** 偏好设置区(本人当前租户)。 */
export function PrefsSection() {
  const { current: tenantKey } = useTenant();
  const apiState = useTenantPrefs(tenantKey);
  const { server, save, clearSlot, clearAll, clearingAll, setClearingAll } = apiState;
  const pending = save.isPending || clearSlot.isPending || clearAll.isPending;

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

      <PrefsBody apiState={apiState} />

      <ClearAllPrefsDialog open={clearingAll} onOpenChange={setClearingAll} apiState={apiState} />
    </section>
  );
}
