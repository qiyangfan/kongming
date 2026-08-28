# Design: 零工具 Bot 三层闸治理

## Context

实测复现链路:商店直跑(`POST /store/bots/{key}/run`)→ bot service
权限求交后 tools=[] → agent `chat_with_tools` 空 tools 走纯文本分支
(不传 function schema)→ 但 `REACT_DISCIPLINE` 无条件教模型用
rag_retrieve/execute_sql → 模型以文本伪调用(```tool_code``` 块)→
`react_llm` 见 `tool_calls` 为空即视为最终答案 → `finalize`
success=True + summary=中间思考文本 → 前端原样展示。

既有契约约束:
- agent `test_react_empty_tools_degenerates` 已立「空工具集退化纯文本
  问答」——本设计是该契约的强化(prompt 一致性),非推翻;
- `chat_with_tools` 空 tools 分支、`_build_query_out` 终态构造均不动;
- 公开页 `BotChatOut(success, summary, error)` 与 OpenAI 端点
  `content = summary or error` 回退链(R2-F1)对 fail-fast 形态天然兼容。

## Goals / Non-Goals

**Goals:**
- 三层各守其不变量(纵深防御):内核 prompt-能力一致 / 服务层必败
  运行 fail-fast / 产品层货架准入。
- 运行闸与上架闸求交口径单一真理源(一处实现,两处消费)。
- 有工具路径零回归(既有测试全部不动语义)。

**Non-Goals:**
- 不做文本形态 tool_code 的解析执行(ReAct 文本协议回退)——能力
  错位应在契约层消除,而非加解析层兜底伪调用。
- 不引入「纯文本闲聊 Bot」模式;不动 chat 主链路;不动前端。

## Decisions

1. **闸的三层放置(每层对自己的不变量负责)**:
   - 内核层 `build_react_system(bot_system_prompt, *, has_tools)`:
     纪律段按能力选择。备选「内核直接报错拒空 tools」——否决:空工具
     纯文本问答是既有合法契约(闲聊/说明类问题),内核只负责 prompt
     与能力一致,不越权判业务必败性。
   - 服务层 `BotService.run` 求交后 tools 空 → 同构失败形态直返。
     备选「仅靠内核 prompt 修复」——否决:必败运行仍烧一次 LLM 且
     应答内容不可控;fail-fast 给确定性体验与零成本。
     备选「闸放 store router」——否决:web_embed/OpenAI/api-key 渠道
     同样暴露,闸必须在 service 层收口全部渠道。
   - 产品层 `StoreService.publish` 新增 `ToolLister` 端口校验。备选
     「校验放组合根 store/router」——否决:上架准入是 store 域规则,
     按宪法 Article II 归 service;组合根只做装配(与既有
     BotReader/StoreRunner 端口同范式)。
2. **求交逻辑提取 `BotService.resolve_tools(creator_id, tenant_id, row)`**:
   现 run 内联的「权限求交 → 剔除需确认 → MCP 只读补入」提为公有方法,
   store 侧经 `ToolLister` 端口适配调用(域间不直接 import,经端口——
   既有 store/service 端口范式)。闸判定在 MCP 补入之后(MCP-only Bot
   正确放行)。
3. **fail-fast 形态与 AgentUnavailable 分支同构**
   (`success=False, error=文案, summary="", sql="", columns=[],
   rows=[], stats={}, retry_count=0`):消费方零适配;输入护栏仍在
   求交之前执行(安全闸优先于可用性闸,fail-closed 优先级不降)。
4. **无工具纪律段独立常量**(而非删改工具纪律段):有工具路径逐字
   零回归,prompt 资产评审面最小;段内显式点名禁止伪代码/代码块
   (ReAct 先验强,显式禁止比不提更有效)。
5. **`has_tools` 为必选关键字参数**(无默认值):强制调用方显式表态,
   防止未来新调用方漏传退回无工具纪律错配;既有测试随契约更新。

## Risks / Trade-offs

- [行为收紧:零工具 Bot 的非数据类问题(如「你好」)也从伪调用假应答
  变为明确报错] → 可接受:Bot=数据助手是平台定位,伪调用文本对这类
  问题同样是坏应答;错误文案解释性强。未来真有闲聊 Bot 需求,另立
  显式 no-tools 模式契约(Non-goal 不做隐式退化)。
- [上架闸只管新上架,存量在架零工具 Bot 仍在货架] → 运行闸兜底
  (fail-fast 文案明确);存量处置(补配工具/下架)交用户决策。
- [创建者权限事后变化导致在架 Bot 工具集变空] → 运行闸同源求交
  即时生效(与既有「权限回收即失效」语义一致),上架闸只保证上架时点。
- [prompt 常量新增属 prompt 资产变更] → 按 018 资产纪律,test_prompts/
  test_react 增关键标识断言(无工具段标识在位+工具名不得残留+全角)。

## Migration Plan

纯代码行为收紧,无 schema/无端点结构变更:部署即生效,可回滚
(回滚=恢复三处代码,无数据迁移)。测试先行三组失败用例钉住契约。

## Open Questions

(无——存量 store_smoke 的处置为用户决策项,不阻塞本变更。)
