## Purpose

让平台从单轮问答升级为可编排的自动化:JSON DAG 定义(版本化)经
拓扑序引擎执行,确定性节点(SQL/HTTP/条件/循环/转换)与 AI 节点
(ReAct 代理)混合编排;引擎零凭证,节点执行 100% 复用既有防御层。

## Requirements

### Requirement: 工作流定义与版本化

系统 SHALL 支持 JSON DAG(nodes+depends_on)定义工作流,MUST 结构
校验:节点 id 唯一非空、depends_on 引用存在、无环(报错指出参与
环节点)、无孤立节点(多节点时)、节点类型合法(∈ sql/http/
condition/loop/transform/agent)、类型必填配置齐全;condition 后继
节点的 on 值 MUST ∈ {"true","false"},非法值定义期拒绝(不放行
到执行期静默按 true 处理)。定义 MUST
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

#### Scenario: on 值非法定义期拒绝

- GIVEN 某节点 depends_on 条件节点且 on="yes"
- WHEN 提交保存
- THEN 拒绝保存(on 值仅允许 true/false)

### Requirement: DAG 执行引擎

系统 SHALL 提供拓扑序执行引擎:节点输出经 {{node_id.outputs.field}}
引用语法流转,上下文 MUST 不可变(节点写入冻结历史);循环节点
forEach MUST 有最大迭代上限(配置值且硬顶 100);全局执行 MUST
有超时(默认 120s)与总步数上限(默认 200,循环迭代计入),超限
强制终止,run 记录 status=TIMEOUT/STEP_LIMIT 与已完成节点快照;
TIMEOUT MUST 仅归因全局预算到期——节点内部自行抛出的超时
(socket/驱动语句超时等内源 TimeoutError)SHALL 按节点失败
(FAILED)归类并携带「节点内部超时」明确文案,不得误标 TIMEOUT
「超全局预算中断」;节点失败 MUST fail-fast(run FAILED,保留已
完成快照)。条件跳过判定 SHALL 仅对 condition 类型依赖的前驱应用
on 选路门(其他类型节点产出 bool 型 result 键不触发条件跳过)。
循环体内子节点执行 SHALL 逐次记入 run 快照(node_id 以迭代标记
限定,如 `<loop>.<i>.<sub>` 形态),失败子节点按其带迭代标记的
自身 id 归因 FAILED(不只归因到 loop 节点);子节点 outputs 沿用
节点级截断纪律(sql 行数/http 体积同款上限)。

#### Scenario: 条件分支选路

- GIVEN 条件节点后继标记 on="true"/on="false"
- WHEN 条件求值为 False
- THEN on="true" 后继跳过(SKIPPED),on="false" 后继执行

#### Scenario: 非 condition 前驱的 bool result 不触发选路

- GIVEN 某节点依赖一个 transform 节点(其 outputs 含 bool 型 result 键),未依赖任何 condition 节点
- WHEN 引擎执行到该节点
- THEN 不应用 on 选路门,节点正常执行

#### Scenario: 循环上限保护

- GIVEN forEach 循环配置错误条件(数组无限或迭代不收敛)
- WHEN 迭代达上限
- THEN 循环节点 FAILED,run 终止,不无限循环

#### Scenario: 循环子节点失败按迭代归因

- GIVEN forEach 循环第 2 轮迭代的某子节点执行失败
- WHEN 引擎 fail-fast 终止
- THEN run 快照含该子节点带迭代标记的 FAILED 行(`<loop>.1.<sub>`
  形态,含错误文案),loop 节点自身 FAILED,已完成节点快照保留

#### Scenario: 全局超时与步数上限

- GIVEN 执行超全局超时或步数超上限
- WHEN 触达
- THEN run 终止,status=TIMEOUT/STEP_LIMIT,已完成节点快照保留

#### Scenario: 节点内源超时归 FAILED

- GIVEN 某节点内部执行抛出 TimeoutError(如驱动语句超时),且全局剩余预算充足
- WHEN 引擎捕获该异常
- THEN 节点按 FAILED 归类(错误文案含「节点内部超时」),run=FAILED,
  不标 TIMEOUT

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
(租户隔离,列表形态为防膨胀剥除 outputs);SHALL 提供 run 详情端点
(GET /system/workflow/{key}/runs/{runId},system:workflow:manage),
返回完整节点快照(含 outputs),可见性与分页列表同口径(租户隔离
+团队归属);工作流执行 MUST NOT 产生权限放大——所有节点以触发者
身份经既有防御层复校。

#### Scenario: 手动触发并落历史

- GIVEN 已启用工作流与持 workflow:execute 的用户
- WHEN 提交运行(携带输入)
- THEN 执行并返回结果;run 历史落表(含节点级快照)

#### Scenario: 执行历史分页

- GIVEN 超管持 system:workflow:manage
- WHEN 查询某工作流 run 历史
- THEN 分页返回(status/耗时/触发源/错误),租户隔离,列表行不含 outputs

#### Scenario: run 详情含完整 outputs

- GIVEN 库内一条已完成 run(列表已剥 outputs)
- WHEN 持 system:workflow:manage 请求该 run 详情
- THEN 返回完整节点快照(含各节点 outputs),可见性与列表同口径

### Requirement: 工作流归属(团队可见性)

工作流定义 SHALL 纯增 team_id 归属列(NULL=个人,零回归);
列表/详情/运行/编辑(新版本/启停/删除)SHALL 按 team-workspace
spec 角色规则过滤(VIEWER 可见可运行/MEMBER 可编辑/OWNER 全权);
非成员 fail-closed(列表过滤+直访 404 同形);归属变更限团队
OWNER 或创建者本人并落审计。

#### Scenario: 团队工作流对成员可见

- GIVEN 工作流归属团队 T
- WHEN T 的 MEMBER 查询并触发运行
- THEN 可见可运行可编辑(新版本)

#### Scenario: 非成员不可见

- GIVEN 工作流归属团队 T
- WHEN 非 T 成员查询或触发
- THEN 列表不含该项;触发 404 同形
