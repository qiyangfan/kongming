/* 部门页的编辑/新建对话框与删除确认对话框。 */

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@kongming/ui/alert-dialog";
import { Button } from "@kongming/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@kongming/ui/dialog";
import { Input } from "@kongming/ui/input";
import { FormRow } from "@kongming/ui/form-row";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import type { DeptNodeOut } from "@kongming/api-client/system-api";

export type DeptForm = {
  parentId: string; // "" = 根
  deptName: string;
  sort: number;
  tenantId: string; // 创建子部门时随父节点;根部门缺省(提交时回落清单首项)
};

interface DeptFormDialogProps {
  open: boolean;
  creating: boolean;
  editing: DeptNodeOut | null;
  form: DeptForm;
  setForm: (form: DeptForm) => void;
  formError: string;
  parentCandidates: { id: string; label: string; depth: number }[];
  saving: boolean;
  onClose: () => void;
  onSubmit: () => void;
}

/** 新建/编辑部门对话框;父部门候选已排除自身与后代。 */
export function DeptFormDialog(props: DeptFormDialogProps) {
  const { form, setForm, editing } = props;
  return (
    <Dialog open={props.open} onOpenChange={(open) => !open && props.onClose()}>
      <DialogContent aria-describedby={undefined} className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>
            {props.creating ? "新建部门" : `编辑部门 ${editing?.dept_name}`}
          </DialogTitle>
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
                {props.parentCandidates.map((d) => (
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

interface DeleteDeptDialogProps {
  deleting: DeptNodeOut | null;
  onClose: () => void;
  pending: boolean;
  error: string;
  onConfirm: () => void;
}

/** 删除部门确认:成功才关,失败保持开启展示上方错误(Radix Action 点击即关)。 */
export function DeleteDeptDialog(props: DeleteDeptDialogProps) {
  return (
    <AlertDialog open={props.deleting !== null} onOpenChange={(open) => !open && props.onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除部门 {props.deleting?.dept_name}?</AlertDialogTitle>
          <AlertDialogDescription>仍有子部门或有账号在使用的部门无法删除。</AlertDialogDescription>
        </AlertDialogHeader>
        {props.error && (
          <p className="text-destructive text-sm" role="alert">
            {props.error}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <Button variant="destructive" onClick={props.onConfirm} disabled={props.pending}>
            {props.pending ? "删除中…" : "删除"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
