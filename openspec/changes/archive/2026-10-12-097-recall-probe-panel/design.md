# Design

## Context

- 试跑端点 `GET /system/rag/recall-probe` 已存在(rag/router.py,挂
  `system:rag:manage`),返回 `dict[str, object]`(无 response_model);
  响应键:question / gate_skipped / verdict / 三路命中(各路原生分、判定
  结论与被裁数)与 expansion(enabled / tables[{schema,table,via}] /
  fused_after)。106 收编三态后闸门恒生效(无 mode 字段),零副作用语义
  已由端点保证(不走 ctx 缓存)。
- `RagPage.tsx` 约 800 行,已含数据源下拉(`datasourceApi.options`,三处复用)、
  上传/编辑/删除/扫描/重建各操作区;页面守卫即 `system:rag:manage`。
- 前端禁手写 API 类型(ADR-005);当前生成类型对试跑响应为宽松形态
  (端点无 response_model)。

## Goals / Non-Goals

**Goals:**

- 面板类型化消费:给端点补响应模型,契约管线重生成,前端消费生成类型。
- 知识运营动线同页闭环:上传后立即可验证可召回性。
- 渲染如实:零命中/零扩展/闸门关闭/零知识旁路全部明示,不美化。

**Non-Goals:**

- 不改试跑端点行为与判定逻辑(含阈值配置面)。
- 不做批量试跑、评估集回放、历史试跑记录留存(零落库纪律)。
- 不新增权限点、不改路由与导航结构(面板在知识库管理页内)。
- 不动 agent 面(端点不在 `/internal` 链路)。

## Decisions

1. **端点补 response_model(纯定型)**:service 返回键集已是事实契约,把
   `dict[str, object]` 定型为显式 schema(嵌套:三路命中项、闸门结论、
   expansion 明细),键名与现值逐一对齐;`None`/可选键按现状标注可选。
   备选是前端局部手写视图类型——违反契约单源铁律,否决。定型后
   `make contracts` 重生成,CI 漂移门禁口径不变。
2. **面板抽独立子组件**:`RagPage.tsx` 已约 800 行,试跑面板(表单+三类结果
   渲染)独立成组件文件,数据源下拉选项复用页面既有 options 查询(不重复
   请求);RagPage 仅挂载。
3. **结果渲染分层**:命中明细走管理面结构化列表标准件双形态;闸门 verdict
   与零知识旁路标注用状态徽章标准件(非命中形态用中性徽章,不加新色值);
   扩展明细用定义列表(表少、层级浅,列表组件过重)。
4. **表单形态**:问句单行输入(textarea 单行增长上限内)+ 数据源 Select
   (与页面既有数据源选择同源);试跑按钮触发一次查询,不自动触发
   (避免每次按键打端点;端点便宜但如实原则优先)。

## Risks / Trade-offs

- [定型遗漏响应键或可选性标注错误] → 对照 service 返回逐键核对;为响应
  schema 写序列化往返测试(现返回样例 → 模型校验);CI 契约漂移门禁兜底。
- [RagPage 再膨胀] → 子组件隔离;面板状态不进主文件。
- [宽松 dict 现有消费方] → 全仓检索确认该端点当前零前端消费(生成类型外
  无引用),定型无破坏面。

## Migration Plan

单提交形态:api schema 定型 + contracts 重生成 + web 面板同批落地;无数据
迁移、无配置项;回退即 revert(前端面板独立挂载,api 定型纯增)。

## Open Questions

(无)
