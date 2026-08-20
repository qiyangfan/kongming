# web-admin 能力域 delta(009)

## MODIFIED Requirements

### Requirement: 管理面路由与页面守卫

/admin 为管理区根,SHALL 含五个子页(用户/角色/部门/数据源/知识库);子页 SHALL 按各自权限点守卫
(用户/部门页 system:user:manage,角色页 system:role:manage,
数据源页 system:datasource:manage,知识库页 system:rag:manage);侧栏管理入口与子页导航数据同源;
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

#### Scenario: 知识库子页守卫
- GIVEN 用户角色不含 system:rag:manage
- WHEN 直达 /admin/rag
- THEN 渲染 403 页,子导航不呈现知识库入口

#### Scenario: 子页导航
- GIVEN 管理面已渲染
- THEN 按权限呈现用户/角色/部门/数据源/知识库子页入口,当前子页高亮

## ADDED Requirements

### Requirement: 知识库管理页

知识列表(列:数据源/schema/表名/说明摘要/来源标记/更新时间)按当前租户
上下文拉取,支持数据源筛选与分页;上传经文件选择(md/markdown/txt/pdf/docx)
+ 数据源/schema 提交;删除为显式确认;页内 SHALL 提供「抓取元数据」与
「重建索引」操作(需数据源选择,重建索引为破坏性显式确认,完成后展示
{deleted, rebuilt, enriched} 结果);进行中操作 SHALL 有 loading 态与错误反馈。

#### Scenario: 上传闭环
- GIVEN 管理员在知识库页
- WHEN 选择 Markdown 文件并选择目标数据源/schema 后提交
- THEN 列表刷新可见新条目(来源=上传),错误时表单上方展示端点错误信息

#### Scenario: 抓取元数据触发与反馈
- GIVEN 管理员选择已启用的数据源
- WHEN 点击「抓取元数据」
- THEN 展示进度态,完成后刷新列表并展示发现/润色表数

#### Scenario: 重建索引受控确认
- GIVEN 管理员选择数据源
- WHEN 点击「重建索引」
- THEN 弹出确认对话框(明示将清空该数据源全部表说明后重建),确认后执行,
完成后展示 {deleted, rebuilt, enriched};取消则不动
