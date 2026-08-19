# 008 动态数据源注册/管理端点

## Why

`sys_datasource` 表自 baseline 即建成、池管理器 key 维度与失效钩子已预留、
datasource_key 契约链(api→agent→回调)已全透传——但表无模型/仓储/种子,
管理端点不存在,非 default 源一律 400 fail-closed,前端无切换入口。
本变更把「动态数据源」从预留位落成真实能力:超管免重启接入新 PG 业务库,
问答链路按 datasource_key 真路由。spec 行为契约(datasource 域)早已立约,
本变更是其注册/路由/管理面部分的首期兑现。

## What Changes

- **datasource 域包落地**(宪法分层 router→service→models→repository):
  管理端点 分页列表/创建/编辑/启停/删除/测试连接/选项清单,
  全挂权限点 `system:datasource:manage`(选项接口登录即可);
  管理端点全局视角(X-Tenant-Id 无语义,与身份治理同惯例),
  创建时租户归属由超管显式指定;管理面写操作全量审计
  (与业务同事务,沿用 007 惯例)。
- **凭证 AES-GCM 加密**:密码落库前加密(cryptography 已为主依赖),
  DTO 永不回传敏感字段;密钥 `KM_DATASOURCE_ENCRYPT_KEY`(配置项已在,
  prod fail-closed 已配,本变更实现消费方)。
- **0005 迁移**:`sys_datasource` 补 `name`/`default_schema` 两列
  (spec 已立约 name 用于选项展示、default_schema 用于 search_path,
  baseline 建表时漏建,ADR-004 演进走新迁移)。
- **动态池真实化**:`internal/executor.py` `_ENGINES` 放开任意 key,
  惰性建池(连接级只读/池 5/statement_timeout 30s,与 default 同构),
  定向失效钩子 `evict(key)`(删除/禁用/连接信息变更时,commit 后调用,
  防僵尸池);`SUPPORTED_KEYS` fail-closed 硬编码移除。
- **执行路由切真路由**:`/internal/sql/execute` 非 default 源
  校验(存在+启用+归属当前租户,fail-closed 不静默回退)后经动态池执行,
  防御层与默认路径完全一致(防火墙/data_scope/事务内 search_path
  =`default_schema, public`/行数上限/审计含 datasource_key)。
- **internal-meta 同构**:`/internal/meta/schema` 支持非 default 源
  (限 default_schema 内省,保留表排除同一判定)——agent retrieve 节点
  对动态源可用,问答全链路贯通。
- **chat 入口 fail-fast 预检**:`/chat/query` 携带 datasource_key 时
  先校验存在+启用+归属当前租户,伪造/越权 key 不进 LLM 链路
  (datasource spec「查询入口 fail-fast 预检」兑现)。
- **web 管理页 + 切换器**:/admin 第四子页「数据源」(列表/创建编辑对话框
  /启停/删除确认/测试连接);对话页数据源切换器(选项接口驱动,
  选中 key 随 /chat/query 发送,切租户重置);`make contracts` 重生成。
- 边界收紧:保留字 `default` 禁注册(内置路径标识);datasource_key
  传输层 pattern `^[a-z][a-z0-9_]{1,63}$`;key 与租户归属创建后不可改
  (spec 已立约);数据源删除=物理删除(软删仅 identity 域,AGENTS.md 约定)
  +级联失效连接池+审计留痕。

## Capabilities

### New Capabilities

(无新域——datasource 域 spec 已立约,本变更兑现其中注册/路由/管理面部分)

### Modified Capabilities

- `datasource`:「数据源注册与凭证加密」收紧(key pattern/保留字/default
  语义明确);「动态路由与查询」补 default_schema 注入防护与 key 维度定向
  失效细节;明确删除语义为物理删除+审计。「元数据抓取」「RAG datasource
  维度」两 requirement 本变更不兑现(spec 保留,随 rag 变更)。
- `internal-meta`:「租户 schema 实时内省」扩 datasource_key 维度
  (非 default 源限 default_schema 内省,fail-closed 与执行路由同构)。
- `web-admin`:「管理面路由与页面守卫」扩第四子页「数据源」
  (权限点 system:datasource:manage);页面行为细节归 datasource
  「管理 UI」requirement(已立约)。

## Non-goals

- **元数据抓取 fetch-metadata + 知识条目 embedding**:依赖 rag 域
  embedding 基建(尚不存在),随下一棒 rag/knowledge 变更兑现;
  本变更不碰 rag.table_descriptions 写入路径。
- **RAG 三路召回 datasource 双过滤**:rag 域变更范畴。
- **多方言**(MySQL/Oracle 等):PG-only,ADR-001 既定。
- **EXPLAIN 端点、会话导出等会话体验深化**:路线图第三梯队。
- **agent 面零改动**:datasource_key 透传 005 已就位,本变更不触碰
  (信任边界不变,凭证永不出控制面,ADR-002)。
- **专用只读登录角色**:沿用连接级只读+防火墙双闸(004 design 挂账项,
  本变更维持该裁定,动态源账号由注册方自行保证只读权限,readonly 列
  语义文档化)。

## Impact

- **ADR 引用**:ADR-002(信任边界——凭证加密/解密/建池全在控制面,
  agent 仅透传标识符);ADR-004(schema 演进——0005 新迁移,不改 baseline);
  ADR-005(契约——新端点经 `make contracts` 生成前端类型,禁手写)。
- **api**:新域包 `kongming/datasource/`;`internal/executor.py`
  (池放开+定向失效)、`internal/service.py`(fail-closed 硬编码移除,
  动态校验+路由)、`internal/meta.py`(动态源内省)、`chat/service.py`
  (入口预检)、`auth/seed.py`(权限点播种)、`config.py`(密钥消费),
  alembic 0005。
- **web**:/admin/datasources 页 + AdminLayout 子导航项;ChatPage
  数据源切换器;lib/system-api.ts 封装;类型全生成。
- **agent**:零改动。
- **依赖**:无新增(cryptography 已主依赖)。
- **冒烟维度**:注册→测试连接→问答路由到动态源→禁用即拒→
  改连接信息池失效→删除回收;跨租户 key 越权 403/404。
