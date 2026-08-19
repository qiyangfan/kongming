/* 对话页(web-nav spec「对话区渲染」):
   历史回显 + 提问(Enter 发送/Shift+Enter 换行/防重复) + 新会话品牌空态。
   路由参数 sessionId 是会话选择的单源(刷新/直达天然成立)。
   错误路径:提问失败回填草稿并提示;会话被他端删除(404)时收敛回新建态。 */

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Feather, Loader2, SendHorizontal, TriangleAlert } from "lucide-react";
import { useNavigate, useParams } from "react-router";

import { AssistantCard } from "@/components/assistant-card";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { api, type ChatQueryOut, type SessionMessageOut } from "@/lib/api";
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

function Thinking() {
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      军师运筹中…
    </div>
  );
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
  const bottomRef = useRef<HTMLDivElement>(null);

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

  const ask = useMutation({
    mutationFn: (question: string) =>
      api.post<ChatQueryOut>("/chat/query", {
        question,
        datasource_key: datasource, // 选中的数据源随查询发送(008)
        ...(sessionId ? { session_id: sessionId } : {}),
      }),
    onSuccess: (out) => {
      // 先导航(消空态闪回),再失效侧栏;新会话 URL 即状态
      if (out.session_id && out.session_id !== sessionId) {
        navigate(`/chat/${out.session_id}`, { replace: true });
      }
      void qc.invalidateQueries({ queryKey: ["sessions", current] });
      if (out.session_id === sessionId) {
        void qc.invalidateQueries({ queryKey: ["messages", sessionId, current, datasource] });
      }
    },
    onError: (_err, question) => {
      // 失败回填,但尊重用户等待期间的新输入
      setDraft((prev) => (prev.trim() ? prev : question));
    },
  });

  // 新旧消息变化后贴底(提问/切换会话/回显)
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.data?.length, ask.isPending, sessionId]);

  function submit(): void {
    const q = draft.trim();
    if (!q || ask.isPending) return; // 防重复提交
    setDraft("");
    ask.mutate(q);
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ScrollArea className="min-h-0 flex-1">
        <div className="mx-auto max-w-3xl space-y-5 px-4 py-6">
          {messages.isLoading && sessionId ? (
            <div className="space-y-3">
              <Skeleton className="h-16 w-3/4" />
              <Skeleton className="h-32 w-full" />
            </div>
          ) : !sessionId && !ask.isPending && !ask.isError ? (
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
              {ask.isPending && (
                <>
                  <div className="flex justify-end">
                    <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2 text-sm text-primary-foreground">
                      {ask.variables}
                    </p>
                  </div>
                  <Thinking />
                </>
              )}
              {ask.isError && (
                <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
                  <TriangleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
                  <div>
                    <p className="text-foreground">此问未能送达:{ask.error.message}</p>
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
                submit();
              }
            }}
            placeholder="问计于数据,如问计于孔明…"
            rows={1}
            className="max-h-40 min-h-10 resize-none"
            aria-label="提问"
          />
          <Button
            size="icon"
            onClick={submit}
            disabled={!draft.trim() || ask.isPending}
            aria-label="发送"
          >
            {ask.isPending ? (
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
