/* 连接器管理页(099 http-connector-domain,web-admin spec「连接器管理页」):
   列表(名称/key/方法与路径/鉴权形态与尾号掩码/启停/更新时间,DataView 双形态)+
   新建/编辑表单(声明全集;凭证空框语义:编辑不回填、留空=不修改、填入=替换,
   任何位置不展示明文)+ 启停 StatusSwitch + 删除受控确认(明示注入集即时失效)+
   测试调用(显式 AlertDialog 确认后真实出站一次,展示状态码/摘要/耗时/失败分类)。
   表单校验错误在表单上方结构化展示(服务端 400 明文原因)。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Loader2, Plus, Trash2 } from "lucide-react";

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
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
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
import { Textarea } from "@/components/ui/textarea";
import { DataView } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import { FormRow } from "@/components/form-row";
import { PageHeader } from "@/components/page-header";
import { StatusSwitch } from "@/components/status-switch";
import {
  createConnector,
  deleteConnector,
  listConnectors,
  setConnectorEnabled,
  testConnector,
  updateConnector,
  type ConnectorOut,
  type ConnectorParamIn,
  type TestCallOut,
} from "@/lib/connector-api";

const METHOD_LABELS: Record<string, string> = { GET: "GET(读)", POST: "POST(写)" };
const AUTH_LABELS: Record<string, string> = {
  none: "无鉴权",
  bearer: "Bearer 令牌",
  api_key: "API-Key 头",
  basic: "Basic(user:password)",
};
const PARAM_TYPE_LABELS: Record<string, string> = {
  string: "字符串",
  number: "数值",
  boolean: "布尔",
};

type ParamRow = ConnectorParamIn;

type FormState = {
  id: string | null; // null=新建
  key: string;
  name: string;
  description: string;
  method: "GET" | "POST";
  pathTemplate: string;
  params: ParamRow[];
  allowedHostsText: string; // 一行一个主机
  authType: string;
  authSecret: string; // 编辑态留空=不修改(空框语义)
  confirmMode: "auto" | "yes" | "no";
  timeoutMs: string;
};

function emptyForm(): FormState {
  return {
    id: null,
    key: "",
    name: "",
    description: "",
    method: "GET",
    pathTemplate: "",
    params: [],
    allowedHostsText: "",
    authType: "none",
    authSecret: "",
    confirmMode: "auto",
    timeoutMs: "5000",
  };
}

/* ── 表单 → 请求体的纯函数装配(可测逻辑) ── */

export function parseHosts(text: string): string[] {
  return text
    .split("\n")
    .map((h) => h.trim())
    .filter((h) => h.length > 0);
}

export function resolveConfirmation(mode: "auto" | "yes" | "no"): boolean | null {
  if (mode === "auto") return null; // 缺省规则:非 GET 需确认(GET 豁免)
  return mode === "yes";
}

export function formToBody(form: FormState): {
  key: string;
  name: string;
  description: string;
  method: string;
  path_template: string;
  params: ConnectorParamIn[];
  allowed_hosts: string[];
  auth_type: string;
  auth_secret: string | null;
  requires_confirmation: boolean | null;
  timeout_ms: number;
} {
  const secret = form.authSecret.trim();
  return {
    key: form.key.trim(),
    name: form.name.trim(),
    description: form.description.trim(),
    method: form.method,
    path_template: form.pathTemplate.trim(),
    params: form.params.map((p) => ({
      name: p.name.trim(),
      type: p.type,
      required: p.required,
      description: p.description.trim(),
    })),
    allowed_hosts: parseHosts(form.allowedHostsText),
    auth_type: form.authType,
    // 空框语义:留空=不修改(服务端 null 语义);填入=替换
    auth_secret: secret === "" ? null : secret,
    requires_confirmation: resolveConfirmation(form.confirmMode),
    timeout_ms: Number(form.timeoutMs) || 0,
  };
}

function rowToForm(row: ConnectorOut): FormState {
  return {
    id: row.id,
    key: row.key,
    name: row.name,
    description: row.description,
    method: row.method === "POST" ? "POST" : "GET",
    pathTemplate: row.path_template,
    params: row.params.map((p) => ({
      name: String(p.name ?? ""),
      type: String(p.type ?? "string"),
      required: Boolean(p.required),
      description: String(p.description ?? ""),
    })),
    allowedHostsText: row.allowed_hosts.join("\n"),
    authType: row.auth_type,
    authSecret: "", // 凭证不回填(空框语义)
    confirmMode: row.requires_confirmation ? "yes" : "no",
    timeoutMs: String(row.timeout_ms),
  };
}

/* ── 测试调用对话框的声明快照(行级或表单级入口共用) ── */

type TestSnapshot = {
  title: string;
  body: ReturnType<typeof formToBody> & { use_saved_secret: boolean };
  argNames: { name: string; type: string; required: boolean }[];
};

export default function ConnectorsPage() {
  const qc = useQueryClient();
  const [form, setForm] = useState<FormState | null>(null);
  const [formError, setFormError] = useState("");
  const [deleting, setDeleting] = useState<ConnectorOut | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const [toggleError, setToggleError] = useState<Record<string, string>>({});
  // 测试调用:显式确认 → 真实出站 → 结果展示
  const [testSnap, setTestSnap] = useState<TestSnapshot | null>(null);
  const [testArgs, setTestArgs] = useState<Record<string, string>>({});
  const [testResult, setTestResult] = useState<TestCallOut | null>(null);
  const [testError, setTestError] = useState("");

  const connectors = useQuery({ queryKey: ["admin", "connectors"], queryFn: listConnectors });

  const invalidate = () => void qc.invalidateQueries({ queryKey: ["admin", "connectors"] });

  const save = useMutation({
    mutationFn: async () => {
      if (!form) return;
      const body = formToBody(form);
      if (form.id === null) {
        await createConnector(body);
        return;
      }
      await updateConnector(form.id, body);
    },
    onSuccess: () => {
      setForm(null);
      setFormError("");
      invalidate();
    },
    onError: (e) => setFormError(e instanceof Error ? e.message : "保存失败"),
  });

  const toggle = useMutation({
    mutationFn: async ({ row, enabled }: { row: ConnectorOut; enabled: boolean }) => {
      await setConnectorEnabled(row.id, enabled);
    },
    onSuccess: (_d, v) => setToggleError((m) => ({ ...m, [v.row.id]: "" })),
    onError: (e, v) =>
      setToggleError((m) => ({
        ...m,
        [v.row.id]: e instanceof Error ? e.message : "操作失败",
      })),
  });

  const remove = useMutation({
    mutationFn: async () => {
      if (deleting) await deleteConnector(deleting.id);
    },
    onSuccess: () => {
      setDeleting(null);
      setDeleteError("");
      invalidate();
    },
    onError: (e) => setDeleteError(e instanceof Error ? e.message : "删除失败"),
  });

  const runTest = useMutation({
    mutationFn: async () => {
      if (!testSnap) return null;
      // 声明参数按名装配示例入参(string/number 原文提交,boolean 转 true/false;
      // 缺省空值不携带——与 LLM 调用的入参形态一致)
      const args: Record<string, unknown> = {};
      for (const a of testSnap.argNames) {
        const v = (testArgs[a.name] ?? "").trim();
        if (v === "") continue;
        if (a.type === "number") {
          const n = Number(v);
          if (!Number.isNaN(n)) args[a.name] = n;
          else args[a.name] = v;
        } else if (a.type === "boolean") {
          args[a.name] = v === "true";
        } else {
          args[a.name] = v;
        }
      }
      return await testConnector({ ...testSnap.body, args, use_saved_secret: true });
    },
    onSuccess: (r) => {
      if (r) setTestResult(r);
    },
    onError: (e) => setTestError(e instanceof Error ? e.message : "测试调用失败"),
  });

  function openRowTest(row: ConnectorOut): void {
    const body = { ...formToBody(rowToForm(row)), use_saved_secret: true };
    setTestArgs({});
    setTestResult(null);
    setTestError("");
    setTestSnap({
      title: row.name,
      body,
      argNames: row.params.map((p) => ({
        name: String(p.name ?? ""),
        type: String(p.type ?? "string"),
        required: Boolean(p.required),
      })),
    });
  }

  function openFormTest(): void {
    if (!form) return;
    const body = {
      ...formToBody(form),
      // 编辑态凭证留空=用存量凭证测试(先测后存);新建态材料随表单携带
      use_saved_secret: form.id !== null && form.authSecret.trim() === "",
    };
    setTestArgs({});
    setTestResult(null);
    setTestError("");
    setTestSnap({
      title: form.name || form.key,
      body,
      argNames: form.params
        .filter((p) => p.name.trim())
        .map((p) => ({ name: p.name.trim(), type: p.type, required: p.required })),
    });
  }

  function openEdit(row: ConnectorOut): void {
    setForm(rowToForm(row));
    setFormError("");
  }

  const methodPlaceholder =
    form?.method === "GET" ? "缺省=豁免确认(读端点)" : "缺省=需确认(写端点,执行前走人工确认流)";

  return (
    <div className="space-y-4">
      <PageHeader
        title="连接器"
        description="租户自助注册 HTTP 端点为 Agent 工具:出口主机白名单 + SSRF 防护 + 凭证加密落库(任何界面不回显明文,仅尾号掩码)+ 全量审计。注册/启停即时生效。"
        actions={
          <Button
            onClick={() => {
              setForm(emptyForm());
              setFormError("");
            }}
          >
            <Plus aria-hidden />
            注册连接器
          </Button>
        }
      />

      {connectors.isError && <ErrorAlert />}

      <DataView
        rows={connectors.data ?? []}
        rowKey={(row) => row.id}
        isLoading={connectors.isPending}
        isEmpty={
          !connectors.isPending && !connectors.isError && (connectors.data ?? []).length === 0
        }
        emptyText="尚未注册连接器。"
        columns={[
          {
            key: "key",
            header: "标识",
            cardPriority: 3,
            cellClassName: "font-mono text-xs",
            render: (row) => row.key,
          },
          {
            key: "name",
            header: "名称",
            cardPriority: 1,
            render: (row) => (
              <span className="block max-w-40 truncate" title={row.name}>
                {row.name}
              </span>
            ),
          },
          {
            key: "endpoint",
            header: "方法与路径",
            cardPriority: 2,
            render: (row) => (
              <span className="block max-w-64 truncate font-mono text-xs" title={row.path_template}>
                <Badge variant="outline" className="mr-1.5">
                  {row.method}
                </Badge>
                {row.path_template}
              </span>
            ),
          },
          {
            key: "auth",
            header: "鉴权",
            cardPriority: 4,
            render: (row) => (
              <span className="text-xs">
                {AUTH_LABELS[row.auth_type] ?? row.auth_type}
                {row.has_secret && (
                  <Badge variant="secondary" className="ml-1.5 font-mono">
                    {row.secret_mask || "****"}
                  </Badge>
                )}
              </span>
            ),
          },
          {
            key: "confirm",
            header: "写语义",
            cardPriority: 5,
            render: (row) =>
              row.requires_confirmation ? (
                <Badge variant="secondary">需确认</Badge>
              ) : (
                <Badge variant="outline">读</Badge>
              ),
          },
          {
            key: "enabled",
            header: "启用",
            cardPriority: 2,
            render: (row) => (
              <StatusSwitch
                active={row.enabled === "ENABLED"}
                pending={toggle.isPending && toggle.variables?.row.id === row.id}
                error={toggleError[row.id] || null}
                onToggle={() => toggle.mutate({ row, enabled: row.enabled !== "ENABLED" })}
                ariaLabel={`启停 ${row.name}`}
              />
            ),
          },
        ]}
        actions={[
          { label: "测试调用", onSelect: (row) => openRowTest(row) },
          { label: "编辑", onSelect: openEdit },
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

      {/* 新建/编辑对话框 */}
      <Dialog
        open={form !== null}
        onOpenChange={(o) => {
          if (save.isPending) return;
          if (!o) setForm(null);
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>{form?.id ? "编辑连接器" : "注册连接器"}</DialogTitle>
          </DialogHeader>
          {form && (
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                save.mutate();
              }}
            >
              <FormRow label="标识(key)" htmlFor="conn-key">
                <Input
                  id="conn-key"
                  value={form.key}
                  disabled={form.id !== null} // 创建后不可改(工具名单源)
                  onChange={(e) => setForm({ ...form, key: e.target.value })}
                  placeholder="小写字母开头,如 orders_api"
                  className="font-mono"
                />
              </FormRow>
              <FormRow label="名称" htmlFor="conn-name">
                <Input
                  id="conn-name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="LLM 据此判断何时调用(过输入护栏)"
                />
              </FormRow>
              <FormRow label="描述" htmlFor="conn-desc">
                <Textarea
                  id="conn-desc"
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                  placeholder="工具用途与适用场景(LLM 可见;过输入护栏)"
                  rows={2}
                />
              </FormRow>
              <FormRow label="方法" htmlFor="conn-method">
                <Select
                  value={form.method}
                  onValueChange={(v) => setForm({ ...form, method: v === "POST" ? "POST" : "GET" })}
                >
                  <SelectTrigger id="conn-method" aria-label="方法">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(METHOD_LABELS).map(([k, label]) => (
                      <SelectItem key={k} value={k}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FormRow>
              <FormRow label="路径模板" htmlFor="conn-path">
                <Input
                  id="conn-path"
                  value={form.pathTemplate}
                  onChange={(e) => setForm({ ...form, pathTemplate: e.target.value })}
                  placeholder="https://api.example.com/v1/orders/{order_id}"
                  className="font-mono text-xs"
                />
                <p className="text-xs text-muted-foreground">
                  完整 URL 模板;{"{占位}"}对应下方参数声明(值 URL 编码后直插), scheme
                  与端口由此约束。
                </p>
              </FormRow>

              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <Label>参数声明</Label>
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    onClick={() =>
                      setForm({
                        ...form,
                        params: [
                          ...form.params,
                          { name: "", type: "string", required: false, description: "" },
                        ],
                      })
                    }
                  >
                    <Plus aria-hidden />
                    加一个参数
                  </Button>
                </div>
                {form.params.length === 0 && (
                  <p className="text-xs text-muted-foreground">
                    无参数(无占位且无查询/body 入参)。
                  </p>
                )}
                {form.params.map((p, i) => (
                  <div key={i} className="space-y-1.5 rounded-md border p-2">
                    <div className="flex items-center gap-2">
                      <Input
                        value={p.name}
                        onChange={(e) =>
                          setForm({
                            ...form,
                            params: form.params.map((x, j) =>
                              j === i ? { ...x, name: e.target.value } : x,
                            ),
                          })
                        }
                        placeholder="参数名(如 order_id)"
                        className="font-mono text-xs"
                        aria-label={`参数 ${i + 1} 名`}
                      />
                      <Select
                        value={p.type}
                        onValueChange={(v) =>
                          setForm({
                            ...form,
                            params: form.params.map((x, j) => (j === i ? { ...x, type: v } : x)),
                          })
                        }
                      >
                        <SelectTrigger className="w-24" aria-label={`参数 ${i + 1} 类型`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {Object.entries(PARAM_TYPE_LABELS).map(([k, label]) => (
                            <SelectItem key={k} value={k}>
                              {label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <label className="flex items-center gap-1.5 text-xs whitespace-nowrap">
                        <Checkbox
                          checked={p.required}
                          onCheckedChange={(c) =>
                            setForm({
                              ...form,
                              params: form.params.map((x, j) =>
                                j === i ? { ...x, required: c === true } : x,
                              ),
                            })
                          }
                          aria-label={`参数 ${i + 1} 必填`}
                        />
                        必填
                      </label>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-xs"
                        aria-label={`移除参数 ${i + 1}`}
                        onClick={() =>
                          setForm({ ...form, params: form.params.filter((_, j) => j !== i) })
                        }
                      >
                        <Trash2 aria-hidden />
                      </Button>
                    </div>
                    <Input
                      value={p.description}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          params: form.params.map((x, j) =>
                            j === i ? { ...x, description: e.target.value } : x,
                          ),
                        })
                      }
                      placeholder="参数说明(LLM 可见)"
                      className="text-xs"
                      aria-label={`参数 ${i + 1} 描述`}
                    />
                  </div>
                ))}
              </div>

              <FormRow label="出口主机白名单" htmlFor="conn-hosts">
                <Textarea
                  id="conn-hosts"
                  value={form.allowedHostsText}
                  onChange={(e) => setForm({ ...form, allowedHostsText: e.target.value })}
                  placeholder={"api.example.com\ncorp.internal.example"}
                  rows={2}
                  className="font-mono text-xs"
                />
                <p className="text-xs text-muted-foreground">
                  一行一个精确主机(禁通配/路径/端口);模板主机必须命中,未命中出站即拒。
                </p>
              </FormRow>
              <FormRow label="鉴权形态" htmlFor="conn-auth">
                <Select
                  value={form.authType}
                  onValueChange={(v) => setForm({ ...form, authType: v })}
                >
                  <SelectTrigger id="conn-auth" aria-label="鉴权形态">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(AUTH_LABELS).map(([k, label]) => (
                      <SelectItem key={k} value={k}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FormRow>
              {form.authType !== "none" && (
                <FormRow label="鉴权材料" htmlFor="conn-secret">
                  <Input
                    id="conn-secret"
                    type="password"
                    value={form.authSecret}
                    onChange={(e) => setForm({ ...form, authSecret: e.target.value })}
                    placeholder={form.id !== null ? "留空=不修改" : "凭证材料"}
                    autoComplete="new-password"
                    className="font-mono text-xs"
                  />
                  {form.id !== null && (
                    <p className="text-xs text-muted-foreground">
                      材料不回填(空框=不修改,填入=替换);Basic 形态填 user:password。
                    </p>
                  )}
                </FormRow>
              )}
              <FormRow label="写语义" htmlFor="conn-confirm">
                <Select
                  value={form.confirmMode}
                  onValueChange={(v) =>
                    setForm({
                      ...form,
                      confirmMode: v === "yes" ? "yes" : v === "no" ? "no" : "auto",
                    })
                  }
                >
                  <SelectTrigger id="conn-confirm" aria-label="写语义">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">跟随方法缺省</SelectItem>
                    <SelectItem value="yes">需确认(写语义)</SelectItem>
                    <SelectItem value="no">豁免确认(读语义)</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">{methodPlaceholder}</p>
              </FormRow>
              <FormRow label="超时(毫秒)" htmlFor="conn-timeout">
                <Input
                  id="conn-timeout"
                  type="number"
                  min={100}
                  value={form.timeoutMs}
                  onChange={(e) => setForm({ ...form, timeoutMs: e.target.value })}
                />
              </FormRow>

              {formError && (
                <p
                  role="alert"
                  className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm text-destructive"
                >
                  {formError}
                </p>
              )}
              <DialogFooter className="sm:justify-between">
                <Button
                  type="button"
                  variant="outline"
                  disabled={save.isPending || runTest.isPending}
                  onClick={openFormTest}
                >
                  <FlaskConical aria-hidden />
                  测试调用
                </Button>
                <Button type="submit" disabled={save.isPending}>
                  {save.isPending && <Loader2 className="animate-spin" aria-hidden />}
                  保存
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      {/* 删除受控确认(明示即时失效,spec 场景) */}
      <AlertDialog
        open={deleting !== null}
        onOpenChange={(o) => {
          if (remove.isPending) return;
          if (!o) setDeleting(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除连接器「{deleting?.name}」?</AlertDialogTitle>
            <AlertDialogDescription>
              删除后该工具即时从注入集消失,Agent 调用立即失效;删除为硬删并落操作审计。
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

      {/* 测试调用:显式确认 → 真实出站一次 → 状态码/摘要/耗时/失败分类 */}
      <AlertDialog
        open={testSnap !== null}
        onOpenChange={(o) => {
          if (runTest.isPending) return;
          if (!o) setTestSnap(null);
        }}
      >
        <AlertDialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
          <AlertDialogHeader>
            <AlertDialogTitle>测试调用「{testSnap?.title}」?</AlertDialogTitle>
            <AlertDialogDescription>
              将按当前声明向目标端点发起一次真实出站(落工具审计);确认后执行。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {testSnap && testSnap.argNames.length > 0 && (
            <div className="space-y-2">
              {testSnap.argNames.map((a) => (
                <FormRow
                  key={a.name}
                  label={`${a.name}${a.required ? " *" : ""}`}
                  htmlFor={`ta-${a.name}`}
                >
                  {a.type === "boolean" ? (
                    <Select
                      value={testArgs[a.name] ?? ""}
                      onValueChange={(v) => setTestArgs({ ...testArgs, [a.name]: v })}
                    >
                      <SelectTrigger id={`ta-${a.name}`} aria-label={`参数 ${a.name}`}>
                        <SelectValue placeholder="省略" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="true">true</SelectItem>
                        <SelectItem value="false">false</SelectItem>
                      </SelectContent>
                    </Select>
                  ) : (
                    <Input
                      id={`ta-${a.name}`}
                      value={testArgs[a.name] ?? ""}
                      inputMode={a.type === "number" ? "decimal" : "text"}
                      onChange={(e) => setTestArgs({ ...testArgs, [a.name]: e.target.value })}
                      aria-label={`参数 ${a.name}`}
                    />
                  )}
                </FormRow>
              ))}
            </div>
          )}
          {runTest.isPending && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
              <Loader2 className="animate-spin" aria-hidden />
              出站中…
            </p>
          )}
          {testError && (
            <p role="alert" className="text-sm text-destructive">
              {testError}
            </p>
          )}
          {testResult && (
            <div className="space-y-1.5 text-xs">
              {testResult.ok ? (
                <p className="flex flex-wrap items-center gap-2">
                  <Badge>HTTP {testResult.status_code ?? "-"}</Badge>
                  <span className="text-muted-foreground">耗时 {testResult.duration_ms}ms</span>
                </p>
              ) : (
                <p role="alert" className="text-destructive">
                  调用失败({testResult.failure ?? "未知分类"});耗时 {testResult.duration_ms}ms
                </p>
              )}
              {testResult.body_summary && (
                <pre className="max-h-48 overflow-y-auto rounded-md border bg-muted/40 p-2 whitespace-pre-wrap">
                  {testResult.body_summary}
                </pre>
              )}
            </div>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={runTest.isPending}>关闭</AlertDialogCancel>
            <Button
              disabled={runTest.isPending || testSnap === null}
              onClick={() => runTest.mutate()}
            >
              {runTest.isPending ? (
                <Loader2 className="animate-spin" aria-hidden />
              ) : (
                <FlaskConical aria-hidden />
              )}
              {testResult !== null ? "重新调用" : "确认调用"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
