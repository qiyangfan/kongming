/* 数据源域 API 封装:类型全生成(ADR-005,禁手写)。 管理端点(/system/datasource/*)全局视角;options 为登录即可的用户面接口。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type DatasourceItemOut = components["schemas"]["DatasourceItemOut"];
type DatasourcePageOut = components["schemas"]["DatasourcePageOut"];
export type DatasourceOptionOut = components["schemas"]["DatasourceOptionOut"];
type DatasourceCreateIn = components["schemas"]["DatasourceCreateIn"];
type DatasourceUpdateIn = components["schemas"]["DatasourceUpdateIn"];
type TestConnectionIn = components["schemas"]["TestConnectionIn"];
export type TestConnectionOut = components["schemas"]["TestConnectionOut"];

type CreatedId = { id: string };

export const datasourceApi = {
  page: (page: number, size: number, keyword: string) =>
    api.get<DatasourcePageOut>(
      `/system/datasource?page=${page}&size=${size}&keyword=${encodeURIComponent(keyword)}`,
    ),
  create: (body: DatasourceCreateIn) => api.post<CreatedId>("/system/datasource", body),
  update: (id: string, body: DatasourceUpdateIn) => api.put<void>(`/system/datasource/${id}`, body),
  enable: (id: string) => api.post<void>(`/system/datasource/${id}/enable`, {}),
  disable: (id: string) => api.post<void>(`/system/datasource/${id}/disable`, {}),
  remove: (id: string) => api.delete<void>(`/system/datasource/${id}`),
  testConnection: (body: TestConnectionIn) =>
    api.post<TestConnectionOut>("/system/datasource/test", body),
  /** 用户面选项(首位合成 default 项);X-Tenant-Id 由 api 层统一注入 */
  options: () => api.get<DatasourceOptionOut[]>("/system/datasource/options"),
};
