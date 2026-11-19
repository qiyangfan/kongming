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
}

/** 输入区工具栏：附件、语音、报告入口与运筹模式开关。 */
function ComposerTools({
  canGenerateReport,
  streaming,
  uploading,
  recording,
  transcribing,
  agentMode,
  setAgentMode,
  fileRef,
  report,
  onMicClick,
}: Pick<
  ComposerBarProps,
  | "canGenerateReport"
  | "streaming"
  | "uploading"
  | "recording"
  | "transcribing"
  | "agentMode"
  | "setAgentMode"
  | "fileRef"
  | "report"
  | "onMicClick"
>) {
  return (
    <div className="mb-2 flex items-center justify-between gap-2">
      {/* 附件钮置于工具行左位:输入行只留 [输入框|发送],水平间距以容器
          px-4 为单源;热区盒 24px(图标几何居中仅 4px),触控面经伪元素补至 40px */}
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
      {/* 语音输入:录音中 Mic 变警示色+脉冲;转写中文本填入输入框,
          用户可修正后发送;Escape 取消丢弃 */}
      <Button
        variant="ghost"
        size="icon-xs"
        className="after:-inset-2"
        onClick={() => void onMicClick()}
        disabled={transcribing || uploading || streaming}
        aria-label={recording ? "停止录音" : "语音输入"}
        title={recording ? "停止录音并转写(Escape 取消)" : "语音输入(说完点停止)"}
      >
        {transcribing ? (
          <Loader2 className="size-4 animate-spin" aria-hidden />
        ) : (
          <Mic
            className={recording ? "size-4 animate-pulse text-destructive" : "size-4"}
            aria-hidden
          />
        )}
      </Button>
      <div className="flex items-center gap-2">
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
          className="max-h-40 min-h-10 resize-none"
          aria-label="提问"
        />
        {/* 流式期间发送位切换为显式「停止接收」——
            中止读取并立即释放输入;服务端不中止(契约口径),文案对齐 */}
        {streaming ? (
          <Button
            size="icon"
            variant="outline"
            onClick={props.onStop}
            aria-label="停止接收本轮输出"
            title="停止接收本轮输出(该轮仍在后台完成)"
          >
            <StopCircle aria-hidden />
          </Button>
        ) : (
          <Button
            size="icon"
            onClick={props.onSubmit}
            disabled={!draft.trim() || uploading}
            aria-label="发送"
          >
            <SendHorizontal aria-hidden />
          </Button>
        )}
      </div>
    </>
  );
}
