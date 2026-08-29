# Design: 045 保留表收编 user_memory

## 决策

### D1 落点:防火墙保留表清单(frozenset 补一项),零新机制

- `RESERVED_TABLE_NAMES = frozenset({"alembic_version", "user_identities",
  "user_memory"})`;拦截在首轮 `check`(先于 datascope applier),改写产物
  复检段 `check_reserved_tables` 同名单自动生效(纵深防御双段闭合)。
- 既有判定语义全部复用:末段名小写折叠 → 裸名(search_path 落 public)/
  `public.user_memory` 限定名/CTE 遮蔽(WITH m AS (SELECT FROM
  public.user_memory) SELECT FROM m)/JOIN 夹带,全形态覆盖,零新增代码路径。
- 拦截形态 = 既有 RESERVED_TABLE BLOCKED(落审计、友好文案),与
  sys_user 同款,无新错误分类。

### D2 datascope 零改动(职责边界重申)

- SELF/DEPT 域对无 create_by/dept_id 表的 SKIP 放行是立约正确语义:业务库
  大量表无归属列,行级过滤仅在归属列在场时有意义;
- 平台内部表的防线=保留表拒读(005 立约),datascope 从不承担该职责——
  本变更后两道防线职责边界与 spec 背景描述完全对齐。

### D3 验证口径

- 单测:RESERVED_BLOCK 补 user_memory 四形态(裸名/限定名/CTE 遮蔽/JOIN
  夹带);放行用例(user_memory 之外的普通名)回归不误伤;
- datascope 单测零改动全绿(SELF 降级用例语义不变);
- 端到端:default 租户问答「列出所有用户记忆」→ BLOCKED 失败终态(防火墙
  拦截文案),sys_sql_audit 落 BLOCKED;记忆召回通道(022 memory_hint)
  回归不受影响(纯控制面路径,不过防火墙)。

## 落点

| 文件 | 改动 |
|---|---|
| api sqlfw/firewall.py | RESERVED_TABLE_NAMES 增补 "user_memory"+注释 |
| api tests/test_sqlfw_firewall.py | RESERVED_BLOCK 四形态+放行回归 |
| contracts | 零漂移(无 api 路由/模型变化) |
