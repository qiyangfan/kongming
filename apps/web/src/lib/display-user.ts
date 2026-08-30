/* 操作者身份展示单源(050):昵称缺失回退登录用户名,两者皆缺「已注销」;
   禁止渲染用户 id 或其截断片段(管理面反馈/审计列表共用,spec「操作者身份
   可读性」)。系统行为的「系统」语义由调用方按 user_id 判定后传入。 */
export function displayUser(
  nickname: string | null | undefined,
  username: string | null | undefined,
): string {
  return nickname || username || "已注销";
}
