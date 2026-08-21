# 012 管理台状态启停范式统一(admin-status-toggle)

## Why

管理台两个管理页的「启用/禁用」交互形态不一致且偏重:用户页状态列只有静态
Badge,启停要走「编辑对话框→Select 切换→保存」四步;数据源页是 Badge+行内
文字按钮。二元、即时生效、高频的状态切换,标准控件是 Switch——一步到位、
语义直观。同时存在两处真实缺口:①用户启停没有专用端点,前端若走通用更新
端点需按 role_keys 反查 role_ids 组装载荷(踩 007「roles 未就绪」坑,脆弱);
②禁用用户只挡新登录与下一次权限点校验,其 refresh 家族仍可续换访问令牌到
TTL 到期——改密已有吊销管线(007),禁用反而不吊销,语义缺口。

## What Changes

- **用户启停专用端点**:新增 `POST /system/users/{id}/enable` 与
  `POST /system/users/{id}/disable`(权限点 system:user:manage,与数据源
  008 同范式);保护规则与编辑路径同构:自禁 409、超管账号禁禁用 409、
  目标不存在 404;审计动作 user.enable/user.disable。
- **禁用即吊销 refresh 家族**:禁用(专用端点与编辑对话框路径同源收口)
  后吊销该用户全部 refresh 家族,复用 007 改密吊销管线——堵「禁用后旧
  refresh 仍可续换」的窗口;启用不涉及。
- **StatusSwitch 公共组件**:新 shadcn switch 组件之上封装行内启停开关
  (受控 checked=服务器真值、请求飞行期禁用、失败自动回弹+行内
  role=alert,008「行内启停失败静默」教训固化);用户页与数据源页状态列
  统一接入,消两处各自形态;编辑对话框内 Select 保留(表单提交语义)。

## Capabilities

### New Capabilities

(无——rbac 与 web-admin 既有能力域内的行为修订)

### Modified Capabilities

- `rbac`: 修订「用户启用与禁用」——补专用启停端点 scenario(保护规则与
  审计);补「禁用即踢下线」scenario(禁用任一路径吊销 refresh 家族,
  与改密踢下线对称)。
- `web-admin`: 修订「管理台用户页」与「数据源管理页」的状态列呈现语义
  (行内开关即时启停+失败回弹,对话框编辑路径保留)。

## Non-goals

- 租户/角色启停(租户无启停语义;角色靠删除保护,不在本变更)。
- 批量启停、启停确认弹窗(二元即时操作,失败回弹即反馈;确认属过度设计)。
- 禁用用户的存量 access token 即时失效(JWT 无状态,已由下一次权限点校验
  拒绝兜底,spec 既有语义不变)。
- 会话列表/在线设备管理等账号生命周期深化。
- 不改用户编辑对话框的 Select 形态(表单语境)。

## Impact

- **ADR 引用**:ADR-005(契约管线——新增 2 用户面端点,`make contracts`
  重生成提交);ADR-003 不新增(认证体系不变,refresh 吊销复用既有家族
  机制);ADR-004 不触发(零表结构变更)。
- **apps/api**:system 域 service(启停用例+吊销收口)/router(2 端点)/
  审计动作清单扩展;测试先行(403/404/409×2/吊销直证/审计断言)。
- **apps/web**:新 shadcn switch 组件(CLI 引入,006 R4 纪律核验)、
  StatusSwitch 公共组件、UsersPage/DatasourcesPage 状态列接入、
  system-api.ts 封装。
- **contracts**:openapi.json 重生成(+2 端点);web 类型再生成。
- **测试**:api 安全面(权限/保护规则)测试先行;web 经 Playwright MCP
  冒烟(启停/失败回弹/自禁 409 文案可见)。
