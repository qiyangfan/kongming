/* 部门管理页(web-admin spec「部门管理页」):树形展示+创建/编辑
   (父部门选择防自身/后代)+删除确认(端点前置校验兜底,失败展示错误)。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";

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
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { FormRow } from "@/components/form-row";
import {
  Dialog,
  DialogContent,
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
import { systemApi, type DeptNodeOut } from "@/lib/system-api";

type DeptForm = {
  parentId: string; // "" = 根
  deptName: string;
  sort: number;
  tenantId: string; // 创建子部门时随父节点;根部门缺省(提交时回落清单首项)
};

const EMPTY: DeptForm = { parentId: "", deptName: "", sort: 0, tenantId: "" };

/* 树形缩进用预定义类数组(禁内联 style;深度超限取模兜底) */
const DEPTH_PADS = ["pl-2", "pl-8", "pl-14", "pl-20", "pl-26", "pl-32"];

function flatten(
  nodes: DeptNodeOut[] | undefined,
  depth = 0,
): { id: string; label: string; depth: number }[] {
  return (nodes ?? []).flatMap((n) => [
    { id: n.id, label: n.dept_name, depth },
    ...flatten(n.children, depth + 1),
  ]);
}

function TreeRow({
  node,
  depth,
  onEdit,
  onDelete,
  onAddChild,
}: {
  node: DeptNodeOut;
  depth: number;
  onEdit: (n: DeptNodeOut) => void;
  onDelete: (n: DeptNodeOut) => void;
  onAddChild: (n: DeptNodeOut) => void;
}) {
  const kids = node.children ?? [];
  const hasChildren = kids.length > 0;
  const row = (
    <div
      className={`flex items-center gap-2 rounded-md py-1.5 pr-2 hover:bg-secondary/40 ${
        DEPTH_PADS[depth % DEPTH_PADS.length]
      }`}
    >
      {hasChildren ? (
        <CollapsibleTrigger className="text-muted-foreground hover:text-foreground">
          <ChevronRight
            className="size-4 transition-transform [[data-state=open]>&]:rotate-90"
            aria-hidden
          />
        </CollapsibleTrigger>
      ) : (
        <span className="size-4" />
      )}
      <span className="flex-1 text-sm font-medium">{node.dept_name}</span>
      {node.status === "DISABLED" && <Badge variant="secondary">停用</Badge>}
      <span className="text-muted-foreground text-xs">#{node.sort}</span>
      <Button variant="ghost" size="sm" onClick={() => onAddChild(node)}>
        加子级
      </Button>
      <Button variant="ghost" size="sm" onClick={() => onEdit(node)}>
        编辑
      </Button>
      <Button variant="ghost-destructive" size="sm" onClick={() => onDelete(node)}>
        删除
      </Button>
    </div>
  );
  if (!hasChildren) return row;
  return (
    <Collapsible defaultOpen>
      {row}
      <CollapsibleContent>
        {kids.map((c) => (
          <TreeRow
            key={c.id}
            node={c}
            depth={depth + 1}
            onEdit={onEdit}
            onDelete={onDelete}
            onAddChild={onAddChild}
          />
        ))}
      </CollapsibleContent>
    </Collapsible>
  );
}

export default function DeptsPage() {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<DeptNodeOut | null>(null);
  const [form, setForm] = useState<DeptForm>(EMPTY);
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

  // 父部门候选:编辑时排除自身与后代(端点亦校验,前端先行避免明显环)
  const parentCandidates = (() => {
    const all = flatten(depts.data ?? []);
    if (!editing) return all;
    const excluded = new Set<string>([editing.id]);
    const collect = (nodes?: DeptNodeOut[]) =>
      (nodes ?? []).forEach((n) => {
        excluded.add(n.id);
        collect(n.children);
      });
    collect(editing.children);
    return all.filter((d) => !excluded.has(d.id));
  })();

  const submit = () => {
    setFormError("");
    if (!form.deptName.trim()) return setFormError("部门名称不可为空");
    if (creating) create.mutate();
    else update.mutate();
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          部门用于划定数据可见范围(如「本部门」「本部门及以下」)。
        </p>
        <Button
          size="sm"
          onClick={() => {
            setForm(EMPTY);
            setFormError("");
            setCreating(true);
          }}
        >
          新建部门
        </Button>
      </div>

      {depts.isError && (
        <p className="text-destructive text-sm" role="alert">
          部门树加载失败:{(depts.error as Error).message}
        </p>
      )}

      <div className="rounded-md border">
        {depts.isPending && (
          <p className="p-4 text-center text-sm text-muted-foreground">加载中…</p>
        )}
        {depts.data?.length === 0 && (
          <p className="p-4 text-center text-sm text-muted-foreground">
            暂无部门,点击右上角「新建部门」开始。
          </p>
        )}
        {(depts.data ?? []).map((n) => (
          <TreeRow
            key={n.id}
            node={n}
            depth={0}
            onEdit={(node) => {
              setForm({
                parentId: node.parent_id ?? "",
                deptName: node.dept_name,
                sort: node.sort,
                tenantId: node.tenant_id,
              });
              setFormError("");
              setEditing(node);
            }}
            onDelete={(node) => {
              setDeleteError("");
              setDeleting(node);
            }}
            onAddChild={(node) => {
              setForm({ parentId: node.id, deptName: "", sort: 0, tenantId: node.tenant_id });
              setFormError("");
              setCreating(true);
            }}
          />
        ))}
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
        <DialogContent aria-describedby={undefined} className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{creating ? "新建部门" : `编辑部门 ${editing?.dept_name}`}</DialogTitle>
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
            <FormRow label="名称" htmlFor="d-name">
              <Input
                id="d-name"
                value={form.deptName}
                onChange={(e) => setForm({ ...form, deptName: e.target.value })}
              />
            </FormRow>
            <FormRow label="上级部门">
              <Select
                value={form.parentId || "root"}
                onValueChange={(v) => setForm({ ...form, parentId: v === "root" ? "" : v })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="root">(根部门)</SelectItem>
                  {parentCandidates.map((d) => (
                    <SelectItem key={d.id} value={d.id}>
                      {"　".repeat(d.depth)}
                      {d.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormRow>
            <FormRow label="排序" htmlFor="d-sort">
              <Input
                id="d-sort"
                type="number"
                value={form.sort}
                onChange={(e) => setForm({ ...form, sort: Number(e.target.value) || 0 })}
              />
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

      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除部门 {deleting?.dept_name}?</AlertDialogTitle>
            <AlertDialogDescription>
              仍有子部门或有账号在使用的部门无法删除。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deleteError && (
            <p className="text-destructive text-sm" role="alert">
              {deleteError}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            {/* 受控关闭:成功才关,失败保持开启展示上方错误(Radix Action 点击即关) */}
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
