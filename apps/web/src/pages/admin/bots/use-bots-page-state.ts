/* 助手页的数据与变更逻辑:列表、版本映射、上架/归属/启停/建键等 mutation 集中在此,页面只做组装。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listBotVersions, publishBot, rollbackBotVersion, unpublishBot } from "@/lib/store-api";
import { toolList } from "@/lib/toolcall-api";
import { attachResource, listMyTeams } from "@/lib/team-api";
import { api } from "@/lib/api";
import type { BotItem } from "../BotsPage";
import { EMPTY_BOT_FORM, type BotForm } from "./CreateBotDialog";

/** 把上架表单里的建议问题按换行/逗号切分,去空并至多保留 5 条。 */
function parseSuggestions(raw: string): string[] {
  return raw
    .split(/[\n,，]/)
    .map((x) => x.trim())
    .filter(Boolean)
    .slice(0, 5);
}

export function useBotsPageState() {
  const qc = useQueryClient();
  const [attaching, setAttaching] = useState<string | null>(null); // bot_id
  const [attachTeam, setAttachTeam] = useState("");
  const [attachError, setAttachError] = useState("");
  const myTeams = useQuery({ queryKey: ["admin", "teams"], queryFn: listMyTeams });
  // 上架前要求填写欢迎语与建议问题,避免商店卡片/详情展示落空;下架直接操作(可逆)
  const [pubTarget, setPubTarget] = useState<string | null>(null); // bot_id
  const [pubGreeting, setPubGreeting] = useState("");
  const [pubSuggestions, setPubSuggestions] = useState("");
  const [creating, setCreating] = useState(false);
  const [keyShown, setKeyShown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<BotForm>(EMPTY_BOT_FORM);

  const publish = useMutation({
    mutationFn: ({ id, published }: { id: string; published: boolean }) =>
      published
        ? unpublishBot(id)
        : publishBot(id, pubGreeting.trim(), parseSuggestions(pubSuggestions)),
    onSuccess: () => {
      setPubTarget(null);
      void qc.invalidateQueries({ queryKey: ["bots"] });
      setError("");
    },
    onError: (e) => setError(e instanceof Error ? e.message : "上架失败"),
  });
  const attach = useMutation({
    mutationFn: () => attachResource(attachTeam, "bot", attaching!),
    onSuccess: () => {
      setAttaching(null);
      setAttachTeam("");
      setAttachError("");
    },
    onError: (e) => setAttachError(e instanceof Error ? e.message : "归属变更失败"),
  });

  const bots = useQuery({
    queryKey: ["bots"],
    queryFn: () => api.get<BotItem[]>("/system/bots"),
  });

  // 版本历史(对话框按需拉取)+ 在架 Bot 的当前版本快照(差异提示单源)
  const [verTarget, setVerTarget] = useState<string | null>(null); // bot_id
  const [rollbackNo, setRollbackNo] = useState<number | null>(null);
  const [rollbackError, setRollbackError] = useState("");
  const verList = useQuery({
    queryKey: ["bot-versions", verTarget],
    queryFn: () => listBotVersions(verTarget!),
    enabled: verTarget !== null,
  });
  const rollback = useMutation({
    mutationFn: ({ id, no }: { id: string; no: number }) => rollbackBotVersion(id, no),
    onSuccess: () => {
      setRollbackNo(null);
      setRollbackError("");
      void qc.invalidateQueries({ queryKey: ["bot-versions"] });
      void qc.invalidateQueries({ queryKey: ["bots"] });
    },
    onError: (e) => setRollbackError(e instanceof Error ? e.message : "回滚失败"),
  });

  // 工具多选候选源=注册表(封闭集合;CSV 手输拼错会被 resolve_tools 静默求交丢弃)
  const registry = useQuery({ queryKey: ["toolcall", "registry"], queryFn: toolList });

  const publishedIds = (bots.data ?? []).filter((b) => b.published_at !== null).map((b) => b.id);
  const { versionsOf, draftDiffers } = useVersionMap(publishedIds);

  const create = useMutation({
    mutationFn: () =>
      api.post<{ id: string }>("/system/bots", {
        bot_key: form.bot_key.trim(),
        name: form.name.trim(),
        system_prompt: form.system_prompt.trim(),
        tools: form.tools,
      }),
    onSuccess: () => {
      setCreating(false);
      setForm(EMPTY_BOT_FORM);
      setError(null);
      void qc.invalidateQueries({ queryKey: ["bots"] });
    },
    onError: (e) => setError(e instanceof Error ? e.message : "创建失败"),
  });

  const patch = useMutation({
    mutationFn: (input: { id: string; fields: Record<string, unknown> }) =>
      api.put(`/system/bots/${input.id}`, input.fields),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["bots"] }),
    onError: (e) => setError(e instanceof Error ? e.message : "操作失败"),
  });

  const createKey = useMutation({
    mutationFn: (botId: string) =>
      api.post<{ api_key: string }>(`/system/bots/${botId}/keys?name=default`, {}),
    onSuccess: (d) => setKeyShown(d.api_key),
    onError: (e) => setError(e instanceof Error ? e.message : "创建失败"),
  });

  return {
    attaching,
    setAttaching,
    attachTeam,
    setAttachTeam,
    attachError,
    setAttachError,
    myTeams,
    pubTarget,
    setPubTarget,
    pubGreeting,
    setPubGreeting,
    pubSuggestions,
    setPubSuggestions,
    creating,
    setCreating,
    keyShown,
    setKeyShown,
    error,
    form,
    setForm,
    publish,
    attach,
    bots,
    verTarget,
    setVerTarget,
    rollbackNo,
    setRollbackNo,
    rollbackError,
    setRollbackError,
    verList,
    rollback,
    registry,
    versionsOf,
    draftDiffers,
    create,
    patch,
    createKey,
  };
}

/** 拉取在架 Bot 的版本列表并按 id 建映射;versionsOf/draftDiffers 供列表与操作共用。 */
function useVersionMap(publishedIds: string[]) {
  // 差异提示:草稿定义(name/system_prompt/tools)与当前上架版本快照比对
  // (工具集按集合比较——上架快照保序,但草稿勾选顺序变化不构成实质差异)
  const versionsQ = useQuery({
    queryKey: ["bot-versions", "map", publishedIds.join(",")],
    queryFn: async () => {
      const lists = await Promise.all(publishedIds.map((id) => listBotVersions(id)));
      return Object.fromEntries(publishedIds.map((id, i) => [id, lists[i]]));
    },
    enabled: publishedIds.length > 0,
    // 每个在架 Bot 触发一次版本查询;staleTime 内复用,不重复扇出
    // (批量端点可根治,另行立项;回滚/上架后经 invalidate 刷新)
    staleTime: 60_000,
  });
  const versionsMap = versionsQ.data ?? {};
  const versionsOf = (id: string) => versionsMap[id] ?? [];
  const draftDiffers = (b: BotItem): boolean => {
    const cur = versionsOf(b.id).find((v) => v.current);
    if (!cur) return false;
    const s = cur.snapshot;
    const toolsEq = [...b.tools].sort().join(",") === [...s.tools].sort().join(",");
    return b.name !== s.name || b.system_prompt !== s.system_prompt || !toolsEq;
  };
  return { versionsOf, draftDiffers };
}
