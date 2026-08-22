## MODIFIED Requirements

### Requirement: 对话区渲染

对话区 SHALL 支持多轮问答:历史消息按时间升序回显(user 问句 +
assistant 应答);提问框常驻底部,Enter 发送(Shift+Enter 换行),
提问期间防重复提交;assistant 应答 SHALL 结构化呈现:摘要文案、
结果表格(列头 + 行,空结果有明确空态)、SQL 折叠查看、洞察列表、
统计信息;被拦截(REJECTED)与失败(FAILED)的应答 SHALL 有区别于
成功的视觉呈现并附原因文案。提问进行中对话区 SHALL 呈现流式过程
(事件契约见 query-streaming spec):阶段指示按节点完成顺序逐个点亮
(running 有进行中视觉,done 有完成视觉),触发自愈时显示
「第 N 次自愈重试」;sql 事件到达即渲染 SQL 折叠卡片(内容与终态
一致,多次下发以最后一次为准);summary 事件逐条追加呈现(打字机语义);
result 终态到达后流式过程呈现 SHALL 被完整结果卡片接管(与历史回显同形态)。
用户切会话/新建会话/切租户/切数据源/离开页面时 SHALL 中止在途流,迟到的流
事件 SHALL NOT 写入新上下文(守卫按流的发起上下文丢弃过期写入);
流异常结束(无终态)SHALL 按失败呈现并回填问句可重试。

#### Scenario: 完整问答回合渲染
- GIVEN 用户在会话中提交问题且问答成功
- WHEN 应答返回
- THEN user 问句与 assistant 应答按序追加;应答含摘要/表格/SQL(折叠)/洞察/统计

#### Scenario: 拦截态应答呈现
- GIVEN 问答被安全策略拦截
- WHEN 应答返回
- THEN 呈现拦截态视觉与策略原因文案,不渲染结果表格

#### Scenario: 提问中防重复提交
- GIVEN 一次提问尚未返回
- WHEN 再次尝试提交
- THEN 提交被阻止(输入保留,不产生重复请求)

#### Scenario: 空会话初始态
- GIVEN 新建对话未提任何问题
- WHEN 对话区渲染
- THEN 呈现品牌空态引导文案,提问框可用

#### Scenario: 流式阶段逐个点亮
- GIVEN 流式问答进行中
- WHEN 阶段事件依次到达
- THEN 阶段指示按到达顺序逐个从进行中视觉转为完成视觉

#### Scenario: SQL 提前渲染
- GIVEN sql 事件到达而终态未到
- WHEN 流式过程呈现
- THEN SQL 折叠卡片已渲染,内容与终态 SQL 一致

#### Scenario: 终态接管流式呈现
- GIVEN result 终态事件到达
- WHEN 呈现切换
- THEN 流式过程(阶段/打字机)被完整结果卡片替换,与历史回显同形态
- AND 会话列表与消息历史按新落库结果刷新

#### Scenario: 切换上下文丢弃在途流
- GIVEN 流式问答进行中
- WHEN 用户切换会话或租户或数据源或离开页面
- THEN 在途流被中止,流式过程呈现不残留到新上下文
