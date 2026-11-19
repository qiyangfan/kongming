/* 提供常用频率和 cron 编辑；外部表单只保存 cron 字符串，无法识别的表达式保持原样进入高级模式。 */
import { useEffect, useRef, useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  EXPERT_KEY,
  FREQ_OPTIONS,
  parseCronToSchedule,
  scheduleFromKey,
  scheduleToCron,
  scheduleToKey,
  type Schedule,
} from "@/lib/cron-schedule";
import { ScheduleParams } from "@/components/schedule-param-fields";

type SchedulePickerProps = {
  value: string;
  onChange: (expr: string) => void;
};

/* 频率下拉：按间隔/按日历分组，末尾单列「cron 表达式」高级项。 */
function FrequencySelect({
  selectValue,
  onPick,
}: {
  selectValue: string;
  onPick: (key: string) => void;
}) {
  return (
    <Select value={selectValue} onValueChange={onPick}>
      <SelectTrigger id="tg-schedule" aria-label="调度频率" className="w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {(["interval", "calendar"] as const).map((group) => (
          <SelectGroup key={group}>
            <SelectLabel>{group === "interval" ? "按间隔" : "按日历"}</SelectLabel>
            {FREQ_OPTIONS.filter((o) => o.group === group).map((o) => (
              <SelectItem key={o.key} value={o.key}>
                {o.label}
              </SelectItem>
            ))}
          </SelectGroup>
        ))}
        <SelectGroup>
          <SelectLabel>高级</SelectLabel>
          <SelectItem value={EXPERT_KEY}>cron 表达式</SelectItem>
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}

/* 高级模式：直接编辑五字段 cron 原文。 */
function CronInput({ value, onChange }: { value: string; onChange: (expr: string) => void }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor="tg-cron">cron 表达式</Label>
      <Input
        id="tg-cron"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="font-mono"
        autoComplete="off"
        placeholder="分 时 日 月 周,如 0 9 * * 1-5"
      />
      <p className="text-xs text-muted-foreground">五字段:分 时 日 月 周;日与周仅其一可为 *。</p>
    </div>
  );
}

export function SchedulePicker({ value, onChange }: SchedulePickerProps) {
  const [schedule, setSchedule] = useState<Schedule>({ kind: "daily", hour: 9, minute: 0 });
  const [expert, setExpert] = useState(false);
  // 本组件 emit 的表达式回流时不重复解析;仅外部设值(编辑回显/表单重置)走白名单识别。
  const lastEmitted = useRef<string | null>(null);

  useEffect(() => {
    if (value === lastEmitted.current) return;
    lastEmitted.current = null;
    const parsed = parseCronToSchedule(value);
    if (parsed) {
      setSchedule(parsed);
      setExpert(false);
    } else {
      setExpert(true);
    }
  }, [value]);

  const emit = (next: Schedule) => {
    setSchedule(next);
    setExpert(false);
    const expr = scheduleToCron(next);
    lastEmitted.current = expr;
    onChange(expr);
  };

  const pick = (key: string) => {
    if (key === EXPERT_KEY) {
      setExpert(true);
      return;
    }
    const next = scheduleFromKey(key, schedule);
    if (next) emit(next);
  };

  // 高级模式编辑时同步推进回流标记,避免自身输入被当作外部设值再解析
  const changeCron = (expr: string) => {
    lastEmitted.current = expr;
    onChange(expr);
  };

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="tg-schedule">调度频率</Label>
        <FrequencySelect
          selectValue={expert ? EXPERT_KEY : scheduleToKey(schedule)}
          onPick={pick}
        />
      </div>
      {expert ? (
        <CronInput value={value} onChange={changeCron} />
      ) : (
        <ParamsRow schedule={schedule} emit={emit} />
      )}
    </div>
  );
}

/* 非高级模式的参数行；频率类型下无参数（如按间隔）时整行不渲染。 */
function ParamsRow({ schedule, emit }: { schedule: Schedule; emit: (s: Schedule) => void }) {
  const params = <ScheduleParams schedule={schedule} emit={emit} />;
  if (params === null) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">{params}</div>
  );
}
