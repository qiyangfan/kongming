# Proposal

## Why

095 任务 2.7(SHOULD)遗留:召回试跑端点(064 建)自始 API-only,web 前端无任何
recall-probe 消费面(`RagPage.tsx` 零消费,生成类型里出现该端点即全部)。两个高频
场景——「上传表说明后验证可召回性」与「召回闸门判定观察」——目前只能手工
调 API;知识运营动线(上传 → 验证召回)应在同一管理页闭环。

## What Changes

- 知识库管理页内新增「召回试跑」面板:问句输入 + 数据源选择 → 渲染三路命中
  (各路原生分)、闸门判定结论(verdict 与被裁命中数)、零知识旁路标注
  (`gate_skipped=no_knowledge`)与关系扩展明细(补入表 / 经由关系 / 扩展后表集合)。
- 试跑端点补响应模型(`response_model`)使前端类型化消费(ADR-005 契约管线单源):
  响应键集锁定为现有 service 返回形态,纯定型、零行为变更;`contracts/openapi.json`
  随管线重生成。
- 面板为纯只读消费:权限复用既有页面守卫(system:rag:manage),不新增权限点;
  零副作用语义(不改配置/不落库/不触发缓存失效)由端点既有契约保证。
- UI 一律 shadcn/ui 标准件与既有管理面标准件(空态/错误告警/结构化列表双形态)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `web-admin`:「知识库管理页」requirement 扩展——页内新增只读「召回试跑」面板的
  行为契约(输入、三路命中与闸门结论渲染、扩展明细、零知识标注、只读边界、窄屏形态)。

## Impact

- `apps/api`:仅 rag 域为试跑端点补响应 schema(纯定型);契约产物经 `make contracts`
  重生成并入库(CI 漂移门禁口径不变)。
- `apps/web`:RagPage 新增试跑面板(抽独立子组件,主文件体量可控);消费生成类型。
- `apps/agent`:零改动(试跑为管理台端点,不在 agent 回调链路)。
- schema:零变更;权限:零新增。
