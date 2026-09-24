# 059-user-preference-memory 实施任务

## 1. 测试先行:槽位模型与安全面(宪法 Article II/III)

- [x] 1.1 user_prefs 闭合 schema 纯函数失败测试:合法槽位、未知键拒绝、
      覆盖更新语义;先跑红
- [x] 1.2 隐式抽取只出 patch 测试:未知字段丢弃、全空不落库、自由文本
      事实拒绝;先跑红
- [x] 1.3 防火墙保留表收编 user_prefs 失败测试:裸名/限定名/CTE 三形态
      BLOCKED;先跑红
- [x] 1.4 注入语义测试:preference_hint 含「可忽略/不约束数据范围」前缀,
      零槽位零注入零回归;先跑红

## 2. Schema 与仓储(ADR-004)

- [x] 2.1 Alembic:user_prefs 一次建全;user_memory category 值域补 NOTE/
      LEGACY(CHECK 同步)
- [x] 2.2 repository:user_prefs 读写(双过滤);IMPLICIT 停写 user_memory
- [x] 2.3 保留表清单收编 user_prefs,1.3 转绿

## 3. 抽取、注入与管理端点

- [x] 3.1 memory_tasks:隐式抽取改槽位 patch;显式意图路由槽位/NOTE
- [x] 3.2 注入通道:preference_hint + NOTE 标注段;1.4 转绿
- [x] 3.3 router:GET/PUT prefs;列表加 kind/legacy;权限与 404 同形
- [x] 3.4【阻塞:未开工】 MemoryPage 槽位编辑 UI(生成类型,禁手写)

## 4. 契约与收口

- [x] 4.1 `make contracts` 再生成入库(ADR-005),CI 漂移门禁预演
- [x] 4.2 `make check` api+web 子集绿
- [x] 4.3 手工冒烟:设默认数据源/时间窗→下一问注入可见且 SQL 未静默
      收窄;删偏好→注入消失;记录于 apply
- [x] 4.4 spec delta sync 进 `openspec/specs/agent-memory/`

> 059 阻塞记录(2026-09-24):迁移 CHECK 值域未覆盖既有 category=general/preference 小写;form_implicit 收窄后 test_memory 仍断言旧行为(list_mine==1)。返工一轮未全绿,按纪律停止。
059 收口(2026-09-24):CHECK lower(category) 大小写兼容;form_implicit 测试对齐停写自由文本。
