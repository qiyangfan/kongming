# web-admin 能力域(007 新立)

## Purpose

管理面(应用壳内 /admin 区)的界面行为:路由结构、页面级权限守卫、
用户/角色/部门/数据源各管理页的结构与交互契约。数据行为归 rbac/datasource 域端点。

## Requirements

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

### Requirement: 用户管理页

用户页 SHALL 呈现表格列:用户名/昵称/状态/角色/部门/创建时间;分页控件
与关键字搜索;创建与编辑经对话框表单(创建后用户名不可改;角色多选;
部门单选;租户绑定;状态启停);重置密码与删除为显式确认操作;状态列为
行内启停开关(见「状态列行内启停范式」)。

#### Scenario: 创建用户闭环
- GIVEN 管理员在用户页
- WHEN 提交创建表单(用户名/密码/角色/部门/租户)
- THEN 列表刷新可见新用户,密码不回显

#### Scenario: 删除确认
- GIVEN 用户列表已渲染
- WHEN 点击删除
- THEN 弹出确认对话框,确认后调用删除并刷新;取消则不动

### Requirement: 状态列行内启停范式

用户页与数据源页的状态列 SHALL 呈现行内启停开关:开关视觉状态 SHALL
取服务器真值(非乐观长驻),请求飞行期禁用防抖动,失败 SHALL 自动回弹
并在行内以可达性角色暴露错误;点击即发起启停,无需确认弹窗。编辑对话框
内的状态 Select 保留(表单提交语义,与行内开关并存)。

#### Scenario: 行内开关启停
- GIVEN 用户/数据源列表已渲染
- WHEN 点击某行状态开关
- THEN 立即调用启停并刷新列表,开关反映新状态

#### Scenario: 启停失败回弹
- GIVEN 管理员对自己的用户行点击禁用开关
- WHEN 服务端返回 409
- THEN 开关回弹为原状态,行内展示错误文案(不静默)

### Requirement: 角色管理页

表格列:角色键/名称/data_scope/sql_scope/排序/内置标记;
创建与编辑表单(roleKey 创建后不可改,dataScope/sqlScope 下拉);
权限点绑定面板按命名空间分组复选,保存为全量覆盖;内置角色的
删除操作不可用;删除非内置角色需确认。

#### Scenario: 权限绑定覆盖式保存
- GIVEN 角色已绑定权限点 A、B
- WHEN 在绑定面板取消 B 后保存
- THEN 角色绑定变为仅 A

#### Scenario: 内置角色保护可见
- GIVEN 角色列表含 SUPER_ADMIN
- THEN 其删除操作不可用(禁用态)

### Requirement: 部门管理页

树形展示部门层级;创建/编辑经表单(名称/父部门选择/排序);
删除需确认且由端点前置校验兜底(有子部门或用户绑定时展示错误)。

#### Scenario: 部门树渲染与新增
- GIVEN 已有部门层级
- THEN 树形展示各层级
- WHEN 新增子部门并保存
- THEN 树刷新呈现新节点

### Requirement: 写操作反馈

管理面全部写操作有即时反馈:成功后刷新对应列表;失败展示
端点错误信息;进行中操作不可重复提交。

#### Scenario: 失败反馈
- GIVEN 提交创建角色时 roleKey 已存在
- WHEN 服务端返回冲突
- THEN 表单上方展示错误信息,列表不变

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
