# Proposal

## Why

工具网关后续域清单(089 登记)的第二项:内部系统连接器(工单/日历等)。
web 检索域(094)证明部署级接入范式,HTTP 连接器域(099,同批提案)补租户级
用户自定义;内部系统连接器补第三块:**平台预置方言**——固定工具语义、结构化
参数、标准化摘要,而非任意端点的通用转发。这是「治理型网关」叙事的完成态:
治理谱系三形态(内置工具/部署级域/租户级域)齐备,且首次在网关上演示
**写工具确认流**(工单创建)——读域为主的网关补上写语义治理的真实证据。

## What Changes

- 新增内部系统连接器域(首接工单):部署级条件注册(端点+服务凭证环境变量,
  未配置与「域从未存在」不可区分,照 websearch 范式),预置三个工具:
  - `ticket_search`:按关键词/状态检索工单列表 → 标准化工单卡摘要;
  - `ticket_get`:按 id 查单 → 标准化详情卡;
  - `ticket_create`:创建工单(**写工具,缺省需确认**,走既有写工具确认流)。
- 响应标准化:工单卡 allowlist 字段(id/标题/状态/优先级/负责人/时间戳),
  适配字段映射收在适配单点;畸形报文按响应类失败显式收口,空结果如实
  表达(计数 0)。
- 工具权限:`tool:ticket:search`、`tool:ticket:manage`(create;search 与
  get 同权限点)显式授予制;域治理自动出现在既有域清单治理面
  (system:tool:manage),web 零新增页面。
- 演示与评估基座:仓库提供最小 mock 工单服务(单文件,内存态,按「最小
  工单 API 约定」实现),供 e2e 测试与演示环境出站真链路;约定文档化。
- `apps/agent` 零改动;既有域与内置工具零回归铁律不变。

## Capabilities

### New Capabilities

(无——与 websearch 域同组织,域契约挂 tool-gateway 能力域)

### Modified Capabilities

- `tool-gateway`:新增内部系统连接器域 requirement 组——条件注册与服务凭证、
  预置工具语义与响应标准化、写工具确认、审计与配额。

## Impact

- `apps/api`:tool-gateway 域扩展(域清单声明+适配器/策略器/执行器);
  零 schema 变更(部署级域无新表);contracts 零变更(无新管理端点)。
- `apps/web`、`apps/agent`:零改动。
- 仓库脚本:新增 mock 工单服务单文件(演示/测试用,不进部署形态);
  HANDOFF 补演示配方(mock 启动 + 环境变量 + 问答冒烟路径)。
- 权限:新增 `tool:ticket:search` / `tool:ticket:manage` 两个权限点
  (SUPER_ADMIN 种子覆盖,不进 DEVELOPER 推荐集)。
