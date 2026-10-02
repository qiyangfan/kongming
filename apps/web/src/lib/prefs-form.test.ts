import { describe, expect, it } from "vitest";

import {
  PREF_SLOTS,
  buildPrefsPatch,
  draftFromPrefs,
  isEmptyPatch,
  singleSlotDelete,
} from "./prefs-form";

describe("draftFromPrefs", () => {
  it("未设置槽位取空形态;列表槽位数组化", () => {
    const draft = draftFromPrefs({ language: "zh", metric_aliases: ["GMV=成交额"] });
    expect(draft.language).toBe("zh");
    expect(draft.metric_aliases).toEqual(["GMV=成交额"]);
    expect(draft.timezone).toBe("");
    expect(draft.chart_pref).toBe("");
  });

  it("非常规值回落空形态(契约 map 值为 unknown)", () => {
    const draft = draftFromPrefs({ language: 42, timezone: null });
    expect(draft.language).toBe("");
    expect(draft.timezone).toBe("");
  });

  it("草稿覆盖全部七个槽位(与服务端闭合集合同序)", () => {
    const draft = draftFromPrefs({});
    expect(Object.keys(draft).sort()).toEqual(PREF_SLOTS.map((s) => s.key).sort());
  });
});

describe("buildPrefsPatch 表单语义", () => {
  it("未变化的键省略(最小补丁)", () => {
    const server = { language: "zh", timezone: "UTC" };
    const patch = buildPrefsPatch(server, draftFromPrefs(server));
    expect(isEmptyPatch(patch)).toBe(true);
  });

  it("修改字符串槽位提交 trim 后新值", () => {
    const server = { language: "zh" };
    const patch = buildPrefsPatch(server, { ...draftFromPrefs(server), language: " en " });
    expect(patch).toEqual({ language: "en" });
  });

  it("清空原有字符串槽位=显式 null 删除", () => {
    const server = { language: "zh" };
    const patch = buildPrefsPatch(server, { ...draftFromPrefs(server), language: "" });
    expect(patch).toEqual({ language: null });
  });

  it("清空本就不存在的槽位不产生删除指令(空串≠删除)", () => {
    const patch = buildPrefsPatch({}, draftFromPrefs({}));
    expect(isEmptyPatch(patch)).toBe(true);
  });

  it("列表槽位增删改提交整表", () => {
    const server = { metric_aliases: ["GMV=成交额"] };
    const draft = draftFromPrefs(server);
    (draft.metric_aliases as string[]).push("AOV=客单价");
    expect(buildPrefsPatch(server, draft)).toEqual({
      metric_aliases: ["GMV=成交额", "AOV=客单价"],
    });
  });

  it("列表清到空且原非空=显式 null 删除", () => {
    const server = { metric_aliases: ["GMV=成交额"] };
    const patch = buildPrefsPatch(server, { ...draftFromPrefs(server), metric_aliases: [] });
    expect(patch).toEqual({ metric_aliases: null });
  });

  it("混变更只含变化键", () => {
    const server = { language: "zh", timezone: "UTC", chart_pref: "bar" };
    const draft = draftFromPrefs(server);
    draft.language = "en";
    draft.chart_pref = "";
    expect(buildPrefsPatch(server, draft)).toEqual({ language: "en", chart_pref: null });
  });
});

describe("singleSlotDelete", () => {
  it("单项清除发送 null 指令", () => {
    expect(singleSlotDelete("timezone")).toEqual({ timezone: null });
  });
});
