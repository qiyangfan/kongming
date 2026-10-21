"""通过 Podman CLI 在隔离容器中执行沙箱任务。

命令以参数数组启动不经 shell;容器禁网、只读挂载、非 root 用户,并限制
内存/CPU/进程数。超时终止子进程;退出码 137 归为资源超限;缺少协议结果行
时按错误返回并附 stderr 尾部。
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from typing import Any, cast

from kongming.sandbox.models import PROTOCOL_PREFIX, SandboxOutcome

log = logging.getLogger(__name__)

# 容器内 runner 读 stdin 的 JSON 载荷宿主侧上限(与 service 1MB 闸双闸,
# 运行器独立可用时仍自保);stderr 收集上限(防恶意 stderr 刷屏撑内存)
_STDIN_CAP = 2 * 1024 * 1024
_STDERR_TAIL = 2000

# 宿主侧 stdout/stderr 各设总量闸——容器内 64KB 闸只拦 sys.stdout
# 替换位,os.write(1,…)/sys.__stdout__/刷 stderr 可绕过;communicate 全量
# 读入 api 进程内存是无界放大面(30s × pipe 数百 MB/s → GB 级)。合法协议
# 行上界 ≈ 500KB 图 + 64KB stdout + 开销,2MB 充裕
_OUTPUT_CAP = 2 * 1024 * 1024


class _StreamOverflow(Exception):
    """单侧输出超宿主总量闸(疑洪水攻击)。"""


async def _read_bounded(stream: asyncio.StreamReader, cap: int) -> bytes:
    """有界读:超 cap 即抛 _StreamOverflow(调用方杀容器收敛)。"""
    buf = bytearray()
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return bytes(buf)
        buf.extend(chunk)
        if len(buf) > cap:
            raise _StreamOverflow(f"输出超 {cap} 字节上限")


def _parse_outcome(
    *,
    stdout_text: str,
    stderr_tail: str,
    exit_code: int | None,
    duration_ms: int,
) -> SandboxOutcome:
    """根据子进程退出码和协议输出分类结果；137 表示资源超限，缺少结果行表示执行错误。"""
    if exit_code == 137:
        return SandboxOutcome(
            status="RESOURCE_LIMIT",
            error="资源超限已终止(内存配额)",
            duration_ms=duration_ms,
            exit_code=exit_code,
        )
    last_line = ""
    for line in reversed(stdout_text.splitlines()):
        if line.strip():
            last_line = line.strip()
            break
    if not last_line.startswith(PROTOCOL_PREFIX):
        if last_line or stderr_tail:
            log.warning("沙箱协议行缺失 exit=%s stderr=%s", exit_code, stderr_tail[:200])
        return SandboxOutcome(
            status="ERROR",
            error=(stderr_tail or f"沙箱异常退出(exit {exit_code})")[:2000],
            stdout=stdout_text[-2000:],
            duration_ms=duration_ms,
            exit_code=exit_code,
        )
    try:
        parsed = json.loads(last_line[len(PROTOCOL_PREFIX) :])
        if not isinstance(parsed, dict):
            raise ValueError("协议体非对象")
        body = cast("dict[str, Any]", parsed)
        # 协议字段取值全收敛:伪造协议行的 int 转换失败不得逃逸(端口零抛出契约)
        data_rows = int(body.get("data_rows") or 0)
        data_cols = int(body.get("data_cols") or 0)
        images_raw = body.get("images")
        images = (
            [str(i) for i in cast("list[object]", images_raw) if isinstance(i, str)]
            if isinstance(images_raw, list)
            else []
        )
        # 宿主复核(伪造/篡改面):v1 至多 1 张,单张 base64 ≤500KB
        images = [i for i in images[:1] if len(i) <= 500 * 1024]
        stdout_val = str(body.get("stdout") or "")
        error_val = body.get("error")
        note_val = body.get("image_note")
        preview_val = body.get("result_preview")
        status_val = str(body.get("status") or "")
    except (json.JSONDecodeError, ValueError, TypeError):
        return SandboxOutcome(
            status="ERROR",
            error="沙箱协议行畸形",
            duration_ms=duration_ms,
            exit_code=exit_code,
        )
    return SandboxOutcome(
        status="SUCCESS" if status_val == "SUCCESS" else "ERROR",
        stdout=stdout_val,
        error=None if error_val is None else str(error_val),
        images=images,
        image_note=None if note_val is None else str(note_val),
        result_preview=None if preview_val is None else str(preview_val),
        data_rows=data_rows,
        data_cols=data_cols,
        duration_ms=duration_ms,
        exit_code=exit_code,
    )


class PodmanSandboxRunner:
    """timeout_sec/mem_mb/cpus 来自 Settings(组合根)。"""

    def __init__(
        self,
        *,
        image: str,
        timeout_sec: int = 30,
        mem_mb: int = 256,
        cpus: float = 0.5,
    ) -> None:
        self._image = image
        self._timeout = timeout_sec
        self._mem = mem_mb
        self._cpus = cpus

    def _argv(self, name: str) -> list[str]:
        return [
            "podman",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=16m",  # noqa: S108  # 容器内挂载点非宿主临时文件
            "--memory",
            f"{self._mem}m",
            "--cpus",
            str(self._cpus),
            "--pids-limit",
            "64",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "1000",
            "--stop-timeout",
            "1",
            "--name",
            name,
            self._image,
        ]

    @staticmethod
    async def _kill_container(name: str) -> None:
        """按名杀容器(集成测试:杀 podman CLI 客户端不会终止容器——
        conmon 托管下容器续跑至自然退出,--rm 才生效;TIMEOUT 语义要求
        立即终止,须 podman kill 容器本体)。"""
        with contextlib.suppress(Exception):
            killer = await asyncio.create_subprocess_exec(
                "podman",
                "kill",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=5)

    async def run(self, *, code: str, data: dict[str, object] | None) -> SandboxOutcome:
        started = time.monotonic()
        payload = json.dumps({"code": code, "data": data}, ensure_ascii=False, default=str).encode(
            "utf-8"
        )[:_STDIN_CAP]
        name = f"km-sbx-{uuid.uuid4().hex[:8]}"
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._argv(name),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            # podman 二进制失踪/权限不足等宿主故障:收敛 ERROR(端口零抛出契约)
            log.error("沙箱进程启动失败", exc_info=True)
            return SandboxOutcome(
                status="ERROR",
                error=f"沙箱启动失败: {e}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        async def _exchange() -> tuple[bytes, bytes]:
            # 写 stdin → 关闭 → 双侧有界读。容器引导脚本先读尽
            # stdin 再执行,写侧不会与读侧死锁;子早夭则 drain 断管
            # 三管道已配 PIPE,None 不可达;显式收窄(S101 禁 assert)+
            # 局部变量锁定(mypy 属性收窄会被后续调用冲掉)
            stdin, stdout_s, stderr_s = proc.stdin, proc.stdout, proc.stderr
            if stdin is None or stdout_s is None or stderr_s is None:
                raise RuntimeError("子进程管道未建立")
            stdin.write(payload)
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await stdin.drain()
                stdin.close()
            # 显式任务+异常撤兄:溢出/取消时另一侧重读后随管道 EOF 结束,
            # 但孤儿任务的异常须取回(防 "never retrieved" 噪音)
            out_t = asyncio.ensure_future(_read_bounded(stdout_s, _OUTPUT_CAP))
            err_t = asyncio.ensure_future(_read_bounded(stderr_s, _OUTPUT_CAP))
            try:
                return await asyncio.gather(out_t, err_t)
            except BaseException:
                for t in (out_t, err_t):
                    t.cancel()
                    with contextlib.suppress(BaseException):
                        await t
                raise

        try:
            stdout_b, stderr_b = await asyncio.wait_for(_exchange(), timeout=self._timeout)
        except TimeoutError:
            await self._kill_container(name)  # 先杀容器本体(conmon 托管)
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            # wait 裹超时保持防御对称(SIGKILL 秒回,裹 5s 防极端滞留)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5)
            return SandboxOutcome(
                status="TIMEOUT",
                error=f"执行超 {self._timeout}s 上限已终止",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except _StreamOverflow:
            # 输出洪水——杀容器收敛 RESOURCE_LIMIT(资源滥用类)
            await self._kill_container(name)
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5)
            log.warning("沙箱输出超宿主上限,已终止 name=%s", name)
            return SandboxOutcome(
                status="RESOURCE_LIMIT",
                error=f"输出超宿主 {_OUTPUT_CAP // 1024 // 1024}MB 上限已终止",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except asyncio.CancelledError:
            # 停机/取消窗口也必须杀容器本体(conmon 托管不受 CLI 生死)
            await self._kill_container(name)
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            raise
        except Exception as e:  # 管道断裂等通信故障
            await self._kill_container(name)
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            log.error("沙箱通信故障", exc_info=True)
            return SandboxOutcome(
                status="ERROR",
                error=f"沙箱通信故障: {e}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        return _parse_outcome(
            stdout_text=stdout_b.decode("utf-8", errors="replace"),
            stderr_tail=stderr_b.decode("utf-8", errors="replace")[-_STDERR_TAIL:],
            exit_code=proc.returncode,
            duration_ms=duration_ms,
        )
