/* 对话页(web-nav spec「对话区渲染」+ 017 流式):
   历史回显 + 流式提问(阶段点亮/SQL 提前/summary 打字机;终态由 AssistantCard
   接管)+ 新会话品牌空态。路由参数 sessionId 是会话选择的单源(刷新/直达天然成立)。
   错误路径:流异常回填草稿并提示;会话被他端删除(404)时收敛回新建态。
   取消语义:切会话/新建/切租户/数据源切换/卸载时中止在途流,迟到事件按
   发起上下文守卫丢弃,不写入新上下文(017)。 */

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Feather, Loader2, SendHorizontal, TriangleAlert } from "lucide-react";
import { useNavigate, useParams } from "react-router";

import { AssistantCard } from "@/components/assistant-card";
import { StreamCard } from "@/components/stream-card";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  api,
  streamChatQuery,
  type ChatQueryOut,
  type SessionMessageOut,
  type StreamStageOut,
} from "@/lib/api";
import { useDatasource } from "@/lib/datasource-store";
import { useTenant } from "@/lib/tenant-store";

function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
      <span className="flex size-14 items-center justify-center rounded-2xl bg-primary">
        <Feather className="size-7 text-primary-foreground" aria-hidden />
      </span>
      <h2 className="text-xl font-semibold tracking-widest text-foreground">万事,问孔明。</h2>
      <p className="max-w-sm text-sm leading-relaxed text-muted-foreground">
        数据之事,直问即可。问库存、问销量、问趋势——
        运筹帷幄之事,交给军师。
      </p>
    </div>
  );
}

interface StreamingState {
  question: string;
  stages: StreamStageOut[];
  sql: string | null;
  summary: string;
}

/** 终态快照(R1-B1):result 到达即由 ChatQueryOut 乐观构造本轮消息渲染,
 * 消息历史 refetch 含该轮(message_id 命中)后自然替换——消灭「先清空后补位」
 * 的空白间隙;persist=false 无历史可对照时保留至下一轮/切换上下文。 */
function toSyntheticMessage(out: ChatQueryOut): SessionMessageOut {
  const status = out.success ? "SUCCESS" : out.verdict === "BLOCKED" ? "REJECTED" : "FAILED";
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
    result: {
      columns: out.columns,
      rows: out.rows,
      insights: out.insights,
      stats: out.stats,
      chart: out.chart,
      error: out.error,
    },
    rating: null,
    corrected_sql: null,
    feedback_comment: null,
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
  const [finalized, setFinalized] = useState<{ question: string; out: ChatQueryOut } | null>(
    null,
  );
  const [askError, setAskError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // 流发起时的上下文(守卫基准):迟到事件与当前不一致即丢弃(防串上下文)
  const streamCtxRef = useRef<
    { sid: string | null; tenant: string | null; ds: string | null } | null
  >(null);
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

  function finalizeError(message: string, question: string): void {
    if (!inOriginCtx()) return;
    setStreaming(null);
    setAskError(message);
    // 失败回填,但尊重用户等待期间的新输入
    setDraft((prev) => (prev.trim() ? prev : question));
  }

  async function submit(): Promise<void> {
    const q = draft.trim();
    if (!q || streaming !== null) return; // 防重复提交
    setDraft("");
    setAskError(null);
    setFinalized(null); // 新一轮提问顶掉上一轮快照
    const ac = new AbortController();
    abortRef.current = ac;
    streamCtxRef.current = { sid: sessionId ?? null, tenant: current, ds: datasource };
    setStreaming({ question: q, stages: [], sql: null, summary: "" });
    try {
      await streamChatQuery(
        {
          question: q,
          datasource_key: datasource, // 选中的数据源随查询发送(008)
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
          onResult: (out) => finalizeOk(out, q),
          onError: (d) => finalizeError(d.error, q),
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
              <EmptyState />
            </div>
          ) : (
            <>
              {messages.data?.map((m) =>
                m.role === "USER" ? (
                  <div key={m.id} className="flex justify-end">
                    <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2 text-sm leading-relaxed text-primary-foreground">
                      {m.question}
                    </p>
                  </div>
                ) : (
                  <div key={m.id} className="max-w-full">
                    <AssistantCard msg={m} />
                  </div>
                ),
              )}
              {finalized && !streaming && (
                <>
                  <div className="flex justify-end">
                    <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2 text-sm text-primary-foreground">
                      {finalized.question}
                    </p>
                  </div>
                  <div className="max-w-full">
                    <AssistantCard msg={toSyntheticMessage(finalized.out)} />
                  </div>
                </>
              )}
              {streaming && (
                <>
                  <div className="flex justify-end">
                    <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2 text-sm text-primary-foreground">
                      {streaming.question}
                    </p>
                  </div>
                  <div className="max-w-full">
                    <StreamCard
                      stages={streaming.stages}
                      sql={streaming.sql}
                      summary={streaming.summary}
                    />
                  </div>
                </>
              )}
              {askError && (
                <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
                  <TriangleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
                  <div>
                    <p className="text-foreground">此问未能送达:{askError}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">问句已回到输入框,可重试。</p>
                  </div>
                </div>
              )}
            </>
          )}
          <div ref={bottomRef} />
        </div>
      </ScrollArea>

      <div className="border-t border-border bg-background px-4 py-3">
        <div className="mx-auto flex max-w-3xl items-end gap-2">
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
            disabled={!draft.trim() || streaming !== null}
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
  );
}
