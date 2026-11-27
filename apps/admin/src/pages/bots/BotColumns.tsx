/* 助手列表的列定义:名称/工具/数据源/启停/公开。 */

import { Bot } from "lucide-react";

import { Badge } from "@kongming/ui/badge";
import type { DataViewColumn } from "@kongming/ui/data-view";
import { Switch } from "@kongming/ui/switch";
import type { TeamItem } from "@kongming/api-client/team-api";
import type { BotItem } from "./bot-types";

/** 构造助手表格列;draftDiffers 判定「有未上架修改」徽章。 */
export function botColumns(ctx: {
  teams: TeamItem[];
  patchPending: boolean;
  onPatch: (id: string, fields: Record<string, unknown>) => void;
  draftDiffers: (b: BotItem) => boolean;
}): DataViewColumn<BotItem>[] {
  const teamName = (id: string) => ctx.teams.find((t) => t.id === id)?.name ?? "团队";
  return [
    {
      key: "name",
      header: "助手",
      cardPriority: 1,
      render: (b) => (
        <span className="flex items-center gap-1.5">
          <Bot className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <span className="max-w-40 truncate font-medium" title={b.name}>
            {b.name}
          </span>
          <span
            className="max-w-28 truncate font-mono text-xs text-muted-foreground"
            title={b.bot_key}
          >
            {b.bot_key}
          </span>
          {b.team_id && (
            <Badge variant="outline" className="max-w-32" title={teamName(b.team_id)}>
              <span className="truncate">{teamName(b.team_id)}</span>
            </Badge>
          )}
          {/* 草稿与在架版本不一致提示(分离是特性,提示防遗忘) */}
          {b.published_at !== null && ctx.draftDiffers(b) && (
            <Badge variant="warning" className="shrink-0">
              有未上架修改
            </Badge>
          )}
        </span>
      ),
    },
    {
      key: "tools",
      header: "工具",
      cardPriority: 4,
      render: (b) => (
        <span
          className="block max-w-56 truncate font-mono text-xs"
          title={b.tools.length ? b.tools.join(", ") : undefined}
        >
          {b.tools.length ? b.tools.join(", ") : "—"}
        </span>
      ),
    },
    {
      key: "datasource_key",
      header: "数据源",
      render: (b) => (
        <span className="block max-w-32 truncate text-xs" title={b.datasource_key ?? undefined}>
          {b.datasource_key}
        </span>
      ),
    },
    {
      key: "enabled",
      header: "启用",
      cardPriority: 2,
      render: (b) => (
        <Switch
          checked={b.enabled}
          disabled={ctx.patchPending}
          onCheckedChange={(on) => ctx.onPatch(b.id, { enabled: on })}
          aria-label={`启用 ${b.name}`}
        />
      ),
    },
    {
      key: "is_public",
      header: "公开",
      cardPriority: 3,
      render: (b) => (
        <span className="flex items-center gap-1.5">
          <Switch
            checked={b.is_public}
            disabled={ctx.patchPending}
            onCheckedChange={(on) => ctx.onPatch(b.id, { is_public: on })}
            aria-label={`公开 ${b.name}`}
          />
          {b.is_public && (
            <Badge
              variant="outline"
              className="max-w-28 font-mono text-xs"
              title={`/bot/${b.bot_key}`}
            >
              <span className="truncate">/bot/{b.bot_key}</span>
            </Badge>
          )}
        </span>
      ),
    },
  ];
}
