/* Bot 管理页(025,custom-agent spec):列表/创建/编辑/启停/公开开关/
   api-key 创建(明文一次展示);归属=本人或超管。
   104 版本化上架:草稿与在架版本差异提示、重新上架入口、版本历史
   视图(版本号/上架时间/当前标识/回滚 AlertDialog 确认)。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Check, Copy, Loader2, Plus } from "lucide-react";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { DataView } from "@/components/data-view";
import { FormRow } from "@/components/form-row";
import { PageHeader } from "@/components/page-header";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  BotVersion,
  listBotVersions,
  publishBot,
  rollbackBotVersion,
  unpublishBot,
} from "@/lib/store-api";
import { toolList } from "@/lib/toolcall-api";
import { attachResource, listMyTeams } from "@/lib/team-api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types.gen";
import { ErrorAlert } from "@/components/error-alert";

// 036⑥:消费生成类型(ADR-005,禁手写;team_id 回传已在生成类型)
type BotItem = components["schemas"]["BotOut"];

export default function BotsPage() {
  // 032:归属变更(团队下拉,attach 后经 attach 端点)
  const [attaching, setAttaching] = useState<string | null>(null); // bot_id
  const [attachTeam, setAttachTeam] = useState("");
  const [attachError, setAttachError] = useState("");
  const myTeams = useQuery({ queryKey: ["admin", "teams"], queryFn: listMyTeams });
  // 审查循环 B2:上架须录入商店展示内容(曾恒传空 greeting/suggestions,
  // 商店卡片与详情「建议问题」全部落空);下架保持直接操作(可逆)
  const [pubTarget, setPubTarget] = useState<string | null>(null); // bot_id
  const [pubGreeting, setPubGreeting] = useState("");
  const [pubSuggestions, setPubSuggestions] = useState("");
  const publish = useMutation({
    mutationFn: ({ id, published }: { id: string; published: boolean }) =>
      published
        ? unpublishBot(id)
        : publishBot(
            id,
            pubGreeting.trim(),
            pubSuggestions
              .split(/[\n,，]/)
              .map((x) => x.trim())
              .filter(Boolean)
              .slice(0, 5),
          ),
    onSuccess: () => {
      setPubTarget(null);
      // R1-S1:键修正(列表键 ["bots"],曾 ["admin","bots"] 不刷新)
      void qc.invalidateQueries({ queryKey: ["bots"] });
      setError("");
    },
    onError: (e) =>
      // R1-S1:护栏拒绝行内可见(提案交付「护栏拒绝行内可见」)
      setError(e instanceof Error ? e.message : "上架失败"),
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
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [keyShown, setKeyShown] = useState<string | null>(null);
  const [keyCopied, setKeyCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    bot_key: "",
    name: "",
    system_prompt: "",
    tools: ["execute_sql", "rag_retrieve"],
  });

  const bots = useQuery({
    queryKey: ["bots"],
    queryFn: () => api.get<BotItem[]>("/system/bots"),
  });

  // 104 版本历史(对话框按需拉取)+ 在架 Bot 的当前版本快照(差异提示单源)
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

  // 040:工具多选候选源=注册表(封闭集合;CSV 手输拼错会被 resolve_tools 静默求交丢弃)
  const registry = useQuery({ queryKey: ["toolcall", "registry"], queryFn: toolList });

  // 104 差异提示:草稿定义(name/system_prompt/tools)与当前上架版本快照比对
  // (工具集按集合比较——上架快照保序,但草稿勾选顺序变化不构成实质差异)
  const publishedIds = (bots.data ?? []).filter((b) => b.published_at !== null).map((b) => b.id);
  const versionsQ = useQuery({
    queryKey: ["bot-versions", "map", publishedIds.join(",")],
    queryFn: async () => {
      const lists = await Promise.all(publishedIds.map((id) => listBotVersions(id)));
      return Object.fromEntries(publishedIds.map((id, i) => [id, lists[i]]));
    },
    enabled: publishedIds.length > 0,
    // 评审缓解:N 个在架 Bot = 进页 N 个并发请求(徽标用途),staleTime 内
    // 复用不重复扇出;根治(批量端点)另行立项,回滚/上架后仍经 invalidate 刷新
    staleTime: 60_000,
  });
  const versionsMap = versionsQ.data ?? {};
  const versionsOf = (id: string): BotVersion[] => versionsMap[id] ?? [];
  const draftDiffers = (b: BotItem): boolean => {
    const cur = versionsOf(b.id).find((v) => v.current);
    if (!cur) return false;
    const s = cur.snapshot;
    const toolsEq = [...b.tools].sort().join(",") === [...s.tools].sort().join(",");
    return b.name !== s.name || b.system_prompt !== s.system_prompt || !toolsEq;
  };

  const toggleTool = (name: string) => {
    setForm((f) => ({
      ...f,
      tools: f.tools.includes(name) ? f.tools.filter((t) => t !== name) : [...f.tools, name],
    }));
  };

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
      setForm({ bot_key: "", name: "", system_prompt: "", tools: ["execute_sql", "rag_retrieve"] });
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

  return (
    <div className="space-y-4">
      <PageHeader
        title="助手"
        description="创建自定义助手:设定人设与守则、勾选工具、绑定数据源;可上架商店,或以 API 供外部系统调用。"
        actions={
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="size-3.5" aria-hidden />
            新建助手
          </Button>
        }
      />

      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}

      {bots.isError && <ErrorAlert />}

      <DataView
        rows={bots.data ?? []}
        rowKey={(b) => b.id}
        isLoading={bots.isPending}
        isEmpty={!bots.isPending && !bots.isError && (bots.data ?? []).length === 0}
        emptyText="暂无助手——新建一个专属助手"
        columns={[
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
                  <Badge
                    variant="outline"
                    className="max-w-32"
                    title={(myTeams.data ?? []).find((t) => t.id === b.team_id)?.name ?? "团队"}
                  >
                    <span className="truncate">
                      {(myTeams.data ?? []).find((t) => t.id === b.team_id)?.name ?? "团队"}
                    </span>
                  </Badge>
                )}
                {/* 104:草稿与在架版本不一致提示(治理补偿:分离是特性,提示防遗忘) */}
                {b.published_at !== null && draftDiffers(b) && (
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
              <span
                className="block max-w-32 truncate text-xs"
                title={b.datasource_key ?? undefined}
              >
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
                disabled={patch.isPending}
                onCheckedChange={(on) => patch.mutate({ id: b.id, fields: { enabled: on } })}
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
                  disabled={patch.isPending}
                  onCheckedChange={(on) => patch.mutate({ id: b.id, fields: { is_public: on } })}
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
        ]}
        actions={[
          {
            label: "创建密钥",
            onSelect: (b) => {
              // 与原按钮 disabled={createKey.isPending} 同口径
              if (!createKey.isPending) createKey.mutate(b.id);
            },
          },
          {
            label: "归属",
            onSelect: (b) => {
              setAttaching(b.id);
              setAttachError("");
            },
          },
          {
            // 104:版本历史入口(在架/曾下架均可查;回滚入口在历史视图内)
            label: "版本历史",
            onSelect: (b) => {
              setRollbackError("");
              setVerTarget(b.id);
            },
          },
          {
            // 104:草稿与在架版本不一致时的重新上架入口(预填当前版本商店内容)
            label: "重新上架",
            isVisible: (b) => b.published_at !== null && draftDiffers(b),
            onSelect: (b) => {
              const cur = versionsOf(b.id).find((v) => v.current);
              setPubGreeting(cur?.snapshot.greeting ?? "");
              setPubSuggestions(cur?.snapshot.suggestions.join("\n") ?? "");
              setPubTarget(b.id);
            },
          },
          {
            // 行级文案:已上架行展示「下架」,未上架行展示「上架」
            label: (b) => (b.published_at !== null ? "下架" : "上架"),
            onSelect: (b) => {
              if (publish.isPending) return;
              if (b.published_at !== null) {
                publish.mutate({ id: b.id, published: true });
              } else {
                setPubGreeting("");
                setPubSuggestions("");
                setPubTarget(b.id);
              }
            },
          },
        ]}
      />

      {/* 创建对话框 */}
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建助手</DialogTitle>
            <DialogDescription>
              可勾选的工具不超过你被授予的权限;助手的人设与守则会经安全审核。
            </DialogDescription>
          </DialogHeader>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (
                !form.bot_key.trim() ||
                !form.name.trim() ||
                !form.system_prompt.trim() ||
                create.isPending
              ) {
                return;
              }
              create.mutate();
            }}
          >
            <FormRow
              label="标识"
              htmlFor="bot-key"
              help="小写字母开头,可含数字与下划线;创建后不可改。"
            >
              <Input
                id="bot-key"
                value={form.bot_key}
                onChange={(e) => setForm({ ...form, bot_key: e.target.value })}
                placeholder="after_sale"
              />
            </FormRow>
            <FormRow label="名称" htmlFor="bot-name">
              <Input
                id="bot-name"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="售后助手"
              />
            </FormRow>
            <FormRow label="人设与守则" htmlFor="bot-prompt">
              <Textarea
                id="bot-prompt"
                value={form.system_prompt}
                onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
                placeholder="你是售后客服助手…"
                rows={4}
              />
            </FormRow>
            <FormRow
              label="可用工具"
              help="从清单勾选该助手可用的工具;不勾选时,助手仅对话,不执行任何操作。"
            >
              {/* 040:CSV 手输改注册表勾选——封闭集合消灭拼错工具名被求交静默丢弃 */}
              <ScrollArea className="h-40 rounded-md border p-2">
                <div className="space-y-1 pr-2">
                  {(registry.data?.tools ?? []).map((t) => (
                    <label
                      key={t.name}
                      className="flex cursor-pointer items-start gap-2 rounded px-1 py-1 hover:bg-muted/60"
                    >
                      <Checkbox
                        checked={form.tools.includes(t.name)}
                        onCheckedChange={() => toggleTool(t.name)}
                        className="mt-0.5"
                        aria-label={`工具 ${t.name}`}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5">
                          <span className="font-mono text-xs font-medium">{t.name}</span>
                          <Badge variant={t.write ? "destructive" : "secondary"}>
                            {t.write ? "写" : "只读"}
                          </Badge>
                        </span>
                        <span
                          className="block truncate text-xs text-muted-foreground"
                          title={t.description}
                        >
                          {t.description}
                        </span>
                      </span>
                    </label>
                  ))}
                  {registry.isError ? (
                    <ErrorAlert text="工具清单加载失败,请关闭后重试" />
                  ) : (
                    registry.isPending && (
                      <p className="py-4 text-center text-xs text-muted-foreground">清单加载中…</p>
                    )
                  )}
                </div>
              </ScrollArea>
            </FormRow>
            {error && (
              <p className="text-xs text-destructive" role="alert">
                {error}
              </p>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setCreating(false)}>
                取消
              </Button>
              <Button
                type="submit"
                disabled={
                  !form.bot_key.trim() ||
                  !form.name.trim() ||
                  !form.system_prompt.trim() ||
                  create.isPending
                }
              >
                {create.isPending && <Loader2 className="animate-spin" aria-hidden />}
                创建
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* api-key 一次展示 */}
      <Dialog open={keyShown !== null} onOpenChange={() => setKeyShown(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>API 密钥(仅本次显示)</DialogTitle>
            <DialogDescription>立即复制保存;关闭后不再可见,库内只存哈希。</DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-2 rounded-md bg-muted/60 p-3">
            <code className="flex-1 break-all font-mono text-xs">{keyShown}</code>
            <Button
              size="icon"
              variant="outline"
              aria-label={keyCopied ? "已复制" : "复制"}
              onClick={() => {
                if (!keyShown) return;
                void navigator.clipboard.writeText(keyShown);
                setKeyCopied(true);
                window.setTimeout(() => setKeyCopied(false), 2000);
              }}
            >
              {keyCopied ? (
                <Check className="size-3.5" aria-hidden />
              ) : (
                <Copy className="size-3.5" aria-hidden />
              )}
            </Button>
          </div>
          <DialogFooter>
            <Button onClick={() => setKeyShown(null)}>我已保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {/* 032:归属变更对话框(团队下拉) */}
      <Dialog
        open={attaching !== null}
        onOpenChange={(o) => {
          if (attach.isPending) return;
          if (!o) setAttaching(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>变更归属</DialogTitle>
            <DialogDescription>
              将助手归属到团队后,团队成员按角色共享(观察者可见可运行/
              成员可编辑/负责人全权);仅目标团队负责人可执行。
            </DialogDescription>
          </DialogHeader>
          <FormRow label="归属团队">
            <Select value={attachTeam} onValueChange={setAttachTeam}>
              <SelectTrigger aria-label="归属团队">
                <SelectValue placeholder="选择团队" />
              </SelectTrigger>
              <SelectContent>
                {(myTeams.data ?? []).map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {attachError && (
              <p role="alert" className="text-sm text-destructive">
                {attachError}
              </p>
            )}
          </FormRow>
          <DialogFooter>
            <Button onClick={() => attach.mutate()} disabled={attach.isPending || !attachTeam}>
              {attach.isPending && <Loader2 className="animate-spin" aria-hidden />}
              确认变更
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 审查循环 B2:上架录入商店展示内容;104:上架=创建版本快照
          (内容无变化幂等复用,不产生重复版本) */}
      <Dialog
        open={pubTarget !== null}
        onOpenChange={(open) => {
          if (!open) setPubTarget(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>上架到商店</DialogTitle>
            <DialogDescription>
              上架会对当前定义与商店内容创建不可变版本快照并经安全审核,商店展示与试运行始终使用「当前上架版本」——之后的草稿修改不影响在架版本,重新上架才生效。欢迎语与建议问题展示在商店卡片与详情页;内容无变化时重复上架不产生新版本。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <FormRow label="欢迎语" htmlFor="pub-greeting">
              <Textarea
                id="pub-greeting"
                rows={2}
                maxLength={200}
                value={pubGreeting}
                onChange={(e) => setPubGreeting(e.target.value)}
                placeholder="如:我是订单分析助手,可以直接问我销量问题"
              />
            </FormRow>
            <FormRow label="建议问题(每行一条,至多 5 条)" htmlFor="pub-suggestions">
              <Textarea
                id="pub-suggestions"
                rows={3}
                value={pubSuggestions}
                onChange={(e) => setPubSuggestions(e.target.value)}
                placeholder={"上月销量最高的商品是什么\n各状态的订单有多少"}
              />
            </FormRow>
          </div>
          {publish.isError && (
            <p className="text-sm text-destructive" role="alert">
              {(publish.error as Error).message}
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setPubTarget(null)}>
              取消
            </Button>
            <Button
              disabled={publish.isPending}
              onClick={() => pubTarget && publish.mutate({ id: pubTarget, published: false })}
            >
              {publish.isPending ? "上架中…" : "上架"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 104:版本历史视图(版本号/上架时间/当前标识/回滚入口) */}
      <Dialog
        open={verTarget !== null}
        onOpenChange={(open) => {
          if (!open) setVerTarget(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>版本历史</DialogTitle>
            <DialogDescription>
              每次上架生成一条不可变版本快照;商店始终使用「当前上架版本」。回滚视同上架:按现行安全规则重新审核后切换,通过才生效。
            </DialogDescription>
          </DialogHeader>
          <ScrollArea className="max-h-96">
            <div className="space-y-2 pr-2">
              {verList.isPending && (
                <p className="py-4 text-center text-xs text-muted-foreground">版本加载中…</p>
              )}
              {verList.isError && <ErrorAlert text="版本历史加载失败,请关闭后重试" />}
              {verList.data?.length === 0 && (
                <p className="py-4 text-center text-xs text-muted-foreground">
                  暂无版本——上架后这里会记录每次上架的快照
                </p>
              )}
              {(verList.data ?? []).map((v) => (
                <div
                  key={v.version_no}
                  className="flex items-start justify-between gap-2 rounded-md border p-3"
                >
                  <div className="min-w-0">
                    <p className="flex items-center gap-1.5">
                      <span className="font-mono text-sm font-medium">v{v.version_no}</span>
                      {v.current && <Badge variant="default">当前上架版本</Badge>}
                    </p>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">
                      {new Date(v.published_at).toLocaleString()} · {v.snapshot.name}
                      {v.snapshot.greeting ? ` · ${v.snapshot.greeting}` : ""}
                    </p>
                  </div>
                  {v.current ? (
                    <Button size="sm" variant="outline" disabled>
                      当前
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={rollback.isPending}
                      onClick={() => setRollbackNo(v.version_no)}
                    >
                      回滚到此版本
                    </Button>
                  )}
                </div>
              ))}
            </div>
          </ScrollArea>
          <DialogFooter>
            <Button variant="outline" onClick={() => setVerTarget(null)}>
              关闭
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 104:回滚确认(破坏性语义;受控关闭,失败保持开启展示错误) */}
      <AlertDialog
        open={rollbackNo !== null}
        onOpenChange={(open) => {
          // 进行中不可关闭(评审修复:Esc 中途关框会让回滚失败信息无处渲染,
          // 用户误以为已生效);未进行中才允许取消
          if (!open && !rollback.isPending) setRollbackNo(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>回滚到 v{rollbackNo ?? ""}?</AlertDialogTitle>
            <AlertDialogDescription>
              回滚会把该历史版本重新指为当前上架版本,商店展示与试运行随之切换(该版本保留在历史中)。回滚按现行安全规则重新审核,审核不通过则拒绝且回滚不生效。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {rollbackError && (
            <p className="text-destructive text-sm" role="alert">
              {rollbackError}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={rollback.isPending}>取消</AlertDialogCancel>
            <Button
              onClick={() => {
                if (verTarget && rollbackNo !== null) {
                  rollback.mutate({ id: verTarget, no: rollbackNo });
                }
              }}
            >
              {rollback.isPending && <Loader2 className="animate-spin" aria-hidden />}
              确认回滚
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
