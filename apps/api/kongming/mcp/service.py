"""mcp 用例编排:配置 CRUD 校验 + 连通性测试 + 发现同步 +
动态工具解析 + 出站执行。用例层不依赖 FastAPI 或 SQLAlchemy;操作日志经端口写入。
三原语探测(test/sync)、资源快照存库、read_resource 白名单执行
适配(白名单/文本 mime/截断/注入检测)、模板只读预览(实时拉取)。"""

from typing import TYPE_CHECKING, Any, Protocol, cast

from kongming.mcp.models import (
    READ_RESOURCE_TOOL,
    SERVER_KEY_RE,
    McpServerNotFound,
    is_text_mime,
    merge_discovered,
    parse_qualified,
    prompt_snapshot,
    read_resource_tool,
    resource_snapshot,
    sanitize_external_text,
)
from kongming.mcp.ports import McpCaller, McpCallError, McpRepo

if TYPE_CHECKING:
    from kongming.mcp.breaker import McpBreaker


class McpValidationError(Exception):
    """领域校验错误(key/endpoint 形态),路由层映射 400。"""


class McpOperLogger(Protocol):
    """操作审计端口(operlog 适配,组合根注入 actor/ip 横切)。"""

    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None: ...


def _validate_key(server_key: str) -> None:
    if not SERVER_KEY_RE.fullmatch(server_key):
        raise McpValidationError("server_key 须为小写字母开头的字母/数字/下划线(2-64 位)")


def _validate_endpoint(endpoint: str) -> None:
    if not endpoint.startswith(("http://", "https://")) or len(endpoint) > 512:
        raise McpValidationError("endpoint 须为 http(s) URL(≤512 字符)")


# 模板预览文本上限(管理面低频只读动作,UI 形态量级;截断附标注)
_PREVIEW_CAP = 64 * 1024


def _sanitize_schemas(tools: list[Any]) -> list[str]:
    """收敛敌意/畸形 JSON Schema,返回降级注记。

    check_schema 不过降级为 {"type":"object"}(L4 退化为仅结构校验),不合法
    schema 进快照会让 validate_args 双路径裸 500;check_schema 不检查正则回溯
    风险,回溯型 pattern 也必须降级,避免阻塞 worker。"""
    import jsonschema.validators as _jsv

    from kongming.mcp.models import has_redos_pattern

    notes: list[str] = []

    def _validate(t: Any) -> None:
        if has_redos_pattern(t.schema):
            raise ValueError("回溯型 pattern(ReDoS 面已降级)")
        _jsv.validator_for(t.schema).check_schema(t.schema)

    for t in tools:
        try:
            _validate(t)
        except Exception:  # noqa: BLE001  # 敌意面宽收敛,降级即安全
            notes.append(f"schema 非法已降级: {t.name[:40]}")
            t.schema.clear()
            t.schema.update({"type": "object"})
    return notes


class McpService:
    def __init__(
        self,
        repo: McpRepo,
        caller: McpCaller,
        oper: McpOperLogger | None = None,
        breaker: "McpBreaker | None" = None,
    ) -> None:
        self._repo = repo
        self._caller = caller
        self._oper = oper
        self._breaker = breaker

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
        """握手 + 三原语探测。不支持与原语类端点不可用一律计数 0 不算
        失败(spec 验收句);tools 为核心原语,其失败保持整体失败(既有分类)。
        结构化错误注明原语名,不暴露堆栈/凭证。"""
        row = await self._repo.get(server_id=server_id, tenant_id=tenant_id)
        if row is None:
            raise McpServerNotFound(server_id)
        endpoint, headers = await self._call_target(row, require_enabled=False)
        try:
            probe = await self._caller.probe(endpoint=endpoint, headers=headers)
        except McpCallError as e:
            # 握手失败:结构化错误返回
            return {"ok": False, "kind": e.kind, "detail": e.detail}
        if "tools" in probe.failures:
            kind, detail = probe.failures["tools"]
            return {"ok": False, "kind": kind, "detail": f"tools: {detail}"}
        out: dict[str, Any] = {
            "ok": True,
            "tools": len(probe.tools),
            "resources": len(probe.resources),
            "prompts": len(probe.prompts),
        }
        if probe.failures:  # 原语类降级:计数 0 + 注记(排障可见,不算失败)
            out["detail"] = "原语降级计数 0: " + "; ".join(
                f"{name}: {detail}" for name, (_, detail) in sorted(probe.failures.items())
            )
        return out

    async def sync(self, *, server_id: str, tenant_id: str) -> dict[str, Any]:
        """发现同步:三原语并行探测 → 快照原子替换(tools + resources
        + prompts);单原语失败降级保留旧清单(同断连降级口径);
        资源清单非空 → 前置注册 read_resource 合成工具。"""
        row = await self._repo.get(server_id=server_id, tenant_id=tenant_id)
        if row is None:
            raise McpServerNotFound(server_id)
        endpoint, headers = await self._call_target(row, require_enabled=False)
        old = (
            list(row.get("tools") or []),
            list(row.get("resources") or []),
            list(row.get("prompts") or []),
        )
        try:
            probe = await self._caller.probe(endpoint=endpoint, headers=headers)
        except McpCallError as e:
            await self._save_kept(
                server_id=server_id,
                tenant_id=tenant_id,
                kept=old,
                error=f"{e.kind}: {e.detail}"[:500],
            )
            return {"ok": False, "kind": e.kind, "detail": e.detail}
        if "tools" in probe.failures:
            # 工具(核心原语)失败:整体降级,三快照全保留
            kind, detail = probe.failures["tools"]
            await self._save_kept(
                server_id=server_id,
                tenant_id=tenant_id,
                kept=old,
                error=f"{kind}: {detail}"[:500],
            )
            return {"ok": False, "kind": kind, "detail": detail}
        return await self._save_discovered(
            probe=probe,
            row=row,
            kept=old,
            server_id=server_id,
            tenant_id=tenant_id,
            endpoint=endpoint,
        )

    async def _save_discovered(
        self,
        *,
        probe: Any,
        row: dict[str, Any],
        kept: tuple[list[Any], list[Any], list[Any]],
        server_id: str,
        tenant_id: str,
        endpoint: str,
    ) -> dict[str, Any]:
        """成功路径落快照:清洗/降级/合成工具/合并 → 原子替换 → 复位熔断。"""
        old = kept
        discovered = list(probe.tools)
        notes = _sanitize_schemas(discovered)
        resources_out, prompts_out = self._per_primitive_snapshots(
            probe, kept=(old[1], old[2]), notes=notes
        )
        # 资源白名单非空 → 前置注册 read_resource 合成工具(先于 server
        # 自有工具,同名冲突合成方胜——平台治理语义优先);白名单空/不支持
        # 不注册(目录零噪音,「只有工具的简单 server」零感知)
        if resources_out:
            discovered.insert(0, read_resource_tool())
        entries, merge_conflicts = merge_discovered(str(row["server_key"]), discovered)
        notes.extend(merge_conflicts)
        error_note = f"注册告警: {', '.join(notes)}"[:500] if notes else ""
        await self._repo.save_snapshot(
            server_id=server_id,
            tenant_id=tenant_id,
            tools=entries,
            resources=resources_out,
            prompts=prompts_out,
            error=error_note,
        )
        if self._breaker is not None:
            # 同步成功=管理员确认可达,显式复位熔断
            # (运行时立即可用,不等半开探测;管理面探测不受熔断约束)
            self._breaker.reset(endpoint)
        return {
            "ok": True,
            "tools": len(entries),
            "resources": len(resources_out),
            "prompts": len(prompts_out),
            "conflicts": notes,
        }

    async def _save_kept(
        self,
        *,
        server_id: str,
        tenant_id: str,
        kept: tuple[list[Any], list[Any], list[Any]],
        error: str,
    ) -> None:
        """降级落快照:三原语全保留旧清单,只记错误注记。"""
        old_tools, old_resources, old_prompts = kept
        await self._repo.save_snapshot(
            server_id=server_id,
            tenant_id=tenant_id,
            tools=old_tools,
            resources=old_resources,
            prompts=old_prompts,
            error=error,
        )

    def _per_primitive_snapshots(
        self,
        probe: Any,
        *,
        kept: tuple[list[Any], list[Any]],
        notes: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """资源/模板清单:失败降级保留旧值(单原语失败不整体失败),
        成功/不支持如实替换(同步语义=快照原子替换)。"""
        old_resources, old_prompts = kept
        if "resources" in probe.failures:
            kind, detail = probe.failures["resources"]
            notes.append(f"原语降级 resources: {kind}: {detail}")
            resources_out = cast("list[dict[str, Any]]", old_resources)
        else:
            resources_out = resource_snapshot(probe.resources)
        if "prompts" in probe.failures:
            kind, detail = probe.failures["prompts"]
            notes.append(f"原语降级 prompts: {kind}: {detail}")
            prompts_out = cast("list[dict[str, Any]]", old_prompts)
        else:
            prompts_out = prompt_snapshot(probe.prompts)
        return resources_out, prompts_out

    async def _call_target(
        self, row: dict[str, Any], *, require_enabled: bool = True
    ) -> tuple[str, dict[str, str]]:
        """get 行(掩码面)没有明文——出站前经专用出口取(明文不过查询面)。
        管理面探测(test/sync)传 require_enabled=False(分流)。"""
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
            # 快照产物键型未知,先 isinstance 收窄再 cast(仅类型断言)
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
        """出站执行(六层防御后主链调用点):解析 → 取明文(瞬时)→ 调用。
        read_resource 走资源白名单适配,其余工具原样出站。"""
        parsed = parse_qualified(qualified)
        if parsed is None:
            raise McpCallError("protocol", "工具名形态非法")
        _, tool_name = parsed
        entry = await self.resolve_tool(tenant_id=tenant_id, qualified=qualified)
        if entry is None:
            raise McpCallError("remote", "工具不可用(已禁用/删除/未注册)")
        # server_key 取 resolve 结果——parse_qualified 按第一个 __
        # 切分,key 内含连续下划线(a__b 合法形态)会被错切
        server_key = str(entry["server_key"])
        target = await self._repo.get_call_target(tenant_id=tenant_id, server_key=server_key)
        if target is None:
            raise McpCallError("remote", "server 已禁用或删除")
        endpoint, headers = target
        if tool_name == READ_RESOURCE_TOOL:
            return await self._call_read_resource(
                tenant_id=tenant_id,
                server_key=server_key,
                endpoint=endpoint,
                headers=headers,
                args=args,
            )
        result = await self._caller.call(
            endpoint=endpoint, headers=headers, tool_name=tool_name, args=args
        )
        if result.get("truncated"):
            result["content"] = str(result["content"]) + "\n…[结果超 1MB 已截断]"
        return result

    async def _call_read_resource(
        self,
        *,
        tenant_id: str,
        server_key: str,
        endpoint: str,
        headers: dict[str, str],
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """read_resource 执行适配(资源内容三层防线):uri 白名单校验
        (不出站)→ 文本 mime 限定(声明二进制不出站)→ 出站读取 → 截断
        标注 → 注入检测(既有注入检测面,与工具观察既有防线同构)。"""
        uri = str(args.get("uri") or "")
        if not uri.strip():
            raise McpCallError("protocol", "uri 参数缺失")
        whitelist = await self._repo.get_enabled_resources(
            tenant_id=tenant_id, server_key=server_key
        )
        hit = next((r for r in whitelist if str(r.get("uri") or "") == uri), None)
        if hit is None:
            # 白名单外拒绝:结构化错误观察,不出站;白名单来自
            # 最近一次成功探测的资源快照,server 未重同步前新增资源不可读
            raise McpCallError("remote", "uri 不在资源白名单(仅限该 server 资源清单内 uri,未出站)")
        if not is_text_mime(str(hit.get("mime_type") or "")):
            # 观察面仅文本形态,二进制资源不出站
            raise McpCallError("remote", "二进制资源不进观察(文本形态限定,未出站)")
        result = await self._caller.read_resource(endpoint=endpoint, headers=headers, uri=uri)
        cleaned, hits = sanitize_external_text(str(result.get("content") or ""))
        if hits:
            result["sanitized"] = True
            result["injection_hits"] = hits
        result["content"] = cleaned
        if result.get("truncated"):
            result["content"] = str(result["content"]) + "\n…[结果超 1MB 已截断]"
        return result

    async def preview_prompt(
        self,
        *,
        server_id: str,
        tenant_id: str,
        name: str,
        arguments: dict[str, str],
    ) -> dict[str, Any]:
        """模板按名实时拉取只读预览(渲染带 arguments,快照
        无法表达,管理面低频动作实时拉取不落库);预览文本过注入检测 +
        大小上限(截断标注)。只读:不注入任何提示词、非运行时提示来源。"""
        row = await self._repo.get(server_id=server_id, tenant_id=tenant_id)
        if row is None:
            raise McpServerNotFound(server_id)
        endpoint, headers = await self._call_target(row, require_enabled=False)
        try:
            out = await self._caller.get_prompt(
                endpoint=endpoint, headers=headers, name=name, arguments=arguments
            )
        except McpCallError as e:
            return {"ok": False, "kind": e.kind, "detail": e.detail, "text": ""}
        text = str(out.get("text") or "")
        original = len(text.encode("utf-8"))
        truncated = original > _PREVIEW_CAP
        if truncated:
            text = text.encode("utf-8")[:_PREVIEW_CAP].decode("utf-8", errors="ignore")
        cleaned, hits = sanitize_external_text(text)
        if truncated:
            cleaned = f"{cleaned}\n…[预览超 {_PREVIEW_CAP // 1024}KB 已截断]"
        return {
            "ok": True,
            "text": cleaned,
            "truncated": truncated,
            "injection_hits": hits,
        }
