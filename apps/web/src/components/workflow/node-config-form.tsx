/* 节点配置侧栏表单(107 task 2.2):六类型必填配置与既有定义契约一一对齐
   (字段集 = models.py _REQUIRED_CONFIG ∪ 可选键,不加新语义),shadcn 标准件
   拼装;缺必填/JSON 语法等本地提示仅作 UX,结构校验语义仍在服务端;服务端
   校验错误经 configKey 定位到具体字段。 */

import { useState } from "react";
import { Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { FormRow } from "@/components/form-row";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  NODE_ID_RE,
  NODE_TYPE_LABELS,
  RESERVED_NODE_IDS,
  type WfNodeType,
} from "@/lib/workflow-canvas";
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
const FIELDS: Record<WfNodeType, readonly FieldSpec[]> = {
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

interface NodeConfigFormProps {
  nodeId: string;
  nodeType: WfNodeType;
  config: Record<string, unknown>;
  /** 目标节点的条件入边 on 值;null = 无条件入边(不显示该字段) */
  conditionOn: "true" | "false" | null;
  /** 服务端校验错误(已定位到本节点) */
  serverError: { message: string; configKey?: string } | null;
  takenIds: readonly string[];
  onConfigChange: (config: Record<string, unknown>) => void;
  onIdChange: (id: string) => void;
  onOnValueChange: (on: "true" | "false") => void;
  onDelete: () => void;
}

function configToString(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  return JSON.stringify(v, null, 2);
}

export function NodeConfigForm({
  nodeId,
  nodeType,
  config,
  conditionOn,
  serverError,
  takenIds,
  onConfigChange,
  onIdChange,
  onOnValueChange,
  onDelete,
}: NodeConfigFormProps) {
  const Icon = NODE_TYPE_ICONS[nodeType];
  const [idDraft, setIdDraft] = useState(nodeId);
  const [jsonDrafts, setJsonDrafts] = useState<Record<string, string>>({});
  const [numDraft, setNumDraft] = useState<string>(
    typeof config.max_iterations === "number" ? String(config.max_iterations) : "",
  );

  const setField = (key: string, value: unknown | undefined) => {
    const next = { ...config };
    if (value === undefined) delete next[key];
    else next[key] = value;
    onConfigChange(next);
  };

  const idError =
    idDraft === nodeId
      ? null
      : idDraft.length === 0
        ? "节点 id 不能为空"
        : !NODE_ID_RE.test(idDraft)
          ? "仅字母/数字/下划线"
          : RESERVED_NODE_IDS.has(idDraft)
            ? "与保留名冲突"
            : takenIds.includes(idDraft)
              ? "与其他节点 id 重复"
              : null;

  const fieldError = (key: string): string | null => {
    if (serverError?.configKey === key) return serverError.message;
    if (!(key in config)) return `${key} 为必填配置`;
    return null;
  };

  const renderField = (f: FieldSpec) => {
    const err = fieldError(f.key);
    const id = `wf-cfg-${f.key}`;
    return (
      <FormRow
        key={f.key}
        label={`${f.label}${f.required ? "" : "(可选)"}`}
        htmlFor={id}
        help={f.help}
      >
        {f.kind === "text" && (
          <Input
            id={id}
            value={configToString(config[f.key])}
            placeholder={f.placeholder}
            aria-invalid={err !== null}
            onChange={(e) => setField(f.key, e.target.value === "" ? undefined : e.target.value)}
          />
        )}
        {f.kind === "textarea" && (
          <Textarea
            id={id}
            rows={3}
            value={configToString(config[f.key])}
            placeholder={f.placeholder}
            aria-invalid={err !== null}
            className="font-mono text-xs"
            onChange={(e) => setField(f.key, e.target.value === "" ? undefined : e.target.value)}
          />
        )}
        {f.kind === "select" && (
          <Select
            value={typeof config[f.key] === "string" ? (config[f.key] as string) : ""}
            onValueChange={(v) => setField(f.key, v)}
          >
            <SelectTrigger id={id} className="w-full" aria-invalid={err !== null}>
              <SelectValue placeholder={f.required ? "必选" : "未设置"} />
            </SelectTrigger>
            <SelectContent>
              {(f.options ?? []).map((opt) => (
                <SelectItem key={opt} value={opt}>
                  <span className="font-mono">{opt}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {f.kind === "json" && (
          <JsonField
            id={id}
            draft={
              jsonDrafts[f.key] ??
              (config[f.key] == null ? "" : JSON.stringify(config[f.key], null, 2))
            }
            onDraft={(text, parsed) => {
              setJsonDrafts((d) => ({ ...d, [f.key]: text }));
              if (parsed === undefined && text === "") setField(f.key, undefined);
              else if (parsed !== undefined) setField(f.key, parsed);
              // 解析失败:只留草稿与提示,不写 config(语义仍交服务端把关)
            }}
            invalid={err !== null}
          />
        )}
        {f.kind === "number" && (
          <>
            <Input
              id={id}
              type="number"
              min={1}
              value={numDraft}
              aria-invalid={err !== null || (numDraft !== "" && !/^[1-9][0-9]*$/.test(numDraft))}
              onChange={(e) => {
                const raw = e.target.value;
                setNumDraft(raw);
                if (raw === "") setField(f.key, undefined);
                else if (/^[1-9][0-9]*$/.test(raw)) setField(f.key, Number(raw));
              }}
            />
            {numDraft !== "" && !/^[1-9][0-9]*$/.test(numDraft) && (
              <p className="text-xs text-destructive">须为 ≥1 的整数</p>
            )}
          </>
        )}
        {err !== null && f.kind !== "number" && <p className="text-xs text-destructive">{err}</p>}
      </FormRow>
    );
  };

  return (
    <div className="space-y-3">
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
            // 提交时机=失焦/回车(评审修复:逐键即时改名会更换 key={节点id} 的
            // 表单挂载,每键焦点丢失,多字符改名实际不可用);非法草稿回退现值
            if (idDraft !== nodeId && idError === null) onIdChange(idDraft);
            else setIdDraft(nodeId);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.currentTarget as HTMLInputElement).blur();
          }}
        />
        {idError && <p className="text-xs text-destructive">{idError}</p>}
      </FormRow>

      {conditionOn !== null && (
        <FormRow
          label="条件分支 on"
          htmlFor="wf-cfg-on"
          help="本节点依赖条件节点时的分支取值:条件成立走 true、不成立走 false;多条条件入边共用此值。"
        >
          <Select value={conditionOn} onValueChange={(v) => onOnValueChange(v as "true" | "false")}>
            <SelectTrigger id="wf-cfg-on" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="true">
                <span className="font-mono">true(条件成立)</span>
              </SelectItem>
              <SelectItem value="false">
                <span className="font-mono">false(条件不成立)</span>
              </SelectItem>
            </SelectContent>
          </Select>
        </FormRow>
      )}

      {FIELDS[nodeType].map(renderField)}

      {serverError && !serverError.configKey && (
        <p role="alert" className="text-xs text-destructive">
          {serverError.message}
        </p>
      )}
    </div>
  );
}

function unsafeParse(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
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
  const syntaxError = touched && draft !== "" && unsafeParse(draft) === undefined;
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
