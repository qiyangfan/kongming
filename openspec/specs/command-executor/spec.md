# command-executor Specification

## Purpose

孔明的运行时命令执行器：消费锦官跨服务命令队列中的 reindex、清缓存、取消任务等有限命令，幂等执行并回执；本地运行命令独立执行，不进入控制面队列；不接受任意 SQL。

## Requirements

### Requirement: 跨服务命令执行与回执

kongming SHALL 以服务身份消费锦官命令队列，执行有限动作并回执；命令 SHALL 带幂等键、action/params schema/version；服务 SHALL 按 X-01 schema 校验，未知 action/版本或参数不合法 SHALL 拒绝并 ack failed；重复命令 SHALL 只执行一次；失败 SHALL 回执 failed；SHALL NOT 接受任意 SQL/脚本；本地运行命令 SHALL 由本地管理台直接执行，控制面缺席仍可用。

#### Scenario: 执行 reindex

- WHEN claim reindex 命令
- THEN 执行并 ack 结果

#### Scenario: 本地命令

- GIVEN 锦官不可用
- WHEN 运维在本地台执行 reindex
- THEN 本地执行并审计，不依赖命令队列

#### Scenario: 拒绝任意 SQL

- WHEN 收到 SQL 命令
- THEN 拒绝，ack failed

#### Scenario: 未知 action/参数

- WHEN 收到未注册 action/版本或参数不合法
- THEN 拒绝，ack failed，不执行

### Requirement: 幂等、认证与审计

同幂等键命令 SHALL 只执行一次；执行/结果 SHALL 审计；命令 claim SHALL 认证服务身份，匿名 SHALL 拒绝；命令执行 SHALL NOT 绕过服务权限与不变量；锦官 SHALL 收到最终状态。

#### Scenario: 重复命令

- WHEN 同幂等键再次到达
- THEN 返回既有结果，不重复执行

#### Scenario: 匿名 claim

- WHEN 无服务身份 claim
- THEN 401/403，不执行

#### Scenario: 审计

- WHEN 命令完成
- THEN 本地审计有 actor/命令/结果
