import { describe, expect, it } from "vitest";

import {
  parseCronToSchedule,
  scheduleFromKey,
  scheduleToCron,
  scheduleToKey,
} from "./cron-schedule";
import type { Schedule } from "./cron-schedule";

/* cron 解析白名单锚(066.3.2):档位生成的表达式恒可反解(等价写法互通);
   白名单外形态一律 null 走高级直通(fail-open),后端语义是唯一事实源。 */

describe("scheduleToCron + parseCronToSchedule 往返", () => {
  const cases: Schedule[] = [
    { kind: "interval", unit: "minute", n: 15 },
    { kind: "interval", unit: "minute", n: 1 },
    { kind: "interval", unit: "hour", n: 6 },
    { kind: "interval", unit: "hour", n: 1 },
    { kind: "hourly", minute: 30 },
    { kind: "daily", hour: 9, minute: 0 },
    { kind: "weekly", weekday: 3, hour: 8, minute: 5 },
    { kind: "workday", hour: 9, minute: 30 },
    { kind: "monthly", monthDay: 1, hour: 0, minute: 0 },
  ];
  for (const s of cases) {
    it(`${JSON.stringify(s)} 往返后语义相等`, () => {
      const expr = scheduleToCron(s);
      const back = parseCronToSchedule(expr);
      // 等价写法归一:每 1 小时(0 * * * *)反解为 hourly 档,语义相同
      const equivalent = (
        s.kind === "interval" && s.unit === "hour" && s.n === 1 ? { kind: "hourly", minute: 0 } : s
      ) as Schedule;
      expect(back).toEqual(equivalent);
    });
  }

  it("分钟间隔档生成 */n 形态且反解一致", () => {
    expect(scheduleToCron({ kind: "interval", unit: "minute", n: 15 })).toBe("*/15 * * * *");
    expect(scheduleToCron({ kind: "interval", unit: "hour", n: 6 })).toBe("0 */6 * * *");
  });

  it("工作档生成 1-5 星期域(而非 0-4 混淆)", () => {
    expect(scheduleToCron({ kind: "workday", hour: 9, minute: 0 })).toBe("0 9 * * 1-5");
  });
});

describe("parseCronToSchedule 非法/白名单外输入", () => {
  it("字段数不为 5 → null", () => {
    expect(parseCronToSchedule("* * * *")).toBeNull();
    expect(parseCronToSchedule("* * * * * *")).toBeNull();
    expect(parseCronToSchedule("")).toBeNull();
  });

  it("字段越界/区间颠倒 → null", () => {
    expect(parseCronToSchedule("60 * * * *")).toBeNull(); // 分钟上限 59
    expect(parseCronToSchedule("* 24 * * *")).toBeNull(); // 小时上限 23
    expect(parseCronToSchedule("0 0 * * 8")).toBeNull(); // 星期上限 7
    expect(parseCronToSchedule("30-10 * * * *")).toBeNull(); // 区间颠倒
  });

  it("白名单外的合法 cron 不猜解 → null 走高级模式", () => {
    expect(parseCronToSchedule("*/7 * * * *")).toBeNull(); // 7 非 60 因数,档位不收
    expect(parseCronToSchedule("0,15,45 * * * *")).toBeNull(); // 非步进等距,无档位可生成
    expect(parseCronToSchedule("0 9 * * 1,3")).toBeNull(); // 星期列表
    expect(parseCronToSchedule("0 9 1 * *")).toEqual({
      kind: "monthly",
      monthDay: 1,
      hour: 9,
      minute: 0,
    });
  });

  it("等价写法互通:0,30 与 */30 同一档(字段集合相等比对)", () => {
    expect(parseCronToSchedule("0,30 * * * *")).toEqual({
      kind: "interval",
      unit: "minute",
      n: 30,
    });
    expect(parseCronToSchedule("*/30 * * * *")).toEqual({
      kind: "interval",
      unit: "minute",
      n: 30,
    });
  });

  it("星期 0/7 双写归一:两者都反解为同一每周档", () => {
    const sunday0 = parseCronToSchedule("0 9 * * 0");
    const sunday7 = parseCronToSchedule("0 9 * * 7");
    expect(sunday0).toEqual({ kind: "weekly", weekday: 0, hour: 9, minute: 0 });
    expect(sunday7).toEqual(sunday0);
  });

  it("多值分钟无法定档(如 0-30 每分钟)→ null,宁缺毋错", () => {
    expect(parseCronToSchedule("0-30 * * * *")).toBeNull();
  });
});

describe("档位键往返", () => {
  it("interval/minute/hour 与键互转稳定", () => {
    const s: Schedule = { kind: "interval", unit: "minute", n: 5 };
    expect(scheduleToCron(s)).toBe("*/5 * * * *");
    expect(scheduleToKey(s)).toBe("interval-minute-5");
  });

  it("scheduleFromKey 对非法键返回 null", () => {
    const cur: Schedule = { kind: "daily", hour: 9, minute: 0 };
    expect(scheduleFromKey("interval-minute-7", cur)).toBeNull();
    expect(scheduleFromKey("nonsense", cur)).toBeNull();
  });
});
