## ADDED Requirements

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
