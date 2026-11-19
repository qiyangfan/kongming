/* 助手详情对话框:简介、统计、上架版本、工具清单、建议问题(点击即问)与应答。 */

import { Loader2, Play } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ErrorAlert } from "@/components/error-alert";
import type { StoreBotDetail } from "@/lib/store-api";

import { Stars } from "./store-bot-card";

interface BotDetailBodyProps {
  detail: StoreBotDetail;
  askBusy: string;
  answer: string | undefined;
  onAsk: (question: string) => void;
}

/** 详情正文:统计行、版本行、工具徽标、建议问题与最近一次应答。 */
function BotDetailBody({ detail, askBusy, answer, onAsk }: BotDetailBodyProps) {
  // 任一助手应答中即禁用全部建议问题(与货架页原口径一致)
  const idle = askBusy === "";
  return (
    <div className="space-y-3">
      {detail.greeting && <p className="text-sm">{detail.greeting}</p>}
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <Stars value={detail.avg_score} />
        <span>{detail.rating_count} 人评</span>
        <span>{detail.use_count} 次使用</span>
        <span>{detail.fav_count} 收藏</span>
        {detail.my_rating && <Badge variant="secondary">我评 {detail.my_rating} 星</Badge>}
      </div>
      {/* 当前上架版本号与上架时间(纯增字段;商店语义=消费该版本快照) */}
      {detail.version_no !== null && detail.version_no !== undefined && (
        <p className="text-xs text-muted-foreground">
          上架版本 v{detail.version_no}
          {detail.version_published_at
            ? ` · ${new Date(detail.version_published_at).toLocaleString()} 上架`
            : ""}
        </p>
      )}
      {(detail.tools ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1">
          {detail.tools.map((t) => (
            <Badge key={t} variant="outline" className="font-mono">
              {t}
            </Badge>
          ))}
        </div>
      )}
      {(detail.suggestions ?? []).length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs text-muted-foreground">建议问题(点击即问):</p>
          {detail.suggestions.map((sug) => (
            <Button
              key={sug}
              variant="outline"
              size="sm"
              className="mr-1.5"
              disabled={!idle}
              onClick={() => onAsk(sug)}
            >
              {askBusy === detail.id ? (
                <Loader2 className="animate-spin" aria-hidden />
              ) : (
                <Play aria-hidden />
              )}
              {sug}
            </Button>
          ))}
        </div>
      )}
      {answer && (
        <p role="status" className="text-sm text-muted-foreground">
          {answer}
        </p>
      )}
    </div>
  );
}

interface StoreDetailDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  detailKey: string | null;
  detail: { data: StoreBotDetail | undefined; isError: boolean };
  askBusy: string;
  answer: Record<string, string>;
  onAsk: (bot: StoreBotDetail, question: string) => void;
}

/** 详情对话框外壳;加载/错误态也保留标题,便于读屏识别对话框。 */
export function StoreDetailDialog(props: StoreDetailDialogProps) {
  const { detail } = props;
  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent className="max-w-lg">
        {/* DialogHeader 恒渲染(加载/错误态也有 Title,读屏可识别对话框) */}
        <DialogHeader>
          <DialogTitle>{detail.data?.name ?? "助手详情"}</DialogTitle>
          <DialogDescription className="font-mono text-xs">
            {detail.data ? detail.data.bot_key : props.detailKey}
          </DialogDescription>
        </DialogHeader>
        {detail.isError && <ErrorAlert />}
        {detail.data && (
          <BotDetailBody
            detail={detail.data}
            askBusy={props.askBusy}
            answer={props.answer[detail.data.id]}
            onAsk={(q) => props.onAsk(detail.data!, q)}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
