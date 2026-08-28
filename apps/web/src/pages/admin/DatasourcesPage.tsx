/* 数据源管理页(web-admin 008 delta + datasource spec「管理 UI」):
   分页列表 + 搜索 + 创建/编辑对话框(key/default_schema/租户创建后不可改,编辑态禁改回显;
   密码留空不改)+ 启停 + 删除确认(受控:成功才关)+ 测试连接(编辑态留空走库存密文)。
   文案提示(004 裁定挂账):行级过滤依赖目标库 create_by 列,注册账号请使用只读账号。 */

import { useEffect, useState } from "react";
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
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { StatusSwitch } from "@/components/status-switch";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  datasourceApi,
  type DatasourceItemOut,
  type TestConnectionOut,
} from "@/lib/datasource-api";
import { systemApi } from "@/lib/system-api";

const PAGE_SIZE = 10;

type FormState = {
  datasourceKey: string;
  name: string;
  tenantId: string;
  host: string;
  port: string;
  database: string;
  username: string;
  password: string;
  defaultSchema: string;
  readonly: boolean;
};

const EMPTY_FORM: FormState = {
  datasourceKey: "",
  name: "",
  tenantId: "",
  host: "",
  port: "5432",
  database: "",
  username: "",
  password: "",
  defaultSchema: "public",
  readonly: true,
};

export default function DatasourcesPage() {
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<DatasourceItemOut | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState("");
  const [deleting, setDeleting] = useState<DatasourceItemOut | null>(null);
  const [testResult, setTestResult] = useState<TestConnectionOut | null>(null);
  const [toggleId, setToggleId] = useState<string | null>(null);
  const [toggleError, setToggleError] = useState("");

  const datasources = useQuery({
    queryKey: ["admin", "datasources", page, search],
    queryFn: () => datasourceApi.page(page, PAGE_SIZE, search),
  });
  // 租户下拉(创建时显式指定归属;复用 007 租户只读清单)
  const tenants = useQuery({ queryKey: ["admin", "tenants"], queryFn: systemApi.listTenants });

  // 越界自愈:删除末页条目/过滤后总页数收缩时,回退到最后有效页
  useEffect(() => {
    const data = datasources.data;
    if (!data) return;
    const lastPage = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
    if (page > lastPage) setPage(lastPage);
  }, [datasources.data, page]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["admin", "datasources"] });
    // 选项接口(对话页切换器)同失效:启停/删除即时反映到用户面
    void qc.invalidateQueries({ queryKey: ["datasource-options"] });
  };

  const create = useMutation({
    mutationFn: () =>
      datasourceApi.create({
        datasource_key: form.datasourceKey.trim(),
        name: form.name.trim(),
        tenant_id: form.tenantId,
        host: form.host.trim(),
        port: Number(form.port),
        database: form.database.trim(),
        username: form.username.trim(),
        password: form.password,
        default_schema: form.defaultSchema.trim(),
        readonly: form.readonly,
      }),
    onSuccess: () => {
      setCreating(false);
      invalidate();
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const update = useMutation({
    mutationFn: () =>
      datasourceApi.update(editing!.id, {
        datasource_key: form.datasourceKey,
        tenant_id: form.tenantId,
        name: form.name.trim(),
        host: form.host.trim(),
        port: Number(form.port),
        database: form.database.trim(),
        username: form.username.trim(),
        password: form.password || null, // 留空=不改
        default_schema: form.defaultSchema,
        readonly: form.readonly,
      }),
    onSuccess: () => {
      setEditing(null);
      invalidate();
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const toggle = useMutation({
    mutationFn: (item: DatasourceItemOut) =>
      item.status === "ACTIVE" ? datasourceApi.disable(item.id) : datasourceApi.enable(item.id),
    onMutate: (item) => {
      setToggleId(item.id);
      setToggleError("");
    },
    onSuccess: () => {
      setToggleId(null);
      invalidate();
    },
    onError: (e: Error) => setToggleError(e.message), // R1-web-1:启停失败不得静默(012 收敛为行内)
  });

  const remove = useMutation({
    mutationFn: () => datasourceApi.remove(deleting!.id),
    onSuccess: () => {
      setDeleting(null);
      invalidate();
    },
  });

  const testConn = useMutation({
    mutationFn: () =>
      datasourceApi.testConnection({
        id: editing?.id ?? null,
        host: form.host.trim(),
        port: Number(form.port),
        database: form.database.trim(),
        username: form.username.trim(),
        password: form.password || null, // 编辑态留空 → 服务端取库存密文补位
      }),
    onSuccess: (result) => setTestResult(result),
    onError: (e: Error) => setTestResult({ ok: false, reason: e.message }),
  });

  const openCreate = () => {
    setForm({ ...EMPTY_FORM, tenantId: tenants.data?.[0]?.id ?? "" });
    setFormError("");
    setTestResult(null);
    setCreating(true);
  };

  const openEdit = (d: DatasourceItemOut) => {
    setForm({
      datasourceKey: d.datasource_key,
      name: d.name,
      tenantId: d.tenant_id,
      host: d.host,
      port: String(d.port),
      database: d.database,
      username: d.username,
      password: "",
      defaultSchema: d.default_schema,
      readonly: d.readonly,
    });
    setFormError("");
    setTestResult(null);
    setEditing(d);
  };

  const submit = () => {
    setFormError("");
    if (creating && !/^[a-z][a-z0-9_]{1,63}$/.test(form.datasourceKey.trim())) {
      return setFormError("数据源键须为小写蛇形(小写字母起头,2..64 位)");
    }
    if (!form.name.trim()) return setFormError("名称不可为空");
    if (!form.tenantId) return setFormError("必须选择归属租户");
    if (!form.host.trim() || !form.database.trim() || !form.username.trim()) {
      return setFormError("主机/库名/用户名不可为空");
    }
    const port = Number(form.port);
    if (!Number.isInteger(port) || port < 1 || port > 65535) return setFormError("端口不合法");
    if (creating && !form.password) return setFormError("密码不可为空");
    if (creating) create.mutate();
    else update.mutate();
  };

  const total = datasources.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Input
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              setPage(1);
              setSearch(keyword.trim());
            }
          }}
          placeholder="搜索数据源键/名称,回车确认"
          className="max-w-64"
        />
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setPage(1);
            setSearch(keyword.trim());
          }}
        >
          搜索
        </Button>
        <div className="flex-1" />
        <Button size="sm" onClick={openCreate}>
          注册数据源
        </Button>
      </div>

      {datasources.isError && (
        <p className="text-destructive text-sm" role="alert">
          数据源列表加载失败:{(datasources.error as Error).message}
        </p>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>键</TableHead>
            <TableHead>名称</TableHead>
            <TableHead>归属租户</TableHead>
            <TableHead>连接</TableHead>
            <TableHead>默认 schema</TableHead>
            <TableHead>状态</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {datasources.isPending && (
            <TableRow>
              <TableCell colSpan={7} className="text-center text-muted-foreground">
                加载中…
              </TableCell>
            </TableRow>
          )}
          {datasources.data?.items.map((d) => (
            <TableRow key={d.id}>
              <TableCell className="max-w-32 truncate font-medium" title={d.datasource_key}>{d.datasource_key}</TableCell>
              <TableCell className="max-w-32 truncate" title={d.name}>{d.name}</TableCell>
              <TableCell className="max-w-40 truncate" title={`${d.tenant_name}(${d.tenant_key})`}>
                {d.tenant_name}({d.tenant_key})
              </TableCell>
              <TableCell className="text-muted-foreground">
                <span className="block max-w-56 truncate" title={`${d.host}:${d.port}/${d.database}`}>
                  {d.host}:{d.port}/{d.database}
                </span>
              </TableCell>
              <TableCell>{d.default_schema}</TableCell>
              <TableCell>
                <StatusSwitch
                  active={d.status === "ACTIVE"}
                  pending={toggle.isPending}
                  error={!toggle.isPending && toggleId === d.id && toggleError ? toggleError : null}
                  onToggle={() => toggle.mutate(d)}
                  ariaLabel={`${d.name} 启用状态`}
                />
              </TableCell>
              <TableCell className="text-right">
                <Button variant="ghost" size="sm" onClick={() => openEdit(d)}>
                  编辑
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-destructive hover:text-destructive"
                  onClick={() => setDeleting(d)}
                >
                  删除
                </Button>
              </TableCell>
            </TableRow>
          ))}
          {datasources.data && datasources.data.items.length === 0 && (
            <TableRow>
              <TableCell colSpan={7} className="text-center text-muted-foreground">
                暂无数据源
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>

      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>共 {total} 条</span>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            上一页
          </Button>
          <span>
            {page} / {pages}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= pages}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </Button>
        </div>
      </div>

      <Dialog
        open={creating || editing !== null}
        onOpenChange={(open) => {
          if (!open) {
            setCreating(false);
            setEditing(null);
          }
        }}
      >
        <DialogContent aria-describedby={undefined} className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {creating ? "注册数据源" : `编辑数据源 ${form.datasourceKey}`}
            </DialogTitle>
          </DialogHeader>
          {formError && (
            <p className="text-destructive text-sm" role="alert">
              {formError}
            </p>
          )}
          <form
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="ds-key">数据源键</Label>
                <Input
                  id="ds-key"
                  value={form.datasourceKey}
                  disabled={!creating}
                  placeholder="如 sakila(创建后不可改)"
                  onChange={(e) => setForm({ ...form, datasourceKey: e.target.value })}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="ds-name">名称</Label>
                <Input
                  id="ds-name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <Label>归属租户</Label>
                <Select
                  value={form.tenantId}
                  disabled={!creating}
                  onValueChange={(v) => setForm({ ...form, tenantId: v })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="选择租户(创建后不可改)" />
                  </SelectTrigger>
                  <SelectContent>
                    {(tenants.data ?? []).map((t) => (
                      <SelectItem key={t.id} value={t.id}>
                        {t.tenant_name}({t.tenant_key})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="ds-schema">默认 schema</Label>
                <Input
                  id="ds-schema"
                  value={form.defaultSchema}
                  disabled={!creating}
                  onChange={(e) => setForm({ ...form, defaultSchema: e.target.value })}
                />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2 grid gap-1.5">
                <Label htmlFor="ds-host">主机</Label>
                <Input
                  id="ds-host"
                  value={form.host}
                  onChange={(e) => setForm({ ...form, host: e.target.value })}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="ds-port">端口</Label>
                <Input
                  id="ds-port"
                  inputMode="numeric"
                  value={form.port}
                  onChange={(e) => setForm({ ...form, port: e.target.value })}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="ds-database">库名</Label>
                <Input
                  id="ds-database"
                  value={form.database}
                  onChange={(e) => setForm({ ...form, database: e.target.value })}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="ds-username">用户名</Label>
                <Input
                  id="ds-username"
                  value={form.username}
                  onChange={(e) => setForm({ ...form, username: e.target.value })}
                />
              </div>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="ds-password">密码{editing ? "(留空不改)" : ""}</Label>
              <Input
                id="ds-password"
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
              />
              <p className="text-xs text-muted-foreground">
                请使用只读账号;行级过滤(数据范围)依赖目标库表的 create_by 列。
              </p>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={form.readonly}
                onCheckedChange={(checked) => setForm({ ...form, readonly: checked === true })}
              />
              只读数据源(语义登记;平台侧另有连接级只读+防火墙双闸)
            </label>
            {testResult && (
              <p
                className={testResult.ok ? "text-sm text-muted-foreground" : "text-destructive text-sm"}
                role="status"
              >
                {testResult.ok ? "连接成功" : `连接失败:${testResult.reason}`}
              </p>
            )}
            <DialogFooter className="gap-2 sm:justify-between">
              <Button
                type="button"
                variant="secondary"
                disabled={testConn.isPending}
                onClick={() => {
                  setTestResult(null);
                  testConn.mutate();
                }}
              >
                {testConn.isPending ? "测试中…" : "测试连接"}
              </Button>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setCreating(false);
                    setEditing(null);
                  }}
                >
                  取消
                </Button>
                <Button type="submit" disabled={create.isPending || update.isPending}>
                  {create.isPending || update.isPending ? "保存中…" : "保存"}
                </Button>
              </div>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除数据源 {deleting?.datasource_key}?</AlertDialogTitle>
            <AlertDialogDescription>
              删除为物理删除:连接池立即失效,后续对该数据源的查询一律拒绝。此操作不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {remove.isError && (
            <p className="text-destructive text-sm" role="alert">
              {(remove.error as Error).message}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            {/* 受控关闭:mutation 成功才关对话框,失败保持开启展示错误(007 教训) */}
            <Button
              variant="destructive"
              onClick={() => remove.mutate()}
              disabled={remove.isPending}
            >
              {remove.isPending ? "删除中…" : "删除"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
