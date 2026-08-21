# feedback-analytics 能力域 delta(014)

## MODIFIED Requirements

### Requirement: 反馈统计概览

系统 SHALL 提供 `GET /feedback/overview` 返回统计概览;权限点 `system:feedback:view`
(种子排序 7,仅 SUPER_ADMIN);JWT 认证;统计口径时区固定
(Asia/Shanghai,PG `AT TIME ZONE` 分桶)分桶;datasourceKey 维度按
派生值过滤(会话 datasource_key 快照,与纠错沉淀口径同源)。

#### Scenario: 概览统计口径
- GIVEN SUPER_ADMIN 用户(持有 system:feedback:view)
- WHEN GET /feedback/overview?days=&tenantId=&datasourceKey=
- THEN 返回 { total, upCount, downCount, correctedCount, downRate, trend[] }
- AND days 默认 30、上限 90(超上限按 90 收口);tenantId/datasourceKey 可空(空=不限)
- AND 统计口径:近 days 天内 feedback_time 非空的 assistant 消息
- AND correctedCount = corrected_sql 非空非空串条数
- AND downRate = downCount/total(total=0 时为 0)

#### Scenario: trend 逐天补零
- GIVEN trend 为近 days 天逐天数据
- WHEN 按固定时区分组
- THEN 返回逐天 {day, up, down},无反馈的日期补零
- AND datasourceKey 维度按「会话 datasource_key 快照」派生过滤

### Requirement: 反馈明细分页

系统 SHALL 提供 `GET /feedback/page` 分页返回反馈明细;权限点 `system:feedback:view`;
筛选参数均可空;按 feedback_time 倒序(id DESC 时序 tiebreak,与
会话列表同一惯例);page/size 传输层边界(page≥1,size 1..100 缺省 20)。

#### Scenario: 明细分页筛选
- GIVEN SUPER_ADMIN 用户
- WHEN GET /feedback/page?page=&size=&userId=&rating=&tenantId=&datasourceKey=&feedbackStatus=&onlyCorrected=&startTime=&endTime=
- THEN 返回分页明细,筛选参数均可空
- AND userId 仅数据范围 ALL 时生效,非 ALL 时被强制覆盖为当前用户
- AND 按 feedback_time 倒序

#### Scenario: 明细字段
- GIVEN 每条明细
- THEN 含 messageId/sessionId/用户(昵称)/租户/数据源(派生同概览)/评分/问句(同会话该消息前最近一条 user 问句)/原始 SQL/纠错 SQL/留言/处置状态/反馈时间

#### Scenario: onlyCorrected 仅返回带纠错
- GIVEN onlyCorrected=true
- WHEN 筛选
- THEN 仅返回带纠错 SQL 的反馈(corrected_sql 非空非空串)

### Requirement: 行级可见性

当前用户数据范围非 ALL 时,概览与明细分页 SHALL 强制只返回其本人会话的
反馈(防越权,服务端强制覆盖筛选传值);ALL 按筛选条件查。
数据范围取当前用户角色 data_scope 最宽松档(与 datascope 域同一
最宽松序 ALL>CUSTOM>DEPT_AND_BELOW>DEPT>SELF;本域只判 ALL vs 非 ALL)。

#### Scenario: 非超管强制只见本人反馈
- GIVEN 当前用户数据范围非 ALL
- WHEN 访问任一反馈分析接口
- THEN 强制只返回其本人会话的反馈,覆盖前端筛选传值

#### Scenario: 超管按筛选条件查
- GIVEN 数据范围 ALL
- WHEN 访问接口
- THEN 按筛选条件查

### Requirement: 前端页面

反馈分析页(`/admin/feedback` 第六子页,nav 注册 perm=
system:feedback:view,无权限者菜单不可见+直达 403 品牌页)SHALL 提供统计卡、
趋势折线图、筛选区、明细分页表与详情弹窗;页面只读(处置在详情弹窗内,
见 feedback-refine);无权限用户接口层 403。
图表配色从设计 token 派生(与对话页图表同一纪律);筛选区含评分/租户/
数据源/处置状态/仅看纠错/时间范围;明细分页表含处置状态列。

#### Scenario: 页面只读全功能
- GIVEN SUPER_ADMIN 访问反馈分析页
- THEN 统计卡(总数/赞/踩/差评率/纠错数)+ 趋势折线图 + 筛选区(评分/租户/数据源/处置状态/仅看纠错/时间范围)+ 明细分页表 + 详情弹窗(问句/原 SQL/纠错 SQL/留言对照,SQL 等宽字体展示)

#### Scenario: 页面只读无写操作入口
- GIVEN 反馈分析页
- THEN 不提供任何写操作入口(处置在反馈处置域详情弹窗)

#### Scenario: 无权限用户接口层 403
- GIVEN 无权限用户访问反馈分析接口
- THEN 接口层返回 403
