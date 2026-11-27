/* 管理知识库条目：按数据源和归属筛选，支持上传、编辑说明、删除、扫描及重建索引。 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";

import { Button } from "@kongming/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@kongming/ui/tooltip";
import { PageHeader } from "@kongming/ui/page-header";
import type { KnowledgeItemOut, TeamScope } from "@kongming/api-client/rag-api";
import type { TeamItem } from "@kongming/api-client/team-api";
import { RecallProbePanel } from "@kongming/ui/recall-probe-panel";
import { RagUploadSection } from "./rag-page-upload";
import { RagTable } from "./rag-page-table";
import {
  RagDeleteDialog,
  RagEditDialog,
  RagOwnershipDialog,
  RagReindexDialog,
} from "./rag-page-dialogs";
import { DocumentsSection } from "./rag-page-documents";
import { useRagMutations, useRagQueries } from "./rag-page-queries";

const PAGE_SIZE = 10;

function HeaderActions({
  actionDs,
  options,
  fetchPending,
  reindexPending,
  onActionDsChange,
  onScan,
  onReindex,
}: {
  actionDs: string;
  options: { datasource_key: string; name: string }[] | undefined;
  fetchPending: boolean;
  reindexPending: boolean;
  onActionDsChange: (v: string) => void;
  onScan: () => void;
  onReindex: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <Select value={actionDs || "default"} onValueChange={onActionDsChange}>
        <SelectTrigger className="w-40">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {(options ?? []).map((o) => (
            <SelectItem key={o.datasource_key} value={o.datasource_key}>
              {o.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="outline" onClick={onScan} disabled={fetchPending}>
            {fetchPending ? "扫描中…" : "扫描数据源"}
          </Button>
        </TooltipTrigger>
        <TooltipContent className="max-w-72">
          读取该数据源的表结构,由 AI
          生成每张表的业务说明并保存到知识库(耗时较长);人工精修的条目不会被覆盖。
        </TooltipContent>
      </Tooltip>
      <Button variant="destructive" onClick={onReindex} disabled={reindexPending}>
        重建索引
      </Button>
    </div>
  );
}

// 页顶:定位提示、页头动作与操作结果提示(错误优先)
function RagPageTop({
  locateTable,
  onClearLocate,
  header,
  error,
  notice,
}: {
  locateTable: string | null;
  onClearLocate: () => void;
  header: ReactNode;
  error: string | null;
  notice: string | null;
}) {
  return (
    <>
      {locateTable && (
        <div className="flex items-center justify-between rounded-lg border border-ring/40 bg-accent/40 px-3 py-2 text-sm">
          <span>
            已定位自反馈详情:表 <code className="font-mono">{locateTable}</code>
          </span>
          <Button variant="ghost" size="xs" aria-label="清除定位" onClick={onClearLocate}>
            清除
          </Button>
        </div>
      )}
      <PageHeader title="知识库" actions={header} />

      {(error || notice) && (
        <p
          role="alert"
          className={error ? "text-sm text-destructive" : "text-sm text-muted-foreground"}
        >
          {error ?? notice}
        </p>
      )}
    </>
  );
}
/** 已选 schema 不在当前数据源集合(换源/集合变化)时回落到首项。 */
function useUploadSchemaFallback(
  schemas: string[] | undefined,
  current: string,
  setSchema: (v: string) => void,
) {
  useEffect(() => {
    const first = schemas?.[0];
    if (schemas && first !== undefined && !schemas.includes(current)) setSchema(first);
  }, [schemas, current, setSchema]);
}

export default function RagPage() {
  // ?table=<名> 用于从反馈详情定位条目,并显示提示和行高亮。
  const [searchParams, setSearchParams] = useSearchParams();
  const locateTable = searchParams.get("table");
  const [page, setPage] = useState(1);
  const [dsFilter, setDsFilter] = useState("");
  const [actionDs, setActionDs] = useState("");
  const [uploadSchema, setUploadSchema] = useState("");
  const [uploadDs, setUploadDs] = useState("default");
  // 上传归属(""=公共,否则团队 id)与列表归属筛选
  const [uploadTeamId, setUploadTeamId] = useState("");
  const [teamScope, setTeamScope] = useState<TeamScope>("all");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeItemOut | null>(null);
  const [reindexOpen, setReindexOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<KnowledgeItemOut | null>(null);
  const [editComment, setEditComment] = useState("");
  const [editAlias, setEditAlias] = useState("");
  // 归属变更确认框(target=条目;dest=框内选择的目标归属)
  const [ownershipTarget, setOwnershipTarget] = useState<KnowledgeItemOut | null>(null);
  const [ownershipDest, setOwnershipDest] = useState<string>("public");
  const fileRef = useRef<HTMLInputElement>(null);

  const q = useRagQueries({ page, dsFilter, teamScope, uploadDs });
  const m = useRagMutations({ setNotice, setError }, () => setReindexOpen(false));

  useUploadSchemaFallback(q.schemas.data?.schemas, uploadSchema, setUploadSchema);

  // 数据源动作下拉缺省对齐筛选
  useEffect(() => {
    if (!actionDs && dsFilter) setActionDs(dsFilter);
  }, [actionDs, dsFilter]);

  const total = q.list.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // 删除末页唯一条目后重新计算页码，避免显示空页。
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  const changeScope = (setter: (v: TeamScope) => void) => (v: TeamScope) => {
    setter(v);
    setPage(1);
  };
  const items = q.list.data?.items ?? [];

  return (
    <div className="space-y-4">
      <RagPageTop
        locateTable={locateTable}
        onClearLocate={() => setSearchParams({}, { replace: true })}
        header={
          <HeaderActions
            actionDs={actionDs}
            options={q.options.data}
            fetchPending={m.fetchMeta.isPending}
            reindexPending={m.reindex.isPending}
            onActionDsChange={setActionDs}
            onScan={() => m.fetchMeta.mutate(actionDs || "default")}
            onReindex={() => setReindexOpen(true)}
          />
        }
        error={error}
        notice={notice}
      />

      <RagUploadSection
        options={q.options.data}
        uploadDs={uploadDs}
        onUploadDsChange={setUploadDs}
        uploadSchema={uploadSchema}
        onUploadSchemaChange={setUploadSchema}
        schemas={q.schemas}
        uploadTeamId={uploadTeamId}
        onUploadTeamIdChange={setUploadTeamId}
        writableTeams={q.writableTeams}
        fileRef={fileRef}
        uploading={m.upload.isPending}
        onUploadFile={(file) =>
          m.upload.mutate({ file, ds: uploadDs, schema: uploadSchema, teamId: uploadTeamId })
        }
      />

      {/* 召回试跑面板只读验证知识表，不写入知识库；数据源选项复用本页查询。 */}
      <RecallProbePanel options={q.options.data ?? []} />

      <RagTable
        items={items}
        isLoading={q.list.isPending}
        isEmpty={!q.list.isPending && items.length === 0}
        options={q.options.data}
        dsFilter={dsFilter}
        onDsFilterChange={changeScope(setDsFilter)}
        teamScope={teamScope}
        onTeamScopeChange={changeScope(setTeamScope)}
        myTeams={q.myTeams.data ?? []}
        total={total}
        page={page}
        totalPages={totalPages}
        onPageChange={setPage}
        locateTable={locateTable}
        canChangeOwnership={q.writableTeams.length > 0}
        onEdit={(item) => {
          setEditComment(item.table_comment);
          setEditAlias(item.alias);
          setEditTarget(item);
        }}
        onOwnership={(item) => {
          setOwnershipTarget(item);
          // 预选与现状相对的目标(公共条目→首个可写团队;团队条目→公共)
          setOwnershipDest(item.team_id ? "public" : (q.writableTeams[0]?.id ?? "public"));
        }}
        onDelete={setDeleteTarget}
      />

      <RagDialogs
        m={m}
        dialog={{
          editTarget,
          setEditTarget,
          editComment,
          setEditComment,
          editAlias,
          setEditAlias,
          deleteTarget,
          setDeleteTarget,
          reindexOpen,
          setReindexOpen,
          actionDs,
          ownershipTarget,
          setOwnershipTarget,
          ownershipDest,
          setOwnershipDest,
        }}
        writableTeams={q.writableTeams}
      />

      {/* 业务文档区:政策/流程类文档上传与维护(与表知识双通道隔离) */}
      <DocumentsSection datasource={actionDs || "default"} />
    </div>
  );
}

// 四个确认弹窗:成功回调里再关闭,避免先关框导致行级反馈丢失
type DialogState = {
  editTarget: KnowledgeItemOut | null;
  setEditTarget: (v: KnowledgeItemOut | null) => void;
  editComment: string;
  setEditComment: (v: string) => void;
  editAlias: string;
  setEditAlias: (v: string) => void;
  deleteTarget: KnowledgeItemOut | null;
  setDeleteTarget: (v: KnowledgeItemOut | null) => void;
  reindexOpen: boolean;
  setReindexOpen: (v: boolean) => void;
  actionDs: string;
  ownershipTarget: KnowledgeItemOut | null;
  setOwnershipTarget: (v: KnowledgeItemOut | null) => void;
  ownershipDest: string;
  setOwnershipDest: (v: string) => void;
};

function RagDialogs({
  m,
  dialog: d,
  writableTeams,
}: {
  m: ReturnType<typeof useRagMutations>;
  dialog: DialogState;
  writableTeams: TeamItem[];
}) {
  return (
    <>
      <RagEditDialog
        target={d.editTarget}
        comment={d.editComment}
        alias={d.editAlias}
        onCommentChange={d.setEditComment}
        onAliasChange={d.setEditAlias}
        onClose={() => d.setEditTarget(null)}
        edit={{
          ...m.edit,
          mutate: (v) => m.edit.mutate(v, { onSuccess: () => d.setEditTarget(null) }),
        }}
      />
      <RagDeleteDialog
        target={d.deleteTarget}
        onClose={() => d.setDeleteTarget(null)}
        remove={{
          ...m.remove,
          mutate: (id) => m.remove.mutate(id, { onSuccess: () => d.setDeleteTarget(null) }),
        }}
      />
      <RagReindexDialog
        open={d.reindexOpen}
        onOpenChange={d.setReindexOpen}
        actionDs={d.actionDs}
        reindex={{ ...m.reindex, mutate: () => m.reindex.mutate(d.actionDs || "default") }}
      />
      <RagOwnershipDialog
        target={d.ownershipTarget}
        dest={d.ownershipDest}
        onDestChange={d.setOwnershipDest}
        writableTeams={writableTeams}
        onClose={() => d.setOwnershipTarget(null)}
        setOwnership={{
          ...m.setOwnership,
          mutate: (v) => m.setOwnership.mutate(v, { onSuccess: () => d.setOwnershipTarget(null) }),
        }}
      />
    </>
  );
}
