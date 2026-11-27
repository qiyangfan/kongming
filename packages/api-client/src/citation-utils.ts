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

/** 追加一条标记片段:正文取完整标记【…】,键取括号内去空白后的内容 */
function pushMarker(
  out: { text: string; key: string | null; start: number; end: number }[],
  m: RegExpExecArray,
): void {
  out.push({
    text: m[0],
    key: (m[1] ?? "").trim(),
    start: m.index,
    end: m.index + m[0].length,
  });
}

/** 追加两个位置之间的正文片段(空区间不追加) */
function pushPlainText(
  out: { text: string; key: string | null; start: number; end: number }[],
  text: string,
  range: { start: number; end: number },
): void {
  if (range.start >= range.end) return;
  const { start, end } = range;
  out.push({ text: text.slice(start, end), key: null, start, end });
}

/** 把正文按【…】标记切段(纯函数;供角标渲染与 vitest 断言) */
export function splitCitationMarkers(
  text: string,
): { text: string; key: string | null; start: number; end: number }[] {
  const out: {
    text: string;
    key: string | null;
    start: number;
    end: number;
  }[] = [];
  const re = /【([^【】]*)】/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    pushPlainText(out, text, { start: last, end: m.index });
    pushMarker(out, m);
    last = m.index + m[0].length;
  }
  pushPlainText(out, text, { start: last, end: text.length });
  return out;
}
