## ADDED Requirements

### Requirement: SQL 安全引擎降级

SQL 安全管线(防火墙校验、schema 作用域、保留表拒读、行级权限改写、生成侧预检)SHALL 可运行于可插拔解析引擎之上,且行为契约与本域既有 Requirement 完全一致:verdict 三态语义、白名单规则、AST 改写结果 SHALL NOT 因引擎切换而变。引擎不可用(加载失败、版本不匹配、运行期异常)时,系统 SHALL 请求级降级至内置解析路径,服务 SHALL NOT 中断、请求 SHALL NOT 失败;每次降级 SHALL 计入审计日志与可观测计数。管线当前生效的引擎 SHALL 可从审计日志判别。

#### Scenario: 引擎异常时请求无感降级

- GIVEN 管线配置为引擎模式,某请求触发引擎运行期异常
- WHEN 该请求执行安全校验与行级改写
- THEN 请求以与内置路径完全一致的判定语义继续执行,不失败
- AND 审计日志记录一次降级事件,含请求标识与异常类别

#### Scenario: 引擎缺失时启动可用

- GIVEN 部署环境缺少引擎依赖或版本不匹配
- WHEN api 或 agent 进程启动并接收查询请求
- THEN 管线以内置解析路径正常工作,启动 SHALL NOT 因引擎缺失而失败
- AND 审计日志反映管线实际运行于内置路径

#### Scenario: 深嵌套 SQL 受控拒绝

- GIVEN 管线运行于引擎模式,且引擎对解析深度设有可配置上限
- WHEN 一条超限深嵌套 SQL 进入管线
- THEN 管线返回 ERROR verdict(受控拒绝),SHALL NOT 以栈溢出或递归异常形态失败
