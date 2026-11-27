/* 上架到商店的对话框:填写欢迎语与建议问题后提交(生成版本快照并送审)。 */

import { Button } from "@kongming/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@kongming/ui/dialog";
import { FormRow } from "@kongming/ui/form-row";
import { Textarea } from "@kongming/ui/textarea";

export function PublishDialog({
  open,
  greeting,
  onGreetingChange,
  suggestions,
  onSuggestionsChange,
  error,
  pending,
  onSubmit,
  onClose,
}: {
  open: boolean;
  greeting: string;
  onGreetingChange: (v: string) => void;
  suggestions: string;
  onSuggestionsChange: (v: string) => void;
  error: string;
  pending: boolean;
  onSubmit: () => void;
  onClose: () => void;
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>上架到商店</DialogTitle>
          <DialogDescription>
            上架会对当前定义与商店内容创建不可变版本快照并经安全审核,商店展示与试运行始终使用「当前上架版本」——之后的草稿修改不影响在架版本,重新上架才生效。欢迎语与建议问题展示在商店卡片与详情页;内容无变化时重复上架不产生新版本。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <FormRow label="欢迎语" htmlFor="pub-greeting">
            <Textarea
              id="pub-greeting"
              rows={2}
              maxLength={200}
              value={greeting}
              onChange={(e) => onGreetingChange(e.target.value)}
              placeholder="如:我是订单分析助手,可以直接问我销量问题"
            />
          </FormRow>
          <FormRow label="建议问题(每行一条,至多 5 条)" htmlFor="pub-suggestions">
            <Textarea
              id="pub-suggestions"
              rows={3}
              value={suggestions}
              onChange={(e) => onSuggestionsChange(e.target.value)}
              placeholder={"上月销量最高的商品是什么\n各状态的订单有多少"}
            />
          </FormRow>
        </div>
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button disabled={pending} onClick={onSubmit}>
            {pending ? "上架中…" : "上架"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
