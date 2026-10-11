# Proposal

## Why

工具网关(089)框架已就绪,web 检索域(094)验证了「域=声明清单+策略器+执行器」
的接入范式,但平台工具集仍由平台预置——租户无法把自有业务系统/API 接入通用
智能体。roadmap 已登记「HTTP 连接器域(用户自定义端点,白名单/凭证治理面需先
立约)」。这是通用智能体「工具广度」最直接的证据,也是治理型网关叙事的关键
一步:第一个**租户级**域——用户自带 API,平台供治理(白名单、SSRF 防护、凭证
加密、审计),与既有部署级域(websearch)形成治理谱系的两端。

## What Changes

- 新增 HTTP 连接器能力域:租户管理员注册自定义 HTTP 端点为 Agent 工具
  (名称/描述/方法/路径模板/参数声明/出口主机白名单/鉴权形态),注册即经
  闸门审核进入注入集,Agent 面恒经既有工具回调执行(ADR-002 零改动)。
- 凭证治理:鉴权材料(bearer/api-key/basic)加密落库(复用 webhook secret
  既有加密机制),任何接口不回显明文,仅在控制面出站请求装配(治理面只读
  形态+尾号掩码)。
- 出站安全:出口主机 MUST 命中注册声明的白名单 + 029 SSRF 拒绝段强制
  (内网/链路本地/云元数据地址一律拒);策略器四职责(出口校验/参数校验/
  响应处理/重试分类)按域实现。
- 写语义治理:非 GET 连接器缺省标记需确认,执行前走既有写工具确认流;
  注册时可显式声明(读端点可豁免)。
- 工具授权:`tool:http:call` 权限点显式授予制(同 websearch 形态);租户内
  已启用连接器对授权用户全量可见;per-connector 细粒度授权不做(Non-goal)。
- 注入集动态装配:连接器为库表行(非部署配置),注入集按请求租户维度
  实时装配,注册/启停即时生效。
- 治理面 UI:管理台连接器管理页(列表/新建/编辑/启停/删除/测试调用),
  破坏性操作 AlertDialog 确认;契约经管线生成。
- schema:新增连接器表(UUID v7 主键、VARCHAR+CHECK 状态列、timestamptz,
  ADR-004),走新 Alembic 迁移。

## Capabilities

### New Capabilities

(无——域契约挂既有 tool-gateway 能力域,与 websearch 域同组织方式)

### Modified Capabilities

- `tool-gateway`:新增 HTTP 连接器域 requirement 组——注册与凭证治理、
  出站安全(SSRF+白名单)、工具注入与授权(动态装配+确认流)、执行与
  观察(截断/错误分类/审计)。
- `web-admin`:新增连接器管理页行为契约(列表/表单/测试调用/启停,
  凭证不回显)。

## Impact

- `apps/api`:tool-gateway 域扩展(连接器 repository/service/router +
  域清单动态装配 + 域策略器/执行器);新增 Alembic 迁移(连接器表);
  工具回调执行链路新增域路由(既有域零回归铁律不变)。
- `apps/web`:新增连接器管理页;类型经 `make contracts` 生成。
- `apps/agent`:零改动(恒经 `/internal/tool/execute`,不感知新域)。
- 信任边界:ADR-002 不动(凭证加密落库且仅在控制面装配);契约:ADR-005
  管线;权限:新增 `connector:manage` 与 `tool:http:call` 两个权限点
  (SUPER_ADMIN 种子覆盖,不进 DEVELOPER 推荐集)。
- 上游参考:SQL 防火墙白名单语义与 029 SSRF 拒绝段为本域安全基座
  (逻辑参考,不复制实现)。
