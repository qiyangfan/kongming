# Tasks

## 1. 执行器

- [ ] 1.1 跨服务命令 claim/执行/ack。验证：幂等/失败回执/服务身份。
- [ ] 1.2 reindex/清缓存/取消任务 + 本地命令。验证：端到端；锦官不可用时本地仍可执行。
- [ ] 1.3 action schema/version 校验。验证：未知 action/版本、参数不合法 ack failed；不执行。

## 2. 验收与独立运行

- [ ] 2.1 权限负向与跨租户用例。验证：未授权 401/403，跨租户 404/不可见。
- [ ] 2.2 审计断言。验证：每次写操作有审计记录；普通列表只读不写审计；敏感详情/导出按审计策略。
- [ ] 2.3 独立运行/降级 smoke。验证：停掉管理面或其他仓，本服务核心流程不受影响；管理面停服仅局部不可用。
- [ ] 2.4 契约与生成客户端。验证：OpenAPI/生成物无漂移；跨仓契约测试必跑（X-01）。
- [ ] 2.5 Greenfield 重置：删除/禁用旧控制数据与旧 spec 需求。验证：无兼容/双轨分支，旧端点/旧表不存在或只读历史。

## Workflow follow-up

- 归档前跑 `openspec validate --strict`；实现完成后按仓库门禁收口。
- 本 change 与 longzhong 聚合层 `docs/CONTROL-PLANE-PROJECT.md` 及 `docs/CONTROL-PLANE-BOUNDARY.md` 的对应 WP/边界表保持一致。