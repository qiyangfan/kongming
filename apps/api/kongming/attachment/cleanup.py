"""孤儿附件清理(036⑤,attachment 域):UPLOADED 超期未被消费的
存储行+落盘文件双删(防磁盘泄漏)。

lifespan 每日薄壳调用(015/031/034/035 同款独立短会话);删行优先
(RETURNING 驱动文件删除),文件删除失败仅日志——残留文件归运维面
(行已删,不再被引用;design D3)。
"""

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.attachment.models import EXT_BY_MIME
from kongming.attachment.repository import ATTACH_DIR

log = structlog.get_logger()

_ORPHAN_HOURS = 24


async def cleanup_orphan_attachments(session: AsyncSession) -> int:
    """清理超 24h 的 UPLOADED 孤儿附件;返回清理件数。"""
    from sqlalchemy import text

    rows = (
        await session.execute(
            text(
                "DELETE FROM public.sys_attachment"
                " WHERE status='UPLOADED'"
                " AND created_at < now() - make_interval(hours => :h)"
                " RETURNING id, mime"
            ),
            {"h": _ORPHAN_HOURS},
        )
    ).mappings().all()
    await session.commit()
    deleted = 0
    for r in rows:
        path = ATTACH_DIR / f"{r['id']}.{EXT_BY_MIME.get(str(r['mime']), 'bin')}"
        try:
            path.unlink(missing_ok=True)
            deleted += 1
        except OSError as e:
            log.warning("孤儿附件文件删除失败(行已删,残留归运维面): %s %s", path, e)
    if rows:
        log.info("孤儿附件清理: %d 件(文件删除成功 %d)", len(rows), deleted)
    return len(rows)
