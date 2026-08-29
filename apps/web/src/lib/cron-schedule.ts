/* 调度档位 <-> 五字段 cron 表达式的生成与白名单反解。
 *
 * cron 语义单源在后端(kongming/trigger/models.py),本文件不构成第二套
 * 校验语义,只做两件事:
 * 1. 生成:档位状态 -> cron 表达式,档位直出恒合法;
 * 2. 反解:cron 表达式 -> 档位状态,白名单制——仅识别本文件可生成的
 *    形态(按字段集合相等比对,等价写法互通);识别失败一律返回 null,
 *    由调用方落「cron 表达式(高级)」直通模式,宁缺毋错(fail-open):
 *    后端语义将来扩展时此处无需跟随。
 *
 * 档位的分钟/小时间隔只收 60/24 的因数:步进字段是「每小时/天内
 * 重复」的日历对齐语义,非因数(如每 7 分钟)心智与实际会偏差,该类
 * 长尾走高级模式全表达力承载。
 */

export type Schedule =
  | { kind: "interval"; unit: "minute" | "hour"; n: number }
  | { kind: "hourly"; minute: number }
  | { kind: "daily"; hour: number; minute: number }
  | { kind: "weekly"; weekday: number; hour: number; minute: number }
  | { kind: "workday"; hour: number; minute: number }
  | { kind: "monthly"; monthDay: number; hour: number; minute: number };

/** 高级模式在档位下拉里的固定键。 */
export const EXPERT_KEY = "expert";

const MINUTE_STEPS = [1, 2, 3, 5, 6, 10, 12, 15, 20, 30] as const;
const HOUR_STEPS = [2, 3, 4, 6, 8, 12] as const;

export const WEEKDAY_LABELS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

/** 档位下拉选项(分组次序即展示次序)。 */
export const FREQ_OPTIONS: { group: "interval" | "calendar"; key: string; label: string }[] = [
  ...MINUTE_STEPS.map((n) => ({
    group: "interval" as const,
    key: `interval-minute-${n}`,
    label: n === 1 ? "每分钟" : `每 ${n} 分钟`,
  })),
  ...HOUR_STEPS.map((n) => ({
    group: "interval" as const,
    key: `interval-hour-${n}`,
    label: `每 ${n} 小时`,
  })),
  { group: "interval", key: "hourly", label: "每小时" },
  { group: "calendar", key: "daily", label: "每天" },
  { group: "calendar", key: "weekly", label: "每周" },
  { group: "calendar", key: "workday", label: "工作日(周一至周五)" },
  { group: "calendar", key: "monthly", label: "每月" },
];

export function scheduleToCron(s: Schedule): string {
  const step = (n: number) => (n === 1 ? "*" : `*/${n}`);
  switch (s.kind) {
    case "interval":
      return s.unit === "minute"
        ? `${step(s.n)} * * * *`
        : `0 ${step(s.n)} * * *`;
    case "hourly":
      return `${s.minute} * * * *`;
    case "daily":
      return `${s.minute} ${s.hour} * * *`;
    case "weekly":
      return `${s.minute} ${s.hour} * * ${s.weekday}`;
    case "workday":
      return `${s.minute} ${s.hour} * * 1-5`;
    case "monthly":
      return `${s.minute} ${s.hour} ${s.monthDay} * *`;
  }
}

/** 档位 -> 下拉键。 */
export function scheduleToKey(s: Schedule): string {
  return s.kind === "interval" ? `interval-${s.unit}-${s.n}` : s.kind;
}

/** 下拉键 -> 档位;时刻参数尽量继承当前档位,首次切换落默认值。 */
export function scheduleFromKey(key: string, cur: Schedule): Schedule | null {
  const hour = "hour" in cur ? cur.hour : 9;
  const minute = "minute" in cur ? cur.minute : 0;
  if (key.startsWith("interval-minute-")) {
    const n = Number(key.slice("interval-minute-".length));
    return (MINUTE_STEPS as readonly number[]).includes(n)
      ? { kind: "interval", unit: "minute", n }
      : null;
  }
  if (key.startsWith("interval-hour-")) {
    const n = Number(key.slice("interval-hour-".length));
    return (HOUR_STEPS as readonly number[]).includes(n)
      ? { kind: "interval", unit: "hour", n }
      : null;
  }
  switch (key) {
    case "hourly":
      return { kind: "hourly", minute };
    case "daily":
      return { kind: "daily", hour, minute };
    case "weekly":
      return { kind: "weekly", weekday: cur.kind === "weekly" ? cur.weekday : 1, hour, minute };
    case "workday":
      return { kind: "workday", hour, minute };
    case "monthly":
      return {
        kind: "monthly",
        monthDay: cur.kind === "monthly" ? cur.monthDay : 1,
        hour,
        minute,
      };
    default:
      return null;
  }
}

// ── 白名单反解 ──

function rangeSet(lo: number, hi: number): Set<number> {
  const s = new Set<number>();
  for (let v = lo; v <= hi; v++) s.add(v);
  return s;
}

function setEquals(a: Set<number>, b: Set<number>): boolean {
  if (a.size !== b.size) return false;
  for (const v of a) if (!b.has(v)) return false;
  return true;
}

function stepSet(n: number, hi: number): Set<number> {
  const s = new Set<number>();
  for (let v = 0; v <= hi; v += n) s.add(v);
  return s;
}

function matchStep(set: Set<number>, unit: "minute" | "hour"): number | null {
  const steps = unit === "minute" ? MINUTE_STEPS : HOUR_STEPS;
  const hi = unit === "minute" ? 59 : 23;
  for (const n of steps) if (setEquals(set, stepSet(n, hi))) return n;
  return null;
}

function single(set: Set<number>): number | null {
  return set.size === 1 ? [...set][0] : null;
}

/** 字段段 -> 值集合;仅覆盖后端立约的形态,超纲返回 null(落高级模式)。 */
function parseField(src: string, lo: number, hi: number): Set<number> | null {
  const out = new Set<number>();
  for (const part of src.split(",")) {
    if (part === "") return null;
    let base = part;
    let step = 1;
    const slash = part.indexOf("/");
    if (slash >= 0) {
      base = part.slice(0, slash);
      const stepPart = part.slice(slash + 1);
      if (!/^\d+$/.test(stepPart) || stepPart === "0") return null;
      step = Number(stepPart);
    }
    let start: number;
    let end: number;
    if (base === "*") {
      start = lo;
      end = hi;
    } else if (/^\d+$/.test(base)) {
      start = Number(base);
      end = slash >= 0 ? hi : start;
    } else if (/^\d+-\d+$/.test(base)) {
      [start, end] = base.split("-").map(Number);
      if (start > end) return null;
    } else {
      return null;
    }
    if (start < lo || start > hi || end > hi || start > end) return null;
    for (let v = start; v <= end; v += step) out.add(v);
  }
  return out;
}

/** cron 表达式 -> 档位;不可识别返回 null。 */
export function parseCronToSchedule(expr: string): Schedule | null {
  const fields = expr.trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const parts = [
    parseField(fields[0], 0, 59),
    parseField(fields[1], 0, 23),
    parseField(fields[2], 1, 31),
    parseField(fields[3], 1, 12),
    parseField(fields[4], 0, 7),
  ];
  if (parts.some((p) => p === null)) return null;
  const [minute, hour, dom, month, dow] = parts as Set<number>[];
  // 周日 0/7 双写归一(与后端同口径),让 `0 9 * * 7` 也能反解为每周档
  if (dow.has(7)) {
    dow.add(0);
    dow.delete(7);
  }
  const domFull = setEquals(dom, rangeSet(1, 31));
  const monFull = setEquals(month, rangeSet(1, 12));
  const dowFull = setEquals(dow, rangeSet(0, 6));
  if (!domFull || !monFull || !dowFull) {
    const m = single(minute);
    const h = single(hour);
    if (m === null || h === null) return null;
    if (monFull && domFull && dow.size === 1) {
      return { kind: "weekly", weekday: [...dow][0], hour: h, minute: m };
    }
    if (monFull && domFull && setEquals(dow, new Set([1, 2, 3, 4, 5]))) {
      return { kind: "workday", hour: h, minute: m };
    }
    if (monFull && dowFull && dom.size === 1) {
      return { kind: "monthly", monthDay: [...dom][0], hour: h, minute: m };
    }
    return null;
  }
  const hFull = setEquals(hour, rangeSet(0, 23));
  if (hFull) {
    const n = matchStep(minute, "minute");
    if (n !== null) return { kind: "interval", unit: "minute", n };
    const m = single(minute);
    return m === null ? null : { kind: "hourly", minute: m };
  }
  const m = single(minute);
  if (m === null) return null;
  if (m === 0) {
    const n = matchStep(hour, "hour");
    if (n !== null) return { kind: "interval", unit: "hour", n };
  }
  const h = single(hour);
  if (h === null) return null;
  return { kind: "daily", hour: h, minute: m };
}
