/* 对话页展示历史消息、接收流式回答，并在新会话时提供建议问题。sessionId 决定当前会话；请求失败时保留输入草稿。 */

import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowDown } from "lucide-react";
import { useParams } from "react-router";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { ChatQueryOut } from "@/lib/api";
import { useAgentMode } from "@/lib/agent-mode-store";
import { useAuth } from "@/lib/auth-store";
import { hasPerm } from "@/lib/nav";
import { useDatasource } from "@/lib/datasource-store";
import { useTenant } from "@/lib/tenant-store";

import { ChatThread } from "./chat/chat-thread";
import { useChatAttachments } from "./chat/use-chat-attachments";
import { useChatScroll } from "./chat/use-chat-scroll";
import { useChatSession, useForceBottomOnSessionChange } from "./chat/use-chat-session";
import { useChatStream } from "./chat/use-chat-stream";
import { useRecording } from "./chat/use-recording";
import { useReportDialog } from "./chat/use-report-dialog";
import { ComposerSection, MobileHeader } from "./chat/chat-page-shell";

export default function ChatPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  // 会话内一键生成报告(入口 a:既有问答产物为素材;新会话无 sid=直接指令)
  const canGenerateReport = hasPerm(
    useAuth((s) => s.user),
    "report:generate",
  );

  const { current } = useTenant();
  const datasource = useDatasource((s) => s.current);
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const [askError, setAskError] = useState<string | null>(null);
  const agentMode = useAgentMode((s) => s.mode);
  const setAgentMode = useAgentMode((s) => s.setMode);

  // navigate/invalidate 回调读最新值的稳定引用;sid 归一 null(useParams
  // 无参数时为 undefined,与流发起上下文的 null 归一比对,否则守卫恒 false)
  const sessionIdRef = useRef<string | null>(sessionId ?? null);
  sessionIdRef.current = sessionId ?? null;
  const tenantRef = useRef(current);
  tenantRef.current = current;
  const dsRef = useRef(datasource);
  dsRef.current = datasource;

  const scroll = useChatScroll();
  const { attachments, setAttachments, uploading, fileRef, onPickFile } =
    useChatAttachments(setAskError);
  const recorder = useRecording(setDraft, setAskError);
  const stream = useChatStream(
    { sessionIdRef, tenantRef, dsRef, cancelRecording: recorder.cancelRecording },
    qc,
    { setDraft, setAskError, setAttachments, setFollowBottom: scroll.setFollowBottom },
  );
  const report = useReportDialog(datasource, sessionIdRef);

  const { messages, suggestions, finalizedMsg } = useChatSession({
    sessionId,
    current,
    datasource,
    askError,
    stream,
    scroll,
  });

  useForceBottomOnSessionChange(sessionId, stream, scroll);

  const submit = () => stream.submit(undefined, { draft, uploading, agentMode, attachments });
  const streaming = stream.streaming;

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <MobileHeader />
      <ScrollArea ref={scroll.scrollRootRef} className="min-h-0 flex-1">
        <ChatThread
          sessionId={sessionId}
          messagesLoading={messages.isLoading}
          messages={messages.data}
          suggestions={{ items: suggestions.data?.items, loading: suggestions.isLoading }}
          streaming={streaming}
          finalized={stream.finalized}
          finalizedMsg={finalizedMsg}
          pendingConfirm={stream.pendingConfirm}
          stoppedNotice={stream.stoppedNotice}
          askError={askError}
          onAsk={(q) => stream.submit(q, { draft, uploading, agentMode, attachments })}
          onResult={(out: ChatQueryOut) => stream.finalizeOk(out, streaming?.question ?? "")}
          onError={(message: string) =>
            stream.finalizeError(message, streaming?.question ?? "", streaming?.attSnapshot)
          }
          onAllResolved={() => stream.setPendingConfirm(null)}
          onTraceId={(traceId) => {
            stream.traceIdRef.current = traceId;
          }}
        />
      </ScrollArea>

      {!scroll.followBottom && (
        <ScrollToBottomFab
          onClick={() => {
            stream.forceBottomRef.current = true;
            scroll.setFollowBottom(true);
            scroll.scrollToBottom("smooth");
          }}
        />
      )}

      <ComposerSection
        sessionId={sessionId}
        canGenerateReport={canGenerateReport}
        streaming={streaming}
        uploading={uploading}
        recorder={recorder}
        agentMode={agentMode}
        setAgentMode={setAgentMode}
        draft={draft}
        setDraft={setDraft}
        attachments={attachments}
        setAttachments={setAttachments}
        fileRef={fileRef}
        report={report}
        onPickFile={onPickFile}
        onSubmit={submit}
        onStop={stream.stopStreaming}
      />
    </div>
  );
}

/** 回到底部浮标——非跟随时出现;到达底部或点击回底后消失。 */
function ScrollToBottomFab({ onClick }: { onClick: () => void }) {
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-28 z-10 flex justify-center">
      <Button
        variant="secondary"
        size="sm"
        className="pointer-events-auto rounded-full shadow-md"
        aria-label="回到底部"
        onClick={onClick}
      >
        <ArrowDown aria-hidden />
        回到底部
      </Button>
    </div>
  );
}
