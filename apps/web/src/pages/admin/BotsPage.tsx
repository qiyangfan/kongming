/* Bot 管理页(025,custom-agent spec):列表/创建/编辑/启停/公开开关/
   api-key 创建(明文一次展示);归属=本人或超管。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Check, Copy, Loader2, Plus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { DataView } from "@/components/data-view";
import { FormRow } from "@/components/form-row";
import { PageHeader } from "@/components/page-header";
import { ScrollArea } from "@/components/ui/scroll-area";
import { publishBot, unpublishBot } from "@/lib/store-api";
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

  // 040:工具多选候选源=注册表(封闭集合;CSV 手输拼错会被 resolve_tools 静默求交丢弃)
  const registry = useQuery({ queryKey: ["toolcall", "registry"], queryFn: toolList });

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

      {/* 审查循环 B2:上架录入商店展示内容 */}
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
              欢迎语与建议问题将展示在商店卡片与详情页(建议问题点击即问)。
              上架内容会经安全审核,含诱导性或违规内容将被拒绝。
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
    </div>
  );
}
