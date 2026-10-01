# 082-knowledge-tool-multitenant-warning 提案:单租户闸降级为告警

## Why

081 立的三道注册闸里,「单租户闸(租户数 > 1 即拒注册 + 告警)」被 081 冒烟期间的实证否证:

- **假阳性成本已兑现**:owner 的开发库事实上有 3 个租户(kongming 多租户能力自测残留)。
  冒烟时该闸直接把 `knowledge_search` 变为不存在——我是靠另建 scratch 单租户库
  才测通本功能。一个在开发者真实环境里已经误伤的保护,不成立。
- **真阳性场景被 owner 明确排除**:owner 裁决本项目为个人/求职项目,短期与长期均无
  多租户现网场景;闸要防的「跨租户可见个人 vault」在该操作环境里不存在受害对象。
- **闸是设计缺口的占位符而非解法**:个人知识源无租户语义,正确解法是 028 已有的
  租户级动态工具模式(每租户各配各的知识库);启动闸只是把这个缺口粉饰成「已防护」,
  还附带静默失效(silent disable)这一最差的失败形态——功能消失只有一个没人会
  去看的启动日志行。

裁定:注册条件收窄为两条(端点已配置 + localhost 白名单——两者对应「端点不存在」
而非「环境状态」,零假阳性成本);多租户从**拒注册**降为**告警不改行为**,残留风险
以 spec 明示条款 + 启动告警双重承接,租户级隔离登记 backlog 触发条件。

## What Changes

1. `external-knowledge` spec「部署级注册条件」:删除条件 3(单租户拒注册)对注册的
   否决力;多租户部署 SHALL 输出启动告警(个人知识源无租户隔离,所有租户共享同一
   检索面),但 MUST NOT 拒绝注册;对应 Scenario 翻转。
2. `main._knowledge_endpoint`:多租户分支从 `return None`(拒注册)改为告警后
   `return url`(注册);租户计数查询保留(告警内容需要),其余不变。
3. 测试:`test_endpoint_gate_multi_tenant_blocked`(断言拒注册)翻转为
   `test_endpoint_gate_multitenant_warns_but_registers`(断言注册 + caplog 告警)。

## Non-goals

- **租户级知识源隔离**(028 mcp-interop 模式:每租户各配各的知识库端点、快照注册、
  `knowledge__<tenantKey>__<tool>` 命名):个人部署下是多造一套 CRUD;触发条件 =
  出现真实第二个人类租户,届时应立独立 change 迁入 028 模式,不本变更铺垫。
- **删 localhost 白名单**:入参校验,零假阳性成本,且把 siku 契约的 localhost-only
  事实钉在代码里;与单租户闸不同类,不动。
- **删部署级开关**:无端点 URL 则工具无从注册,开关即配置本身,不存在可删面。
- **运行时租户数重探测**:启动探测一次足够;租户创建是低频显式动作,热探测是给
  不存在的场景加移动件。

## Capabilities

### Modified Capabilities

- `external-knowledge`: 「部署级注册条件」Requirement——注册条件由三条收窄为两条,
  多租户从注册否决项降为告警项,新增多租户可见性条款(明示共享检索面,v1 定位)。

## Impact

- 代码:`kongming/main.py`(`_knowledge_endpoint` 多租户分支翻转,注释同步);
  `apps/api/tests/test_external_knowledge.py`(一个用例翻转 + caplog 告警断言)。
- 契约:无对外端点/字段/交互变化(工具注入集在配置合法时与 081 一致;变化仅在
  多租户环境下从「无工具」变为「有工具」);contracts/openapi.json 不动。
- 与 081 的关系:本变更为 081 design D4(单租户闸)的证否与废止;归档后主
  specs 的 external-knowledge 以本 delta 为准。
- ADR:不涉及(无信任边界/认证/schema/契约管线变化;agent 面仍零凭证、执行仍
  收口控制面)。
