/* 触发器新建/编辑弹窗骨架:组合标识、目标与各形态配置区块,并展示保存校验。 */

import { Loader2 } from "lucide-react";

import { Button } from "@kongming/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@kongming/ui/dialog";
import { Input } from "@kongming/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import { FormRow } from "@kongming/ui/form-row";
import {
  emailFormInvalid,
  TYPE_LABELS,
  type FormState,
  type TriggerType,
} from "./triggers-page-model";
import {
  CronFields,
  switchTargetType,
  TargetSelect,
  TaskFields,
  type TriggersFormDialogProps,
} from "./triggers-page-fields";
import { EmailFields, WebhookFields } from "./triggers-page-email-fields";

// 已填数据、校验通过且未在保存中才允许提交
function submitDisabled(form: FormState, savePending: boolean, retriesInvalid: boolean): boolean {
  if (savePending || !form.targetKey || retriesInvalid) return true;
  return form.type === "EMAIL" && emailFormInvalid(form);
}

function IdentityFields({ form, onChange }: { form: FormState; onChange: (f: FormState) => void }) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <FormRow label="标识" htmlFor="tg-key">
        <Input
          id="tg-key"
          value={form.tgKey}
          disabled={form.id !== null}
          onChange={(e) => onChange({ ...form, tgKey: e.target.value })}
          className="font-mono"
        />
      </FormRow>
      <FormRow label="名称" htmlFor="tg-name">
        <Input
          id="tg-name"
          value={form.name}
          onChange={(e) => onChange({ ...form, name: e.target.value })}
        />
      </FormRow>
    </div>
  );
}

function TypeAndTargetFields(props: TriggersFormDialogProps & { form: FormState }) {
  const { form, onChange } = props;
  return (
    <div className="grid grid-cols-2 gap-3">
      {form.id === null && (
        <FormRow label="类型">
          <Select
            value={form.type}
            onValueChange={(v) => onChange({ ...form, type: v as TriggerType })}
          >
            <SelectTrigger aria-label="类型">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(TYPE_LABELS).map(([k, label]) => (
                <SelectItem key={k} value={k}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FormRow>
      )}
      <FormRow label="目标类型">
        <Select value={form.targetType} onValueChange={(v) => onChange(switchTargetType(form, v))}>
          <SelectTrigger aria-label="目标类型">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="workflow">工作流</SelectItem>
            <SelectItem value="bot">助手</SelectItem>
            <SelectItem value="task">数据任务</SelectItem>
          </SelectContent>
        </Select>
      </FormRow>
      {form.targetType === "task" ? (
        <TaskFields form={form} onChange={onChange} datasources={props.datasources} />
      ) : (
        <TargetSelect
          form={form}
          onChange={onChange}
          workflows={props.workflows}
          bots={props.bots}
        />
      )}
    </div>
  );
}

function CommonFields({
  form,
  onChange,
  retriesInvalid,
}: {
  form: FormState;
  onChange: (f: FormState) => void;
  retriesInvalid: boolean;
}) {
  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        <FormRow label="失败重试上限" htmlFor="tg-max-retries">
          <Input
            id="tg-max-retries"
            value={form.maxRetries}
            onChange={(e) => onChange({ ...form, maxRetries: e.target.value })}
            className="w-32 font-mono"
            inputMode="numeric"
            aria-invalid={retriesInvalid}
          />
          <p
            role={retriesInvalid ? "alert" : undefined}
            className={
              retriesInvalid ? "text-xs text-destructive" : "text-xs text-muted-foreground"
            }
          >
            {retriesInvalid ? "须为 0–5 的整数" : "执行失败后自动重试的最大次数;0 = 不重试。"}
          </p>
        </FormRow>
      </div>
      <FormRow label="执行完成后通知地址(可空)" htmlFor="tg-notify">
        <Input
          id="tg-notify"
          value={form.notifyUrl}
          onChange={(e) => onChange({ ...form, notifyUrl: e.target.value })}
          className="font-mono text-xs"
          placeholder="https://example.com/hook(执行结果发送到此地址;仅接受公网地址)"
        />
      </FormRow>
    </>
  );
}

function TypeSpecificFields(props: TriggersFormDialogProps & { form: FormState }) {
  const { form, onChange } = props;
  if (form.type === "CRON") {
    return (
      <CronFields
        form={form}
        onChange={onChange}
        cronFires={props.cronFires}
        cronExprDebounced={props.cronExprDebounced}
      />
    );
  }
  if (form.type === "EMAIL") return <EmailFields form={form} onChange={onChange} />;
  if (form.id === null) return <WebhookFields form={form} onChange={onChange} />;
  return null;
}

export function TriggersFormDialog(props: TriggersFormDialogProps) {
  const { form, formError, savePending, retriesInvalid, onChange, onClose, onSubmit } = props;
  return (
    <Dialog
      open={form !== null}
      onOpenChange={(o) => {
        if (savePending) return;
        if (!o) onClose();
      }}
    >
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{form?.id ? "编辑触发器" : "新建触发器"}</DialogTitle>
        </DialogHeader>
        {form && (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              onSubmit();
            }}
          >
            <IdentityFields form={form} onChange={onChange} />
            <TypeAndTargetFields {...props} form={form} />
            <TypeSpecificFields {...props} form={form} />
            <CommonFields form={form} onChange={onChange} retriesInvalid={retriesInvalid} />
            {formError && (
              <p role="alert" className="text-sm text-destructive">
                {formError}
              </p>
            )}
            <DialogFooter>
              <Button type="submit" disabled={submitDisabled(form, savePending, retriesInvalid)}>
                {savePending && <Loader2 className="animate-spin" aria-hidden />}
                保存
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
