# multi-model-routing spec delta(075-model-registry-pricing-removal)

## MODIFIED Requirements

### Requirement: 多模型注册表

系统 SHALL 支持经环境变量注册多个 LLM 模型:每个模型条目 MUST 含唯一名称、base_url、api_key(仅 Agent 面进程内存,不落库不落日志)、能力标签(code/reasoning/chat/vision 子集);并 SHALL 支持可选 `protocol` 字段声明出站协议,取值域 `openai`(OpenAI Chat Completions,缺省)| `anthropic`(Anthropic Messages)| `responses`(OpenAI Responses)。注册表为空或未配置时 SHALL 回退既有单模型配置(llm_*),行为与引入前一致。注册表条目畸形(缺字段/重名/protocol 非法)SHALL 启动 fail-closed 拒配(告警并剔除该条目,不裸崩;主模型 protocol 非法 SHALL 启动告警并回退 openai,不拒启)。主模型 SHALL 经环境变量声明协议(缺省 openai),语义与条目字段一致。条目中的未知键 SHALL 被忽略,不因未知键拒配。

**上下文窗口(080)**:条目 SHALL 支持可选 `context_window` 声明该模型的上下文窗口规模,作为生成提示词装配预算的输入;未声明时 SHALL 回退全局缺省窗口值(可配)。该字段 SHALL NOT 出现在任何对外响应或管理面输出中(与 api_key 同为进程内配置),SHALL NOT 影响出站协议与路由判定语义。窗口值非正数等畸形形态 SHALL 按"未声明"处理并告警,SHALL NOT 拒配整条目(窗口只用于预算收敛,不构成安全边界)。

#### Scenario: 空注册表零回归

- GIVEN 未配置模型注册表
- WHEN Agent 面启动并处理问答
- THEN 全部 LLM 调用走既有 llm_* 单模型配置,行为与 021 之前一致

#### Scenario: 多模型注册生效

- GIVEN 注册表配置了 qwen-plus(主)与 deepseek-coder(code 标签)
- WHEN Agent 面启动
- THEN 两个模型可用,按路由表分发调用

#### Scenario: 畸形条目拒配

- GIVEN 注册表某条目缺 base_url
- WHEN Agent 面启动
- THEN 该条目被拒(告警),其余条目与主模型可用

#### Scenario: 未知键不拒配

- GIVEN 注册表条目携带契约外的额外键(如历史遗留的计价字段)
- WHEN Agent 面启动
- THEN 该条目正常生效,额外键被忽略,不告警不拒配

#### Scenario: 缺省 openai 零回归

- GIVEN 注册表条目未声明 protocol(或显式 openai)
- WHEN Agent 面启动并处理问答
- THEN 该条目出站行为与引入前一致(Chat Completions 通道)

#### Scenario: 畸形 protocol 单条目剔除

- GIVEN 注册表条目 protocol 值不在取值域内
- WHEN Agent 面启动
- THEN 该条目被剔除(告警),其余条目与主模型可用(不裸崩)

#### Scenario: 窗口缺省回退

- GIVEN 注册表条目未声明 `context_window`
- WHEN 生成环节做装配预算
- THEN 按全局缺省窗口计算上界,不因缺字段而报错或跳过预算

#### Scenario: 畸形窗口不拒配

- GIVEN 某条目 `context_window` 为 0 或非数字
- WHEN Agent 面启动
- THEN 该条目仍可用(窗口按缺省处理并告警),SHALL NOT 被整条剔除

#### Scenario: 窗口不外泄

- GIVEN 已配置带窗口的注册表
- WHEN 查询用量或任何对外端点
- THEN 响应不含 `context_window` 与模型侧配置

> 备注(并行变更协调):本块为 075-model-registry-pricing-removal(删计价)
> 与 080-prompt-assembly-budget(增上下文窗口)对「多模型注册表」的
> **合并终态**。openspec MODIFIED 为整块替换,两变更的本块文本刻意保持
> 逐字一致——无论谁后 sync 进 `specs/multi-model-routing/`,结果都是
> 「无计价 + 有 context_window」,不会互相覆盖丢失。
