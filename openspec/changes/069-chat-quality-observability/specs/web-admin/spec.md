# web-admin spec delta(069-chat-quality-observability)

## MODIFIED Requirements

### Requirement: 管理面路由与页面守卫

/admin 为管理区根,SHALL 含既有子页(用户/角色/部门/数据源/知识库)及后继
增设子页(含问答质量观测页);子页 SHALL 按各自权限点守卫(用户/部门页
system:user:manage,角色页 system:role:manage,数据源页
system:datasource:manage,知识库页 system:rag:manage,问答质量观测页
system:chat-quality:view);侧栏管理入口与子页导航数据同源;无权限直达返回 403
品牌页。

#### Scenario: 按权限进入子页

- GIVEN 用户角色含 system:user:manage 但不含 system:role:manage
- WHEN 进入 /admin/users
- THEN 页面正常渲染
- WHEN 直达 /admin/roles
- THEN 渲染 403 页

#### Scenario: 问答质量观测子页守卫

- GIVEN 用户角色不含 system:chat-quality:view
- WHEN 直达 /admin/quality
- THEN 渲染 403 页,子导航不呈现问答质量入口

#### Scenario: 子页导航

- GIVEN 管理面已渲染
- THEN 按权限呈现用户/角色/部门/数据源/知识库/问答质量子页入口,当前子页高亮

## ADDED Requirements

### Requirement: 问答质量观测页

问答质量观测页(/admin/quality)SHALL 呈现:轮次四态汇总
(SUCCESS/REJECTED/NO_DATA/FAILED 占比与计数)、按天趋势、数据源与 prompt
版本切片,以及轮次明细分页(时间/问题/改写问题/状态/重试次数/评分/trace_id)。
列表 SHALL 采用管理面结构化列表标准件的双形态(宽屏表格/窄屏卡片),状态
SHALL 用状态徽章标准件呈现,空态/错误态 SHALL 用既有空态/错误告警标准件。
明细行动作 SHALL 提供「复制问题」与「跳转知识库管理页」(NO_DATA 知识补全
最小操作面);trace_id SHALL 可跳转既有 trace 时间线。页面数据 SHALL 与观测
端点同源,页面自身零写操作。

#### Scenario: NO_DATA 明细定位与补全入口

- GIVEN 窗口内若干知识未覆盖(NO_DATA)轮次
- WHEN 以 NO_DATA 筛选明细分页
- THEN 仅返回匹配轮次;行内可复制问题原文,并可跳转知识库管理页

#### Scenario: 窄屏形态

- GIVEN 视口 375px
- WHEN 打开观测页
- THEN 明细以卡片形态呈现,行操作收溢出菜单且可达,横向不溢出

#### Scenario: 只读边界

- GIVEN 观测页已渲染
- WHEN 检查页面能力
- THEN 零写操作;「一键转知识」类写动作不在本页(知识域另立)
