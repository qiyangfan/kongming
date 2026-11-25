/* 消息流：骨架屏、空态、历史消息、终态快照/在途轮、待确认卡与错误提示。 */

import { Info, TriangleAlert } from "lucide-react";

import { AssistantCard } from "@/components/assistant-card";
import { ChatBubble } from "@/components/chat-bubble";
import { StreamingTurn } from "@/components/streaming-turn";
import { ToolConfirmCard } from "@/components/tool-confirm-card";
import { Skeleton } from "@/components/ui/skeleton";
import type { ChatQueryOut, SessionMessageOut } from "@/lib/api";
import { humanizeErrorText } from "@/lib/api";
import { stopNoticeText, type StopNotice, type StopNoticeKind } from "@/lib/turn-stop";

import { EmptyState } from "./empty-state";
import { MessageRow, UserTurn } from "./message-row";
import type { PendingConfirm, StreamingTurnStart } from "./use-chat-stream";

interface ChatThreadProps {
  sessionId: string | undefined;
  messagesLoading: boolean;
  messages: SessionMessageOut[] | undefined;
  suggestions: { items?: string[] | undefined; loading: boolean };
  streaming: StreamingTurnStart | null;
  finalized: { question: string; out: ChatQueryOut } | null;
  finalizedMsg: SessionMessageOut | null;
  pendingConfirm: PendingConfirm | null;
  stoppedNotice: StopNotice | null;
  askError: string | null;
  onAsk: (q: string) => void;
  /** 本轮标识到达(X-Trace-Id):停止入口据此向控制面发起取消 */
  onTraceId: (traceId: string) => void;
  onResult: (out: ChatQueryOut) => void;
  onError: (message: string) => void;
  onAllResolved: () => void;
}

/** 停止后的提示:文案随取消结果如实变化(确认前不声称已取消)。 */
function StoppedNotice({ kind }: { kind: StopNoticeKind }) {
  const text = stopNoticeText(kind);
  return (
    <div
      className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm"
      role="status"
    >
      <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
      <div>
        <p className="text-foreground">{text.title}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{text.detail}</p>
      </div>
    </div>
  );
}

/** 提问未送达的提示(问句已回填输入框)。 */
function AskErrorNotice({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
      <TriangleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
      <div>
        <p className="text-foreground">此问未能送达:{humanizeErrorText(message)}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">问句已回到输入框,可重试。</p>
      </div>
    </div>
  );
}

/** 历史消息 + 终态快照(无在途轮时)呈现。 */
function ConversationBody(props: ChatThreadProps) {
  const { streaming, finalized, finalizedMsg, messages } = props;
  return (
    <>
      {messages?.map((m) => (
        <MessageRow key={m.id} m={m} />
      ))}
      {finalized && !streaming && finalizedMsg && (
        <>
          <UserTurn question={finalized.question} attachments={finalized.out.attachments ?? []} />
          <div className="max-w-full">
            <AssistantCard msg={finalizedMsg} />
          </div>
        </>
      )}
    </>
  );
}

/** 在途轮:用户问句气泡 + 流式回答。 */
function StreamingBlock({
  streaming,
  onResult,
  onError,
  onTraceId,
}: {
  streaming: StreamingTurnStart;
  onResult: ChatThreadProps["onResult"];
  onError: ChatThreadProps["onError"];
  onTraceId: ChatThreadProps["onTraceId"];
}) {
  return (
    <>
      <div className="flex justify-end">
        <ChatBubble variant="user">{streaming.question}</ChatBubble>
      </div>
      <div className="max-w-full">
        <StreamingTurn
          body={streaming.body}
          signal={streaming.ac.signal}
          onResult={onResult}
          onError={onError}
          onTraceId={onTraceId}
        />
      </div>
    </>
  );
}

/** 空态(无会话、无在途、无快照、无错误)按建议问题展示。 */
function ThreadBody(props: ChatThreadProps) {
  const { sessionId, streaming, finalized, askError } = props;
  if (props.messagesLoading && sessionId && !finalized) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-16 w-3/4" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }
  // 空态不含停止提示:新对话第一问停止时会话尚未建立(无 sessionId),漏此条件
  // 会把取消结果提示整个吞掉——用户看不到「本轮已取消」的确认
  if (!sessionId && !streaming && !finalized && !askError && !props.stoppedNotice) {
    return (
      <div className="h-[60vh]">
        <EmptyState
          items={props.suggestions.items ?? []}
          loading={props.suggestions.loading}
          onAsk={props.onAsk}
        />
      </div>
    );
  }
  return (
    <>
      <ConversationBody {...props} />
      {streaming && (
        <StreamingBlock
          key={streaming.id}
          streaming={streaming}
          onResult={props.onResult}
          onError={props.onError}
          onTraceId={props.onTraceId}
        />
      )}
      {props.pendingConfirm && (
        <ToolConfirmCard
          pending={props.pendingConfirm.pending}
          sessionId={props.pendingConfirm.sessionId}
          onAllResolved={props.onAllResolved}
        />
      )}
      {props.stoppedNotice && <StoppedNotice kind={props.stoppedNotice.kind} />}
      {askError && <AskErrorNotice message={askError} />}
    </>
  );
}

/** 消息流列容器。 */
export function ChatThread(props: ChatThreadProps) {
  return (
    <div className="mx-auto max-w-3xl space-y-5 px-4 py-6">
      <ThreadBody {...props} />
    </div>
  );
}
