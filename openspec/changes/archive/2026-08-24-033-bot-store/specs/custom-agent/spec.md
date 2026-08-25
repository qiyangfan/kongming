## ADDED Requirements

### Requirement: Bot 上架列与商店渠道

Bot 定义 SHALL 纯增上架字段(published_at/published_by/greeting/
suggestions);未上架 Bot MUST 不出现在商店任何列表(含创建者视角);
上架后 Bot 运行渠道枚举 SHALL 纯增 "store"(商店直跑溯源)。

#### Scenario: 新建默认未上架

- GIVEN 新建 Bot
- WHEN 查看商店列表
- THEN 不出现(默认未上架)

#### Scenario: 商店直跑溯源

- GIVEN 上架 Bot
- WHEN 经商店直跑
- THEN 运行经 025 链路,审计 channel="store"
