# Spec Delta

## ADDED Requirements

### Requirement: 可视化画布编辑

工作流编辑 SHALL 提供可视化画布形态:六类节点(sql/http/condition/loop/
transform/agent)经拖拽放置,依赖关系经连线表达,condition 节点的后继连线
SHALL 显式携带 on 值(true/false 两类分支边,MUST NOT 出现无 on 值的条件
后继连线);节点配置经侧栏表单编辑,表单字段与该类型定义的必填配置对齐。
画布状态与 JSON DAG 定义 SHALL 双向可转换,转换 SHALL 无损(画布可表达的
定义 ⇄ JSON 互转后语义逐字段一致);JSON 中画布暂不表达的形态 SHALL 明示
并阻断切换到画布(回落 JSON 编辑),MUST NOT 静默丢弃字段。保存 SHALL 走
既有定义端点——结构校验(环/孤立/类型/必填配置/on 值)、版本化、租户隔离
语义与 JSON 编辑完全同源,MUST NOT 出现第二套校验;校验错误 SHALL 在画布
上定位呈现(环参与节点高亮、非法 on 值/缺配置定位到节点)。节点 id 生成
SHALL 与手工定义同规(唯一非空),画布不产生服务端无法接受的定义形态。

#### Scenario: 拖拽建图保存闭环

- GIVEN 管理员在工作流编辑画布
- WHEN 拖入 sql 与 transform 节点、配置必填项并连线后保存
- THEN 定义经既有端点校验通过并产生新版本,列表可见;重新打开画布
  还原同一图形态

#### Scenario: 条件分支边携带 on 值

- GIVEN 画布中 condition 节点连出两条后继边
- WHEN 分别指定 on=true 与 on=false 并保存
- THEN 定义中两个后继节点的 on 值分别为 "true"/"false"(与手工 JSON
  定义契约一致)

#### Scenario: 校验错误定位到节点

- GIVEN 画布中存在 A→B→A 的依赖连线
- WHEN 保存
- THEN 保存被拒且画布高亮 A、B 两节点(环参与节点),错误文案与
  JSON 编辑同源

#### Scenario: 画布不支持的形态明示回落

- GIVEN JSON 定义含画布暂不支持表达的形态
- WHEN 用户切换到画布编辑
- THEN 明示不支持原因并停留在 JSON 编辑态,定义零丢失零改写

#### Scenario: 画布与 JSON 双态互转无损

- GIVEN 画布编辑产出的合法定义
- WHEN 切换到 JSON 文本态再切回画布
- THEN 图形态与切换前一致(节点/连线/配置逐字段一致)
