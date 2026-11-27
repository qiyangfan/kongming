import { describe, expect, it } from "vitest";

import { formatDuration } from "@kongming/api-client/datetime";

/* 验证毫秒、秒和分秒格式及各档之间的边界。 */
describe("formatDuration", () => {
  it("毫秒档:<1000ms 舍入封顶 999", () => {
    expect(formatDuration(0)).toBe("0ms");
    expect(formatDuration(840)).toBe("840ms");
    expect(formatDuration(999.6)).toBe("999ms"); // 进位不得越档到 "1000ms"
  });

  it("秒档:小于 10 秒保留一位小数,超过则整秒", () => {
    expect(formatDuration(1000)).toBe("1s");
    expect(formatDuration(8300)).toBe("8.3s");
    expect(formatDuration(12_340)).toBe("12s"); // 12.34s 整秒去小数
  });

  it("撞档边界:59.95s 钳到 59s,不与 1m0s 混淆", () => {
    expect(formatDuration(59_950)).toBe("59s");
    // ≥10s 走整秒舍入:59.499s → 59s;59.96s 进位同样被 min 钳回 59
    expect(formatDuration(59_499)).toBe("59s");
    expect(formatDuration(59_960)).toBe("59s");
  });

  it("分钟档:秒数舍入进位跨整分时显 m0s", () => {
    expect(formatDuration(60_000)).toBe("1m0s");
    expect(formatDuration(205_000)).toBe("3m25s");
    expect(formatDuration(119_960)).toBe("2m0s"); // 59.96s → 60s → 进一分
  });

  it("空值显占位符", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(undefined)).toBe("—");
    expect(formatDuration(Number.NaN)).toBe("—");
  });
});
