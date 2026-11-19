/* 消息流：骨架屏、空态、历史消息、终态快照/在途轮、待确认卡与错误提示。 */

import { Info, TriangleAlert } from "lucide-react";

import { AssistantCard } from "@/components/assistant-card";
import { ChatBubble } from "@/components/chat-bubble";
import { StreamingTurn } from "@/components/streaming-turn";
import { ToolConfirmCard } from "@/components/tool-confirm-card";
import { Skeleton } from "@/components/ui/skeleton";
import type { ChatQueryOut, SessionMessageOut } from "@/lib/api";
import { humanizeErrorText } from "@/lib/api";

import { EmptyState } from "./empty-state";
import { MessageRow, UserTurn } from "./message-row";
import type { PendingConfirm, StreamingTurnStart } from "./use-chat-stream";

interface ChatThreadProps {
  sessionId: string | undefined;
  messagesLoading: boolean;
  messages: SessionMessageOut[] | undefined;
  suggestions: { items?: string[]; loading: boolean };
  streaming: StreamingTurnStart | null;
  finalized: { question: string; out: ChatQueryOut } | null;
  finalizedMsg: SessionMessageOut | null;
  pendingConfirm: PendingConfirm | null;
  stoppedNotice: boolean;
  askError: string | null;
  onAsk: (q: string) => void;
  onResult: (out: ChatQueryOut) => void;
  onError: (message: string) => void;
  onAllResolved: () => void;
}

/** 停止接收后的口径呈现——禁"已取消/已停止生成"类误导措辞。 */
function StoppedNotice() {
  return (
    <div
      className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm"
      role="status"
    >
      <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
      <div>
        <p className="text-foreground">已不再接收本轮输出。</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          该轮问答仍在后台完成,可稍后回看;问句已回到输入框,也可立即重发。
        </p>
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
}: {
  streaming: StreamingTurnStart;
  onResult: ChatThreadProps["onResult"];
  onError: ChatThreadProps["onError"];
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
  if (!sessionId && !streaming && !finalized && !askError) {
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
        <StreamingBlock streaming={streaming} onResult={props.onResult} onError={props.onError} />
      )}
      {props.pendingConfirm && (
        <ToolConfirmCard
          pending={props.pendingConfirm.pending}
          sessionId={props.pendingConfirm.sessionId}
          onAllResolved={props.onAllResolved}
        />
      )}
      {props.stoppedNotice && <StoppedNotice />}
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
