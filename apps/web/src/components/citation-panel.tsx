/* 将正文引用键渲染为可定位来源的徽章，并提供可折叠来源列表；无来源时隐藏来源区。 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronRight, FileText } from "lucide-react";

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@kongming/ui/collapsible";
import { isCitationKeyShape, keySuffix } from "@kongming/api-client/citation-utils";

export interface CitationSource {
  citation_key: string;
  source_ref?: string;
  doc_name?: string;
}

/** 键尾四位(角标短显;全键 hover 展示) */
function keyTail(key: string): string {
  return keySuffix(key) || key;
}

/** 把正文中的【合法引用键】替换为角标,非键形态的中括号原样保留。
 * 匹配口径与 agent 侧 citations.sanitize_citations 同源:存活键(或其尾缀)即合法。 */
export function renderWithCitations(
  text: string,
  sources: CitationSource[],
  onCite?: (key: string) => void,
): ReactNode[] {
  if (sources.length === 0 || !text) return [text];
  const legal = new Set(sources.map((s) => s.citation_key));
  const suffixes = new Set(sources.map((s) => keySuffix(s.citation_key)));
  const out: ReactNode[] = [];
  const re = /【([^【】]*)】/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    const token = (m[1] ?? "").trim();
    const isLegal =
      legal.has(token) || (isCitationKeyShape(token) && suffixes.has(keySuffix(token)));
    if (!isLegal) continue; // 非键形态/编造键原样保留为文本,不渲染角标
    if (m.index > last) out.push(text.slice(last, m.index));
    const key = token;
    out.push(
      <span
        key={`cite-${i++}`}
        role="button"
        tabIndex={0}
        className="mx-0.5 inline-flex h-4 cursor-pointer items-center rounded bg-primary/10 px-1 align-baseline text-2xs font-medium text-primary underline-offset-2 hover:underline"
        title={`引用 ${key}`}
        onClick={() => onCite?.(key)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onCite?.(key);
          }
        }}
      >
        [{keyTail(key)}]
      </span>,
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out.length > 0 ? out : [text];
}

/** 来源区(可折叠;空=不渲染) */
export function CitationSourcesPanel({
  sources,
  activeKey,
}: {
  sources: CitationSource[];
  activeKey?: string | null;
}) {
  if (sources.length === 0) return null;
  return (
    <Collapsible>
      <CollapsibleTrigger className="group flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ChevronRight
          className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
          aria-hidden
        />
        <FileText className="size-3.5" aria-hidden />
        来源({sources.length})
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul className="mt-1.5 space-y-1 rounded-md border border-border bg-muted/40 p-3">
          {sources.map((s) => (
            <li
              key={s.citation_key}
              data-cite-key={s.citation_key}
              className={`space-y-0.5 rounded px-1 py-0.5 text-xs transition-colors ${
                activeKey === s.citation_key ? "bg-primary/10" : ""
              }`}
            >
              <span className="font-mono text-2xs text-muted-foreground">{s.citation_key}</span>
              <p className="text-foreground">{s.source_ref || s.doc_name || s.citation_key}</p>
            </li>
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  );
}

/** 角标点击 → 对应来源条目短时高亮;高亮态由各消息实例自行持有。 */
export function useCitationHighlight(): {
  activeKey: string | null;
  highlight: (key: string) => void;
} {
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    [],
  );
  return {
    activeKey,
    highlight: (key: string) => {
      setActiveKey(key);
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = setTimeout(() => setActiveKey(null), 1600);
    },
  };
}
