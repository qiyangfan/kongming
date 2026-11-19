/* 计费页的确认与编辑弹窗:套餐编辑、套餐/策略删除确认、账单作废确认。 */

import { Loader2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { FormRow } from "@/components/form-row";
import type { PlanItem } from "@/lib/billing-api";
import type { PlanForm } from "./billing-page-model";
import type { useBillingMutations } from "./billing-page-queries";

type BillingMutations = ReturnType<typeof useBillingMutations>;

// 套餐编辑对话框
function PlanEditDialog({
  plan,
  form,
  onFormChange,
  error,
  pending,
  onClose,
  onSubmit,
}: {
  plan: PlanItem | null;
  form: PlanForm;
  onFormChange: (f: PlanForm) => void;
  error: string;
  pending: boolean;
  onClose: () => void;
  onSubmit: () => void;
}) {
  return (
    <Dialog open={plan !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>编辑套餐 {plan?.plan_key}</DialogTitle>
        </DialogHeader>
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            onSubmit();
          }}
        >
          <FormRow label="名称">
            <Input
              value={form.name}
              onChange={(e) => onFormChange({ ...form, name: e.target.value })}
              aria-label="套餐名称"
            />
          </FormRow>
          <div className="grid grid-cols-2 gap-2">
            <FormRow label="月 Token(0 不限)">
              <Input
                value={form.monthly_tokens}
                onChange={(e) => onFormChange({ ...form, monthly_tokens: e.target.value })}
                aria-label="月 Token"
              />
            </FormRow>
            <FormRow label="月调用(0 不限)">
              <Input
                value={form.monthly_calls}
                onChange={(e) => onFormChange({ ...form, monthly_calls: e.target.value })}
                aria-label="月调用"
              />
            </FormRow>
            <FormRow label="千 Token 单价(元)">
              <Input
                value={form.price_per_mtok}
                onChange={(e) => onFormChange({ ...form, price_per_mtok: e.target.value })}
                aria-label="千 Token 单价"
              />
            </FormRow>
            <FormRow label="每次调用单价(元)">
              <Input
                value={form.price_per_call}
                onChange={(e) => onFormChange({ ...form, price_per_call: e.target.value })}
                aria-label="次单价"
              />
            </FormRow>
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            改价只对之后的账单生效;已出账单按出账时的价格结算。
          </p>
          <Button type="submit" disabled={pending}>
            {pending && <Loader2 className="animate-spin" aria-hidden />}
            保存
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// 套餐删除确认:请求进行中禁止关闭
function PlanDeleteDialog({
  target,
  error,
  pending,
  onClose,
  onConfirm,
}: {
  target: PlanItem | null;
  error: string;
  pending: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog
      open={target !== null}
      onOpenChange={(o) => {
        if (!o && !pending) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除套餐 {target?.plan_key}?</AlertDialogTitle>
          <AlertDialogDescription>
            绑定中的套餐不可删除;删除后新租户不可再绑定,既有账单不受影响。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>再想想</AlertDialogCancel>
          <AlertDialogAction
            disabled={pending}
            onClick={(e) => {
              e.preventDefault();
              onConfirm();
            }}
          >
            {pending && <Loader2 className="animate-spin" aria-hidden />}
            确认删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// 配置类破坏性确认:标题/说明/执行按钮文案由调用方给出
function ConfirmDialog({
  open,
  title,
  description,
  confirmText,
  pending,
  error,
  onClose,
  onConfirm,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmText: string;
  pending: boolean;
  error?: string;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog
      open={open}
      onOpenChange={(o) => {
        if (!o && !pending) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>再想想</AlertDialogCancel>
          <AlertDialogAction
            disabled={pending}
            onClick={(e) => {
              e.preventDefault();
              onConfirm();
            }}
          >
            {pending && <Loader2 className="animate-spin" aria-hidden />}
            {confirmText}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function PolicyDeleteDialog({
  open,
  pending,
  onClose,
  onConfirm,
}: {
  open: boolean;
  pending: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <ConfirmDialog
      open={open}
      title="删除该配额策略?"
      description="删除后该维度不再限量(此操作会记入审计日志)。"
      confirmText="确认删除"
      pending={pending}
      onClose={onClose}
      onConfirm={onConfirm}
    />
  );
}

function VoidInvoiceDialog({
  open,
  error,
  pending,
  onClose,
  onConfirm,
}: {
  open: boolean;
  error: string;
  pending: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <ConfirmDialog
      open={open}
      title="作废该账单?"
      description="作废后不可再支付(在途支付单将连带取消);已支付账单不可作废。"
      confirmText="确认作废"
      pending={pending}
      error={error}
      onClose={onClose}
      onConfirm={onConfirm}
    />
  );
}

type Toggle<T> = { value: T | null; close: () => void };

// 计费页四个弹窗的集装:关闭只改开关状态,提交在当前弹窗目标非空时发起
export function BillingDialogs({
  m,
  edit,
  deletePlan,
  deletePolicyId,
  voidError,
  voidTarget,
  closeDeletePolicy,
  closeVoid,
}: {
  m: BillingMutations;
  edit: {
    plan: PlanItem | null;
    form: PlanForm;
    onChange: (f: PlanForm) => void;
    error: string;
    close: () => void;
  };
  deletePlan: { target: PlanItem | null; error: string; close: () => void };
  deletePolicyId: string | null;
  voidError: string;
  voidTarget: string | null;
  closeDeletePolicy: () => void;
  closeVoid: () => void;
}) {
  const confirmWith =
    <T,>(t: Toggle<T>, run: (v: T) => void) =>
    () => {
      if (t.value !== null) run(t.value);
    };
  return (
    <>
      <PlanEditDialog
        plan={edit.plan}
        form={edit.form}
        onFormChange={edit.onChange}
        error={edit.error}
        pending={m.update.isPending}
        onClose={edit.close}
        onSubmit={() => m.update.mutate()}
      />
      <PlanDeleteDialog
        target={deletePlan.target}
        error={deletePlan.error}
        pending={m.removePlan.isPending}
        onClose={deletePlan.close}
        onConfirm={confirmWith({ value: deletePlan.target, close: deletePlan.close }, (p) =>
          m.removePlan.mutate(p.id),
        )}
      />
      <PolicyDeleteDialog
        open={deletePolicyId !== null}
        pending={m.removePolicy.isPending}
        onClose={closeDeletePolicy}
        onConfirm={confirmWith({ value: deletePolicyId, close: closeDeletePolicy }, (id) =>
          m.removePolicy.mutate(id),
        )}
      />
      <VoidInvoiceDialog
        open={voidTarget !== null}
        error={voidError}
        pending={m.voidMut.isPending}
        onClose={closeVoid}
        onConfirm={confirmWith({ value: voidTarget, close: closeVoid }, (id) =>
          m.voidMut.mutate(id),
        )}
      />
    </>
  );
}
