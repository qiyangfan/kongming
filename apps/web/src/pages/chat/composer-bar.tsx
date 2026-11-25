/* 输入区：运筹模式开关、图片/语音入口、附件 chips 与发送/停止按钮。 */

import { FileText, Loader2, Mic, Paperclip, SendHorizontal, StopCircle, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
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
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

import type { AttachmentChip } from "./use-chat-attachments";
import type { ReportDialogApi } from "./use-report-dialog";

interface ComposerBarProps {
  sessionId: string | undefined;
  canGenerateReport: boolean;
  streaming: boolean;
  uploading: boolean;
  recording: boolean;
  transcribing: boolean;
  agentMode: string;
  setAgentMode: (mode: "react" | "recipe") => void;
  draft: string;
  setDraft: (value: string) => void;
  attachments: AttachmentChip[];
  fileRef: React.RefObject<HTMLInputElement | null>;
  report: ReportDialogApi;
  onPickFile: (e: React.ChangeEvent<HTMLInputElement>) => Promise<void>;
  onMicClick: () => Promise<void>;
  onRemoveAttachment: (id: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  /** 语音通话入口(插槽:输入框为空且非流式时替代发送键;是否可用由页面按能力探测决定) */
  voiceEntry?: React.ReactNode;
}

/** 输入区工具栏：附件、报告入口与运筹模式开关。 */
function ComposerTools({
  canGenerateReport,
  streaming,
  uploading,
  agentMode,
  setAgentMode,
  fileRef,
  report,
}: Pick<
  ComposerBarProps,
  | "canGenerateReport"
  | "streaming"
  | "uploading"
  | "agentMode"
  | "setAgentMode"
  | "fileRef"
  | "report"
>) {
  return (
    // 桌面习语:动作钮聚左成一组,模式开关(状态而非动作)独立居右;窄屏同样适配
    <div className="mb-2 flex items-center justify-between gap-2">
      <div className="flex items-center gap-1.5">
        {/* 附件钮:热区盒 24px(图标几何居中仅 4px),触控面经伪元素补至 40px */}
        <Button
          variant="ghost"
          size="icon-xs"
          className="after:-inset-2"
          onClick={() => fileRef.current?.click()}
          disabled={uploading || streaming}
          aria-label="添加图片附件"
          title="添加图片(png/jpg/webp/gif,≤10MB)"
        >
          {/* 图标显式钉 size-4(icon-xs 默认 12px 经 :not 排除),与发送键同大 */}
          {uploading ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <Paperclip className="size-4" aria-hidden />
          )}
        </Button>
        {canGenerateReport && (
          <Button
            variant="ghost"
            size="icon-xs"
            className="after:-inset-2"
            onClick={() => report.setOpen(true)}
            disabled={streaming}
            aria-label="生成报告"
            title="基于本会话问答生成分析报告"
          >
            <FileText className="size-4" aria-hidden />
          </Button>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Switch
          id="agent-mode"
          checked={agentMode === "react"}
          onCheckedChange={(on) => setAgentMode(on ? "react" : "recipe")}
          aria-label="运筹模式"
        />
        <Tooltip>
          <TooltipTrigger asChild>
            <Label htmlFor="agent-mode" className="cursor-pointer text-xs text-muted-foreground">
              运筹模式
            </Label>
          </TooltipTrigger>
          <TooltipContent className="max-w-72">
            AI 将自主规划步骤、调用工具求解,适合复杂问题;日常提问无需开启。
          </TooltipContent>
        </Tooltip>
      </div>
    </div>
  );
}

/** 报告要求表单；入口 a 描述本会话素材，入口 b 由 AI 自主取数。 */
function ReportDialog({
  sessionId,
  report,
}: {
  sessionId: string | undefined;
  report: ReportDialogApi;
}) {
  return (
    <Dialog open={report.open} onOpenChange={report.setOpen}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>生成分析报告</DialogTitle>
          <DialogDescription>
            {sessionId
              ? "以本会话既有问答为素材生成;也可只写指令,由 AI 自主取数。"
              : "描述报告目标与数据范围,AI 经问数链路自主取数。"}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="report-instruction">报告要求</Label>
            <Textarea
              id="report-instruction"
              value={report.instruction}
              onChange={(e) => report.setInstruction(e.target.value)}
              rows={3}
              maxLength={2000}
              placeholder={
                sessionId ? "例:面向周会,重点讲异常与趋势" : "例:出一份近 30 天销售分析报告"
              }
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="report-title">标题(可选)</Label>
            <Input
              id="report-title"
              value={report.title}
              onChange={(e) => report.setTitle(e.target.value)}
              maxLength={200}
              placeholder="留空则由 AI 拟定"
            />
          </div>
          {report.error && (
            <p role="alert" className="text-sm text-destructive">
              {report.error}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => report.setOpen(false)}>
            取消
          </Button>
          <Button
            onClick={() => void report.submit()}
            disabled={report.busy || !report.instruction.trim()}
          >
            {report.busy ? "发起中…" : "发起生成"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** 附件 chips：展示已上传附件并可移除。 */
function AttachmentChips({
  attachments,
  onRemove,
}: {
  attachments: AttachmentChip[];
  onRemove: (id: string) => void;
}) {
  if (attachments.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5 pb-1">
      {attachments.map((a) => (
        <Badge key={a.id} variant="secondary">
          <Paperclip className="size-3" aria-hidden />
          {a.filename}
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={`移除附件 ${a.filename}`}
            onClick={() => onRemove(a.id)}
          >
            <X aria-hidden />
          </Button>
        </Badge>
      ))}
    </div>
  );
}

/** 右端动作键:流式期取消、空草稿时语音通话入口、否则发送。 */
function ComposerActionButton({
  streaming,
  draft,
  uploading,
  voiceEntry,
  onSubmit,
  onStop,
}: Pick<
  ComposerBarProps,
  "streaming" | "draft" | "uploading" | "voiceEntry" | "onSubmit" | "onStop"
>) {
  if (streaming) {
    return (
      <Button
        size="icon"
        variant="outline"
        onClick={onStop}
        aria-label="取消本轮问答"
        title="取消本轮(已开始执行的操作不会被撤回)"
      >
        <StopCircle aria-hidden />
      </Button>
    );
  }
  if (!draft.trim() && voiceEntry) {
    return <>{voiceEntry}</>;
  }
  return (
    <Button size="icon" onClick={onSubmit} disabled={!draft.trim() || uploading} aria-label="发送">
      <SendHorizontal aria-hidden />
    </Button>
  );
}

/** 底部输入区：附件/chips、报告对话框与草稿发送。 */
export function ComposerBar(props: ComposerBarProps) {
  const { streaming, uploading, draft, setDraft, attachments, fileRef } = props;
  return (
    <>
      <ComposerTools {...props} />
      <ReportDialog sessionId={props.sessionId} report={props.report} />
      <AttachmentChips attachments={attachments} onRemove={props.onRemoveAttachment} />
      <div className="flex items-end gap-2">
        <input
          ref={fileRef}
          type="file"
          accept=".png,.jpg,.jpeg,.webp,.gif"
          className="hidden"
          aria-hidden
          tabIndex={-1}
          onChange={(e) => void props.onPickFile(e)}
        />
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              props.onSubmit();
            }
          }}
          placeholder="问计于数据,如问计于孔明…"
          rows={1}
          className="max-h-40 min-h-10"
          aria-label="提问"
        />
        {/* 语音输入收进输入行右端(紧邻语音通话/发送键):录音中变警示色+脉冲,转写文本填入草稿 */}
        <Button
          variant="ghost"
          size="icon"
          onClick={() => void props.onMicClick()}
          disabled={props.transcribing || uploading || streaming}
          aria-label={props.recording ? "停止录音" : "语音输入"}
          title={props.recording ? "停止录音并转写(Escape 取消)" : "语音输入(说完点停止)"}
        >
          {props.transcribing ? (
            <Loader2 className="animate-spin" aria-hidden />
          ) : (
            <Mic
              className={props.recording ? "animate-pulse text-destructive" : undefined}
              aria-hidden
            />
          )}
        </Button>
        <ComposerActionButton
          streaming={streaming}
          draft={draft}
          uploading={uploading}
          voiceEntry={props.voiceEntry}
          onSubmit={props.onSubmit}
          onStop={props.onStop}
        />
      </div>
    </>
  );
}
