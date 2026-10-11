# Spec Delta

## MODIFIED Requirements

### Requirement: MCP server 配置管理

持 system:mcp:manage 权限点的用户 SHALL 能在租户内管理外部 MCP
server 配置:新增/编辑/启停/删除(server_key/显示名/endpoint/自定义
headers),传输类型仅 Streamable HTTP。系统 SHALL 提供连通性测试
(initialize 握手 + tools/list + resources/list + prompts/list 探测,
返回工具数与资源数、模板数;原语类端点不可用或返回不支持 SHALL 如实
计数 0 不算失败;失败返回结构化错误,不暴露内部堆栈)。自定义 headers
MUST 加密落库(AES-GCM),任何查询接口 MUST NOT 回显明文(掩码形态,
键名可见值为 ***);编辑时未改动的 header 键值留空即保持现值。配置 MUST
按租户隔离(跨租户 404 同形),管理操作 MUST 落操作审计。配置详情 SHALL
展示三类原语清单:工具(既有)、资源(uri/名称/描述/mediaType)与
提示模板(名称/描述/参数声明);模板 SHALL 支持按名拉取只读内容预览
(渲染结果),预览内容作为外部文本处理(展示前过既有注入检测)。

#### Scenario: 创建并连通性测试

- GIVEN 持 system:mcp:manage 的用户
- WHEN 录入外部 MCP server(endpoint+鉴权 headers)并触发连通性测试
- THEN 系统执行握手与三类原语探测,返回可达状态与工具/资源/模板计数;
  连通失败返回结构化错误(超时/拒连/协议错分类),不暴露内部堆栈

#### Scenario: 原语不支持计数如实为零

- GIVEN server 仅提供工具原语(无 resources/prompts 能力)
- WHEN 连通性测试执行
- THEN 资源数与模板数如实为 0,测试判定不受影响(不算失败)

#### Scenario: headers 不回显明文

- GIVEN 已配置鉴权 headers 的 server
- WHEN 查询配置详情/列表
- THEN headers 值以 *** 掩码返回,明文不出现在任何响应/日志/审计中

#### Scenario: 越权与租户隔离

- GIVEN 未授权用户,或另一租户用户
- WHEN 调用管理端点
- THEN 403/404 同形拒绝

## ADDED Requirements

### Requirement: 资源与提示原语消费

对启用且可达的 server,系统 SHALL 消费其资源与提示原语(发现以服务端
枚举为准):

- **资源读取**:每 server 注册一个读资源工具(命名
  `mcp__<serverKey>__read_resource`,uri 入参);入参 uri MUST 命中该
  server 已枚举的资源白名单,白名单外拒绝(结构化错误观察,不出站);
  资源内容作为外部数据进工具观察——大小超上限截断并标注,进观察前过
  既有注入检测面。该工具的执行(授权/回调/审计/失败分类)SHALL 走既有
  MCP 工具执行纵深防御语义,零新增机制。
- **提示模板**:模板清单与按名渲染预览 SHALL 为管理面只读能力(挂既有
  system:mcp:manage 口径),MUST NOT 自动注入任何提示词、MUST NOT 成为
  运行时提示来源。
- server 禁用或删除时,其资源工具与原语清单 MUST 随之消失(与既有工具
  语义同形);单 server 原语探测/读取失败 MUST NOT 影响其余 server 与
  内置工具(降级记录,同既有断连口径)。

#### Scenario: 读资源工具白名单校验

- GIVEN server A 已枚举 3 个资源
- WHEN LLM 以白名单内 uri 调用 read_resource
- THEN 出站读取并把内容(截断后)作为观察返回,执行落既有工具审计;
  以白名单外 uri 调用则拒绝且不出站

#### Scenario: 资源内容截断与净化

- GIVEN 某资源内容超大且含注入形态文本
- WHEN read_resource 执行
- THEN 观察含截断标注的净化后内容(注入检测已过),不超观察上限

#### Scenario: 模板只读边界

- GIVEN server 提供 2 个提示模板
- WHEN 管理面查看模板清单并拉取预览
- THEN 预览为只读渲染结果;任意运行时问答/Bot 的提示词组装不含该模板

#### Scenario: 禁用后原语消失

- GIVEN 已注册读资源工具的 server
- WHEN 管理员禁用该 server
- THEN read_resource 从工具目录与注入集消失,原语清单不再可见;
  直调回调被拒落审计
