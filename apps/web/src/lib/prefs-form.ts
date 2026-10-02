/* 偏好表单纯逻辑(087):槽位草稿 ↔ PUT 补丁的确定性转换。
   服务端语义(D1):省略=保留、显式 null=删除、空串/空列表≠删除;
   本模块负责把「表单里清空了某槽位」翻译成显式 null(删除指令),
   并只提交发生变化的键(最小补丁,审计只记受影响槽位)。 */

export type PrefsMap = Record<string, unknown>;

export type PrefsDraft = Record<string, string | string[]>;

export interface PrefSlotMeta {
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

/**
 * 草稿 → PUT 补丁:只含变化键。
 * - 字符串槽位:填了新值 → 提交 trim 后值;清空且服务端原有值 → null(删除);
 *   清空且服务端本无值 → 省略(空补丁语义,非删除)。
 * - 列表槽位:增删改 → 提交整表;清到空且服务端原非空 → null(删除)。
 * - 服务端已有但表单外修改产生的差异不在本函数职责(保存后以响应重播草稿)。
 */
export function buildPrefsPatch(server: PrefsMap, draft: PrefsDraft): PrefsMap {
  const patch: PrefsMap = {};
  for (const slot of PREF_SLOTS) {
    const val = draft[slot.key];
    if (slot.list) {
      const next = Array.isArray(val) ? val.map((x) => String(x).trim()).filter(Boolean) : [];
      const prev = serverList(server, slot.key);
      const changed = next.length !== prev.length || next.some((x, i) => x !== prev[i]);
      if (!changed) continue;
      patch[slot.key] = next.length > 0 ? next : null;
    } else {
      const next = (typeof val === "string" ? val : "").trim();
      const prev = serverString(server, slot.key);
      if (next === prev) continue;
      // 走到这里 next 与 prev 必不同:清空且原有值 → 删除(null);否则新值
      patch[slot.key] = next === "" ? null : next;
    }
  }
  return patch;
}

/** 补丁是否为空(无键)——空补丁提交前应拦下(服务端为真无操作) */
export function isEmptyPatch(patch: PrefsMap): boolean {
  return Object.keys(patch).length === 0;
}

/** 单项清除指令:{槽位: null} */
export function singleSlotDelete(key: string): PrefsMap {
  return { [key]: null };
}
