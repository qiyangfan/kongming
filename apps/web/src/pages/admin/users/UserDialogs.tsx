/* 用户页弹层:编辑授权资料与删除确认。 */

import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FormRow } from "@/components/form-row";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { RoleOut, TenantOut } from "@/lib/system-api";

import type { UserForm } from "./use-users-page-state";

type Option = { id: string; label: string };

export function UserFormDialog({
  open,
  creating,
  form,
  onFormChange,
  formError,
  tenantOptions,
  deptOptions,
  roles,
  pending,
  onSubmit,
  onClose,
}: {
  open: boolean;
  creating: boolean;
  form: UserForm;
  onFormChange: (form: UserForm) => void;
  formError: string;
  tenantOptions: TenantOut[];
  deptOptions: Option[];
  roles: RoleOut[];
  pending: boolean;
  onSubmit: () => void;
  onClose: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
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
            onSubmit();
          }}
        >
          {creating && (
            <FormRow label="用户名" htmlFor="u-username">
              <Input
                id="u-username"
                value={form.username}
                onChange={(e) => onFormChange({ ...form, username: e.target.value })}
              />
            </FormRow>
          )}
          <FormRow label="昵称" htmlFor="u-nickname">
            <Input
              id="u-nickname"
              value={form.nickname}
              onChange={(e) => onFormChange({ ...form, nickname: e.target.value })}
            />
          </FormRow>
          <FormRow label="邮箱(可空)" htmlFor="u-email">
            <Input
              id="u-email"
              value={form.email}
              onChange={(e) => onFormChange({ ...form, email: e.target.value })}
            />
          </FormRow>
          <div className="grid grid-cols-2 gap-3">
            <FormRow label="状态">
              <Select
                value={form.status}
                onValueChange={(v) => onFormChange({ ...form, status: v as UserForm["status"] })}
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
                onValueChange={(v) => onFormChange({ ...form, tenantId: v })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择租户" />
                </SelectTrigger>
                <SelectContent>
                  {tenantOptions.map((t) => (
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
              onValueChange={(v) => onFormChange({ ...form, deptId: v === "none" ? "" : v })}
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
            <RoleChecklist form={form} onFormChange={onFormChange} roles={roles} />
          </FormRow>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? "保存中…" : "保存"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function DeleteUserDialog({
  username,
  error,
  pending,
  onConfirm,
  onClose,
}: {
  username: string | null;
  error: string | null;
  pending: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <AlertDialog open={username !== null} onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除用户 {username ?? ""}?</AlertDialogTitle>
          <AlertDialogDescription>
            删除为软删除:该用户无法再登录,用户名可被新用户复用。此操作可由管理员感知但不可自助恢复。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && (
          <p className="text-destructive text-sm" role="alert">
            {error}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          {/* 受控关闭:mutation 成功才关对话框(onSuccess setDeleting(null)),
              失败保持开启展示上方错误——Radix Action 点击即关,错误行会随卸载不可见 */}
          <Button variant="destructive" onClick={onConfirm} disabled={pending}>
            {pending ? "删除中…" : "删除"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** 角色多选清单;勾选/取消时在 form.roleIds 中增删对应角色 id。 */
function RoleChecklist({
  form,
  onFormChange,
  roles,
}: {
  form: UserForm;
  onFormChange: (form: UserForm) => void;
  roles: RoleOut[];
}) {
  return (
    <div className="space-y-1.5 rounded-md border p-2">
      {roles.map((r) => (
        <label key={r.id} className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={form.roleIds.includes(r.id)}
            onCheckedChange={(checked) =>
              onFormChange({
                ...form,
                roleIds: checked ? [...form.roleIds, r.id] : form.roleIds.filter((x) => x !== r.id),
              })
            }
          />
          {r.role_name}({r.role_key})
        </label>
      ))}
    </div>
  );
}
