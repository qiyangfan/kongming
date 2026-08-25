## ADDED Requirements

### Requirement: LLM 调用的任务级路由挂接

Agent 面 LLM 客户端 SHALL 在既有 chat/embeddings 接口上纯增任务类型参数(缺省=主模型,既有调用方零改动零回归);各图节点按语义传入任务类型(generate=SQL_GEN、understand=UNDERSTAND、summarize=SUMMARIZE、标题改写=TITLER、表润色=DESCRIBE、ReAct 决策=REACT)。路由判定 SHALL 在 LLM 客户端内单点完成(multi-model-routing spec),节点不感知模型选择细节。多轮上下文/指代消解/生成质量规则/结构化洞察等既有行为契约 SHALL NOT 因路由引入而变化(同一模型配置下行为逐字一致)。

#### Scenario: 节点任务类型全标记

- GIVEN 路由表已配置且注册表多模型可用
- WHEN 任一图节点调用 LLM
- THEN 调用携带该节点的任务类型,LLM 客户端按路由表选定模型

#### Scenario: 单模型配置零回归

- GIVEN 未配置注册表/路由表
- WHEN 任意问答链路执行
- THEN 全部调用走主模型,016 评估基线不降
