# Fix: 零工具 Bot 三层闸治理(伪调用假应答根治)

## Why

浏览器实测复现(2026-08-28):商店「数据参谋」(tools=[])点击建议问题,
1.5s 返回 200,但应答正文是 ReAct 模型的中间思考文本(「我需要查询…
```tool_code rag_retrieve(…)```」),无数据无结论——用户体感「点了没反应」。
根因是三层能力-契约错位:① agent 内核 ReAct 纪律段无条件教模型调用
rag_retrieve/execute_sql,而注入工具集为空时模型只能以文本伪调用,被
`react_llm` 当作最终答案(success=True 假成功);② api 服务层让必败运行
进入 ReAct,白烧一次 LLM 并产出假应答;③ store 产品层允许零工具 Bot
上架货架,把必然失败的商品摆上架。

## What Changes

- **agent 内核(prompt-能力一致性)**:`build_react_system` 增加
  `has_tools` 显式入参——有工具=现行纪律段逐字不变;无工具=新
  「无工具纪律段」(禁伪调用/禁伪代码块;数据类问题如实说明无法查询,
  不编造;中文直答)。`react_llm` 按注入工具集选择纪律段。
- **api 服务层(运行闸 fail-fast)**:`BotService.run` 求交+剔除需确认
  工具+MCP 补入后,工具集为空 → 直接返回同构失败形态(友好错误文案),
  零 LLM 调用;求交逻辑提取为 `resolve_tools` 公有方法(运行闸与上架闸
  同源单一真理源)。覆盖 store/web_embed/OpenAI(api-key)全部渠道。
- **store 产品层(上架闸)**:`StoreService.publish` 新增 `ToolLister`
  端口,上架前校验求交后工具集非空,空 → 400 拒上架;组合根
  (store/router)以 `BotService.resolve_tools` 装配实现。
- 测试先行:agent 无工具纪律段(纯函数+图级)/api 空 tools run
  fail-fast(断言零 LLM 调用)/publish 空工具 400。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `custom-agent`:ReAct 系统提示纪律段 MUST 与注入工具集一致——工具集
  为空时使用无工具纪律段(禁伪调用、数据类问题如实说明),不得保留
  工具使用教学;有工具分支行为不变。
- `bot-store`:上架 MUST 校验求交后工具集非空(空 → 400 拒上架);
  商店直跑遇求交后空工具集 MUST fail-fast 返回友好错误(不发起 LLM
  调用、不计使用量)。

## Impact

- 代码:`apps/agent/kongming_agent/graph/react/prompts.py`、
  `.../react/nodes.py`;`apps/api/kongming/bot/service.py`、
  `apps/api/kongming/store/service.py`、`apps/api/kongming/store/router.py`。
- 测试:`apps/agent/tests/test_react.py`;`apps/api/tests/test_bot.py`、
  `test_store.py`。
- 零表结构/零端点新增/零 OpenAPI 结构变更(publish 400 为既有端点
  行为收紧);既有公开页 `BotChatOut` 与 OpenAI 端点
  `content = summary or error` 回退链天然兼容 fail-fast 形态,零前端改动。
- 行为收紧(有意):零工具 Bot 的运行从「伪调用假应答」变为「明确报错」;
  纯文本闲聊类 Bot 不在本平台数据助手定位内(见 Non-goals)。

## Non-goals

- 不引入「纯文本闲聊 Bot」产品模式——Bot=数据助手是平台定位;未来若
  有真实需求,另立显式 no-tools 模式契约,不走隐式退化。
- 不改前端:错误文案经既有 error 通道如实到达用户;详情页「未配置
  工具」预提示等 UI 增强不在本次范围。
- 不处置存量数据:已在架的零工具 Bot(store_smoke)由运行闸兜底,
  补配工具或下架由用户决定,不做自动迁移。
- 不动 chat 主链路(8 节点图):其工具集语义与 ReAct 内核不同,另行评估。
