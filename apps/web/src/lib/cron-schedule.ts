/* 将预设调度档位转换为五字段 cron，并只将本模块生成的表达式反解为预设。完整 cron 合法性由后端校验。 */

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
      return s.unit === "minute" ? `${step(s.n)} * * * *` : `0 ${step(s.n)} * * *`;
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

const WEEKDAY_IDS = [1, 2, 3, 4, 5];

function rangePart(
  base: string,
  bounds: { slash: number; lo: number; hi: number },
): [number, number] | null {
  const { slash, lo, hi } = bounds;
  if (base === "*") return [lo, hi];
  if (/^\d+$/.test(base)) {
    const start = Number(base);
    return [start, slash >= 0 ? hi : start];
  }
  if (/^\d+-\d+$/.test(base)) {
    const [start, end] = base.split("-").map(Number);
    return start > end ? null : [start, end];
  }
  return null;
}

/* 拆分 `a/b` 形式:无斜杠时步长取 1;步长非法返回 null。 */
function splitStep(part: string): { base: string; slash: number; step: number } | null {
  let base = part;
  let step = 1;
  const slash = part.indexOf("/");
  if (slash >= 0) {
    base = part.slice(0, slash);
    const stepPart = part.slice(slash + 1);
    if (!/^\d+$/.test(stepPart) || stepPart === "0") return null;
    step = Number(stepPart);
  }
  return { base, slash, step };
}

/* 单个片段(如星号、单值、区间、步进形式)解析为 [起始, 结束, 步长]。 */
function parsePart(part: string, lo: number, hi: number): [number, number, number] | null {
  if (part === "") return null;
  const split = splitStep(part);
  if (!split) return null;
  const { base, slash, step } = split;
  const partBounds = rangePart(base, { slash, lo, hi });
  if (!partBounds) return null;
  const [start, end] = partBounds;
  if (start < lo || start > hi || end > hi || start > end) return null;
  return [start, end, step];
}

/** 单个 cron 字段解析为值集合;仅支持与后端约定的形态,其余返回 null(落入高级模式)。 */
function parseField(src: string, lo: number, hi: number): Set<number> | null {
  const out = new Set<number>();
  for (const part of src.split(",")) {
    const range = parsePart(part, lo, hi);
    if (!range) return null;
    const [start, end, step] = range;
    for (let v = start; v <= end; v += step) out.add(v);
  }
  return out;
}

/* 月/日/周三个字段是否都取满取值范围(全通配) */
function isWildcardDayMonth(fields: {
  dom: Set<number>;
  month: Set<number>;
  dow: Set<number>;
}): boolean {
  const { dom, month, dow } = fields;
  return (
    setEquals(dom, rangeSet(1, 31)) &&
    setEquals(month, rangeSet(1, 12)) &&
    setEquals(dow, rangeSet(0, 6))
  );
}

/* 月/日/周组合是否可落到某个固定周期档;定不下返回 null。 */
function namedKind(
  dom: Set<number>,
  month: Set<number>,
  dow: Set<number>,
): "weekly" | "workday" | "monthly" | null {
  const domFull = setEquals(dom, rangeSet(1, 31));
  const monFull = setEquals(month, rangeSet(1, 12));
  const dowFull = setEquals(dow, rangeSet(0, 6));
  if (domFull && monFull && dow.size === 1) return "weekly";
  if (monFull && domFull && setEquals(dow, new Set(WEEKDAY_IDS))) return "workday";
  if (monFull && dowFull && dom.size === 1) return "monthly";
  return null;
}

/* 非全通配的月/日/周组合 → 每周/工作日/每月档;均不匹配返回 null。 */
function namedSchedule(fields: {
  minute: Set<number>;
  hour: Set<number>;
  dom: Set<number>;
  month: Set<number>;
  dow: Set<number>;
}): Schedule | null {
  const { minute, hour, dom, month, dow } = fields;
  const kind = namedKind(dom, month, dow);
  const m = single(minute);
  const h = single(hour);
  if (kind === null || m === null || h === null) return null;
  if (kind === "weekly") return { kind, weekday: [...dow][0], hour: h, minute: m };
  if (kind === "workday") return { kind, hour: h, minute: m };
  return { kind: "monthly", monthDay: [...dom][0], hour: h, minute: m };
}

/* 小时轴全通配时的分钟档:整分钟步进优先,否则整点档。 */
function minuteSchedule(minute: Set<number>): Schedule | null {
  const n = matchStep(minute, "minute");
  if (n !== null) return { kind: "interval", unit: "minute", n };
  const m = single(minute);
  return m === null ? null : { kind: "hourly", minute: m };
}

/* 全通配的月/日/周组合 → 按分钟/小时步进定档,否则退回每日/整点档。 */
function wildcardSchedule(minute: Set<number>, hour: Set<number>): Schedule | null {
  if (setEquals(hour, rangeSet(0, 23))) return minuteSchedule(minute);
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
  return (
    namedSchedule({ minute, hour, dom, month, dow }) ??
    (isWildcardDayMonth({ dom, month, dow }) ? wildcardSchedule(minute, hour) : null)
  );
}
