/* 管理 MCP 服务和工具、资源、模板；编辑时密钥留空表示保留，清空键值表示删除。 */

import { Plus } from "lucide-react";

import { Button } from "@kongming/ui/button";
import { DataView } from "@kongming/ui/data-view";
import { ErrorAlert } from "@kongming/ui/error-alert";
import { PageHeader } from "@kongming/ui/page-header";

import { McpFormDialog } from "./mcp/McpFormDialog";
import { mcpColumns } from "./mcp/McpColumns";
import { DeleteMcpDialog, PromptPreviewDialog } from "./mcp/McpDialogs";
import { EMPTY_FORM } from "./mcp/mcp-form-model";
import { PrimitivesDialog } from "./mcp/PrimitivesDialog";
import { useMcpPageState } from "./mcp/use-mcp-page-state";

export default function McpPage() {
  const s = useMcpPageState();

  return (
    <div className="space-y-4">
      <PageHeader
        title="MCP 服务"
        description="外部 MCP server 的工具经桥接进入平台工具集(无 readOnlyHint 标注的工具按写工具走人工确认);自定义请求头加密落库,任何界面不回显明文。"
        actions={
          <Button
            onClick={() => {
              s.setForm(EMPTY_FORM);
              s.setFormError("");
            }}
          >
            <Plus aria-hidden />
            接入 MCP 服务
          </Button>
        }
      />

      {s.servers.isError && <ErrorAlert />}

      <DataView
        rows={s.servers.data ?? []}
        rowKey={(row) => row.id}
        isLoading={s.servers.isPending}
        isEmpty={!s.servers.isPending && !s.servers.isError && (s.servers.data ?? []).length === 0}
        emptyText="尚未接入 MCP 服务。"
        columns={mcpColumns({
          rowNote: s.rowNote,
          togglePending: s.toggle.isPending,
          toggleRowId: s.toggle.variables?.row.id,
          toggleError: s.toggleError,
          onToggle: (row) => s.toggle.mutate({ row, enabled: !row.enabled }),
        })}
        actions={[
          {
            // 行级禁用:测试/同步共用 busyKey,任一进行中即冻结全部行操作
            label: "测试",
            isDisabled: () => s.busyKey !== null,
            disabledReason: "操作进行中",
            onSelect: (row) => void s.runAction(row, "test"),
          },
          {
            label: "同步",
            isDisabled: () => s.busyKey !== null,
            disabledReason: "操作进行中",
            onSelect: (row) => void s.runAction(row, "sync"),
          },
          { label: "原语清单", onSelect: (row) => s.setPrimitives(row) },
          { label: "编辑", onSelect: s.openEdit },
          {
            label: "删除",
            destructive: true,
            onSelect: (row) => {
              s.setDeleting(row);
              s.setDeleteError("");
            },
          },
        ]}
      />

      {/* 新建/编辑对话框 */}
      <McpFormDialog
        form={s.form}
        onFormChange={s.setForm}
        error={s.formError}
        pending={s.save.isPending}
        onSubmit={() => s.save.mutate()}
        onClose={() => s.setForm(null)}
      />

      {/* 删除确认(受控:成功才关) */}
      <DeleteMcpDialog
        server={s.deleting}
        error={s.deleteError}
        pending={s.remove.isPending}
        onConfirm={() => s.remove.mutate()}
        onClose={() => s.setDeleting(null)}
      />

      <PrimitivesDialog
        server={s.primitives}
        onPreview={s.openPreview}
        onClose={() => {
          s.setPrimitives(null);
          s.setPreviewing(null);
        }}
      />

      <PromptPreviewDialog
        prompt={s.previewing}
        args={s.previewArgs}
        onArgsChange={s.setPreviewArgs}
        data={s.previewData}
        error={s.previewError}
        loading={s.previewLoading}
        canFetch={s.primitives !== null && s.previewing !== null}
        onFetch={() => {
          if (s.primitives !== null && s.previewing !== null) {
            void s.runPreview(s.primitives.id, s.previewing.name);
          }
        }}
        onClose={() => s.setPreviewing(null)}
      />
    </div>
  );
}
