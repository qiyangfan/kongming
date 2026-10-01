# 081-external-knowledge-tool 提案:外部个人知识库检索工具(siku 桥接)

## Why

已逐行核实的当前状态:

- kongming 的知识面只有「库内 RAG」:检索对象是 schema 说明、租户上传文档与历史成功 SQL(document-rag 域),全部住在控制面的 PostgreSQL + pgvector 里,按租户隔离。
- 个人本地知识库 siku 是另一个进程、另一个库:本地优先、单人 vault、已摄取全部文档,对外暴露 localhost HTTP API。其中 `POST /api/search`(请求 `{query, top_k, candidate_k}`,响应 `{results: [{chunk_id, source_name, note_path, anchor, text, ...scores}]}`)是**不依赖 LLM 的纯检索契约**(siku `src/siku/api/models.py:44-73`;`siku serve` 默认绑定 127.0.0.1,该端点无鉴权)。
- 两个库当前零连接:问数场景里「这个指标的口径写在哪个文档」这类问题,用户要手动切到 siku 问一遍再回来。单人使用状态下这是每日多次的上下文切换,而孔明的定位是「万事,问孔明」——文档类知识缺口让这句话在文档问题上不成立。

架构落点由既有契约决定,不需新发明:工具注册表唯一真理源在控制面(`apps/api/kongming/toolcall/registry.py`,代码声明式),工具执行收口控制面回调,Agent 面零凭证(ADR-002)。因此这是纯控制面变更:注册 + 执行 + 审计,agent 面零代码改动。

## What Changes

1. **部署级内置工具 `knowledge_search`**:入参 `query`(必填)/ `top_k`(可选,缺省 5);工具描述供 LLM 在选择时判别用途(个人文档口径检索)。仅在控制面配置 `KM_SIKU_BASE_URL` 时注册;未配置时该工具不进入注入集,对调用方零感知。
2. **执行收口控制面**:`tool_execute` 回调 → 控制面执行器 POST siku `/api/search`,将 results 转为结构化工具观察(chunk 文本 + `note_path`/`source_name`/`anchor` 来源锚点)喂回 ReAct 循环。成败与耗时落既有工具调用审计(与 `validate_sql` 同范式)。
3. **localhost 白名单(fail-closed)**:`KM_SIKU_BASE_URL` 主机 MUST 为 `127.0.0.1`/`localhost`/`host.docker.internal`(compose 部署形态下容器到宿主机的 localhost 等价形式,design D3);非白名单主机拒配并启动告警——与 021 多模型注册表「白名单外拒配」同构,防止把个人 vault 暴露给远端地址。
4. **单租户闸**:个人知识源没有租户语义。多租户部署(租户数 > 1)MUST NOT 注册该工具——所有租户可见同一 vault 是数据越界,不是功能。启动检测,不满足即不注册并告警。
5. **失败降级为观察,不升级为崩溃**:siku 拒连 → 结构化错误观察(unreachable),agent 据实说明「个人知识库暂不可用」,不编造检索结果。连接层未送达类失败复用 072 只读分层重试一次;响应类错误(4xx/5xx/协议错)不重试,直接降级为观察(design D6)。
6. **授权过滤**:工具带权限点 `tool:knowledge:search`,经既有 `filter_specs` 按调用方授权集过滤——未授权调用方看不到也用不了。

## Non-goals

- **siku 侧零改动**:`/api/search` 读契约已存在且稳定(Phase 1 已交付),本变更只写 kongming 侧。
- **不接 `/api/ask`**:siku 的问答端点走 siku 自己的 BYOK LLM 链路。接进来等于引入第二问答链路、第二套模型配置与双倍 token 成本,且答案绕过孔明的护栏、审计与数据权限——与「万事问孔明」的单点可控目标相反。
- **不做双库检索融合**:kongming RAG 三路召回与 siku 检索结果不合并、不互相 rerank。两库口径混淆(同一概念两个来源)比少一路召回更危险;agent 自行取舍并在答案中标注来源,是 v1 的诚实边界。
- **不做图谱/wiki 检索**:v1 只接文本检索路;图谱实体检索建立在 siku `/api/graph` 之上,是另一类契约,按需再立项。
- **多租户知识源**:每租户各配各的知识库是产品级能力(隔离/市场/计费),本变更明确只服务单租户个人部署。
- **租户级 UI 配置页**:部署级 env 配置,个人单部署场景不为一个开关建 CRUD。

## Capabilities

### New Capabilities

- `external-knowledge`: 外部个人知识库检索工具的注册条件(部署级配置 + localhost 白名单 + 单租户闸)、执行路径(控制面收口)、观察格式(来源锚点)、失败降级与审计契约。

### Modified Capabilities

- `tool-calling`: 工具注册表增补「部署级可选内置工具」语义——未配置时不注册、不注入、不出现在任何工具清单,调用方零感知;已配置时与内置工具同范式(权限过滤 + 执行审计 + 不重试副作用)。

## Impact

- **落点(全部在 apps/api,apps/agent 零改动)**:
  - `kongming/toolcall/registry.py`:`REGISTRY` 保持静态纯数据,新增 `build_registry(settings)` 装配函数——配置了 siku 端点且单租户时追加 `knowledge_search`(registry 零框架纪律不变,settings 经参数传入,不 import 框架)。
  - `kongming/toolcall/service.py`:分发新增 `knowledge_search` 分支,执行器经领域端口调检索,审计沿用既有工具调用落库。
  - 新增 `kongming/toolcall/knowledge.py`:领域 Protocol(`KnowledgeSearchPort`)与结果值对象 + httpx 实现(localhost、超时、连接层重试分层复用 072 原语)。
  - `kongming/settings.py`:新增 `KM_SIKU_BASE_URL`(缺省空)、`KM_SIKU_TIMEOUT_MS`(缺省 5000)与主机校验纯函数。
  - `apps/api/tests/`:注册闸/白名单拒配/单租户闸/降级分类/mock siku 端到端。
- **契约**:ReAct 端点对外契约不变(工具集是运行期注入数据,非 OpenAPI 静态面),`contracts/openapi.json` 预期无漂移——按 072 同款口径核对后记录,无需 `make contracts`。
- **ADR 引用**:ADR-002(信任边界:agent 仍零凭证;siku localhost 无鉴权,出站与配置校验收口控制面);白名单纪律为 021 spec 层口径的同构延伸,非新 ADR。
- **与既有 change 的交互**:072(只读重试分层复用其原语,不自建第二套);document-rag(双知识源并存,本 spec 明示不融合,防粒度互相污染)。
