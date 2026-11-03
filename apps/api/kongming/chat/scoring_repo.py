"""chat_turn_score 仓储(基础设施):幂等写入与按消息读取。

幂等以 message_id UNIQUE 约束承载:重复触发(重启重放/并发投递)
ON CONFLICT DO NOTHING 跳过,spec「重复触发幂等」的落地层。"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.uuid7 import uuid7


class SqlScoreRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def insert_if_absent(
        self,
        *,
        message_id: str,
        score: int,
        grounding: int,
        coverage: int,
        decomposition: dict[str, object],
        score_model: str,
    ) -> bool:
        """写入评分;消息已有评分时 False(幂等跳过,既有记录不变)。"""
        row = (
            await self._s.execute(
                text(
                    "INSERT INTO public.chat_turn_score"
                    " (id, message_id, score, grounding, coverage, decomposition, score_model)"
                    " VALUES (:id, :mid, :sc, :g, :c, CAST(:dec AS jsonb), :model)"
                    " ON CONFLICT (message_id) DO NOTHING"
                    " RETURNING id"
                ),
                {
                    "id": str(uuid7()),
                    "mid": message_id,
                    "sc": score,
                    "g": grounding,
                    "c": coverage,
                    "dec": json.dumps(decomposition, ensure_ascii=False, default=str),
                    "model": score_model[:64],
                },
            )
        ).scalar_one_or_none()
        return row is not None

    async def get_by_message(self, message_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT message_id, score, grounding, coverage, decomposition,"
                        "       score_model, scored_at"
                        " FROM public.chat_turn_score WHERE message_id = :mid"
                    ),
                    {"mid": message_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None
