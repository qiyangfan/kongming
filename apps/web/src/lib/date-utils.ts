/** 日期工具(共享单源) */

/**
 * 结束日期 → 排他上界(次日零点)。后端 `created_at < :end` 为排他语义,
 * 直接取当日零点会把结束日全天排除(off-by-one)。
 */
export function endExclusive(date: string): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString();
}
