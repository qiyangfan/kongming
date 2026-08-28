## Purpose

零工具 Bot 伪调用假应答根治——产品层与服务层契约:求交后工具集为空
的 Bot 不得上架货架(必败商品不摆上架);直跑遇空工具集 fail-fast
返回友好错误,不发起 LLM 调用、不计使用量。

## ADDED Requirements

### Requirement: 零工具 Bot 上架与直跑闸

系统 SHALL 在上架与直跑两处校验 Bot 求交后(创建者权限交集、剔除需
确认写工具、MCP 只读补入后的)实际可用工具集,校验口径 MUST 同源:

- 上架:求交后工具集为空的 Bot MUST 拒绝上架(400 并返回原因),
  货架不得出现必然失败的运行;
- 直跑(POST /store/bots/{key}/run 及 web_embed/OpenAI/api-key 等全部
  Bot 渠道):求交后工具集为空时 MUST fail-fast 返回结构化失败
  (success=false 与友好错误文案),MUST NOT 发起 LLM 调用,
  MUST NOT 使使用量计数自增;
- 工具集求交口径(权限交集/需确认剔除/MCP 补入)MUST 为单一实现,
  上架闸与运行闸共用。

#### Scenario: 空工具 Bot 上架被拒

- **GIVEN** 启用且归属个人的 Bot,求交后工具集为空
- **WHEN** 归属者上架
- **THEN** 拒绝(400)并返回「未配置可用工具」原因,Bot 保持未上架

#### Scenario: 有工具 Bot 上架不受影响

- **WHEN** 求交后工具集非空的 Bot 上架
- **THEN** 既有上架流程(归属/护栏/可见性)行为不变

#### Scenario: 空工具 Bot 直跑 fail-fast

- **GIVEN** 已上架 Bot 求交后工具集为空(含存量在架)
- **WHEN** 商店直跑或任意 Bot 渠道运行
- **THEN** 立即返回 success=false 与友好错误文案,不发起 LLM 调用
- **AND** 使用量计数不变
