## Purpose

让平台从单轮问答升级为可编排的自动化:JSON DAG 定义(版本化)经
拓扑序引擎执行,确定性节点(SQL/HTTP/条件/循环/转换)与 AI 节点
(ReAct 代理)混合编排;引擎零凭证,节点执行 100% 复用既有防御层。

## ADDED Requirements

### Requirement: 工作流定义与版本化

系统 SHALL 支持 JSON DAG(nodes+depends_on)定义工作流,MUST 结构
校验:节点 id 唯一非空、depends_on 引用存在、无环(报错指出参与
环节点)、无孤立节点(多节点时)、节点类型合法(∈ sql/http/
condition/loop/transform/agent)、类型必填配置齐全。定义 MUST
版本化:同 wf_key 再保存即产生新版本(max+1),旧版本不可改
(无更新路径);启停仅作用于最新版本;按 tenant_id 隔离(跨租户
404 同形)。

#### Scenario: 环检测

- GIVEN 含环(A 依赖 B,B 依赖 A)的定义
- WHEN 提交保存
- THEN 拒绝保存,错误指出参与环的节点

#### Scenario: 版本冻结

- GIVEN 已有 v3 的工作流
- WHEN 再次保存同 key 定义
- THEN 产生 v4,v3 不变;旧版本随新版本产生自动失活(启用闸仅作用
  最新版本);执行按指定版本,缺省取最新版本,禁用即 409

#### Scenario: 跨租户隔离

- GIVEN 租户 A 的工作流
- WHEN 租户 B 用户访问/执行
- THEN 404 同形(fail-closed)

### Requirement: DAG 执行引擎

系统 SHALL 提供拓扑序执行引擎:节点输出经 {{node_id.outputs.field}}
引用语法流转,上下文 MUST 不可变(节点写入冻结历史);循环节点
forEach MUST 有最大迭代上限(配置值且硬顶 100);全局执行 MUST
有超时(默认 120s)与总步数上限(默认 200,循环迭代计入),超限
强制终止,run 记录 status=TIMEOUT/STEP_LIMIT 与已完成节点快照;
节点失败 MUST fail-fast(run FAILED,保留已完成快照)。

#### Scenario: 条件分支选路

- GIVEN 条件节点后继标记 on="true"/on="false"
- WHEN 条件求值为 False
- THEN on="true" 后继跳过(SKIPPED),on="false" 后继执行

#### Scenario: 循环上限保护

- GIVEN forEach 循环配置错误条件(数组无限或迭代不收敛)
- WHEN 迭代达上限
- THEN 循环节点 FAILED,run 终止,不无限循环

#### Scenario: 全局超时与步数上限

- GIVEN 执行超全局超时或步数超上限
- WHEN 触达
- THEN run 终止,status=TIMEOUT/STEP_LIMIT,已完成节点快照保留

#### Scenario: 上下文不可变

- GIVEN 节点 A 已产出 outputs
- WHEN 后续节点执行
- THEN 后续节点经引用语法读取,任何节点无法改写 A 的 outputs

### Requirement: 节点类型与纵深防御

系统 SHALL 内置六类节点,防御语义:
- sql 节点 MUST 复用 internal 执行全链路(防火墙/行级权限/只读池,
  触发者身份),verdict 非 PASS 即节点 FAILED——工作流不开 SQL
  写通路;
- http 节点 MUST SSRF 防护:仅 http/https;host 解析后任一地址落入
  私网/回环/链路本地/元数据段 MUST 拒绝(节点 FAILED,不发出请求);
  不跟随重定向;响应 1MB 截断;超时 15s;
- condition 节点 SHALL 支持 ==/!=/>/>=/</<=/contains(数值优先/
  字符串兜底),MUST NOT 使用 eval;
- loop 节点 SHALL 为 forEach(数组引用+子节点段+迭代上限);
- transform 节点 SHALL 为模板映射({{ref}} 递归替换);
- agent 节点 SHALL 经 019 ReAct 回调执行(工具集=触发者权限点
  过滤,channel="workflow" 审计溯源)。

#### Scenario: SQL 节点不开写通路

- GIVEN sql 节点配置 DROP 语句
- WHEN 执行
- THEN 防火墙拦截(节点 FAILED),只读账号物理无 DDL 权限

#### Scenario: HTTP 节点 SSRF 防护

- GIVEN http 节点配置 169.254.169.254(云元数据端点)或内网地址
- WHEN 执行
- THEN 拒绝(节点 FAILED),不发出网络请求

#### Scenario: agent 节点权限复校

- GIVEN 触发者无 tool:sql:execute 权限
- WHEN 执行含工具需求的 agent 节点
- THEN 注入工具集不含 execute_sql(触发者权限过滤),落审计

### Requirement: 触发与执行历史

系统 SHALL 支持手动与 API 触发(POST /system/workflow/{key}/run,
workflow:execute 权限点,trigger_source=MANUAL/API),SHALL 记录
run 历史(sys_workflow_run:触发源/输入/每节点状态与输出快照/总耗时/
status);管理面(system:workflow:manage)SHALL 可分页查询 run 历史
(租户隔离)。工作流执行 MUST NOT 产生权限放大——所有节点以触发者
身份经既有防御层复校。

#### Scenario: 手动触发并落历史

- GIVEN 已启用工作流与持 workflow:execute 的用户
- WHEN 提交运行(携带输入)
- THEN 执行并返回结果;run 历史落表(含节点级快照)

#### Scenario: 执行历史分页

- GIVEN 超管持 system:workflow:manage
- WHEN 查询某工作流 run 历史
- THEN 分页返回(status/耗时/触发源/错误),租户隔离
