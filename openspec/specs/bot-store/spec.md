## Purpose

Bot 商店:Bot 经显式上架进入租户内商店供发现与运行,配套搜索/排序/
详情发现体验与收藏/评分/使用量互动;上架(归属者主动分发)与资源
归属(团队/个人)为正交维度。

## Requirements

### Requirement: Bot 上架与下架治理

系统 SHALL 支持 Bot 显式上架/下架:**上架 SHALL 以当前定义创建不可变版本
快照(名称/问候语/建议问题/系统提示/工具集)——version_no 在 Bot 内递增,
快照创建后 MUST NOT 被任何后续编辑修改;经护栏审核通过后该版本成为「当前
上架版本」,商店列表与直跑一律消费当前上架版本快照(草稿定义与货架分离,
定义编辑不影响在架版本)**。快照内容 MUST 经护栏审核(BLOCK 拒绝上架并
返回原因);商店列表 MUST 只含上架态且按 tenant_id 隔离;下架即时从商店
不可见(Bot 定义与公开页/api-key 渠道不受影响);新建 Bot 默认未上架。
**定义与当前上架版本内容一致时的再次上架 SHALL 幂等复用该版本(不产生
重复快照),审计记录「内容无变化」;内容有变更时创建新版本并重新过护栏。
零工具闸(求交后工具集为空拒绝上架)在版本快照的工具集上执行,口径与
直跑闸单源不变。**

#### Scenario: 归属者上架进入商店

- GIVEN 归属个人的未上架 Bot(启用)
- WHEN 创建者上架(快照过护栏)
- THEN 创建 v1 版本快照并成为当前上架版本,商店可见可运行(消费快照)

#### Scenario: 草稿编辑不影响在架版本

- GIVEN 已上架 Bot(当前版本 v2)
- WHEN 创建者编辑定义的问候语为未完成文案(不重新上架)
- THEN 商店展示与直跑仍为 v2 内容;差异提示在创建者面可见

#### Scenario: 重新上架创建新版本

- GIVEN 已上架 Bot(v2),定义已修改
- WHEN 创建者重新上架(快照过护栏)
- THEN 创建 v3 并切换当前上架版本,商店即时呈现 v3;v2 保留在版本历史

#### Scenario: 内容无变化幂等复用

- GIVEN 已上架 Bot(v2),定义与 v2 快照一致
- WHEN 创建者再次上架
- THEN 不创建新版本,当前上架版本仍为 v2,审计记录内容无变化

#### Scenario: 非归属者上架被拒

- GIVEN 用户 B 非创建者、非归属团队 OWNER
- WHEN B 尝试上架
- THEN 拒绝(403),上架状态与版本指针不变

#### Scenario: 违规内容拦截

- GIVEN Bot 定义含护栏 BLOCK 规则命中内容
- WHEN 上架(创建快照后审核)
- THEN 拒绝并返回审核失败原因,快照不成为上架版本、商店不可见

#### Scenario: 未上架不出现在商店

- GIVEN 用户 A 的个人 Bot(未上架)
- WHEN 任何人(含 A)查看商店列表
- THEN 不可见;直访商店详情 404 同形

#### Scenario: 下架即时生效

- GIVEN 已上架 Bot
- WHEN 下架
- THEN 商店列表与收藏列表即时不含该 Bot;定义、版本历史与既有渠道不受影响

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

### Requirement: 版本历史与回滚

系统 SHALL 保留 Bot 的上架版本历史:每版本含版本号、快照内容与上架时间,
历史查询限归属者与商店治理同权限口径(创建者或归属团队 OWNER);版本数
SHALL 受封顶上限约束(超出时淘汰最旧的非当前版本,当前上架版本 MUST NOT
被淘汰)。回滚 SHALL 为「将历史版本重新指为当前上架版本」:回滚视同上架
动作,快照内容 MUST 按当前护栏规则重新审核(通过才切换,BLOCK 拒绝且指针
不变);下架态 Bot 的回滚仅恢复指针、不改变下架状态。商店详情 SHALL 展示
当前上架版本号与上架时间。

#### Scenario: 版本历史可查

- GIVEN 已上架 Bot 经历 v1→v2→v3 三次上架
- WHEN 归属者查询版本历史
- THEN 三个版本均可见(版本号/上架时间),当前版本标识明确

#### Scenario: 回滚切换当前版本

- GIVEN 当前版本 v3 有问题,v2 在历史中
- WHEN 归属者回滚到 v2(审核通过)
- THEN 当前上架版本切回 v2,商店与直跑即时消费 v2;v3 保留在历史

#### Scenario: 回滚过当前护栏

- GIVEN 历史版本 v1 的内容被现行护栏规则判为 BLOCK
- WHEN 归属者回滚到 v1
- THEN 拒绝并返回原因,当前上架版本指针不变

#### Scenario: 版本封顶淘汰

- GIVEN 版本历史上限 10,当前版本 v12(v2–v11 为历史)
- WHEN 创建 v13
- THEN 最旧的历史版本(v2)被淘汰,v3–v12 与当前版本保留

#### Scenario: 存量在架零回归

- GIVEN 迁移前已在架的 Bot(无版本概念)
- WHEN 迁移完成
- THEN 自动补 v1 快照(=迁移时定义)且指针指向,商店展示与直跑行为
  与迁移前逐字段一致
