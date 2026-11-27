/* Bot 商店支持搜索、排序、查看详情、收藏和评分。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Search } from "lucide-react";

import { Button } from "@kongming/ui/button";
import { Input } from "@kongming/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@kongming/ui/toggle-group";
import { EmptyState } from "@kongming/ui/empty-state";
import { ErrorAlert } from "@kongming/ui/error-alert";
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
} from "@kongming/api-client/store-api";

import { StoreBotCard } from "./store-bot-card";
import { StoreDetailDialog } from "./store-detail-dialog";

type ShelfTab = "all" | "fav";

/** 货架主体:首次加载/空态/卡片网格。 */
function Shelf({
  tab,
  items,
  isError,
  isLoading,
  onToggleFavorite,
  onDetail,
  onRate,
}: {
  tab: ShelfTab;
  items: StoreBotItem[] | undefined;
  isError: boolean;
  isLoading: boolean;
  onToggleFavorite: (bot: StoreBotItem) => void;
  onDetail: (bot: StoreBotItem) => void;
  onRate: (bot: StoreBotItem, score: number) => void;
}) {
  if (isError) return <ErrorAlert />;
  if (isLoading) {
    return <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden />;
  }
  if ((items ?? []).length === 0) {
    return (
      <EmptyState>
        {tab === "all" ? "货架空空——上架第一位助手吧。" : "还没有收藏——点心形即可。"}
      </EmptyState>
    );
  }
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {(items ?? []).map((b) => (
        <StoreBotCard
          key={b.id}
          bot={b}
          onToggleFavorite={() => onToggleFavorite(b)}
          onDetail={() => onDetail(b)}
          onRate={(score) => onRate(b, score)}
        />
      ))}
    </div>
  );
}

/** 货架筛选项:搜索框、范围与排序切换。 */
function ShelfControls({
  q,
  setQ,
  tab,
  setTab,
  sort,
  setSort,
  onSearch,
}: {
  q: string;
  setQ: (value: string) => void;
  tab: ShelfTab;
  setTab: (tab: ShelfTab) => void;
  sort: string;
  setSort: (sort: string) => void;
  onSearch: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h1 className="text-lg font-semibold">助手商店</h1>
      <div className="flex flex-wrap items-center gap-2">
        <form
          className="flex items-center gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            onSearch();
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
          onValueChange={(v) => v && setTab(v as ShelfTab)}
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
  const [tab, setTab] = useState<ShelfTab>("all");

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

  const invalidateStore = () => {
    void qc.invalidateQueries({ queryKey: ["store"] });
  };

  const fav = useMutation({
    mutationFn: ({ key, favorited }: { key: string; favorited: boolean }) =>
      favorited ? addFavorite(key) : removeFavorite(key),
    onSuccess: invalidateStore,
  });

  const rate = useMutation({
    mutationFn: ({ key, score }: { key: string; score: number }) => rateBot(key, score),
    onSuccess: invalidateStore,
  });

  /** 直接展示应答正文(无会话不产生「去看对话」语义)。 */
  async function ask(bot: StoreBotItem | StoreBotDetail, question: string): Promise<void> {
    if (askBusy) return;
    setAskBusy(bot.id);
    try {
      const out = await runStoreBot(bot.bot_key, question);
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

  const shelf = tab === "all" ? bots : favorites;
  const toggleFavorite = (b: StoreBotItem) =>
    fav.mutate({ key: b.bot_key, favorited: !b.favorited });

  return (
    <div className="space-y-4 px-6 py-5">
      <ShelfControls
        q={q}
        setQ={setQ}
        tab={tab}
        setTab={setTab}
        sort={sort}
        setSort={setSort}
        onSearch={() => setKeyword(q.trim())}
      />

      <Shelf
        tab={tab}
        items={tab === "all" ? bots.data : favorites.data}
        isError={shelf.isError}
        isLoading={shelf.isLoading}
        onToggleFavorite={toggleFavorite}
        onDetail={(b) => setDetailKey(b.bot_key)}
        onRate={(b, score) => rate.mutate({ key: b.bot_key, score })}
      />

      <StoreDetailDialog
        open={detailKey !== null}
        onOpenChange={(o) => !o && setDetailKey(null)}
        detailKey={detailKey}
        detail={detail}
        askBusy={askBusy}
        answer={answer}
        onAsk={(bot, question) => void ask(bot, question)}
      />
    </div>
  );
}
