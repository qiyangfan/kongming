# 030 multimodal-input 实施任务

## 1. 迁移与上传(测试先行:安全类)

- [x] 1.1 失败测试:类型白名单/magic 复核/10MB 中断/归属 404/
  operlog 审计
- [x] 1.2 0018 迁移 sys_attachment(status CHECK)+attachment 域包
  (models/ports/repository/service)+POST /chat/attachments
  (流式落盘超限即删)+import-linter 契约

## 2. 内部内容端点(测试先行:安全类)

- [x] 2.1 失败测试:无内部令牌 401/绑定复校/一次性消费(二次取
  404+文件已删)
- [x] 2.2 /internal/attachment/{id}/content(同事务置 CONSUMED+删文件)

## 3. agent 预处理(测试先行)

- [x] 3.1 失败测试:拼接形态/单件失败 fail-closed/纯文本零回归
  (不取件不调用)
- [x] 3.2 multimodal.py(内部取件客户端)+llm.chat_vision(task=
  "vision" 路由)+/query 与 /query_stream 处理器接线

## 4. chat 链路

- [x] 4.1 ChatQueryIn 纯增 attachment_ids(≤3 uuid)+归属校验
  (任一 404 不进回调)+消息 attachments 摘要落库回显(同步/流式
  同源)+agent 请求透传
- [x] 4.2 make contracts

## 5. web

- [x] 5.1 composer 附件按钮(白名单 accept)+chips 可移除+发送携带
  ids+历史徽标(Paperclip)

## 6. 验证与收尾

- [x] 6.1 make check 全绿;016 评估 smoke 复验;真 LLM 冒烟(配置
  vision 路由→截图问答;未配主模型报错路径);UI 上传发送
- [x] 6.2 R1 独立评审+修复清零
- [x] 6.3 HANDOFF+挂账(STT/OCR/内容审核/孤儿清理/Bot 渠道)+归档
