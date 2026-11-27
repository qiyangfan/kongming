/* 管理当前租户的记忆和用户偏好。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Download } from "lucide-react";

import { Button } from "@kongming/ui/button";
import { DataView } from "@kongming/ui/data-view";
import { ErrorAlert } from "@kongming/ui/error-alert";
import { api } from "@kongming/api-client/api";
import { listMyTeams } from "@kongming/api-client/team-api";

import {
  type MemoryItem,
  EditMemoryDialog,
  RemoveMemoryDialog,
  exportMemories,
  memoryColumns,
} from "./memory-page-shared";
import { PrefsSection, TeamMemoryWrite } from "./memory-page-sections";

export default function MemoryPage() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<MemoryItem | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editCategory, setEditCategory] = useState("general");
  const [removing, setRemoving] = useState<MemoryItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 根据 team_id 显示团队名，复用已有团队清单查询。
  const myTeams = useQuery({ queryKey: ["admin", "teams"], queryFn: listMyTeams });
  const teamName = (id: string | null | undefined) =>
    id ? ((myTeams.data ?? []).find((t) => t.id === id)?.name ?? "团队") : null;

  const list = useQuery({
    queryKey: ["memory"],
    queryFn: () => api.get<{ items: MemoryItem[] }>("/memory"),
  });

  const update = useMutation({
    mutationFn: (input: { id: string; content: string; category: string }) =>
      api.patch(`/memory/${input.id}`, {
        content: input.content,
        category: input.category,
      }),
    onSuccess: () => {
      setEditing(null);
      setError(null);
      void qc.invalidateQueries({ queryKey: ["memory"] });
    },
    onError: (err) => setError(err instanceof Error ? err.message : "保存失败"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/memory/${id}`),
    onSuccess: () => {
      setRemoving(null);
      setError(null);
      void qc.invalidateQueries({ queryKey: ["memory"] });
    },
    onError: (err) => setError(err instanceof Error ? err.message : "删除失败"),
  });

  function openEdit(m: MemoryItem) {
    setEditing(m);
    setEditContent(m.content);
    setEditCategory(m.category);
    setError(null);
  }

  return (
    <div className="mx-auto max-w-4xl space-y-4 px-6 py-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            <Brain className="size-5 text-primary" aria-hidden />
            我的记忆
          </h2>
          <p className="text-sm text-muted-foreground">
            孔明在对话中记住的偏好与事实;「隐式记忆」为系统自动沉淀,可编辑确认或删除。
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={() => void exportMemories(setError)}>
          <Download className="size-3.5" aria-hidden />
          导出
        </Button>
      </div>

      {/* 团队记忆写入(个人记忆走对话「请记住」同路径) */}
      <TeamMemoryWrite />

      {/* 偏好自管理(本人当前租户;查看/编辑/单项清除/全部清空) */}
      <PrefsSection />

      {list.isError && <ErrorAlert />}
      {/* 外框仅宽屏保留,窄屏卡片形态与 DataView 口径一致 */}
      <div className="md:rounded-lg md:border md:border-border">
        <DataView
          rows={list.data?.items ?? []}
          rowKey={(m) => m.id}
          isLoading={list.isPending}
          isEmpty={!list.isPending && list.data?.items.length === 0}
          emptyText="还没有记忆——在对话里说「请记住:…」即可沉淀"
          columns={memoryColumns(teamName)}
          actions={[
            { label: "编辑", onSelect: openEdit },
            { label: "删除", destructive: true, onSelect: setRemoving },
          ]}
        />
      </div>

      <EditMemoryDialog
        editing={editing}
        onClose={() => setEditing(null)}
        content={editContent}
        setContent={setEditContent}
        category={editCategory}
        setCategory={setEditCategory}
        error={error}
        pending={update.isPending}
        onSave={() =>
          editing &&
          update.mutate({
            id: editing.id,
            content: editContent.trim(),
            category: editCategory.trim() || "general",
          })
        }
      />

      <RemoveMemoryDialog
        removing={removing}
        onClose={() => setRemoving(null)}
        pending={remove.isPending}
        onConfirm={(id) => remove.mutate(id)}
      />
    </div>
  );
}
