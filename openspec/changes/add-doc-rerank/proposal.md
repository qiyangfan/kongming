# Proposal: add-doc-rerank(文档检索精排层)

## Why

ruoyi-ai 对齐调研(`docs/ruoyi-ai-alignment.md` §4)实证孔明检索管线止步于 RRF
融合排序:document-rag 的子块双路召回(向量+全文)经 RRF 融合后直接按名次截断,
无精排层。ruoyi-ai 的同链路为「粗召回×3 扩量 → rerank 精排 → 阈值过滤」,检索
质量工程比孔明多一层。问答智能体方向(ADR-010)以知识问答为核心场景之一,
精排是该场景检索质量的行业标准一环(候选扩量后重排能显著提升 top-K 命中率),
且 rerank 出站形态与 094 web 检索同构,增量接入成本低。

## What Changes

- **精排级插入**:document-rag 检索在 RRF 融合之后、父块回填之前增加可选
  rerank 级——候选池扩量(部署级可配,缺省 top-20 子块)→ rerank 打分 →
  按分数取既有 top-5 父块边界;未启用时行为与现状逐字节一致(RRF-only)。
- **供应商注册**:rerank 出站适配走 Cohere 形态 `/rerank` 归一约定(事实
  标准:query+documents→scores),起步适配硅基流动与百炼(gte-rerank)两家;
  部署级环境变量条件注册,未配 key=精排级从未存在(同 websearch 094 形态),
  本地已有百炼 key 即真实生效。
- **降级语义**:rerank 服务不可用/超时/报错时 SHALL 降级为 RRF-only 继续作答
  (同「embedding 不可用降级仅全文路」先例),降级事件计入可观测,不静默。
- **阈值可配**:rerank 分数阈值部署级可配,缺省 0(不过滤)保证零回归;
  阈值过滤只作用于文档通道命中集,不影响「文档未命中退化为纯数据答」的
  既有收口语义(knowledge-qa)。
- **信任边界零变化**:检索与 rerank 出站均发生在 api 控制面(ADR-002 边界
  不动,agent 面零凭证零新增出站);出站协议形态属 ADR-008 multi-protocol
  egress 的既有框架内新增一路。
- **既有面复用**:出站审计与调用计量进既有面(复用 websearch 域出站通道与
  计量形态);引用键稳定性由既有契约保障(068:键派生与名次无关,精排改变
  名次不破坏引用)。

## Capabilities

### New Capabilities

(无——本变更是既有检索管线的层级增强,不立新域。)

### Modified Capabilities

- `document-rag`:「文档检索(small-to-big + 混合召回)」Requirement 扩精排
  语义(RRF 融合后的可选 rerank 级、扩量与截断边界、降级与阈值行为)。

## Impact

- `apps/api`:document 检索服务内插入精排级;rerank 供应商适配器(两个起步)
  + 部署级条件注册;新增环境变量(rerank 供应商/key/模型名/扩量/阈值,均有
  缺省)。零新表、零 Alembic 迁移(配置全部署级)。
- `apps/agent`:零改动(检索经既有内部接口,响应形态不变,仅命中顺序可能
  变化)。
- `contracts/openapi.json`:零变化(内部检索接口与响应形态不变;rerank 不
  暴露任何新公共端点)。
- `apps/web`:零改动。
- 依赖:零新增第三方库(httpx 既有)。

## Non-goals

- **表说明召回精排**(rag 域三路召回):schema 选取候选集小(topK=5)且由
  生成端模型消化,精排边际收益低;登记 roadmap 候选,不在本变更内。
- **跨通道精排**(文档段与表说明段联合重排):两通道物理+逻辑双隔离是
  document-rag 既有铁律,不因精排破例。
- **管理面 rerank 配置 CRUD**:v1 部署级环境变量注册(同 websearch 形态),不做租户级管理端。
- **rerank 分数下发前端**:内部检索响应形态不变,score 不进公共契约;
  调试可见性走可观测面。
- **评分模型本地化/自建 rerank**:只接外部 API,不引入本地推理。
