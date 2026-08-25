# 033 bot-store:Bot 商店

## Why

025 的 Bot 体系让创建者能造助手,但租户内没有「发现与分发」面——别人
的 Bot 只有公开链接/api-key 两条窄路可达。本变更补商店层:显式上架
进入租户商店(发现/搜索/排序/详情),收藏/评分/使用量三件互动,
商店直跑(上游 bot-store 域的 Kongming 适配)。上架与 032 归属正交:
上架是归属者的主动分发,未上架个人 Bot 不出现在任何列表。

## What Changes

- **上架治理**:sys_bot_definition 纯增 published_at/published_by/
  greeting/suggestions(JSONB)列(0021);POST /system/bots/{id}/
  publish|unpublish——仅创建者或归属团队 OWNER(032 can_manage 同判,
  fail-closed);上架内容(name/greeting/suggestions)经 020 输出护栏
  审核(BLOCK 拒上架并回原因);下架即时从商店消失(定义与既有
  渠道[公开页/api-key]不受影响);新建 Bot 默认未上架(Kongming
  无存量商店,不做存量迁移——货架从空开始,语义直白)。
- **商店发现**:GET /store/bots(关键词搜索[name/bot_key/system_prompt
  ILIKE,大小写不敏感]+排序 hot=使用量降序/new=上架时间降序/
  rating=均分降序)+GET /store/bots/{key} 详情(greeting/suggestions/
  工具集/评分均分与数/使用量/收藏数);仅含上架态,租户隔离;
  未上架直访 404 同形;新权限点 bot:run(排序 26,进 DEVELOPER
  推荐集——商店消费面与 chat:execute 同哲学)。
- **互动三件**:收藏 POST /store/bots/{key}/favorite(幂等切换,
  一人一 Bot 一条;我的收藏列表仅含在架)+评分 PUT /store/bots/
  {key}/rating {score 1-5 整数,upsert 改分,非法分值 400}+使用量
  (服务端运行路径自增,无客户端上报通道)。
- **商店直跑**:POST /store/bots/{key}/run {question}(经 025
  service.run 创建者身份,channel="store" 审计溯源;成功运行
  使用量+1)。
- **web**:/store 页(主布局导航,非管理子页):搜索框+排序切换+
  Bot 卡片(名称/greeting/评分星/使用量/收藏心)+详情对话框
  (suggestions 点击即问)+收藏/评分交互;Bots 管理页加上架
  按钮(护栏拒绝行内可见)。

## Capabilities

### New Capabilities

- `bot-store`:上架治理、商店发现、收藏、评分、使用量计数、商店
  直跑的行为契约。

### Modified Capabilities

- `custom-agent`:Bot 纯增上架列与发布渠道(store 渠道溯源)。

## Non-goals

- 不做评论文本通道(评分无文本,与 spec 一致);不做 Bot 版本化
  上架(货架即最新定义);不做跨租户商店;不做个性化推荐(搜索/
  排序即全部发现面);不做使用量分渠道细分(单一总计数);
  不做存量 Bot 自动上架(货架从空开始,新建默认未上架)。
- 不改公开页/api-key 语义(上架与 032 归属、025 公开渠道三维
  正交);不做 Bot 定价/付费(034 billing 域)。

## Impact

- **代码**:bot 域上架列与治理端点 + store 域包(models/ports/
  repository/service/router[发现/互动/直跑])+ web StorePage。
- **Schema**:0021 迁移(上架四列+sys_bot_favorite/sys_bot_rating
  两表[唯一约束];ADR-004 约定)。
- **契约**:/store/* 端点族 + publish/unpublish;make contracts。
- **ADR 引用**:ADR-004/ADR-005。
- **安全**:上架归属校验(fail-closed)+护栏审核+租户隔离+唯一
  约束幂等+计数服务端唯一来源;测试先行(归属/护栏/隔离/幂等/
  分值/计数不可伪造)。
