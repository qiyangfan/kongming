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
  WEEKDAY_LABELS,
  parseCronToSchedule,
  scheduleFromKey,
  scheduleToCron,
  scheduleToKey,
  type Schedule,
} from "@/lib/cron-schedule";

const pad2 = (v: number) => String(v).padStart(2, "0");

const HOUR_OPTIONS = Array.from({ length: 24 }, (_, i) => i);
const MINUTE_OPTIONS = Array.from({ length: 60 }, (_, i) => i);
const MONTH_DAY_OPTIONS = Array.from({ length: 31 }, (_, i) => i + 1);
const WEEKDAY_OPTIONS = [0, 1, 2, 3, 4, 5, 6];

type SchedulePickerProps = {
  value: string;
  onChange: (expr: string) => void;
};

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

  // Radix BubbleSelect 缺陷防御:受控值在挂载后变化(编辑回显)且下拉从未
  // 打开时,其隐藏原生 select 无 options,同步 change 事件回落为 "",直接
  // 忽略,防 Number("")=0 静默打掉反解回显值。
  const guardNum = (fn: (v: number) => void) => (v: string) => {
    if (v === "") return;
    fn(Number(v));
  };

  const numSelect = (
    label: string,
    options: number[],
    current: number,
    onPick: (v: number) => void,
    width: string,
  ) => (
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

  const renderParams = () => {
    switch (schedule.kind) {
      case "interval":
        return null;
      case "hourly":
        return (
          <>
            <span>每小时第</span>
            {numSelect(
              "分钟",
              MINUTE_OPTIONS,
              schedule.minute,
              (minute) => emit({ ...schedule, minute }),
              "w-20",
            )}
            <span>分触发</span>
          </>
        );
      case "daily":
        return (
          <>
            <span>每天</span>
            {numSelect(
              "小时",
              HOUR_OPTIONS,
              schedule.hour,
              (hour) => emit({ ...schedule, hour }),
              "w-20",
            )}
            <span>时</span>
            {numSelect(
              "分钟",
              MINUTE_OPTIONS,
              schedule.minute,
              (minute) => emit({ ...schedule, minute }),
              "w-20",
            )}
            <span>分触发</span>
          </>
        );
      case "weekly":
        return (
          <>
            <span>每周</span>
            <Select
              value={String(schedule.weekday)}
              onValueChange={guardNum((weekday) => emit({ ...schedule, weekday }))}
            >
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
            {numSelect(
              "小时",
              HOUR_OPTIONS,
              schedule.hour,
              (hour) => emit({ ...schedule, hour }),
              "w-20",
            )}
            <span>时</span>
            {numSelect(
              "分钟",
              MINUTE_OPTIONS,
              schedule.minute,
              (minute) => emit({ ...schedule, minute }),
              "w-20",
            )}
            <span>分触发</span>
          </>
        );
      case "workday":
        return (
          <>
            <span>周一至周五</span>
            {numSelect(
              "小时",
              HOUR_OPTIONS,
              schedule.hour,
              (hour) => emit({ ...schedule, hour }),
              "w-20",
            )}
            <span>时</span>
            {numSelect(
              "分钟",
              MINUTE_OPTIONS,
              schedule.minute,
              (minute) => emit({ ...schedule, minute }),
              "w-20",
            )}
            <span>分触发</span>
          </>
        );
      case "monthly":
        return (
          <>
            <span>每月</span>
            {numSelect(
              "日期",
              MONTH_DAY_OPTIONS,
              schedule.monthDay,
              (monthDay) => emit({ ...schedule, monthDay }),
              "w-20",
            )}
            <span>日</span>
            {numSelect(
              "小时",
              HOUR_OPTIONS,
              schedule.hour,
              (hour) => emit({ ...schedule, hour }),
              "w-20",
            )}
            <span>时</span>
            {numSelect(
              "分钟",
              MINUTE_OPTIONS,
              schedule.minute,
              (minute) => emit({ ...schedule, minute }),
              "w-20",
            )}
            <span>分触发</span>
          </>
        );
    }
  };

  const params = renderParams();

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="tg-schedule">调度频率</Label>
        <Select value={expert ? EXPERT_KEY : scheduleToKey(schedule)} onValueChange={pick}>
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
      </div>
      {expert ? (
        <div className="space-y-1.5">
          <Label htmlFor="tg-cron">cron 表达式</Label>
          <Input
            id="tg-cron"
            value={value}
            onChange={(e) => {
              lastEmitted.current = e.target.value;
              onChange(e.target.value);
            }}
            className="font-mono"
            autoComplete="off"
            placeholder="分 时 日 月 周,如 0 9 * * 1-5"
          />
          <p className="text-xs text-muted-foreground">
            五字段:分 时 日 月 周;日与周仅其一可为 *。
          </p>
        </div>
      ) : (
        params !== null && (
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            {params}
          </div>
        )
      )}
    </div>
  );
}
