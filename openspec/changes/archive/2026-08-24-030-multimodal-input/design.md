# 030 multimodal-input 技术设计

## Context

图片输入是数据问答高频形态。约束:ADR-002(api 无 LLM 凭证,vision
调用必须在 agent);021 路由(task 键零代码可配);prompt 资产纪律
(预处理在进图前,不动 prompts.py);隐私(一次性消费即删)。

## Decisions

### D1: 附件存储与生命周期(0018)

sys_attachment:id uuid7 / tenant_id / user_id / filename / mime /
size_bytes / status VARCHAR CHECK(UPLOADED/CONSUMED)/ created_at /
consumed_at。文件:var/attachments/<uuid>.<ext>(写时流式落盘,
超 10MB 即中断删除;mime 白名单 png/jpeg/webp/gif,sniff 头部
magic bytes 复核——Content-Type 可伪造,magic 不可)。

一次性消费:/internal/attachment/{id}/content 命中即同事务置
CONSUMED+删文件(先删文件再提交;重复取 404)——防重放+隐私
默认。引用(ChatQueryIn.attachment_ids)只做归属校验(存在+未消费+
user+tenant),404 同形;消费发生在 agent 取内容时。

### D2: agent 预处理(进图前,内核零改动)

agent 新模块 multimodal.py:`preprocess_attachments(ids, http_client)`
→ 逐件 GET /internal/attachment/{id}/content(内部令牌头)→ bytes
→ `llm.chat_vision(prompt, [(mime, bytes)], task="vision")`(新方法:
content parts=[text, image_url(data url)];021 路由 vision 键,未配
回退主模型[文本模型报错→LlmError 收敛])→ 描述文本。
拼接:`[图片 1 内容描述]\n{desc}\n\n[图片 2 …]\n\n用户问题:\n{q}`。
任一附件失败(404/消费冲突/vision 错)→ 该件标注「无法读取」段
继续?否——fail-closed:任一失败即整轮 FAILED(清晰错误文案),
防「半聋」回答误导。预处理在 /query 与 /query_stream 处理器共用
(图invoke 之前)。

### D3: chat 链路纯增

ChatQueryIn.attachment_ids: list[str](≤3,uuid 形态);service
_prepare_query 段归属校验(全有效才过,任一 404);agent 请求体
透传 ids;消息落库 result_json 邻列 attachments JSONB 摘要
([{filename, mime, size}]——不含内容)供回显;流式同源(017
共用前段)。

### D4: web

composer 加 Paperclip 按钮(accept 白名单后缀)→ 上传即传(逐件
POST,进度即 chips)→ chips 可移除(未发送前可删?——删除端点
不做,移除仅不引用;孤儿附件 24h 清理挂账)→ 发送携带 ids;
历史回显「Paperclip n 张图片」徽标(消息 attachments 摘要非空时)。

### D5: 测试策略

- 上传:类型/大小/magic 复核/归属 404/operlog。
- 内部内容端点:内部令牌缺失 403(全局内部中间件口径)/绑定复校/一次性(二次取 404,
  文件已删)。
- agent 预处理(fake http+fake vision):拼接形态/失败 fail-closed/
  纯文本零回归(不取件不调用)。
- chat 链路:attachment_ids 归属校验/消息摘要落库回显。
- 冒烟:真 LLM(qwen-vl 若 .env 可配;不可配则主模型报错路径直证
  结构化失败)+UI 上传发送。

## Risks / Trade-offs

- [图片注入(像素→描述→问题)] → 残余面挂账(020 深化);描述段
  以显式分隔标注为数据。
- [vision 模型未配的部署] → 回退主模型报错=结构化失败文案,用户
  可见可操作(非静默)。
- [一次性消费与多轮追问] → 附件仅当轮有效;追问需重传(语义
  直白,文档说明)。

## Migration Plan

0018 新表;零权限点(复用 chat:execute);var/attachments 目录
运行时自建;回滚=删端点(纯增)。

## Open Questions

- O1:STT 选型(Whisper 自部署 vs 通义听悟);O2:OCR 兜底;
O3:图片内容审核;O4:孤儿附件定时清理(lifespan 薄壳);
O5:Bot/工作流渠道附件。
- O6(冒烟实证):recipe 内核(固定 NL2SQL 管线)对纯图片问题会硬走
  SQL——答非所问面(冒烟:图表问「哪个分类最高」recipe 答了库内
  'admin' 计数;ReAct 内核据图片描述正确作答 Sports)。v1 处置=挂账:
  图片问答建议 ReAct 渠道;后续按 024 知识分流器形态加「图片描述自足」
  意图分流(描述可答即不生成 SQL)。
