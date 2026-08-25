## Purpose

把 assistant 响应升级为类型化可交互产物(SQL 可编辑再执行、图表调参重绘、答案为文档卡),持久化随消息回显,再生成/再编辑产生新消息不覆盖原产物。

## Requirements

### Requirement: artifact 类型化抽象

assistant 消息的 result_json SHALL 纯增 artifacts 数组:每项含 type(∈ sql/chart/document)、content(主内容)、metadata(辅助信息,如图表配置/SQL 数据源)。控制面 SHALL 从既有要素确定性派生:sql 非空→sql 卡;chart.type≠none→chart 卡(含完整 chart 配置);summary 非空→document 卡。无产物时 SHALL 输出空数组(向后兼容,老前端不受影响);派生逻辑对同步/流式/历史回显 SHALL 同源(读档重算兜底与既有形态一致)。

#### Scenario: 全要素派生

- GIVEN 一轮成功问答(SQL+图表+摘要)
- WHEN 响应组装
- THEN artifacts 含 sql/chart/document 三卡,内容逐字段对应既有要素

#### Scenario: 无产物零回归

- GIVEN REJECTED/FAILED 轮(无 SQL 无结果)
- WHEN 组装
- THEN artifacts 为空数组,既有分态文案不变

### Requirement: SQL 卡再执行

系统 SHALL 提供 SQL 再执行端点 POST /chat/artifacts/rerun:请求携带消息 id 与编辑后 SQL;SHALL 先校验消息归属(当前用户+租户,404 同形);SQL SHALL 经六层防御全量(防火墙/行级权限/只读池,与问答执行同构);执行结果 SHALL 作为新 assistant 消息落库(不覆盖原 artifact)并在响应返回;写操作/保留表 SHALL 拦截(REJECTED 形态)。

#### Scenario: 编辑后再执行

- GIVEN 用户把 SQL 的 LIMIT 50 改为 20
- WHEN 点「重新执行」
- THEN 防御全量过后执行,结果作为新消息出现在会话尾部

#### Scenario: 写操作拦截

- GIVEN 编辑后 SQL 为 DELETE
- WHEN 提交再执行
- THEN 防火墙拦截(REJECTED),不执行,不落新结果消息

#### Scenario: 他人消息不可再执行

- GIVEN 用户 B 持有用户 A 的消息 id
- WHEN 提交再执行
- THEN 404 同形,不执行

### Requirement: 类型化渲染器(web)

前端 SHALL 渲染:sql 卡(可编辑代码框+「重新执行」按钮)+ chart 卡(调参面板:标题/轴名/类型,前端重绘不调 LLM)+ document 卡(Markdown 渲染+复制)。历史回显 SHALL 完整渲染 artifacts;无 artifacts 的历史消息按既有形态渲染(兼容)。

#### Scenario: 图表调参重绘

- GIVEN chart 卡已渲染
- WHEN 用户改标题或切换类型
- THEN 前端即时重绘,不发起任何 LLM/查询调用

### Requirement: artifact 内容安全

再执行响应的自然语言产出 SHALL 经 020 输出护栏;artifact 内容(SQL/配置)SHALL 经服务端复检(再执行路径的防火墙),前端渲染 SHALL NOT 信任 artifact 注入可执行 HTML(Markdown 渲染不解析原始 HTML)。

#### Scenario: 再执行响应过护栏

- GIVEN 再执行结果的摘要含凭证形态
- WHEN 响应返回
- THEN 脱敏后返回并落护栏审计
