/* team 域 API 封装；请求和响应类型由 ADR-005 生成。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type TeamItem = components["schemas"]["TeamOut"];
type TeamMemberItem = components["schemas"]["TeamMemberOut"];

export function listMyTeams(): Promise<TeamItem[]> {
  return api.get<TeamItem[]>("/system/teams");
}

export function getTeam(id: string): Promise<{
  team: TeamItem;
  members: TeamMemberItem[];
}> {
  return api.get(`/system/teams/${id}`);
}

export function createTeam(body: {
  team_key: string;
  name: string;
}): Promise<{ id: string }> {
  return api.post("/system/teams", body);
}

export function deleteTeam(id: string): Promise<{ ok: boolean }> {
  return api.delete(`/system/teams/${id}`);
}

export function addTeamMember(
  teamId: string,
  userId: string,
  role: string,
): Promise<{ ok: boolean }> {
  return api.post(`/system/teams/${teamId}/members`, { user_id: userId, role });
}

export function changeTeamMemberRole(
  teamId: string,
  userId: string,
  role: string,
): Promise<{ ok: boolean }> {
  return api.put(`/system/teams/${teamId}/members/${userId}`, {
    user_id: userId,
    role,
  });
}

export function removeTeamMember(
  teamId: string,
  userId: string,
): Promise<{ ok: boolean }> {
  return api.delete(`/system/teams/${teamId}/members/${userId}`);
}

export function attachResource(
  teamId: string,
  resourceType: "bot" | "workflow",
  resourceId: string,
): Promise<{ ok: boolean }> {
  return api.post(`/system/teams/${teamId}/attach`, {
    resource_type: resourceType,
    resource_id: resourceId,
  });
}
