/* 昵称缺失时显示登录名或“已注销”，不显示用户 ID；系统操作由调用方识别。 */
export function displayUser(
  nickname: string | null | undefined,
  username: string | null | undefined,
): string {
  return nickname || username || "已注销";
}
