# internal-meta 能力域

## Purpose

内部元数据通道:为 agent 面 retrieve 节点提供租户 schema 的实时内省结果
(表/列/类型/注释),是 RAG 落地前 schema 提示的过渡来源,也是未来元数据
抓取的执行期底座。内省内容只进 LLM 提示文本,永不进入 SQL 执行路径。

## Requirements

### Requirement: 租户 schema 实时内省

内省请求 SHALL 经内部通道携带 tenant_key;系统 SHALL 返回该租户业务 schema
(tenant_<tenant_key>)与 public 范围内用户表的表名、表注释、列名、
列类型、列注释;控制面保留表(前缀 sys_/chat_ 及 alembic_version、
user_identities,与执行路由保留表拒读同一判定)SHALL NOT 出现在结果中,
且不占表数上限。tenant_key 未知或租户已禁用 SHALL fail-closed 拒绝。
返回表数量 SHALL 有上限截断(防提示文本膨胀),截断时 SHALL 显式标注
truncated 标志,不得静默漏表。

#### Scenario: 正常内省返回表结构
- GIVEN 租户 default 的 schema 内存在若干用户表(含注释)
- WHEN 内部调用携带 tenant_key=default 发起内省
- THEN 返回每张表的表名/表注释/列清单(列名+类型+注释)
- AND 覆盖 tenant_default 与 public 两个 schema 的用户表

#### Scenario: 未知或禁用租户拒绝
- GIVEN 请求携带不存在或已禁用的 tenant_key
- WHEN 内省请求到达
- THEN 拒绝(404),不返回任何 schema 信息

#### Scenario: 不越出租户边界
- GIVEN 其他租户 schema(tenant_xxx)或系统 schema(pg_catalog 等)存在表
- WHEN 内省 tenant_key=default
- THEN 返回结果不含 tenant_default/public 以外的任何表

#### Scenario: 控制面保留表不进内省结果
- GIVEN public schema 存在控制面表(sys_user/chat_message/user_identities 等)
- WHEN 内省任意租户
- THEN 返回结果不含任何保留表(与执行路由的保留表拒读同一判定)
- AND 保留表不占表数上限

#### Scenario: 表数超限截断并标注
- GIVEN 租户 schema 内用户表数量超过内省上限
- WHEN 内省返回
- THEN 表清单截断到上限,且响应 truncated=true

#### Scenario: 内部通道鉴权
- GIVEN 调用方未携带内部令牌或令牌错误
- WHEN 请求内省端点
- THEN 返回 403,与内部面其余端点一致

### Requirement: 内省内容不进执行路径

内省结果 SHALL 仅作为 LLM 提示文本消费;SHALL NOT 影响 SQL 防火墙校验、
data_scope 改写与执行路由的任何决策。

#### Scenario: 内省结果只进提示
- GIVEN 某表经内省出现在 schema 提示中
- WHEN 后续问答链路执行
- THEN 对该表生成 SQL 仍须经防火墙与 data_scope 全量校验,内省结果不构成放行依据
