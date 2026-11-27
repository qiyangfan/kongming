/* MCP 服务的新建/编辑对话框:标识、名称、Endpoint 与自定义请求头。 */

import { Loader2, Plus, Trash2 } from "lucide-react";

import { Button } from "@kongming/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@kongming/ui/dialog";
import { FormRow } from "@kongming/ui/form-row";
import { Input } from "@kongming/ui/input";
import { Label } from "@kongming/ui/label";

import { replaceHeader, type FormState } from "./mcp-form-model";

export function McpFormDialog({
  form,
  onFormChange,
  error,
  pending,
  onSubmit,
  onClose,
}: {
  form: FormState | null;
  onFormChange: (form: FormState) => void;
  error: string;
  pending: boolean;
  onSubmit: () => void;
  onClose: () => void;
}) {
  return (
    <Dialog
      open={form !== null}
      onOpenChange={(o) => {
        if (pending) return; // 提交中不可关
        if (!o) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{form?.id ? "编辑 MCP 服务" : "接入 MCP 服务"}</DialogTitle>
        </DialogHeader>
        {form && (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              onSubmit();
            }}
          >
            <FormRow label="标识(server_key)" htmlFor="mcp-key">
              <Input
                id="mcp-key"
                value={form.serverKey}
                disabled={form.id !== null} // 创建后不可改
                onChange={(e) => onFormChange({ ...form, serverKey: e.target.value })}
                placeholder="小写字母开头,如 fs"
                className="font-mono"
              />
            </FormRow>
            <FormRow label="名称" htmlFor="mcp-name">
              <Input
                id="mcp-name"
                value={form.name}
                onChange={(e) => onFormChange({ ...form, name: e.target.value })}
              />
            </FormRow>
            <FormRow label="Endpoint(Streamable HTTP)" htmlFor="mcp-endpoint">
              <Input
                id="mcp-endpoint"
                value={form.endpoint}
                onChange={(e) => onFormChange({ ...form, endpoint: e.target.value })}
                placeholder="https://example.com/mcp"
                className="font-mono text-xs"
              />
            </FormRow>
            <HeaderRows form={form} onFormChange={onFormChange} />
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <DialogFooter>
              <Button type="submit" disabled={pending}>
                {pending && <Loader2 className="animate-spin" aria-hidden />}
                保存并同步
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** 自定义请求头编辑区:新增/改名/改值/移除,编辑态锁定已有键名。 */
function HeaderRows({
  form,
  onFormChange,
}: {
  form: FormState;
  onFormChange: (form: FormState) => void;
}) {
  const removeAt = (i: number) => {
    const removed = form.headers[i];
    onFormChange({
      ...form,
      headers: form.headers.filter((_, j) => j !== i),
      removedKeys:
        form.id !== null && removed?.key.trim()
          ? [...form.removedKeys, removed.key.trim()]
          : form.removedKeys,
    });
  };
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label>自定义请求头</Label>
        <Button
          type="button"
          variant="ghost"
          size="xs"
          onClick={() =>
            onFormChange({ ...form, headers: [...form.headers, { key: "", value: "" }] })
          }
        >
          <Plus aria-hidden />
          加一行
        </Button>
      </div>
      {form.headers.length === 0 && (
        <p className="text-xs text-muted-foreground">无(按需添加鉴权头)</p>
      )}
      {form.headers.map((h, i) => (
        <div key={i} className="flex items-center gap-2">
          <Input
            value={h.key}
            disabled={form.existingKeys.includes(h.key)}
            onChange={(e) =>
              onFormChange({
                ...form,
                headers: replaceHeader(form.headers, i, { key: e.target.value }),
              })
            }
            placeholder="Authorization"
            className="font-mono text-xs"
            aria-label={`请求头键 ${i + 1}`}
          />
          <Input
            value={h.value}
            type="password"
            onChange={(e) =>
              onFormChange({
                ...form,
                headers: replaceHeader(form.headers, i, { value: e.target.value }),
              })
            }
            placeholder={form.id ? "留空保持现值" : "值"}
            className="font-mono text-xs"
            aria-label={`请求头值 ${i + 1}`}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label={`移除请求头 ${i + 1}`}
            onClick={() => removeAt(i)}
          >
            <Trash2 aria-hidden />
          </Button>
        </div>
      ))}
      {form.id !== null && (
        <p className="text-xs text-muted-foreground">值留空=保持现值;删除行=保存后移除该请求头。</p>
      )}
    </div>
  );
}
