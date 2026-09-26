/* 用户管理页(web-admin spec「用户管理页」):表格+分页+搜索+创建/编辑对话框
   (用户名创建后不可改、角色多选、部门单选、状态启停)+重置密码+删除确认。 */

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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { StatusSwitch } from "@/components/status-switch";
import { DataView } from "@/components/data-view";
import { FormRow } from "@/components/form-row";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Pagination } from "@/components/pagination";
import { ErrorAlert } from "@/components/error-alert";
import { PageHeader } from "@/components/page-header";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  systemApi,
  type DeptNodeOut,
  type RoleOut,
  type TenantOut,
  type UserItemOut,
} from "@/lib/system-api";
import { roleLabel } from "@/lib/role-labels";
import { formatDate } from "@/lib/datetime";

const PAGE_SIZE = 10;

type FormState = {
  username: string;
  password: string;
  nickname: string;
  email: string;
  status: "ACTIVE" | "DISABLED";
  deptId: string;
  tenantId: string;
  roleIds: string[];
};

const EMPTY_FORM: FormState = {
  username: "",
  password: "",
  nickname: "",
  email: "",
  status: "ACTIVE",
  deptId: "",
  tenantId: "",
  roleIds: [],
};

function flattenDepts(
  nodes: DeptNodeOut[] | undefined,
  depth = 0,
): { id: string; label: string }[] {
  return (nodes ?? []).flatMap((n) => [
    { id: n.id, label: `${"—　".repeat(depth)}${n.dept_name}` },
    ...flattenDepts(n.children, depth + 1),
  ]);
}

export default function UsersPage() {
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<UserItemOut | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState("");
  const [resetting, setResetting] = useState<UserItemOut | null>(null);
  const [resetPw, setResetPw] = useState("");
  const [resetError, setResetError] = useState("");
  const [deleting, setDeleting] = useState<UserItemOut | null>(null);

  const users = useQuery({
    queryKey: ["admin", "users", page, search],
    queryFn: () => systemApi.listUsers(page, PAGE_SIZE, search),
  });

  // 越界自愈:删除末页条目/过滤后总页数收缩时,回退到最后有效页
  useEffect(() => {
    const data = users.data;
    if (!data) return;
    const lastPage = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
    if (page > lastPage) setPage(lastPage);
  }, [users.data, page]);
  const roles = useQuery({ queryKey: ["admin", "roles"], queryFn: systemApi.listRoles });
  const depts = useQuery({ queryKey: ["admin", "depts"], queryFn: systemApi.listDepts });
  const tenants = useQuery({ queryKey: ["admin", "tenants"], queryFn: systemApi.listTenants });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["admin", "users"] });
  };

  const [toggleId, setToggleId] = useState<string | null>(null);
  const [toggleErr, setToggleErr] = useState<string | null>(null);
  const toggle = useMutation({
    mutationFn: (u: UserItemOut) =>
      u.status === "ACTIVE" ? systemApi.disableUser(u.id) : systemApi.enableUser(u.id),
    onMutate: (u) => {
      setToggleId(u.id);
      setToggleErr(null);
    },
    onSuccess: () => {
      setToggleId(null);
      invalidate();
    },
    onError: (e: Error) => setToggleErr(e.message), // 失败回弹+行内可见(012)
  });

  const create = useMutation({
    mutationFn: () =>
      systemApi.createUser({
        username: form.username,
        password: form.password,
        nickname: form.nickname,
        email: form.email || null,
        status: form.status,
        dept_id: form.deptId || null,
        tenant_id: form.tenantId,
        role_ids: form.roleIds,
      }),
    onSuccess: () => {
      setCreating(false);
      invalidate();
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const update = useMutation({
    mutationFn: () =>
      systemApi.updateUser(editing!.id, {
        nickname: form.nickname,
        email: form.email || null,
        status: form.status,
        dept_id: form.deptId || null,
        tenant_id: form.tenantId,
        role_ids: form.roleIds,
        password: form.password || null,
      }),
    onSuccess: () => {
      setEditing(null);
      invalidate();
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const reset = useMutation({
    mutationFn: () => systemApi.resetPassword(resetting!.id, resetPw),
    onSuccess: () => {
      setResetting(null);
      setResetPw("");
    },
    onError: (e: Error) => setResetError(e.message),
  });

  const remove = useMutation({
    mutationFn: () => systemApi.deleteUser(deleting!.id),
    onSuccess: () => {
      setDeleting(null);
      invalidate();
    },
  });

  const openCreate = () => {
    setForm({
      ...EMPTY_FORM,
      tenantId: tenants.data?.[0]?.id ?? "",
    });
    setFormError("");
    setCreating(true);
  };

  const openEdit = (u: UserItemOut) => {
    const roleIds =
      roles.data?.filter((r) => u.role_keys.includes(r.role_key)).map((r) => r.id) ?? [];
    setForm({
      username: u.username,
      password: "",
      nickname: u.nickname,
      email: u.email ?? "",
      status: u.status === "DISABLED" ? "DISABLED" : "ACTIVE",
      deptId: u.dept_id ?? "",
      tenantId: u.tenant_id ?? tenants.data?.[0]?.id ?? "",
      roleIds,
    });
    setFormError("");
    setEditing(u);
  };

  const submit = () => {
    setFormError("");
    if (!form.nickname.trim()) return setFormError("昵称不可为空");
    if (!form.tenantId) return setFormError("必须选择租户");
    if (creating && form.password.length < 8) return setFormError("密码至少 8 位");
    if (editing && form.password && form.password.length < 8) return setFormError("密码至少 8 位");
    // 角色清单未就绪时禁止提交编辑:roleIds 会按空集覆盖,静默清空既有角色绑定
    if (editing && !roles.data && editing.role_keys.length > 0) {
      return setFormError("角色清单尚未加载完成,请稍后重试");
    }
    if (creating) create.mutate();
    else update.mutate();
  };

  const total = users.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const deptOptions = flattenDepts(depts.data ?? []);

  return (
    <div className="space-y-3">
      <PageHeader
        title="用户"
        description="租户成员账号管理:搜索、新建,分配角色与部门,启用停用。"
        actions={
          <Button size="sm" onClick={openCreate}>
            新建用户
          </Button>
        }
      />
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
          placeholder="搜索用户名/昵称,回车确认"
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
      </div>

      {users.isError && <ErrorAlert text={`用户列表加载失败:${(users.error as Error).message}`} />}

      <DataView
        rows={users.data?.items ?? []}
        rowKey={(u) => u.id}
        isLoading={users.isPending}
        isEmpty={!users.isPending && users.data?.items.length === 0}
        emptyText="无匹配用户"
        columns={[
          {
            key: "username",
            header: "用户名",
            cardPriority: 1,
            render: (u) => (
              <span className="block max-w-40 truncate font-medium" title={u.username}>
                {u.username}
              </span>
            ),
          },
          {
            key: "nickname",
            header: "昵称",
            cardPriority: 2,
            render: (u) => (
              <span className="block max-w-32 truncate" title={u.nickname}>
                {u.nickname}
              </span>
            ),
          },
          {
            key: "status",
            header: "状态",
            cardPriority: 3,
            render: (u) => (
              <StatusSwitch
                active={u.status === "ACTIVE"}
                pending={toggle.isPending}
                error={!toggle.isPending && toggleId === u.id ? toggleErr : null}
                onToggle={() => toggle.mutate(u)}
                ariaLabel={`${u.username} 启用状态`}
              />
            ),
          },
          {
            key: "role_keys",
            header: "角色",
            cardPriority: 4,
            render: (u) => (
              <div className="flex flex-wrap gap-1">
                {u.role_keys.length === 0 ? (
                  <span className="text-muted-foreground text-sm">—</span>
                ) : (
                  u.role_keys.map((k) => (
                    <Badge key={k} variant="outline">
                      {roleLabel(k)}
                    </Badge>
                  ))
                )}
              </div>
            ),
          },
          {
            key: "dept_name",
            header: "部门",
            render: (u) => (
              <span className="block max-w-32 truncate" title={u.dept_name ?? undefined}>
                {u.dept_name ?? "—"}
              </span>
            ),
          },
          {
            key: "created_at",
            header: "创建时间",
            cellClassName: "text-muted-foreground",
            render: (u) => formatDate(u.created_at),
          },
        ]}
        actions={[
          { label: "编辑", onSelect: openEdit },
          {
            label: "重置密码",
            onSelect: (u) => {
              setResetError("");
              setResetPw("");
              setResetting(u);
            },
          },
          { label: "删除", destructive: true, onSelect: setDeleting },
        ]}
      />

      <Pagination page={page} pageCount={pages} total={total} onPageChange={setPage} />

      <Dialog
        open={creating || editing !== null}
        onOpenChange={(open) => {
          if (!open) {
            setCreating(false);
            setEditing(null);
          }
        }}
      >
        <DialogContent aria-describedby={undefined} className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{creating ? "新建用户" : `编辑用户 ${form.username}`}</DialogTitle>
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
            {creating && (
              <FormRow label="用户名" htmlFor="u-username">
                <Input
                  id="u-username"
                  value={form.username}
                  onChange={(e) => setForm({ ...form, username: e.target.value })}
                />
              </FormRow>
            )}
            <FormRow label={`密码${editing ? "(留空不改)" : ""}`} htmlFor="u-password">
              <Input
                id="u-password"
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
              />
            </FormRow>
            <FormRow label="昵称" htmlFor="u-nickname">
              <Input
                id="u-nickname"
                value={form.nickname}
                onChange={(e) => setForm({ ...form, nickname: e.target.value })}
              />
            </FormRow>
            <FormRow label="邮箱(可空)" htmlFor="u-email">
              <Input
                id="u-email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </FormRow>
            <div className="grid grid-cols-2 gap-3">
              <FormRow label="状态">
                <Select
                  value={form.status}
                  onValueChange={(v) => setForm({ ...form, status: v as FormState["status"] })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="ACTIVE">启用</SelectItem>
                    <SelectItem value="DISABLED">禁用</SelectItem>
                  </SelectContent>
                </Select>
              </FormRow>
              <FormRow label="租户">
                <Select
                  value={form.tenantId}
                  onValueChange={(v) => setForm({ ...form, tenantId: v })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="选择租户" />
                  </SelectTrigger>
                  <SelectContent>
                    {(tenants.data ?? []).map((t: TenantOut) => (
                      <SelectItem key={t.id} value={t.id}>
                        {t.tenant_name}({t.tenant_key})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FormRow>
            </div>
            <FormRow label="部门(可空)">
              <Select
                value={form.deptId || "none"}
                onValueChange={(v) => setForm({ ...form, deptId: v === "none" ? "" : v })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="未绑定" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">未绑定</SelectItem>
                  {deptOptions.map((d) => (
                    <SelectItem key={d.id} value={d.id}>
                      {d.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormRow>
            <FormRow label="角色(可多选)">
              <div className="space-y-1.5 rounded-md border p-2">
                {(roles.data ?? []).map((r: RoleOut) => (
                  <label key={r.id} className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={form.roleIds.includes(r.id)}
                      onCheckedChange={(checked) =>
                        setForm({
                          ...form,
                          roleIds: checked
                            ? [...form.roleIds, r.id]
                            : form.roleIds.filter((x) => x !== r.id),
                        })
                      }
                    />
                    {r.role_name}({r.role_key})
                  </label>
                ))}
              </div>
            </FormRow>
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

      <Dialog open={resetting !== null} onOpenChange={(open) => !open && setResetting(null)}>
        <DialogContent aria-describedby={undefined} className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>重置密码 — {resetting?.username}</DialogTitle>
          </DialogHeader>
          {resetError && (
            <p className="text-destructive text-sm" role="alert">
              {resetError}
            </p>
          )}
          <form
            className="grid gap-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              setResetError("");
              if (resetPw.length < 8) return setResetError("密码至少 8 位");
              reset.mutate();
            }}
          >
            <FormRow label="新密码(重置后强制首登改密)" htmlFor="u-reset">
              <Input
                id="u-reset"
                type="password"
                value={resetPw}
                onChange={(e) => setResetPw(e.target.value)}
              />
            </FormRow>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setResetting(null)}>
                取消
              </Button>
              <Button type="submit" disabled={reset.isPending}>
                {reset.isPending ? "重置中…" : "重置"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除用户 {deleting?.username}?</AlertDialogTitle>
            <AlertDialogDescription>
              删除为软删除:该用户无法再登录,用户名可被新用户复用。此操作可由管理员感知但不可自助恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {remove.isError && (
            <p className="text-destructive text-sm" role="alert">
              {(remove.error as Error).message}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            {/* 受控关闭:mutation 成功才关对话框(onSuccess setDeleting(null)),
                失败保持开启展示下方错误——Radix Action 点击即关,错误行会随卸载不可见 */}
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
