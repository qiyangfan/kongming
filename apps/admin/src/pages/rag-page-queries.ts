/* 知识库页的查询与写操作:列表、候选(数据源/schema/团队)、上传/编辑/删除/扫描/重建/归属变更。 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { datasourceApi } from "@kongming/api-client/datasource-api";
import {
  ragApi,
  type FetchOut,
  type KnowledgeItemOut,
  type ReindexOut,
  type TeamScope,
  type UploadOut,
} from "@kongming/api-client/rag-api";
import { listMyTeams } from "@kongming/api-client/team-api";

const PAGE_SIZE = 10;

// 写操作统一改写的页顶提示落点
type RagNotices = {
  setNotice: (v: string | null) => void;
  setError: (v: string | null) => void;
};

type UploadVars = { file: File; ds: string; schema: string; teamId: string };
type EditVars = { id: string; comment: string; alias: string };
type OwnershipVars = { id: string; teamId: string | null };

export function useRagQueries(params: {
  page: number;
  dsFilter: string;
  teamScope: TeamScope;
  uploadDs: string;
}) {
  const { page, dsFilter, teamScope, uploadDs } = params;
  const options = useQuery({ queryKey: ["rag", "ds-options"], queryFn: datasourceApi.options });
  // 我所在的团队(上传归属候选=OWNER/MEMBER 可写团队;筛选用全部所在团队)
  const myTeams = useQuery({ queryKey: ["teams", "mine"], queryFn: listMyTeams });
  const writableTeams = (myTeams.data ?? []).filter(
    (t) => t.role === "OWNER" || t.role === "MEMBER",
  );
  // 可扫描 schema 集合(上传表单候选源,随上传数据源联动)
  const schemas = useQuery({
    queryKey: ["rag", "schemas", uploadDs],
    queryFn: () => ragApi.allowedSchemas(uploadDs),
  });
  const list = useQuery({
    queryKey: ["rag", "tables", page, dsFilter, teamScope],
    queryFn: () => ragApi.page({ page, size: PAGE_SIZE, datasourceKey: dsFilter, teamScope }),
  });
  return { options, myTeams, writableTeams, schemas, list };
}

// 各写操作的成功/失败统一改写页顶提示;扫描与重建附明细
function noticeHandlers(n: RagNotices, onReindexDone: () => void) {
  return {
    upload: {
      onSuccess: (out: UploadOut) => {
        n.setNotice(`上传成功:${out.tables.join("、")}`);
        n.setError(null);
      },
      // SCHEMA_NOT_ALLOWED 等边界错误已由 lib/api 统一转译为中文
      onError: (e: Error) => {
        n.setError(`上传失败:${e.message}`);
        n.setNotice(null);
      },
    },
    setOwnership: {
      onSuccess: (out: KnowledgeItemOut) => {
        n.setNotice(ownershipNotice(out));
        n.setError(null);
      },
      onError: (e: Error) => {
        n.setError(`归属变更失败:${e.message}`);
        n.setNotice(null);
      },
    },
    remove: {
      onSuccess: () => {
        n.setNotice("已删除");
        n.setError(null);
      },
      onError: (e: Error) => {
        n.setError(`删除失败:${e.message}`);
        n.setNotice(null);
      },
    },
    edit: {
      onSuccess: (out: KnowledgeItemOut) => {
        const alias = out.alias ? `(${out.alias})` : "";
        n.setNotice(`已保存:${out.table_name}${alias}`);
        n.setError(null);
      },
      onError: (e: Error) => {
        n.setError(`保存失败:${e.message}`);
        n.setNotice(null);
      },
    },
    fetchMeta: {
      onSuccess: (out: FetchOut) => {
        n.setNotice(fetchMetaNotice(out));
        n.setError(null);
      },
      onError: (e: Error) => {
        n.setError(`扫描数据源失败:${e.message}`);
        n.setNotice(null);
      },
    },
    reindex: {
      onSuccess: (out: ReindexOut) => {
        onReindexDone();
        n.setNotice(`重建完成:清空 ${out.deleted} 条,重建 ${out.rebuilt} 表(润色 ${out.enriched})`);
        n.setError(null);
      },
      onError: (e: Error) => {
        onReindexDone();
        n.setError(`重建索引失败:${e.message}`);
      },
    },
  };
}

export function useRagMutations(notices: RagNotices, onReindexDone: () => void) {
  const qc = useQueryClient();
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["rag", "tables"] });
  };
  const h = noticeHandlers(notices, onReindexDone);

  return {
    upload: useMutation({
      mutationFn: (v: UploadVars) =>
        ragApi.upload({
          file: v.file,
          datasourceKey: v.ds,
          schemaName: v.schema,
          teamId: v.teamId || null,
        }),
      ...h.upload,
      onSettled: invalidate,
    }),
    setOwnership: useMutation({
      mutationFn: (v: OwnershipVars) => ragApi.setOwnership(v.id, v.teamId),
      ...h.setOwnership,
      onSettled: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: string) => ragApi.remove(id),
      ...h.remove,
      onSettled: invalidate,
    }),
    edit: useMutation({
      mutationFn: (v: EditVars) => ragApi.edit(v.id, { table_comment: v.comment, alias: v.alias }),
      ...h.edit,
      onSettled: invalidate,
    }),
    fetchMeta: useMutation({
      mutationFn: (ds: string) => ragApi.fetchMetadata(ds || "default"),
      ...h.fetchMeta,
      onSettled: invalidate,
    }),
    reindex: useMutation({
      mutationFn: (ds: string) => ragApi.reindex(ds || "default"),
      ...h.reindex,
      onSettled: invalidate,
    }),
  };
}

// 扫描结果摘要:发现/已生成/人工精修未覆盖/清理失效条目
function fetchMetaNotice(out: FetchOut): string {
  const parts = [`发现 ${out.found} 张表,已生成 ${out.enriched} 张的说明`];
  if (out.skipped > 0) parts.push(`${out.skipped} 张人工精修未覆盖`);
  if (out.removed > 0) parts.push(`清理 ${out.removed} 条失效条目`);
  return `扫描完成:${parts.join(",")}`;
}

// 归属变更结果提示;未归属时提示转为租户公共
function ownershipNotice(out: KnowledgeItemOut): string {
  return out.team_name
    ? `已归属团队「${out.team_name}」:${out.table_name}`
    : `已转为租户公共:${out.table_name}`;
}
