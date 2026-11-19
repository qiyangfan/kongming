/* 知识库 API 使用生成类型；管理请求按当前租户校验，并支持团队归属筛选和变更。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type KnowledgeItemOut = components["schemas"]["KnowledgeItemOut"];
type KnowledgePageOut = components["schemas"]["KnowledgePageOut"];
export type UploadOut = components["schemas"]["UploadOut"];
export type FetchOut = components["schemas"]["FetchOut"];
export type ReindexOut = components["schemas"]["ReindexOut"];
type EditTableIn = components["schemas"]["EditTableIn"];
export type SchemasOut = components["schemas"]["SchemasOut"];
/* 召回试跑(只读验证面)响应 */
export type RecallProbeOut = components["schemas"]["RecallProbeOut"];
/* 归属筛选值:all=公共∪我所在团队(缺省形态)/public=仅公共/<team_id>=该团队 */
export type TeamScope = "all" | "public" | (string & {});

export const ragApi = {
  page: (params: { page: number; size: number; datasourceKey: string; teamScope?: TeamScope }) =>
    api.get<KnowledgePageOut>(
      `/system/rag/tables?page=${params.page}&size=${params.size}` +
        (params.datasourceKey
          ? `&datasource_key=${encodeURIComponent(params.datasourceKey)}`
          : "") +
        (params.teamScope && params.teamScope !== "all"
          ? `&team_scope=${encodeURIComponent(params.teamScope)}`
          : ""),
    ),
  allowedSchemas: (datasourceKey: string) =>
    api.get<SchemasOut>(`/system/rag/schemas?datasource_key=${encodeURIComponent(datasourceKey)}`),
  upload: (params: {
    file: File;
    datasourceKey: string;
    schemaName: string;
    teamId?: string | null;
  }) => {
    const form = new FormData();
    form.append("file", params.file);
    form.append("datasource_key", params.datasourceKey);
    form.append("schema_name", params.schemaName);
    if (params.teamId) form.append("team_id", params.teamId);
    return api.postForm<UploadOut>("/system/rag/documents/upload", form);
  },
  remove: (id: string) => api.delete<void>(`/system/rag/tables/${id}`),
  edit: (id: string, body: EditTableIn) =>
    api.put<KnowledgeItemOut>(`/system/rag/tables/${id}`, body),
  /* 归属变更(teamId=null=转公共;受控确认由调用面负责) */
  setOwnership: (id: string, teamId: string | null) =>
    api.put<KnowledgeItemOut>(`/system/rag/tables/${id}/ownership`, { team_id: teamId }),
  fetchMetadata: (datasourceKey: string) =>
    api.post<FetchOut>("/system/rag/fetch-metadata", { datasource_key: datasourceKey }),
  reindex: (datasourceKey: string) =>
    api.post<ReindexOut>("/system/rag/reindex", { datasource_key: datasourceKey }),
  /* 召回试跑:零副作用只读端点;调用面用 useMutation,不进查询缓存 */
  probe: (question: string, datasourceKey: string) =>
    api.get<RecallProbeOut>(
      `/system/rag/recall-probe?question=${encodeURIComponent(question)}` +
        `&datasource_key=${encodeURIComponent(datasourceKey)}`,
    ),
};
