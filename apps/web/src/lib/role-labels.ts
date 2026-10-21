/** role_key → 中文名(与 apps/api auth/seed.py 内置角色名对齐; * 运行时可自建角色,未收录键一律回退显示原值,禁臆造)。 */

export const ROLE_LABELS: Record<string, string> = {
  SUPER_ADMIN: "超级管理员",
  DEVELOPER: "开发者",
};

export function roleLabel(roleKey: string): string {
  return ROLE_LABELS[roleKey] ?? roleKey;
}
