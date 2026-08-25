"""attachment 存储(030):sys_attachment 元数据 + var/attachments 文件。

一次性消费(consume_and_read):同事务置 CONSUMED → 删文件 → 提交;
重复取用返回 None(404 语义由调用方映射)。文件路径全由 id 拼装
(uuid7 名),用户输入(filename)永不进路径。
"""

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.attachment.models import EXT_BY_MIME
from kongming.uuid7 import uuid7

ATTACH_DIR = Path("var/attachments")


def _path(att_id: str, mime: str) -> Path:
    return ATTACH_DIR / f"{att_id}.{EXT_BY_MIME.get(mime, 'bin')}"


class SqlAttachmentRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def insert(
        self,
        *,
        tenant_id: str,
        user_id: str,
        filename: str,
        mime: str,
        size_bytes: int,
    ) -> str:
        rid = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.sys_attachment"
                " (id, tenant_id, user_id, filename, mime, size_bytes)"
                " VALUES (:i, CAST(:t AS uuid), CAST(:u AS uuid), :f, :m, :s)"
            ),
            {
                "i": rid,
                "t": tenant_id,
                "u": user_id,
                "f": filename[:256],
                "m": mime,
                "s": size_bytes,
            },
        )
        return rid

    async def write_file(self, att_id: str, mime: str, data: bytes) -> None:
        def _write() -> None:
            ATTACH_DIR.mkdir(parents=True, exist_ok=True)
            _path(att_id, mime).write_bytes(data)

        await asyncio.to_thread(_write)

    async def delete_file(self, att_id: str, mime: str) -> None:
        """删除已落盘文件(审查循环 B10:commit 失败行回滚后的补偿,
        防 file-only 孤儿——孤儿清理是行驱动,覆盖不了无行文件)。"""
        await asyncio.to_thread(_path(att_id, mime).unlink, True)

    async def find_owned(
        self, *, att_id: str, tenant_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """归属校验视图(引用前置);已消费亦返回(状态由调用方判)。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT id, tenant_id, user_id, filename, mime, size_bytes, status"
                    " FROM public.sys_attachment"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                    " AND user_id=CAST(:u AS uuid)"
                ),
                {"i": att_id, "t": tenant_id, "u": user_id},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def consume_and_read(
        self, *, att_id: str, tenant_id: str
    ) -> tuple[dict[str, Any], bytes] | None:
        """一次性消费(内部端点):UPLOADED 且租户命中 → 置 CONSUMED +
        删文件 + 读 bytes 返回;否则 None。行锁防并发双取(FOR UPDATE)。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT id, filename, mime, size_bytes, status"
                    " FROM public.sys_attachment"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                    " FOR UPDATE"
                ),
                {"i": att_id, "t": tenant_id},
            )
        ).mappings().first()
        if row is None or row["status"] != "UPLOADED":
            return None
        path = _path(str(row["id"]), str(row["mime"]))
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            return None  # 文件失踪(部署事故/提交失败残余:R1-T1——行回滚
            # UPLOADED 但文件已删的低概率窗口):按不可消费收敛(404 语义)
        await self._s.execute(
            text(
                "UPDATE public.sys_attachment SET status='CONSUMED', consumed_at=now()"
                " WHERE id=CAST(:i AS uuid)"
            ),
            {"i": str(row["id"])},
        )
        await asyncio.to_thread(path.unlink, True)
        return dict(row), data
