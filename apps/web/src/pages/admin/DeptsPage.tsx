/* 管理部门树；选择父部门时排除自身和后代，删除失败时显示服务端错误。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/page-header";
import { EmptyState } from "@/components/empty-state";
import { ErrorAlert } from "@/components/error-alert";
import { systemApi, type DeptNodeOut } from "@/lib/system-api";

import { DeleteDeptDialog, DeptFormDialog, type DeptForm } from "./dept-dialogs";
import { EMPTY_DEPT_FORM, deptFormError, parentCandidatesOf } from "./dept-form-logic";
import { TreeRow, type DeptRowActions } from "./dept-tree-row";

/** 树行的三个入口如何改写表单状态。 */
interface RowHandlers {
  startEdit: (n: DeptNodeOut) => void;
  startCreateChild: (n: DeptNodeOut) => void;
  startDelete: (n: DeptNodeOut) => void;
}

export default function DeptsPage() {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<DeptNodeOut | null>(null);
  const [form, setForm] = useState<DeptForm>(EMPTY_DEPT_FORM);
  const [formError, setFormError] = useState("");
  const [deleting, setDeleting] = useState<DeptNodeOut | null>(null);
  const [deleteError, setDeleteError] = useState("");

  const depts = useQuery({ queryKey: ["admin", "depts"], queryFn: systemApi.listDepts });
  const tenants = useQuery({ queryKey: ["admin", "tenants"], queryFn: systemApi.listTenants });
  const defaultTenant = tenants.data?.[0]?.id ?? "";

  const invalidate = () => void qc.invalidateQueries({ queryKey: ["admin", "depts"] });

  const create = useMutation({
    mutationFn: () =>
      systemApi.createDept({
        parent_id: form.parentId || null,
        dept_name: form.deptName,
        // 子部门随父节点租户(避免多租户下静默挂错);根部门才落缺省租户
        tenant_id: form.tenantId || defaultTenant,
        sort: form.sort,
      }),
    onSuccess: () => {
      setCreating(false);
      invalidate();
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const update = useMutation({
    mutationFn: () =>
      systemApi.updateDept(editing!.id, {
        parent_id: form.parentId || null,
        dept_name: form.deptName,
        tenant_id: editing!.tenant_id,
        sort: form.sort,
        status: editing!.status === "DISABLED" ? "DISABLED" : "ACTIVE",
      }),
    onSuccess: () => {
      setEditing(null);
      invalidate();
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const remove = useMutation({
    mutationFn: () => systemApi.deleteDept(deleting!.id),
    onSuccess: () => {
      setDeleting(null);
      invalidate();
    },
    onError: (e: Error) => setDeleteError(e.message),
  });

  const handlers = makeRowHandlers({
    setForm,
    setFormError,
    setEditing,
    setCreating,
    setDeleteError,
    setDeleting,
  });
  const rowActions: DeptRowActions = {
    onEdit: handlers.startEdit,
    onDelete: handlers.startDelete,
    onAddChild: handlers.startCreateChild,
  };

  const submit = () => {
    setFormError("");
    const error = deptFormError(form);
    if (error) return setFormError(error);
    if (creating) create.mutate();
    else update.mutate();
  };

  /** 新建入口:在根层级开一张空表单。 */
  const startCreateRoot = () => {
    setForm(EMPTY_DEPT_FORM);
    setFormError("");
    setCreating(true);
  };

  return (
    <div className="space-y-3">
      <PageHeader
        title="部门"
        description="部门用于划定数据可见范围(如「本部门」「本部门及以下」)。"
        actions={
          <Button size="sm" onClick={startCreateRoot}>
            新建部门
          </Button>
        }
      />

      {depts.isError && <ErrorAlert text={`部门树加载失败:${(depts.error as Error).message}`} />}

      <div className="rounded-md border">
        {depts.isPending && (
          <p className="p-4 text-center text-sm text-muted-foreground">加载中…</p>
        )}
        {depts.data?.length === 0 && <EmptyState>暂无部门,点击右上角「新建部门」开始。</EmptyState>}
        {(depts.data ?? []).map((n) => (
          <TreeRow key={n.id} node={n} depth={0} actions={rowActions} />
        ))}
      </div>

      <DeptFormDialog
        open={creating || editing !== null}
        creating={creating}
        editing={editing}
        form={form}
        setForm={setForm}
        formError={formError}
        parentCandidates={parentCandidatesOf(depts.data, editing)}
        saving={create.isPending || update.isPending}
        onClose={() => {
          setCreating(false);
          setEditing(null);
        }}
        onSubmit={submit}
      />

      <DeleteDeptDialog
        deleting={deleting}
        onClose={() => setDeleting(null)}
        pending={remove.isPending}
        error={deleteError}
        onConfirm={() => remove.mutate()}
      />
    </div>
  );
}

/** 行操作到表单状态的映射:编辑回填节点、加子级预置父节点、删除前清错误。 */
function makeRowHandlers(h: {
  setForm: (f: DeptForm) => void;
  setFormError: (e: string) => void;
  setEditing: (n: DeptNodeOut | null) => void;
  setCreating: (b: boolean) => void;
  setDeleteError: (e: string) => void;
  setDeleting: (n: DeptNodeOut | null) => void;
}): RowHandlers {
  const { setForm, setFormError, setEditing, setCreating, setDeleteError, setDeleting } = h;
  return {
    startEdit: (node) => {
      setForm({
        parentId: node.parent_id ?? "",
        deptName: node.dept_name,
        sort: node.sort,
        tenantId: node.tenant_id,
      });
      setFormError("");
      setEditing(node);
    },
    startCreateChild: (node) => {
      setForm({ parentId: node.id, deptName: "", sort: 0, tenantId: node.tenant_id });
      setFormError("");
      setCreating(true);
    },
    startDelete: (node) => {
      setDeleteError("");
      setDeleting(node);
    },
  };
}
