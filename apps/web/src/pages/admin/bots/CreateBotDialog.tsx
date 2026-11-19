/* 新建助手对话框:填标识/名称/人设,并从工具注册表勾选可用工具。 */

import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ErrorAlert } from "@/components/error-alert";
import { FormRow } from "@/components/form-row";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { toolList } from "@/lib/toolcall-api";

export type BotForm = {
  bot_key: string;
  name: string;
  system_prompt: string;
  tools: string[];
};

export const EMPTY_BOT_FORM: BotForm = {
  bot_key: "",
  name: "",
  system_prompt: "",
  tools: ["execute_sql", "rag_retrieve"],
};

type RegistryQuery = ReturnType<typeof useQuery<Awaited<ReturnType<typeof toolList>>>>;

/** 勾选/取消一个工具名,返回新的工具列表。 */
function toggleTool(tools: string[], name: string): string[] {
  return tools.includes(name) ? tools.filter((t) => t !== name) : [...tools, name];
}

export function CreateBotDialog({
  open,
  onOpenChange,
  form,
  onFormChange,
  error,
  registry,
  pending,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  form: BotForm;
  onFormChange: (form: BotForm) => void;
  error: string | null;
  registry: RegistryQuery;
  pending: boolean;
  onSubmit: () => void;
}) {
  const invalid =
    !form.bot_key.trim() || !form.name.trim() || !form.system_prompt.trim() || pending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>新建助手</DialogTitle>
          <DialogDescription>
            可勾选的工具不超过你被授予的权限;助手的人设与守则会经安全审核。
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (invalid) {
              return;
            }
            onSubmit();
          }}
        >
          <FormRow
            label="标识"
            htmlFor="bot-key"
            help="小写字母开头,可含数字与下划线;创建后不可改。"
          >
            <Input
              id="bot-key"
              value={form.bot_key}
              onChange={(e) => onFormChange({ ...form, bot_key: e.target.value })}
              placeholder="after_sale"
            />
          </FormRow>
          <FormRow label="名称" htmlFor="bot-name">
            <Input
              id="bot-name"
              value={form.name}
              onChange={(e) => onFormChange({ ...form, name: e.target.value })}
              placeholder="售后助手"
            />
          </FormRow>
          <FormRow label="人设与守则" htmlFor="bot-prompt">
            <Textarea
              id="bot-prompt"
              value={form.system_prompt}
              onChange={(e) => onFormChange({ ...form, system_prompt: e.target.value })}
              placeholder="你是售后客服助手…"
              rows={4}
            />
          </FormRow>
          <FormRow
            label="可用工具"
            help="从清单勾选该助手可用的工具;不勾选时,助手仅对话,不执行任何操作。"
          >
            <ScrollArea className="h-40 rounded-md border p-2">
              <div className="space-y-1 pr-2">
                {(registry.data?.tools ?? []).map((t) => (
                  <label
                    key={t.name}
                    className="flex cursor-pointer items-start gap-2 rounded px-1 py-1 hover:bg-muted/60"
                  >
                    <Checkbox
                      checked={form.tools.includes(t.name)}
                      onCheckedChange={() =>
                        onFormChange({ ...form, tools: toggleTool(form.tools, t.name) })
                      }
                      className="mt-0.5"
                      aria-label={`工具 ${t.name}`}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <span className="font-mono text-xs font-medium">{t.name}</span>
                        <Badge variant={t.write ? "destructive" : "secondary"}>
                          {t.write ? "写" : "只读"}
                        </Badge>
                      </span>
                      <span
                        className="block truncate text-xs text-muted-foreground"
                        title={t.description}
                      >
                        {t.description}
                      </span>
                    </span>
                  </label>
                ))}
                {registry.isError ? (
                  <ErrorAlert text="工具清单加载失败,请关闭后重试" />
                ) : (
                  registry.isPending && (
                    <p className="py-4 text-center text-xs text-muted-foreground">清单加载中…</p>
                  )
                )}
              </div>
            </ScrollArea>
          </FormRow>
          {error && (
            <p className="text-xs text-destructive" role="alert">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button type="submit" disabled={invalid}>
              {pending && <Loader2 className="animate-spin" aria-hidden />}
              创建
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
