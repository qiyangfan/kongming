/* 管理角色的数据范围、SQL 范围和权限绑定；内置角色不可删除。 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/error-alert";
import { PageHeader } from "@/components/page-header";
import { flattenDeptTree } from "@/lib/dept-tree";
import { deptIdsForScope, deptScopeError } from "@/lib/role-scope";
import { systemApi, type RoleOut } from "@/lib/system-api";

import { RoleFormDialog } from "./role-form-dialog";
import {
  EMPTY_ROLE_FORM,
  roleFormError,
  rolePayload,
  roleToForm,
  type RoleForm,
} from "./role-logic";
import { RolePermBindDialog } from "./role-perm-bind-dialog";
import { DeleteRoleDialog, RoleTable } from "./role-table";

export default function RolesPage() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<RoleOut | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<RoleForm>(EMPTY_ROLE_FORM);
  const [formError, setFormError] = useState("");
  const [binding, setBinding] = useState<RoleOut | null>(null);
  const [boundIds, setBoundIds] = useState<string[]>([]);
  const [bindError, setBindError] = useState("");
  const [deleting, setDeleting] = useState<RoleOut | null>(null);
  const [deleteError, setDeleteError] = useState("");

  const roles = useQuery({ queryKey: ["admin", "roles"], queryFn: systemApi.listRoles });
  const perms = useQuery({ queryKey: ["admin", "perms"], queryFn: systemApi.listPerms });
  // 部门候选与部门管理页同源端点(/system/depts),不另开数据面
  const depts = useQuery({ queryKey: ["admin", "depts"], queryFn: systemApi.listDepts });
  const deptOptions = useMemo(
    () =>
      flattenDeptTree(depts.data).map((d) => ({
        id: d.id,
        label: `${"　".repeat(d.depth)}${d.label}`,
        // 缩进只服务视觉层级,悬浮回读必须给原始部门名
        title: d.label,
      })),
    [depts.data],
  );

  const invalidateRoles = () => void qc.invalidateQueries({ queryKey: ["admin", "roles"] });

  /** 新建与编辑共用同一份表单负载。 */
  const roleBody = () => rolePayload(form, deptIdsForScope);

  const create = useMutation({
    mutationFn: () => systemApi.createRole(roleBody()),
    onSuccess: () => {
      setCreating(false);
      invalidateRoles();
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const update = useMutation({
    mutationFn: () => systemApi.updateRole(editing!.id, roleBody()),
    onSuccess: () => {
      setEditing(null);
      invalidateRoles();
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const bind = useMutation({
    mutationFn: () => systemApi.bindRolePerms(binding!.id, boundIds),
    onSuccess: () => {
      setBinding(null);
      invalidateRoles();
    },
    onError: (e: Error) => setBindError(e.message),
  });

  const remove = useMutation({
    mutationFn: () => systemApi.deleteRole(deleting!.id),
    onSuccess: () => {
      setDeleting(null);
      invalidateRoles();
    },
    onError: (e: Error) => setDeleteError(e.message),
  });

  const submit = () => {
    setFormError("");
    const error = roleFormError(form, creating, deptScopeError);
    if (error) return setFormError(error);
    if (creating) create.mutate();
    else update.mutate();
  };

  return (
    <div className="space-y-3">
      <PageHeader
        title="角色"
        description="多角色用户:权限点取并集,数据范围取最宽松。"
        actions={
          <Button
            size="sm"
            onClick={() => {
              setForm(EMPTY_ROLE_FORM);
              setFormError("");
              setCreating(true);
            }}
          >
            新建角色
          </Button>
        }
      />

      {roles.isError && <ErrorAlert text={`角色列表加载失败:${(roles.error as Error).message}`} />}

      <RoleTable
        rows={roles.data ?? []}
        isPending={roles.isPending}
        onEdit={(r) => {
          setForm(roleToForm(r));
          setFormError("");
          setEditing(r);
        }}
        onBind={(r) => {
          setBoundIds(r.perm_ids);
          setBindError("");
          setBinding(r);
        }}
        onDelete={(r) => {
          setDeleteError("");
          setDeleting(r);
        }}
      />

      <RoleFormDialog
        open={creating || editing !== null}
        creating={creating}
        form={form}
        setForm={setForm}
        formError={formError}
        depts={depts}
        deptOptions={deptOptions}
        saving={create.isPending || update.isPending}
        onClose={() => (setCreating(false), setEditing(null))}
        onSubmit={submit}
      />

      <RolePermBindDialog
        binding={binding}
        perms={perms.data ?? []}
        boundIds={boundIds}
        setBoundIds={setBoundIds}
        bindError={bindError}
        pending={bind.isPending}
        onClose={() => setBinding(null)}
        onSave={() => bind.mutate()}
      />

      <DeleteRoleDialog
        deleting={deleting}
        onClose={() => setDeleting(null)}
        pending={remove.isPending}
        error={deleteError}
        onConfirm={() => remove.mutate()}
      />
    </div>
  );
}
