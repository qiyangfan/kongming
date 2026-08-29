# Tasks: 045-reserved-user-memory

## 1. 测试先行(失败测试)

- [x] 1.1 test_sqlfw_firewall:RESERVED_BLOCK 补 user_memory 四形态
  (裸名 / public.user_memory 限定名 / CTE 遮蔽 / JOIN 夹带),当前应放行(红灯)
- [x] 1.2 放行回归:业务表普通名查询不受收编影响(既有 pass 用例保持)

## 2. 实现

- [x] 2.1 firewall.py:RESERVED_TABLE_NAMES 增补 "user_memory" + 注释记 039
  上报定性(022 建表漏同步收编;datascope 降级不构成防线)

## 3. 门禁与契约

- [x] 3.1 api 全量(ruff+mypy+pytest)全绿;make check 复跑
- [x] 3.2 make contracts 零漂移(无路由/模型变化)

## 4. 实测验证

- [x] 4.1 default 租户问答「列出所有用户记忆」→ BLOCKED 失败终态,
  sys_sql_audit 落 BLOCKED 记录(不再透出记忆数据)
- [x] 4.2 022 记忆召回通道回归:含记忆用户正常问答,memory_hint 注入不受影响
