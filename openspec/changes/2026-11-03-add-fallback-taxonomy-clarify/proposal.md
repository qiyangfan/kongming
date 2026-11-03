# Proposal: add-fallback-taxonomy-clarify(兜底轮分类与澄清引导)

## Why

兜底出口有三处真实失真:①复语直通把招呼/致谢轮收敛为 NO_DATA 并以
「当前数据源未找到回答该问题所需的数据…」作答——用户说「你好」得到
数据缺失文案,体验错误;该类轮还计入 no_data 率,质量指标被无取数意图
的输入污染。②歧义问句(缺口径/缺主体限定)不做澄清直接猜 SQL,产出
**静默错误答案**——这是问答链路最坏的失败形态(错得像对的)。③观测面
无法把兜底轮与真实「知识未覆盖」区分开,两类问题的治理动作完全不同
(前者优化入口体验,后者补数据/补文档)。

## What Changes

- **兜底分类诊断字段**:`chat_message` 新增 nullable `fallback_type`
  列,闭合枚举 `CHITCHAT` / `CLARIFY`。仅作管理面诊断与观测切片,
  SHALL NOT 进入用户响应与流式事件;四态 status 语义与映射零变化
  (兜底轮仍走 NO_DATA 中性形态通道)。
- **复语直通应答修正**(nl2sql-agent 既有 Requirement 修订):直通
  命中轮 SHALL 以族别静态中性文案作答(否定终止/应答致谢/招呼三族,
  零模型调用),SHALL NOT 呈现数据缺失文案;标记 CHITCHAT。
- **澄清引导**(新行为):意图路由帧扩展歧义判定(与路由**共用同一
  次模型调用**,零新增每轮调用)——仅高置信命中「缺关键查询口径且
  无法从历史补全」的问句,早退为澄清问句作答(零检索/零生成/零执行);
  判定 fail-open(模型失败/低置信/解析失败→照常进入链路);防循环:
  上一轮已澄清的会话本轮 SHALL NOT 再命中。
- **闲聊语义兜底**(新行为):同一意图路由帧扩展第四意图值,接住
  闭合词表漏网的无取数意图输入(寒暄/致谢/客套,如「太感谢了」
  「在吗」)——高置信命中即早退为中性文案并标记 CHITCHAT,不再烧
  检索/生成/执行全链;判定 fail-open,与词表直通构成两层(精确层
  零成本短路 + 语义层漏网兜底);**不引入**独立预检模型(裁决见
  design D7)。
- **观测切片**(observability 新 Requirement):质量汇总新增兜底分类
  计数,no_data 率口径剔除兜底分类轮;明细分页行携带分类并支持筛选。

## Capabilities

### New Capabilities

(无——三个 spec 变更均挂在既有能力域:agent 图行为归 nl2sql-agent,
用户契约面归 chat,观测面归 observability。)

### Modified Capabilities

- `nl2sql-agent`:MODIFIED「对话复语不进取数补全」(直通轮应答语义:
  族别中性文案 + 兜底标记,零执行断言不变);ADDED「歧义问句澄清
  引导」(判定口径/早退/防循环/fail-open)与「闲聊语义兜底判定」
  (第四意图值/与直通分层/与澄清互斥/fail-open)。
- `chat`:ADDED「兜底轮持久化与呈现」(诊断字段落库、四态映射不变、
  中性呈现与反馈可用)。
- `observability`:ADDED「兜底轮分类观测」(汇总计数、no_data 率口径、
  明细筛选)。

## Impact

- `apps/agent`:route_intent 帧 JSON 扩三字段(第四意图值 chitchat +
  ambiguous/clarify_question,解析纯函数同款扩展)、条件边双早退出口
  (闲聊/澄清)、直通族别文案表与语义路径统一中性文案、state 扩
  fallback 字段;QueryOut 携带 fallback_type。
- `apps/api`:AgentReply/parse_agent_reply 扩诊断字段(形态同
  resolved_question:仅落库不进响应);`AgentClient` 请求载荷扩可选
  last_fallback(防循环判据);`_finalize_reply` 落 fallback_type 列;
  一列迁移(ADR-004);quality 端点扩字段后 `make contracts`(ADR-005)。
  api↔agent 内部通道仅增可选诊断字段,信任边界形态不变(ADR-002)。
- `apps/web`:质量台兜底分类列与筛选;用户对话面零改动(澄清/复语
  应答经既有中性形态通道渲染)。
- 迁移:一列(nullable + CHECK),零既有行为变更;无开关——行为
  修正类变更不设开关(fail-open 已保证判定失效时零回归)。
- 评估:INTENT_ROUTE prompt 变更 prompt 指纹,apply 期重跑 eval
  确认无退化并以新指纹重建基线。

## Non-goals

- **ReAct/Bot 公开对话链路**:澄清与复语直通均为 recipe 图行为,
  ReAct 内核不注入。
- **二次澄清**:防循环规则生效后本轮直接照常链路,不做多轮澄清
  会话状态机。
- **改写型拒答/低质答案的语义检测**:依赖评分系统(独立变更),
  就绪后另行提案,不搭车。
- **闲聊话术池/管理台热配**:文案与词表保持随代码发布(与既有复语
  词表立约一致)。
- **新增 status/verdict 取值**:四态状态机与 verdict 映射零变化,
  分类由独立诊断字段承载。
- **复语词表扩充**:三族闭合枚举不变;词表宽严以既有直通计数观测
  依据另行调优。
- **独立闲聊预检模型**:不引入专用分类/决策模型或编码器小模型做
  闲聊预检——意图路由帧本就每轮运行,搭帧零边际成本;独立模型意味
  着新运行时依赖、训练评估负担与串行延迟(裁决与备选比较见
  design D7)。
