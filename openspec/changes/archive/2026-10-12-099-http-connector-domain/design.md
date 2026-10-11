# Design

## Context

- 工具网关框架已就绪(tool-gateway spec:声明式域清单/策略器四职责/统一
  出站通道/条件注册);websearch 域(094)是部署级域先例——清单由环境配置
  单源派生、启动装载、凭证走环境变量。
- HTTP 连接器是**租户级**域:声明存库表行,与部署级域的两处结构差异是
  本设计的核心(清单装配时机、凭证来源)。
- 写工具确认流、工具审计表、域配额分桶、029 SSRF 拒绝段、webhook secret
  加密机制均已存在,本设计全部复用不重造。

## Goals / Non-Goals

**Goals:**

- 租户自助注册 → 注入 → 回调执行的完整闭环,治理面(白名单/SSRF/凭证/
  审计/配额)全部落在既有框架语义内。
- 既有域与内置工具零回归(域路由仅新增维度)。

**Non-Goals:**

- 部署级出站总白名单(平台级允许主机总闸)——注册白名单+SSRF 已闭合
  风险面,总闸留待真实需求。
- per-connector 细粒度授权(一把 tool:http:call 足够第一切片)。
- OAuth 授权码流程、请求签名类协议(HMAC/AWS SigV4)、multipart 上传、
  WebSocket/SSE——鉴权白名单收敛为 none/bearer/api-key 头/basic。
- 响应字段级脱敏规则(连接器响应是租户自有数据;注入检测面照走)。
- 出站 webhook 方向(连接器只做请求-响应,订阅类接入属内部连接器域)。

## Decisions

1. **租户级而非平台级**:「用户自定义端点」的叙事与多租户治理展示价值
   决定;凭证加密落库复用 webhook secret 机制(Fernet 单点,密钥环境变量
   注入),新增风险面已被 SSRF 段+白名单+确认流覆盖。备选(部署级声明,
   照 websearch 范式)实现减半但叙事归零,否决。
2. **注入集按请求动态装配**:连接器行数少(租户内索引点查),换取
   「注册/启停即时生效」;部署级域维持启动装载不变。清单装配纯函数
   (行 → 域清单声明)保持可重放测试性。
3. **SSRF 判定双落点**:注册期校验白名单主机形态 + 执行期强制拒绝段
   (存量行/白名单拼错的兜底);白名单命中不构成 SSRF 豁免。重定向
   跟随默认关闭(重定向是白名单绕过的经典通道)。
4. **写语义缺省规则**:非 GET 缺省需确认、GET 缺省豁免,注册可显式
   覆盖(POST 只读端点可标读,GET 写端点可标写——信任显式声明胜过
   方法启发式的例外堆积)。
5. **参数声明用有限字段集**(名/类型 string|number|boolean/必填/描述),
   不收完整 JSON Schema:LLM 注入形态与策略器校验都只需这四元;完整
   Schema 的表达力收益撑不起解析/校验/展示三面成本。
6. **路径模板占位符直插**(URL 编码后替换),查询参数与 POST JSON body
   由参数声明按名映射;响应一律文本化截断进观察(不解析结构化提取——
   结构提取是内部连接器域的预置方言职责,见 100)。
7. **schema**(新迁移,ADR-004 口径):`tool_http_connectors`——id UUID v7
   PK、tenant_id、key VARCHAR(同租户唯一 CHECK 形态)、name/description、
   method VARCHAR + CHECK IN ('GET','POST')、path_template、params jsonb、
   allowed_hosts jsonb、auth_type VARCHAR + CHECK、auth_secret_enc TEXT
   NULL、requires_confirmation BOOLEAN、timeout_ms INTEGER、enabled
   VARCHAR + CHECK ('ENABLED','DISABLED')(状态列字符串口径)、audit
   时间戳 timestamptz、created_by。无软删(硬删,identity 域外)。

## Risks / Trade-offs

- [用户自定义端点扩大出站攻击面] → 白名单+SSRF 双落点、重定向关闭、
  超时封顶、配额独立分桶、确认流、审计全量——纵深逐层落,单层失效
  不裸奔;测试先行(策略器红绿循环)。
- [租户注册恶意名称/描述操纵 LLM] → 注入集装配过既有输入护栏(与
  bot-store 上架护栏同闸),BLOCK 拒注册。
- [凭证加密密钥轮换] → 沿用 webhook secret 既有密钥治理,不新增机制
  (轮换缺口是既有问题,不因本变更扩大)。
- [动态装配的每请求开销] → 租户内连接器行数小 + 主键点查;装配结果
  不缓存(正确性优先,量级无虞)。

## Migration Plan

1. 迁移加表(纯增,零既有列改动);2. api 域包落地(测试先行:策略器
   安全规则红→绿);3. `make contracts` 前端类型;4. web 管理页;
5. 回退:revert + 迁移 downgrade(表独立,无交叉依赖)。

## Open Questions

(无——鉴权白名单、方法白名单、写语义缺省规则均为已裁决设计点;
若 owner 对权限点命名(connector:manage / tool:http:call)有偏好,
实施前可调,不影响契约结构)
