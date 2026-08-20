/* 会话列表时间分组纯函数(010,chat spec「会话持久化」侧边栏场景):
   今天/昨天/近 7 天/近 30 天/更早,组内维持服务端 updated_at 倒序;
   按前端本地日界切分(分组是展示层模糊语义,本地日界即用户直觉,design D5)。 */

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
