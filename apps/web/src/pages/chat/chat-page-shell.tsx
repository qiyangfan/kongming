/* 对话页外壳：窄屏页头与底部输入区容器，把页面的流/录音/附件状态接到 ComposerBar。 */

import { SessionDrawer } from "@/components/session-drawer";

import { ComposerBar } from "./composer-bar";
import type { ChatStreamApi } from "./chat-stream-types";
import type { AttachmentChip } from "./use-chat-attachments";
import type { RecordingApi } from "./use-recording";
import type { ReportDialogApi } from "./use-report-dialog";

/** 窄屏页头(mobile-experience B2.3):会话抽屉唯一入口;宽屏侧栏常驻不渲染。 */
export function MobileHeader() {
  return (
    <header className="flex items-center gap-2 border-b border-border px-3 py-2 md:hidden">
      <SessionDrawer />
      <span className="text-sm font-medium tracking-widest text-foreground">孔明</span>
    </header>
  );
}

interface ComposerSectionProps {
  sessionId: string | undefined;
  canGenerateReport: boolean;
  streaming: ChatStreamApi["streaming"];
  uploading: boolean;
  recorder: RecordingApi;
  agentMode: string;
  setAgentMode: (mode: "react" | "recipe") => void;
  draft: string;
  setDraft: (value: string) => void;
  attachments: AttachmentChip[];
  setAttachments: React.Dispatch<React.SetStateAction<AttachmentChip[]>>;
  fileRef: React.RefObject<HTMLInputElement | null>;
  report: ReportDialogApi;
  onPickFile: (e: React.ChangeEvent<HTMLInputElement>) => Promise<void>;
  onSubmit: () => void;
  onStop: () => void;
  /** 语音通话入口(透传给输入区) */
  voiceEntry?: React.ReactNode;
}

/**
 * 输入区(mobile-experience B4.1):mb-3 与 BottomNav 呼吸分隔;
 * 底部安全区由导航层统一避让(bottom-nav pb-safe),此处不再叠加。
 */
export function ComposerSection(props: ComposerSectionProps) {
  const { recorder, setAttachments } = props;
  return (
    <div className="mb-3 border-t border-border bg-background px-4 py-3 md:mb-0">
      <div className="mx-auto max-w-3xl">
        <ComposerBar
          sessionId={props.sessionId}
          canGenerateReport={props.canGenerateReport}
          streaming={props.streaming !== null}
          uploading={props.uploading}
          recording={recorder.recording}
          transcribing={recorder.transcribing}
          agentMode={props.agentMode}
          setAgentMode={props.setAgentMode}
          draft={props.draft}
          setDraft={props.setDraft}
          attachments={props.attachments}
          fileRef={props.fileRef}
          report={props.report}
          onPickFile={props.onPickFile}
          onMicClick={recorder.onMicClick}
          onRemoveAttachment={(id) => setAttachments((prev) => prev.filter((x) => x.id !== id))}
          onSubmit={props.onSubmit}
          onStop={props.onStop}
          voiceEntry={props.voiceEntry}
        />
      </div>
    </div>
  );
}
