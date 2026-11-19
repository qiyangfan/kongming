/* 角色新建/编辑对话框:键、名称、数据范围(含自定义部门)与 SQL 范围。 */

import type { UseQueryResult } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { CheckList } from "@/components/check-list";
import { FormRow } from "@/components/form-row";
import { ErrorAlert } from "@/components/error-alert";
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
import type { DeptNodeOut, DataScopeValue, SqlScopeValue } from "@/lib/system-api";

import { DATA_SCOPES, SQL_SCOPES, type RoleForm } from "./role-logic";

interface DeptOption {
  id: string;
  label: string;
  title: string;
}

interface RoleFormDialogProps {
  open: boolean;
  creating: boolean;
  form: RoleForm;
  setForm: (form: RoleForm) => void;
  formError: string;
  depts: UseQueryResult<DeptNodeOut[]>;
  deptOptions: DeptOption[];
  saving: boolean;
  onClose: () => void;
  onSubmit: () => void;
}

/** 两个范围下拉并排。 */
function ScopeSelects({ form, setForm }: { form: RoleForm; setForm: (form: RoleForm) => void }) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <FormRow label="数据范围">
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
      </FormRow>
      <FormRow label="SQL 范围">
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
      </FormRow>
    </div>
  );
}

/**
 * 自定义部门选择:候选读不出来 ≠ 没有部门,空态会把人支去新建,真实成因常是缺
 * 读部门权限(部门列表挂在 system:user:manage 下)。错误态按 web-nav spec 走
 * 共享件 ErrorAlert,重试钮在其外(该件只收 text 单参)。
 */
function CustomDeptField({
  form,
  setForm,
  depts,
  deptOptions,
}: Pick<RoleFormDialogProps, "form" | "setForm" | "depts" | "deptOptions">) {
  return (
    <FormRow label="自定义部门" htmlFor="r-depts">
      {depts.isPending ? (
        <p className="text-muted-foreground text-sm">部门加载中…</p>
      ) : depts.isError ? (
        <div className="space-y-1">
          <ErrorAlert
            text={`部门列表加载失败:${(depts.error as Error).message}。已配置的部门集合保持不变(仍可保存);要增删部门请重试,或改用具备「用户与部门」权限的账号。`}
          />
          <Button
            type="button"
            variant="link"
            size="xs"
            className="px-0"
            onClick={() => void depts.refetch()}
          >
            重试
          </Button>
        </div>
      ) : (
        <CheckList
          options={deptOptions}
          selected={form.deptIds}
          onChange={(ids) => setForm({ ...form, deptIds: ids })}
          emptyText="暂无部门,请先到「部门」页新建"
        />
      )}
      <p className="text-muted-foreground text-xs">
        只放行所选部门的数据,不含其下级部门(要含下级请选「本部门及以下」)。
      </p>
    </FormRow>
  );
}

/** 角色新建/编辑对话框。 */
export function RoleFormDialog(props: RoleFormDialogProps) {
  const { form, setForm } = props;
  return (
    <Dialog open={props.open} onOpenChange={(open) => !open && props.onClose()}>
      <DialogContent aria-describedby={undefined} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{props.creating ? "新建角色" : `编辑角色 ${form.roleKey}`}</DialogTitle>
        </DialogHeader>
        {props.formError && (
          <p className="text-destructive text-sm" role="alert">
            {props.formError}
          </p>
        )}
        <form
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            props.onSubmit();
          }}
        >
          <FormRow label="角色键" htmlFor="r-key">
            <Input
              id="r-key"
              value={form.roleKey}
              disabled={!props.creating}
              onChange={(e) => setForm({ ...form, roleKey: e.target.value })}
            />
            {!props.creating && (
              <p className="text-muted-foreground text-xs">角色键创建后不可修改</p>
            )}
          </FormRow>
          <FormRow label="名称" htmlFor="r-name">
            <Input
              id="r-name"
              value={form.roleName}
              onChange={(e) => setForm({ ...form, roleName: e.target.value })}
            />
          </FormRow>
          <ScopeSelects form={form} setForm={setForm} />
          {form.dataScope === "CUSTOM" && (
            <CustomDeptField
              form={form}
              setForm={setForm}
              depts={props.depts}
              deptOptions={props.deptOptions}
            />
          )}
          <FormRow label="排序" htmlFor="r-sort">
            <Input
              id="r-sort"
              type="number"
              value={form.sort}
              onChange={(e) => setForm({ ...form, sort: Number(e.target.value) || 0 })}
            />
          </FormRow>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={props.onClose}>
              取消
            </Button>
            <Button type="submit" disabled={props.saving}>
              {props.saving ? "保存中…" : "保存"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
