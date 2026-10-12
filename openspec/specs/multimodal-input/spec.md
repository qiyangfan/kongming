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

### Requirement: 语音转写输入

系统 SHALL 提供语音转写端点 `POST /chat/stt`(multipart 音频,复用
chat:execute 权限点):音频经本地语音模型(faster-whisper,api 面运行,
零凭证零外部调用)转写为文本,返回 `{text}` 供用户填入输入框确认后
发送。音频 MUST 即转即弃——不落盘、不建附件行、不进日志正文(转写
产物是问题文本而非附件内容,不走附件链路);单次音频上限 15MB(超限
拒绝)。

转写能力为**可选启用形态**:模型文件在位即可用;模型缺失或引擎加载
失败时端点 MUST 返回 503 并明示启用配方(不静默半可用,不加独立开关
——模型在=启用,状态自明)。转写引擎 MUST 以接口反转注入(领域零
框架依赖)。

#### Scenario: 语音转文字进输入框

- GIVEN 持 chat:execute 的用户录制一段「各上映年份的影片数量分布」
- WHEN 上传音频转写
- THEN 返回转写文本,前端填入输入框待用户确认发送

#### Scenario: 即转即弃

- GIVEN 一段音频完成转写
- WHEN 检查存储与日志
- THEN 无音频文件残留、无附件行、日志无音频与转写正文(仅操作审计 action)

#### Scenario: 未启用形态明示

- GIVEN 部署未下载语音模型
- WHEN 调用转写端点
- THEN 503 明示未启用与模型下载配方,不静默失败

#### Scenario: 超限与权限

- GIVEN 超 15MB 音频,或无 chat:execute 的调用者
- WHEN 调用转写端点
- THEN 拒绝(413/403 分类文案)
