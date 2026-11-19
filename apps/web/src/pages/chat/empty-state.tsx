/* 新建会话的品牌空态与冷启动建议问题。 */

import { Feather } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

export function EmptyState({
  items,
  loading,
  onAsk,
}: {
  items: string[];
  loading: boolean;
  onAsk: (q: string) => void;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-5 px-6 text-center">
      <div className="space-y-3">
        <span className="flex size-14 items-center justify-center rounded-2xl bg-primary mx-auto">
          <Feather className="size-7 text-primary-foreground" aria-hidden />
        </span>
        <h2 className="text-xl font-semibold tracking-widest text-foreground">万事,问孔明。</h2>
        <p className="max-w-sm text-sm leading-relaxed text-muted-foreground">
          数据之事,直问即可。问库存、问销量、问趋势—— 运筹帷幄之事,交给军师。
        </p>
      </div>
      {/* 冷启动建议:加载骨架/空清单或失败静默隐藏(不阻塞品牌空态) */}
      {loading && items.length === 0 && (
        <div className="flex max-w-lg flex-wrap justify-center gap-2" aria-hidden>
          <Skeleton className="h-8 w-44 rounded-full" />
          <Skeleton className="h-8 w-36 rounded-full" />
          <Skeleton className="h-8 w-40 rounded-full" />
        </div>
      )}
      {items.length > 0 && (
        <div className="flex max-w-lg flex-wrap justify-center gap-2">
          {items.map((q) => (
            <Button
              key={q}
              variant="outline"
              size="sm"
              className="rounded-full font-normal text-muted-foreground"
              onClick={() => onAsk(q)}
            >
              {q}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}
