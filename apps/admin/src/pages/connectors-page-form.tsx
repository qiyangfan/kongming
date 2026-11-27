/* 连接器注册/编辑弹窗:声明标识、方法、路径模板、参数、出口主机与鉴权材料。 */

import { FlaskConical, Loader2, Plus, Trash2 } from "lucide-react";

import { Button } from "@kongming/ui/button";
import { Checkbox } from "@kongming/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@kongming/ui/dialog";
import { Input } from "@kongming/ui/input";
import { Label } from "@kongming/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import { Textarea } from "@kongming/ui/textarea";
import { FormRow } from "@kongming/ui/form-row";
import {
  AUTH_LABELS,
  METHOD_LABELS,
  PARAM_TYPE_LABELS,
  type FormState,
  type ParamRow,
} from "./connectors-page-model";

type ParamPatch = (i: number, patch: Partial<ParamRow>) => void;

// 路径模板说明:占位与参数声明对应,主机与 scheme 由白名单约束
function PathHint() {
  return (
    <p className="text-xs text-muted-foreground">
      完整 URL 模板;{"{占位}"}对应下方参数声明(值 URL 编码后直插), scheme 与端口由此约束。
    </p>
  );
}

function ParamEditor({
  params,
  onPatch,
  onAdd,
  onRemove,
}: {
  params: ParamRow[];
  onPatch: ParamPatch;
  onAdd: () => void;
  onRemove: (i: number) => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label>参数声明</Label>
        <Button type="button" variant="ghost" size="xs" onClick={onAdd}>
          <Plus aria-hidden />
          加一个参数
        </Button>
      </div>
      {params.length === 0 && (
        <p className="text-xs text-muted-foreground">无参数(无占位且无查询/body 入参)。</p>
      )}
      {params.map((p, i) => (
        <div key={i} className="space-y-1.5 rounded-md border p-2">
          <div className="flex items-center gap-2">
            <Input
              value={p.name}
              onChange={(e) => onPatch(i, { name: e.target.value })}
              placeholder="参数名(如 order_id)"
              className="font-mono text-xs"
              aria-label={`参数 ${i + 1} 名`}
            />
            <Select value={p.type} onValueChange={(v) => onPatch(i, { type: v })}>
              <SelectTrigger className="w-24" aria-label={`参数 ${i + 1} 类型`}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(PARAM_TYPE_LABELS).map(([k, label]) => (
                  <SelectItem key={k} value={k}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <label className="flex items-center gap-1.5 text-xs whitespace-nowrap">
              <Checkbox
                checked={p.required}
                onCheckedChange={(c) => onPatch(i, { required: c === true })}
                aria-label={`参数 ${i + 1} 必填`}
              />
              必填
            </label>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label={`移除参数 ${i + 1}`}
              onClick={() => onRemove(i)}
            >
              <Trash2 aria-hidden />
            </Button>
          </div>
          <Input
            value={p.description}
            onChange={(e) => onPatch(i, { description: e.target.value })}
            placeholder="参数说明(LLM 可见)"
            className="text-xs"
            aria-label={`参数 ${i + 1} 描述`}
          />
        </div>
      ))}
    </div>
  );
}

// 标识/名称/描述:工具的可识别信息,都会展示给 LLM
function BasicFields({ form, onChange }: { form: FormState; onChange: (f: FormState) => void }) {
  return (
    <>
      <FormRow label="标识(key)" htmlFor="conn-key">
        <Input
          id="conn-key"
          value={form.key}
          disabled={form.id !== null} // 创建后不可改(工具名单源)
          onChange={(e) => onChange({ ...form, key: e.target.value })}
          placeholder="小写字母开头,如 orders_api"
          className="font-mono"
        />
      </FormRow>
      <FormRow label="名称" htmlFor="conn-name">
        <Input
          id="conn-name"
          value={form.name}
          onChange={(e) => onChange({ ...form, name: e.target.value })}
          placeholder="LLM 据此判断何时调用(过输入护栏)"
        />
      </FormRow>
      <FormRow label="描述" htmlFor="conn-desc">
        <Textarea
          id="conn-desc"
          value={form.description}
          onChange={(e) => onChange({ ...form, description: e.target.value })}
          placeholder="工具用途与适用场景(LLM 可见;过输入护栏)"
          rows={2}
        />
      </FormRow>
    </>
  );
}

// 出口白名单、鉴权与写语义:决定出站可达范围与确认流
function PolicyFields({
  form,
  onChange,
  methodPlaceholder,
}: {
  form: FormState;
  onChange: (f: FormState) => void;
  methodPlaceholder: string;
}) {
  return (
    <>
      <FormRow label="出口主机白名单" htmlFor="conn-hosts">
        <Textarea
          id="conn-hosts"
          value={form.allowedHostsText}
          onChange={(e) => onChange({ ...form, allowedHostsText: e.target.value })}
          placeholder={"api.example.com\ncorp.internal.example"}
          rows={2}
          className="font-mono text-xs"
        />
        <p className="text-xs text-muted-foreground">
          一行一个精确主机(禁通配/路径/端口);模板主机必须命中,未命中出站即拒。
        </p>
      </FormRow>
      <FormRow label="鉴权形态" htmlFor="conn-auth">
        <Select value={form.authType} onValueChange={(v) => onChange({ ...form, authType: v })}>
          <SelectTrigger id="conn-auth" aria-label="鉴权形态">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(AUTH_LABELS).map(([k, label]) => (
              <SelectItem key={k} value={k}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </FormRow>
      {form.authType !== "none" && (
        <FormRow label="鉴权材料" htmlFor="conn-secret">
          <Input
            id="conn-secret"
            type="password"
            value={form.authSecret}
            onChange={(e) => onChange({ ...form, authSecret: e.target.value })}
            placeholder={form.id !== null ? "留空=不修改" : "凭证材料"}
            autoComplete="new-password"
            className="font-mono text-xs"
          />
          {form.id !== null && (
            <p className="text-xs text-muted-foreground">
              材料不回填(空框=不修改,填入=替换);Basic 形态填 user:password。
            </p>
          )}
        </FormRow>
      )}
      <FormRow label="写语义" htmlFor="conn-confirm">
        <Select
          value={form.confirmMode}
          onValueChange={(v) => onChange({ ...form, confirmMode: toConfirmMode(v) })}
        >
          <SelectTrigger id="conn-confirm" aria-label="写语义">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="auto">跟随方法缺省</SelectItem>
            <SelectItem value="yes">需确认(写语义)</SelectItem>
            <SelectItem value="no">豁免确认(读语义)</SelectItem>
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">{methodPlaceholder}</p>
      </FormRow>
      <FormRow label="超时(毫秒)" htmlFor="conn-timeout">
        <Input
          id="conn-timeout"
          type="number"
          min={100}
          value={form.timeoutMs}
          onChange={(e) => onChange({ ...form, timeoutMs: e.target.value })}
        />
      </FormRow>
    </>
  );
}

type ConnectorFormDialogProps = {
  form: FormState | null;
  formError: string;
  savePending: boolean;
  testPending: boolean;
  onChange: (f: FormState) => void;
  onClose: () => void;
  onSave: () => void;
  onTest: () => void;
};

function toConfirmMode(v: string): "yes" | "no" | "auto" {
  if (v === "yes") return "yes";
  if (v === "no") return "no";
  return "auto";
}

export function ConnectorFormDialog(props: ConnectorFormDialogProps) {
  const { form, formError, savePending, testPending, onChange, onClose, onSave, onTest } = props;
  const patchParam: ParamPatch = (i, patch) => {
    if (!form) return;
    onChange({ ...form, params: form.params.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  };
  const methodPlaceholder =
    form?.method === "GET" ? "缺省=豁免确认(读端点)" : "缺省=需确认(写端点,执行前走人工确认流)";
  return (
    <Dialog
      open={form !== null}
      onOpenChange={(o) => {
        if (savePending) return;
        if (!o) onClose();
      }}
    >
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{form?.id ? "编辑连接器" : "注册连接器"}</DialogTitle>
        </DialogHeader>
        {form && (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              onSave();
            }}
          >
            <BasicFields form={form} onChange={onChange} />
            <FormRow label="方法" htmlFor="conn-method">
              <Select
                value={form.method}
                onValueChange={(v) => onChange({ ...form, method: v === "POST" ? "POST" : "GET" })}
              >
                <SelectTrigger id="conn-method" aria-label="方法">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(METHOD_LABELS).map(([k, label]) => (
                    <SelectItem key={k} value={k}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormRow>
            <FormRow label="路径模板" htmlFor="conn-path">
              <Input
                id="conn-path"
                value={form.pathTemplate}
                onChange={(e) => onChange({ ...form, pathTemplate: e.target.value })}
                placeholder="https://api.example.com/v1/orders/{order_id}"
                className="font-mono text-xs"
              />
              <PathHint />
            </FormRow>

            <ParamEditor
              params={form.params}
              onPatch={patchParam}
              onAdd={() =>
                onChange({
                  ...form,
                  params: [
                    ...form.params,
                    { name: "", type: "string", required: false, description: "" },
                  ],
                })
              }
              onRemove={(i) => onChange({ ...form, params: form.params.filter((_, j) => j !== i) })}
            />

            <PolicyFields form={form} onChange={onChange} methodPlaceholder={methodPlaceholder} />
            {formError && (
              <p
                role="alert"
                className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm text-destructive"
              >
                {formError}
              </p>
            )}
            <DialogFooter className="sm:justify-between">
              <Button
                type="button"
                variant="outline"
                disabled={savePending || testPending}
                onClick={onTest}
              >
                <FlaskConical aria-hidden />
                测试调用
              </Button>
              <Button type="submit" disabled={savePending}>
                {savePending && <Loader2 className="animate-spin" aria-hidden />}
                保存
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
