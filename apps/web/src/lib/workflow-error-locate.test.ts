import { describe, expect, it } from "vitest";

import { locateValidationError } from "./workflow-error-locate";

/* 服务端校验错误文案 → 画布节点定位(107)。
   文案全集以 apps/api/kongming/workflow/models.py + service.py 为唯一事实源,
   禁前端臆造;未收录形态一律空定位(文案原样展示,不猜)。 */

describe("locateValidationError 节点定位", () => {
  it("环 → 全部参与节点高亮", () => {
    const r = locateValidationError("定义存在环,参与节点: a, b_2, c");
    expect(r.nodeIds).toEqual(["a", "b_2", "c"]);
    expect(r.configKey).toBeUndefined();
  });

  it("缺必填配置 → 节点 + 配置键", () => {
    const r = locateValidationError("节点 q(sql)缺必填配置 sql");
    expect(r.nodeIds).toEqual(["q"]);
    expect(r.configKey).toBe("sql");
  });

  it("非法 on 值 → 节点 + on 键", () => {
    const r = locateValidationError("节点 t 的 on 仅可为 true/false");
    expect(r.nodeIds).toEqual(["t"]);
    expect(r.configKey).toBe("on");
  });

  it("依赖不存在的节点 → 本节点 + 缺失目标", () => {
    expect(locateValidationError("节点 t 依赖不存在的节点: ghost").nodeIds).toEqual(["t", "ghost"]);
  });

  it("孤立节点 → 该节点", () => {
    expect(locateValidationError("孤立节点(无依赖且无后继): loner").nodeIds).toEqual(["loner"]);
  });

  it("类型非法 / 自依赖 / 重复依赖 → 该节点", () => {
    expect(locateValidationError("节点 x 类型非法: py(合法: sql, http)").nodeIds).toEqual(["x"]);
    expect(locateValidationError("节点 x 自依赖").nodeIds).toEqual(["x"]);
    expect(locateValidationError("节点 x 存在重复依赖项").nodeIds).toEqual(["x"]);
  });

  it("id 形态类错误(节点 id 前缀先行,不误捕字面 id)", () => {
    expect(locateValidationError("节点 id 重复: a").nodeIds).toEqual(["a"]);
    expect(locateValidationError("节点 id 形态非法(仅字母/数字/下划线): bad id").nodeIds).toEqual([
      "bad id",
    ]);
    expect(locateValidationError("节点 id 撞保留名/外层节点: inputs").nodeIds).toEqual(["inputs"]);
  });

  it("非节点形态错误(wf_key/名称/nodes 数组)→ 空定位", () => {
    for (const msg of [
      "wf_key 须为小写字母开头的字母/数字/下划线(2-64 位)",
      "名称非空且 ≤128 字符",
      "definition.nodes 必须是非空数组",
      "nodes[0] 缺 id",
      "节点数超上限 100(当前 120)",
    ]) {
      expect(locateValidationError(msg).nodeIds).toEqual([]);
    }
  });

  it("未知文案 → 空定位且原文透传", () => {
    const r = locateValidationError("某种未收录的错误");
    expect(r.nodeIds).toEqual([]);
    expect(r.message).toBe("某种未收录的错误");
  });
});
