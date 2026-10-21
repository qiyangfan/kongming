"""数据源读取解析(抽公共):存在+启用+归属校验 + 凭证解密。

meta 内省与 rag fetch-metadata 共用(逻辑内联在 internal/meta.py);
engine 构造仍由调用方经 internal.executor 动态池完成(防 datasource↔internal
循环 import;本模块只产出解析结果,不含池操作)。
校验语义与执行路由同形:不存在/禁用/越属不区分(DatasourceInvalid),
凭证不可解同形且证据留日志(契约)。
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.datasource.cipher import AesGcmCipher, get_cipher
from kongming.datasource.models import CredentialUndecryptable, ResolvedDatasource
from kongming.datasource.repository import SqlDatasourceRepo

log = logging.getLogger(__name__)


class DatasourceInvalid(Exception):
    """数据源不存在/禁用/不归属/凭证不可解(同形,防 key 探测)。"""


async def resolve_for_read(
    session: AsyncSession,
    *,
    tenant_id: str,
    datasource_key: str,
    cipher: AesGcmCipher | None = None,
) -> tuple[ResolvedDatasource, str]:
    """校验并解密出可读数据源;返回 (解析结果, 明文密码)——明文仅调用方内存内
    组 DSN,不落日志/审计(纪律);任何不满足抛 DatasourceInvalid(统一映射)。"""
    row = await SqlDatasourceRepo(session).find_for_execute(datasource_key)
    if row is None or row.status != "ACTIVE" or row.tenant_id != tenant_id:
        raise DatasourceInvalid(datasource_key)
    if cipher is None:
        # 延迟导入避免模块级 config 依赖(测试可注入替身)
        from kongming.config import get_settings

        cipher = get_cipher(get_settings().datasource_encrypt_key)
    try:
        plain = cipher.decrypt(row.password_enc)
    except CredentialUndecryptable:
        log.error("数据源凭证不可解 key=%s(密钥轮换/损坏)", datasource_key)
        raise DatasourceInvalid(datasource_key) from None
    return row, plain
