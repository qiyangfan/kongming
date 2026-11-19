/* 管理 Bot 的创建、编辑、启停、公开状态和版本发布。新建 API key 时明文仅展示一次。 */

import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DataView } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import { PageHeader } from "@/components/page-header";
import type { components } from "@/lib/api-types.gen";

import { AttachDialog } from "./bots/AttachDialog";
import { botColumns } from "./bots/BotColumns";
import { CreateBotDialog } from "./bots/CreateBotDialog";
import { KeyShownDialog } from "./bots/KeyShownDialog";
import { PublishDialog } from "./bots/PublishDialog";
import { useBotsPageState } from "./bots/use-bots-page-state";
import { RollbackDialog, VersionHistoryDialog } from "./bots/VersionDialogs";

export type BotItem = components["schemas"]["BotOut"];

export default function BotsPage() {
  const s = useBotsPageState();

  return (
    <div className="space-y-4">
      <PageHeader
        title="助手"
        description="创建自定义助手:设定人设与守则、勾选工具、绑定数据源;可上架商店,或以 API 供外部系统调用。"
        actions={
          <Button size="sm" onClick={() => s.setCreating(true)}>
            <Plus className="size-3.5" aria-hidden />
            新建助手
          </Button>
        }
      />

      {s.error && (
        <p className="text-xs text-destructive" role="alert">
          {s.error}
        </p>
      )}

      {s.bots.isError && <ErrorAlert />}

      <DataView
        rows={s.bots.data ?? []}
        rowKey={(b) => b.id}
        isLoading={s.bots.isPending}
        isEmpty={!s.bots.isPending && !s.bots.isError && (s.bots.data ?? []).length === 0}
        emptyText="暂无助手——新建一个专属助手"
        columns={botColumns({
          teams: s.myTeams.data ?? [],
          patchPending: s.patch.isPending,
          onPatch: (id, fields) => s.patch.mutate({ id, fields }),
          draftDiffers: s.draftDiffers,
        })}
        actions={[
          {
            label: "创建密钥",
            onSelect: (b) => {
              if (!s.createKey.isPending) s.createKey.mutate(b.id);
            },
          },
          {
            label: "归属",
            onSelect: (b) => {
              s.setAttaching(b.id);
              s.setAttachError("");
            },
          },
          {
            // 已上架和已下架的版本都可查看;回滚在历史视图中操作。
            label: "版本历史",
            onSelect: (b) => {
              s.setRollbackError("");
              s.setVerTarget(b.id);
            },
          },
          {
            // 草稿与在架版本不一致时的重新上架入口(预填当前版本商店内容)
            label: "重新上架",
            isVisible: (b) => b.published_at !== null && s.draftDiffers(b),
            onSelect: (b) => {
              const cur = s.versionsOf(b.id).find((v) => v.current);
              s.setPubGreeting(cur?.snapshot.greeting ?? "");
              s.setPubSuggestions(cur?.snapshot.suggestions.join("\n") ?? "");
              s.setPubTarget(b.id);
            },
          },
          {
            label: (b) => (b.published_at !== null ? "下架" : "上架"),
            onSelect: (b) => {
              if (s.publish.isPending) return;
              if (b.published_at !== null) {
                s.publish.mutate({ id: b.id, published: true });
              } else {
                s.setPubGreeting("");
                s.setPubSuggestions("");
                s.setPubTarget(b.id);
              }
            },
          },
        ]}
      />

      <CreateBotDialog
        open={s.creating}
        onOpenChange={s.setCreating}
        form={s.form}
        onFormChange={s.setForm}
        error={s.error}
        registry={s.registry}
        pending={s.create.isPending}
        onSubmit={() => s.create.mutate()}
      />

      <KeyShownDialog apiKey={s.keyShown} onClose={() => s.setKeyShown(null)} />

      <AttachDialog
        open={s.attaching !== null}
        team={s.attachTeam}
        onTeamChange={s.setAttachTeam}
        teams={s.myTeams.data ?? []}
        error={s.attachError}
        pending={s.attach.isPending}
        onSubmit={() => s.attach.mutate()}
        onClose={() => s.setAttaching(null)}
      />

      <PublishDialog
        open={s.pubTarget !== null}
        greeting={s.pubGreeting}
        onGreetingChange={s.setPubGreeting}
        suggestions={s.pubSuggestions}
        onSuggestionsChange={s.setPubSuggestions}
        error={s.publish.isError ? (s.publish.error as Error).message : ""}
        pending={s.publish.isPending}
        onSubmit={() => s.pubTarget && s.publish.mutate({ id: s.pubTarget, published: false })}
        onClose={() => s.setPubTarget(null)}
      />

      <VersionHistoryDialog
        open={s.verTarget !== null}
        versions={s.verList.data ?? []}
        isPending={s.verList.isPending}
        isError={s.verList.isError}
        rollbackPending={s.rollback.isPending}
        onRollback={s.setRollbackNo}
        onClose={() => s.setVerTarget(null)}
      />

      <RollbackDialog
        versionNo={s.rollbackNo}
        error={s.rollbackError}
        pending={s.rollback.isPending}
        onConfirm={() => {
          if (s.verTarget && s.rollbackNo !== null) {
            s.rollback.mutate({ id: s.verTarget, no: s.rollbackNo });
          }
        }}
        onClose={() => s.setRollbackNo(null)}
      />
    </div>
  );
}
