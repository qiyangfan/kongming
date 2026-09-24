## ADDED Requirements

### Requirement: 多步 Todo 账本

ReAct 运行在多子目标场景 SHALL 维护本轮 Todo 账本:每条含目标、状态
(PENDING/DONE/FAILED/SKIPPED)、证据引用与失败/跳过原因。单目标简单问
SHALL 可不建账本,行为与引入前一致。账本状态迁移 SHALL 由工具执行结果
驱动:工具成功且产出结果方可置 DONE 并写证据;工具失败、校验拒绝、空
结果或能力不适用 SHALL 置 FAILED 或 SKIPPED 并填原因,SHALL NOT
置 DONE。语言模型自述不构成完成证据。

#### Scenario: 有证据才完成

- GIVEN 账本含「查华东销售额」且对应查询工具成功返回
- WHEN 工具结果回写
- THEN 该条置 DONE 且 evidence 指向本次工具调用
- AND 无工具成功的条目不得为 DONE

#### Scenario: 失败不得标完成

- GIVEN 某子目标工具返回能力不适用
- WHEN 回写账本
- THEN 该条为 FAILED 或 SKIPPED 并带原因
- AND 不出现在已完成集合

### Requirement: 预算退出诚实

运行因步数、Token 或超时预算耗尽而停止时,未完成账目 SHALL 保持
PENDING/FAILED/SKIPPED 原状,SHALL NOT 被改写为 DONE。收口 SHALL 以
部分完成语义披露剩余目标。

#### Scenario: 预算耗尽不谎称完成

- GIVEN 三条目标完成两条后触发预算退出
- WHEN 生成收口文本
- THEN 第三条保持未完成
- AND 文本不得声称全部完成,并披露未完成目标

### Requirement: 收口与账本一致

存在非 DONE 账目时,收口文本 SHALL 逐项如实说明未完成或失败原因,
SHALL NOT 使用「已全部完成/所有问题均已回答」等全称完成措辞。全部
DONE 时收口正常交付各目标结论。无账本(单目标)时收口行为与引入前
逐字一致。

#### Scenario: 未完成清单披露

- GIVEN 账本一 DONE、一 FAILED
- WHEN 生成收口
- THEN DONE 项给出结论,FAILED 项说明未完成原因
- AND 文本不含全称完成断言

#### Scenario: 全部完成正常收口

- GIVEN 账本全部 DONE
- WHEN 生成收口
- THEN 正常综合各结论,不强制附失败清单

#### Scenario: 无账本零回归

- GIVEN 单目标问答且未建账本
- WHEN 生成收口
- THEN 行为与引入前逐字一致
