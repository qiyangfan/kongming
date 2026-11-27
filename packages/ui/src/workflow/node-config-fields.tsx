/* 节点配置表单的字段规格（分类型，与契约逐键对齐）与字段级渲染。 */

import { useState, type KeyboardEvent, type ReactNode } from "react";
import { Trash2 } from "lucide-react";

import { Button } from "@kongming/ui/button";
import { FormRow } from "@kongming/ui/form-row";
import { Input } from "@kongming/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@kongming/ui/select";
import { Textarea } from "@kongming/ui/textarea";
import {
  NODE_TYPE_LABELS,
  type WfNodeType,
} from "@kongming/api-client/workflow-canvas";
import { NODE_TYPE_ICONS } from "./workflow-canvas-types";

type FieldKind = "text" | "textarea" | "select" | "json" | "number";

interface FieldSpec {
  key: string;
  label: string;
  required: boolean;
  kind: FieldKind;
  options?: readonly string[];
  help?: string;
  placeholder?: string;
}

/** 分类型字段规格(与契约逐键对齐) */
export const FIELDS: Record<WfNodeType, readonly FieldSpec[]> = {
  sql: [
    {
      key: "sql",
      label: "SQL 语句",
      required: true,
      kind: "textarea",
      help: "支持 {{节点id.outputs.字段}} 模板引用;结果自动过 SQL 防火墙。",
    },
    {
      key: "datasource_key",
      label: "数据源",
      required: false,
      kind: "text",
      help: "留空使用 default 数据源。",
    },
  ],
  http: [
    {
      key: "url",
      label: "URL",
      required: true,
      kind: "text",
      placeholder: "https://…",
      help: "支持模板引用;出站受 SSRF 防护约束。",
    },
    {
      key: "method",
      label: "方法",
      required: true,
      kind: "select",
      options: ["GET", "POST", "PUT", "DELETE", "PATCH"],
    },
    {
      key: "headers",
      label: "Headers",
      required: false,
      kind: "json",
      help: 'JSON 对象,如 {"X-A": "1"};值支持模板引用。',
    },
    {
      key: "body",
      label: "请求体",
      required: false,
      kind: "textarea",
      placeholder: "POST/PUT 时的请求体,支持模板引用",
    },
  ],
  condition: [
    {
      key: "left",
      label: "左值",
      required: true,
      kind: "text",
      help: "支持模板引用,如 {{q.outputs.row_count}}",
    },
    {
      key: "op",
      label: "比较符",
      required: true,
      kind: "select",
      options: ["==", "!=", ">=", "<=", ">", "<", "contains"],
    },
    {
      key: "right",
      label: "右值",
      required: true,
      kind: "text",
      help: "数值优先比较,否则字符串;contains 为子串。",
    },
  ],
  loop: [
    {
      key: "items_ref",
      label: "迭代数组引用",
      required: true,
      kind: "text",
      placeholder: "q.outputs.rows",
      help: "裸路径,不带大括号;须解析为数组。",
    },
    {
      key: "max_iterations",
      label: "迭代上限",
      required: true,
      kind: "number",
      help: "≥1 的整数,硬顶 100。",
    },
    {
      key: "body",
      label: "子节点数组",
      required: true,
      kind: "json",
      help: "JSON 数组,子节点同 {id,type,config,depends_on} 契约;子段内用 {{_loop.item}} 引用当前迭代项。",
    },
  ],
  transform: [
    {
      key: "template",
      label: "模板",
      required: true,
      kind: "textarea",
      help: "支持 {{节点id.outputs.字段}} 引用;整串恰为单引用时保留原值类型。",
    },
  ],
  agent: [
    {
      key: "instruction",
      label: "指令",
      required: true,
      kind: "textarea",
      help: "AI 数据问答指令,支持模板引用。",
    },
    {
      key: "datasource_key",
      label: "数据源",
      required: false,
      kind: "text",
      help: "留空使用 default 数据源。",
    },
  ],
};

/** 下拉展开时阻止 Backspace/Delete 传到画布，避免误删已选节点。 */
function CfgSelectContent({ children }: { children: ReactNode }) {
  const swallow = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Backspace" || e.key === "Delete") e.stopPropagation();
  };
  return <SelectContent onKeyDown={swallow}>{children}</SelectContent>;
}

export const INT_RE = /^[1-9]\d*$/;

/* 空值/字符串原样返回，其余对象序列化为两空格缩进 JSON 文本。 */
function configToString(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  return JSON.stringify(v, null, 2);
}

/* JSON 解析失败返回 undefined，由调用方区分「空」与「语法错误」。 */
function unsafeParse(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}

/* 必填字段仅在配置键缺失时报错；服务端该键错误优先透出。 */
function fieldError(
  f: FieldSpec,
  config: Record<string, unknown>,
  serverMessage: string | null,
): string | null {
  if (serverMessage !== null) return serverMessage;
  // 只对必填字段报缺省错误;可选字段带「(可选)」标签,恒报必填会自相矛盾
  if (f.required && !(f.key in config)) return `${f.key} 为必填配置`;
  return null;
}

/* 供单个字段渲染的最小上下文：当前配置、草稿与提交回调。 */
export type FieldContext = {
  config: Record<string, unknown>;
  setField: (key: string, value: unknown) => void;
  jsonDrafts: Record<string, string>;
  setJsonDraft: (key: string, text: string, parsed: unknown) => void;
  numDraft: string;
  setNumDraft: (raw: string) => void;
};

/* 文本/多行/下拉三种简单输入。 */
function SimpleField({
  f,
  err,
  ctx,
}: {
  f: FieldSpec;
  err: boolean;
  ctx: FieldContext;
}) {
  const { config, setField } = ctx;
  const id = `wf-cfg-${f.key}`;
  const onChange = (v: string) => setField(f.key, v === "" ? undefined : v);
  if (f.kind === "text") {
    return (
      <Input
        id={id}
        value={configToString(config[f.key])}
        placeholder={f.placeholder}
        aria-invalid={err}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  if (f.kind === "textarea") {
    return (
      <Textarea
        id={id}
        rows={3}
        value={configToString(config[f.key])}
        placeholder={f.placeholder}
        aria-invalid={err}
        className="font-mono text-xs"
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  if (f.kind !== "select") return null;
  return (
    <Select
      value={typeof config[f.key] === "string" ? (config[f.key] as string) : ""}
      onValueChange={(v) => setField(f.key, v)}
    >
      <SelectTrigger id={id} className="w-full" aria-invalid={err}>
        <SelectValue placeholder={f.required ? "必选" : "未设置"} />
      </SelectTrigger>
      <CfgSelectContent>
        {(f.options ?? []).map((opt) => (
          <SelectItem key={opt} value={opt}>
            <span className="font-mono">{opt}</span>
          </SelectItem>
        ))}
      </CfgSelectContent>
    </Select>
  );
}

/* JSON 字段：解析失败只留草稿与提示，不写 config（语义仍交服务端把关）。 */
function JsonConfigField({
  f,
  err,
  ctx,
}: {
  f: FieldSpec;
  err: boolean;
  ctx: FieldContext;
}) {
  const { config, jsonDrafts, setJsonDraft } = ctx;
  const id = `wf-cfg-${f.key}`;
  return (
    <JsonField
      id={id}
      draft={
        jsonDrafts[f.key] ??
        (config[f.key] == null ? "" : JSON.stringify(config[f.key], null, 2))
      }
      onDraft={(text, parsed) => setJsonDraft(f.key, text, parsed)}
      invalid={err}
    />
  );
}

/* 整数输入：空草稿清值，合法正整数即时写回，其余只保留草稿。 */
function NumberConfigField({
  f,
  err,
  ctx,
}: {
  f: FieldSpec;
  err: boolean;
  ctx: FieldContext;
}) {
  const { numDraft, setNumDraft } = ctx;
  const id = `wf-cfg-${f.key}`;
  const bad = numDraft !== "" && !INT_RE.test(numDraft);
  return (
    <>
      <Input
        id={id}
        type="number"
        min={1}
        value={numDraft}
        aria-invalid={err || bad}
        onChange={(e) => setNumDraft(e.target.value)}
      />
      {bad && <p className="text-xs text-destructive">须为 ≥1 的整数</p>}
    </>
  );
}

/* 按字段类型渲染单个字段，并附带服务端/必填错误提示。 */
function renderField(f: FieldSpec, invalid: boolean, ctx: FieldContext) {
  if (f.kind === "number")
    return <NumberConfigField f={f} err={invalid} ctx={ctx} />;
  if (f.kind === "json")
    return <JsonConfigField f={f} err={invalid} ctx={ctx} />;
  return <SimpleField f={f} err={invalid} ctx={ctx} />;
}

export function ConfigFieldRow({
  f,
  serverMessage,
  ctx,
}: {
  f: FieldSpec;
  serverMessage: string | null;
  ctx: FieldContext;
}) {
  const err = fieldError(f, ctx.config, serverMessage);
  const invalid = err !== null;
  const inner = renderField(f, invalid, ctx);
  return (
    <FormRow
      label={`${f.label}${f.required ? "" : "(可选)"}`}
      htmlFor={`wf-cfg-${f.key}`}
      help={f.help}
    >
      {inner}
      {err !== null && f.kind !== "number" && (
        <p className="text-xs text-destructive">{err}</p>
      )}
    </FormRow>
  );
}

/* 顶部类型图标与删除按钮。 */
export function NodeHeader({
  nodeType,
  nodeId,
  onDelete,
}: {
  nodeType: WfNodeType;
  nodeId: string;
  onDelete: () => void;
}) {
  const Icon = NODE_TYPE_ICONS[nodeType];
  return (
    <div className="flex items-center gap-2">
      <Icon className="size-4 shrink-0 text-primary" aria-hidden />
      <span className="text-xs font-medium tracking-wide text-muted-foreground">
        {NODE_TYPE_LABELS[nodeType]}
      </span>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="ml-auto size-7 text-destructive hover:text-destructive"
        aria-label={`删除节点 ${nodeId}`}
        onClick={onDelete}
      >
        <Trash2 className="size-3.5" aria-hidden />
      </Button>
    </div>
  );
}

/* 条件分支 on 下拉；仅当节点存在条件入边时由调用方渲染。 */
export function ConditionOnField({
  value,
  onChange,
}: {
  value: "true" | "false";
  onChange: (v: "true" | "false") => void;
}) {
  return (
    <FormRow
      label="条件分支 on"
      htmlFor="wf-cfg-on"
      help="本节点依赖条件节点时的分支取值:条件成立走 true、不成立走 false;多条条件入边共用此值。"
    >
      <Select
        value={value}
        onValueChange={(v) => onChange(v as "true" | "false")}
      >
        <SelectTrigger id="wf-cfg-on" className="w-full">
          <SelectValue />
        </SelectTrigger>
        <CfgSelectContent>
          <SelectItem value="true">
            <span className="font-mono">true(条件成立)</span>
          </SelectItem>
          <SelectItem value="false">
            <span className="font-mono">false(条件不成立)</span>
          </SelectItem>
        </CfgSelectContent>
      </Select>
    </FormRow>
  );
}

/* 节点 id 输入：提交时机=失焦/回车（逐键改名会更换表单挂载，每键焦点丢失），非法草稿回退现值。 */
export function NodeIdField({
  nodeId,
  idDraft,
  idError,
  setIdDraft,
  onIdChange,
}: {
  nodeId: string;
  idDraft: string;
  idError: string | null;
  setIdDraft: (v: string) => void;
  onIdChange: (id: string) => void;
}) {
  return (
    <FormRow
      label="节点 id"
      htmlFor="wf-cfg-id"
      help="仅字母/数字/下划线;改 id 不会自动改写其他节点里的模板引用与 loop 子段。"
    >
      <Input
        id="wf-cfg-id"
        value={idDraft}
        className="font-mono"
        aria-invalid={idError !== null}
        onChange={(e) => {
          setIdDraft(e.target.value);
        }}
        onBlur={() => {
          if (idDraft !== nodeId && idError === null) onIdChange(idDraft);
          else setIdDraft(nodeId);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur();
        }}
      />
      {idError && <p className="text-xs text-destructive">{idError}</p>}
    </FormRow>
  );
}

function JsonField({
  id,
  draft,
  onDraft,
  invalid,
}: {
  id: string;
  draft: string;
  onDraft: (text: string, parsed: unknown) => void;
  invalid: boolean;
}) {
  const [touched, setTouched] = useState(false);
  const syntaxError =
    touched && draft !== "" && unsafeParse(draft) === undefined;
  return (
    <>
      <Textarea
        id={id}
        rows={5}
        value={draft}
        aria-invalid={invalid || syntaxError}
        className="font-mono text-xs leading-relaxed"
        onChange={(e) => {
          setTouched(true);
          const text = e.target.value;
          onDraft(text, unsafeParse(text));
        }}
      />
      {syntaxError && <p className="text-xs text-destructive">JSON 语法错误</p>}
    </>
  );
}
