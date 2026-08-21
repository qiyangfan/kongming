# feedback-refine 能力域 delta(014)

## MODIFIED Requirements

### Requirement: 反馈处置写接口

系统 SHALL 提供 `PUT /feedback/{messageId}/status` 端点,权限点 `system:feedback:handle`(种子
排序 8,仅 SUPER_ADMIN 自动覆盖,不推荐绑定 DEVELOPER);JWT 认证;
处置目标归属校验与反馈分页口径一致(数据范围非 ALL 时强制本人会话
的反馈可处置,ALL 可处置全部——SUPER_ADMIN 种子 data_scope=ALL);
status 仅枚举值可写;note 处理语义与提交接口一致。
落库:feedback_status、feedback_handler=当前用户 id、
feedback_handle_time=now、feedback_handle_note;操作日志
`feedback.handle` 与业务同事务落 sys_oper_log(含 messageId 与目标状态)。
messageId 不存在(或数据范围外)返回 404 同形——处置面是管理视角,
与提交面 403 同形口径各自独立(spec 分立约)。

#### Scenario: 处置反馈状态
- GIVEN 用户持有权限点 system:feedback:handle
- WHEN PUT /feedback/{messageId}/status({ status, note })
- THEN status 仅枚举 PENDING/ADOPTED/REJECTED/DISMISSED 可写,非法值返回 400
- AND 落库:feedback_status、feedback_handler=当前用户、feedback_handle_time=now、feedback_handle_note
- AND 操作记 sys_oper_log(feedback.handle,同事务)

#### Scenario: 归属校验防越权处置
- GIVEN 处置人数据范围非 ALL
- WHEN 处置
- THEN 强制校验反馈归属(同分页口径),防越权处置他人反馈
- AND 数据范围 ALL(超管)可处置全部

#### Scenario: note 处理语义
- GIVEN note 参数
- THEN null=保持现值(允许只改状态),显式空串=清空,非空=覆盖(与提交接口 comment 语义一致)

### Requirement: 反馈分页加处置状态筛选

反馈明细分页接口 `GET /feedback/page` SHALL 新增 feedbackStatus 入参(可空,
枚举 PENDING/ADOPTED/REJECTED/DISMISSED,非法值 422);前端列表展示
「处置状态」列与下拉筛选(全部/待处置/已采纳/已驳回/已忽略,
shadcn Badge 分态配色)。

#### Scenario: 分页按处置状态筛选
- GIVEN 反馈明细分页接口
- WHEN 携带可空的 feedbackStatus 参数
- THEN 按 feedback_status 筛选

#### Scenario: 前端筛选与状态列
- GIVEN 反馈管理页
- THEN 提供「处置状态」下拉(全部/PENDING/ADOPTED/REJECTED/DISMISSED)
- AND 列表含「处置状态」列(不同状态不同视觉标签)

### Requirement: 反馈涉及表定位(精修闭环桥梁)

系统 SHALL 提供 `GET /feedback/{messageId}/involved-tables` 端点,权限点 `system:feedback:view`
(种子排序 7,仅 SUPER_ADMIN);从反馈行的 sql_text + corrected_sql 用
sqlglot 抽取表名并匹配知识库条目;解析失败兜底返回空集合。
表名抽取:两 SQL 合并去重(末段名小写折叠,PG 未加引号标识符语义;
CTE 名/表函数放行——与防火墙同一豁免口径);匹配维度
tenant_id(消息所属会话租户)+ datasource_key(会话快照)+ table_name,
schema 名不纳入匹配(知识条目按 default_schema 单 schema 内省,
反馈 SQL 可带任意限定名——命中知识库条目即 hasKnowledge);
解析失败按「失败 SQL 跳过+parse_error=true」降级(全部失败时 tables 为空);行不存在/无 SQL 内容
返回空 tables;数据范围归属校验同分页口径(非 ALL 404 同形)。

#### Scenario: 定位反馈涉及表
- GIVEN 用户持有 system:feedback:view
- WHEN GET /feedback/{messageId}/involved-tables
- THEN 返回 { tables: [{ tableName, schemaName, datasourceKey, hasKnowledge, knowledgeId }] }
- AND 从原始 SQL 与纠错 SQL 合并抽取表名(去重,大小写不敏感)
- AND 按 tenant + datasource_key + table_name 匹配知识库条目
- AND hasKnowledge 标识是否匹配到,knowledgeId 供前端跳转编辑

#### Scenario: 解析失败兜底
- GIVEN 任一 SQL 解析抛异常
- WHEN 定位
- THEN 解析失败的 SQL 跳过并置 parseError: true(另一条仍定位,
  保留部分定位能力,R1-O3);全部失败时 tables 为空
- AND 前端降级提示手动前往知识库编辑

#### Scenario: 归属校验与空值兜底
- GIVEN 反馈行不存在或无 SQL 内容
- WHEN 定位
- THEN 返回空 tables,归属校验同分页口径

### Requirement: 前端反馈详情弹窗(精修闭环 UI)

反馈详情弹窗(shadcn Dialog)SHALL 含「涉及表」区与「处置」区;无 handle
权限时处置区隐藏(前端按 /auth/me 权限点判定,接口层仍各自守卫)。

#### Scenario: 详情弹窗涉及表区
- GIVEN 反馈详情弹窗
- WHEN 展示涉及表
- THEN 每个表名以标签展示,hasKnowledge=true 的表显示「编辑知识」入口
- AND 点击跳转知识库编辑页(携带 knowledgeId/datasourceKey/schemaName/tableName)

#### Scenario: 详情弹窗处置区
- GIVEN 详情弹窗
- WHEN 展示处置区
- THEN 状态下拉(预填当前状态)+ 处理备注(预填现值)+ 保存按钮
- AND 需 system:feedback:handle 权限,无权限时该区隐藏
