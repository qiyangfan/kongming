/* 触发器表单的目标/任务/定时区块组件与候选查询入参类型。 */

import type { UseQueryResult } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { FormRow } from "@/components/form-row";
import { SchedulePicker } from "@/components/schedule-picker";
import type { CronPreviewOut } from "@/lib/trigger-api";
import { formatDateTime } from "@/lib/datetime";
import { INTERVAL_PRESETS, type FormState, type TriggerTargetType } from "./triggers-page-model";

type OptionsQuery<T> = UseQueryResult<T | undefined>;

export type FormSource = {
  workflows: UseQueryResult<{ wf_key: string; name: string }[] | undefined>;
  bots: UseQueryResult<{ bot_key: string; name: string }[] | undefined>;
  datasources: OptionsQuery<{ datasource_key: string; name: string }[]>;
  cronFires: UseQueryResult<CronPreviewOut | undefined>;
  cronExprDebounced: string;
};

export type TriggersFormDialogProps = FormSource & {
  form: FormState | null;
  formError: string;
  savePending: boolean;
  retriesInvalid: boolean;
  onChange: (form: FormState) => void;
  onClose: () => void;
  onSubmit: () => void;
};

// 类型切换即清空目标——各形态配置不通用,防跨域残留
export function switchTargetType(form: FormState, v: string): FormState {
  return {
    ...form,
    targetType: v as TriggerTargetType,
    targetKey: "",
    taskGoal: "",
    taskDatasource: "",
    taskConfirmAuto: false,
    taskThreadField: "",
    taskFilterActions: "",
    taskRawTemplate: null,
  };
}

// cron 预览文案:防抖值与当前输入一致才展示结果,避免窗口内旧结果闪现
function cronPreviewText(q: FormSource["cronFires"], form: FormState, debounced: string): string {
  if (q.isError) {
    return `cron 无法计算:${q.error instanceof Error ? q.error.message : "表达式非法"}`;
  }
  if (q.data && form.cronExpr.trim() === debounced) {
    return `接下来 3 次:${q.data.next.map((s) => formatDateTime(s)).join("、")}`;
  }
  return "预览计算中…";
}

type TargetSelectProps = {
  form: FormState;
  onChange: (form: FormState) => void;
  workflows: FormSource["workflows"];
  bots: FormSource["bots"];
};

// 目标候选下拉:工作流与助手共用;编辑回显时目标已删则给禁用占位项
export function TargetSelect({ form, onChange, workflows, bots }: TargetSelectProps) {
  const isWorkflow = form.targetType === "workflow";
  const wfList = workflows.data ?? [];
  const botList = bots.data ?? [];
  const listLoading = isWorkflow
    ? workflows.isPending || workflows.isError
    : bots.isPending || bots.isError;
  // 候选加载中/失败时不判「已删除」:清单未到不等于目标已删,避免编辑回显被误读
  const exists = isWorkflow
    ? wfList.some((w) => w.wf_key === form.targetKey)
    : botList.some((b) => b.bot_key === form.targetKey);
  return (
    <FormRow label={`目标${isWorkflow ? "(工作流)" : "(助手)"}`}>
      {/* 自由输入改候选下拉(候选=对应域可见清单);
          编辑回显时目标已删则注入禁用占位项,防「打开即保存」静默改值 */}
      <Select value={form.targetKey} onValueChange={(v) => onChange({ ...form, targetKey: v })}>
        <SelectTrigger aria-label="目标">
          <SelectValue placeholder={isWorkflow ? "选择工作流" : "选择助手"} />
        </SelectTrigger>
        <SelectContent>
          {isWorkflow
            ? wfList.map((w) => (
                <SelectItem key={w.wf_key} value={w.wf_key}>
                  {w.name}({w.wf_key})
                </SelectItem>
              ))
            : botList.map((b) => (
                <SelectItem key={b.bot_key} value={b.bot_key}>
                  {b.name}({b.bot_key})
                </SelectItem>
              ))}
          {form.targetKey !== "" && !listLoading && !exists && (
            <SelectItem value={form.targetKey} disabled>
              (已删除){form.targetKey}
            </SelectItem>
          )}
        </SelectContent>
      </Select>
    </FormRow>
  );
}

export function TaskFields({
  form,
  onChange,
  datasources,
}: {
  form: FormState;
  onChange: TriggersFormDialogProps["onChange"];
  datasources: FormSource["datasources"];
}) {
  return (
    <>
      <FormRow label="任务目标模板" htmlFor="tg-task-goal">
        <Textarea
          id="tg-task-goal"
          value={form.taskGoal}
          onChange={(e) => onChange({ ...form, taskGoal: e.target.value })}
          rows={4}
          placeholder="事件到达后创建任务的处置目标(事件材料将作为数据块附加在目标之后)"
        />
      </FormRow>
      <FormRow label="数据源">
        <Select
          value={form.taskDatasource}
          onValueChange={(v) => onChange({ ...form, taskDatasource: v })}
        >
          <SelectTrigger aria-label="数据源">
            <SelectValue placeholder="选择数据源" />
          </SelectTrigger>
          <SelectContent>
            {(datasources.data ?? []).map((d) => (
              <SelectItem key={d.datasource_key} value={d.datasource_key}>
                {d.name}({d.datasource_key})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </FormRow>
      <FormRow label="自动派发">
        <Switch
          checked={form.taskConfirmAuto}
          onCheckedChange={(v) => onChange({ ...form, taskConfirmAuto: v })}
          aria-label="自动派发"
        />
        <p className="text-xs text-muted-foreground">
          关闭(缺省)=事件立项后待你确认才执行;开启=立项即按正常编排派发(预算与审批照常)。
        </p>
      </FormRow>
      <FormRow label="线程键字段(可选)" htmlFor="tg-thread-field">
        <Input
          id="tg-thread-field"
          value={form.taskThreadField}
          onChange={(e) => onChange({ ...form, taskThreadField: e.target.value })}
          className="w-48 font-mono"
          placeholder="如 issue_no;GitHub 适配器自动推导"
        />
        <p className="text-xs text-muted-foreground">
          同线程存在未终态任务时,后续事件追加至既有任务而非重复立项。
        </p>
      </FormRow>
      <FormRow label="过滤动作(可选)" htmlFor="tg-filter-actions">
        <Input
          id="tg-filter-actions"
          value={form.taskFilterActions}
          onChange={(e) => onChange({ ...form, taskFilterActions: e.target.value })}
          className="w-64 font-mono"
          placeholder="opened, reopened(逗号分隔)"
        />
        <p className="text-xs text-muted-foreground">
          程序层先按事件动作筛除噪音,再交语义判读判断是否值得立项。
        </p>
      </FormRow>
    </>
  );
}

export function CronFields({
  form,
  onChange,
  cronFires,
  cronExprDebounced,
}: {
  form: FormState;
  onChange: TriggersFormDialogProps["onChange"];
  cronFires: FormSource["cronFires"];
  cronExprDebounced: string;
}) {
  return (
    <div className="space-y-3">
      <SchedulePicker
        value={form.cronExpr}
        onChange={(cronExpr) => onChange({ ...form, cronExpr })}
      />
      <FormRow label="最小间隔(秒)" htmlFor="tg-interval">
        <Input
          id="tg-interval"
          value={form.minInterval}
          onChange={(e) => onChange({ ...form, minInterval: e.target.value })}
          className="w-32 font-mono"
        />
        <div className="flex flex-wrap gap-1">
          {INTERVAL_PRESETS.map((p) => (
            <Button
              key={p.seconds}
              type="button"
              variant="outline"
              size="xs"
              onClick={() => onChange({ ...form, minInterval: String(p.seconds) })}
            >
              {p.label}
            </Button>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          两次触发之间的最小间隔秒数;间隔内的重复触发会跳过不执行。
        </p>
      </FormRow>
      {/* 预览接下来三次触发时间;非法表达式显示错误原因,与保存校验一致。
          仅当防抖值与当前输入一致才显示,防抖窗口内的旧结果不展示 */}
      {form.cronExpr.trim() !== "" && (
        <p
          role={cronFires.isError ? "alert" : undefined}
          className={
            cronFires.isError ? "text-xs text-destructive" : "text-xs text-muted-foreground"
          }
        >
          {cronPreviewText(cronFires, form, cronExprDebounced)}
        </p>
      )}
    </div>
  );
}
