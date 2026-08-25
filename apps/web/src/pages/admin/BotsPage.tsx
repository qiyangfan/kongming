/* Bot 管理页(025,custom-agent spec):列表/创建/编辑/启停/公开开关/
   api-key 创建(明文一次展示);归属=本人或超管。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Copy, Key, Loader2, Plus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { publishBot, unpublishBot } from "@/lib/store-api";
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
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types.gen";

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
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    bot_key: "",
    name: "",
    system_prompt: "",
    tools: "execute_sql,rag_retrieve",
  });

  const bots = useQuery({
    queryKey: ["bots"],
    queryFn: () => api.get<BotItem[]>("/system/bots"),
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<{ id: string }>("/system/bots", {
        bot_key: form.bot_key.trim(),
        name: form.name.trim(),
        system_prompt: form.system_prompt.trim(),
        tools: form.tools.split(",").map((t) => t.trim()).filter(Boolean),
      }),
    onSuccess: () => {
      setCreating(false);
      setForm({ bot_key: "", name: "", system_prompt: "", tools: "execute_sql,rag_retrieve" });
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
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Bot</h2>
          <p className="text-sm text-muted-foreground">
            自定义助手:系统提示 + 工具子集 + 数据源;可公开发布为嵌入页与 OpenAI 兼容 API。
          </p>
        </div>
        <Button size="sm" onClick={() => setCreating(true)}>
          <Plus className="size-3.5" aria-hidden />
          新建 Bot
        </Button>
      </div>

      {error && <p className="text-xs text-destructive" role="alert">{error}</p>}

      <div className="rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Bot</TableHead>
              <TableHead>工具</TableHead>
              <TableHead>数据源</TableHead>
              <TableHead>启用</TableHead>
              <TableHead>公开</TableHead>
              <TableHead className="w-28">密钥</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {bots.isLoading ? (
              <TableRow>
                <TableCell colSpan={6} className="py-6 text-center">
                  <Loader2 className="mx-auto size-4 animate-spin text-muted-foreground" aria-hidden />
                </TableCell>
              </TableRow>
            ) : (bots.data?.length ?? 0) === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="py-6 text-center text-sm text-muted-foreground">
                  暂无 Bot——新建一个专属助手
                </TableCell>
              </TableRow>
            ) : (
              bots.data?.map((b) => (
                <TableRow key={b.id}>
                  <TableCell>
                    <span className="flex items-center gap-1.5">
                      <Bot className="size-4 text-muted-foreground" aria-hidden />
                      <span className="font-medium">{b.name}</span>
                      <span className="font-mono text-xs text-muted-foreground">{b.bot_key}</span>
                      {b.team_id && (
                        <Badge variant="outline" className="font-normal">
                          {(myTeams.data ?? []).find((t) => t.id === b.team_id)?.name ?? "团队"}
                        </Badge>
                      )}
                    </span>
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {b.tools.length ? b.tools.join(", ") : "—"}
                  </TableCell>
                  <TableCell className="text-xs">{b.datasource_key}</TableCell>
                  <TableCell>
                    <Switch
                      checked={b.enabled}
                      disabled={patch.isPending}
                      onCheckedChange={(on) => patch.mutate({ id: b.id, fields: { enabled: on } })}
                      aria-label={`启用 ${b.name}`}
                    />
                  </TableCell>
                  <TableCell>
                    <span className="flex items-center gap-1.5">
                      <Switch
                        checked={b.is_public}
                        disabled={patch.isPending}
                        onCheckedChange={(on) => patch.mutate({ id: b.id, fields: { is_public: on } })}
                        aria-label={`公开 ${b.name}`}
                      />
                      {b.is_public && (
                        <Badge variant="outline" className="font-mono text-xs">/bot/{b.bot_key}</Badge>
                      )}
                    </span>
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={createKey.isPending}
                        onClick={() => createKey.mutate(b.id)}
                      >
                        <Key className="size-3.5" aria-hidden />
                        创建密钥
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setAttaching(b.id);
                          setAttachError("");
                        }}
                        aria-label={`归属 ${b.name}`}
                      >
                        归属
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={publish.isPending}
                        onClick={() => {
                          if (b.published_at !== null) {
                            publish.mutate({ id: b.id, published: true });
                          } else {
                            setPubGreeting("");
                            setPubSuggestions("");
                            setPubTarget(b.id);
                          }
                        }}
                        aria-label={`${b.published_at !== null ? "下架" : "上架"} ${b.name}`}
                      >
                        {b.published_at !== null ? "下架" : "上架"}
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* 创建对话框 */}
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建 Bot</DialogTitle>
            <DialogDescription>
              工具子集不得超过你当前被授予的工具权限;系统提示经安全审核。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="bot-key">标识(小写蛇形)</Label>
              <Input id="bot-key" value={form.bot_key} onChange={(e) => setForm({ ...form, bot_key: e.target.value })} placeholder="after_sale" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="bot-name">名称</Label>
              <Input id="bot-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="售后助手" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="bot-prompt">系统提示</Label>
              <Textarea id="bot-prompt" value={form.system_prompt} onChange={(e) => setForm({ ...form, system_prompt: e.target.value })} placeholder="你是售后客服助手…" rows={4} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="bot-tools">工具(逗号分隔)</Label>
              <Input id="bot-tools" value={form.tools} onChange={(e) => setForm({ ...form, tools: e.target.value })} placeholder="execute_sql,rag_retrieve" />
            </div>
            {error && <p className="text-xs text-destructive" role="alert">{error}</p>}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreating(false)}>取消</Button>
            <Button
              disabled={!form.bot_key.trim() || !form.name.trim() || !form.system_prompt.trim() || create.isPending}
              onClick={() => create.mutate()}
            >
              {create.isPending && <Loader2 className="animate-spin" aria-hidden />}
              创建
            </Button>
          </DialogFooter>
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
              aria-label="复制"
              onClick={() => keyShown && void navigator.clipboard.writeText(keyShown)}
            >
              <Copy className="size-3.5" aria-hidden />
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
              将 Bot 归属到团队后,团队成员按角色共享(观察者可见可运行/
              成员可编辑/负责人全权);仅目标团队负责人可执行。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label>归属团队</Label>
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
          </div>
          <DialogFooter>
            <Button
              onClick={() => attach.mutate()}
              disabled={attach.isPending || !attachTeam}
            >
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
              上架内容经安全护栏审核,替换形态会被拒。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="pub-greeting">欢迎语</Label>
              <Textarea
                id="pub-greeting"
                rows={2}
                maxLength={200}
                value={pubGreeting}
                onChange={(e) => setPubGreeting(e.target.value)}
                placeholder="如:我是订单分析助手,可以直接问我销量问题"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="pub-suggestions">建议问题(每行一条,至多 5 条)</Label>
              <Textarea
                id="pub-suggestions"
                rows={3}
                value={pubSuggestions}
                onChange={(e) => setPubSuggestions(e.target.value)}
                placeholder={"上月销量最高的商品是什么\n各状态的订单有多少"}
              />
            </div>
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
