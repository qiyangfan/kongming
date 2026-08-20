/* 知识库域 API 封装(009):类型全生成(ADR-005,禁手写)。
   /system/rag/* 均为租户视角(X-Tenant-Id 由 api 层统一注入)+ system:rag:manage。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type KnowledgeItemOut = components["schemas"]["KnowledgeItemOut"];
export type KnowledgePageOut = components["schemas"]["KnowledgePageOut"];
export type UploadOut = components["schemas"]["UploadOut"];
export type FetchOut = components["schemas"]["FetchOut"];
export type ReindexOut = components["schemas"]["ReindexOut"];

export const ragApi = {
  page: (page: number, size: number, datasourceKey: string) =>
    api.get<KnowledgePageOut>(
      `/system/rag/tables?page=${page}&size=${size}` +
        (datasourceKey ? `&datasource_key=${encodeURIComponent(datasourceKey)}` : ""),
    ),
  upload: (file: File, datasourceKey: string, schemaName: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("datasource_key", datasourceKey);
    form.append("schema_name", schemaName);
    return api.postForm<UploadOut>("/system/rag/documents/upload", form);
  },
  remove: (id: string) => api.delete<void>(`/system/rag/tables/${id}`),
  fetchMetadata: (datasourceKey: string) =>
    api.post<FetchOut>("/system/rag/fetch-metadata", { datasource_key: datasourceKey }),
  reindex: (datasourceKey: string) =>
    api.post<ReindexOut>("/system/rag/reindex", { datasource_key: datasourceKey }),
};
