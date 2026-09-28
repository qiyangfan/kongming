import { describe, expect, it } from "vitest";

import { endExclusive } from "./date-utils";

/* endExclusive off-by-one 锚:结束日当天记录须可见 — 直接取当日零点会把
   结束日全天排除;须进到次日零点(后端 created_at < :end 排他语义)。 */
describe("endExclusive", () => {
  it("普通日期进到次日零点(UTC ISO)", () => {
    expect(endExclusive("2026-09-12")).toBe("2026-09-13T00:00:00.000Z");
  });

  it("跨月日界", () => {
    expect(endExclusive("2026-01-31")).toBe("2026-02-01T00:00:00.000Z");
    expect(endExclusive("2026-08-31")).toBe("2026-09-01T00:00:00.000Z");
  });

  it("跨年日界", () => {
    expect(endExclusive("2026-12-31")).toBe("2027-01-01T00:00:00.000Z");
  });

  it("闰年二月末尾(2028 闰):进到 3 月 1 日", () => {
    expect(endExclusive("2028-02-29")).toBe("2028-03-01T00:00:00.000Z");
  });
});
