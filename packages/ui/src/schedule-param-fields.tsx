/* 调度频率的参数选择器：按频率类型组合「星期/日期/时/分」几个数字下拉。 */

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@kongming/ui/select";
import {
  WEEKDAY_LABELS,
  type Schedule,
} from "@kongming/api-client/cron-schedule";

const pad2 = (v: number) => String(v).padStart(2, "0");

const HOUR_OPTIONS = Array.from({ length: 24 }, (_, i) => i);
const MINUTE_OPTIONS = Array.from({ length: 60 }, (_, i) => i);
const MONTH_DAY_OPTIONS = Array.from({ length: 31 }, (_, i) => i + 1);
const WEEKDAY_OPTIONS = [0, 1, 2, 3, 4, 5, 6];

/* 下拉只回传数字：忽略空串，防 Number("")=0 静默打掉反解回显值。 */
const guardNum = (fn: (v: number) => void) => (v: string) => {
  if (v === "") return;
  fn(Number(v));
};

/* 带时/分的日历档（工作日/每周/每月/每天）。 */
type TimedSchedule = Extract<Schedule, { hour: number }>;

type MinuteSchedule = Extract<Schedule, { minute: number }>;

/* 通用数字下拉；值按两位补零展示。 */
function NumSelect({
  label,
  options,
  current,
  onPick,
  width,
}: {
  label: string;
  options: number[];
  current: number;
  onPick: (v: number) => void;
  width: string;
}) {
  return (
    <Select value={String(current)} onValueChange={guardNum(onPick)}>
      <SelectTrigger aria-label={label} className={width}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((v) => (
          <SelectItem key={v} value={String(v)}>
            {pad2(v)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/* 星期下拉；展示 WEEKDAY_LABELS 中的中文名。 */
function WeekdaySelect({
  current,
  onPick,
}: {
  current: number;
  onPick: (v: number) => void;
}) {
  return (
    <Select value={String(current)} onValueChange={guardNum(onPick)}>
      <SelectTrigger aria-label="星期" className="w-24">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {WEEKDAY_OPTIONS.map((v) => (
          <SelectItem key={v} value={String(v)}>
            {WEEKDAY_LABELS[v]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function HourField({
  schedule,
  emit,
}: {
  schedule: TimedSchedule;
  emit: (s: Schedule) => void;
}) {
  return (
    <>
      <NumSelect
        label="小时"
        options={HOUR_OPTIONS}
        current={schedule.hour}
        onPick={(hour) => emit({ ...schedule, hour })}
        width="w-20"
      />
      <span>时</span>
    </>
  );
}

function MinuteField({
  schedule,
  emit,
}: {
  schedule: MinuteSchedule;
  emit: (s: Schedule) => void;
}) {
  return (
    <>
      <NumSelect
        label="分钟"
        options={MINUTE_OPTIONS}
        current={schedule.minute}
        onPick={(minute) => emit({ ...schedule, minute })}
        width="w-20"
      />
      <span>分触发</span>
    </>
  );
}

function HourMinuteFields({
  schedule,
  emit,
}: {
  schedule: TimedSchedule;
  emit: (s: Schedule) => void;
}) {
  return (
    <>
      <HourField schedule={schedule} emit={emit} />
      <MinuteField schedule={schedule} emit={emit} />
    </>
  );
}

/* 按频率类型渲染参数区；interval 由数字输入承载，无参数。 */
export function ScheduleParams({
  schedule,
  emit,
}: {
  schedule: Schedule;
  emit: (s: Schedule) => void;
}) {
  switch (schedule.kind) {
    case "interval":
      return null;
    case "hourly":
      return (
        <>
          <span>每小时第</span>
          <MinuteField schedule={schedule} emit={emit} />
        </>
      );
    case "daily":
      return (
        <>
          <span>每天</span>
          <HourMinuteFields schedule={schedule} emit={emit} />
        </>
      );
    case "weekly":
      return (
        <>
          <span>每周</span>
          <WeekdaySelect
            current={schedule.weekday}
            onPick={(weekday) => emit({ ...schedule, weekday })}
          />
          <HourMinuteFields schedule={schedule} emit={emit} />
        </>
      );
    case "workday":
      return (
        <>
          <span>周一至周五</span>
          <HourMinuteFields schedule={schedule} emit={emit} />
        </>
      );
    case "monthly":
      return (
        <>
          <span>每月</span>
          <NumSelect
            label="日期"
            options={MONTH_DAY_OPTIONS}
            current={schedule.monthDay}
            onPick={(monthDay) => emit({ ...schedule, monthDay })}
            width="w-20"
          />
          <span>日</span>
          <HourMinuteFields schedule={schedule} emit={emit} />
        </>
      );
  }
}
