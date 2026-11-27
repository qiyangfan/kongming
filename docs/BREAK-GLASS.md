# 孔明运行时 break-glass 运行手册

服务或锦官不可用、常规服务 admin API 无法修复运行时数据时的受控通道。
不嵌入管理台、不提供 admin 端点、不能当作日常写路径。

## 前置

独立变量（不经应用进程，建议由 secret store 注入）：

| 变量 | 含义 |
| --- | --- |
| `KM_BREAK_GLASS_DATABASE_URL` | 独立数据库凭证（与应用运行凭证分离） |
| `KM_BREAK_GLASS_TOKEN` | 通道口令；必须与 `--token` 一致 |
| `KM_BREAK_GLASS_ACTOR` | 操作者标识（进审计） |
| `KM_BREAK_GLASS_EXPIRES_AT` | 时间窗截止（ISO8601，带时区） |

时间窗过期、口令缺失或不匹配、目标不是 UUID 时都会在连接数据库前拒绝。

## 允许的场景

| 场景 | 作用 | 影响上限 |
| --- | --- | --- |
| `mark-task-interrupted` | RUNNING/PLANNING 卡死任务收敛为 INTERRUPTED | 1 行 |
| `reset-report-failed` | 滞留 GENERATING 的报告标记 FAILED | 1 行 |

不存在任意 SQL 入口；新增场景必须改代码、评审并随发布上线。

## 操作步骤

1. 先 dry-run，确认候选行数：

   ```bash
   cd apps/api
   uv run python scripts/break_glass.py \
     --scenario mark-task-interrupted \
     --id <task-uuid> \
     --reason "看门狗失效后人工收口" \
     --token "$KM_BREAK_GLASS_TOKEN"
   ```

2. 二次确认后执行：

   ```bash
   uv run python scripts/break_glass.py \
     --scenario mark-task-interrupted \
     --id <task-uuid> \
     --reason "看门狗失效后人工收口" \
     --token "$KM_BREAK_GLASS_TOKEN" \
     --apply \
     --confirm "APPLY mark-task-interrupted <task-uuid>"
   ```

3. 核对审计（actor/原因/SQL 摘要/影响行数）：

   ```sql
   SELECT action, target, detail, created_at
   FROM public.sys_oper_log
   WHERE action LIKE 'break_glass.%'
   ORDER BY created_at DESC;
   ```

## 回滚

本通道只改状态列，不删数据。`mark-task-interrupted` 与
`reset-report-failed` 的回滚是把状态改回不可能自动恢复的终态（会误导），
因此不提供反向场景；如需重新执行任务/报告，走正常服务接口重新发起。

## 边界

- 不替代备份/恢复、HA 或常规修复；日常修复走服务 admin API。
- 不绕过服务不变量：只处理白名单状态迁移，行数上限 1。
- 审计中的 actor/reason 不含敏感值；不记录数据库连接串。
