# feedback-refine 能力域

## Purpose

反馈处置闭环:反馈处置状态(feedback_status/handler/handle_time/handle_note)支撑 PENDING→ADOPTED/REJECTED/DISMISSED 流转,处置写接口与前端详情弹窗留痕,SQL 表名抽取定位反馈涉及表,打通「用户反馈 → 知识精修」运维链路。

状态机:`NONE(未反馈) → PENDING(已提交待处置) → ADOPTED(采纳,纠错已沉淀)/ REJECTED(驳回)/ DISMISSED(忽略)`。

## Requirements

### Requirement: 反馈处置写接口

`PUT /feedback/{messageId}/status` 权限点 system:feedback:handle(仅 SUPER_ADMIN);归属校验同反馈分页口径;status 仅枚举值可写;note 处理语义与提交接口一致。

#### Scenario: 处置反馈状态
- GIVEN 用户持有权限点 system:feedback:handle
- WHEN PUT /feedback/{messageId}/status({ status, note })
- THEN status 仅枚举 PENDING/ADOPTED/REJECTED/DISMISSED 可写,非法值返回 400
- AND 落库:feedback_status、feedback_handler=当前用户、feedback_handle_time=now、feedback_handle_note
- AND 操作记操作日志

#### Scenario: 归属校验防越权处置
- GIVEN 处置人数据范围非 ALL
- WHEN 处置
- THEN 强制校验反馈归属(同分页口径),防越权处置他人反馈
- AND 数据范围 ALL(超管)可处置全部

#### Scenario: note 处理语义
- GIVEN note 参数
- THEN null=保持现值(允许只改状态),显式空串=清空,非空=覆盖(与提交接口 comment 语义一致)

### Requirement: 反馈分页加处置状态筛选

反馈明细分页接口新增 feedbackStatus 入参;前端列表展示「处置状态」列与下拉筛选。

#### Scenario: 分页按处置状态筛选
- GIVEN 反馈明细分页接口
- WHEN 携带可空的 feedbackStatus 参数
- THEN 按 feedback_status 筛选

#### Scenario: 前端筛选与状态列
- GIVEN 反馈管理页
- THEN 提供「处置状态」下拉(全部/PENDING/ADOPTED/REJECTED/DISMISSED)
- AND 列表含「处置状态」列(不同状态不同视觉标签)

### Requirement: 反馈涉及表定位(精修闭环桥梁)

`GET /feedback/{messageId}/involved-tables` 权限点 system:feedback:view;从反馈行的 sqlText + correctedSql 用 SQL 解析器抽取表名并匹配知识库条目;解析失败兜底返回空集合。

#### Scenario: 定位反馈涉及表
- GIVEN 用户持有 system:feedback:view
- WHEN GET /feedback/{messageId}/involved-tables
- THEN 返回 { tables: [{ tableName, schemaName, datasourceKey, hasKnowledge, knowledgeId }] }
- AND 从原始 SQL 与纠错 SQL 合并抽取表名(去重,大小写不敏感)
- AND 按 tenant + datasource_key + table_name 匹配知识库条目
- AND hasKnowledge 标识是否匹配到,knowledgeId 供前端跳转编辑

#### Scenario: 解析失败兜底
- GIVEN SQL 解析抛异常
- WHEN 定位
- THEN 返回 { tables: [], parseError: true },前端降级提示手动前往知识库编辑

#### Scenario: 归属校验与空值兜底
- GIVEN 反馈行不存在或无 SQL 内容
- WHEN 定位
- THEN 返回空 tables,归属校验同分页口径

### Requirement: 前端反馈详情弹窗(精修闭环 UI)

反馈详情弹窗含「涉及表」区与「处置」区;无 handle 权限时处置区隐藏。

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
