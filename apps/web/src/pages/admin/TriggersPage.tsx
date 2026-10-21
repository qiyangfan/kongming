/* 管理触发器的创建、编辑、启停和执行历史。Webhook 密钥创建后只展示一次；编辑邮件触发器时凭证留空表示保留现值。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus } from "lucide-react";

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { EmptyState } from "@/components/empty-state";
import { StatusSwitch } from "@/components/status-switch";
import { StatusBadge, type StatusMeta } from "@/components/status-badge";
import { FormRow } from "@/components/form-row";
import { PageHeader } from "@/components/page-header";
import { DataView } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import { SchedulePicker } from "@/components/schedule-picker";
import {
  createTrigger,
  deleteTrigger,
  listTriggers,
  pageTriggerRuns,
  setTriggerEnabled,
  updateTrigger,
  type TriggerItem,
} from "@/lib/trigger-api";
import { cronPreview } from "@/lib/trigger-api";
import { useDebounced } from "@/hooks/use-debounced";
import { listWorkflows } from "@/lib/workflow-api";
import { api } from "@/lib/api";
import { formatDateTime, formatDuration } from "@/lib/datetime";

const PAGE_SIZE = 10;

// 触发器类型中文(列表徽章与表单下拉同源;后端枚举 CRON/WEBHOOK/EMAIL)
const TYPE_LABELS: Record<string, string> = {
  CRON: "定时调度",
  WEBHOOK: "webhook 触发",
  EMAIL: "邮件触发",
};

type TriggerType = "CRON" | "WEBHOOK" | "EMAIL";

// 执行状态中文(后端枚举:成功/失败/间隔节流跳过/挂起重试);未收录值回退原值
const RUN_STATUS_META: StatusMeta = {
  SUCCESS: { label: "成功", variant: "secondary" },
  FAILED: { label: "失败", variant: "destructive" },
  SKIPPED: { label: "已跳过", variant: "outline" },
  PENDING: { label: "待重试", variant: "outline" },
};

// 最小间隔快捷档(点击填入对应秒数)
const INTERVAL_PRESETS: { label: string; seconds: number }[] = [
  { label: "1 分钟", seconds: 60 },
  { label: "5 分钟", seconds: 300 },
  { label: "15 分钟", seconds: 900 },
  { label: "1 小时", seconds: 3600 },
];

type FormState = {
  id: string | null;
  tgKey: string;
  name: string;
  type: TriggerType;
  targetType: "workflow" | "bot";
  targetKey: string;
  cronExpr: string;
  minInterval: string;
  adapter: "custom" | "github";
  notifyUrl: string;
  secret: string;
  maxRetries: string;
  // 邮件形态(凭证仅写入不回显;编辑空框=保持现值)
  imapHost: string;
  imapPort: string;
  imapTls: boolean;
  imapUser: string;
  imapPassword: string;
  imapFolder: string;
  imapPollInterval: string;
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
  maxRetries: "0",
  imapHost: "",
  imapPort: "993",
  imapTls: true,
  imapUser: "",
  imapPassword: "",
  imapFolder: "INBOX",
  imapPollInterval: "300",
};

// 邮件形态保存前校验(创建须填主机/账号/凭证;编辑凭证空框=保持现值)
function emailFormInvalid(form: FormState): boolean {
  const port = Number(form.imapPort);
  return (
    form.imapHost.trim() === "" ||
    form.imapUser.trim() === "" ||
    (form.id === null && form.imapPassword === "") ||
    !Number.isInteger(port) ||
    port < 1 ||
    port > 65535
  );
}

// 重试上限解析与值域(0–5;空串=未填按 0;非整数/越界为非法)
function parseMaxRetries(raw: string): { value: number; invalid: boolean } {
  const t = raw.trim();
  if (t === "") return { value: 0, invalid: false };
  const n = Number(t);
  const invalid = !Number.isInteger(n) || n < 0 || n > 5;
  return { value: invalid ? 0 : n, invalid };
}

export default function TriggersPage() {
  const qc = useQueryClient();
  const [form, setForm] = useState<FormState | null>(null);
  const [formError, setFormError] = useState("");
  const [created, setCreated] = useState<{ webhook_path: string | null; secret?: string } | null>(
    null,
  );
  const [deleting, setDeleting] = useState<TriggerItem | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const [historyOf, setHistoryOf] = useState<TriggerItem | null>(null);
  const [historyPage, setHistoryPage] = useState(1);
  const [toggleError, setToggleError] = useState<Record<string, string>>({});
  // 重试上限表单校验(0–5;非法禁保存)
  const retries = parseMaxRetries(form?.maxRetries ?? "0");

  const triggers = useQuery({ queryKey: ["admin", "triggers"], queryFn: listTriggers });
  // 目标候选(封闭集合,按类型联动;自由输入拼错会挂到运行期才暴露)。
  // 弹窗打开时才拉取,候选随 form.targetType 切换取用。
  const workflows = useQuery({
    queryKey: ["admin", "workflow"],
    queryFn: listWorkflows,
    enabled: form !== null,
  });
  const bots = useQuery({
    queryKey: ["bots"],
    queryFn: () => api.get<{ bot_key: string; name: string }[]>("/system/bots"),
    enabled: form !== null,
  });

  // cron 预览使用 400ms 防抖；空表达式或弹窗关闭时不发请求。
  const cronExprD = useDebounced(form?.cronExpr ?? "", 400);
  const cronFires = useQuery({
    queryKey: ["admin", "cron-preview", cronExprD],
    queryFn: () => cronPreview(cronExprD),
    enabled: form !== null && form.type === "CRON" && cronExprD.trim() !== "",
  });
  const runs = useQuery({
    queryKey: ["admin", "trigger-runs", historyOf?.id, historyPage],
    queryFn: () => pageTriggerRuns(historyOf!.id, historyPage, PAGE_SIZE),
    enabled: historyOf !== null,
  });

  const invalidate = () => void qc.invalidateQueries({ queryKey: ["admin", "triggers"] });

  const save = useMutation({
    mutationFn: async () => {
      if (!form) return null;
      const retries = parseMaxRetries(form.maxRetries);
      const payload: Record<string, unknown> = {
        name: form.name.trim(),
        target_type: form.targetType,
        target_key: form.targetKey.trim(),
        cron_expr: form.type === "CRON" ? form.cronExpr.trim() : null,
        // 空串才回退 60,0 是合法值,不可用 ||
        min_interval_sec: form.minInterval.trim() === "" ? 60 : Number(form.minInterval),
        adapter: form.adapter,
        notify_url: form.notifyUrl.trim(),
        // 失败重试次数上限(0=不重试)
        max_retries: retries.value,
      };
      if (form.type === "EMAIL") {
        // 邮件形态配置(凭证空框=后端保持现值;下限封顶在后端)
        payload.imap_host = form.imapHost.trim();
        payload.imap_port = Number(form.imapPort);
        payload.imap_tls = form.imapTls;
        payload.imap_user = form.imapUser.trim();
        payload.imap_password = form.imapPassword;
        payload.imap_folder = form.imapFolder.trim() || "INBOX";
        payload.imap_poll_interval_sec =
          form.imapPollInterval.trim() === "" ? 300 : Number(form.imapPollInterval);
      }
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
        setCreated(out); // 密钥只在创建成功后展示一次。
      }
      invalidate();
    },
    onError: (e) => {
      // TARGET_NOT_FOUND 等后端 detail 已在 lib/api 统一转译为中文
      setFormError(e instanceof Error ? e.message : "保存失败");
    },
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
      <PageHeader
        title="触发器"
        description="定时调度(常用频率点选,或高级 cron 表达式)、webhook 触发(外部系统调用专属地址即运行)或邮件触发(IMAP 收件箱到达新邮件即运行)工作流/助手;执行时以创建者身份经过完整安全校验。"
        actions={
          <Button
            onClick={() => {
              setForm(EMPTY_FORM);
              setFormError("");
            }}
          >
            <Plus aria-hidden />
            新建触发器
          </Button>
        }
      />

      {triggers.isError && <ErrorAlert />}

      <DataView
        rows={triggers.data ?? []}
        rowKey={(row) => row.id}
        isLoading={triggers.isPending}
        isEmpty={!triggers.isPending && !triggers.isError && (triggers.data ?? []).length === 0}
        emptyText="尚未配置触发器。"
        columns={[
          {
            key: "tg_key",
            header: "标识",
            cardPriority: 1,
            cellClassName: "font-mono text-xs",
            render: (row) => row.tg_key,
          },
          {
            key: "type",
            header: "类型",
            cardPriority: 4,
            render: (row) => <Badge variant="secondary">{TYPE_LABELS[row.type] ?? row.type}</Badge>,
          },
          {
            key: "target",
            header: "目标",
            cellClassName: "text-xs",
            render: (row) => `${row.target_type}:${row.target_key}`,
          },
          {
            key: "schedule",
            header: "调度/地址",
            cardPriority: 3,
            cellClassName: "max-w-56 truncate font-mono text-xs",
            render: (row) =>
              row.type === "CRON" ? (
                <>
                  <span className="block truncate" title={row.cron_expr ?? undefined}>
                    {row.cron_expr}
                  </span>
                  {row.next_fire_at && (
                    <p className="whitespace-normal text-muted-foreground">
                      下次 {formatDateTime(row.next_fire_at)}
                    </p>
                  )}
                </>
              ) : row.type === "EMAIL" ? (
                <>
                  {/* 邮件形态展示 IMAP 连接要点(凭证永不回显) */}
                  <span className="block truncate" title={row.imap_host}>
                    {row.imap_host}/{row.imap_folder}
                  </span>
                  <p className="whitespace-normal text-muted-foreground">
                    每 {row.imap_poll_interval_sec} 秒轮询
                    {row.next_fire_at ? ` · 下次 ${formatDateTime(row.next_fire_at)}` : ""}
                  </p>
                </>
              ) : (
                <span className="block truncate" title={`/webhooks/${row.webhook_path}`}>
                  /webhooks/{row.webhook_path}
                </span>
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
            label: "历史",
            onSelect: (row) => {
              setHistoryOf(row);
              setHistoryPage(1);
            },
          },
          {
            label: "编辑",
            onSelect: (row) => {
              setForm({
                id: row.id,
                tgKey: row.tg_key,
                name: row.name,
                type: row.type as TriggerType,
                targetType: row.target_type as "workflow" | "bot",
                targetKey: row.target_key,
                cronExpr: row.cron_expr ?? "0 9 * * *",
                minInterval: String(row.min_interval_sec),
                adapter: row.adapter as "custom" | "github",
                notifyUrl: row.notify_url,
                secret: "",
                maxRetries: String(row.max_retries ?? 0),
                imapHost: row.imap_host ?? "",
                imapPort: String(row.imap_port ?? 993),
                imapTls: row.imap_tls ?? true,
                imapUser: row.imap_user ?? "",
                imapPassword: "", // 空框=保持现值(后端口径)
                imapFolder: row.imap_folder ?? "INBOX",
                imapPollInterval: String(row.imap_poll_interval_sec ?? 300),
              });
              setFormError("");
            },
          },
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
                <FormRow label="标识" htmlFor="tg-key">
                  <Input
                    id="tg-key"
                    value={form.tgKey}
                    disabled={form.id !== null}
                    onChange={(e) => setForm({ ...form, tgKey: e.target.value })}
                    className="font-mono"
                  />
                </FormRow>
                <FormRow label="名称" htmlFor="tg-name">
                  <Input
                    id="tg-name"
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                  />
                </FormRow>
              </div>
              <div className="grid grid-cols-2 gap-3">
                {form.id === null && (
                  <FormRow label="类型">
                    <Select
                      value={form.type}
                      onValueChange={(v) => setForm({ ...form, type: v as TriggerType })}
                    >
                      <SelectTrigger aria-label="类型">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {Object.entries(TYPE_LABELS).map(([k, label]) => (
                          <SelectItem key={k} value={k}>
                            {label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </FormRow>
                )}
                <FormRow label="目标类型">
                  <Select
                    value={form.targetType}
                    onValueChange={(v) =>
                      // 类型切换即清空目标——两类候选键不通用,防跨域残留
                      setForm({ ...form, targetType: v as "workflow" | "bot", targetKey: "" })
                    }
                  >
                    <SelectTrigger aria-label="目标类型">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="workflow">工作流</SelectItem>
                      <SelectItem value="bot">助手</SelectItem>
                    </SelectContent>
                  </Select>
                </FormRow>
                <FormRow label={`目标${form.targetType === "workflow" ? "(工作流)" : "(助手)"}`}>
                  {/* 自由输入改候选下拉(候选=对应域可见清单);
                      编辑回显时目标已删则注入禁用占位项,防「打开即保存」静默改值 */}
                  <Select
                    value={form.targetKey}
                    onValueChange={(v) => setForm({ ...form, targetKey: v })}
                  >
                    <SelectTrigger aria-label="目标">
                      <SelectValue
                        placeholder={form.targetType === "workflow" ? "选择工作流" : "选择助手"}
                      />
                    </SelectTrigger>
                    <SelectContent>
                      {form.targetType === "workflow"
                        ? (workflows.data ?? []).map((w) => (
                            <SelectItem key={w.wf_key} value={w.wf_key}>
                              {w.name}({w.wf_key})
                            </SelectItem>
                          ))
                        : (bots.data ?? []).map((b) => (
                            <SelectItem key={b.bot_key} value={b.bot_key}>
                              {b.name}({b.bot_key})
                            </SelectItem>
                          ))}
                      {/* 候选加载中/失败时不判「已删除」:清单未到不等于目标已删,
                          避免编辑回显被误读 */}
                      {form.targetKey !== "" &&
                        !(form.targetType === "workflow"
                          ? workflows.isPending || workflows.isError
                          : bots.isPending || bots.isError) &&
                        !(form.targetType === "workflow"
                          ? (workflows.data ?? []).some((w) => w.wf_key === form.targetKey)
                          : (bots.data ?? []).some((b) => b.bot_key === form.targetKey)) && (
                          <SelectItem value={form.targetKey} disabled>
                            (已删除){form.targetKey}
                          </SelectItem>
                        )}
                    </SelectContent>
                  </Select>
                </FormRow>
              </div>
              {form.type === "CRON" && (
                <div className="space-y-3">
                  <SchedulePicker
                    value={form.cronExpr}
                    onChange={(cronExpr) => setForm({ ...form, cronExpr })}
                  />
                  <FormRow label="最小间隔(秒)" htmlFor="tg-interval">
                    <Input
                      id="tg-interval"
                      value={form.minInterval}
                      onChange={(e) => setForm({ ...form, minInterval: e.target.value })}
                      className="w-32 font-mono"
                    />
                    <div className="flex flex-wrap gap-1">
                      {INTERVAL_PRESETS.map((p) => (
                        <Button
                          key={p.seconds}
                          type="button"
                          variant="outline"
                          size="xs"
                          onClick={() => setForm({ ...form, minInterval: String(p.seconds) })}
                        >
                          {p.label}
                        </Button>
                      ))}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      两次触发之间的最小间隔秒数;间隔内的重复触发会跳过不执行。
                    </p>
                  </FormRow>
                  {/* 预览接下来三次触发时间;非法表达式显示错误原因,与保存校验一致。
                      仅当防抖值与当前输入一致才显示,防抖窗口内的旧结果不展示 */}
                  {form.cronExpr.trim() !== "" && (
                    <p
                      role={cronFires.isError ? "alert" : undefined}
                      className={
                        cronFires.isError
                          ? "text-xs text-destructive"
                          : "text-xs text-muted-foreground"
                      }
                    >
                      {cronFires.isError
                        ? `cron 无法计算:${cronFires.error instanceof Error ? cronFires.error.message : "表达式非法"}`
                        : cronFires.data && form.cronExpr.trim() === cronExprD
                          ? `接下来 3 次:${cronFires.data.next.map((s) => formatDateTime(s)).join("、")}`
                          : "预览计算中…"}
                    </p>
                  )}
                </div>
              )}
              {form.type === "EMAIL" && (
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <FormRow label="IMAP 主机" htmlFor="tg-imap-host">
                      <Input
                        id="tg-imap-host"
                        value={form.imapHost}
                        onChange={(e) => setForm({ ...form, imapHost: e.target.value })}
                        className="font-mono"
                        placeholder="imap.example.com"
                      />
                    </FormRow>
                    <FormRow label="端口" htmlFor="tg-imap-port">
                      <Input
                        id="tg-imap-port"
                        value={form.imapPort}
                        onChange={(e) => setForm({ ...form, imapPort: e.target.value })}
                        className="w-28 font-mono"
                        inputMode="numeric"
                      />
                    </FormRow>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <FormRow label="账号" htmlFor="tg-imap-user">
                      <Input
                        id="tg-imap-user"
                        value={form.imapUser}
                        onChange={(e) => setForm({ ...form, imapUser: e.target.value })}
                        className="font-mono"
                        placeholder="ops@example.com"
                      />
                    </FormRow>
                    <FormRow
                      label={form.id === null ? "凭证(密码)" : "凭证(留空保持现值)"}
                      htmlFor="tg-imap-password"
                    >
                      {/* 凭证任何接口不回显(webhook secret 同口径);
                          编辑空框=保持现值 */}
                      <Input
                        id="tg-imap-password"
                        value={form.imapPassword}
                        onChange={(e) => setForm({ ...form, imapPassword: e.target.value })}
                        className="font-mono"
                        type="password"
                      />
                    </FormRow>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <FormRow label="文件夹" htmlFor="tg-imap-folder">
                      <Input
                        id="tg-imap-folder"
                        value={form.imapFolder}
                        onChange={(e) => setForm({ ...form, imapFolder: e.target.value })}
                        className="font-mono"
                        placeholder="INBOX"
                      />
                    </FormRow>
                    <FormRow label="轮询间隔(秒)" htmlFor="tg-imap-interval">
                      <Input
                        id="tg-imap-interval"
                        value={form.imapPollInterval}
                        onChange={(e) => setForm({ ...form, imapPollInterval: e.target.value })}
                        className="w-32 font-mono"
                        inputMode="numeric"
                      />
                      <p className="text-xs text-muted-foreground">
                        到期即拉取新邮件;低于 30 秒按 30 秒封顶。
                      </p>
                    </FormRow>
                  </div>
                  <FormRow label="TLS 加密连接" htmlFor="tg-imap-tls">
                    <Switch
                      id="tg-imap-tls"
                      checked={form.imapTls}
                      onCheckedChange={(checked) => setForm({ ...form, imapTls: checked })}
                    />
                    <p className="text-xs text-muted-foreground">
                      默认开启(993 端口);关闭则走明文 143 端口,仅限内网自建服务。
                    </p>
                  </FormRow>
                  <p className="text-xs text-muted-foreground">
                    新邮件经标准化摘要(发件人/主题/日期/正文预览,过安全护栏)触发目标; 已处理邮件按
                    UID 游标续传,停用期间的邮件不丢失。
                  </p>
                </div>
              )}
              {form.type === "WEBHOOK" && form.id === null && (
                <div className="grid grid-cols-2 gap-3">
                  <FormRow label="签名密钥(留空自动生成)" htmlFor="tg-secret">
                    <Input
                      id="tg-secret"
                      value={form.secret}
                      onChange={(e) => setForm({ ...form, secret: e.target.value })}
                      className="font-mono"
                      type="password"
                    />
                  </FormRow>
                  <FormRow label="请求数据格式">
                    <Select
                      value={form.adapter}
                      onValueChange={(v) => setForm({ ...form, adapter: v as "custom" | "github" })}
                    >
                      <SelectTrigger aria-label="请求数据格式">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="custom">自定义(手写 JSON)</SelectItem>
                        <SelectItem value="github">GitHub(Webhook 载荷)</SelectItem>
                      </SelectContent>
                    </Select>
                  </FormRow>
                </div>
              )}
              <div className="grid grid-cols-2 gap-3">
                <FormRow label="失败重试上限" htmlFor="tg-max-retries">
                  <Input
                    id="tg-max-retries"
                    value={form.maxRetries}
                    onChange={(e) => setForm({ ...form, maxRetries: e.target.value })}
                    className="w-32 font-mono"
                    inputMode="numeric"
                    aria-invalid={retries.invalid}
                  />
                  <p
                    role={retries.invalid ? "alert" : undefined}
                    className={
                      retries.invalid ? "text-xs text-destructive" : "text-xs text-muted-foreground"
                    }
                  >
                    {retries.invalid
                      ? "须为 0–5 的整数"
                      : "执行失败后自动重试的最大次数;0 = 不重试。"}
                  </p>
                </FormRow>
              </div>
              <FormRow label="执行完成后通知地址(可空)" htmlFor="tg-notify">
                <Input
                  id="tg-notify"
                  value={form.notifyUrl}
                  onChange={(e) => setForm({ ...form, notifyUrl: e.target.value })}
                  className="font-mono text-xs"
                  placeholder="https://example.com/hook(执行结果发送到此地址;仅接受公网地址)"
                />
              </FormRow>
              {formError && (
                <p role="alert" className="text-sm text-destructive">
                  {formError}
                </p>
              )}
              <DialogFooter>
                <Button
                  type="submit"
                  disabled={
                    save.isPending ||
                    !form.targetKey ||
                    retries.invalid ||
                    (form.type === "EMAIL" && emailFormInvalid(form))
                  }
                >
                  {save.isPending && <Loader2 className="animate-spin" aria-hidden />}
                  保存
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

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
                <p className="break-all font-mono text-xs">密钥: {created.secret}</p>
              )}
              <p className="text-xs text-muted-foreground">
                签名校验:请求头 X-Signature 的值为 HMAC-SHA256(密钥, 请求体) 的十六进制串。
              </p>
            </div>
          )}
          <DialogFooter>
            <Button onClick={() => setCreated(null)}>我已保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={historyOf !== null} onOpenChange={(o) => !o && setHistoryOf(null)}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>执行历史「{historyOf?.name}」</DialogTitle>
            <DialogDescription>按时间倒序的触发执行记录。</DialogDescription>
          </DialogHeader>
          <div className="max-h-96 space-y-2 overflow-y-auto">
            {runs.isError ? (
              <ErrorAlert />
            ) : (runs.data?.items ?? []).length === 0 ? (
              <EmptyState>暂无记录。</EmptyState>
            ) : (
              (runs.data?.items ?? []).map((r) => (
                <div key={r.id} className="rounded-md border px-3 py-2 text-sm">
                  <StatusBadge
                    meta={RUN_STATUS_META}
                    value={r.status}
                    fallbackVariant="destructive"
                  />
                  {/* 重试链:attempt>1 标注尝试序号(首跑不标,免噪音) */}
                  {r.attempt > 1 && (
                    <Badge variant="outline" className="ml-2">
                      第 {r.attempt} 次
                    </Badge>
                  )}
                  <span className="ml-2 text-xs text-muted-foreground">
                    {formatDuration(r.duration_ms)} · {formatDateTime(r.created_at)}
                  </span>
                  {r.status === "PENDING" && r.next_retry_at && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      将于 {formatDateTime(r.next_retry_at)} 重试
                    </p>
                  )}
                  {r.error && <p className="mt-1 text-xs text-destructive">{r.error}</p>}
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
              删除后不再触发,webhook 地址随之失效(历史记录保留)。此操作不可恢复。
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
