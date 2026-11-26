# Spec Delta(conversation-artifacts)

## ADDED Requirements

### Requirement: SQL 卡与代码块的只读展示

SQL 卡 SHALL 默认以只读代码块呈现:等宽、语法高亮、可复制;可编辑控件 SHALL
仅在用户进入编辑态后呈现,编辑态保留再执行与还原能力(再执行行为沿用「SQL 卡
再执行」需求,不因入口层级变化而改变)。回答正文与文档卡中的围栏代码块 SHALL
以语法高亮呈现;无语言标注或未知语言的代码块 SHALL 原样等宽渲染,SHALL NOT
因高亮失败而报错或丢内容。亮暗两种主题下代码高亮 SHALL 保持可读。

#### Scenario: 默认只读展示

- GIVEN 一轮成功问答产生了 SQL 卡
- WHEN 用户展开该卡
- THEN 呈现只读的高亮代码块与复制入口,不出现可编辑控件

#### Scenario: 进入编辑再执行

- GIVEN 用户展开 SQL 卡并选择编辑
- WHEN 修改 SQL 后点击重新执行
- THEN 进入可编辑态并以既有再执行链路产生新消息,原卡不被覆盖
- AND 还原或再次收起后回到只读态

#### Scenario: 未知语言代码块回退

- GIVEN 回答或文档卡中包含未标注语言或未知语言的围栏代码块
- WHEN 渲染该内容
- THEN 以等宽样式原样呈现,内容不丢失、不报错
