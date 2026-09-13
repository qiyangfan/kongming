/* 对话页(web-nav spec「对话区渲染」+ 017 流式 + 039 冷启动建议问题):
   历史回显 + 流式提问(阶段点亮/SQL 提前/summary 打字机;终态由 AssistantCard
   接管)+ 新会话品牌空态(下方建议问句 chips,点击即问)。路由参数 sessionId
   是会话选择的单源(刷新/直达天然成立)。
   错误路径:流异常回填草稿并提示;会话被他端删除(404)时收敛回新建态。
   取消语义:切会话/新建/切租户/数据源切换/卸载时中止在途流,迟到事件按
   发起上下文守卫丢弃,不写入新上下文(017)。 */

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Feather, Loader2, Paperclip, SendHorizontal, TriangleAlert, X } from "lucide-react";
import { useNavigate, useParams } from "react-router";

import { AssistantCard } from "@/components/assistant-card";
import { ChatBubble } from "@/components/chat-bubble";
import { StreamCard, mergeToolCall } from "@/components/stream-card";
import { ToolConfirmCard } from "@/components/tool-confirm-card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import {
  api,
  humanizeErrorText,
  streamChatQuery,
  type ChatQueryOut,
  type SessionMessageOut,
  type StreamStageOut,
  type StreamToolCallOut,
} from "@/lib/api";
import { useAgentMode } from "@/lib/agent-mode-store";
import { getSuggestions, uploadAttachment } from "@/lib/chat-api";
import { useDatasource } from "@/lib/datasource-store";
import { useTenant } from "@/lib/tenant-store";

function EmptyState({
  items,
  loading,
  onAsk,
}: {
  items: string[];
  loading: boolean;
  onAsk: (q: string) => void;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-5 px-6 text-center">
      <div className="space-y-3">
        <span className="flex size-14 items-center justify-center rounded-2xl bg-primary mx-auto">
          <Feather className="size-7 text-primary-foreground" aria-hidden />
        </span>
        <h2 className="text-xl font-semibold tracking-widest text-foreground">万事,问孔明。</h2>
        <p className="max-w-sm text-sm leading-relaxed text-muted-foreground">
          数据之事,直问即可。问库存、问销量、问趋势—— 运筹帷幄之事,交给军师。
        </p>
      </div>
      {/* 039 冷启动建议:加载骨架/空清单或失败静默隐藏(不阻塞品牌空态) */}
      {loading && items.length === 0 && (
        <div className="flex max-w-lg flex-wrap justify-center gap-2" aria-hidden>
          <Skeleton className="h-8 w-44 rounded-full" />
          <Skeleton className="h-8 w-36 rounded-full" />
          <Skeleton className="h-8 w-40 rounded-full" />
        </div>
      )}
      {items.length > 0 && (
        <div className="flex max-w-lg flex-wrap justify-center gap-2">
          {items.map((q) => (
            <Button
              key={q}
              variant="outline"
              size="sm"
              className="rounded-full font-normal text-muted-foreground"
              onClick={() => onAsk(q)}
            >
              {q}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}

interface StreamingState {
  question: string;
  stages: StreamStageOut[];
  sql: string | null;
  summary: string;
  toolCalls: StreamToolCallOut[];
}

/** 终态快照(R1-B1):result 到达即由 ChatQueryOut 乐观构造本轮消息渲染,
 * 消息历史 refetch 含该轮(message_id 命中)后自然替换——消灭「先清空后补位」
 * 的空白间隙;persist=false 无历史可对照时保留至下一轮/切换上下文。 */
/** 030 附件徽标(user 气泡两处共用:历史回显与即时快照) */
function AttachmentBadge({ items }: { items: { [key: string]: unknown }[] }) {
  if (items.length === 0) return null;
  return (
    <span className="flex items-center gap-1 text-xs text-muted-foreground">
      <Paperclip className="size-3" aria-hidden />
      {items.length} 张图片:
      {items.map((a) => String(a.filename ?? "")).join(", ")}
    </span>
  );
}

function toSyntheticMessage(out: ChatQueryOut): SessionMessageOut {
  // 044 四态:NO_DATA=知识未覆盖如实说明(中性,非失败)
  const status = out.success
    ? "SUCCESS"
    : out.verdict === "BLOCKED"
      ? "REJECTED"
      : out.verdict === "NO_DATA"
        ? "NO_DATA"
        : "FAILED";
  return {
    // message_id 缺失(落库容错分支)置空串:FeedbackActions 的 !msg.id 守卫
    // 与 EXPLAIN 入口({msg.sql && ...}同上)天然不渲染,避免非 UUID 直传 422(R3-B2)
    id: out.message_id ?? "",
    role: "ASSISTANT",
    question: null,
    summary: out.summary || null,
    sql: out.sql || null,
    status,
    error: out.error,
    error_detail: out.error_detail ?? null, // 047:技术详情折叠(快照与回显同源)
    result: {
      columns: out.columns,
      rows: out.rows,
      insights: out.insights,
      stats: out.stats,
      chart: out.chart,
      // 049:候选快照同源(与 041 column_labels 同款先例)——漏带会使快照期
      // 回落单图绑定、历史 refetch 后切换到候选绑定,产生一次视觉跳变
      chart_candidates: out.chart_candidates ?? [],
      error: out.error,
      error_detail: out.error_detail ?? null, // 047 同源
      // 026:终态快照携带产物卡(与历史回显 MessageResultOut.artifacts 同形),
      // 缺档空数组 → assistant-card 回落既有形态,与读档重算兜底口径一致
      artifacts: out.artifacts ?? [],
      // 041:呈现层列标签快照同源(与 artifacts/attachments 同款先例)——
      // 漏带会使快照期表头物理名、历史 refetch 后闪变业务名(评审实锤)
      column_labels: out.column_labels ?? [],
    },
    rating: null,
    corrected_sql: null,
    feedback_comment: null,
    attachments: out.attachments ?? [], // 030:终态快照同源
  };
}

function isStatus(err: unknown, status: number): boolean {
  return (err as Error & { status?: number }).status === status;
}

export default function ChatPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const { current } = useTenant();
  const datasource = useDatasource((s) => s.current);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState<StreamingState | null>(null);
  const [finalized, setFinalized] = useState<{ question: string; out: ChatQueryOut } | null>(null);
  const [askError, setAskError] = useState<string | null>(null);
  const agentMode = useAgentMode((s) => s.mode);
  const setAgentMode = useAgentMode((s) => s.setMode);
  // 019 待确认写工具(独立于 finalized 快照:快照退位由历史接管后确认卡
  // 仍在,直到用户确认/拒绝或切换上下文——响应级瞬态,不落库不回显)
  const [pendingConfirm, setPendingConfirm] = useState<{
    pending: ChatQueryOut["pending_tool_calls"];
    sessionId: string;
  } | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // 流发起时的上下文(守卫基准):迟到事件与当前不一致即丢弃(防串上下文)
  const streamCtxRef = useRef<{
    sid: string | null;
    tenant: string | null;
    ds: string | null;
  } | null>(null);
  // 本流自建会话的导航标记(R2-B2:取消闸豁免依据)
  const selfNavigatedRef = useRef<string | null>(null);
  // navigate/invalidate 回调里读最新值的稳定引用(sid 归一 null:useParams
  // 无参数时为 undefined,与流发起上下文的 null 归一比对——不归一则守卫恒 false)
  const sessionIdRef = useRef<string | null>(sessionId ?? null);
  sessionIdRef.current = sessionId ?? null;
  const tenantRef = useRef(current);
  tenantRef.current = current;
  const dsRef = useRef(datasource);
  dsRef.current = datasource;

  const messages = useQuery({
    // 键含租户+数据源双维度:切换即换档,不跨上下文残留
    queryKey: ["messages", sessionId ?? "-", current, datasource],
    queryFn: () => api.get<SessionMessageOut[]>(`/chat/sessions/${sessionId}/messages`),
    enabled: sessionId !== undefined,
  });

  // 039 冷启动建议:仅新建态干净分支启用;键含租户+数据源双维度,切换即换档
  const suggestions = useQuery({
    queryKey: ["chat-suggestions", current, datasource],
    queryFn: () => getSuggestions(datasource),
    enabled: !sessionId && !streaming && !finalized && !askError,
  });

  // 会话已被删除(他端/本端竞态):提示并收敛回新建态,不留空白死态
  useEffect(() => {
    if (messages.isError && isStatus(messages.error, 404)) {
      void qc.invalidateQueries({ queryKey: ["sessions", current] });
      navigate("/chat", { replace: true });
    }
  }, [messages.isError, messages.error, qc, navigate, current]);

  // 取消闸(017):上下文变化(切会话/新建/切租户/切数据源)即中止在途流并
  // 清残留呈现;卸载时同样中止(闭包捕获最新 refs)。
  // R2-B2 豁免:本流自建会话的 navigate 也会触发 sessionId 变化——该导航
  // 是终态接管的一部分,不得清快照(否则接管间隙以骨架屏闪变复活)。
  useEffect(() => {
    if (selfNavigatedRef.current !== null && selfNavigatedRef.current === sessionId) {
      selfNavigatedRef.current = null;
      return; // 本流自己的导航:流已终止,呈现交给快照/历史
    }
    selfNavigatedRef.current = null;
    abortRef.current?.abort();
    abortRef.current = null;
    streamCtxRef.current = null;
    setStreaming(null);
    setFinalized(null);
    setAskError(null);
    setPendingConfirm(null); // 切换上下文丢弃待确认入口(瞬态语义)
  }, [sessionId, current, datasource]);
  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  // 终态快照退位:消息历史 refetch 已含该轮(message_id 命中)即撤快照
  useEffect(() => {
    if (!finalized) return;
    const mid = finalized.out.message_id;
    if (mid && messages.data?.some((m) => m.id === mid)) {
      setFinalized(null);
    }
  }, [messages.data, finalized]);

  // 新旧消息变化后贴底(提问/切换会话/回显)
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.data?.length, streaming, sessionId]);

  /** 守卫:流回调仅在「流的发起上下文 == 当前上下文」时生效(迟到即丢弃) */
  function inOriginCtx(): boolean {
    const ctx = streamCtxRef.current;
    return (
      ctx !== null &&
      ctx.sid === sessionIdRef.current &&
      ctx.tenant === tenantRef.current &&
      ctx.ds === dsRef.current
    );
  }

  function finalizeOk(out: ChatQueryOut, question: string): void {
    if (!inOriginCtx()) return;
    setStreaming(null);
    setAskError(null);
    // 019:待确认调用独立留存(快照退位不带走确认入口);
    // ?? [] 兜底:前后端版本错位(滚动部署)缺键时不炸(R1-D3)
    const pendingCalls = out.pending_tool_calls ?? [];
    if (pendingCalls.length > 0 && out.session_id) {
      setPendingConfirm({ pending: pendingCalls, sessionId: out.session_id });
    }
    setFinalized({ question, out }); // 乐观渲染本轮,历史 refetch 后替换(R1-B1)
    // 先导航(消空态闪回),再失效侧栏;新会话 URL 即状态(与原同步路径同构);
    // 自建导航打标——取消闸据此豁免(R2-B2)
    if (out.session_id && out.session_id !== sessionIdRef.current) {
      selfNavigatedRef.current = out.session_id;
      navigate(`/chat/${out.session_id}`, { replace: true });
    }
    void qc.invalidateQueries({ queryKey: ["sessions", tenantRef.current] });
    if (out.session_id === sessionIdRef.current) {
      void qc.invalidateQueries({
        queryKey: ["messages", out.session_id, tenantRef.current, dsRef.current],
      });
    }
  }

  function finalizeError(
    message: string,
    question: string,
    attSnapshot: { id: string; filename: string }[] = [],
  ): void {
    if (!inOriginCtx()) return;
    setStreaming(null);
    setAskError(message);
    // 失败回填,但尊重用户等待期间的新输入
    setDraft((prev) => (prev.trim() ? prev : question));
    // R1-F4:传输层失败时附件未被消费(agent 未取件),chips 恢复可重试;
    // 尊重等待期间用户新贴的附件(去重合并)
    if (attSnapshot.length) {
      setAttachments((prev) => {
        const seen = new Set(prev.map((a) => a.id));
        return [...prev, ...attSnapshot.filter((a) => !seen.has(a.id))];
      });
    }
  }

  // 030:附件状态(已上传附件 chips;发送后清空)
  const [attachments, setAttachments] = useState<{ id: string; filename: string }[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function onPickFile(e: React.ChangeEvent<HTMLInputElement>): Promise<void> {
    const file = e.target.files?.[0];
    e.target.value = ""; // 同文件可重选(009 R2-S2 教训)
    if (!file) return;
    if (attachments.length >= 3) {
      setAskError("一次最多引用 3 张图片。");
      return;
    }
    setUploading(true);
    setAskError(null);
    try {
      const out = await uploadAttachment(file);
      setAttachments((prev) => [...prev, { id: out.attachment_id, filename: out.filename }]);
    } catch (err) {
      setAskError(err instanceof Error ? err.message : "附件上传未成,请稍后再试。");
    } finally {
      setUploading(false);
    }
  }

  async function submit(text?: string): Promise<void> {
    // 039:建议 chips 直发(text 显式传入,保留草稿不被清空);缺省=输入框草稿
    const fromDraft = text === undefined;
    const q = (text ?? draft).trim();
    // R1-S2:与按钮/契约对齐(question min_length=1)——曾放行纯附件,
    // Enter 可绕过按钮禁用直撞 422 且 chips 已清空
    // 审查循环 B1:上传中禁发——此时提交 attIds 不含上传中附件,
    // 问答无图且完成后 chips 孤立出现(与按钮禁用态对齐)
    if (!q || uploading || streaming !== null) return; // 防重复提交
    const attIds = attachments.map((a) => a.id);
    const attSnapshot = attachments; // R1-F4:失败恢复用快照
    setAttachments([]);
    if (fromDraft) setDraft("");
    setAskError(null);
    setFinalized(null); // 新一轮提问顶掉上一轮快照
    const ac = new AbortController();
    abortRef.current = ac;
    streamCtxRef.current = { sid: sessionId ?? null, tenant: current, ds: datasource };
    setStreaming({ question: q, stages: [], sql: null, summary: "", toolCalls: [] });
    try {
      await streamChatQuery(
        {
          question: q,
          datasource_key: datasource, // 选中的数据源随查询发送(008)
          agent_mode: agentMode, // 019:ReAct 内核开关(缺省 recipe 零回归)
          ...(attIds.length ? { attachment_ids: attIds } : {}), // 030
          ...(sessionId ? { session_id: sessionId } : {}),
        },
        {
          onStage: (d) => {
            if (!inOriginCtx()) return;
            setStreaming((prev) =>
              prev
                ? {
                    ...prev,
                    stages: [...prev.stages, d],
                  }
                : prev,
            );
          },
          onSql: (d) => {
            if (!inOriginCtx()) return;
            setStreaming((prev) => (prev ? { ...prev, sql: d.sql } : prev));
          },
          onSummary: (d) => {
            if (!inOriginCtx()) return;
            // 打字机:逐事件追加(契约语义;当前实现为单条完整 delta)
            setStreaming((prev) => (prev ? { ...prev, summary: prev.summary + d.delta } : prev));
          },
          onToolCall: (d) => {
            if (!inOriginCtx()) return;
            setStreaming((prev) =>
              prev ? { ...prev, toolCalls: mergeToolCall(prev.toolCalls, d) } : prev,
            );
          },
          onResult: (out) => finalizeOk(out, q),
          onError: (d) => finalizeError(d.error, q, attSnapshot),
        },
        ac.signal,
      );
    } catch (err) {
      if (ac.signal.aborted) return; // 主动取消:静默(呈现已被取消闸清理)
      finalizeError(err instanceof Error ? err.message : "问答服务异常", q);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ScrollArea className="min-h-0 flex-1">
        <div className="mx-auto max-w-3xl space-y-5 px-4 py-6">
          {messages.isLoading && sessionId && !finalized ? (
            <div className="space-y-3">
              <Skeleton className="h-16 w-3/4" />
              <Skeleton className="h-32 w-full" />
            </div>
          ) : !sessionId && !streaming && !finalized && !askError ? (
            <div className="h-[60vh]">
              <EmptyState
                items={suggestions.data?.items ?? []}
                loading={suggestions.isLoading}
                onAsk={(q) => void submit(q)}
              />
            </div>
          ) : (
            <>
              {messages.data?.map((m) =>
                m.role === "USER" ? (
                  <div key={m.id} className="flex flex-col items-end gap-1">
                    <ChatBubble role="user">{m.question}</ChatBubble>
                    <AttachmentBadge items={m.attachments ?? []} />
                  </div>
                ) : (
                  <div key={m.id} className="max-w-full">
                    <AssistantCard msg={m} />
                  </div>
                ),
              )}
              {finalized && !streaming && (
                <>
                  <div className="flex flex-col items-end gap-1">
                    <ChatBubble role="user">{finalized.question}</ChatBubble>
                    <AttachmentBadge items={finalized.out.attachments ?? []} />
                  </div>
                  <div className="max-w-full">
                    <AssistantCard msg={toSyntheticMessage(finalized.out)} />
                  </div>
                </>
              )}
              {streaming && (
                <>
                  <div className="flex justify-end">
                    <ChatBubble role="user">{streaming.question}</ChatBubble>
                  </div>
                  <div className="max-w-full">
                    <StreamCard
                      stages={streaming.stages}
                      sql={streaming.sql}
                      summary={streaming.summary}
                      toolCalls={streaming.toolCalls}
                    />
                  </div>
                </>
              )}
              {pendingConfirm && (
                <ToolConfirmCard
                  pending={pendingConfirm.pending}
                  sessionId={pendingConfirm.sessionId}
                  onAllResolved={() => setPendingConfirm(null)}
                />
              )}
              {askError && (
                <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
                  <TriangleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
                  <div>
                    <p className="text-foreground">此问未能送达:{humanizeErrorText(askError)}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      问句已回到输入框,可重试。
                    </p>
                  </div>
                </div>
              )}
            </>
          )}
          <div ref={bottomRef} />
        </div>
      </ScrollArea>

      <div className="border-t border-border bg-background px-4 py-3">
        <div className="mx-auto max-w-3xl">
          {/* 019:Agent 内核开关(自主工具调用=ReAct;缺省 recipe 零回归);
              UI 命名「运筹模式」与空态文案「运筹帷幄之事,交给军师」同源,实现术语不入用户文案 */}
          <div className="mb-2 flex items-center justify-end gap-2">
            <Switch
              id="agent-mode"
              checked={agentMode === "react"}
              onCheckedChange={(on) => setAgentMode(on ? "react" : "recipe")}
              aria-label="运筹模式"
            />
            <Tooltip>
              <TooltipTrigger asChild>
                <Label
                  htmlFor="agent-mode"
                  className="cursor-pointer text-xs text-muted-foreground"
                >
                  运筹模式
                </Label>
              </TooltipTrigger>
              <TooltipContent className="max-w-72">
                AI 将自主规划步骤、调用工具求解,适合复杂问题;日常提问无需开启。
              </TooltipContent>
            </Tooltip>
          </div>
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pb-1">
              {attachments.map((a) => (
                <Badge key={a.id} variant="secondary">
                  <Paperclip className="size-3" aria-hidden />
                  {a.filename}
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label={`移除附件 ${a.filename}`}
                    onClick={() => setAttachments((prev) => prev.filter((x) => x.id !== a.id))}
                  >
                    <X aria-hidden />
                  </Button>
                </Badge>
              ))}
            </div>
          )}
          <div className="flex items-end gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".png,.jpg,.jpeg,.webp,.gif"
              className="hidden"
              aria-hidden
              tabIndex={-1}
              onChange={(e) => void onPickFile(e)}
            />
            <Button
              variant="ghost"
              size="icon"
              onClick={() => fileRef.current?.click()}
              disabled={uploading || streaming !== null}
              aria-label="添加图片附件"
              title="添加图片(png/jpg/webp/gif,≤10MB)"
            >
              {uploading ? (
                <Loader2 className="animate-spin" aria-hidden />
              ) : (
                <Paperclip aria-hidden />
              )}
            </Button>
            <Textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void submit();
                }
              }}
              placeholder="问计于数据,如问计于孔明…"
              rows={1}
              className="max-h-40 min-h-10 resize-none"
              aria-label="提问"
            />
            <Button
              size="icon"
              onClick={() => void submit()}
              disabled={!draft.trim() || uploading || streaming !== null}
              aria-label="发送"
            >
              {streaming ? (
                <Loader2 className="animate-spin" aria-hidden />
              ) : (
                <SendHorizontal aria-hidden />
              )}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
