# ADR-005: 契约管线 —— OpenAPI 生成 + CI 漂移门禁

日期: 2026-08-16
状态: 已接受(平移前系统 038-contract-single-source 已验证模式)

## 背景

双进程(FastAPI×2)+ React SPA 共三方,契约漂移是历史债的重灾区
(前系统曾为契约漂移专门立项偿还)。TS monorepo 的 tRPC 方案被否,
契约安全必须由**生成管线**补齐。

## 决策

> **单一事实源 = `apps/api` 的 FastAPI OpenAPI schema。
> 前端类型由生成获得,手写 API 类型视为违规。**

```text
apps/api (FastAPI + Pydantic)
   └─ GET /openapi.json
        └─ scripts/export_openapi.py ─→ contracts/openapi.json(入库,审 diff)
             └─ web: openapi-typescript ─→ src/lib/api-types.gen.ts
```

1. `contracts/openapi.json` **提交入库**——PR 里 schema 变更与代码变更同屏可审;
2. CI 门禁:重新导出 → `git diff --exit-code contracts/`,
   防止"改了代码忘了导出"的漂移;
3. 前端 `src/lib/api-types.gen.ts` 生成物入库,类型直接 import;
4. Agent 面(`/internal/*` 契约)同样走此管线——内部接口的漂移更隐蔽,
   更需要门禁。

## 边界

- 该管线覆盖 REST 契约;Agent 图内部状态(`AgentState`)不进 OpenAPI,
  由 `apps/agent` 内部 Pydantic 模型自持,这是进程内实现细节。
