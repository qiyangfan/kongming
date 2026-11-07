# Tasks: tune-entity-recall-pool

## 1. 仓库层门槛过滤

- [x] 1.1 `documents_repo.recall_children_by_entities` 增加 `min_shared` 参数
  与 `HAVING count(*) >= :min_shared`(docstring 同步门槛语义)
- [x] 1.2 `documents_service._entity_child_hits`:门槛常量、合并实体集 <2
  短路返回空(不执行 join)

## 2. 测试

- [x] 2.1 既有 join 测试更新为双实体形态(门槛下命中断言 + 单实体空断言)
- [x] 2.2 试跑实体路测试更新:双实体查询实体命中展示共享数原生分
- [x] 2.3 租户/数据源双过滤测试在门槛形态下复验(既有断言口径保持)

## 3. 验收与对照

- [x] 3.1 相关测试+静态门禁(ruff/mypy/pyright)
- [x] 3.2 实体开形态重启 api(复用 agent env LLM 三件+私网豁免),复跑
  retrieval 层评测,002 probe 逐路复核
- [x] 3.3 README 基线表新增调优后对照行(同环境同条件口径),判据=不低于
  实体关行;不达判据→按 owner 指令转下掉处置
- [x] 4.1 openspec validate + 归档同步主 spec + handoff/roadmap 状态回填
