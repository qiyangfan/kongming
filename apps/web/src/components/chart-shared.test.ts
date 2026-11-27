import { describe, expect, it } from "vitest";

import { hasChartToRender, type ChartSpec } from "@kongming/api-client/chart-shared";

/* 图表渲染闸:任一维为空即不渲染(含带 chart 产物但零候选的存量形态)。 */

const SPEC: ChartSpec = { type: "bar", x: "month", y: ["amount"], series: null };

describe("hasChartToRender", () => {
  it("有数据列且有候选才渲染", () => {
    expect(hasChartToRender(["month", "amount"], [SPEC])).toBe(true);
  });

  it("零候选不渲染(即使存在 chart 产物)", () => {
    expect(hasChartToRender(["month", "amount"], [])).toBe(false);
  });

  it("零数据列不渲染", () => {
    expect(hasChartToRender([], [SPEC])).toBe(false);
  });
});
