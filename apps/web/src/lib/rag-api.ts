/* 知识库域 API 封装(009):类型全生成(ADR-005,禁手写)。
   /system/rag/* 均为租户视角(X-Tenant-Id 由 api 层统一注入)+ system:rag:manage。
   102:归属筛选(team_scope)/上传归属(team_id)/归属变更(ownership)。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type KnowledgeItemOut = components["schemas"]["KnowledgeItemOut"];
export type KnowledgePageOut = components["schemas"]["KnowledgePageOut"];
export type UploadOut = components["schemas"]["UploadOut"];
export type FetchOut = components["schemas"]["FetchOut"];
export type ReindexOut = components["schemas"]["ReindexOut"];
export type EditTableIn = components["schemas"]["EditTableIn"];
export type SchemasOut = components["schemas"]["SchemasOut"];
/* 097 召回试跑(只读验证面)响应 */
export type RecallProbeOut = components["schemas"]["RecallProbeOut"];
/* 102 归属筛选值:all=公共∪我所在团队(缺省形态)/public=仅公共/<team_id>=该团队 */
export type TeamScope = "all" | "public" | (string & {});

export const ragApi = {
  page: (page: number, size: number, datasourceKey: string, teamScope?: TeamScope) =>
    api.get<KnowledgePageOut>(
      `/system/rag/tables?page=${page}&size=${size}` +
        (datasourceKey ? `&datasource_key=${encodeURIComponent(datasourceKey)}` : "") +
        (teamScope && teamScope !== "all" ? `&team_scope=${encodeURIComponent(teamScope)}` : ""),
    ),
  allowedSchemas: (datasourceKey: string) =>
    api.get<SchemasOut>(`/system/rag/schemas?datasource_key=${encodeURIComponent(datasourceKey)}`),
  upload: (file: File, datasourceKey: string, schemaName: string, teamId?: string | null) => {
    const form = new FormData();
    form.append("file", file);
    form.append("datasource_key", datasourceKey);
    form.append("schema_name", schemaName);
    if (teamId) form.append("team_id", teamId);
    return api.postForm<UploadOut>("/system/rag/documents/upload", form);
  },
  remove: (id: string) => api.delete<void>(`/system/rag/tables/${id}`),
  edit: (id: string, body: EditTableIn) =>
    api.put<KnowledgeItemOut>(`/system/rag/tables/${id}`, body),
  /* 102:归属变更(teamId=null=转公共;受控确认由调用面负责) */
  setOwnership: (id: string, teamId: string | null) =>
    api.put<KnowledgeItemOut>(`/system/rag/tables/${id}/ownership`, { team_id: teamId }),
  fetchMetadata: (datasourceKey: string) =>
    api.post<FetchOut>("/system/rag/fetch-metadata", { datasource_key: datasourceKey }),
  reindex: (datasourceKey: string) =>
    api.post<ReindexOut>("/system/rag/reindex", { datasource_key: datasourceKey }),
  /* 097:召回试跑(零副作用只读端点;不走 ctx 缓存,面板消费面) */
  probe: (question: string, datasourceKey: string) =>
    api.get<RecallProbeOut>(
      `/system/rag/recall-probe?question=${encodeURIComponent(question)}` +
        `&datasource_key=${encodeURIComponent(datasourceKey)}`,
    ),
};
