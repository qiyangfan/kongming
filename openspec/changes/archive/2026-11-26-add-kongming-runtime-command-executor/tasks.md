# Tasks

## 1. 执行器

- [x] 1.1 跨服务命令 claim/执行/ack。验证：幂等/失败回执/服务身份。
- [x] 1.2 reindex/清缓存/取消任务 + 本地命令。验证：端到端；锦官不可用时本地仍可执行。
- [x] 1.3 action schema/version 校验。验证：未知 action/版本、参数不合法 ack failed；不执行。

## 2. 验收与独立运行

- [x] 2.1 权限负向与跨租户用例。验证：未授权 401/403,跨租户 404/不可见。
- [x] 2.2 审计断言。验证：每次写操作有审计记录；普通列表只读不写审计；敏感详情/导出按审计策略。
- [x] 2.3 独立运行/降级 smoke。验证：停掉管理面或其他仓,本服务核心流程不受影响；管理面停服仅局部不可用。
- [x] 2.4 契约与生成客户端。验证：OpenAPI/生成物无漂移；跨仓契约测试必跑（X-01）。
- [x] 2.5 Greenfield 重置：删除/禁用旧控制数据与旧 spec 需求。验证：无兼容/双轨分支,旧端点/旧表不存在或只读历史。


> 2026-11-27 实施备注:跨仓契约(X-01)与 fujie client/真实 Keycloak 联调未就绪,
> 对应任务保持未勾选并登记为阻塞;本仓机制与本地验证已完成。

本地证据:
- `control/commands.py` 有限 action 注册表(rag.reindex/cache.clear/task.cancel)、
  参数白名单、版本校验、幂等回执(EXECUTING→终态);任意 SQL/脚本无入口。
- 本地命令端点 `POST /system/commands`(+`/system/commands/receipts`),
  不依赖控制面;执行写 `sys_oper_log`。跨服务 claim/ack 用服务身份客户端。
- 2.4 跨仓 action schema 对拍依赖 X-01,标**阻塞**。
