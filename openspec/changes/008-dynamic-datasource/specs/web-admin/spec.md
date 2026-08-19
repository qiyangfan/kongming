# web-admin 能力域(008 delta)

> 管理区扩第四子页「数据源」;页面行为细节(分页/弹窗/测试连接/删除确认)
> 归 datasource 域「管理 UI」requirement(已立约),本 delta 只立路由与
> 守卫结构契约。

## MODIFIED Requirements

### Requirement: 管理面路由与页面守卫

/admin 为管理区根,SHALL 含四个子页(用户/角色/部门/数据源);子页 SHALL 按各自权限点守卫
(用户/部门页 system:user:manage,角色页 system:role:manage,
数据源页 system:datasource:manage);侧栏管理入口与子页导航数据同源;
无权限直达返回 403 品牌页。

#### Scenario: 按权限进入子页
- GIVEN 用户角色含 system:user:manage 但不含 system:role:manage
- WHEN 进入 /admin/users
- THEN 页面正常渲染
- WHEN 直达 /admin/roles
- THEN 渲染 403 页

#### Scenario: 数据源子页守卫
- GIVEN 用户角色不含 system:datasource:manage
- WHEN 直达 /admin/datasources
- THEN 渲染 403 页,子导航不呈现数据源入口

#### Scenario: 子页导航
- GIVEN 管理面已渲染
- THEN 按权限呈现用户/角色/部门/数据源子页入口,当前子页高亮
