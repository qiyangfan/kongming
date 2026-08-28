/* 角色管理页(web-admin spec「角色管理页」):表格+创建/编辑
   (roleKey 创建后不可改,dataScope/sqlScope 下拉)+权限绑定面板
   (按命名空间分组复选,覆盖式保存)+内置角色删除禁用。 */

import { useMemo, useState } from "react";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  systemApi,
  type DataScopeValue,
  type PermOut,
  type RoleOut,
  type SqlScopeValue,
} from "@/lib/system-api";

const DATA_SCOPES: { value: DataScopeValue; label: string }[] = [
  { value: "ALL", label: "全部数据" },
  { value: "CUSTOM", label: "自定义(预留)" },
  { value: "DEPT_AND_BELOW", label: "本部门及以下" },
  { value: "DEPT", label: "仅本部门" },
  { value: "SELF", label: "仅本人" },
];

const SQL_SCOPES: { value: SqlScopeValue; label: string }[] = [
  { value: "SELECT_ONLY", label: "只读查询" },
  { value: "READ_DDL", label: "只读+DDL" },
  { value: "DML_DDL", label: "读写+DDL" },
];

type RoleForm = {
  roleKey: string;
  roleName: string;
  dataScope: DataScopeValue;
  sqlScope: SqlScopeValue;
  sort: number;
};

const EMPTY: RoleForm = {
  roleKey: "",
  roleName: "",
  dataScope: "SELF",
  sqlScope: "SELECT_ONLY",
  sort: 0,
};

/** 权限点按 permCode 首段(命名空间)分组(rbac spec「权限点清单查询」) */
function groupPerms(perms: PermOut[]): [string, PermOut[]][] {
  const groups = new Map<string, PermOut[]>();
  for (const p of perms) {
    const ns = p.perm_code.split(":")[0] ?? "other";
    groups.set(ns, [...(groups.get(ns) ?? []), p]);
  }
  return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

export default function RolesPage() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<RoleOut | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<RoleForm>(EMPTY);
  const [formError, setFormError] = useState("");
  const [binding, setBinding] = useState<RoleOut | null>(null);
  const [boundIds, setBoundIds] = useState<string[]>([]);
  const [bindError, setBindError] = useState("");
  const [deleting, setDeleting] = useState<RoleOut | null>(null);
  const [deleteError, setDeleteError] = useState("");

  const roles = useQuery({ queryKey: ["admin", "roles"], queryFn: systemApi.listRoles });
  const perms = useQuery({ queryKey: ["admin", "perms"], queryFn: systemApi.listPerms });

  const create = useMutation({
    mutationFn: () =>
      systemApi.createRole({
        role_key: form.roleKey,
        role_name: form.roleName,
        data_scope: form.dataScope,
        sql_scope: form.sqlScope,
        sort: form.sort,
      }),
    onSuccess: () => {
      setCreating(false);
      void qc.invalidateQueries({ queryKey: ["admin", "roles"] });
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const update = useMutation({
    mutationFn: () =>
      systemApi.updateRole(editing!.id, {
        role_key: form.roleKey,
        role_name: form.roleName,
        data_scope: form.dataScope,
        sql_scope: form.sqlScope,
        sort: form.sort,
      }),
    onSuccess: () => {
      setEditing(null);
      void qc.invalidateQueries({ queryKey: ["admin", "roles"] });
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const bind = useMutation({
    mutationFn: () => systemApi.bindRolePerms(binding!.id, boundIds),
    onSuccess: () => {
      setBinding(null);
      void qc.invalidateQueries({ queryKey: ["admin", "roles"] });
    },
    onError: (e: Error) => setBindError(e.message),
  });

  const remove = useMutation({
    mutationFn: () => systemApi.deleteRole(deleting!.id),
    onSuccess: () => {
      setDeleting(null);
      void qc.invalidateQueries({ queryKey: ["admin", "roles"] });
    },
    onError: (e: Error) => setDeleteError(e.message),
  });

  const grouped = useMemo(() => groupPerms(perms.data ?? []), [perms.data]);

  const submit = () => {
    setFormError("");
    if (creating && !form.roleKey.trim()) return setFormError("角色键不可为空");
    if (!form.roleName.trim()) return setFormError("角色名称不可为空");
    if (creating) create.mutate();
    else update.mutate();
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          多角色用户:权限点取并集,数据范围取最宽松。
        </p>
        <Button
          size="sm"
          onClick={() => {
            setForm(EMPTY);
            setFormError("");
            setCreating(true);
          }}
        >
          新建角色
        </Button>
      </div>

      {roles.isError && (
        <p className="text-destructive text-sm" role="alert">
          角色列表加载失败:{(roles.error as Error).message}
        </p>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>角色键</TableHead>
            <TableHead>名称</TableHead>
            <TableHead>数据范围</TableHead>
            <TableHead>SQL 范围</TableHead>
            <TableHead>排序</TableHead>
            <TableHead>权限点</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {roles.data?.map((r) => (
            <TableRow key={r.id}>
              <TableCell className="max-w-40 truncate font-medium">
                <span className="flex items-center gap-1.5">
                  <span title={r.role_key} className="truncate">{r.role_key}</span>
                  {r.builtin && (
                    <Badge variant="secondary" className="shrink-0">
                      内置
                    </Badge>
                  )}
                </span>
              </TableCell>
              <TableCell className="max-w-40 truncate" title={r.role_name}>{r.role_name}</TableCell>
              <TableCell>{DATA_SCOPES.find((d) => d.value === r.data_scope)?.label ?? r.data_scope}</TableCell>
              <TableCell>{SQL_SCOPES.find((s) => s.value === r.sql_scope)?.label ?? r.sql_scope}</TableCell>
              <TableCell>{r.sort}</TableCell>
              <TableCell>{r.perm_ids.length} 项</TableCell>
              <TableCell className="text-right">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setForm({
                      roleKey: r.role_key,
                      roleName: r.role_name,
                      dataScope: r.data_scope as DataScopeValue,
                      sqlScope: r.sql_scope as SqlScopeValue,
                      sort: r.sort,
                    });
                    setFormError("");
                    setEditing(r);
                  }}
                >
                  编辑
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setBoundIds(r.perm_ids);
                    setBindError("");
                    setBinding(r);
                  }}
                >
                  权限
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-destructive hover:text-destructive"
                  disabled={r.builtin}
                  title={r.builtin ? "内置角色不可删除" : undefined}
                  onClick={() => {
                    setDeleteError("");
                    setDeleting(r);
                  }}
                >
                  删除
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <Dialog open={creating || editing !== null} onOpenChange={(open) => {
        if (!open) {
          setCreating(false);
          setEditing(null);
        }
      }}>
        <DialogContent aria-describedby={undefined} className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{creating ? "新建角色" : `编辑角色 ${form.roleKey}`}</DialogTitle>
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
            <div className="grid gap-1.5">
              <Label htmlFor="r-key">角色键</Label>
              <Input
                id="r-key"
                value={form.roleKey}
                disabled={editing !== null}
                onChange={(e) => setForm({ ...form, roleKey: e.target.value })}
              />
              {editing && (
                <p className="text-muted-foreground text-xs">角色键创建后不可修改</p>
              )}
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="r-name">名称</Label>
              <Input
                id="r-name"
                value={form.roleName}
                onChange={(e) => setForm({ ...form, roleName: e.target.value })}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <Label>数据范围</Label>
                <Select
                  value={form.dataScope}
                  onValueChange={(v) => setForm({ ...form, dataScope: v as DataScopeValue })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {DATA_SCOPES.map((d) => (
                      <SelectItem key={d.value} value={d.value}>
                        {d.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-1.5">
                <Label>SQL 范围</Label>
                <Select
                  value={form.sqlScope}
                  onValueChange={(v) => setForm({ ...form, sqlScope: v as SqlScopeValue })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SQL_SCOPES.map((s) => (
                      <SelectItem key={s.value} value={s.value}>
                        {s.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="r-sort">排序</Label>
              <Input
                id="r-sort"
                type="number"
                value={form.sort}
                onChange={(e) => setForm({ ...form, sort: Number(e.target.value) || 0 })}
              />
            </div>
            <DialogFooter>
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
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={binding !== null} onOpenChange={(open) => !open && setBinding(null)}>
        <DialogContent aria-describedby={undefined} className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>权限绑定 — {binding?.role_name}</DialogTitle>
          </DialogHeader>
          {bindError && (
            <p className="text-destructive text-sm" role="alert">
              {bindError}
            </p>
          )}
          <div className="max-h-80 space-y-3 overflow-y-auto">
            {grouped.map(([ns, list]) => (
              <div key={ns} className="space-y-1.5">
                <p className="text-muted-foreground text-xs font-medium">{ns}</p>
                <div className="space-y-1 rounded-md border p-2">
                  {list.map((p) => (
                    <label key={p.id} className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={boundIds.includes(p.id)}
                        onCheckedChange={(checked) =>
                          setBoundIds((ids) =>
                            checked ? [...ids, p.id] : ids.filter((x) => x !== p.id),
                          )
                        }
                      />
                      {p.perm_name}
                      <span className="text-muted-foreground text-xs">{p.perm_code}</span>
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <p className="text-muted-foreground text-xs">保存为全量覆盖:未勾选项将被移除。</p>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setBinding(null)}>
              取消
            </Button>
            <Button onClick={() => bind.mutate()} disabled={bind.isPending}>
              {bind.isPending ? "保存中…" : "保存"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除角色 {deleting?.role_key}?</AlertDialogTitle>
            <AlertDialogDescription>
              仍绑定未软删用户的角色会被拒绝;删除后其权限绑定一并清除。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deleteError && (
            <p className="text-destructive text-sm" role="alert">
              {deleteError}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            {/* 受控关闭:成功才关对话框,失败保持开启展示上方错误(Radix Action 点击即关) */}
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
