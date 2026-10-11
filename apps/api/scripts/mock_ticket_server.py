"""最小工单 API 约定 + mock 工单服务(100 internal-connector-domain)。

════════════════════════════════════════════════════════════════════
「最小工单 API 约定」(单一事实源;HANDOFF 引用本文,ticket 域按此对接)
════════════════════════════════════════════════════════════════════

面向「内部工单系统」的最小 HTTP 面:三端点 + 统一 JSON 字段命名。
真实系统(Jira/ServiceNow 等)接入时按本约定写门面或加适配层,平台侧
(ticket 域)不为对接差异在适配单点之外散落第二套映射。

端点(全部 JSON;相对路径挂在与 KM_TICKET_BASE_URL 一致的根上):

- GET  /tickets?query=&status=&limit=
    按关键词(标题/描述子串)/状态过滤,limit 截断(1–50,缺省 10)。
    → 200 {"tickets": [<工单>...], "total": <过滤后总数>}
- GET  /tickets/{id}
    → 200 <工单>;id 不存在 → 404 {"error": "ticket not found"}
- POST /tickets   body: {"title", "description", "priority", "assignee"?}
    title/description/priority 必填;assignee 可选。
    → 201 <工单>(服务端生成 id 与时间戳,status 初始 open)

工单统一 JSON 字段(snake_case,六字段语义即工具观察 allowlist 的来源):

- id          工单标识(字符串,如 "T-1001")
- title       标题
- description 描述(仅入站/存储面;工具观察 allowlist 不含,不进 LLM 上下文)
- status      状态枚举,原样透传不做平台侧翻译(约定缺省集:
              open / in_progress / resolved / closed)
- priority    优先级枚举,原样透传(约定缺省集:high / medium / low)
- assignee    负责人(可空字符串表示未指派)
- created_at / updated_at   ISO 8601 UTC 时间戳

鉴权:Authorization: Bearer <token>;启动以 --token 固定期望值,缺失或不
匹配 → 401 {"error": "unauthorized"}。token 即控制面 KM_TICKET_API_KEY
(服务凭证只存控制面环境变量,ADR-002)。

════════════════════════════════════════════════════════════════════
mock 实现说明
════════════════════════════════════════════════════════════════════

单文件脚本:标准库 http.server + 内存态种子数据,零第三方依赖;e2e 集成
测试(tests/test_ticket_domain.py)与演示环境共用同一实现——测试过的就是
演示的。不进 compose 部署形态(演示期手工启动,配方见 docs/HANDOFF.md)。

启动:uv run python scripts/mock_ticket_server.py --port 8123 \
        --token "$KM_TICKET_API_KEY"
内存态:重启即回种子数据(演示形态,非持久化承诺)。
"""

from __future__ import annotations

import argparse
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from urllib.parse import parse_qs

# 种子数据(内存态;演示形态)。id 自增序列接续种子最大号。
_SEED_TICKETS: list[dict[str, object]] = [
    {
        "id": "T-1001",
        "title": "报表导出超时",
        "description": "导出十万行报表时前端等待超时,需要分页或异步化。",
        "status": "open",
        "priority": "high",
        "assignee": "张三",
        "created_at": "2026-10-09T02:15:00+00:00",
        "updated_at": "2026-10-09T02:15:00+00:00",
    },
    {
        "id": "T-1002",
        "title": "登录页验证码不显示",
        "description": "部分浏览器下图形验证码偶发空白,刷新后恢复。",
        "status": "in_progress",
        "priority": "medium",
        "assignee": "李四",
        "created_at": "2026-10-08T09:40:00+00:00",
        "updated_at": "2026-10-10T11:05:00+00:00",
    },
    {
        "id": "T-1003",
        "title": "工单列表希望支持按优先级排序",
        "description": "业务侧希望默认按优先级降序展示待处理工单。",
        "status": "open",
        "priority": "low",
        "assignee": "",
        "created_at": "2026-10-10T06:00:00+00:00",
        "updated_at": "2026-10-10T06:00:00+00:00",
    },
    {
        "id": "T-1004",
        "title": "数据源连接串轮换后未生效",
        "description": "轮换密码后仍提示连接失败,疑似连接池未刷新。",
        "status": "resolved",
        "priority": "high",
        "assignee": "王五",
        "created_at": "2026-10-07T14:20:00+00:00",
        "updated_at": "2026-10-09T18:30:00+00:00",
    },
]

_LIMIT_MIN, _LIMIT_MAX, _LIMIT_DEFAULT = 1, 50, 10


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class TicketStore:
    """内存工单库(线程安全;读写锁语义以互斥锁简化——演示频度足够)。"""

    def __init__(self, seed: list[dict[str, object]] | None = None) -> None:
        self._lock = threading.Lock()
        self._tickets = [dict(t) for t in (seed if seed is not None else _SEED_TICKETS)]

    def list(
        self, *, query: str = "", status: str = "", limit: int = _LIMIT_DEFAULT
    ) -> tuple[list[dict[str, object]], int]:
        """返回(截断后的工单列表, 过滤后总数)。"""
        q = query.strip().lower()
        st = status.strip().lower()
        with self._lock:
            hits = [
                t
                for t in self._tickets
                if (not q or q in str(t["title"]).lower() or q in str(t["description"]).lower())
                and (not st or str(t["status"]).lower() == st)
            ]
            total = len(hits)
            return [dict(t) for t in hits[:limit]], total

    def get(self, ticket_id: str) -> dict[str, object] | None:
        with self._lock:
            for t in self._tickets:
                if str(t["id"]) == ticket_id:
                    return dict(t)
        return None

    def create(
        self, *, title: str, description: str, priority: str, assignee: str = ""
    ) -> dict[str, object]:
        with self._lock:
            seqs = [
                int(str(t["id"]).lstrip("T-"))
                for t in self._tickets
                if str(t["id"]).startswith("T-")
            ]
            seq = 1 + max(seqs, default=1000)
            now = _now_iso()
            ticket: dict[str, object] = {
                "id": f"T-{seq}",
                "title": title,
                "description": description,
                "status": "open",
                "priority": priority,
                "assignee": assignee,
                "created_at": now,
                "updated_at": now,
            }
            self._tickets.append(ticket)
            return dict(ticket)


def make_handler(store: TicketStore, *, token: str) -> type[BaseHTTPRequestHandler]:
    """按约定实现三端点的 handler 工厂(token 为空=不校验鉴权,便于本地试玩)。"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002  # 基类形参名;静默默认访问日志
            pass

        def _send(self, code: int, payload: object) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if not token:
                return True
            header = self.headers.get("Authorization", "")
            return header == f"Bearer {token}"

        def _read_json(self) -> dict[str, object] | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return None
            try:
                data = cast("object", json.loads(self.rfile.read(length).decode("utf-8")))
            except (ValueError, UnicodeDecodeError):
                return None
            return cast("dict[str, object]", data) if isinstance(data, dict) else None

        def do_GET(self) -> None:  # noqa: N802  # http.server 命名约定
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            path, _, qs = self.path.partition("?")
            if path.rstrip("/") == "/tickets":
                # 标准 URL 解码(parse_qs;curl/浏览器/客户端均按百分号编码传参)
                params = parse_qs(qs, keep_blank_values=True)
                query = params.get("query", [""])[0]
                status = params.get("status", [""])[0]
                try:
                    limit = int(params.get("limit", [str(_LIMIT_DEFAULT)])[0])
                except ValueError:
                    limit = _LIMIT_DEFAULT
                limit = max(_LIMIT_MIN, min(_LIMIT_MAX, limit))
                tickets, total = store.list(query=query, status=status, limit=limit)
                self._send(200, {"tickets": tickets, "total": total})
                return
            prefix = "/tickets/"
            if path.startswith(prefix):
                ticket = store.get(path[len(prefix) :].rstrip("/"))
                if ticket is None:
                    self._send(404, {"error": "ticket not found"})
                else:
                    self._send(200, ticket)
                return
            self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            if self.path.rstrip("/") != "/tickets":
                self._send(404, {"error": "not found"})
                return
            body = self._read_json()
            if body is None:
                self._send(400, {"error": "invalid json"})
                return
            title = body.get("title")
            description = body.get("description")
            priority = body.get("priority")
            if not isinstance(title, str) or not title.strip():
                self._send(422, {"error": "title required"})
                return
            if not isinstance(description, str) or not description.strip():
                self._send(422, {"error": "description required"})
                return
            if not isinstance(priority, str) or not priority.strip():
                self._send(422, {"error": "priority required"})
                return
            assignee = body.get("assignee")
            ticket = store.create(
                title=title.strip(),
                description=description.strip(),
                priority=priority.strip(),
                assignee=assignee.strip() if isinstance(assignee, str) else "",
            )
            self._send(201, ticket)

    return Handler


class MockTicketServer:
    """可编程起停的 mock 服务(线程形态;测试内起停与演示手工启动同一实现)。"""

    def __init__(self, *, host: str = "127.0.0.1", port: int = 0, token: str = "") -> None:
        self._httpd = ThreadingHTTPServer((host, port), make_handler(TicketStore(), token=token))
        self.port: int = self._httpd.server_address[1]  # type: ignore[index]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread.start()

    def serve_forever(self) -> None:
        """前台常驻(演示 CLI 用):阻塞至 stop() 被调(Ctrl-C 路径)。"""
        self._thread.join()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description="最小工单 API mock 服务(约定见文件头)")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址(缺省 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8123, help="监听端口(缺省 8123)")
    parser.add_argument(
        "--token", default="", help="期望的 Bearer 凭证(与 KM_TICKET_API_KEY 一致;空=不校验)"
    )
    args = parser.parse_args()
    # 显式端口被占时直接报错退出(演示形态:让用户改端口,不静默漂移)
    server = MockTicketServer(host=args.host, port=args.port, token=args.token)
    print(  # noqa: T201
        f"mock 工单服务已启动:{server.base_url}(token={'已启用' if args.token else '未校验'})",
        flush=True,
    )
    server.start()
    try:
        server.serve_forever()  # 前台常驻:阻塞至 Ctrl-C
    except KeyboardInterrupt:
        print("\nmock 工单服务已停止")  # noqa: T201
    finally:
        server.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
