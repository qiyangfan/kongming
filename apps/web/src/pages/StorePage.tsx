/* Bot 商店页(033 bot-store spec):关键词搜索+排序切换+Bot 卡片
 (名称/greeting/评分星/使用量/收藏心)+详情对话框(suggestions 点击即问)
 +收藏/评分交互。主布局导航项(非管理子页)。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Heart, Loader2, Play, Search, Star } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { EmptyState } from "@/components/empty-state";
import { ErrorAlert } from "@/components/error-alert";
import {
  addFavorite,
  getStoreBot,
  listFavorites,
  listStoreBots,
  rateBot,
  removeFavorite,
  runStoreBot,
  type StoreBotDetail,
  type StoreBotItem,
} from "@/lib/store-api";

function Stars({ value }: { value: number }) {
  return (
    <span className="flex items-center gap-0.5 text-xs text-muted-foreground">
      <Star className={`size-3.5 ${value >= 3 ? "fill-primary text-primary" : ""}`} aria-hidden />
      {value > 0 ? value.toFixed(1) : "—"}
    </span>
  );
}

export default function StorePage() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [keyword, setKeyword] = useState("");
  const [sort, setSort] = useState("new");
  const [detailKey, setDetailKey] = useState<string | null>(null);
  const [answer, setAnswer] = useState<Record<string, string>>({});
  const [askBusy, setAskBusy] = useState("");

  const [tab, setTab] = useState<"all" | "fav">("all");
  const bots = useQuery({
    queryKey: ["store", keyword, sort],
    queryFn: () => listStoreBots(keyword, sort),
    enabled: tab === "all",
  });
  const favorites = useQuery({
    queryKey: ["store", "favorites"],
    queryFn: listFavorites,
    enabled: tab === "fav",
  });
  const detail = useQuery({
    queryKey: ["store", "detail", detailKey],
    queryFn: () => getStoreBot(detailKey!),
    enabled: detailKey !== null,
  });

  const fav = useMutation({
    mutationFn: ({ key, favorited }: { key: string; favorited: boolean }) =>
      favorited ? addFavorite(key) : removeFavorite(key),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["store"] });
    },
  });

  const rate = useMutation({
    mutationFn: ({ key, score }: { key: string; score: number }) => rateBot(key, score),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["store"] });
    },
  });

  async function ask(bot: StoreBotItem | StoreBotDetail, question: string): Promise<void> {
    if (askBusy) return;
    setAskBusy(bot.id);
    try {
      const out = await runStoreBot(bot.bot_key, question);
      // R1-S2:直接展示应答正文(无会话不产生「去看对话」语义)
      setAnswer((m) => ({
        ...m,
        [bot.id]: out.success ? out.summary || "(助手未返回内容)" : out.error || "运行失败",
      }));
    } catch (e) {
      setAnswer((m) => ({
        ...m,
        [bot.id]: e instanceof Error ? e.message : "运行失败",
      }));
    } finally {
      setAskBusy("");
    }
  }

  return (
    <div className="space-y-4 px-6 py-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">助手商店</h1>
        <div className="flex flex-wrap items-center gap-2">
          <form
            className="flex items-center gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              setKeyword(q.trim());
            }}
          >
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索助手…"
              className="w-56"
              aria-label="搜索商店"
            />
            <Button variant="outline" size="icon" type="submit" aria-label="搜索">
              <Search aria-hidden />
            </Button>
          </form>
          <ToggleGroup
            type="single"
            value={tab}
            onValueChange={(v) => v && setTab(v as "all" | "fav")}
            variant="outline"
            size="sm"
            aria-label="货架范围"
          >
            <ToggleGroupItem value="all">全部</ToggleGroupItem>
            <ToggleGroupItem value="fav">我的收藏</ToggleGroupItem>
          </ToggleGroup>
          <ToggleGroup
            type="single"
            value={sort}
            onValueChange={(v) => v && setSort(v)}
            variant="outline"
            size="sm"
            aria-label="排序"
          >
            <ToggleGroupItem value="hot">热门</ToggleGroupItem>
            <ToggleGroupItem value="new">最新</ToggleGroupItem>
            <ToggleGroupItem value="rating">评分</ToggleGroupItem>
          </ToggleGroup>
        </div>
      </div>

      {(tab === "all" ? bots.isError : favorites.isError) ? (
        <ErrorAlert />
      ) : (tab === "all" ? bots.isLoading : favorites.isLoading) ? (
        <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden />
      ) : ((tab === "all" ? bots.data : favorites.data) ?? []).length === 0 ? (
        <EmptyState>
          {tab === "all" ? "货架空空——上架第一位助手吧。" : "还没有收藏——点心形即可。"}
        </EmptyState>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {((tab === "all" ? bots.data : favorites.data) ?? []).map((b) => (
            <div key={b.id} className="space-y-2 rounded-lg border border-border p-4">
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
                  onClick={() => fav.mutate({ key: b.bot_key, favorited: !b.favorited })}
                >
                  <Heart
                    className={b.favorited ? "fill-primary text-primary" : undefined}
                    aria-hidden
                  />
                </Button>
              </div>
              {b.greeting && (
                <p className="line-clamp-2 text-sm text-muted-foreground">{b.greeting}</p>
              )}
              <div className="flex items-center gap-3 text-xs text-muted-foreground">
                <Stars value={b.avg_score} />
                <span>({b.rating_count})</span>
                <span>{b.use_count} 次使用</span>
                <span>{b.fav_count} 收藏</span>
              </div>
              <div className="flex items-center gap-2 pt-1">
                <Button size="sm" variant="outline" onClick={() => setDetailKey(b.bot_key)}>
                  详情
                </Button>
                {[1, 2, 3, 4, 5].map((n) => (
                  <Button
                    key={n}
                    variant="ghost"
                    size="icon-xs"
                    className="text-muted-foreground hover:text-primary"
                    aria-label={`评 ${n} 星`}
                    onClick={() => rate.mutate({ key: b.bot_key, score: n })}
                  >
                    <Star
                      className={`size-3.5 ${n <= Math.round(b.avg_score) ? "fill-primary text-primary" : ""}`}
                      aria-hidden
                    />
                  </Button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 详情对话框 */}
      <Dialog open={detailKey !== null} onOpenChange={(o) => !o && setDetailKey(null)}>
        <DialogContent className="max-w-lg">
          {/* R1-B4:DialogHeader 恒渲染(加载/错误态也有 Title,读屏可识别对话框) */}
          <DialogHeader>
            <DialogTitle>{detail.data?.name ?? "助手详情"}</DialogTitle>
            <DialogDescription className="font-mono text-xs">
              {detail.data ? detail.data.bot_key : detailKey}
            </DialogDescription>
          </DialogHeader>
          {detail.isError && <ErrorAlert />}
          {detail.data && (
            <>
              <div className="space-y-3">
                {detail.data.greeting && <p className="text-sm">{detail.data.greeting}</p>}
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  <Stars value={detail.data.avg_score} />
                  <span>{detail.data.rating_count} 人评</span>
                  <span>{detail.data.use_count} 次使用</span>
                  <span>{detail.data.fav_count} 收藏</span>
                  {detail.data.my_rating && (
                    <Badge variant="secondary">我评 {detail.data.my_rating} 星</Badge>
                  )}
                </div>
                {/* 104:当前上架版本号与上架时间(纯增字段;商店语义=消费该版本快照) */}
                {detail.data.version_no !== null && detail.data.version_no !== undefined && (
                  <p className="text-xs text-muted-foreground">
                    上架版本 v{detail.data.version_no}
                    {detail.data.version_published_at
                      ? ` · ${new Date(detail.data.version_published_at).toLocaleString()} 上架`
                      : ""}
                  </p>
                )}
                {(detail.data.tools ?? []).length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {detail.data.tools.map((t) => (
                      <Badge key={t} variant="outline" className="font-mono">
                        {t}
                      </Badge>
                    ))}
                  </div>
                )}
                {(detail.data.suggestions ?? []).length > 0 && (
                  <div className="space-y-1.5">
                    <p className="text-xs text-muted-foreground">建议问题(点击即问):</p>
                    {detail.data.suggestions.map((sug) => (
                      <Button
                        key={sug}
                        variant="outline"
                        size="sm"
                        className="mr-1.5"
                        disabled={askBusy === detail.data!.id}
                        onClick={() => void ask(detail.data!, sug)}
                      >
                        {askBusy === detail.data!.id ? (
                          <Loader2 className="animate-spin" aria-hidden />
                        ) : (
                          <Play aria-hidden />
                        )}
                        {sug}
                      </Button>
                    ))}
                  </div>
                )}
                {answer[detail.data.id] && (
                  <p role="status" className="text-sm text-muted-foreground">
                    {answer[detail.data.id]}
                  </p>
                )}
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
