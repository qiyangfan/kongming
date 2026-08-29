"""mcp 用例编排(028):配置 CRUD 校验 + 连通性测试 + 发现同步 +
动态工具解析 + 出站执行。零框架(宪法 Article II);operlog 经端口。"""

from typing import Any, Protocol, cast

from kongming.mcp.models import (
    SERVER_KEY_RE,
    McpServerNotFound,
    merge_discovered,
    parse_qualified,
)
from kongming.mcp.ports import McpCaller, McpCallError, McpRepo


class McpValidationError(Exception):
    """领域层 400(fullmatch/endpoint 形态;008 同哲学)。"""


class McpOperLogger(Protocol):
    """操作审计端口(015 operlog 适配,组合根注入 actor/ip 横切)。"""

    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None: ...


def _validate_key(server_key: str) -> None:
    if not SERVER_KEY_RE.fullmatch(server_key):
        raise McpValidationError(
            "server_key 须为小写字母开头的字母/数字/下划线(2-64 位)"
        )


def _validate_endpoint(endpoint: str) -> None:
    if not endpoint.startswith(("http://", "https://")) or len(endpoint) > 512:
        raise McpValidationError("endpoint 须为 http(s) URL(≤512 字符)")


class McpService:
    def __init__(
        self,
        repo: McpRepo,
        caller: McpCaller,
        oper: McpOperLogger | None = None,
    ) -> None:
        self._repo = repo
        self._caller = caller
        self._oper = oper

    async def _audit(self, action: str, target: str, detail: dict[str, object]) -> None:
        if self._oper is not None:
            await self._oper.log(action=action, target=target, detail=detail)

    # ── 配置管理 ──

    async def create(
        self,
        *,
        tenant_id: str,
        server_key: str,
        name: str,
        endpoint: str,
        headers: dict[str, str],
    ) -> str:
        _validate_key(server_key)
        _validate_endpoint(endpoint)
        if not name.strip() or len(name) > 128:
            raise McpValidationError("名称非空且 ≤128 字符")
        clean = {str(k): str(v) for k, v in headers.items() if str(k).strip()}
        sid = await self._repo.create(
            tenant_id=tenant_id,
            server_key=server_key,
            name=name.strip(),
            endpoint=endpoint,
            headers=clean,
        )
        await self._audit("mcp.create", server_key, {"name": name.strip(), "endpoint": endpoint})
        return sid

    async def update(
        self,
        *,
        server_id: str,
        tenant_id: str,
        name: str,
        endpoint: str,
        headers: dict[str, str | None],
    ) -> None:
        _validate_endpoint(endpoint)
        if not name.strip() or len(name) > 128:
            raise McpValidationError("名称非空且 ≤128 字符")
        await self._repo.update(
            server_id=server_id,
            tenant_id=tenant_id,
            name=name.strip(),
            endpoint=endpoint,
            headers=headers,
        )
        await self._audit("mcp.update", server_id, {"name": name.strip(), "endpoint": endpoint})

    async def set_enabled(self, *, server_id: str, tenant_id: str, enabled: bool) -> None:
        if not await self._repo.set_enabled(
            server_id=server_id, tenant_id=tenant_id, enabled=enabled
        ):
            raise McpServerNotFound(server_id)
        await self._audit(
            "mcp.enable" if enabled else "mcp.disable", server_id, {"enabled": enabled}
        )

    async def delete(self, *, server_id: str, tenant_id: str) -> None:
        if not await self._repo.delete(server_id=server_id, tenant_id=tenant_id):
            raise McpServerNotFound(server_id)
        await self._audit("mcp.delete", server_id, {})

    # ── 查询与事务透传(适配层消费) ──

    async def repo_list_masked(self, *, tenant_id: str) -> list[dict[str, Any]]:
        return await self._repo.list_masked(tenant_id=tenant_id)

    async def get(self, *, server_id: str, tenant_id: str) -> dict[str, Any] | None:
        return await self._repo.get(server_id=server_id, tenant_id=tenant_id)

    async def commit(self) -> None:
        await self._repo.commit()

    # ── 连通性测试与同步 ──

    async def test_connection(self, *, server_id: str, tenant_id: str) -> dict[str, Any]:
        row = await self._repo.get(server_id=server_id, tenant_id=tenant_id)
        if row is None:
            raise McpServerNotFound(server_id)
        endpoint, headers = await self._call_target(row, require_enabled=False)
        try:
            count = await self._caller.handshake(endpoint=endpoint, headers=headers)
        except McpCallError as e:
            # 结构化错误,不暴露堆栈/凭证(spec)
            return {"ok": False, "kind": e.kind, "detail": e.detail}
        return {"ok": True, "tools": count}

    async def sync(self, *, server_id: str, tenant_id: str) -> dict[str, Any]:
        """发现同步:tools/list → 冲突跳过告警 → 快照原子替换;
        不可达降级记 last_sync_error(spec「断连降级」)。"""
        row = await self._repo.get(server_id=server_id, tenant_id=tenant_id)
        if row is None:
            raise McpServerNotFound(server_id)
        endpoint, headers = await self._call_target(row, require_enabled=False)
        try:
            discovered = await self._caller.list_tools(endpoint=endpoint, headers=headers)
        except McpCallError as e:
            err = f"{e.kind}: {e.detail}"[:500]
            await self._repo.save_snapshot(
                server_id=server_id,
                tenant_id=tenant_id,
                tools=list(row.get("tools") or []),  # 保留旧快照(降级)
                error=err,
            )
            return {"ok": False, "kind": e.kind, "detail": e.detail}
        # R1-S2:注册时收敛敌意/畸形 JSON Schema——check_schema 不过
        # 降级为 {"type":"object"}(L4 退化为仅结构校验),不合法 schema
        # 进快照会让 validate_args 双路径裸 500(评审实证);
        # 审查循环 F1:回溯型 pattern(check_schema 不查,实测 ReDoS 可
        # 卡死 worker)同走降级
        import jsonschema.validators as _jsv

        from kongming.mcp.models import has_redos_pattern

        conflicts: list[str] = []
        for t in discovered:
            try:
                if has_redos_pattern(t.schema):
                    raise ValueError("回溯型 pattern(ReDoS 面已降级)")
                _jsv.validator_for(t.schema).check_schema(t.schema)
            except Exception:  # noqa: BLE001  # 敌意面宽收敛,降级即安全
                conflicts.append(f"schema 非法已降级: {t.name[:40]}")
                t.schema.clear()
                t.schema.update({"type": "object"})
        entries, merge_conflicts = merge_discovered(str(row["server_key"]), discovered)
        conflicts.extend(merge_conflicts)
        error_note = ""
        if conflicts:
            error_note = f"注册告警: {', '.join(conflicts)}"[:500]
        await self._repo.save_snapshot(
            server_id=server_id, tenant_id=tenant_id, tools=entries, error=error_note
        )
        return {"ok": True, "tools": len(entries), "conflicts": conflicts}

    async def _call_target(
        self, row: dict[str, Any], *, require_enabled: bool = True
    ) -> tuple[str, dict[str, str]]:
        """get 行(掩码面)没有明文——出站前经专用出口取(明文不过查询面)。
        管理面探测(test/sync)传 require_enabled=False(R1-S1 分流)。"""
        target = await self._repo.get_call_target(
            tenant_id=str(row["tenant_id"]),
            server_key=str(row["server_key"]),
            require_enabled=require_enabled,
        )
        if target is None:  # 禁用中/被删竞态
            raise McpServerNotFound(str(row["server_key"]))
        return target

    # ── 动态工具面(toolcall 主链消费) ──

    async def resolve_tool(self, *, tenant_id: str, qualified: str) -> dict[str, Any] | None:
        """动态解析:返回 {name, description, schema, read_only, server_key};
        未命中(不存在/禁用/快照无)→ None(TOOL_NOT_REGISTERED 语义)。"""
        row = await self._repo.find_enabled_by_tool(tenant_id=tenant_id, qualified=qualified)
        if row is None:
            return None
        for raw_entry in cast("list[object]", row.get("tools") or []):
            # isinstance 守卫保留;收窄产物键型未知,cast 收窄后再取值(仅类型断言)
            entry = cast("dict[str, Any]", raw_entry) if isinstance(raw_entry, dict) else None
            if entry is not None and entry.get("qualified") == qualified:
                return {**entry, "server_key": str(row["server_key"])}
        return None

    async def list_dynamic_tools(self, *, tenant_id: str) -> list[dict[str, Any]]:
        """目录聚合:全部启用 server 的快照展开(跨 server 冲突先注册方胜,
        按 created_at 序)——list_enabled 已按 created_at 升序。"""
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for row in await self._repo.list_enabled(tenant_id=tenant_id):
            entries = cast("list[object]", row.get("tools") or [])
            for raw in entries:
                if not isinstance(raw, dict):
                    continue
                # isinstance 收窄产物键型未知,cast 收窄后再取值(仅类型断言)
                e = cast("dict[str, Any]", raw)
                q = str(e.get("qualified") or "")
                if not q or q in seen:
                    continue
                seen.add(q)
                out.append({**e, "server_key": str(row["server_key"])})
        return out

    async def call_tool(
        self, *, tenant_id: str, qualified: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """出站执行(六层防御后主链调用点):解析 → 取明文(瞬时)→ 调用。"""
        parsed = parse_qualified(qualified)
        if parsed is None:
            raise McpCallError("protocol", "工具名形态非法")
        _, tool_name = parsed
        entry = await self.resolve_tool(tenant_id=tenant_id, qualified=qualified)
        if entry is None:
            raise McpCallError("remote", "工具不可用(已禁用/删除/未注册)")
        # R1-A5:server_key 取 resolve 结果——parse_qualified 按第一个 __
        # 切分,key 内含连续下划线(a__b 合法形态)会被错切
        target = await self._repo.get_call_target(
            tenant_id=tenant_id, server_key=str(entry["server_key"])
        )
        if target is None:
            raise McpCallError("remote", "server 已禁用或删除")
        endpoint, headers = target
        result = await self._caller.call(
            endpoint=endpoint, headers=headers, tool_name=tool_name, args=args
        )
        if result.get("truncated"):
            result["content"] = str(result["content"]) + "\n…[结果超 1MB 已截断]"
        return result
