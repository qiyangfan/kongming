/* 表单草稿 → 偏好补丁:只提交相对服务端变化的键;null 表示删除,空串/空列表是有效值。 */

export type PrefsMap = Record<string, unknown>;

export type PrefsDraft = Record<string, string | string[]>;

interface PrefSlotMeta {
  key: string;
  label: string;
  list: boolean;
}

/** 七个闭合槽位的呈现序与中文标签(与服务端 SLOT_KEYS 同序) */
export const PREF_SLOTS: PrefSlotMeta[] = [
  { key: "preferred_datasource", label: "默认数据源", list: false },
  { key: "default_time_range", label: "默认时间范围", list: false },
  { key: "metric_aliases", label: "指标别名", list: true },
  { key: "chart_pref", label: "图表偏好", list: false },
  { key: "language", label: "语言", list: false },
  { key: "timezone", label: "时区", list: false },
  { key: "display_style", label: "表达风格", list: false },
];

/** 服务端偏好 → 表单草稿(未设置槽位 = 空串/空列表) */
export function draftFromPrefs(prefs: PrefsMap): PrefsDraft {
  const draft: PrefsDraft = {};
  for (const slot of PREF_SLOTS) {
    const val = prefs[slot.key];
    if (slot.list) {
      draft[slot.key] = Array.isArray(val) ? val.map((x) => String(x)) : [];
    } else {
      draft[slot.key] = typeof val === "string" && val !== "" ? val : "";
    }
  }
  return draft;
}

function serverString(prefs: PrefsMap, key: string): string {
  const val = prefs[key];
  return typeof val === "string" ? val : "";
}

function serverList(prefs: PrefsMap, key: string): string[] {
  const val = prefs[key];
  return Array.isArray(val) ? val.map((x) => String(x)) : [];
}

/** 草稿中的列表值规整:去首尾空白并丢弃空项 */
function draftList(val: string | string[] | undefined): string[] {
  return Array.isArray(val) ? val.map((x) => String(x).trim()).filter(Boolean) : [];
}

/** 列表槽位的补丁值:未变化返回 undefined,清空返回 null */
function listSlotPatch(
  server: PrefsMap,
  slot: PrefSlotMeta,
  draft: PrefsDraft,
): string[] | null | undefined {
  const next = draftList(draft[slot.key]);
  const prev = serverList(server, slot.key);
  const changed = next.length !== prev.length || next.some((x, i) => x !== prev[i]);
  if (!changed) return undefined;
  return next.length > 0 ? next : null;
}

/** 单值槽位的补丁值:未变化返回 undefined,清空返回 null */
function textSlotPatch(
  server: PrefsMap,
  slot: PrefSlotMeta,
  draft: PrefsDraft,
): string | null | undefined {
  const val = draft[slot.key];
  const next = (typeof val === "string" ? val : "").trim();
  if (next === serverString(server, slot.key)) return undefined;
  return next === "" ? null : next;
}

/** 只提交相对服务端值发生变化的字段;清空已有值提交 null(删除),未变化字段省略。 */
export function buildPrefsPatch(server: PrefsMap, draft: PrefsDraft): PrefsMap {
  const patch: PrefsMap = {};
  for (const slot of PREF_SLOTS) {
    const value = slot.list
      ? listSlotPatch(server, slot, draft)
      : textSlotPatch(server, slot, draft);
    if (value !== undefined) patch[slot.key] = value;
  }
  return patch;
}

/** 补丁是否无键;空补丁应拦下,不发无操作的请求。 */
export function isEmptyPatch(patch: PrefsMap): boolean {
  return Object.keys(patch).length === 0;
}

/** 单项清除指令:{槽位: null} */
export function singleSlotDelete(key: string): PrefsMap {
  return { [key]: null };
}
