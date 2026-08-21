# chat 能力域 delta(015)

## MODIFIED Requirements

### Requirement: 结果导出 CSV/Excel

查询结果支持导出 CSV(UTF-8 with BOM,Excel 直接打开不乱码)与 Excel(xlsx,表头加粗+自动列宽),导出行数截断保护。导出端点 SHALL 复用与问答端点一致的鉴权约束:JWT 认证 + 权限点 `chat:execute` + X-Tenant-Id 租户白名单校验。导出 SHALL 以请求方已查询到的结果集(columns+rows)为输入,不重新执行 SQL——所见即所导,数据隔离在查询时已由 data_scope 保证,导出本身不产生新的数据访问。导出 SHALL 落操作日志 `chat.export`(与业务同事务;detail 含 fmt/rows/truncated;010 挂账⑦,015 清零)——数据外发可见性;审计写失败 SHALL NOT 阻断导出(告警日志,audit spec 铁律)。

#### Scenario: 导出 CSV
- GIVEN 查询结果有 columns+rows
- WHEN 点击「CSV」导出
- THEN 生成 UTF-8 with BOM 的 CSV,浏览器下载「查询结果-<时间戳>.csv」

#### Scenario: 导出 Excel
- GIVEN 查询结果有 columns+rows
- WHEN 点击「Excel」导出
- THEN 生成 xlsx(表头加粗+自动列宽),浏览器下载

#### Scenario: 导出不重跑 SQL
- GIVEN 某轮问答已返回结果集
- WHEN 对该结果集发起导出
- THEN 服务端仅做格式转换,不向业务库发起任何新查询
- AND 请求方无法借导出通道获取其未查询过的数据

#### Scenario: 导出行数上限保护
- GIVEN 结果集超出上限(默认 10000 行)
- WHEN 导出
- THEN 截断到上限,防止服务端内存失控

#### Scenario: 导出格式非法拒绝
- GIVEN 导出请求 format 非 csv/xlsx
- WHEN 提交
- THEN 返回参数错误,不生成文件

#### Scenario: 无权限导出 403
- GIVEN 已登录用户角色不含 chat:execute
- WHEN 请求导出端点
- THEN 返回 403

#### Scenario: 导出落操作日志
- GIVEN 用户执行导出
- WHEN 导出完成
- THEN sys_oper_log 含 chat.export 动作,detail 含 fmt/rows/truncated,与业务同事务
- AND 审计写失败不阻断导出(文件照常返回)
