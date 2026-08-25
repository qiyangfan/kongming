# 032 team-workspace 实施任务

## 1. 迁移与域包骨架

- [x] 1.1 0020 迁移(sys_team/sys_team_member 两新表 + bot/workflow/
  user_memory 三处 team_id 纯增列)+conftest 清单
- [x] 1.2 team 域包(models[可见性判定纯函数]/ports/repository/
  service/router)+import-linter 契约
- [x] 1.3 权限点种子(system:team:manage 25)+种子测试计数

## 2. 团队与成员(测试先行:安全类)

- [x] 2.1 失败测试:角色边界矩阵(VIEWER 编辑拒/MEMBER 成员管理拒/
  OWNER 全权)/末位 OWNER 拒删/跨租户 404/我所在的团队列表
- [x] 2.2 /system/teams 端点族(CRUD+成员管理+归属变更 attach)

## 3. 资源归属(测试先行:安全类)

- [x] 3.1 失败测试:Bot 列表归属过滤/直访同形/编辑 MEMBER 边界/
  运行 VIEWER 放行;工作流同矩阵;个人零回归
- [x] 3.2 bot 域归属列+列表过滤+直访/编辑/运行判定;workflow 同

## 4. 团队记忆(测试先行:安全类)

- [x] 4.1 失败测试:VIEWER 写入拒/双路召回注入/非成员不召回/
  隐式提取仅个人
- [x] 4.2 memory 写入 team_id 纯增(成员校验)+召回双路合并

## 5. web

- [x] 5.1 /admin/teams 子页(列表/创建/成员管理对话框/删除受控)+
  Bots/工作流归属选择器+MemoryPage 团队写入选择器

## 6. 验证与收尾

- [x] 6.1 make check 全绿;make contracts;016 评估 smoke 复验;
  冒烟(团队 Bot 三角色矩阵 curl;团队记忆双路真 LLM 召回)
- [x] 6.2 R1 独立评审+修复清零
- [x] 6.3 HANDOFF+挂账(知识库归属/触发器归属/团队配额/VIEWER
  细粒度/团队记忆隐式提取)+归档
