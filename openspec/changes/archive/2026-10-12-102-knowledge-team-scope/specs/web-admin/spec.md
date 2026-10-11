# Spec Delta

## MODIFIED Requirements

### Requirement: 知识库管理页

知识列表(列:数据源/schema/表名/说明摘要/来源标记/更新时间/**归属
[公共/团队名]**)按当前租户上下文拉取,支持数据源筛选与分页;上传经文件
选择(md/markdown/txt/pdf/docx)+ 数据源/schema 提交;删除为显式确认;页内
SHALL 提供「扫描元数据」与「重建索引」操作(需数据源选择,重建索引为
破坏性显式确认,完成后展示 {deleted, rebuilt, enriched} 结果);进行中
操作 SHALL 有 loading 态与错误反馈。**列表 SHALL 支持归属筛选(公共/
我所在的团队);上传表单 SHALL 支持选择归属(默认公共,可选我作为
OWNER/MEMBER 的团队);归属变更 SHALL 走受控确认(AlertDialog 明示变更后
可见范围变化),非成员团队不出现在可选集。**

#### Scenario: 上传闭环
- GIVEN 管理员在知识库页
- WHEN 选择 Markdown 文件并选择目标数据源/schema 后提交
- THEN 列表刷新可见新条目(来源=上传),错误时表单上方展示端点错误信息

#### Scenario: 扫描元数据触发与反馈
- GIVEN 管理员选择已启用的数据源
- WHEN 点击「扫描元数据」
- THEN 展示进度态,完成后刷新列表并展示发现/润色表数

#### Scenario: 重建索引受控确认
- GIVEN 管理员选择数据源
- WHEN 点击「重建索引」
- THEN 弹出确认对话框(明示将清空该数据源全部表说明后重建),确认后执行,
完成后展示 {deleted, rebuilt, enriched};取消则不动

#### Scenario: 上传选择团队归属

- GIVEN 管理员是团队 T 的 MEMBER
- WHEN 上传表单归属选择「团队 T」提交
- THEN 新条目归属 T,列表归属列显示团队名;非 T 成员的知识列表不含该条目

#### Scenario: 归属变更受控确认

- GIVEN 管理员对公共条目执行「转为团队专属」
- WHEN 确认对话框确认
- THEN 归属变更生效(列表刷新可见),非成员后续召回不再命中;取消则不动

#### Scenario: 归属筛选

- GIVEN 列表含公共条目与团队 T 条目
- WHEN 管理员按「团队 T」筛选
- THEN 仅呈现 T 条目;按「公共」筛选仅呈现公共条目
