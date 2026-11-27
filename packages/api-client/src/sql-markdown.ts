/** 把 SQL 包成不会被内容中的反引号提前闭合的 Markdown 围栏。 */
export function fencedSql(sql: string): string {
  const longest = [...sql.matchAll(/`+/g)].reduce(
    (max, match) => Math.max(max, match[0].length),
    0,
  );
  const fence = "`".repeat(Math.max(3, longest + 1));
  return `${fence}sql\n${sql}\n${fence}`;
}
