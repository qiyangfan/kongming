/* 按本地日期分组会话，组内保持服务端更新时间倒序。 */

export interface SessionGroup<T> {
  label: string;
  items: T[];
}

interface HasUpdatedAt {
  updated_at: string;
}

const DAY_MS = 24 * 60 * 60 * 1000;

export function groupSessionsByTime<T extends HasUpdatedAt>(
  items: T[],
  now: Date = new Date(),
): SessionGroup<T>[] {
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  // 顺序即优先级:先命中先入前组(今天 ∩ 近 7 天 = 今天)
  const buckets: { label: string; since: number }[] = [
    { label: "今天", since: startOfToday },
    { label: "昨天", since: startOfToday - DAY_MS },
    { label: "近 7 天", since: startOfToday - 6 * DAY_MS },
    { label: "近 30 天", since: startOfToday - 29 * DAY_MS },
    { label: "更早", since: Number.NEGATIVE_INFINITY },
  ];
  const groups: SessionGroup<T>[] = [];
  for (const item of items) {
    const t = new Date(item.updated_at).getTime();
    const bucket = buckets.find((b) => t >= b.since) ?? buckets[buckets.length - 1];
    let g = groups.find((x) => x.label === bucket.label);
    if (!g) {
      g = { label: bucket.label, items: [] };
      groups.push(g);
    }
    g.items.push(item);
  }
  return groups;
}
