/* 商店货架卡片与评分星标。 */

import { Heart, Star } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { StoreBotItem } from "@/lib/store-api";

/** 平均分星标:≥3 分实心主色,0 分显示占位。 */
export function Stars({ value }: { value: number }) {
  return (
    <span className="flex items-center gap-0.5 text-xs text-muted-foreground">
      <Star className={`size-3.5 ${value >= 3 ? "fill-primary text-primary" : ""}`} aria-hidden />
      {value > 0 ? value.toFixed(1) : "—"}
    </span>
  );
}

interface StoreBotCardProps {
  bot: StoreBotItem;
  onToggleFavorite: () => void;
  onDetail: () => void;
  onRate: (score: number) => void;
}

/** 货架卡片:名称/标识、收藏、简介、评分与使用统计、详情与打分入口。 */
export function StoreBotCard({ bot: b, onToggleFavorite, onDetail, onRate }: StoreBotCardProps) {
  return (
    <div className="space-y-2 rounded-lg border border-border p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="font-medium">{b.name}</p>
          <p className="font-mono text-xs text-muted-foreground">{b.bot_key}</p>
        </div>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={`收藏 ${b.name}`}
          aria-pressed={b.favorited}
          onClick={onToggleFavorite}
        >
          <Heart className={b.favorited ? "fill-primary text-primary" : undefined} aria-hidden />
        </Button>
      </div>
      {b.greeting && <p className="line-clamp-2 text-sm text-muted-foreground">{b.greeting}</p>}
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <Stars value={b.avg_score} />
        <span>({b.rating_count})</span>
        <span>{b.use_count} 次使用</span>
        <span>{b.fav_count} 收藏</span>
      </div>
      <div className="flex items-center gap-2 pt-1">
        <Button size="sm" variant="outline" onClick={onDetail}>
          详情
        </Button>
        {[1, 2, 3, 4, 5].map((n) => (
          <Button
            key={n}
            variant="ghost"
            size="icon-xs"
            className="text-muted-foreground hover:text-primary"
            aria-label={`评 ${n} 星`}
            onClick={() => onRate(n)}
          >
            <Star
              className={`size-3.5 ${n <= Math.round(b.avg_score) ? "fill-primary text-primary" : ""}`}
              aria-hidden
            />
          </Button>
        ))}
      </div>
    </div>
  );
}
