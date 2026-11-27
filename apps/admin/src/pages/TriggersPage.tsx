/* 管理触发器的创建、编辑、启停和执行历史。Webhook 密钥创建后只展示一次;编辑邮件触发器时凭证留空表示保留现值。 */

import { useState } from "react";
import { Plus } from "lucide-react";

import { Button } from "@kongming/ui/button";
import { PageHeader } from "@kongming/ui/page-header";
import { ErrorAlert } from "@kongming/ui/error-alert";
import type { TriggerItem } from "@kongming/api-client/trigger-api";
import { EMPTY_FORM, parseMaxRetries, toFormState, type FormState } from "./triggers-page-model";
import { useTriggerMutations } from "./triggers-page-mutations";
import {
  useCronPreview,
  useTriggerDatasources,
  useTriggersList,
  useTriggerRuns,
  useTriggerTargetQueries,
} from "./triggers-page-queries";
import { TriggersTable } from "./triggers-page-table";
import { TriggersFormDialog } from "./triggers-page-form-dialog";
import { TriggersHistoryDialog } from "./triggers-page-history-dialog";
import { TriggerDeleteDialog, WebhookCreatedDialog } from "./triggers-page-dialogs";

// 保存成功:关弹窗、清错误;新建时把 webhook 地址与密钥交给一次性展示弹窗
function afterSaved(saved: {
  out: { webhook_path: string | null; secret?: string } | null;
  id: string | null | undefined;
  setForm: (f: FormState | null) => void;
  setFormError: (m: string) => void;
  setCreated: (c: { webhook_path: string | null; secret?: string } | null) => void;
}) {
  saved.setForm(null);
  saved.setFormError("");
  // 密钥只在创建成功后展示一次。
  if (saved.id === null && saved.out) saved.setCreated(saved.out);
}

export default function TriggersPage() {
  const [form, setForm] = useState<FormState | null>(null);
  const [formError, setFormError] = useState("");
  const [created, setCreated] = useState<{ webhook_path: string | null; secret?: string } | null>(
    null,
  );
  const [deleting, setDeleting] = useState<TriggerItem | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const [historyOf, setHistoryOf] = useState<TriggerItem | null>(null);
  const [historyPage, setHistoryPage] = useState(1);
  const [toggleError, setToggleError] = useState<Record<string, string>>({});
  // 重试上限表单校验(0–5;非法禁保存)
  const retries = parseMaxRetries(form?.maxRetries ?? "0");

  const triggers = useTriggersList();
  const { workflows, bots } = useTriggerTargetQueries(form);
  const datasourceOptions = useTriggerDatasources(form);
  const { cronExprDebounced: cronExprD, cronFires } = useCronPreview(form);
  const runs = useTriggerRuns(historyOf, historyPage);

  // 写操作共享列表失效;回传结果交给弹窗状态处理
  const { save, toggle, remove } = useTriggerMutations({
    form,
    deleting,
    onSaved: (out) => afterSaved({ out, id: form?.id, setForm, setFormError, setCreated }),
    onSaveError: (m) => setFormError(m),
    onToggleError: setToggleError,
    onDeleted: () => {
      setDeleting(null);
      setDeleteError("");
    },
    onDeleteError: setDeleteError,
  });

  const rows = triggers.data ?? [];
  const isEmpty = !triggers.isPending && !triggers.isError && rows.length === 0;

  return (
    <div className="space-y-4">
      <PageHeader
        title="触发器"
        description="定时调度(常用频率点选,或高级 cron 表达式)、webhook 触发(外部系统调用专属地址即运行)或邮件触发(IMAP 收件箱到达新邮件即运行)工作流/助手;执行时以创建者身份经过完整安全校验。"
        actions={
          <Button
            onClick={() => {
              setForm(EMPTY_FORM);
              setFormError("");
            }}
          >
            <Plus aria-hidden />
            新建触发器
          </Button>
        }
      />

      {triggers.isError && <ErrorAlert />}

      <TriggersTable
        rows={rows}
        isLoading={triggers.isPending}
        isEmpty={isEmpty}
        togglePending={toggle.isPending}
        togglePendingId={toggle.variables?.row.id}
        toggleError={toggleError}
        onToggle={(row) => toggle.mutate({ row, enabled: !row.enabled })}
        onHistory={(row) => {
          setHistoryOf(row);
          setHistoryPage(1);
        }}
        onEdit={(row) => {
          setForm(toFormState(row));
          setFormError("");
        }}
        onDelete={(row) => {
          setDeleting(row);
          setDeleteError("");
        }}
      />

      <TriggersFormDialog
        form={form}
        formError={formError}
        savePending={save.isPending}
        retriesInvalid={retries.invalid}
        onChange={setForm}
        onClose={() => setForm(null)}
        onSubmit={() => save.mutate()}
        workflows={workflows}
        bots={bots}
        datasources={datasourceOptions}
        cronFires={cronFires}
        cronExprDebounced={cronExprD}
      />

      <WebhookCreatedDialog created={created} onClose={() => setCreated(null)} />

      <TriggersHistoryDialog
        trigger={historyOf}
        page={historyPage}
        items={runs.data?.items}
        total={runs.data?.total}
        isError={runs.isError}
        onPageChange={setHistoryPage}
        onClose={() => setHistoryOf(null)}
      />

      <TriggerDeleteDialog
        target={deleting}
        error={deleteError}
        pending={remove.isPending}
        onClose={() => setDeleting(null)}
        onConfirm={() => remove.mutate()}
      />
    </div>
  );
}
