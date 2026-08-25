## MODIFIED Requirements

### Requirement: 收藏

系统 SHALL 支持收藏/取消收藏(bot:run):一人一 Bot 至多一条(唯一
约束,重复操作幂等);**收藏切换 MUST 原子完成(单语句语义),
并发重复切换下响应布尔 MUST 等于真实终态**(不允许先查后写窗口
导致的瞬时不符);「我的收藏」列表 SHALL 仅含当前在架 Bot(下架
即移出,重新上架自动回归)。

#### Scenario: 收藏幂等

- GIVEN 已收藏 Bot X
- WHEN 再次收藏/取消后再次取消
- THEN 状态不重复不报错(幂等)

#### Scenario: 并发切换响应即终态

- GIVEN 对同一 Bot 的并发收藏切换
- WHEN 任一请求返回
- THEN 其响应布尔与数据库最终状态一致(唯一约束兜底,无竞态窗口)

#### Scenario: 收藏列表仅在架

- GIVEN 收藏 X 后 X 被下架
- WHEN 查看我的收藏
- THEN X 不在列表中
