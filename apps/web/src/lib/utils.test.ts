import { describe, expect, it } from "vitest";

import { cellText } from "./utils";

describe("cellText", () => {
  it("空值统一为空串", () => {
    expect(cellText(null)).toBe("");
    expect(cellText(undefined)).toBe("");
  });

  it("标量按 String 输出", () => {
    expect(cellText("x")).toBe("x");
    expect(cellText(0)).toBe("0");
    expect(cellText(false)).toBe("false");
    expect(cellText(10n)).toBe("10");
  });

  it("对象与数组走 JSON,不渲染 [object Object]", () => {
    expect(cellText({ a: 1 })).toBe('{"a":1}');
    expect(cellText([1, 2])).toBe("[1,2]");
  });
});
