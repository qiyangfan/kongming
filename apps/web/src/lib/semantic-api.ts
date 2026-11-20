/* semantic 域 API 封装( 语义层)。类型契约由 ADR-005 管线生成(禁手写)。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type RelationItem = components["schemas"]["RelationOut"];
export type MetricItem = components["schemas"]["MetricOut"];
type IntrospectOut = components["schemas"]["IntrospectOut"];

export function listRelations(datasourceKey: string): Promise<RelationItem[]> {
  return api.get<RelationItem[]>(`/system/semantic/relations?datasource_key=${datasourceKey}`);
}

export function introspectRelations(datasourceKey: string): Promise<IntrospectOut> {
  return api.post("/system/semantic/relations/introspect", { datasource_key: datasourceKey });
}

export function addRelation(input: {
  datasource_key: string;
  source_table: string;
  target_table: string;
  join_keys: { left_col: string; right_col: string }[];
}): Promise<{ id: string }> {
  return api.post("/system/semantic/relations", input);
}

export function deleteRelation(id: string): Promise<void> {
  return api.delete(`/system/semantic/relations/${id}`);
}

export function listMetrics(datasourceKey: string): Promise<MetricItem[]> {
  return api.get<MetricItem[]>(`/system/semantic/metrics?datasource_key=${datasourceKey}`);
}

export function upsertMetric(input: {
  datasource_key: string;
  metric_name: string;
  sql_template: string;
  business_terms: string[];
  enabled: boolean;
  visible_scope?: string | undefined;
  dept_id?: string | null | undefined;
}): Promise<{ id: string }> {
  return api.post("/system/semantic/metrics", input);
}

export function deleteMetric(id: string): Promise<void> {
  return api.delete(`/system/semantic/metrics/${id}`);
}
