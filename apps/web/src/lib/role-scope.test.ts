import { describe, expect, it } from "vitest";

import { deptIdsForScope, deptScopeError } from "./role-scope";

/* CUSTOM 部门集合的出站口径锚(纯函数;与服务端 system/service 搭配规则同构): */

describe("deptIdsForScope", () => {
  it("CUSTOM 才携带字段", () => {
    expect(deptIdsForScope("CUSTOM", ["d1", "d2"])).toEqual(["d1", "d2"]);
  });

  it("非 CUSTOM 一律不发(undefined=键缺失,JSON 序列化即省略)", () => {
    expect(deptIdsForScope("DEPT", ["d1"])).toBeUndefined();
    expect(deptIdsForScope("ALL", [])).toBeUndefined();
    expect(deptIdsForScope("SELF", [])).toBeUndefined();
    expect(deptIdsForScope("DEPT_AND_BELOW", ["d1"])).toBeUndefined();
  });
});

describe("deptScopeError", () => {
  it("CUSTOM 空集合提交前即拦下", () => {
    expect(deptScopeError("CUSTOM", [])).toBe("自定义范围需至少选择一个部门");
  });

  it("CUSTOM 有集合与非 CUSTOM 均放行", () => {
    expect(deptScopeError("CUSTOM", ["d1"])).toBeNull();
    expect(deptScopeError("DEPT", [])).toBeNull();
  });
});
