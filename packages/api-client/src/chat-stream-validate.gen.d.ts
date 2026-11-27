// 生成产物:聊天流公开事件的运行时校验(勿手改;contracts:gen 再生成)
// 返回 true 表示收齐的 data: JSON 是六种公开事件之一且字段形状合格
declare function validateChatStreamEvent(value: unknown): value is { type: string; data: unknown };

export default validateChatStreamEvent;
