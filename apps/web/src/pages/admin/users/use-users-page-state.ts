/* 用户页的状态、查询、变更与表单逻辑;页面只负责组装。 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { systemApi, type DeptNodeOut, type UserItemOut } from "@/lib/system-api";

export const PAGE_SIZE = 10;

export type UserForm = {
  username: string;
  password: string;
  nickname: string;
  email: string;
  status: "ACTIVE" | "DISABLED";
  deptId: string;
  tenantId: string;
  roleIds: string[];
};

const EMPTY_FORM: UserForm = {
  username: "",
  password: "",
  nickname: "",
  email: "",
  status: "ACTIVE",
  deptId: "",
  tenantId: "",
  roleIds: [],
};

/** 部门树展平为带缩进前缀的选项列表(层级深度用全角破折号缩进)。 */
export function flattenDepts(
  nodes: DeptNodeOut[] | undefined,
  depth = 0,
): { id: string; label: string }[] {
  return (nodes ?? []).flatMap((n) => [
    { id: n.id, label: `${"—　".repeat(depth)}${n.dept_name}` },
    ...flattenDepts(n.children, depth + 1),
  ]);
}

export function useUsersPageState() {
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<UserItemOut | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<UserForm>(EMPTY_FORM);
  const [formError, setFormError] = useState("");

  const users = useQuery({
    queryKey: ["admin", "users", page, search],
    queryFn: () => systemApi.listUsers(page, PAGE_SIZE, search),
  });

  // 越界自愈:删除末页条目/过滤后总页数收缩时,回退到最后有效页
  const { data: usersData } = users;
  useEffect(() => {
    const data = usersData;
    if (!data) return;
    const lastPage = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
    if (page > lastPage) setPage(lastPage);
  }, [usersData, page]);
  const roles = useQuery({ queryKey: ["admin", "roles"], queryFn: systemApi.listRoles });
  const depts = useQuery({ queryKey: ["admin", "depts"], queryFn: systemApi.listDepts });
  const tenants = useQuery({ queryKey: ["admin", "tenants"], queryFn: systemApi.listTenants });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["admin", "users"] });
  };

  const [toggleId, setToggleId] = useState<string | null>(null);
  const [toggleErr, setToggleErr] = useState<string | null>(null);
  const danger = useUserDangerActions(invalidate);

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
    onError: (e: Error) => setToggleErr(e.message), // 失败时开关回弹,错误行内展示
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

  const { openCreate, openEdit, submit, closeForm } = useFormActions({
    creating,
    editing,
    form,
    setForm,
    setFormError,
    setCreating,
    setEditing,
    tenantOptions: tenants.data ?? [],
    roleOptions: roles.data,
    create,
    update,
  });

  return {
    page,
    setPage,
    keyword,
    setKeyword,
    search,
    setSearch,
    editing,
    creating,
    form,
    setForm,
    formError,
    danger,
    users,
    roles,
    depts,
    tenants,
    toggle,
    toggleId,
    toggleErr,
    create,
    update,
    openCreate,
    openEdit,
    submit,
    closeForm,
    formOpen: creating || editing !== null,
    formPending: create.isPending || update.isPending,
    runSearch: () => {
      setPage(1);
      setSearch(keyword.trim());
    },
    usersErrorText: `用户列表加载失败:${users.error?.message ?? ""}`,
  };
}

/** 表单跨字段校验(空昵称/未选租户/密码长度/角色未加载),返回错误文案或 null。 */
function validateUserForm(v: {
  form: UserForm;
  creating: boolean;
  editing: UserItemOut | null;
  rolesLoaded: boolean;
}): string | null {
  const { form, creating, editing, rolesLoaded } = v;
  if (!form.nickname.trim()) return "昵称不可为空";
  if (!form.tenantId) return "必须选择租户";
  if (creating && form.password.length < 8) return "密码至少 8 位";
  if (editing && form.password && form.password.length < 8) return "密码至少 8 位";
  // 角色清单未就绪时禁止提交编辑:roleIds 会按空集覆盖,静默清空既有角色绑定
  if (editing && !rolesLoaded && editing.role_keys.length > 0) {
    return "角色清单尚未加载完成,请稍后重试";
  }
  return null;
}

/** 重置密码与删除用户的 mutation;成功后关闭对应弹层并刷新列表。 */
function useUserDangerActions(invalidate: () => void) {
  const [resetting, setResetting] = useState<UserItemOut | null>(null);
  const [resetPw, setResetPw] = useState("");
  const [resetError, setResetError] = useState("");
  const [deleting, setDeleting] = useState<UserItemOut | null>(null);

  const reset = useMutation({
    mutationFn: ({ id, password }: { id: string; password: string }) =>
      systemApi.resetPassword(id, password),
    onSuccess: () => {
      setResetting(null);
      setResetPw("");
    },
    onError: (e: Error) => setResetError(e.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => systemApi.deleteUser(id),
    onSuccess: () => {
      setDeleting(null);
      invalidate();
    },
  });

  /** 打开重置密码框并清空上一次的输入与错误。 */
  const openReset = (u: UserItemOut) => {
    setResetError("");
    setResetPw("");
    setResetting(u);
  };

  const submitReset = () => {
    if (resetting) reset.mutate({ id: resetting.id, password: resetPw });
  };
  const confirmRemove = () => {
    if (deleting) remove.mutate(deleting.id);
  };

  return {
    openReset,
    submitReset,
    resetUsername: resetting?.username ?? null,
    deletingName: deleting?.username ?? null,
    confirmRemove,
    closeReset: () => setResetting(null),
    closeDelete: () => setDeleting(null),
    resetting,
    setResetting,
    resetPw,
    setResetPw,
    resetError,
    setResetError,
    deleting,
    setDeleting,
    reset,
    remove,
  };
}

/** 新建/编辑表单的开关与提交;提交前做跨字段校验。 */
function useFormActions(src: {
  creating: boolean;
  editing: UserItemOut | null;
  form: UserForm;
  setForm: (f: UserForm) => void;
  setFormError: (e: string) => void;
  setCreating: (v: boolean) => void;
  setEditing: (u: UserItemOut | null) => void;
  tenantOptions: { id: string }[];
  roleOptions: { id: string; role_key: string }[] | undefined;
  create: { mutate: () => void };
  update: { mutate: () => void };
}) {
  // 组织成对象便于按快照取值,避免逐项解构
  const s = src;
  const openCreate = () => {
    s.setForm({ ...EMPTY_FORM, tenantId: s.tenantOptions[0]?.id ?? "" });
    s.setFormError("");
    s.setCreating(true);
  };
  const openEdit = (u: UserItemOut) => {
    const roleIds = (s.roleOptions ?? [])
      .filter((r) => u.role_keys.includes(r.role_key))
      .map((r) => r.id);
    s.setForm({
      username: u.username,
      password: "",
      nickname: u.nickname,
      email: u.email ?? "",
      status: u.status === "DISABLED" ? "DISABLED" : "ACTIVE",
      deptId: u.dept_id ?? "",
      tenantId: u.tenant_id ?? s.tenantOptions[0]?.id ?? "",
      roleIds,
    });
    s.setFormError("");
    s.setEditing(u);
  };
  /** 前端预校验;通过后按新建/编辑分派 mutation。 */
  const submit = () => {
    s.setFormError("");
    const invalid = validateUserForm({
      form: s.form,
      creating: s.creating,
      editing: s.editing,
      rolesLoaded: s.roleOptions !== undefined,
    });
    if (invalid) return s.setFormError(invalid);
    (s.creating ? s.create : s.update).mutate();
  };
  const closeForm = () => {
    s.setCreating(false);
    s.setEditing(null);
  };
  return { openCreate, openEdit, submit, closeForm };
}

/** 删除 mutation 的错误文案;未失败时为 null。 */
export function removeError(m: { isError: boolean; error: Error | null }): string | null {
  return m.isError ? (m.error?.message ?? "删除失败") : null;
}
