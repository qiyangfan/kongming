# 036 cross-domain-debt-clearance:跨域挂账清偿包

## Why

029–033 五变更的评审挂账经架构裁决(2026-08-24,裁决记录于会话):
34 项中真正构成缺陷/安全/体验债的 8 项,单项太小不值得独立变更,
照 013 先例打包清偿,避免挂账积压腐化。其中 030④(纯图片问题硬走
SQL 答非所问)与 031⑥(远未来 cron 保存阻塞请求 0.3–0.5s)是真实
正确性/性能缺陷,030③(vision 描述注入面)是安全纵深缺口,其余
为小正确性与体验收尾。

## What Changes

1. **vision 描述注入包裹(agent,030③)**:vision 描述段以「不可信
   数据」标记包裹后拼入问题(内容非指令),消图片内嵌文字指令经
   vision 描述混入 prompt 的间接注入面(020 输出护栏兜底不变)。
2. **纯图片问题意图分流(agent,030④)**:recipe 图复用 024 分流
   框架——附件在场且无 SQL 面意图信号的问题走文档/直答通道,
   不再硬走 SQL 生成答非所问。
3. **孤儿附件清理(api,030⑤)**:lifespan 每日薄壳清理 UPLOADED
   超期(24h)未被引用的附件(删文件+删行),消磁盘泄漏。
4. **每模型图片上限配置化(agent,030⑦)**:模型注册表条目支持
   图片体积上限键,vision 调用前按 min(模型上限,10MB)闸,
   供应商限制先于平台上限生效时不再误导。
5. **cron next_fire 日历跳跃重写(api,031⑥⑦)**:解析与推进改
   日历字段跳跃式(消远未来表达式 52 万次逐 tick 迭代的请求
   阻塞),并兼容 Vixie `a/b` 形态(如 `5/10`=从 5 起步进 10)。
6. **team_id 回传(api+web,032⑥)**:BotOut/MemoryItemOut 纯增
   team_id(契约纯增零破坏),Bots 页/Memory 页展示归属徽标。
7. **成员候选排除 user_id 化(web,032⑧)**:Teams 页加成员
   对话框排除已有成员按 user_id 语义键(用户名可改不再漏排)。
8. **收藏切换原子化(api,033⑥)**:toggle_favorite 改单语句
   CTE(DELETE...RETURNING + INSERT...WHERE NOT EXISTS),消
   先查后写竞态的响应布尔瞬时不符。

## Capabilities

### New Capabilities

(无——全部为既有能力的行为清偿)

### Modified Capabilities

- `multimodal-input`:vision 描述注入包裹(③);纯图片问题意图
  分流(④);孤儿附件定期清理(⑤);每模型图片上限(⑦)。
- `triggers-webhooks`:cron 解析与推进的日历跳跃实现与 `a/b`
  形态兼容(⑥⑦)。
- `team-workspace`:归属字段(team_id)响应回传与徽标展示(⑥);
  成员候选排除按 user_id 语义键(⑧)。
- `bot-store`:收藏切换的原子语义(并发下响应与终态一致)(⑥)。

## Non-goals

- 029–033 裁决为「远期/部署域/不做」的项:可视化画布/STT/OCR/
  知识库团队归属(独立变更候选)/多实例调度互斥/SSRF DNS TOCTOU
  (部署域出口代理)/个性化推荐/Bot 版本化上架/评论通道等。
- 上传频率/总量配额(030⑤ 的另一半——当前无滥用者,随配额体系)。
- 真实支付通道(035 挂账①,独立域)。
- 图片内容合规审核(外部内容安全服务,产品决策远期)。

## Impact

- **代码**:agent multimodal(包裹/上限)+graph 分流条件+api
  attachment 清理薄壳+trigger cronline 重写+store toggle 单语句+
  bot/memory 响应纯增+web BotsPage/MemoryPage/TeamsPage 徽标与排除。
- **Schema**:零迁移(纯行为与响应纯增)。
- **契约**:BotOut/MemoryItemOut 纯增 team_id;make contracts。
- **ADR 引用**:ADR-005(契约纯增);零信任边界/零认证变更。
- **安全**:③为注入纵深收口;测试先行(cron 跳跃矩阵/包裹标记/
  分流/原子切换并发)。
