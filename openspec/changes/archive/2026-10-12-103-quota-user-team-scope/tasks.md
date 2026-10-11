# Tasks

## 1. Schema 与策略 CRUD(api)

- [x] 1.1 新迁移:配额策略 scope CHECK 扩展('USER','TEAM';downgrade 带
  存量守卫),验证:升级/降级往返、存量 TENANT/BOT 行零影响
- [x] 1.2 策略 CRUD scope 扩展(scope_ref 校验三分支:用户/团队存在性+
  租户内,不存在/跨租户拒绝),测试先行,验证:红→绿,`make api-check`
  billing 子集绿
- [x] 1.3 计费配置面 USER/TEAM 清单端点(挂 billing:manage,单授可配,
  不依赖 user:manage/team:manage),验证:单授角色可取清单测试;
  `make contracts` 重生成

## 2. 网关判定多路化(api)

- [x] 2.1 判定多路叠加(确定性顺序 TENANT→BOT→USER→TEAM,任一超限即拒,
  文案带 scope 前缀),测试先行,验证:红→绿;逐路独立 + 两两叠加采样测试
- [x] 2.2 TEAM 聚合联表解析(用量行→bot.team_id 当月窗口聚合;资源换队
  跟随现归属;引用目标删除零命中),验证:TEAM 聚合/换队/删除零命中
  三场景测试
- [x] 2.3 CALLS 维度仅 TENANT(USER/TEAM/BOT 级 CALLS 策略创建期拒绝),
  验证:非法组合 400 测试;既有网关判定测试零回归

## 3. 配置面(web)

- [x] 3.1 计费配置页 scope 下拉扩展 USER/TEAM + scope_ref 选择器(按 scope
  切换数据源),验证:四 scope 创建策略闭环冒烟,typecheck 绿;375 亮暗走查

## 4. 收尾

- [x] 4.1 `make check` 全量绿;冒烟以集成测试等价(本机无运行栈):
  TEAM 级合计达限与「TEAM:」前缀拒绝文案有专项测试钉住
  (test_billing_user_team_quota),验证:冒烟步骤可复现
