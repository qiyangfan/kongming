## MODIFIED Requirements

### Requirement: 会话持久化(刷新不丢历史)

chat_session + chat_message 持久化对话历史,首次问答自动新建会话,刷新页面恢复历史,会话列表侧边栏可新建/切换/删除,按 user+tenant 隔离。

会话标题两阶段生成:初始标题=首问前 30 字符(截断兜底,保证任何时刻标题可用);首个成功问答轮(assistant 状态 SUCCESS)完成后,系统 SHALL 调用 LLM 将首问改写为简洁概括标题并回写会话。LLM 改写输出 SHALL 经净化(去首尾空白/换行/包裹引号)并截断至标题列上限以内;改写失败或超时 SHALL 保持截断标题,不阻断、不延迟问答主链路响应。标题已被用户手动重命名(不再等于截断初始值)的会话 SHALL 跳过自动改写,人工标题不被覆盖。

问答请求契约 SHALL 纯增 attachment_ids(图片附件引用,≤3 个,uuid 形态;缺省空=纯文本零回归,030);user 与 assistant 消息落库 SHALL 含附件引用摘要(filename/mime/size 三元组数组,不含内容),历史回显逐字同源;引用附件 MUST 先过归属校验(当前用户+租户,multimodal-input spec),任一无效 SHALL 404 同形且不进 agent 回调。

#### Scenario: 首次问答自动新建会话
- GIVEN 用户进入对话页(无当前会话)
- WHEN 发起首次提问
- THEN 后端自动新建会话(初始标题=首问前 30 字符),返回 sessionId
- AND user 问句与 assistant 结果都落消息表

#### Scenario: 首个成功轮后 LLM 改写标题
- GIVEN 新会话标题仍为截断初始值
- WHEN 该会话产生首个 assistant 状态 SUCCESS 的消息
- THEN 后端调用 LLM 以首问生成简洁标题并回写会话
- AND 改写期间问答响应照常返回(用户无感知)

#### Scenario: LLM 改写失败保持截断标题
- GIVEN LLM 服务不可达或返回异常
- WHEN 触发标题改写
- THEN 会话标题保持截断初始值,不报错、不重试风暴

#### Scenario: 手动重命名后不被自动改写覆盖
- GIVEN 用户已将会话重命名为自定义标题
- WHEN 后续问答轮成功完成
- THEN 标题保持用户设定值,不再触发自动改写

#### Scenario: 刷新页面恢复历史
- GIVEN 用户在会话中聊了多轮后刷新页面
- WHEN 重新进入对话页
- THEN 从本地存储读上次 sessionId,拉取历史消息完整回显

#### Scenario: 会话列表侧边栏
- GIVEN 用户有多个跨天会话
- WHEN 加载对话页
- THEN 侧边栏按更新时间倒序展示会话列表,并按「今天/昨天/近 7 天/近 30 天/更早」
  时间分组呈现,组内保持更新时间倒序,可新建/切换/删除

#### Scenario: 会话按 user+tenant 隔离
- GIVEN 用户 A 的会话
- WHEN 用户 B 尝试拉取该会话消息
- THEN 返回会话不存在或无权访问(不区分二者,不泄露信息)

#### Scenario: 带附件问答落库(030)

- GIVEN 用户引用 2 件已上传附件提问
- WHEN 问答完成
- THEN 消息含附件摘要;刷新回显徽标;attachment_ids 经归属校验

#### Scenario: 无附件零回归(030)

- GIVEN 纯文本问答(无 attachment_ids)
- WHEN 落库与回显
- THEN 消息形态与本字段引入前一致(空数组/缺省不渲染)
