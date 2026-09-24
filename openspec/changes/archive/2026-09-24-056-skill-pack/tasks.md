# 056-skill-pack 实施任务

## 1. 测试先行:领域纯函数与安全面(宪法 Article II/III)

- [x] 1.1 `skillpack/models.py` 包契约纯函数失败测试:四段(可选 metrics)
      结构校验、合并投影截断封顶(glossary≤50/fewshots≤10/persona≤800)、
      零包投影为空段、metric_name 冲突判定;先跑红
- [x] 1.2 SQL 防火墙保留前缀收编 `skill_` 的失败测试:裸名/限定名/CTE
      遮蔽三形态 BLOCKED,业务表不误伤;先跑红(安全规则测试先行)
- [x] 1.3 router 权限失败测试:无 system:skill:manage → 403;跨租户
      pack id 操作 404 同形;先跑红

## 2. Schema 与仓储(ADR-004)

- [x] 2.1 Alembic 迁移:skill_pack + skill_pack_binding 一次建全
      (UUID v7、timestamptz、status VARCHAR+CHECK、唯一键)
- [x] 2.2 repository:CRUD + 按 (tenant, datasource) 加载 enabled 包;
      验证:单测覆盖双过滤与启用事务
- [x] 2.3 防火墙 RESERVED 前缀补 skill_,1.2 转绿

## 3. service 投影 + 管理端点

- [x] 3.1 service:加载合并投影三段+metrics 登记(冲突则启用整体失败);
      验证:1.1 转绿
- [x] 3.2 router:CRUD/启停/列表,权限与租户隔离;验证:1.3 转绿
- [x] 3.3 投影出口挂到既有 retrieve/内部语义段通道;零包时响应字段与
      引入前同构;验证:零回归用例绿

## 4. Agent 拼接与契约

- [x] 4.1 generate/summarize 消费三段(可空);fewshot 段带禁令前缀;
      验证:prompt 断言 + 零段零回归
- [x] 4.2 `make contracts` 重新生成并提交产物(ADR-005);CI 漂移门禁
      本地预演通过
- [x] 4.3 `make check` 对应子集绿;手工冒烟:录入冒烟包→启用注入可见→
      禁用后消失,记录于 apply

## 5. 收口

- [x] 5.1 spec delta sync 进 `openspec/specs/skill-pack/`
- [x] 5.2 权限种子/文档同步;全量 `make check` 收口

> 056 阻塞记录(2026-09-24):本轮未开工(前序返工耗尽预算)。
056 落地记录(2026-09-24):models/repository/service/router+迁移 0030+防火墙 skill_ 前缀+投影挂 retrieve+agent 三段消费;契约已再生。
