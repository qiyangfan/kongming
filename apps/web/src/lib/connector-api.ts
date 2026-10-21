/* 连接器域 API 封装。类型契约由 ADR-005 管线生成(禁手写);
   凭证任何接口不回显明文——列表/详情/创建/更新响应只含形态与尾号掩码。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type ConnectorOut = components["schemas"]["ConnectorOut"];
export type TestCallOut = components["schemas"]["TestCallOut"];

export type ConnectorParamIn = {
  name: string;
  type: string;
  required: boolean;
  description: string;
};

export type ConnectorBody = {
  key: string;
  name: string;
  description: string;
  method: string;
  path_template: string;
  params: ConnectorParamIn[];
  allowed_hosts: string[];
  auth_type: string;
  /** 凭证仅写入形态:创建=按鉴权形态必填;更新 留空=不修改、填入=替换 */
  auth_secret?: string | null;
  requires_confirmation: boolean | null;
  timeout_ms: number;
};

export function listConnectors(): Promise<ConnectorOut[]> {
  return api.get<ConnectorOut[]>("/system/connectors");
}

export function createConnector(body: ConnectorBody): Promise<ConnectorOut> {
  return api.post<ConnectorOut>("/system/connectors", body);
}

export function updateConnector(id: string, body: ConnectorBody): Promise<ConnectorOut> {
  return api.put<ConnectorOut>(`/system/connectors/${id}`, body);
}

export function setConnectorEnabled(id: string, enabled: boolean): Promise<void> {
  return api.post<void>(`/system/connectors/${id}/${enabled ? "enable" : "disable"}`, {});
}

export function deleteConnector(id: string): Promise<void> {
  return api.delete<void>(`/system/connectors/${id}`);
}

/** 测试调用(按当前表单声明真实出站一次;显式确认在 UI 层;落工具审计)。 */
export function testConnector(
  body: ConnectorBody & {
    args: Record<string, unknown>;
    use_saved_secret: boolean;
  },
): Promise<TestCallOut> {
  return api.post<TestCallOut>("/system/connectors/test-call", body);
}
