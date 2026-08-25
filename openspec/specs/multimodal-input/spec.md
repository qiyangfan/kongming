## Purpose

让助手接受图片输入:报表截图/图表照片经 vision LLM 理解为文本描述
注入问题,agent 内核零改动;附件一次性消费(隐私友好),归属隔离
fail-closed。

## Requirements

### Requirement: 附件上传与限制

系统 SHALL 提供附件上传端点 POST /chat/attachments(multipart,复用
chat:execute 权限点):图片类型白名单(png/jpeg/webp/gif,MUST 以
magic bytes 复核——Content-Type 可伪造)+大小上限 10MB(超限即
中断删除)+单次问答引用上限 3 个;文件以 uuid 名落盘(路径零拼接);
上传落操作审计。

#### Scenario: 类型与大小限制

- GIVEN 非白名单类型或超 10MB 文件,或伪造 Content-Type 的非图片
- WHEN 上传
- THEN 拒绝(400 分类文案),不保留文件

#### Scenario: 上传成功

- GIVEN 持 chat:execute 的用户上传合法图片
- WHEN 完成
- THEN 返回 attachment_id;sys_attachment 落 UPLOADED 行,文件落盘

### Requirement: 附件归属与一次性消费

附件引用 MUST 按 user+tenant 隔离(跨用户/跨租户引用 404 同形);
附件内容 MUST 一次性消费:agent 经内部端点取内容即置 CONSUMED
并删除原始文件,重复取用 SHALL 404(防重放);内部端点 MUST 内部
令牌+绑定复校。

#### Scenario: 跨用户引用拒绝

- GIVEN 用户 A 上传的附件
- WHEN 用户 B 在问答中引用其 id
- THEN 404 同形,不处理

#### Scenario: 一次性消费

- GIVEN 一件已上传附件
- WHEN agent 取过一次内容
- THEN 状态 CONSUMED,文件已删;再次取用 404

### Requirement: vision 预处理注入(内核零改动)

多模态输入 MUST 在进图前预处理:逐件取 bytes → vision LLM(image_url
data-url,task="vision" 走模型路由;未配置回退主模型,文本模型报错
即结构化失败)→ 描述文本以显式分隔段拼于问题前;纯文本输入(无
附件)MUST 行为等价老链路(不预处理);任一附件处理失败 MUST
fail-closed(整轮结构化失败,不「半聋」作答)。

#### Scenario: 报表截图提问

- GIVEN 用户上传销售报表截图并问「哪个品类涨最多」
- WHEN 预处理
- THEN vision 理解截图 → 描述段拼于问题前 → agent 据图回答

#### Scenario: 纯文本零回归

- GIVEN 纯文本问题(无附件)
- WHEN 问答
- THEN 不预处理,行为与老链路逐字段一致(评估基线不变)

#### Scenario: 附件失败 fail-closed

- GIVEN 引用的附件中一件取内容失败(已消费/不存在)
- WHEN 预处理
- THEN 整轮 FAILED(明确文案指出哪件),不进图

### Requirement: 消息附件摘要回显

assistant 消息(与 user 消息)SHALL 记录附件引用摘要(filename/mime/
size,不含内容)随消息回显;前端 SHALL 以附件徽标展示数量与文件名。

#### Scenario: 刷新回显

- GIVEN 带附件的一轮问答
- WHEN 刷新页面
- THEN 消息区展示附件徽标(数量/文件名),无原始文件下载入口
