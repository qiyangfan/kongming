import { describe, expect, it } from "vitest";

import {
  isCitationKeyShape,
  keySuffix,
  splitCitationMarkers,
} from "@kongming/api-client/citation-utils";

/* 引用键纯函数工具;agent 侧有同逻辑实现(citations.py),两端行为需保持一致 */

describe("isCitationKeyShape", () => {
  it("键形态:前缀-4位大写十六进制", () => {
    expect(isCitationKeyShape("员工手册-A7F2")).toBe(true);
    expect(isCitationKeyShape("手册-A7F2")).toBe(true); // 前缀被改写仍为键形态
    expect(isCitationKeyShape("a-0BAD")).toBe(true);
  });

  it("非键形态:无 `-`/前缀空/末段非4位大写hex", () => {
    expect(isCitationKeyShape("普通文本")).toBe(false);
    expect(isCitationKeyShape("abc-xyz")).toBe(false); // 小写/非hex
    expect(isCitationKeyShape("abc-a7f2")).toBe(false); // 小写hex
    expect(isCitationKeyShape("abc-A7F")).toBe(false); // 3位
    expect(isCitationKeyShape("-A7F2")).toBe(false); // 空前缀
    expect(isCitationKeyShape("K7A4")).toBe(false); // K 非hex
  });
});

describe("keySuffix", () => {
  it("末段 -之后;无 - 返回空串", () => {
    expect(keySuffix("员工手册-A7F2")).toBe("A7F2");
    expect(keySuffix("a-b-c-DEAD")).toBe("DEAD"); // 取最后一个 -
    expect(keySuffix("无横线")).toBe("");
  });
});

describe("splitCitationMarkers", () => {
  it("按【…】标记切段(文本/键成对;无标记整段返回)", () => {
    const parts = splitCitationMarkers("答案【员工手册-A7F2】续");
    expect(parts).toHaveLength(3);
    expect(parts[0]).toMatchObject({ text: "答案", key: null });
    expect(parts[1]).toMatchObject({ key: "员工手册-A7F2" });
    expect(parts[2]).toMatchObject({ text: "续", key: null });
  });

  it("嵌套方括号:字符类互斥——最内完整对成键,其余按文本", () => {
    const parts = splitCitationMarkers("外层【内【层】外】");
    // 正则【([^【】]*)】对【】互斥:首个完整对=【层】,前置文本=「外层【内」
    expect(parts[0]).toMatchObject({ text: "外层【内", key: null });
    expect(parts[1]).toMatchObject({ key: "层" });
    // 成键者不含 【(互斥),后续文本按原样
    expect(parts[parts.length - 1]?.key).toBeNull();
  });
});
