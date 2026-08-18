# nl2sql 能力域(005 delta)

## ADDED Requirements

### Requirement: 控制面回调 Agent 面

控制面 SHALL 经内部令牌调用 Agent 面问答端点,请求报文 SHALL 携带
trace_id、tenant_key、question;SHOULD 携带 user_id、history、
datasource_key(缺省 default)。trace_id SHALL 与执行审计(sys_sql_audit)
同源贯穿,同一次问答的多次执行回调(含 selfheal 重试)共用同一 trace_id。
Agent 面响应 SHALL 携带 success、verdict(PASS/BLOCKED/ERROR 或空)、
summary、insights、stats、columns、rows、sql、retry_count,失败时携带
error 摘要。控制面 SHALL 为回调设置超时上限;Agent 面不可达/超时 SHALL
映射为友好错误提示,不得向上抛裸 500、不得泄露内部地址与堆栈。

#### Scenario: 正常回调一次走通
- GIVEN 控制面收到合法问答请求
- WHEN 回调 Agent 面
- THEN 报文含 trace_id/tenant_key/user_id/question/history
- AND Agent 面响应 success=true 时含 summary/columns/rows/sql/insights/stats

#### Scenario: selfheal 重试共用 trace_id
- GIVEN 首次执行 verdict=ERROR 触发 selfheal 重试
- WHEN Agent 面多次回调执行路由
- THEN 各次执行回调的 trace_id 相同,审计可按 trace_id 串起重试链

#### Scenario: Agent 面不可达
- GIVEN Agent 面进程不可达或响应超时
- WHEN 控制面回调
- THEN 返回友好错误提示,不暴露内部地址/堆栈,不裸 500

#### Scenario: 三态 verdict 映射
- GIVEN Agent 面响应返回
- WHEN 控制面处理响应
- THEN verdict=PASS 映射为成功结果;verdict=BLOCKED 映射为拒绝提示
  (不重试语义已由 Agent 面执行);verdict=ERROR 映射为失败提示
