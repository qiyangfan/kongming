# delta spec: query-streaming(在途轮登记跨实例共享)

## MODIFIED Requirements

### Requirement: 取消端点与归属校验

系统 SHALL 提供取消端点(路径含轮次标识):JWT 认证 + 权限点
`chat:execute` + 归属校验,仅本轮发起用户可取消。他人的轮次、不存在的
轮次,以及已结束且无落库结果的轮次,SHALL 统一返回 404(不区分「不存在」
「无权」「已结束」)。取消结果 SHALL 以 outcome 如实返回:`CANCELLED`
(本轮已取消)或 `COMPLETED`(取消到达前本轮已正常结束),并 SHALL 在有限
时间内返回(上限为部署常量)。

在途轮登记 SHALL 跨实例共享:取消请求落到任一实例都应生效——本实例未
命中在途登记时,凭共享登记经内部通道把取消转给持有该轮的实例执行,结果
原样回传。共享登记不可用、持有实例不可达,或登记指向本实例而本实例并无
该轮(陈旧登记)时,系统 SHALL 按落库结果回答:该轮已落库→`COMPLETED`
并携带消息标识;未落库→404。任何路径下 SHALL NOT 伪造终态,亦 SHALL NOT
把他人轮次取消掉。

#### Scenario: 发起用户取消在途轮
- GIVEN 用户 A 的一轮流式问答正在执行
- WHEN 用户 A 请求取消该轮
- THEN 返回 outcome=CANCELLED,该轮执行停止

#### Scenario: 他人轮次 404 同形
- GIVEN 轮次 R 属于用户 A 且正在执行
- WHEN 用户 B 请求取消 R
- THEN 返回 404,响应不区分「不存在」与「无权」,R 的执行不受影响

#### Scenario: 取消与完成竞争
- GIVEN 本轮在取消请求到达前已正常结束并落库
- WHEN 发起用户请求取消
- THEN 返回 outcome=COMPLETED 并携带该轮落库消息标识,历史不出现已取消消息

#### Scenario: 无权限或已结束未落库
- GIVEN 调用者不含 chat:execute,或轮次已结束且无落库结果
- WHEN 请求取消
- THEN 分别返回 403 / 404

#### Scenario: 取消请求落在非持有轮次的实例
- GIVEN 轮次 R 属于用户 A,正在实例 A 上执行,取消请求到达实例 B
- WHEN 用户 A 请求取消该轮
- THEN B 凭共享登记把取消转给 A,A 执行取消编排
- AND 返回 outcome=CANCELLED,R 的执行停止,历史只落一条已取消消息

#### Scenario: 共享登记不可用或持有实例不可达
- GIVEN 跨实例登记不可用(如存储故障),或持有该轮的实例不可达
- WHEN 发起用户请求取消
- THEN 按落库结果回答:已落库→COMPLETED 并携带消息标识;未落库→404
- AND 不伪造终态,不在存储里写已取消行

#### Scenario: 陈旧登记不自我转发
- GIVEN 共享登记指向本实例,但本实例并无该轮在途(实例重启一类的残留登记)
- WHEN 发起用户请求取消
- THEN 不向本实例自我转发(避免自环),按落库结果回答
