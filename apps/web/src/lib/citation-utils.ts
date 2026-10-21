/** 校验引用键并提取后缀；末段必须是 4 位大写十六进制，与 Agent 端使用相同规则。 */

/** 键后缀:末段 `-` 之后的部分(无 `-` 返回空串) */
export function keySuffix(token: string): string {
  const idx = token.lastIndexOf("-");
  return idx < 0 ? "" : token.slice(idx + 1);
}

const SUFFIX_RE = /^[0-9A-F]{4}$/;

/** 键形态判定:含 `-`、前缀非空、末段为 4 位大写十六进制 */
export function isCitationKeyShape(token: string): boolean {
  const idx = token.lastIndexOf("-");
  if (idx <= 0) return false;
  return SUFFIX_RE.test(token.slice(idx + 1));
}

/** 把正文按【…】标记切段(纯函数;供角标渲染与 vitest 断言) */
export function splitCitationMarkers(
  text: string,
): { text: string; key: string | null; start: number; end: number }[] {
  const out: { text: string; key: string | null; start: number; end: number }[] = [];
  const re = /【([^【】]*)】/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) {
      out.push({ text: text.slice(last, m.index), key: null, start: last, end: m.index });
    }
    out.push({
      text: m[0],
      key: m[1].trim(),
      start: m.index,
      end: m.index + m[0].length,
    });
    last = m.index + m[0].length;
  }
  if (last < text.length) {
    out.push({ text: text.slice(last), key: null, start: last, end: text.length });
  }
  return out;
}
