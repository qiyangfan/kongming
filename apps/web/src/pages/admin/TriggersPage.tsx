/* 触发器管理页(031 triggers-webhooks spec):
   列表(类型/目标/下次触发/启停)+ 新建/编辑对话框(cron 校验经服务端
   400 行内反馈;webhook 创建后一次性展示 URL 与密钥)+ 删除受控确认 +
   执行历史分页。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, Trash2 } from "lucide-react";

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
  createTrigger,
  deleteTrigger,
  listTriggers,
  pageTriggerRuns,
  setTriggerEnabled,
  updateTrigger,
  type TriggerItem,
} from "@/lib/trigger-api";

const PAGE_SIZE = 10;

type FormState = {
  id: string | null;
  tgKey: string;
  name: string;
  type: "CRON" | "WEBHOOK";
  targetType: "workflow" | "bot";
  targetKey: string;
  cronExpr: string;
  minInterval: string;
  adapter: "custom" | "github";
  notifyUrl: string;
  secret: string;
};

const EMPTY_FORM: FormState = {
  id: null,
  tgKey: "",
  name: "",
  type: "CRON",
  targetType: "workflow",
  targetKey: "",
  cronExpr: "0 9 * * *",
  minInterval: "60",
  adapter: "custom",
  notifyUrl: "",
  secret: "",
};

export default function TriggersPage() {
  const qc = useQueryClient();
  const [form, setForm] = useState<FormState | null>(null);
  const [formError, setFormError] = useState("");
  const [created, setCreated] = useState<{ webhook_path: string | null; secret?: string } | null>(null);
  const [deleting, setDeleting] = useState<TriggerItem | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const [historyOf, setHistoryOf] = useState<TriggerItem | null>(null);
  const [historyPage, setHistoryPage] = useState(1);
  const [toggleError, setToggleError] = useState<Record<string, string>>({});

  const triggers = useQuery({ queryKey: ["admin", "triggers"], queryFn: listTriggers });
  const runs = useQuery({
    queryKey: ["admin", "trigger-runs", historyOf?.id, historyPage],
    queryFn: () => pageTriggerRuns(historyOf!.id, historyPage, PAGE_SIZE),
    enabled: historyOf !== null,
  });

  const invalidate = () => void qc.invalidateQueries({ queryKey: ["admin", "triggers"] });

  const save = useMutation({
    mutationFn: async () => {
      if (!form) return null;
      const payload: Record<string, unknown> = {
        name: form.name.trim(),
        target_type: form.targetType,
        target_key: form.targetKey.trim(),
        cron_expr: form.type === "CRON" ? form.cronExpr.trim() : null,
        // R1-T9:|| 60 会吞掉合法的 0(ge=0 后端允许);空串才回退
        min_interval_sec:
          form.minInterval.trim() === "" ? 60 : Number(form.minInterval),
        adapter: form.adapter,
        notify_url: form.notifyUrl.trim(),
      };
      if (form.id === null) {
        return await createTrigger({
          ...payload,
          tg_key: form.tgKey.trim(),
          type: form.type,
          secret: form.secret,
        });
      }
      await updateTrigger(form.id, payload);
      return null;
    },
    onSuccess: (out) => {
      const wasCreate = form?.id === null;
      setForm(null);
      setFormError("");
      if (wasCreate && out && (out.webhook_path || out.secret)) {
        setCreated(out); // 一次性展示(025 api-key 同哲学)
      }
      invalidate();
    },
    onError: (e) => setFormError(e instanceof Error ? e.message : "保存失败"),
  });

  const toggle = useMutation({
    mutationFn: async ({ row, enabled }: { row: TriggerItem; enabled: boolean }) => {
      await setTriggerEnabled(row.id, enabled);
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
      if (deleting) await deleteTrigger(deleting.id);
    },
    onSuccess: () => {
      setDeleting(null);
      setDeleteError("");
      invalidate();
    },
    onError: (e) => setDeleteError(e instanceof Error ? e.message : "删除失败"),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">触发器</h1>
        <Button onClick={() => { setForm(EMPTY_FORM); setFormError(""); }}>
          <Plus aria-hidden />
          新建触发器
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">
        cron 定时(五字段,日/周仅其一可为 *)或 webhook 入站(HMAC 签名+
        随机路径)触发工作流/Bot;目标以创建者身份经全套防御执行。
      </p>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>标识</TableHead>
              <TableHead>类型</TableHead>
              <TableHead>目标</TableHead>
              <TableHead>调度/路径</TableHead>
              <TableHead>启用</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {triggers.isError ? (
              <TableRow>
                <TableCell colSpan={6}>
                  <QueryErrorAlert />
                </TableCell>
              </TableRow>
            ) : (triggers.data ?? []).length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">
                  尚未配置触发器。
                </TableCell>
              </TableRow>
            ) : (
              (triggers.data ?? []).map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="font-mono text-xs">{row.tg_key}</TableCell>
                  <TableCell>
                    <Badge variant="secondary" className="font-normal">
                      {row.type}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-xs">
                    {row.target_type}:{row.target_key}
                  </TableCell>
                  <TableCell className="max-w-56 font-mono text-xs">
                    {row.type === "CRON" ? (
                      <>
                        {row.cron_expr}
                        {row.next_fire_at && (
                          <p className="text-muted-foreground">
                            下次 {new Date(row.next_fire_at).toLocaleString()}
                          </p>
                        )}
                      </>
                    ) : (
                      <span className="break-all">/webhooks/{row.webhook_path}</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <StatusSwitch
                      active={row.enabled}
                      pending={toggle.isPending && toggle.variables?.row.id === row.id}
                      error={toggleError[row.id] || null}
                      onToggle={() => toggle.mutate({ row, enabled: !row.enabled })}
                      ariaLabel={`启停 ${row.name}`}
                    />
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center justify-end gap-1">
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
                        size="xs"
                        onClick={() => {
                          setForm({
                            id: row.id,
                            tgKey: row.tg_key,
                            name: row.name,
                            type: row.type as "CRON" | "WEBHOOK",
                            targetType: row.target_type as "workflow" | "bot",
                            targetKey: row.target_key,
                            cronExpr: row.cron_expr ?? "0 9 * * *",
                            minInterval: String(row.min_interval_sec),
                            adapter: row.adapter as "custom" | "github",
                            notifyUrl: row.notify_url,
                            secret: "",
                          });
                          setFormError("");
                        }}
                      >
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

      {/* 新建/编辑 */}
      <Dialog
        open={form !== null}
        onOpenChange={(o) => {
          if (save.isPending) return;
          if (!o) setForm(null);
        }}
      >
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>{form?.id ? "编辑触发器" : "新建触发器"}</DialogTitle>
          </DialogHeader>
          {form && (
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                save.mutate();
              }}
            >
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="tg-key">标识</Label>
                  <Input
                    id="tg-key"
                    value={form.tgKey}
                    disabled={form.id !== null}
                    onChange={(e) => setForm({ ...form, tgKey: e.target.value })}
                    className="font-mono"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="tg-name">名称</Label>
                  <Input
                    id="tg-name"
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                {form.id === null && (
                  <div className="space-y-1.5">
                    <Label>类型</Label>
                    <Select
                      value={form.type}
                      onValueChange={(v) =>
                        setForm({ ...form, type: v as "CRON" | "WEBHOOK" })
                      }
                    >
                      <SelectTrigger aria-label="类型">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="CRON">cron 定时</SelectItem>
                        <SelectItem value="WEBHOOK">webhook 入站</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                )}
                <div className="space-y-1.5">
                  <Label>目标类型</Label>
                  <Select
                    value={form.targetType}
                    onValueChange={(v) =>
                      setForm({ ...form, targetType: v as "workflow" | "bot" })
                    }
                  >
                    <SelectTrigger aria-label="目标类型">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="workflow">工作流</SelectItem>
                      <SelectItem value="bot">Bot</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="tg-target">目标标识</Label>
                  <Input
                    id="tg-target"
                    value={form.targetKey}
                    onChange={(e) => setForm({ ...form, targetKey: e.target.value })}
                    className="font-mono"
                    placeholder="wf_key / bot_key"
                  />
                </div>
              </div>
              {form.type === "CRON" && (
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="tg-cron">cron 表达式</Label>
                    <Input
                      id="tg-cron"
                      value={form.cronExpr}
                      onChange={(e) => setForm({ ...form, cronExpr: e.target.value })}
                      className="font-mono"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="tg-interval">最小间隔(秒)</Label>
                    <Input
                      id="tg-interval"
                      value={form.minInterval}
                      onChange={(e) => setForm({ ...form, minInterval: e.target.value })}
                      className="font-mono"
                    />
                  </div>
                </div>
              )}
              {form.type === "WEBHOOK" && form.id === null && (
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="tg-secret">签名密钥(留空自动生成)</Label>
                    <Input
                      id="tg-secret"
                      value={form.secret}
                      onChange={(e) => setForm({ ...form, secret: e.target.value })}
                      className="font-mono"
                      type="password"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label>事件源适配器</Label>
                    <Select
                      value={form.adapter}
                      onValueChange={(v) =>
                        setForm({ ...form, adapter: v as "custom" | "github" })
                      }
                    >
                      <SelectTrigger aria-label="适配器">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="custom">custom(透传)</SelectItem>
                        <SelectItem value="github">github</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="tg-notify">结果出站通知 URL(可空)</Label>
                <Input
                  id="tg-notify"
                  value={form.notifyUrl}
                  onChange={(e) => setForm({ ...form, notifyUrl: e.target.value })}
                  className="font-mono text-xs"
                  placeholder="https://example.com/hook(经 SSRF 判定)"
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
                  保存
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      {/* 创建后一次性展示(webhook URL + 密钥) */}
      <Dialog open={created !== null} onOpenChange={(o) => !o && setCreated(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>webhook 已创建</DialogTitle>
            <DialogDescription>
              以下信息仅此一次展示,请立即保存;密钥不回显于任何列表。
            </DialogDescription>
          </DialogHeader>
          {created && (
            <div className="space-y-2 text-sm">
              <p className="break-all font-mono text-xs">
                URL: {location.origin}/webhooks/{created.webhook_path}
              </p>
              {created.secret && (
                <p className="break-all font-mono text-xs">
                  密钥: {created.secret}
                </p>
              )}
              <p className="text-xs text-muted-foreground">
                签名: X-Signature = hex(hmac_sha256(密钥, 请求体))
              </p>
            </div>
          )}
          <DialogFooter>
            <Button onClick={() => setCreated(null)}>我已保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 历史 */}
      <Dialog open={historyOf !== null} onOpenChange={(o) => !o && setHistoryOf(null)}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>执行历史「{historyOf?.name}」</DialogTitle>
            <DialogDescription>按时间倒序的触发执行记录。</DialogDescription>
          </DialogHeader>
          <div className="max-h-96 space-y-2 overflow-y-auto">
            {runs.isError ? (
              <QueryErrorAlert />
            ) : (runs.data?.items ?? []).length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">暂无记录。</p>
            ) : (
              (runs.data?.items ?? []).map((r) => (
                <div key={r.id} className="rounded-md border px-3 py-2 text-sm">
                  <Badge
                    variant={r.status === "SUCCESS" ? "secondary" : "destructive"}
                    className="font-normal"
                  >
                    {r.status}
                  </Badge>
                  <span className="ml-2 text-xs text-muted-foreground">
                    {r.duration_ms}ms ·{" "}
                    {r.created_at ? new Date(r.created_at).toLocaleString() : ""}
                  </span>
                  {r.error && (
                    <p className="mt-1 text-xs text-destructive">{r.error}</p>
                  )}
                </div>
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

      {/* 删除确认 */}
      <AlertDialog
        open={deleting !== null}
        onOpenChange={(o) => {
          if (remove.isPending) return;
          if (!o) setDeleting(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除触发器「{deleting?.name}」?</AlertDialogTitle>
            <AlertDialogDescription>
              删除后不再调度,入站 webhook 立即失效(历史记录保留)。此操作不可恢复。
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
