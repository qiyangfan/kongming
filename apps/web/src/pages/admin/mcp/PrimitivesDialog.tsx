/* 原语清单对话框:工具/资源/模板分区;清单来自最近一次同步快照,模板可打开只读预览。 */

import { Eye } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { McpPromptEntryOut, McpServerOut } from "@/lib/mcp-api";

/** 分区标题右侧的计数徽章。 */
function SectionTitle({ label, count }: { label: string; count: number }) {
  return (
    <h3 className="flex items-center gap-2 text-sm font-medium">
      {label}
      <Badge variant="secondary">{count}</Badge>
    </h3>
  );
}

export function PrimitivesDialog({
  server,
  onPreview,
  onClose,
}: {
  server: McpServerOut | null;
  onPreview: (prompt: McpPromptEntryOut) => void;
  onClose: () => void;
}) {
  return (
    <Dialog
      open={server !== null}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
    >
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>原语清单「{server?.name}」</DialogTitle>
        </DialogHeader>
        {server && (
          <div className="space-y-5">
            <section>
              <SectionTitle label="工具" count={server.tools.length} />
              {server.tools.length === 0 ? (
                <p className="mt-1.5 text-xs text-muted-foreground">无(同步后可见)。</p>
              ) : (
                <ul className="mt-1.5 space-y-1.5">
                  {server.tools.map((t) => (
                    <li key={t.qualified} className="text-xs">
                      <span className="font-mono">{t.qualified}</span>
                      {t.read_only && (
                        <Badge variant="outline" className="ml-1.5">
                          只读
                        </Badge>
                      )}
                      {t.description && <p className="text-muted-foreground">{t.description}</p>}
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section>
              <SectionTitle label="资源" count={server.resources.length} />
              {server.resources.length === 0 ? (
                <p className="mt-1.5 text-xs text-muted-foreground">
                  无(server 未提供 resources 原语时如实为 0)。
                </p>
              ) : (
                <ul className="mt-1.5 space-y-1.5">
                  {server.resources.map((r) => (
                    <li key={r.uri} className="text-xs">
                      <span className="font-mono">{r.uri}</span>
                      {r.media_type && (
                        <Badge variant="outline" className="ml-1.5">
                          {r.media_type}
                        </Badge>
                      )}
                      {r.name && <span className="ml-1.5">{r.name}</span>}
                      {r.description && <p className="text-muted-foreground">{r.description}</p>}
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-1.5 text-xs text-muted-foreground">
                资源经工具 mcp__{server.server_key}__read_resource 读取(uri 白名单约束)。
              </p>
            </section>
            <section>
              <SectionTitle label="提示模板" count={server.prompts.length} />
              {server.prompts.length === 0 ? (
                <p className="mt-1.5 text-xs text-muted-foreground">
                  无(server 未提供 prompts 原语时如实为 0)。
                </p>
              ) : (
                <ul className="mt-1.5 space-y-1.5">
                  {server.prompts.map((p) => (
                    <li key={p.name} className="flex items-start justify-between gap-2 text-xs">
                      <div className="min-w-0">
                        <span className="font-mono">{p.name}</span>
                        {p.description && <p className="text-muted-foreground">{p.description}</p>}
                        {(p.arguments ?? []).length > 0 && (
                          <p className="text-muted-foreground">
                            参数:
                            {(p.arguments ?? [])
                              .map((a) => (a.required ? `${a.name}*` : a.name))
                              .join(", ")}
                            (* 为必填)
                          </p>
                        )}
                      </div>
                      <Button
                        type="button"
                        variant="ghost"
                        size="xs"
                        aria-label={`预览模板 ${p.name}`}
                        onClick={() => onPreview(p)}
                      >
                        <Eye aria-hidden />
                        预览
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <DialogFooter>
              <Button variant="outline" onClick={onClose}>
                关闭
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
