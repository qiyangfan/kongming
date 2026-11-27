/* 新建 API key 后的明文展示对话框(仅本次可见,关闭即不再显示)。 */

import { useState } from "react";
import { Check, Copy } from "lucide-react";

import { Button } from "@kongming/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@kongming/ui/dialog";

export function KeyShownDialog({
  apiKey,
  onClose,
}: {
  apiKey: string | null;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <Dialog open={apiKey !== null} onOpenChange={() => onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>API 密钥(仅本次显示)</DialogTitle>
          <DialogDescription>立即复制保存;关闭后不再可见,库内只存哈希。</DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2 rounded-md bg-muted/60 p-3">
          <code className="flex-1 break-all font-mono text-xs">{apiKey}</code>
          <Button
            size="icon"
            variant="outline"
            aria-label={copied ? "已复制" : "复制"}
            onClick={() => {
              if (!apiKey) return;
              void navigator.clipboard.writeText(apiKey);
              setCopied(true);
              window.setTimeout(() => setCopied(false), 2000);
            }}
          >
            {copied ? (
              <Check className="size-3.5" aria-hidden />
            ) : (
              <Copy className="size-3.5" aria-hidden />
            )}
          </Button>
        </div>
        <DialogFooter>
          <Button onClick={onClose}>我已保存</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
