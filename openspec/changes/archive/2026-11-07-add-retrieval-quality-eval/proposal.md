# Proposal: add-retrieval-quality-eval

## Why

document-rag 的检索质量目前没有直接量化面:eval 域的 Execution Match 与 ai-eval
的 Recall@K 都是 NL2SQL 选表层的间接口径(经 sqlglot 抽表比对),chunk 级检索
(子块召回→父块回填)从未被量化,SAG 对照登记(roadmap 2026-11-07)确立的
借鉴路线第一步就是补上这个基线面——没有它,后续事件-实体扩展(同日第二变更)
拿不出 before/after 证据。

## What Changes

- document-rag 新增管理端**文档检索试跑端点**(只读零副作用):输入问句+数据源,
  返回各召回路(向量/全文)命中与原生分、RRF 融合名次、父块回填与稳定引用键;
  与 nl2sql 侧既有 recall-probe 对称,复用同一判定纯函数(同源)。
- evals harness 新增 **retrieval 显式分层**:文档检索质量评测——固定种子语料
  (跨文档关联事实的 md 文档)+ multi-hop 评测集(gold=文档名+必含文本片段,
  运行时经分块纯函数确定性映射到 gold 父块集)+ Recall@k 指标纯函数。
- 评测集 category 覆盖单跳/同文档多跳/跨文档多跳;语料挂评测租户既有数据源
  key,零新增数据源登记。
- eval-nightly 纳入 retrieval 层;README 基线表新增检索档并记录首轮基线;
  回归红线沿用劣化 ≥5pp 判失败口径。

## Capabilities

### New Capabilities

- `retrieval-eval`: 文档检索质量评测——评测运行形态(进程外 harness 显式分层、
  默认收集隔离、语料幂等前置)、评测集结构(语料+问题集文件态、gold 片段映射)、
  Recall@k 指标与基线红线(纯函数+默认门禁守护、5pp 红线)。

### Modified Capabilities

- `document-rag`: 新增「文档检索试跑」requirement——管理端只读试跑端点,
  逐路命中可见、与主检索同源、零副作用、权限复用既有权限点。

## Impact

- `apps/api/kongming/rag/`: router 增试跑端点;documents_service 提取逐路命中
  形态供试跑与主链路共用(主行为零回归)。
- `apps/api/evals/`: 新增 retrieval 分层(语料、评测集、指标纯函数、runner、
  conftest 前置);README 基线表增行。
- `apps/api/tests/`: 指标纯函数单测 + 试跑端点测试。
- `contracts/openapi.json` + `apps/web/src/api/api-types.gen.ts`: 新端点契约
  产物再生(ADR-005 管线,make contracts)。
- `.github` eval-nightly 工作流: 纳入 retrieval 层。
- agent 进程零改动(信任边界不动,ADR-002);零 DB 迁移(不建新表)。

## Non-goals

- 不做检索评测集自动构造(092 construct 面向 gold_sql,检索题生成另立变更)。
- 不做 web 试跑界面(probe 端点数据面先行,RagPage 试跑面后续搭车)。
- 不引入评测落库表(保持文件态 YAML + pytest harness 形态,与既有 eval 域一致)。
- 不动 nl2sql 侧既有 Execution Match / 间接 Recall@K 口径。
- 不在本变更内实现实体扩展召回(同日第二变更,本变更只交付其基线面)。
