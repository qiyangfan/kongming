## Purpose

Bot 商店:Bot 经显式上架进入租户内商店供发现与运行,配套搜索/排序/
详情发现体验与收藏/评分/使用量互动;上架(归属者主动分发)与资源
归属(团队/个人)为正交维度。

## Requirements

### Requirement: Bot 上架与下架治理

系统 SHALL 支持 Bot 显式上架/下架:上架 MUST 限创建者或归属团队
OWNER(fail-closed,032 can_manage 同判);上架内容(name/greeting/
suggestions)MUST 经护栏审核(BLOCK 拒绝上架并返回原因);商店列表
MUST 只含上架态且按 tenant_id 隔离;下架即时从商店不可见(Bot 定义
与公开页/api-key 渠道不受影响);新建 Bot 默认未上架。

#### Scenario: 归属者上架进入商店

- GIVEN 归属个人的未上架 Bot(启用)
- WHEN 创建者上架(内容过护栏)
- THEN 出现在租户商店列表,可被其他成员发现与运行

#### Scenario: 非归属者上架被拒

- GIVEN 用户 B 非创建者、非归属团队 OWNER
- WHEN B 尝试上架
- THEN 拒绝(403),上架状态不变

#### Scenario: 违规内容拦截

- GIVEN Bot 的 greeting 含护栏 BLOCK 规则命中内容
- WHEN 上架
- THEN 拒绝并返回审核失败原因,保持未上架

#### Scenario: 未上架不出现在商店

- GIVEN 用户 A 的个人 Bot(未上架)
- WHEN 任何人(含 A)查看商店列表
- THEN 不可见;直访商店详情 404 同形

#### Scenario: 下架即时生效

- GIVEN 已上架 Bot
- WHEN 下架
- THEN 商店列表与收藏列表即时不含该 Bot;定义与既有渠道不受影响

### Requirement: 商店发现

系统 SHALL 提供商店列表(关键词搜索[name/bot_key/system_prompt 命中,
大小写不敏感]+排序 hot=使用量降序/new=上架时间降序/rating=均分
降序)与详情(greeting/suggestions/工具集/评分均分与评分数/使用量/
收藏数);读取面要求 bot:run 权限点;全部按 tenant_id 隔离。

#### Scenario: 关键词搜索

- GIVEN 商店内有 name 含「销售」的上架 Bot
- WHEN 搜索「销售」
- THEN 返回命中项(名称/标识/系统提示任意一处)

#### Scenario: 热门排序

- GIVEN 多个上架 Bot 使用量不同
- WHEN 选 hot 排序
- THEN 按使用量降序

#### Scenario: 详情对未上架不可达

- GIVEN 已下架或从未上架的 Bot
- WHEN 直访商店详情
- THEN 404 同形,不暴露存在性

### Requirement: 收藏

系统 SHALL 支持收藏/取消收藏(bot:run):一人一 Bot 至多一条(唯一
约束,重复操作幂等);「我的收藏」列表 SHALL 仅含当前在架 Bot(下架
即移出,重新上架自动回归)。

#### Scenario: 收藏幂等

- GIVEN 已收藏 Bot X
- WHEN 再次收藏/取消后再次取消
- THEN 状态不重复不报错(幂等)

#### Scenario: 收藏列表仅在架

- GIVEN 收藏 X 后 X 被下架
- WHEN 查看我的收藏
- THEN X 不在列表中

### Requirement: 评分

系统 SHALL 支持 1-5 整数星评分(bot:run):一人一 Bot 一条(唯一
约束,重复提交为改分 upsert);非法分值(0/6/非整数/非数字)MUST
400 拒绝;列表与详情展示均分与评分数。

#### Scenario: 改分 upsert

- GIVEN 已评 5 星
- WHEN 改评 3 星
- THEN 记录更新为 3 星,评分数不变

#### Scenario: 非法分值拒绝

- GIVEN 任意用户
- WHEN 提交 0/6/4.5/"abc"
- THEN 参数校验拒绝(传输层 422 口径),记录不变

### Requirement: 使用量计数与商店直跑

系统 SHALL 在商店直跑路径(POST /store/bots/{key}/run)经 025 运行
链路执行(创建者身份,channel="store" 审计溯源),成功运行 MUST 使
使用量服务端自增 1;计数 MUST 仅服务端维护(无客户端上报通道),
作为 hot 排序依据并在详情展示。

#### Scenario: 直跑计数自增

- GIVEN 上架 Bot 使用量 N
- WHEN 商店直跑一次成功
- THEN 使用量 N+1,热门排序随动

#### Scenario: 客户端不可伪造

- GIVEN 任意客户端
- WHEN 尝试提交使用量数据
- THEN 无此端点/参数,伪造无效
