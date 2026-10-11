# Proposal

## Why

工作流定义当前是 JSON DAG 文本形态:用户手工写 nodes+depends_on,结构错误
(环/孤立/非法 on 值)要提交后才被校验拒绝,编辑体验与「可视化拖拽画布」的
产品预期(roadmap 登记)差距大。画布是工作流域从「能用」到「好用」的展示
门面,也是多智能体叙事(静态 DAG 编排)最直观的可视化证据。后端定义契约
(JSON DAG + 结构校验 + 版本化)已完备,本变更纯升级 web 编辑形态。

## What Changes

- 工作流编辑页升级为**可视化画布**:节点面板(六类节点:sql/http/
  condition/loop/transform/agent)拖拽放置、连线表达 depends_on、
  condition 后继连线携带 on 值(true/false 分支边)、节点配置侧栏表单
  (与各类型必填配置对齐)。
- 画布状态与 JSON DAG 定义双向转换:转换层为纯函数;保存走**既有定义
  端点**(校验/版本化/租户隔离语义零变化),校验错误 SHALL 回显并定位到
  画布节点(环参与节点/非法 on 值/缺配置),MUST NOT 出现第二套校验语义。
- 既有 JSON 文本编辑形态保留为高级入口(画布 ⇄ JSON 双向可切,互转无损
  时切换;画布暂不能表达的形态 SHALL 明示并阻断切换到画布)。
- 契约零变更(定义端点与 schema 不动);依赖新增一个前端画布库(见
  design 决策,不在技术栈否决清单)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `workflow-orchestration`:新增「可视化画布编辑」requirement——画布产出
  定义与 JSON 定义同一契约、校验同源、错误定位、无损互转边界。

## Impact

- `apps/web`:工作流编辑页重构为画布形态(新画布组件+节点配置侧栏+JSON
  双态切换);新增前端画布库依赖。
- `apps/api`:零改动(定义端点/校验/版本化全部复用);contracts 零变更;
  schema 零变更;`apps/agent` 零改动。
- 上游参考:无(上游无画布实现,交互形态按 React Flow 惯例)。
