# 030 multimodal-input:多模态输入(图片)

## Why

数据问答的现实输入不总是文字——报表截图、图表照片是高频形态。本变更
让助手接受图片输入:上传→vision LLM 理解为文本描述→注入问题,内核
零改动(上游 multimodal-input 域的 Kongming 适配;语音 STT 因服务选型
未定收 Non-goals)。杀手场景:拍张报表截图直接问「哪个品类涨最多」。

## What Changes

- **附件上传**(api):POST /chat/attachments(multipart,复用
  chat:execute 权限点)——类型白名单(png/jpeg/webp/gif)+大小上限
  10MB+单次引用上限 3 个;sys_attachment(0018 迁移:user/tenant/
  文件名/mime/大小/状态);文件落 var/attachments/(uuid 名,路径零
  拼接);operlog 审计。
- **一次性消费语义**:附件被 agent 取内容即 CONSUMED(文件即删,
  隐私友好默认);归属校验(user+tenant,跨用户引用 404 同形);
  agent 取内容走内部端点 /internal/attachment/{id}/content(内部令牌+
  绑定复校,与既有 internal 面同构)。
- **agent 预处理(内核零改动)**:/query 与 /query_stream 处理器在进图
  前统一预处理:attachment_ids 非空 → 逐件经内部端点取 bytes →
  vision 调用(OpenAI image_url data-url,**task="vision" 走 021 路由**
  ——运营方在 KM_LLM_ROUTES 配 vision→qwen-vl 类模型;未配回退主
  模型,文本模型报错即结构化失败,不静默)→ 描述文本以「[图片 n
  内容描述]」分隔段拼在问题前 → 进 understand 老链路。纯文本零回归
  (无附件不预处理)。
- **web**:对话输入区附件按钮(图片选择)+已选附件 chips(可移除)+
  发送携带 attachment_ids;历史消息回显附件标记(消息 attachments
  JSONB 列存引用摘要,回显「📎 n 张图片」——图表标用 lucide Paperclip)。

## Capabilities

### New Capabilities

- `multimodal-input`:附件上传与隔离、一次性消费、vision 预处理注入、
  文件限制的行为契约。

### Modified Capabilities

- `chat`:问答请求纯增 attachment_ids;消息存引用摘要回显。

## Non-goals

- **不做语音输入(STT)**:服务选型未定(Whisper 自部署 vs 通义听悟
  API),上游亦二选一留白;选定后独立变更接入(预处理管线已就绪)。
- **不做 OCR 兜底**:vision 失败即结构化错误返回(OCR 引擎引入是
  另一个依赖决策);不做图片内容审核(内容安全服务随后续域;020
  护栏作用于文本面,图片内容经 vision 描述进问题的注入残余面挂账
  020 深化)。
- 不做附件持久化(一次性消费即删是隐私友好默认;持久化随后续);
  不做图片生成/编辑;不做 PDF 等多页文档(024 文档 RAG 域覆盖
  知识面);不做 vision 结果缓存。
- 不改 agent 图结构(prompts.py 零改动——prompt 资产纪律;预处理
  在进图前的处理器层);不做 Bot/工作流渠道的附件(v1 仅 chat)。

## Impact

- **代码**:api attachment 上传/内容端点+chat 请求模型纯增+消息
  引用摘要;agent 预处理模块(内部客户端取件+vision 调用+拼接);
  web 输入区附件 UI。
- **Schema**:0018 迁移 sys_attachment(ADR-004 约定;status
  VARCHAR+CHECK:UPLOADED/CONSUMED)。
- **契约**:POST /chat/attachments(multipart)+/internal/attachment/
  {id}/content(内部)+ChatQueryIn 纯增 attachment_ids+消息回显字段;
  make contracts。
- **ADR 引用**:ADR-002(agent 经内部端点取件,附件字节不直接
  进 agent 请求体)/ADR-004/ADR-005。
- **安全**:类型/大小/数量上限;归属 404 同形;一次性消费防重放;
  内部端点绑定复校;测试先行(隔离/限制/消费语义/纯文本零回归)。
- **依赖**:零新依赖(vision=OpenAI 兼容 image_url;上传=既有
  multipart 处理,009 同款)。
