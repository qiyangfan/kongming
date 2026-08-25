# 029 workflow-orchestration:工作流编排

## Why

平台的能力积木已齐(取数/工具/沙箱/Bot),但都是「一问一答」单轮形态。
真正的自动化价值在把它们串成确定性流程:取数→计算→判断→通知。本变更
落地工作流域:JSON DAG 定义(版本化)+ 拓扑序执行引擎(上下文不可变
流转)+ 五类节点(SQL/HTTP/条件/循环/转换)+ AI 节点(复用 019 工具
回调)+ 手动/API 触发 + run 历史。所有节点执行复用既有防御层——引擎
本身不持凭证、不开新通路(上游 workflow-orchestration 域的 Kongming
适配)。

## What Changes

- **定义与版本化**(api 新 `workflow` 域包 + 0017 迁移
  sys_workflow_def):JSON DAG(nodes+edges)结构校验(无环[指出
  参与环节点]/无孤立节点/节点类型合法/必填配置齐全);版本冻结
  (发布后不可改,编辑产生新版本;tenant+wf_key+version 唯一);
  租户隔离(404 同形);启停;新权限点 system:workflow:manage
  (排序 22)。
- **执行引擎**(零框架 domain 核心):拓扑序逐节点执行;节点输出经
  `{{node_id.outputs.field}}` 引用语法流转,上下文不可变(深拷贝
  写入);循环节点 forEach(数组引用,最大迭代上限,超限 FAILED);
  全局超时(asyncio 预算)+ 总步数上限,超限 TIMEOUT/STEP_LIMIT
  结构化错误;节点失败 fail-fast(标记 FAILED 即止,已完成节点
  快照保留)。
- **节点类型与纵深防御**:
  - sql 节点:复用 internal 执行全链路(防火墙/行级/只读池——
    工作流不开 SQL 写通路);
  - http 节点:SSRF 防护(http/https+私网/回环/链路本地/元数据
    地址 DNS 解析后判定+响应 1MB+超时 15s);
  - condition 节点:`{{ref}} 比较符 值` 求值(==/!=/>/>=/</<=/
    contains;无 eval——手写解析);
  - loop 节点:forEach(数组引用+子节点段+迭代上限);
  - transform 节点:模板映射(字符串/对象模板,`{{ref}}` 替换);
  - agent 节点:自然语言指令经 019 ReAct 回调(触发者权限点集
    过滤工具,六层防御全量;channel="workflow" 审计溯源)。
- **触发与历史**:手动/ API 触发(POST /system/workflow/{key}/run,
  workflow:execute 权限点[排序 23,进 DEVELOPER 推荐集——节点级
  防御逐层以触发者身份复校,与 chat:execute 同哲学]);run 历史
  sys_workflow_run(触发源/输入/每节点状态与输出快照/总耗时/status);
  分页查询(超管管理面)。
- **web**:/admin/workflow 第十二子页——定义列表+JSON 编辑器
  (textarea+校验反馈;不做拖拽画布,见 Non-goals)+启停+运行
  对话框(输入 JSON)+run 历史分页(节点状态逐行可查)。

## Capabilities

### New Capabilities

- `workflow-orchestration`:定义与版本化、DAG 执行引擎、节点类型与
  纵深防御、触发与执行历史的行为契约。

### Modified Capabilities

- 无(纯新域;sql/http/agent 执行经既有端口回调,不改被复用域)。

## Non-goals

- **不做可视化拖拽画布**(上游 VueFlow 形态):v1 用 JSON 编辑器+
  结构校验反馈;画布编辑器是独立大工程,随后续前端域。理由:用户
  价值在引擎+触发+防御,画布是编辑器体验增强而非能力前提。
- 不做定时/webhook 触发(032 triggers-webhooks 域);不做执行暂停/
  续跑/人工介入节点;不做并行分支(v1 拓扑序串行——确定性优先)。
- 不做子工作流嵌套调用;不做节点级重试策略(失败即 fail-fast);
  不做表达式语言(手写比较/模板替换,永不 eval)。
- agent 节点不提供自定义 system prompt(指令即 inputs;Bot 化随
  后续);http 节点不支持自定义认证方案(headers 明文配置进定义,
  管理面资产;SSRF 闸不变)。
- 不改 agent/web 既有链路;契约纯增。

## Impact

- **代码**:api 新 workflow 域包(models[DAG 校验纯函数]/engine
  [执行引擎]/ports/repository/service/router)+ web WorkflowsPage。
- **Schema**:0017 迁移两表 sys_workflow_def/sys_workflow_run
  (ADR-004 约定)。
- **契约**:/system/workflow/* 端点族入 OpenAPI;make contracts。
- **ADR 引用**:ADR-002(引擎零凭证,SQL/AI 全回调)/ADR-004/
  ADR-005。
- **安全**:安全规则类测试先行(环检测/SSRF 各形态/循环上限/
  超时/步数/行级复校/权限点/跨租户);HTTP 节点是 SSRF 首要面,
  DNS 解析判定+私网段全枚举。
