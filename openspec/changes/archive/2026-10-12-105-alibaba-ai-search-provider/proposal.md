# Proposal

## Why

web 检索域(094)已接 tavily/bocha(+zhipu 收尾),供应商适配框架把「接入
新供应商」收敛为「两条映射 + 配置枚举一项」。**阿里云 AI 搜索开放平台**
的联网搜索 API 契约已核对(2026-10-11,见 HANDOFF 检索供应商调查结论):
`POST {host}/v3/openapi/workspaces/{ws}/web-search/{service_id}`,请求
`{query, top_k, content_type}`,响应 `result.search_result[]{title, link,
snippet, content}`——发查询、返回全网网页结果卡,与 tavily/bocha/zhipu
完全同构。接入是纯实例化工作——检索供应商广度 3→4,补齐国内大厂谱系;
e2e 验证待真实 key(代码与契约桩测试不依赖 key)。

**命名澄清**:该服务归在阿里云 OpenSearch 品牌线下,但**不是**「自建
索引的托管搜索引擎」那条老产品线(那种需开实例、灌数据建索引、查的是
自己的索引)——本候选消费的是其**联网搜索服务**(web-search),开通
即用、按次计费、无实例无运维,语义上就是标准 web search API。

## What Changes

- 新增 `alibaba_ai_search` 检索供应商适配器:请求映射(查询词/条数/
  页参)与响应映射(结果结构 → 标准检索卡:标题/链接/摘要)两条映射
  收在适配单点;出口主机白名单由既有「端点与白名单代码单源派生」机制
  自动覆盖。
- 供应商配置枚举扩展一项;条件注册/fail-closed 语义/脱敏 allowlist/
  配额分桶全部复用既有域契约(tool-gateway spec 既有 requirement 零
  修订——通用语义已立,本变更是实例化)。
- 契约桩测试:以核对过的真实报文样例为夹具(响应归一/畸形报文显式失败/
  空结果如实),不依赖真实 key;e2e 待 key 后按 HANDOFF 配方补验。

## Capabilities

(零 spec delta——web 检索域行为契约在 tool-gateway spec 已完整立约
「供应商响应归一为确定性检索卡整形(字段映射收在适配单点);畸形报文
显式按响应类失败收口」,新供应商为该契约下的实例化,无行为契约变化;
`.openspec.yaml` 已置 `skip_specs: true`)

## Impact

- `apps/api`:toolgateway/websearch 适配注册(+两条映射函数)、配置
  枚举一项;契约桩测试(报文样例夹具)。
- `apps/web` / `apps/agent`:零改动;contracts 零变更。
- 外部依赖:无新增包(纯 HTTP 映射);e2e 验证待阿里云 AI 搜索开放平台
  key(`OS-` 前缀独立 key,与百炼 `sk-` 不通用;zhipu 先例:有 key 后
  按配方补验并记录)。
